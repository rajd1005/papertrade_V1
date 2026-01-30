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
    Handles initial broker orders (if Live), calculates targets, and saves the trade to the DB.
    Accepts 'target_channels' list (e.g., ['main', 'vip']) to filter notifications.
    Now accepts 'risk_ratios' (list of 3 floats) to override default [0.5, 1, 2] target calculation.
    INCLUDES DEBUG LOGGING.
    """
    print(f"\n[DEBUG] --- START CREATE TRADE ({mode}) ---")
    print(f"[DEBUG] Symbol: {specific_symbol}, Qty: {quantity}")
    
    try:
        with TRADE_LOCK:
            trades = load_trades()
            current_ts = int(time.time())
            
            # --- FIX: ROBUST DUPLICATE CHECK ---
            for t in trades:
                # 1. If Modes are different (e.g. Paper vs Live), it is NOT a duplicate. Skip check.
                if t.get('mode') != mode:
                    continue
                
                # 2. Check strict duplicates within the same mode
                if t['symbol'] == specific_symbol and t['quantity'] == quantity and (current_ts - t['id']) < 5:
                     print(f"[DEBUG] Duplicate Blocked: {specific_symbol}")
                     return {"status": "error", "message": "Duplicate Trade Blocked"}

            # --- FIX: UNIQUE ID GENERATION ---
            # Ensure new_id is always greater than the max existing ID to prevent overwrites
            new_id = current_ts
            existing_ids = [t['id'] for t in trades]
            if existing_ids:
                max_id = max(existing_ids)
                if new_id <= max_id:
                    new_id = max_id + 1
            
            print(f"[DEBUG] Generated New ID: {new_id}")

            # 1. Detect Exchange (e.g., NSE, NFO)
            exchange = smart_trader.get_exchange_name(specific_symbol)
            
            # 2. Fetch LTP using the safe function
            current_ltp = smart_trader.get_ltp(kite, specific_symbol)
            
            if current_ltp == 0:
                print(f"[DEBUG] Error: LTP 0")
                return {"status": "error", "message": f"Could not fetch LTP for Symbol: {specific_symbol}"}

            # Determine Entry Status
            status = "OPEN"
            entry_price = current_ltp
            trigger_dir = "BELOW"
            
            if order_type == "LIMIT":
                entry_price = float(limit_price)
                status = "PENDING"
                trigger_dir = "ABOVE" if entry_price >= current_ltp else "BELOW"

            logs = []
            sl_order_id = None
            
            # Execute Live Order if Mode is LIVE and Status is OPEN (Market Order)
            if mode == "LIVE" and status == "OPEN":
                try:
                    # 1. Place Entry Order (Using wrapper)
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
                         return {"status": "error", "message": "Broker Rejected Entry Order"}

                    # 2. Place Broker SL-M Order (Using wrapper)
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
                        logs.append(f"[{get_time_str()}] Broker SL Placed: ID {sl_order_id}")
                    except Exception as sl_e: 
                        logs.append(f"[{get_time_str()}] Broker SL FAILED: {sl_e}")

                except Exception as e: 
                    print(f"[DEBUG] Broker Error: {e}")
                    return {"status": "error", "message": f"Broker Rejected: {e}"}

            # Calculate Targets
            # Use custom targets if provided (valid T1 > 0), else calculate ratio-based defaults
            # [UPDATED] Use dynamic risk ratios if provided, otherwise default to [0.5, 1.0, 2.0]
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

            logs.insert(0, f"[{get_time_str()}] Trade Added. Status: {status}")
            
            record = {
                "id": new_id, # <--- USE THE UNIQUE ID
                "entry_time": get_time_str(), 
                "symbol": specific_symbol, 
                "exchange": exchange,
                "mode": mode, 
                "order_type": order_type, 
                "status": status, 
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
                "sl_order_id": sl_order_id,
                "targets_hit_indices": [], 
                "highest_ltp": entry_price, 
                "made_high": entry_price, 
                "current_ltp": current_ltp, 
                "trigger_dir": trigger_dir, 
                "logs": logs
            }
            
            # --- SEND TELEGRAM NOTIFICATION ---
            msg_ids = telegram_bot.notify_trade_event(record, "NEW_TRADE")
            if msg_ids:
                record['telegram_msg_ids'] = msg_ids
                if isinstance(msg_ids, dict):
                    record['telegram_msg_id'] = msg_ids.get('main')
                else:
                    record['telegram_msg_id'] = msg_ids
            
            print(f"[DEBUG] Appending trade to list. Previous count: {len(trades)}")
            trades.append(record)
            print(f"[DEBUG] Saving list. New count: {len(trades)}")
            save_trades(trades)
            print(f"[DEBUG] Trade Creation Successful.")
            return {"status": "success", "trade": record}
            
    except Exception as e:
        print(f"[DEBUG] EXCEPTION in Create Trade: {e}")
        return {"status": "error", "message": str(e)}

def update_trade_protection(kite, trade_id, sl, targets, trailing_sl=0, entry_price=None, target_controls=None, sl_to_entry=0, exit_multiplier=1):
    """
    Updates the protection parameters (SL, Targets, Trailing) for an existing trade.
    Also syncs the changes to the broker if the trade is LIVE.
    """
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
                
                # Modify Broker SL if Live
                if t['mode'] == 'LIVE' and t.get('sl_order_id'):
                    try:
                        broker_ops.modify_order(
                            kite, 
                            order_id=t['sl_order_id'], 
                            trigger_price=t['sl']
                        )
                        entry_msg += " [Broker SL Updated]"
                    except Exception as e: 
                        entry_msg += f" [Broker SL Fail: {e}]"

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
                
                log_event(t, f"Manual Update: SL {t['sl']}{entry_msg}. Trailing: {t['trailing_sl']} pts. Multiplier: {exit_multiplier}x")
                
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
    """
    with TRADE_LOCK:
        trades = load_trades()
        updated = False
        
        for t in trades:
            if str(t['id']) == str(trade_id):
                qty_delta = lots_count * lot_size
                ltp = smart_trader.get_ltp(kite, t['symbol'])
                
                # --- ADD LOTS ---
                if action == 'ADD':
                    new_total = t['quantity'] + qty_delta
                    avg_entry = ((t['quantity'] * t['entry_price']) + (qty_delta * ltp)) / new_total
                    t['quantity'] = new_total
                    t['entry_price'] = avg_entry
                    log_event(t, f"Added {qty_delta} Qty. New Avg: {avg_entry:.2f}")
                    
                    if t['mode'] == 'LIVE':
                        try:
                            # Place Market Buy
                            broker_ops.place_order(
                                kite, 
                                symbol=t['symbol'], 
                                exchange=t['exchange'], 
                                transaction_type=kite.TRANSACTION_TYPE_BUY, 
                                quantity=qty_delta, 
                                order_type=kite.ORDER_TYPE_MARKET, 
                                product=kite.PRODUCT_MIS,
                                tag="RD_ADD"
                            )
                            # Update Broker SL Quantity
                            if t.get('sl_order_id'): 
                                broker_ops.modify_order(
                                    kite, 
                                    order_id=t['sl_order_id'], 
                                    quantity=new_total
                                )
                        except Exception as e: 
                            log_event(t, f"Broker Fail (Add): {e}")
                    updated = True
                    
                # --- EXIT LOTS ---
                elif action == 'EXIT':
                    if t['quantity'] > qty_delta:
                        # 1. Reduce Broker SL Qty First
                        if t['mode'] == 'LIVE': 
                            broker_ops.manage_broker_sl(kite, t, qty_delta)
                        
                        t['quantity'] -= qty_delta
                        log_event(t, f"Partial Exit {qty_delta} Qty @ {ltp}")
                        
                        # 2. Place Sell Order
                        if t['mode'] == 'LIVE':
                            try: 
                                broker_ops.place_order(
                                    kite, 
                                    symbol=t['symbol'], 
                                    exchange=t['exchange'], 
                                    transaction_type=kite.TRANSACTION_TYPE_SELL, 
                                    quantity=qty_delta, 
                                    order_type=kite.ORDER_TYPE_MARKET, 
                                    product=kite.PRODUCT_MIS,
                                    tag="RD_EXIT_PART"
                                )
                            except Exception as e: 
                                log_event(t, f"Broker Fail (Exit): {e}")
                        updated = True
                    else: 
                        return False 
                break
                
        if updated: 
            save_trades(trades)
        return True
    return False

def promote_to_live(kite, trade_id):
    """
    Promotes a PAPER trade to LIVE execution.
    Places a Market Buy order and a Stop Loss order immediately.
    """
    with TRADE_LOCK:
        trades = load_trades()
        for t in trades:
            if t['id'] == int(trade_id) and t['mode'] == "PAPER":
                try:
                    # 1. Place Buy Order
                    broker_ops.place_order(
                        kite, 
                        symbol=t['symbol'], 
                        exchange=t['exchange'], 
                        transaction_type=kite.TRANSACTION_TYPE_BUY, 
                        quantity=t['quantity'], 
                        order_type=kite.ORDER_TYPE_MARKET, 
                        product=kite.PRODUCT_MIS,
                        tag="RD_PROMOTE"
                    )
                    
                    # 2. Place SL Order
                    try:
                        sl_id = broker_ops.place_order(
                            kite, 
                            symbol=t['symbol'], 
                            exchange=t['exchange'], 
                            transaction_type=kite.TRANSACTION_TYPE_SELL, 
                            quantity=t['quantity'], 
                            order_type=kite.ORDER_TYPE_SL_M, 
                            product=kite.PRODUCT_MIS, 
                            trigger_price=t['sl'],
                            tag="RD_SL"
                        )
                        t['sl_order_id'] = sl_id
                    except: 
                        log_event(t, "Promote: Broker SL Failed")
                        
                    t['mode'] = "LIVE"
                    t['status'] = "PROMOTED_LIVE"
                    
                    # Notify Promotion
                    telegram_bot.notify_trade_event(t, "UPDATE", "Promoted to LIVE")
                    
                    save_trades(trades)
                    return True
                except: 
                    return False
        return False

def close_trade_manual(kite, trade_id):
    """
    Manually closes a trade via the UI.
    Squares off position (if Live), cancels SL, and moves to history.
    """
    with TRADE_LOCK:
        trades = load_trades()
        active_list = []
        found = False
        
        for t in trades:
            if t['id'] == int(trade_id):
                found = True
                
                # Default Exit Reason
                exit_reason = "MANUAL_EXIT"
                exit_p = t.get('current_ltp', 0)
                
                # Fetch fresh LTP if possible
                try: 
                    exit_p = smart_trader.get_ltp(kite, t['symbol'])
                except: pass
                
                # --- NEW: Handle Pending Cancellations ---
                # If closing a PENDING order, it means we canceled it. 
                # PnL should be 0, so we set exit_price = entry_price and status = NOT_ACTIVE
                if t['status'] == 'PENDING':
                    exit_reason = "NOT_ACTIVE"
                    exit_p = t['entry_price']
                
                # Handle Live Execution
                if t['mode'] == "LIVE" and t['status'] != "PENDING":
                    broker_ops.manage_broker_sl(kite, t, cancel_completely=True)
                    try: 
                        broker_ops.place_order(
                            kite, 
                            symbol=t['symbol'], 
                            exchange=t['exchange'], 
                            transaction_type=kite.TRANSACTION_TYPE_SELL, 
                            quantity=t['quantity'], 
                            order_type=kite.ORDER_TYPE_MARKET, 
                            product=kite.PRODUCT_MIS,
                            tag="RD_MANUAL_EXIT"
                        )
                    except: pass
                
                broker_ops.move_to_history(t, exit_reason, exit_p)
            else:
                active_list.append(t)
        
        if found: 
            save_trades(active_list)
        return found
