import json
import time
import copy
import smart_trader
import settings
from datetime import datetime
from database import db, TradeHistory
from managers.persistence import TRADE_LOCK, load_trades, save_trades, load_history, get_risk_state, save_risk_state
from managers.common import IST, log_event
from managers.broker_ops import manage_broker_sl, move_to_history
from managers.telegram_manager import bot as telegram_bot
from managers import zerodha_ticker

# --- NEW: End of Day Report Helper (Automated) ---
def send_eod_report(mode):
    """
    Generates and sends two Telegram reports:
    1. Individual Trade Status (Entries, Exits, Highs, Potentials)
    2. Aggregate Summary (Total P/L, Funds, Wins/Losses)
    """
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history = load_history()
        
        # Filter for Today's trades in the specific Mode (LIVE/PAPER)
        todays_trades = [t for t in history if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode]
        
        if not todays_trades:
            return

        # --- REPORT 1: INDIVIDUAL TRADE DETAILS ---
        msg_details = f"📊 <b>{mode} - FINAL TRADE STATUS</b>\n"
        
        total_pnl = 0.0
        total_wins = 0.0
        total_loss = 0.0
        total_funds_used = 0.0
        total_max_potential = 0.0
        
        # Counters for specific request
        cnt_not_active = 0
        cnt_direct_sl = 0

        for t in todays_trades:
            raw_symbol = t.get('symbol', 'Unknown')
            symbol = smart_trader.get_telegram_symbol(raw_symbol)
            
            entry = t.get('entry_price', 0)
            sl = t.get('sl', 0)
            targets = t.get('targets', [])
            raw_status = t.get('status', 'CLOSED')
            qty = t.get('quantity', 0)
            pnl = t.get('pnl', 0)
            
            # --- CUSTOM STATUS DISPLAY LOGIC ---
            display_status = raw_status
            is_direct_sl = False 
            
            # 1. Check for "Time_Exit" (Not active trade)
            if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
                display_status = "Not Active"
                cnt_not_active += 1
                is_direct_sl = True 
            
            # 2. Check for "SL (Without going T1)"
            elif raw_status == "SL_HIT":
                if not t.get('targets_hit_indices'): # No targets were hit
                    display_status = "Stop-Loss"
                    cnt_direct_sl += 1
                    is_direct_sl = True 
                else:
                    display_status = "SL Hit (After Target)"
            
            # Use made_high if available, else exit price, else entry
            made_high = t.get('made_high', t.get('exit_price', entry))
            
            # --- TRACKING TAG LOGIC ---
            # 🔴 = Virtual SL Hit (Tracking Stopped) | 🟢 = Still Tracking
            track_tag = "🟢"
            if t.get('virtual_sl_hit'):
                track_tag = "🔴"
            
            # Suppress Potential for Direct SL
            if is_direct_sl:
                made_high = entry 
                max_pot_val = 0.0
                pot_target = "None"
            else:
                # Max Potential Calculation
                max_pot_val = (made_high - entry) * qty
                if max_pot_val < 0: max_pot_val = 0
                
                # Potential Target Logic
                pot_target = "None"
                if len(targets) >= 3:
                    if made_high >= targets[2]: pot_target = "T3 ✅"
                    elif made_high >= targets[1]: pot_target = "T2 ✅"
                    elif made_high >= targets[0]: pot_target = "T1 ✅"

            total_pnl += pnl
            if pnl >= 0: 
                total_wins += pnl
            else: 
                total_loss += pnl
            
            total_max_potential += max_pot_val
            
            # Funds Used
            invested = entry * qty
            total_funds_used += invested
            
            msg_details += (
                f"\n🔹 <b>{symbol}</b>\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"Targets: {targets}\n"
                f"Status: {display_status}\n" 
                f"High Made: {made_high} {track_tag}\n"
                f"Potential Target: {pot_target}\n"
                f"Max Potential: {max_pot_val:.2f}\n"
                f"----------------"
            )

        # Send Detailed Report
        telegram_bot.send_message(msg_details)

        # --- REPORT 2: AGGREGATE SUMMARY ---
        msg_summary = (
            f"📈 <b>{mode} - EOD SUMMARY</b>\n\n"
            f"💰 <b>Total P/L: ₹ {total_pnl:.2f}</b>\n"
            f"----------------\n"
            f"🟢 Total Wins: ₹ {total_wins:.2f}\n"
            f"🔴 Total Loss: ₹ {total_loss:.2f}\n"
            f"🚀 Max Potential: ₹ {total_max_potential:.2f}\n"
            f"💼 Funds Used: ₹ {total_funds_used:.2f}\n"
            f"📊 Total Trades: {len(todays_trades)}\n"
            f"🚫 Not Active: {cnt_not_active}\n" 
            f"🛑 Direct SL: {cnt_direct_sl}"     
        )
        
        # Send Summary Report
        telegram_bot.send_message(msg_summary)

    except Exception as e:
        print(f"Error generating EOD report: {e}")

# --- NEW: Manual Report Helpers (Triggered by Button) ---

def send_manual_trade_status(mode):
    """
    Sends the detailed 'Final Trade Status' report for all trades of the day (Manual Trigger).
    """
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history = load_history()
        
        # Filter for Today's trades in the specific Mode
        todays_trades = [t for t in history if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode]
        
        if not todays_trades:
            return {"status": "error", "message": "No trades found for today."}

        msg_details = f"📊 <b>{mode} - FINAL TRADE STATUS (MANUAL)</b>\n"
        
        for t in todays_trades:
            raw_symbol = t.get('symbol', 'Unknown')
            symbol = smart_trader.get_telegram_symbol(raw_symbol)
            
            entry = t.get('entry_price', 0)
            sl = t.get('sl', 0)
            targets = t.get('targets', [])
            raw_status = t.get('status', 'CLOSED')
            qty = t.get('quantity', 0)
            pnl = t.get('pnl', 0)
            
            # --- CUSTOM STATUS DISPLAY LOGIC ---
            display_status = raw_status
            is_direct_sl = False 
            
            if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
                display_status = "Not Active"
                is_direct_sl = True
            elif raw_status == "SL_HIT":
                if not t.get('targets_hit_indices'):
                    display_status = "Stop-Loss"
                    is_direct_sl = True
                else:
                    display_status = "SL Hit (After Target)"
            
            made_high = t.get('made_high', t.get('exit_price', entry))
            
            # --- TRACKING TAG LOGIC ---
            track_tag = "🟢"
            if t.get('virtual_sl_hit'):
                track_tag = "🔴"

            # Suppress Potential for Direct SL
            if is_direct_sl:
                made_high = entry 
                max_pot_val = 0.0
                pot_target = "None"
            else:
                max_pot_val = (made_high - entry) * qty
                if max_pot_val < 0: max_pot_val = 0
                
                pot_target = "None"
                if len(targets) >= 3:
                    if made_high >= targets[2]: pot_target = "T3 ✅"
                    elif made_high >= targets[1]: pot_target = "T2 ✅"
                    elif made_high >= targets[0]: pot_target = "T1 ✅"
            
            msg_details += (
                f"\n🔹 <b>{symbol}</b>\n"
                f"Entry: {entry}\n"
                f"SL: {sl}\n"
                f"Targets: {targets}\n"
                f"Status: {display_status}\n" 
                f"High Made: {made_high} {track_tag}\n"
                f"Potential Target: {pot_target}\n"
                f"Max Potential: {max_pot_val:.2f}\n"
                f"----------------"
            )

        telegram_bot.send_message(msg_details)
        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}

def send_manual_trade_report(trade_id):
    """
    Sends a detailed status report for a SINGLE specific trade.
    """
    try:
        # Look in History first
        history = load_history()
        trade = next((t for t in history if str(t['id']) == str(trade_id)), None)
        
        # If not in history, check Active trades
        if not trade:
            active = load_trades()
            trade = next((t for t in active if str(t['id']) == str(trade_id)), None)
            
        if not trade:
            return {"status": "error", "message": "Trade not found"}

        # Construct Message
        raw_symbol = trade.get('symbol', 'Unknown')
        symbol = smart_trader.get_telegram_symbol(raw_symbol)
        
        entry = trade.get('entry_price', 0)
        sl = trade.get('sl', 0)
        targets = trade.get('targets', [])
        raw_status = trade.get('status', 'UNKNOWN')
        qty = trade.get('quantity', 0)
        pnl = trade.get('pnl', 0)
        
        # --- CUSTOM STATUS DISPLAY LOGIC ---
        display_status = raw_status
        is_direct_sl = False

        if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
            display_status = "Not Active"
            is_direct_sl = True
        elif raw_status == "SL_HIT" and not trade.get('targets_hit_indices'):
            display_status = "Stop-Loss"
            is_direct_sl = True
        
        # Use made_high if available, else exit price, else entry
        made_high = trade.get('made_high', trade.get('exit_price', entry))
        
        # --- TRACKING TAG LOGIC ---
        track_tag = "🟢"
        if trade.get('virtual_sl_hit'):
            track_tag = "🔴"
        
        # --- FIX: Suppress Potential for Direct SL ---
        if is_direct_sl:
            made_high = entry 
            max_pot_val = 0.0
            pot_target = "None"
        else:
            # Max Potential
            max_pot_val = (made_high - entry) * qty
            if max_pot_val < 0: max_pot_val = 0
            
            # Potential Target Logic
            pot_target = "None"
            if len(targets) >= 3:
                if made_high >= targets[2]: pot_target = "T3 ✅"
                elif made_high >= targets[1]: pot_target = "T2 ✅"
                elif made_high >= targets[0]: pot_target = "T1 ✅"

        msg = (
            f"🔹 <b>TRADE STATUS: {symbol}</b>\n"
            f"Entry: {entry}\n"
            f"SL: {sl}\n"
            f"Targets: {targets}\n"
            f"Status: {display_status}\n"
            f"High Made: {made_high} {track_tag}\n"
            f"Potential Target: {pot_target}\n"
            f"Max Potential: {max_pot_val:.2f}"
        )
        
        telegram_bot.send_message(msg)
        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}

def send_manual_summary(mode):
    """
    Sends the Aggregate Summary for the current day.
    """
    try:
        # This function reuses the logic to include the new counts
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history = load_history()
        
        todays_trades = [t for t in history if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode]
        
        if not todays_trades:
            return {"status": "error", "message": "No trades found for today."}

        total_pnl = 0.0
        total_wins = 0.0
        total_loss = 0.0
        total_funds_used = 0.0
        total_max_potential = 0.0
        
        cnt_not_active = 0
        cnt_direct_sl = 0

        for t in todays_trades:
            entry = t.get('entry_price', 0)
            qty = t.get('quantity', 0)
            pnl = t.get('pnl', 0)
            made_high = t.get('made_high', t.get('exit_price', entry))
            raw_status = t.get('status', 'CLOSED')

            is_direct_sl = False

            # Counters
            if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
                cnt_not_active += 1
                is_direct_sl = True
            elif raw_status == "SL_HIT" and not t.get('targets_hit_indices'):
                cnt_direct_sl += 1
                is_direct_sl = True

            total_pnl += pnl
            if pnl >= 0: total_wins += pnl
            else: total_loss += pnl
            
            total_funds_used += (entry * qty)
            
            # --- FIX: Suppress Potential in Summary too ---
            if not is_direct_sl:
                max_pot_val = (made_high - entry) * qty
                if max_pot_val < 0: max_pot_val = 0
                total_max_potential += max_pot_val

        msg_summary = (
            f"📈 <b>{mode} - MANUAL SUMMARY</b>\n\n"
            f"💰 <b>Total P/L: ₹ {total_pnl:.2f}</b>\n"
            f"----------------\n"
            f"🟢 Total Wins: ₹ {total_wins:.2f}\n"
            f"🔴 Total Loss: ₹ {total_loss:.2f}\n"
            f"🚀 Max Potential: ₹ {total_max_potential:.2f}\n"
            f"💼 Funds Used: ₹ {total_funds_used:.2f}\n"
            f"📊 Total Trades: {len(todays_trades)}\n"
            f"🚫 Not Active: {cnt_not_active}\n"
            f"🛑 Direct SL: {cnt_direct_sl}"
        )
        
        telegram_bot.send_message(msg_summary)
        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}

def check_global_exit_conditions(kite, mode, mode_settings):
    """
    Checks and executes global risk rules:
    1. Universal Square-off Time (e.g., 15:25) -> NOW SEND REPORT ALWAYS (ONCE)
    2. Profit Locking (Global PnL Trailing)
    """
    with TRADE_LOCK:
        trades = load_trades()
        now = datetime.now(IST)
        exit_time_str = mode_settings.get('universal_exit_time', "15:25")
        today_str = now.strftime("%Y-%m-%d")
        
        # Load State to check if we already ran EOD today
        state = get_risk_state(mode)
        
        # --- 1. TIME BASED EXIT & REPORT ---
        try:
            exit_dt = datetime.strptime(exit_time_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            exit_dt = IST.localize(exit_dt.replace(tzinfo=None))
            
            # Trigger within 2 minutes of the time
            if now >= exit_dt and (now - exit_dt).seconds < 120:
                 
                 # Check if already done for today to prevent spam
                 if state.get('last_eod_date') != today_str:
                     
                     # 1. Close Active Trades (If Any)
                     active_mode = [t for t in trades if t['mode'] == mode]
                     if active_mode:
                         for t in active_mode:
                             # Determine if it's ACTIVE or PENDING
                             exit_reason = "TIME_EXIT"
                             exit_price = t.get('current_ltp', 0)
                             
                             if t['status'] == 'PENDING':
                                 exit_reason = "NOT_ACTIVE"
                                 exit_price = t['entry_price']
                             
                             if t['mode'] == "LIVE" and t['status'] != 'PENDING':
                                manage_broker_sl(kite, t, cancel_completely=True)
                                try: 
                                    kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                                except: pass
                             
                             move_to_history(t, exit_reason, exit_price)
                         
                         # Save remaining trades
                         remaining = [t for t in trades if t['mode'] != mode]
                         save_trades(remaining)
                     
                     # 2. Send EOD Report (ALWAYS, triggers once)
                     send_eod_report(mode)
                     
                     # 3. Mark as Done
                     state['last_eod_date'] = today_str
                     save_risk_state(mode, state)
                     
                     return
        except Exception as e: 
            print(f"Time Check Error: {e}")

        # --- 2. PROFIT LOCKING (Global Trailing) ---
        pnl_start = float(mode_settings.get('profit_lock', 0))
        if pnl_start > 0:
            current_total_pnl = 0.0
            
            # Calculate PnL consistency (Realized + Unrealized)
            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            history = load_history()
            for t in history:
                if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode: 
                    current_total_pnl += t.get('pnl', 0)
            
            active = [t for t in trades if t['mode'] == mode]
            for t in active:
                if t['status'] != 'PENDING':
                    current_total_pnl += (t.get('current_ltp', t['entry_price']) - t['entry_price']) * t['quantity']

            # Activation: Reach minimum threshold
            if not state.get('active') and current_total_pnl >= pnl_start:
                state['active'] = True
                state['high_pnl'] = current_total_pnl
                state['global_sl'] = float(mode_settings.get('profit_min', 0))
                save_risk_state(mode, state)
            
            if state.get('active'):
                # Trail the Global SL up
                if current_total_pnl > state['high_pnl']:
                    diff = current_total_pnl - state['high_pnl']
                    trail_step = float(mode_settings.get('profit_trail', 0))
                    
                    if trail_step > 0 and diff >= trail_step:
                         steps = int(diff / trail_step)
                         state['global_sl'] += (steps * trail_step)
                         state['high_pnl'] = current_total_pnl
                         save_risk_state(mode, state)

                # Breach: Global SL Hit
                if current_total_pnl <= state['global_sl']:
                    active_mode = [t for t in trades if t['mode'] == mode]
                    for t in active_mode:
                         if t['mode'] == "LIVE" and t['status'] != 'PENDING':
                            manage_broker_sl(kite, t, cancel_completely=True)
                            try: 
                                kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                            except: pass
                         
                         move_to_history(t, "PROFIT_LOCK", t.get('current_ltp', 0))
                    
                    remaining = [t for t in trades if t['mode'] != mode]
                    save_trades(remaining)
                    
                    # Reset State
                    state['active'] = False
                    save_risk_state(mode, state)

def update_risk_engine(kite):
    """
    The main monitoring loop called by the background thread.
    Updates prices, checks SL/Target hits, and triggers exits.
    OPTIMIZED: Snapshot -> Execute -> Commit pattern.
    """
    # Check Global Conditions first (Can potentially be optimized, but runs once)
    current_settings = settings.load_settings()
    check_global_exit_conditions(kite, "PAPER", current_settings['modes']['PAPER'])
    check_global_exit_conditions(kite, "LIVE", current_settings['modes']['LIVE'])

    # --- PHASE 1: SNAPSHOT (LOCK) ---
    with TRADE_LOCK:
        active_trades_snapshot = copy.deepcopy(load_trades())
        
        # Load Today's Closed Trades for Missed Opportunity Tracking
        history = load_history()
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        todays_closed = [t for t in history if t.get('exit_time') and t['exit_time'].startswith(today_str)]

    # --- PHASE 2: DATA FETCH (NO LOCK) ---
    # Combine Active Symbols AND Closed Symbols for Data Fetching
    active_symbols = [f"{t['exchange']}:{t['symbol']}" for t in active_trades_snapshot]
    closed_symbols = [f"{t['exchange']}:{t['symbol']}" for t in todays_closed]
    all_instruments = list(set(active_symbols + closed_symbols))

    if not all_instruments: 
        return

    # A. SUBSCRIBE TO TICKER
    if zerodha_ticker.ticker:
        tokens_to_sub = []
        for inst in all_instruments:
            parts = inst.split(":")
            if len(parts) == 2:
                exch, sym = parts
                tok = smart_trader.get_instrument_token(sym, exch)
                if tok:
                    tokens_to_sub.append(tok)
        if tokens_to_sub:
            zerodha_ticker.ticker.subscribe(tokens_to_sub)

    # B. FETCH PRICES
    live_prices = {}
    missing_instruments = []

    # Try Ticker Cache
    if zerodha_ticker.ticker:
        for inst in all_instruments:
            parts = inst.split(":")
            if len(parts) == 2:
                exch, sym = parts
                tok = smart_trader.get_instrument_token(sym, exch)
                if tok:
                    ltp = zerodha_ticker.ticker.get_ltp(tok)
                    if ltp:
                        live_prices[inst] = {'last_price': ltp}
    
    # Identify Missing
    for inst in all_instruments:
        if inst not in live_prices:
            missing_instruments.append(inst)

    # Fallback to API
    if missing_instruments:
        try: 
            q = kite.quote(missing_instruments)
            live_prices.update(q)
        except: 
            if not live_prices: return

    # --- PHASE 3: LOGIC & QUEUING (NO LOCK) ---
    actions_queue = [] # [{'id': 1, 'type': 'EXIT', 'reason': 'SL', ...}]
    updates_queue = [] # [{'id': 1, 'data': {'current_ltp': 100}}]
    
    # 3.1 Active Trades Logic
    for t in active_trades_snapshot:
        inst_key = f"{t['exchange']}:{t['symbol']}"
        if inst_key not in live_prices: continue
             
        ltp = live_prices[inst_key]['last_price']
        
        # Track Update
        if t.get('current_ltp') != ltp:
            updates_queue.append({'id': t['id'], 'data': {'current_ltp': ltp}})
        
        # A. PENDING ORDERS
        if t['status'] == "PENDING":
            condition_met = False
            if t.get('trigger_dir') == 'BELOW':
                if ltp <= t['entry_price']: condition_met = True
            elif t.get('trigger_dir') == 'ABOVE':
                if ltp >= t['entry_price']: condition_met = True
            
            if condition_met:
                actions_queue.append({'id': t['id'], 'type': 'ACTIVATE', 'ltp': ltp})
            continue

        # B. ACTIVE ORDERS
        if t['status'] in ['OPEN', 'PROMOTED_LIVE']:
            current_high = t.get('highest_ltp', 0)
            
            # Check High Made
            if ltp > current_high:
                updates_queue.append({'id': t['id'], 'data': {'highest_ltp': ltp, 'made_high': ltp}})
                
                # Check Notification T3 Logic (Simplified for brevity, logic remains)
                has_crossed_t3 = False
                if 2 in t.get('targets_hit_indices', []): has_crossed_t3 = True
                elif t.get('targets') and len(t['targets']) > 2 and ltp >= t['targets'][2]: has_crossed_t3 = True

                if has_crossed_t3:
                     # Add notification action? Or handle in commit.
                     # For safety, let's allow notify in commit phase.
                     pass 
            
            # Step Trailing Logic
            if t.get('trailing_sl', 0) > 0:
                step = t['trailing_sl']
                current_sl = t['sl']
                diff = ltp - (current_sl + step)
                
                if diff >= step:
                    steps_to_move = int(diff / step)
                    new_sl = current_sl + (steps_to_move * step)
                    
                    sl_limit = float('inf')
                    mode_sl = int(t.get('sl_to_entry', 0))
                    if mode_sl == 1: sl_limit = t['entry_price']
                    elif mode_sl == 2 and t.get('targets'): sl_limit = t['targets'][0]
                    elif mode_sl == 3 and t.get('targets') and len(t['targets']) > 1: sl_limit = t['targets'][1]
                    
                    if mode_sl > 0: new_sl = min(new_sl, sl_limit)
                    
                    if new_sl > t['sl']:
                        actions_queue.append({'id': t['id'], 'type': 'UPDATE_SL', 'sl': new_sl})

            exit_triggered = False
            exit_reason = ""
            
            # Check SL Hit
            if ltp <= t['sl']:
                exit_triggered = True
                exit_reason = "SL_HIT"
                
            # Check Target Hits
            elif not exit_triggered and t.get('targets'):
                controls = t.get('target_controls', [{'enabled':True, 'lots':0}]*3)
                for i, tgt in enumerate(t['targets']):
                    if i not in t.get('targets_hit_indices', []) and ltp >= tgt:
                        actions_queue.append({'id': t['id'], 'type': 'TARGET_HIT', 'index': i, 'ltp': ltp})
                        
                        conf = controls[i]
                        # Trailing to Entry feature
                        if conf.get('trail_to_entry') and t['sl'] < t['entry_price']:
                             actions_queue.append({'id': t['id'], 'type': 'UPDATE_SL', 'sl': t['entry_price']})
                        
                        if not conf['enabled']: continue
                        
                        lot_size = t.get('lot_size') or smart_trader.get_lot_size(t['symbol'])
                        qty_to_exit = conf.get('lots', 0) * lot_size
                        
                        if qty_to_exit >= t['quantity']:
                             exit_triggered = True
                             exit_reason = "TARGET_HIT"
                             break
                        elif qty_to_exit > 0:
                             # Partial Exit
                             actions_queue.append({'id': t['id'], 'type': 'PARTIAL_EXIT', 'qty': qty_to_exit, 'ltp': ltp})

            if exit_triggered:
                actions_queue.append({'id': t['id'], 'type': 'EXIT', 'reason': exit_reason, 'price': t['sl'] if exit_reason=="SL_HIT" else ltp})

    # --- PHASE 4: EXECUTE BROKER ACTIONS (NO LOCK) ---
    for action in actions_queue:
        # Re-fetch context from snapshot
        t_ctx = next((x for x in active_trades_snapshot if x['id'] == action['id']), None)
        if not t_ctx: continue

        if action['type'] == 'ACTIVATE':
            if t_ctx['mode'] == 'LIVE':
                try: 
                    kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t_ctx['symbol'], exchange=t_ctx['exchange'], transaction_type=kite.TRANSACTION_TYPE_BUY, quantity=t_ctx['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                    sl_id = kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t_ctx['symbol'], exchange=t_ctx['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t_ctx['quantity'], order_type=kite.ORDER_TYPE_SL_M, product=kite.PRODUCT_MIS, trigger_price=t_ctx['sl'])
                    action['sl_order_id'] = sl_id
                except Exception as e:
                    action['error'] = str(e)
        
        elif action['type'] == 'UPDATE_SL':
            if t_ctx['mode'] == 'LIVE' and t_ctx.get('sl_order_id'):
                try: 
                    kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=t_ctx['sl_order_id'], trigger_price=action['sl'])
                except: pass
        
        elif action['type'] == 'PARTIAL_EXIT':
             if t_ctx['mode'] == 'LIVE':
                try:
                    manage_broker_sl(kite, t_ctx, action['qty']) # Reduce SL qty
                    kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t_ctx['symbol'], exchange=t_ctx['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=action['qty'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                except: pass

        elif action['type'] == 'EXIT':
            if t_ctx['mode'] == "LIVE":
                try:
                    manage_broker_sl(kite, t_ctx, cancel_completely=True)
                    kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t_ctx['symbol'], exchange=t_ctx['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t_ctx['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                except: pass

    # --- PHASE 5: COMMIT (LOCK) ---
    with TRADE_LOCK:
        real_trades = load_trades()
        updated = False
        
        # Apply Updates (LTP, Highs)
        for up in updates_queue:
            t = next((x for x in real_trades if x['id'] == up['id']), None)
            if t:
                # Merge dicts
                for k, v in up['data'].items():
                    t[k] = v
                updated = True
        
        # Apply Actions
        for action in actions_queue:
            t = next((x for x in real_trades if x['id'] == action['id']), None)
            if not t: continue
            
            if action['type'] == 'ACTIVATE':
                if t['status'] == 'PENDING':
                    t['status'] = 'OPEN'
                    t['highest_ltp'] = t['entry_price']
                    t['current_ltp'] = action['ltp']
                    if 'sl_order_id' in action: t['sl_order_id'] = action['sl_order_id']
                    
                    log_event(t, f"Order ACTIVATED @ {action['ltp']}")
                    telegram_bot.notify_trade_event(t, "ACTIVE", action['ltp'])
                    updated = True
            
            elif action['type'] == 'UPDATE_SL':
                t['sl'] = action['sl']
                log_event(t, f"Step Trailing: SL Moved to {t['sl']:.2f}")
                updated = True
            
            elif action['type'] == 'TARGET_HIT':
                if action['index'] not in t.get('targets_hit_indices', []):
                    t.setdefault('targets_hit_indices', []).append(action['index'])
                    telegram_bot.notify_trade_event(t, "TARGET_HIT", {'t_num': action['index']+1, 'price': action['ltp']})
                    updated = True
            
            elif action['type'] == 'PARTIAL_EXIT':
                t['quantity'] -= action['qty']
                log_event(t, f"Target Hit. Exited {action['qty']} Qty")
                updated = True

            elif action['type'] == 'EXIT':
                if t['status'] in ['OPEN', 'PROMOTED_LIVE']:
                    final_price = action['price']
                    exit_reason = action['reason']
                    
                    if exit_reason == "SL_HIT":
                        trade_snap = t.copy()
                        trade_snap['exit_price'] = final_price
                        pnl_realized = (final_price - t['entry_price']) * t['quantity']
                        telegram_bot.notify_trade_event(trade_snap, "SL_HIT", pnl_realized)
                    
                    move_to_history(t, exit_reason, final_price)
                    updated = True

        if updated: 
            save_trades(real_trades)

    # --- PHASE 6: HISTORY TRACKING (SEPARATE DB SESSION) ---
    history_updated = False
    try:
        for t in todays_closed:
            if t.get('virtual_sl_hit', False): continue

            inst_key = f"{t['exchange']}:{t['symbol']}"
            if inst_key in live_prices:
                ltp = live_prices[inst_key]['last_price']
                
                # Check Virtual SL
                is_dead = False
                if t['entry_price'] > t['sl']: # BUY
                     if ltp <= t['sl']: is_dead = True
                else: # SELL
                     if ltp >= t['sl']: is_dead = True
                
                t['current_ltp'] = ltp
                
                if is_dead:
                    t['virtual_sl_hit'] = True
                    db.session.merge(TradeHistory(id=t['id'], data=json.dumps(t)))
                    history_updated = True
                    continue

                # Check High Made
                current_high = t.get('made_high', t['entry_price'])
                if ltp > current_high:
                    t['made_high'] = ltp
                    try: telegram_bot.notify_trade_event(t, "HIGH_MADE", ltp)
                    except: pass
                    
                db.session.merge(TradeHistory(id=t['id'], data=json.dumps(t)))
                history_updated = True
                
    except Exception as e:
        print(f"Error in History Tracker: {e}")
    
    if history_updated: 
        db.session.commit()
