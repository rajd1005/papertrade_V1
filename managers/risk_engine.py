import json
import smart_trader
import settings
from datetime import datetime
from database import db, TradeHistory
from managers.persistence import TRADE_LOCK, load_trades, save_trades, load_history, load_todays_history, get_risk_state, save_risk_state
from managers.common import IST, log_event
from managers.broker_ops import manage_broker_sl, move_to_history
from managers.telegram_manager import bot as telegram_bot
from managers import zerodha_ticker

# --- End of Day Report Helper (Automated) ---
def send_eod_report(mode):
    """
    Generates and sends two Telegram reports:
    1. Individual Trade Status (Entries, Exits, Highs, Potentials)
    2. Aggregate Summary (Total P/L, Funds, Wins/Losses)
    """
    try:
        # [OPTIMIZATION] Use load_todays_history() instead of loading full history
        history = load_todays_history()
        
        # Filter for specific Mode (LIVE/PAPER)
        todays_trades = [t for t in history if t['mode'] == mode]
        
        if not todays_trades:
            return

        # --- REPORT 1: INDIVIDUAL TRADE DETAILS ---
        msg_details = f"📊 <b>{mode} - FINAL TRADE STATUS</b>\n"
        
        total_pnl = 0.0
        total_wins = 0.0
        total_loss = 0.0
        total_funds_used = 0.0
        total_max_potential = 0.0
        
        # Counters
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

# --- Manual Report Helpers ---

def send_manual_trade_status(mode):
    """
    Sends the detailed 'Final Trade Status' report for all trades of the day (Manual Trigger).
    """
    try:
        history = load_todays_history()
        todays_trades = [t for t in history if t['mode'] == mode]
        
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
            track_tag = "🟢"
            if t.get('virtual_sl_hit'):
                track_tag = "🔴"

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
    try:
        history = load_history()
        trade = next((t for t in history if str(t['id']) == str(trade_id)), None)
        if not trade:
            active = load_trades()
            trade = next((t for t in active if str(t['id']) == str(trade_id)), None)
            
        if not trade:
            return {"status": "error", "message": "Trade not found"}

        raw_symbol = trade.get('symbol', 'Unknown')
        symbol = smart_trader.get_telegram_symbol(raw_symbol)
        
        entry = trade.get('entry_price', 0)
        sl = trade.get('sl', 0)
        targets = trade.get('targets', [])
        raw_status = trade.get('status', 'UNKNOWN')
        qty = trade.get('quantity', 0)
        pnl = trade.get('pnl', 0)
        
        display_status = raw_status
        is_direct_sl = False

        if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
            display_status = "Not Active"
            is_direct_sl = True
        elif raw_status == "SL_HIT" and not trade.get('targets_hit_indices'):
            display_status = "Stop-Loss"
            is_direct_sl = True
        
        made_high = trade.get('made_high', trade.get('exit_price', entry))
        track_tag = "🟢"
        if trade.get('virtual_sl_hit'):
            track_tag = "🔴"
        
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
    try:
        history = load_todays_history()
        todays_trades = [t for t in history if t['mode'] == mode]
        
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
    1. Universal Square-off Time
    2. Profit Locking
    """
    with TRADE_LOCK:
        trades = load_trades()
        now = datetime.now(IST)
        exit_time_str = mode_settings.get('universal_exit_time', "15:25")
        today_str = now.strftime("%Y-%m-%d")
        state = get_risk_state(mode)
        
        # 1. TIME EXIT
        try:
            exit_dt = datetime.strptime(exit_time_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
            exit_dt = IST.localize(exit_dt.replace(tzinfo=None))
            
            if now >= exit_dt and (now - exit_dt).seconds < 120:
                 if state.get('last_eod_date') != today_str:
                     active_mode = [t for t in trades if t['mode'] == mode]
                     if active_mode:
                         for t in active_mode:
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
                         
                         remaining = [t for t in trades if t['mode'] != mode]
                         save_trades(remaining)
                     
                     send_eod_report(mode)
                     state['last_eod_date'] = today_str
                     save_risk_state(mode, state)
                     return
        except Exception as e: 
            print(f"Time Check Error: {e}")

        # 2. PROFIT LOCKING
        pnl_start = float(mode_settings.get('profit_lock', 0))
        if pnl_start > 0:
            current_total_pnl = 0.0
            history = load_todays_history()
            for t in history:
                if t['mode'] == mode: 
                    current_total_pnl += t.get('pnl', 0)
            
            active = [t for t in trades if t['mode'] == mode]
            for t in active:
                if t['status'] != 'PENDING':
                    current_total_pnl += (t.get('current_ltp', t['entry_price']) - t['entry_price']) * t['quantity']

            if not state.get('active') and current_total_pnl >= pnl_start:
                state['active'] = True
                state['high_pnl'] = current_total_pnl
                state['global_sl'] = float(mode_settings.get('profit_min', 0))
                save_risk_state(mode, state)
            
            if state.get('active'):
                if current_total_pnl > state['high_pnl']:
                    diff = current_total_pnl - state['high_pnl']
                    trail_step = float(mode_settings.get('profit_trail', 0))
                    if trail_step > 0 and diff >= trail_step:
                         steps = int(diff / trail_step)
                         state['global_sl'] += (steps * trail_step)
                         state['high_pnl'] = current_total_pnl
                         save_risk_state(mode, state)

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
                    state['active'] = False
                    save_risk_state(mode, state)

def update_risk_engine(kite):
    """
    The main monitoring loop called by the background thread.
    NOW STRICTLY WEBSOCKET ONLY (No API Fallback).
    Fix: Smarter Subscription logic to prevent spamming Zerodha.
    """
    current_settings = settings.load_settings()
    check_global_exit_conditions(kite, "PAPER", current_settings['modes']['PAPER'])
    check_global_exit_conditions(kite, "LIVE", current_settings['modes']['LIVE'])

    with TRADE_LOCK:
        active_trades = load_trades()
        todays_closed = load_todays_history()

        active_symbols = [f"{t['exchange']}:{t['symbol']}" for t in active_trades]
        closed_symbols = [f"{t['exchange']}:{t['symbol']}" for t in todays_closed]
        
        all_instruments = list(set(active_symbols + closed_symbols))

        if not all_instruments: 
            return

        # --- SMART SUBSCRIPTION & FETCHING ---
        live_prices = {}
        tokens_to_sub = []

        if zerodha_ticker.ticker:
            for inst in all_instruments:
                parts = inst.split(":")
                if len(parts) == 2:
                    exch, sym = parts
                    tok = smart_trader.get_instrument_token(sym, exch)
                    if tok:
                        # 1. Try to get LTP from cache
                        ltp = zerodha_ticker.ticker.get_ltp(tok)
                        
                        if ltp and ltp > 0:
                            # Data exists, use it
                            live_prices[inst] = {'last_price': ltp}
                        else:
                            # Data missing, mark for subscription
                            tokens_to_sub.append(tok)
            
            # 2. Subscribe only to tokens that are missing data
            if tokens_to_sub:
                # Deduplicate to be safe
                unique_tokens = list(set(tokens_to_sub))
                # print(f"[RiskEngine] Subscribing to missing tokens: {unique_tokens}") # Debug log
                zerodha_ticker.ticker.subscribe(unique_tokens)
                # Data will arrive in the next tick(s)

        # --- Process ACTIVE TRADES ---
        active_list = []
        updated = False
        
        for t in active_trades:
            try:
                inst_key = f"{t['exchange']}:{t['symbol']}"
                
                # Check if we have valid price data
                if inst_key not in live_prices:
                     # If missing, keep the trade but don't process logic this tick.
                     # Since we subscribed above, data should arrive shortly.
                     active_list.append(t)
                     continue
                     
                ltp = live_prices[inst_key]['last_price']
                
                # Update LTP immediately
                if t.get('current_ltp') != ltp:
                    t['current_ltp'] = ltp
                    updated = True
                
                # A. PENDING ORDERS
                if t['status'] == "PENDING":
                    condition_met = False
                    if t.get('trigger_dir') == 'BELOW':
                        if ltp <= t['entry_price']: condition_met = True
                    elif t.get('trigger_dir') == 'ABOVE':
                        if ltp >= t['entry_price']: condition_met = True
                    
                    if condition_met:
                        t['status'] = "OPEN"
                        t['highest_ltp'] = t['entry_price']
                        log_event(t, f"Order ACTIVATED @ {ltp}")
                        updated = True
                        
                        telegram_bot.notify_trade_event(t, "ACTIVE", ltp)
                        
                        if t['mode'] == 'LIVE':
                            try: 
                                kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_BUY, quantity=t['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                                try:
                                    sl_id = kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t['quantity'], order_type=kite.ORDER_TYPE_SL_M, product=kite.PRODUCT_MIS, trigger_price=t['sl'])
                                    t['sl_order_id'] = sl_id
                                except: 
                                    log_event(t, "Broker SL Fail")
                            except Exception as e: 
                                log_event(t, f"Broker Fail: {e}")
                        
                        active_list.append(t)
                    else: 
                        active_list.append(t)
                    continue

                # B. ACTIVE ORDERS
                if t['status'] in ['OPEN', 'PROMOTED_LIVE']:
                    current_high = t.get('highest_ltp', 0)
                    
                    # --- High Made Logic ---
                    if ltp > current_high:
                        t['highest_ltp'] = ltp
                        t['made_high'] = ltp
                        updated = True
                        
                        has_crossed_t3 = False
                        if 2 in t.get('targets_hit_indices', []):
                            has_crossed_t3 = True
                        elif t.get('targets') and len(t['targets']) > 2 and ltp >= t['targets'][2]:
                            has_crossed_t3 = True

                        if has_crossed_t3:
                             telegram_bot.notify_trade_event(t, "HIGH_MADE", ltp)
                    
                    # --- Step Trailing Logic ---
                    if t.get('trailing_sl', 0) > 0:
                        step = t['trailing_sl']
                        current_sl = t['sl']
                        diff = ltp - (current_sl + step)
                        
                        if diff >= step:
                            steps_to_move = int(diff / step)
                            new_sl = current_sl + (steps_to_move * step)
                            
                            sl_limit = float('inf')
                            mode = int(t.get('sl_to_entry', 0))
                            if mode == 1: sl_limit = t['entry_price']
                            elif mode == 2 and t.get('targets'): sl_limit = t['targets'][0]
                            elif mode == 3 and t.get('targets') and len(t['targets']) > 1: sl_limit = t['targets'][1]
                            
                            if mode > 0: 
                                new_sl = min(new_sl, sl_limit)
                            
                            if new_sl > t['sl']:
                                t['sl'] = new_sl
                                updated = True
                                if t['mode'] == 'LIVE' and t.get('sl_order_id'):
                                    try: 
                                        kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=t['sl_order_id'], trigger_price=new_sl)
                                    except: pass
                                log_event(t, f"Step Trailing: SL Moved to {t['sl']:.2f} (LTP {ltp})")

                    exit_triggered = False
                    exit_reason = ""
                    
                    # --- Check SL Hit ---
                    if ltp <= t['sl']:
                        exit_triggered = True
                        exit_reason = "SL_HIT"
                        
                    # --- Check Target Hits ---
                    elif not exit_triggered and t.get('targets'):
                        controls = t.get('target_controls', [{'enabled':True, 'lots':0}]*3)
                        
                        for i, tgt in enumerate(t['targets']):
                            if i not in t.get('targets_hit_indices', []) and ltp >= tgt:
                                t.setdefault('targets_hit_indices', []).append(i)
                                updated = True
                                conf = controls[i]
                                
                                telegram_bot.notify_trade_event(t, "TARGET_HIT", {'t_num': i+1, 'price': tgt})

                                if conf.get('trail_to_entry') and t['sl'] < t['entry_price']:
                                    t['sl'] = t['entry_price']
                                    log_event(t, f"Target {i+1} Hit: SL Trailed to Entry ({t['sl']})")
                                    if t['mode'] == 'LIVE' and t.get('sl_order_id'):
                                        try: 
                                            kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=t['sl_order_id'], trigger_price=t['sl'])
                                        except: pass

                                if not conf['enabled']: 
                                    continue
                                
                                lot_size = t.get('lot_size') or smart_trader.get_lot_size(t['symbol'])
                                qty_to_exit = conf.get('lots', 0) * lot_size
                                
                                if qty_to_exit >= t['quantity']:
                                     exit_triggered = True
                                     exit_reason = "TARGET_HIT"
                                     break
                                elif qty_to_exit > 0:
                                     if t['mode'] == 'LIVE': 
                                         manage_broker_sl(kite, t, qty_to_exit)
                                     
                                     t['quantity'] -= qty_to_exit
                                     log_event(t, f"Target {i+1} Hit. Exited {qty_to_exit} Qty")
                                     
                                     if t['mode'] == 'LIVE':
                                        try: 
                                            kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=qty_to_exit, order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                                        except: pass

                    # --- Execute Exit ---
                    if exit_triggered:
                        updated = True
                        if t['mode'] == "LIVE":
                            manage_broker_sl(kite, t, cancel_completely=True)
                            try: 
                                kite.place_order(variety=kite.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=t['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                            except: pass
                        
                        final_price = t['sl'] if exit_reason=="SL_HIT" else (t['targets'][-1] if exit_reason=="TARGET_HIT" else ltp)
                        
                        if exit_reason == "SL_HIT":
                            trade_snap = t.copy()
                            trade_snap['exit_price'] = final_price
                            pnl_realized = (final_price - t['entry_price']) * t['quantity']
                            telegram_bot.notify_trade_event(trade_snap, "SL_HIT", pnl_realized)
                        
                        move_to_history(t, exit_reason, final_price)
                    else:
                        active_list.append(t)
            except Exception as e:
                # Log error but don't crash loop
                print(f"Error processing trade {t.get('symbol', 'UNKNOWN')}: {e}")
                active_list.append(t)
        
        if updated: 
            save_trades(active_list)

        # --- Process CLOSED TRADES (Missed Opportunity Tracker) ---
        history_updated = False
        try:
            for t in todays_closed:
                if t.get('virtual_sl_hit', False):
                    continue

                inst_key = f"{t['exchange']}:{t['symbol']}"
                if inst_key in live_prices:
                    ltp = live_prices[inst_key]['last_price']
                    
                    t['current_ltp'] = ltp

                    # Check Virtual SL
                    is_dead = False
                    if t['entry_price'] > t['sl']: # BUY
                         if ltp <= t['sl']: is_dead = True
                    else: # SELL
                         if ltp >= t['sl']: is_dead = True
                    
                    if is_dead:
                        t['virtual_sl_hit'] = True
                        db.session.merge(TradeHistory(id=t['id'], data=json.dumps(t)))
                        history_updated = True
                        continue

                    # Check High Made
                    current_high = t.get('made_high', t['entry_price'])
                    if ltp > current_high:
                        t['made_high'] = ltp
                        try:
                            telegram_bot.notify_trade_event(t, "HIGH_MADE", ltp)
                        except: pass
                        
                    db.session.merge(TradeHistory(id=t['id'], data=json.dumps(t)))
                    history_updated = True
                    
        except Exception as e:
            print(f"Error in History Tracker: {e}")
        
        if history_updated: 
            db.session.commit()
