import time
import copy
import smart_trader
from managers.persistence import TRADE_LOCK, load_trades, save_trades
from managers.common import get_time_str, log_event
from managers import broker_ops
from managers.telegram_manager import bot as telegram_bot

def create_trade_direct(kite, mode, specific_symbol, quantity, sl_points, custom_targets, order_type, limit_price=0, target_controls=None, trailing_sl=0, sl_to_entry=0, exit_multiplier=1, target_channels=None, risk_ratios=None):
    """
    Creates a new trade (Live or Paper). 
    OPTIMIZED: Fetches data and places orders OUTSIDE the lock to prevent blocking.
    Uses 'INITIALIZING' status to reserve the trade ID.
    """
    print(f"\n[DEBUG] --- START CREATE TRADE ({mode}) ---")
    print(f"[DEBUG] Symbol: {specific_symbol}, Qty: {quantity}")
    
    # --- PHASE 1: PREPARATION (NO LOCK) ---
    # Fetch LTP first to fail fast if network/symbol is bad
    try:
        current_ltp = smart_trader.get_ltp(kite, specific_symbol)
    except Exception as e:
        return {"status": "error", "message": f"LTP Fetch Failed: {str(e)}"}
    
    if current_ltp == 0:
        print(f"[DEBUG] Error: LTP 0")
        return {"status": "error", "message": f"Could not fetch LTP for Symbol: {specific_symbol}"}

    # 1. Detect Exchange (e.g., NSE, NFO)
    exchange = smart_trader.get_exchange_name(specific_symbol)
    
    # Determine Entry details
    entry_price = current_ltp
    trigger_dir = "BELOW"
    
    if order_type == "LIMIT":
        entry_price = float(limit_price)
        trigger_dir = "ABOVE" if entry_price >= current_ltp else "BELOW"

    # Calculate Targets
    use_ratios = risk_ratios if risk_ratios else [0.5, 1.0, 2.0]
    targets = custom_targets if len(custom_targets) == 3 and custom_targets[0] > 0 else [entry_price + (sl_points * x) for x in use_ratios]
    
    # Deep copy to prevent Shadow mode shared reference issues
    final_target_controls = []
    if target_controls:
        final_target_controls = copy.deepcopy(target_controls)
    else:
        final_target_controls = [
            {'enabled': True, 'lots': 0, 'trail_to_entry': False}, 
            {'enabled': True, 'lots': 0, 'trail_to_entry': False}, 
            {'enabled': True, 'lots': 1000, 'trail_to_entry': False}
        ]
    
    lot_size = smart_trader.get_lot_size(specific_symbol)
    
    # Auto-Match Trailing Logic (-1 sets trail equal to SL risk)
    final_trailing_sl = float(trailing_sl) if trailing_sl else 0
    if final_trailing_sl == -1.0: 
        final_trailing_sl = float(sl_points)

    # Exit Multiplier Logic: Split quantity and recalculate targets if > 1
    if exit_multiplier > 1:
        # Determine the furthest valid target or default to 1:2
        valid_targets = [x for x in custom_targets if x > 0]
        final_goal = max(valid_targets) if valid_targets else (entry_price + (sl_points * 2))
        
        dist = final_goal - entry_price
        new_targets = []
        new_controls = []
        
        base_lots = (quantity // lot_size) // exit_multiplier
        rem = (quantity // lot_size) % exit_multiplier
        
        for i in range(1, exit_multiplier + 1):
            fraction = i / exit_multiplier
            t_price = entry_price + (dist * fraction)
            new_targets.append(round(t_price, 2))
            
            lots_here = base_lots + (rem if i == exit_multiplier else 0)
            new_controls.append({'enabled': True, 'lots': int(lots_here), 'trail_to_entry': False})
        
        # Fill remaining slots up to 3 (system expects list of 3)
        while len(new_targets) < 3: 
            new_targets.append(0)
            new_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
        
        targets = new_targets
        final_target_controls = new_controls

    # --- PHASE 2: RESERVATION (LOCK) ---
    new_id = int(time.time())
    record = None
    
    with TRADE_LOCK:
        trades = load_trades()
        
        # FIX: ROBUST DUPLICATE CHECK
        for t in trades:
            if t.get('mode') != mode:
                continue
            if t['symbol'] == specific_symbol and t['quantity'] == quantity and (new_id - t['id']) < 5:
                 print(f"[DEBUG] Duplicate Blocked: {specific_symbol}")
                 return {"status": "error", "message": "Duplicate Trade Blocked"}

        # FIX: UNIQUE ID GENERATION
        existing_ids = [t['id'] for t in trades]
        if existing_ids:
            max_id = max(existing_ids)
            if new_id <= max_id:
                new_id = max_id + 1
        
        print(f"[DEBUG] Generated New ID: {new_id}")
        
        # Initial Logs
        logs = [f"[{get_time_str()}] Initializing Trade..."]
        
        record = {
            "id": new_id,
            "entry_time": get_time_str(), 
            "symbol": specific_symbol, 
            "exchange": exchange,
            "mode": mode, 
            "order_type": order_type, 
            "status": "INITIALIZING", # Temporary status
            "entry_price": entry_price, 
            "quantity": quantity,
            "sl": entry_price - sl_points, 
            "targets": targets, 
            "target_controls": final_target_controls, 
            "target_channels": target_channels, 
            "lot_size": lot_size, 
            "trailing_sl": final_trailing_sl, 
            "sl_to_entry": int(sl_to_entry),
            "exit_multiplier": int(exit_multiplier), 
            "sl_order_id": None,
            "targets_hit_indices": [], 
            "highest_ltp": entry_price, 
            "made_high": entry_price, 
            "current_ltp": current_ltp, 
            "trigger_dir": trigger_dir, 
            "logs": logs
        }
        
        trades.append(record)
        save_trades(trades)

    # --- PHASE 3: EXECUTION (NO LOCK) ---
    # Perform slow network calls here
    sl_order_id = None
    execution_success = True
    fail_reason = ""
    
    # Determine Status
    final_status = "OPEN"
    if order_type == "LIMIT":
        final_status = "PENDING"
    
    if mode == "LIVE" and final_status == "OPEN":
        try:
            # 1. Place Entry Order
            order_id = broker_ops.place_order(
                kite,
                symbol=specific_symbol,
                exchange=exchange, 
                transaction_type=kite.TRANSACTION_TYPE_BUY, 
                quantity=quantity, 
                order_type=kite.ORDER_TYPE_MARKET, 
                product=kite.PRODUCT_MIS,
                tag="RD_ENTRY"
            )
            
            if not order_id:
                 execution_success = False
                 fail_reason = "Broker Rejected Entry Order"
            else:
                # 2. Place Broker SL-M Order
                sl_trigger = entry_price - sl_points 
                try:
                    sl_order_id = broker_ops.place_order(
                        kite, 
                        symbol=specific_symbol, 
                        exchange=exchange, 
                        transaction_type=kite.TRANSACTION_TYPE_SELL, 
                        quantity=quantity, 
                        order_type=kite.ORDER_TYPE_SL_M, 
                        product=kite.PRODUCT_MIS, 
                        trigger_price=sl_trigger,
                        tag="RD_SL"
                    )
                except Exception as sl_e: 
                    # Trade is open, but SL failed. We don't fail the trade, just log it.
                    fail_reason = f"Entry Success, but Broker SL Failed: {sl_e}"

        except Exception as e: 
            print(f"[DEBUG] Broker Error: {e}")
            execution_success = False
            fail_reason = f"Broker Rejected: {e}"

    if not execution_success:
        final_status = "FAILED"

    # --- PHASE 4: CONFIRMATION (LOCK) ---
    with TRADE_LOCK:
        trades = load_trades()
        # Find our reserved trade
        t = next((x for x in trades if x['id'] == new_id), None)
        if t:
            t['status'] = final_status
            if sl_order_id:
                t['sl_order_id'] = sl_order_id
            
            t['logs'].append(f"[{get_time_str()}] Trade Status: {final_status}")
            if sl_order_id:
                t['logs'].append(f"[{get_time_str()}] Broker SL Placed: ID {sl_order_id}")
            if fail_reason:
                t['logs'].append(f"[{get_time_str()}] Warning/Error: {fail_reason}")
                
            # Update local record for return
            record = t
            save_trades(trades)
            
    if final_status != "FAILED":
        # --- SEND TELEGRAM NOTIFICATION ---
        telegram_bot.notify_trade_event(record, "NEW_TRADE")
        print(f"[DEBUG] Trade Creation Successful.")
        return {"status": "success", "trade": record}
    else:
        return {"status": "error", "message": fail_reason}

def update_trade_protection(kite, trade_id, sl, targets, trailing_sl=0, entry_price=None, target_controls=None, sl_to_entry=0, exit_multiplier=1):
    """
    Updates the protection parameters (SL, Targets, Trailing) for an existing trade.
    OPTIMIZED: Syncs changes to the broker OUTSIDE the lock.
    """
    # --- PHASE 1: IDENTIFY BROKER ACTION (LOCK) ---
    broker_sl_action = None
    
    with TRADE_LOCK:
        trades = load_trades()
        t = next((x for x in trades if str(x['id']) == str(trade_id)), None)
        if t and t['mode'] == 'LIVE' and t.get('sl_order_id'):
            broker_sl_action = {'order_id': t['sl_order_id'], 'trigger_price': float(sl)}
    
    # --- PHASE 2: EXECUTE BROKER ACTION (NO LOCK) ---
    broker_log = ""
    if broker_sl_action:
        try:
            broker_ops.modify_order(
                kite, 
                order_id=broker_sl_action['order_id'], 
                trigger_price=broker_sl_action['trigger_price']
            )
            broker_log = " [Broker SL Updated]"
        except Exception as e: 
            broker_log = f" [Broker SL Fail: {e}]"

    # --- PHASE 3: UPDATE DB (LOCK) ---
    with TRADE_LOCK:
        trades = load_trades()
        updated = False
        
        for t in trades:
            if str(t['id']) == str(trade_id):
                entry_msg = ""
                
                # Update Entry Price (Only allowed if PENDING)
                if entry_price is not None:
                    if t['status'] == 'PENDING':
                        new_entry = float(entry_price)
                        if new_entry != t['entry_price']:
                            t['entry_price'] = new_entry
                            entry_msg = f" | Entry Updated to {new_entry}"
                
                final_trailing_sl = float(trailing_sl) if trailing_sl else 0
                if final_trailing_sl == -1.0:
                    calc_diff = t['entry_price'] - float(sl)
                    final_trailing_sl = max(0.0, calc_diff)

                t['sl'] = float(sl)
                t['trailing_sl'] = final_trailing_sl
                t['sl_to_entry'] = int(sl_to_entry)
                t['exit_multiplier'] = int(exit_multiplier) 
                
                # Recalculate Targets if Exit Multiplier Changed
                if exit_multiplier > 1:
                    eff_entry = t['entry_price']
                    eff_sl_points = eff_entry - float(sl)
                    
                    valid_custom = [x for x in targets if x > 0]
                    final_goal = max(valid_custom) if valid_custom else (eff_entry + (eff_sl_points * 2))
                    
                    dist = final_goal - eff_entry
                    new_targets = []
                    new_controls = []
                    
                    lot_size = t.get('lot_size') or smart_trader.get_lot_size(t['symbol'])
                    total_lots = t['quantity'] // lot_size
                    base_lots = total_lots // exit_multiplier
                    remainder = total_lots % exit_multiplier
                    
                    for i in range(1, exit_multiplier + 1):
                        fraction = i / exit_multiplier
                        t_price = eff_entry + (dist * fraction)
                        new_targets.append(round(t_price, 2))
                        
                        lots_here = base_lots + (remainder if i == exit_multiplier else 0)
                        new_controls.append({'enabled': True, 'lots': int(lots_here), 'trail_to_entry': False})
                    
                    while len(new_targets) < 3: 
                        new_targets.append(0)
                        new_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                        
                    t['targets'] = new_targets
                    t['target_controls'] = new_controls
                else:
                    t['targets'] = [float(x) for x in targets]
                    if target_controls: 
                        t['target_controls'] = target_controls
                
                log_event(t, f"Manual Update: SL {t['sl']}{entry_msg}{broker_log}. Trailing: {t['trailing_sl']} pts. Multiplier: {exit_multiplier}x")
                
                # --- TELEGRAM UPDATE ---
                telegram_bot.notify_trade_event(t, "UPDATE")
                
                updated = True
                break
                
        if updated:
            save_trades(trades)
            return True
        return False

def manage_trade_position(kite, trade_id, action, lot_size, lots_count):
    """
    Manages position sizing: Adding lots (Averaging) or Partial Exits.
    OPTIMIZED: Fetches prices and places orders OUTSIDE the lock.
    """
    # --- PHASE 1: SNAPSHOT (LOCK) ---
    trade_snapshot = None
    with TRADE_LOCK:
        trades = load_trades()
        t = next((x for x in trades if str(x['id']) == str(trade_id)), None)
        if t:
            trade_snapshot = copy.deepcopy(t)
    
    if not trade_snapshot:
        return False

    qty_delta = lots_count * lot_size
    ltp = 0
    success = False
    fail_msg = ""
    
    # --- PHASE 2: NETWORK EXECUTION (NO LOCK) ---
    try:
        ltp = smart_trader.get_ltp(kite, trade_snapshot['symbol'])
        
        if action == 'ADD':
            if trade_snapshot['mode'] == 'LIVE':
                # Place Market Buy
                broker_ops.place_order(
                    kite, 
                    symbol=trade_snapshot['symbol'], 
                    exchange=trade_snapshot['exchange'], 
                    transaction_type=kite.TRANSACTION_TYPE_BUY, 
                    quantity=qty_delta, 
                    order_type=kite.ORDER_TYPE_MARKET, 
                    product=kite.PRODUCT_MIS,
                    tag="RD_ADD"
                )
                # Update Broker SL Quantity
                if trade_snapshot.get('sl_order_id'): 
                    broker_ops.modify_order(
                        kite, 
                        order_id=trade_snapshot['sl_order_id'], 
                        quantity=(trade_snapshot['quantity'] + qty_delta)
                    )
            success = True
            
        elif action == 'EXIT':
            if trade_snapshot['quantity'] > qty_delta:
                if trade_snapshot['mode'] == 'LIVE': 
                    # 1. Reduce Broker SL Qty First
                    broker_ops.manage_broker_sl(kite, trade_snapshot, qty_delta)
                    
                    # 2. Place Sell Order
                    broker_ops.place_order(
                        kite, 
                        symbol=trade_snapshot['symbol'], 
                        exchange=trade_snapshot['exchange'], 
                        transaction_type=kite.TRANSACTION_TYPE_SELL, 
                        quantity=qty_delta, 
                        order_type=kite.ORDER_TYPE_MARKET, 
                        product=kite.PRODUCT_MIS,
                        tag="RD_EXIT_PART"
                    )
                success = True
            else:
                fail_msg = "Invalid Quantity"
                
    except Exception as e:
        fail_msg = str(e)
        success = False

    # --- PHASE 3: COMMIT (LOCK) ---
    if success:
        with TRADE_LOCK:
            trades = load_trades()
            t = next((x for x in trades if str(x['id']) == str(trade_id)), None)
            if t:
                if action == 'ADD':
                    new_total = t['quantity'] + qty_delta
                    avg_entry = ((t['quantity'] * t['entry_price']) + (qty_delta * ltp)) / new_total
                    t['quantity'] = new_total
                    t['entry_price'] = avg_entry
                    log_event(t, f"Added {qty_delta} Qty @ {ltp}. New Avg: {avg_entry:.2f}")
                
                elif action == 'EXIT':
                    if t['quantity'] > qty_delta:
                        t['quantity'] -= qty_delta
                        log_event(t, f"Partial Exit {qty_delta} Qty @ {ltp}")
                
                save_trades(trades)
            return True
    
    if fail_msg:
        print(f"[DEBUG] Manage Position Failed: {fail_msg}")
        
    return False

def promote_to_live(kite, trade_id):
    """
    Promotes a PAPER trade to LIVE execution.
    OPTIMIZED: Places orders OUTSIDE the lock.
    """
    # --- PHASE 1: SNAPSHOT (LOCK) ---
    target_trade = None
    with TRADE_LOCK:
        trades = load_trades()
        for t in trades:
            if t['id'] == int(trade_id) and t['mode'] == "PAPER":
                t['status'] = "PROMOTING" # Temporary status to prevent double clicks
                target_trade = copy.deepcopy(t)
                break
        save_trades(trades)
        
    if not target_trade:
        return False

    # --- PHASE 2: EXECUTION (NO LOCK) ---
    success = False
    sl_id = None
    
    try:
        # 1. Place Buy Order
        broker_ops.place_order(
            kite, 
            symbol=target_trade['symbol'], 
            exchange=target_trade['exchange'], 
            transaction_type=kite.TRANSACTION_TYPE_BUY, 
            quantity=target_trade['quantity'], 
            order_type=kite.ORDER_TYPE_MARKET, 
            product=kite.PRODUCT_MIS,
            tag="RD_PROMOTE"
        )
        
        # 2. Place SL Order
        try:
            sl_id = broker_ops.place_order(
                kite, 
                symbol=target_trade['symbol'], 
                exchange=target_trade['exchange'], 
                transaction_type=kite.TRANSACTION_TYPE_SELL, 
                quantity=target_trade['quantity'], 
                order_type=kite.ORDER_TYPE_SL_M, 
                product=kite.PRODUCT_MIS, 
                trigger_price=target_trade['sl'],
                tag="RD_SL"
            )
            success = True
        except: 
            # If SL fails, we still consider promotion valid but log error
            success = True
            
    except: 
        success = False

    # --- PHASE 3: COMMIT (LOCK) ---
    with TRADE_LOCK:
        trades = load_trades()
        t = next((x for x in trades if x['id'] == int(trade_id)), None)
        if t:
            if success:
                t['mode'] = "LIVE"
                t['status'] = "PROMOTED_LIVE"
                if sl_id: t['sl_order_id'] = sl_id
                
                log_event(t, "Promoted to LIVE execution")
                # Notify Promotion
                telegram_bot.notify_trade_event(t, "UPDATE", "Promoted to LIVE")
            else:
                t['status'] = "PAPER" # Revert status
                log_event(t, "Promotion Failed: Broker Error")
                
            save_trades(trades)
            return success
            
    return False

def close_trade_manual(kite, trade_id):
    """
    Manually closes a trade via the UI.
    OPTIMIZED: Places orders OUTSIDE the lock.
    """
    # --- PHASE 1: MARK CLOSING (LOCK) ---
    trade_snapshot = None
    with TRADE_LOCK:
        trades = load_trades()
        t = next((x for x in trades if x['id'] == int(trade_id)), None)
        if t:
            t['status'] = "CLOSING" # Mark closing so risk engine skips it
            trade_snapshot = copy.deepcopy(t)
            save_trades(trades)
            
    if not trade_snapshot:
        return False

    # --- PHASE 2: EXECUTE (NO LOCK) ---
    # Default Exit Reason
    exit_reason = "MANUAL_EXIT"
    exit_p = trade_snapshot.get('current_ltp', 0)
    
    # Fetch fresh LTP if possible
    try: 
        exit_p = smart_trader.get_ltp(kite, trade_snapshot['symbol'])
    except: pass
    
    # --- NEW: Handle Pending Cancellations ---
    if trade_snapshot.get('status_orig') == 'PENDING' or trade_snapshot['status'] == 'PENDING':
        exit_reason = "NOT_ACTIVE"
        exit_p = trade_snapshot['entry_price']
    
    # Handle Live Execution
    if trade_snapshot['mode'] == "LIVE" and exit_reason != "NOT_ACTIVE":
        broker_ops.manage_broker_sl(kite, trade_snapshot, cancel_completely=True)
        try: 
            broker_ops.place_order(
                kite, 
                symbol=trade_snapshot['symbol'], 
                exchange=trade_snapshot['exchange'], 
                transaction_type=kite.TRANSACTION_TYPE_SELL, 
                quantity=trade_snapshot['quantity'], 
                order_type=kite.ORDER_TYPE_MARKET, 
                product=kite.PRODUCT_MIS,
                tag="RD_MANUAL_EXIT"
            )
        except: pass
    
    # --- PHASE 3: MOVE TO HISTORY (LOCK) ---
    with TRADE_LOCK:
        trades = load_trades()
        # Remove the specific trade
        active_list = [t for t in trades if t['id'] != int(trade_id)]
        
        # We need to construct the object to move to history
        # (It might have been updated by risk engine in milliseconds, though unlikely due to CLOSING status)
        # We use the snapshot but update with final exit price
        
        # To be safe, we use the helper but need to handle list removal manually or let helper do it?
        # broker_ops.move_to_history usually just appends to history DB. 
        # We explicitly removed it from active_list above.
        
        # Re-construct trade object for history
        history_obj = trade_snapshot
        history_obj['exit_price'] = exit_p
        history_obj['exit_reason'] = exit_reason
        # Recalculate PnL
        if exit_reason != "NOT_ACTIVE":
            history_obj['pnl'] = (exit_p - history_obj['entry_price']) * history_obj['quantity']
        else:
            history_obj['pnl'] = 0
            
        broker_ops.move_to_history(history_obj, exit_reason, exit_p)
        save_trades(active_list)
        
    return True
