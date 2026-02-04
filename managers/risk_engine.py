import json
import time
import threading
from kiteconnect import KiteTicker
import smart_trader
import settings
from datetime import datetime
from database import db, TradeHistory
from managers.persistence import load_trades, save_trades, load_history, get_risk_state, save_risk_state
from managers.common import IST, log_event
from managers.broker_ops import manage_broker_sl, move_to_history
from managers.telegram_manager import bot as telegram_bot

# --- GLOBAL OBJECTS FOR WEBSOCKET ---
kws = None
kite_client = None  # Reference to KiteConnect instance for placing orders
flask_app = None    # Reference to Flask App for DB Context
socket_io_server = None # Reference to SocketIO Server for emitting events

# --- REPORTING FUNCTIONS ---

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
            
            display_status = raw_status
            is_direct_sl = False 
            
            if raw_status == "NOT_ACTIVE" or (raw_status == "TIME_EXIT" and pnl == 0):
                display_status = "Not Active"
                cnt_not_active += 1
                is_direct_sl = True 
            elif raw_status == "SL_HIT":
                if not t.get('targets_hit_indices'): 
                    display_status = "Stop-Loss"
                    cnt_direct_sl += 1
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

            total_pnl += pnl
            if pnl >= 0: total_wins += pnl
            else: total_loss += pnl
            
            total_max_potential += max_pot_val
            total_funds_used += (entry * qty)
            
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
        telegram_bot.send_message(msg_summary)

    except Exception as e:
        print(f"Error generating EOD report: {e}")

def send_manual_trade_status(mode):
    try:
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history = load_history()
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
            if t.get('virtual_sl_hit'): track_tag = "🔴"

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
        if trade.get('virtual_sl_hit'): track_tag = "🔴"
        
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
    1. Universal Square-off Time (e.g., 15:25)
    2. Profit Locking (Global PnL Trailing)
    """
    trades = load_trades()
    now = datetime.now(IST)
    exit_time_str = mode_settings.get('universal_exit_time', "15:25")
    today_str = now.strftime("%Y-%m-%d")
    state = get_risk_state(mode)
    
    # --- 1. TIME BASED EXIT ---
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

    # --- 2. PROFIT LOCKING ---
    pnl_start = float(mode_settings.get('profit_lock', 0))
    if pnl_start > 0:
        current_total_pnl = 0.0
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        history = load_history()
        for t in history:
            if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode: 
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

# --- WEB SOCKET LOGIC ---

def on_ticks(ws, ticks):
    """
    Triggered whenever a price update is received from Zerodha.
    Handles Active Trades and Closed Trades (Virtual SL & Monitoring).
    """
    global kite_client, flask_app, socket_io_server
    
    if not flask_app: return

    # Use App Context for DB operations inside this thread
    with flask_app.app_context():
        active_trades = load_trades()
        
        # Load Today's Closed Trades for Virtual Tracking
        history = load_history()
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        todays_closed = [t for t in history if t.get('exit_time') and t['exit_time'].startswith(today_str)]
        
        if not active_trades and not todays_closed: return

        # Map Ticks: {instrument_token: last_price}
        tick_map = {t['instrument_token']: t['last_price'] for t in ticks}
        
        active_list = []
        updated = False
        
        # --- 1. PROCESS ACTIVE TRADES ---
        for t in active_trades:
            token = t.get('instrument_token')
            
            # If no update for this trade, keep as is
            if not token or token not in tick_map:
                active_list.append(t)
                continue
                
            ltp = tick_map[token]
            
            # Update internal LTP
            if t.get('current_ltp') != ltp:
                t['current_ltp'] = ltp
                updated = True
            
            # A. PENDING ORDERS (Activation)
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
                    telegram_bot.notify_trade_event(t, "ACTIVE", ltp)
                    
                    if t['mode'] == 'LIVE' and kite_client:
                        try:
                            kite_client.place_order(
                                variety=kite_client.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'],
                                transaction_type=kite_client.TRANSACTION_TYPE_BUY, quantity=t['quantity'],
                                order_type=kite_client.ORDER_TYPE_MARKET, product=kite_client.PRODUCT_MIS, tag="RD_ENTRY"
                            )
                            # Place Broker SL
                            sl_id = kite_client.place_order(
                                variety=kite_client.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'],
                                transaction_type=kite_client.TRANSACTION_TYPE_SELL, quantity=t['quantity'],
                                order_type=kite_client.ORDER_TYPE_SL_M, product=kite_client.PRODUCT_MIS,
                                trigger_price=t['sl'], tag="RD_SL"
                            )
                            t['sl_order_id'] = sl_id
                        except Exception as e:
                            log_event(t, f"Broker Fail (Active): {e}")

                    active_list.append(t)
                else:
                    active_list.append(t)
                continue

            # B. ACTIVE ORDERS
            if t['status'] in ['OPEN', 'PROMOTED_LIVE']:
                current_high = t.get('highest_ltp', 0)
                
                # High Made
                if ltp > current_high:
                    t['highest_ltp'] = ltp
                    t['made_high'] = ltp
                    
                    has_crossed_t3 = False
                    if 2 in t.get('targets_hit_indices', []): has_crossed_t3 = True
                    elif t.get('targets') and len(t['targets']) > 2 and ltp >= t['targets'][2]: has_crossed_t3 = True
                    
                    if has_crossed_t3:
                        telegram_bot.notify_trade_event(t, "HIGH_MADE", ltp)

                # Trailing SL
                if t.get('trailing_sl', 0) > 0:
                    step = t['trailing_sl']
                    diff = ltp - (t['sl'] + step)
                    if diff >= step:
                        steps_to_move = int(diff / step)
                        new_sl = t['sl'] + (steps_to_move * step)
                        
                        sl_limit = float('inf')
                        mode = int(t.get('sl_to_entry', 0))
                        if mode == 1: sl_limit = t['entry_price']
                        elif mode == 2 and t.get('targets'): sl_limit = t['targets'][0]
                        elif mode == 3 and t.get('targets') and len(t['targets']) > 1: sl_limit = t['targets'][1]
                        
                        if mode > 0: new_sl = min(new_sl, sl_limit)
                        
                        if new_sl > t['sl']:
                            t['sl'] = new_sl
                            if t['mode'] == 'LIVE' and t.get('sl_order_id') and kite_client:
                                try: kite_client.modify_order(variety=kite_client.VARIETY_REGULAR, order_id=t['sl_order_id'], trigger_price=new_sl)
                                except: pass
                            log_event(t, f"Step Trailing: SL Moved to {t['sl']:.2f}")

                exit_triggered = False
                exit_reason = ""
                
                # Check SL
                if ltp <= t['sl']:
                    exit_triggered = True
                    exit_reason = "SL_HIT"
                
                # Check Targets
                elif not exit_triggered and t.get('targets'):
                    controls = t.get('target_controls', [{'enabled':True, 'lots':0}]*3)
                    for i, tgt in enumerate(t['targets']):
                        if i not in t.get('targets_hit_indices', []) and ltp >= tgt:
                            t.setdefault('targets_hit_indices', []).append(i)
                            conf = controls[i]
                            telegram_bot.notify_trade_event(t, "TARGET_HIT", {'t_num': i+1, 'price': tgt})
                            
                            # Trail to Entry Feature
                            if conf.get('trail_to_entry') and t['sl'] < t['entry_price']:
                                t['sl'] = t['entry_price']
                                log_event(t, f"Target {i+1} Hit: SL Trailed to Entry")
                                if t['mode'] == 'LIVE' and t.get('sl_order_id') and kite_client:
                                    try: kite_client.modify_order(variety=kite_client.VARIETY_REGULAR, order_id=t['sl_order_id'], trigger_price=t['sl'])
                                    except: pass
                            
                            if not conf['enabled']: continue
                            
                            lot_size = t.get('lot_size') or smart_trader.get_lot_size(t['symbol'])
                            qty_to_exit = conf.get('lots', 0) * lot_size
                            
                            if qty_to_exit >= t['quantity']:
                                exit_triggered = True
                                exit_reason = "TARGET_HIT"
                                break
                            elif qty_to_exit > 0:
                                if t['mode'] == 'LIVE' and kite_client: manage_broker_sl(kite_client, t, qty_to_exit)
                                t['quantity'] -= qty_to_exit
                                log_event(t, f"Target {i+1} Hit. Exited {qty_to_exit}")
                                if t['mode'] == 'LIVE' and kite_client:
                                    try: kite_client.place_order(variety=kite_client.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite_client.TRANSACTION_TYPE_SELL, quantity=qty_to_exit, order_type=kite_client.ORDER_TYPE_MARKET, product=kite_client.PRODUCT_MIS)
                                    except: pass

                if exit_triggered:
                    if t['mode'] == "LIVE" and kite_client:
                        manage_broker_sl(kite_client, t, cancel_completely=True)
                        try: kite_client.place_order(variety=kite_client.VARIETY_REGULAR, tradingsymbol=t['symbol'], exchange=t['exchange'], transaction_type=kite_client.TRANSACTION_TYPE_SELL, quantity=t['quantity'], order_type=kite.ORDER_TYPE_MARKET, product=kite.PRODUCT_MIS)
                        except: pass
                    
                    final_price = t['sl'] if exit_reason=="SL_HIT" else (t['targets'][-1] if exit_reason=="TARGET_HIT" else ltp)
                    if exit_reason == "SL_HIT":
                        t['exit_price'] = final_price
                        telegram_bot.notify_trade_event(t, "SL_HIT", (final_price - t['entry_price']) * t['quantity'])
                    
                    move_to_history(t, exit_reason, final_price)
                else:
                    active_list.append(t)
        
        if updated:
            save_trades(active_list)
            # Emit real-time update to Frontend for Active Trades
            if socket_io_server:
                try:
                    socket_io_server.emit('trade_update', active_list)
                except Exception as e:
                    print(f"Socket Emit Error: {e}")

        # --- 2. PROCESS CLOSED TRADES (Modified for Live LTP & Virtual SL) ---
        history_updated = False
        live_closed_updates = []  # List to store live updates for frontend

        try:
            for t in todays_closed:
                # [CHANGED] Removed the check that skipped updates for 'dead' trades
                # if t.get('virtual_sl_hit', False): continue 
                
                token = t.get('instrument_token')
                if not token or token not in tick_map: continue
                
                ltp = tick_map[token]
                t['current_ltp'] = ltp
                
                # [CHANGED] Always add to update list so Frontend gets the live price
                live_closed_updates.append(t) 
                
                # [CHANGED] Only run the "Logic" (Virtual SL / High Made) if not already dead
                if not t.get('virtual_sl_hit', False):
                    
                    # Check Virtual SL (Entry vs SL direction)
                    is_dead = False
                    if t['entry_price'] > t['sl']: # BUY
                         if ltp <= t['sl']: is_dead = True
                    else: # SELL
                         if ltp >= t['sl']: is_dead = True
                    
                    if is_dead:
                        t['virtual_sl_hit'] = True
                        db.session.merge(TradeHistory(id=t['id'], data=json.dumps(t)))
                        history_updated = True
                        continue # Skip High Check if just died

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

        # Emit Real-Time Closed Trade Updates to Frontend
        if socket_io_server and live_closed_updates:
            try:
                socket_io_server.emit('closed_trade_update', live_closed_updates)
            except Exception as e:
                print(f"Socket Emit Error (Closed): {e}")

def on_connect(ws, response):
    print("✅ WebSocket Connected! Resubscribing...")
    subscribe_active_trades(ws)

def on_close(ws, code, reason):
    print(f"⚠️ WebSocket Closed: {code} - {reason}")

def subscribe_active_trades(ws):
    with flask_app.app_context():
        # Get Active Trade Tokens
        trades = load_trades()
        active_tokens = [int(t['instrument_token']) for t in trades if t.get('instrument_token')]
        
        # Get Closed Trade Tokens (for Today) to track Missed Opportunities
        history = load_history()
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        closed_tokens = [int(t['instrument_token']) for t in history 
                         if t.get('exit_time') and t['exit_time'].startswith(today_str) and t.get('instrument_token')]
        
        # Combine unique tokens
        all_tokens = list(set(active_tokens + closed_tokens))
        
        if all_tokens:
            ws.subscribe(all_tokens)
            ws.set_mode(ws.MODE_FULL, all_tokens)
            print(f"📡 Subscribed to {len(all_tokens)} tokens (Active + Closed).")

def start_ticker(api_key, access_token, kite_inst, app_inst, socket_inst=None):
    """
    Initializes and starts the KiteTicker (or MockTicker).
    """
    global kws, kite_client, flask_app, socket_io_server
    kite_client = kite_inst
    flask_app = app_inst
    socket_io_server = socket_inst # Store the SocketIO instance
    
    # --- MOCK BROKER DETECTION ---
    if hasattr(kite_inst, "mock_instruments"):
        print("⚠️ Starting MOCK Ticker...")
        from mock_broker import MockKiteTicker
        kws = MockKiteTicker(api_key, access_token)
    else:
        kws = KiteTicker(api_key, access_token)
    # -----------------------------

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close
    
    # Run in a separate thread so it doesn't block Flask
    kws.connect(threaded=True)
    return kws

def update_subscriptions():
    """
    Call this function whenever a NEW trade is added to dynamically subscribe.
    """
    if kws and kws.is_connected():
        subscribe_active_trades(kws)
