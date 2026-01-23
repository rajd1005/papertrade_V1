from datetime import datetime
import time
import smart_trader
import settings
from managers.common import IST, get_exchange, log_event, get_time_str
from managers.persistence import TRADE_LOCK, load_trades, save_trades, load_history
from managers.broker_ops import move_to_history

def import_past_trade(kite, symbol, entry_dt_str, qty, entry_price, sl_price, targets, trailing_sl, sl_to_entry, exit_multiplier, target_controls, target_channels=['main']):
    """
    Simulates a trade based on historical data.
    Collects notification events (NEW_TRADE, ACTIVE, TARGET_HIT, SL_HIT) into a queue for sequential sending.
    """
    try:
        # 1. Parse Input & Initialize Data
        # HTML datetime-local input is naive (no timezone). We treat it as IST.
        try:
            entry_time = datetime.strptime(entry_dt_str, "%Y-%m-%dT%H:%M") 
            entry_time = IST.localize(entry_time)
        except Exception as e:
            return {"status": "error", "message": f"Date Parse Error: {e}"}

        try:
            s_cfg = settings.load_settings()
            exit_time_conf = s_cfg['modes']['PAPER'].get('universal_exit_time', "15:25")
            exit_H, exit_M = map(int, exit_time_conf.split(':'))
        except: exit_H, exit_M = 15, 25

        now = datetime.now(IST)
        exchange = get_exchange(symbol)
        
        token = smart_trader.get_instrument_token(symbol, exchange)
        if not token: 
            return {"status": "error", "message": "Symbol Token not found"}
        
        # Fetch Data
        hist_data = smart_trader.fetch_historical_data(kite, token, entry_time, now, "minute")
        if not hist_data: 
            return {"status": "error", "message": "No historical data found"}
        
        first_open = hist_data[0]['open']
        trigger_dir = "ABOVE" if first_open < entry_price else "BELOW"

        status = "PENDING"
        final_status = "PENDING" # Default for DB
        current_sl = float(sl_price)
        current_qty = int(qty)
        highest_ltp = float(entry_price)
        targets_hit_indices = []
        t_list = [float(x) for x in targets]
        realized_pnl = 0.0 
        
        logs = [f"[{entry_time.strftime('%Y-%m-%d %H:%M:%S')}] 📋 Replay Import Started. Entry: {entry_price}. Trigger: {trigger_dir}"]
        
        # --- Notification Queue ---
        notification_queue = []
        # Create a base object for the initial notification
        initial_trade_data = {
            "symbol": symbol, "mode": "PAPER", "order_type": "MARKET",
            "quantity": qty, "entry_price": entry_price, "sl": sl_price, "targets": t_list,
            "target_channels": target_channels # Store selected channels
        }
        notification_queue.append({'event': 'NEW_TRADE', 'data': initial_trade_data})

        exit_reason = ""
        final_exit_price = 0.0
        
        # 3. Candle-by-Candle Simulation
        for idx, candle in enumerate(hist_data):
            c_date_str = candle['date']
            
            # Universal Time Exit
            try:
                c_dt = datetime.strptime(c_date_str, "%Y-%m-%d %H:%M:%S")
                if c_dt.hour > exit_H or (c_dt.hour == exit_H and c_dt.minute >= exit_M):
                    if status == "OPEN":
                        final_status = "TIME_EXIT"; exit_reason = "TIME_EXIT"; final_exit_price = candle['open']
                        pnl_here = (final_exit_price - entry_price) * current_qty
                        realized_pnl += pnl_here
                        logs.append(f"[{c_date_str}] ⏰ Universal Time Exit @ {final_exit_price}")
                        current_qty = 0
                        break
                    elif status == "PENDING":
                        final_status = "NOT_ACTIVE"
                        exit_reason = "TIME_EXIT"
                        final_exit_price = entry_price 
                        realized_pnl = 0.0
                        logs.append(f"[{c_date_str}] ⏰ Universal Time Exit (Order Not Triggered)")
                        current_qty = 0
                        break

            except: pass

            O, H, L, C = candle['open'], candle['high'], candle['low'], candle['close']
            ticks = [O, L, H, C] if C >= O else [O, H, L, C]

            for ltp in ticks:
                # Activation
                if status == "PENDING":
                    activated = False
                    if trigger_dir == "ABOVE" and ltp >= entry_price: activated = True
                    elif trigger_dir == "BELOW" and ltp <= entry_price: activated = True
                    if activated:
                        # [FIX] Sync final_status immediately so DB knows it's OPEN
                        status = "OPEN"; final_status = "OPEN"; 
                        fill_price = entry_price; highest_ltp = max(fill_price, ltp)
                        logs.append(f"[{c_date_str}] 🚀 Order ACTIVATED @ {fill_price}")
                        # Notify Activation
                        notification_queue.append({'event': 'ACTIVE', 'data': {'price': fill_price, 'time': c_date_str}})
                        continue 

                # Risk Engine
                if status == "OPEN":
                    if ltp > highest_ltp:
                        highest_ltp = ltp
                        
                        # Check for High Made (Only if T3 is hit)
                        if 2 in targets_hit_indices: 
                             notification_queue.append({
                                'event': 'HIGH_MADE', 
                                'data': {'price': ltp, 'time': c_date_str}
                            })

                        t_sl = float(trailing_sl) if trailing_sl else 0
                        if t_sl > 0:
                            step = t_sl
                            diff = highest_ltp - (current_sl + step)
                            if diff >= step:
                                steps_to_move = int(diff / step)
                                new_sl = current_sl + (steps_to_move * step)
                                limit_val = float('inf')
                                mode = int(sl_to_entry)
                                if mode == 1: limit_val = entry_price
                                elif mode == 2 and len(t_list)>0: limit_val = t_list[0]
                                elif mode == 3 and len(t_list)>1: limit_val = t_list[1]
                                elif mode == 4 and len(t_list)>2: limit_val = t_list[2]
                                if mode > 0: new_sl = min(new_sl, limit_val)
                                if new_sl > current_sl:
                                    current_sl = new_sl
                                    logs.append(f"[{c_date_str}] 📈 Trailing SL Moved: {current_sl:.2f} (LTP: {ltp})")

                    # SL Hit
                    if ltp <= current_sl:
                        final_status = "SL_HIT"; exit_reason = "SL_HIT"; final_exit_price = current_sl
                        pnl_here = (current_sl - entry_price) * current_qty
                        realized_pnl += pnl_here
                        logs.append(f"[{c_date_str}] 🛑 SL Hit @ {current_sl}. Exited {current_qty} Qty.")
                        
                        # Notify SL
                        sl_snap = initial_trade_data.copy()
                        sl_snap['exit_price'] = current_sl
                        notification_queue.append({'event': 'SL_HIT', 'data': {'pnl': pnl_here, 'time': c_date_str}, 'trade': sl_snap})
                        
                        current_qty = 0
                        break

                    # Target Hits
                    for i, tgt in enumerate(t_list):
                        if i in targets_hit_indices: continue 
                        if ltp >= tgt:
                            targets_hit_indices.append(i)
                            # Notify Target
                            notification_queue.append({'event': 'TARGET_HIT', 'data': {'t_num': i+1, 'price': tgt, 'time': c_date_str}})
                            
                            conf = target_controls[i]
                            if conf.get('trail_to_entry') and current_sl < entry_price:
                                current_sl = entry_price
                                logs.append(f"[{c_date_str}] 🎯 Target {i+1} Hit: SL Trailed to Entry ({current_sl})")

                            if conf['enabled']:
                                lot_size = smart_trader.get_lot_size(symbol)
                                exit_qty = conf['lots'] * lot_size
                                if exit_qty >= current_qty or exit_qty >= 1000:
                                    final_status = "TARGET_HIT"; exit_reason = f"TARGET_{i+1}_HIT"; final_exit_price = tgt
                                    pnl_here = (tgt - entry_price) * current_qty
                                    realized_pnl += pnl_here
                                    logs.append(f"[{c_date_str}] 🎯 Target {i+1} Hit ({tgt}). Full Exit.")
                                    current_qty = 0
                                    break 
                                else:
                                    pnl_here = (tgt - entry_price) * exit_qty
                                    realized_pnl += pnl_here
                                    current_qty -= exit_qty
                                    logs.append(f"[{c_date_str}] 🎯 Target {i+1} Hit ({tgt}). Partial Exit {exit_qty} Qty. Rem: {current_qty}")
                    
                    if current_qty == 0:
                         # [FIX] Force update Status if loop ends
                         final_status = "TARGET_HIT"
                         if not exit_reason: exit_reason = "TARGET_HIT"
                         final_exit_price = ltp
                         break 
            
            # Post-Exit Scan Logic (UPDATED)
            if current_qty == 0:
                skip_scan = (final_status == "SL_HIT" and len(targets_hit_indices) > 0)
                if not skip_scan:
                    remaining_candles = hist_data[idx+1:]
                    virtual_sl_price = float(sl_price)
                    
                    for c in remaining_candles:
                        c_h = float(c['high'])
                        c_l = float(c['low'])
                        c_time = c['date']
                        
                        # 1. CHECK VIRTUAL SL (Stop Tracking if hit)
                        is_dead = False
                        if entry_price > virtual_sl_price: # BUY Trade Logic
                            if c_l <= virtual_sl_price: is_dead = True
                        else: # SELL Trade Logic
                            if c_h >= virtual_sl_price: is_dead = True
                        
                        if is_dead:
                            logs.append(f"[{c_time}] 🔴 Virtual SL Hit during scan. Tracking Stopped.")
                            break # STOP SCANNING

                        # 2. CHECK HIGH MADE
                        if c_h > highest_ltp:
                            highest_ltp = c_h
                            logs.append(f"[{c_time}] ℹ️ Post-Exit High Detected: {highest_ltp}")
                            
                            # Only Notify if T3 was previously hit (Moon Move Rule)
                            if 2 in targets_hit_indices:
                                notification_queue.append({
                                    'event': 'HIGH_MADE', 
                                    'data': {'price': highest_ltp, 'time': c_time}
                                })
                break 

        # 4. Finalize & Save
        with TRADE_LOCK:
            current_ltp = entry_price
            
            # [FIX] Use final_status here instead of relying on loop logic
            if final_status in ["OPEN", "PENDING"]:
                try: 
                    q = kite.quote(f"{exchange}:{symbol}")
                    current_ltp = q[f"{exchange}:{symbol}"]['last_price']
                except: 
                    if hist_data: current_ltp = hist_data[-1]['close']
                
                record = {
                    "id": int(time.time()), 
                    "entry_time": entry_time.strftime("%Y-%m-%d %H:%M:%S"), 
                    "symbol": symbol, "exchange": exchange, "mode": "PAPER", 
                    "order_type": "MARKET", "status": final_status, 
                    "entry_price": entry_price, 
                    "quantity": current_qty if final_status == "OPEN" else qty,
                    "sl": current_sl, "targets": t_list, 
                    "target_controls": target_controls,
                    "lot_size": smart_trader.get_lot_size(symbol), 
                    "trailing_sl": float(trailing_sl), "sl_to_entry": int(sl_to_entry), "exit_multiplier": int(exit_multiplier), 
                    "sl_order_id": None, "targets_hit_indices": targets_hit_indices, 
                    "highest_ltp": highest_ltp, "made_high": highest_ltp, 
                    "current_ltp": current_ltp, "trigger_dir": trigger_dir, "logs": logs,
                    "is_replay": True, "last_update_time": hist_data[-1]['date'] if hist_data else get_time_str(),
                    "target_channels": target_channels # Store channels in DB for future reference
                }
                trades = load_trades(); trades.append(record); save_trades(trades)
                
                return {
                    "status": "success", 
                    "message": f"Simulation Complete. Trade Still Active as {final_status}.",
                    "notification_queue": notification_queue,
                    "trade_ref": record
                }
                
            else:
                last_time = logs[-1].split(']')[0].replace('[', '')
                if "Closed:" not in logs[-1]:
                    logs.append(f"[{last_time}] Closed: {final_status} @ {final_exit_price} | P/L ₹ {realized_pnl:.2f}")

                record = {
                    "id": int(time.time()), 
                    "entry_time": entry_time.strftime("%Y-%m-%d %H:%M:%S"), 
                    "symbol": symbol, "exchange": exchange, "mode": "PAPER", 
                    "order_type": "MARKET", "status": final_status, 
                    "entry_price": entry_price, "quantity": qty,
                    "sl": current_sl, "targets": t_list, 
                    "target_controls": target_controls,
                    "lot_size": smart_trader.get_lot_size(symbol), 
                    "trailing_sl": float(trailing_sl), "sl_to_entry": int(sl_to_entry), "exit_multiplier": int(exit_multiplier), 
                    "sl_order_id": None, "targets_hit_indices": targets_hit_indices, 
                    "highest_ltp": highest_ltp, "made_high": highest_ltp, 
                    "current_ltp": final_exit_price, "trigger_dir": trigger_dir, 
                    "logs": logs, "is_replay": True, "pnl": realized_pnl,
                    "target_channels": target_channels
                }
                move_to_history(record, exit_reason, final_exit_price)
                
                return {
                    "status": "success", 
                    "message": f"Simulation Complete. Closed: {exit_reason} @ {final_exit_price}",
                    "notification_queue": notification_queue,
                    "trade_ref": record
                }

    except Exception as e: 
        return {"status": "error", "message": str(e)}

def simulate_trade_scenario(kite, trade_id, scenario_config):
    """
    Runs a hypothetical simulation on a past trade with modified settings.
    Does NOT affect the database or send notifications.
    """
    try:
        trades = load_history()
        original_trade = next((t for t in trades if str(t['id']) == str(trade_id)), None)
        if not original_trade: return {"status": "error", "message": "Trade not found"}

        symbol = original_trade['symbol']
        exchange = original_trade['exchange']
        entry_time_str = original_trade['entry_time']
        entry_price = original_trade['entry_price']
        qty = original_trade['quantity']
        sl_price = original_trade.get('original_sl', original_trade['sl']) 
        if sl_price == 0: sl_price = entry_price - 20
        sl_points = abs(entry_price - sl_price)

        new_mult = int(scenario_config.get('exit_multiplier', 1))
        targets = [float(x) for x in original_trade['targets']] 
        target_controls = scenario_config.get('target_controls')
        if not target_controls:
             target_controls = [{'enabled': True, 'lots': 0, 'trail_to_entry': False} for _ in range(3)]

        if scenario_config.get('trail_to_entry_t1'): target_controls[0]['trail_to_entry'] = True

        if new_mult > 1:
            valid_targets = [x for x in targets if x > 0]
            if not valid_targets: valid_targets = [entry_price + (sl_points * 2)]
            final_goal = max(valid_targets)
            dist = final_goal - entry_price
            new_targets = []; new_controls = []
            lot_size = smart_trader.get_lot_size(symbol)
            if lot_size == 0: lot_size = 1
            total_lots = qty // lot_size
            base_lots = total_lots // new_mult
            remainder = total_lots % new_mult
            
            for i in range(1, 4): 
                if i <= new_mult:
                    fraction = i / new_mult
                    t_price = entry_price + (dist * fraction)
                    new_targets.append(round(t_price, 2))
                    lots_here = base_lots + (remainder if i == new_mult else 0)
                    trail_pref = False
                    if i == 1 and target_controls: trail_pref = target_controls[0].get('trail_to_entry', False)
                    new_controls.append({'enabled': True, 'lots': int(lots_here), 'trail_to_entry': trail_pref})
                else:
                    new_targets.append(0)
                    new_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
            targets = new_targets; target_controls = new_controls

        try: entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        except: 
            try: entry_dt = datetime.strptime(entry_time_str, "%Y-%m-%dT%H:%M:%S")
            except: return {"status": "error", "message": "Invalid Date Format"}
        try: entry_dt = IST.localize(entry_dt.replace(tzinfo=None))
        except: pass
        
        now = datetime.now(IST)
        token = smart_trader.get_instrument_token(symbol, exchange)
        if not token: return {"status": "error", "message": "Token not found"}

        hist_data = smart_trader.fetch_historical_data(kite, token, entry_dt, now, "minute")
        if not hist_data: return {"status": "error", "message": "No Data"}

        current_qty = qty
        current_sl = sl_price
        sim_pnl = 0.0
        targets_hit = []
        sim_logs = [] 
        sim_logs.append(f"🏁 <b>Simulation Start</b> | Entry: {entry_price} | Qty: {qty} | SL: {current_sl}")
        
        trigger_dir = original_trade.get('trigger_dir')
        status = "PENDING" if trigger_dir else "OPEN"
        
        for candle in hist_data:
            O, H, L, C = candle['open'], candle['high'], candle['low'], candle['close']
            ticks = [O, L, H, C] if C >= O else [O, H, L, C]
            c_time = candle['date'].split(' ')[1][:5]

            for ltp in ticks:
                if status == "CLOSED": break
                if status == "PENDING":
                    activated = False
                    if trigger_dir == "ABOVE" and ltp >= entry_price: activated = True
                    elif trigger_dir == "BELOW" and ltp <= entry_price: activated = True
                    if activated: status = "OPEN"; sim_logs.append(f"[{c_time}] 🚀 <b>Activated</b> at {entry_price}"); continue

                if status == "OPEN":
                    if ltp <= current_sl:
                        pnl_loss = (current_sl - entry_price) * current_qty
                        sim_pnl += pnl_loss
                        sim_logs.append(f"[{c_time}] 🛑 <b>SL Hit</b> @ {current_sl} | Exited {current_qty} Qty | P/L: <span class='text-danger'>{pnl_loss:.2f}</span>")
                        status = "CLOSED"; break
                    for i, tgt in enumerate(targets):
                        if i in targets_hit: continue
                        if ltp >= tgt:
                            targets_hit.append(i)
                            conf = target_controls[i]
                            if conf.get('trail_to_entry') and current_sl < entry_price:
                                current_sl = entry_price
                                sim_logs.append(f"[{c_time}] 🛡️ <b>Trail to Cost</b> Triggered. New SL: {current_sl}")
                            if conf['enabled']:
                                lot_size = smart_trader.get_lot_size(symbol)
                                if lot_size == 0: lot_size = 1
                                exit_qty = conf['lots'] * lot_size
                                if exit_qty >= current_qty or exit_qty >= 1000:
                                    pnl_gain = (tgt - entry_price) * current_qty
                                    sim_pnl += pnl_gain
                                    sim_logs.append(f"[{c_time}] 🎯 <b>Target {i+1} Full Exit</b> @ {tgt} | Qty: {current_qty} | P/L: <span class='text-success'>+{pnl_gain:.2f}</span>")
                                    current_qty = 0; status = "CLOSED"; break
                                else:
                                    pnl_gain = (tgt - entry_price) * exit_qty
                                    sim_pnl += pnl_gain
                                    current_qty -= exit_qty
                                    sim_logs.append(f"[{c_time}] 🎯 <b>Target {i+1} Partial</b> @ {tgt} | Qty: {exit_qty} | P/L: <span class='text-success'>+{pnl_gain:.2f}</span>")
            if status == "CLOSED": break
            
        if current_qty > 0 and status == "OPEN":
            last_price = hist_data[-1]['close']
            pnl_run = (last_price - entry_price) * current_qty
            sim_pnl += pnl_run
            sim_logs.append(f"[End] ⏱️ <b>Market Close/End</b> @ {last_price} | Rem Qty: {current_qty} | P/L: {pnl_run:.2f}")

        sim_logs.append(f"💰 <b>Total Hypothetical P/L: {sim_pnl:.2f}</b>")
        return {"status": "success", "original_pnl": original_trade.get('pnl', 0), "simulated_pnl": round(sim_pnl, 2), "difference": round(sim_pnl - original_trade.get('pnl', 0), 2), "logs": sim_logs}

    except Exception as e: return {"status": "error", "message": str(e)}
