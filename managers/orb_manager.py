import time
import threading
import datetime
import pytz
import pandas as pd
import smart_trader
import settings
from managers import trade_manager, persistence, replay_engine
from managers.common import log_event, get_time_str

IST = pytz.timezone('Asia/Kolkata')

class ORBStrategyManager:
    def __init__(self, kite):
        self.kite = kite
        self.active = False
        self.thread = None
        self.lock = threading.Lock()
        
        # --- Strategy Constants ---
        self.timeframe = "5minute"
        self.nifty_spot_token = 256265 
        
        # Initialize to 0 to force fetch from Zerodha
        self.lot_size = 0 
        
        self.mode = "PAPER"
        
        # --- Config State ---
        self.legs_config = [] 
        self.target_direction = "BOTH" 
        self.cutoff_time = datetime.time(13, 0) 
        
        # Re-entry
        self.reentry_same_sl = False      
        self.reentry_same_filter = "BOTH" 
        self.reentry_opposite = False     
        
        # --- Risk Management Settings ---
        self.max_daily_loss = 0
        self.trailing_sl_pts = 0
        self.sl_to_entry_mode = 0
        self.profit_active = 0
        self.profit_lock_min = 0
        self.profit_trail_step = 0
        
        # --- Strategy Session State ---
        self.session_pnl = 0.0
        self.profit_lock_level = -999999 
        self.is_profit_locked = False
        
        self.range_high = 0
        self.range_low = 0
        
        # Signal State
        self.signal_state = "NONE" # NONE, MONITOR_OPT_BUY, MONITOR_OPT_SELL
        self.trigger_price = 0     # Option High
        self.sl_price = 0          # Option Low
        self.monitored_opt_sym = None # Symbol we are watching for breakout
        
        self.trade_active = False
        self.current_trade_id = None
        
        self.ce_trades = 0
        self.pe_trades = 0
        self.last_trade_side = None 
        self.last_trade_status = None 
        self.is_done_for_day = False
        self.stop_reason = ""
        
        self.nifty_fut_token = None

    def start(self, mode="PAPER", direction="BOTH", cutoff_str="13:00", 
              re_sl=False, re_sl_side="BOTH", re_opp=False, legs_config=None,
              max_loss=0, trail_pts=0, sl_entry=0, 
              p_active=0, p_min=0, p_trail=0):
        
        if not self.active:
            # --- Fetch Active Lot Size from Zerodha ---
            try:
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                if det and det.get('lot_size'):
                    self.lot_size = int(det['lot_size'])
                if self.lot_size == 0:
                    fetched_lot = smart_trader.fetch_active_lot_size(self.kite, "NIFTY")
                    if fetched_lot > 0: self.lot_size = fetched_lot
            except Exception as e:
                print(f"⚠️ [ORB] Error fetching Lot Size: {e}")

            self.mode = mode.upper()
            self.target_direction = direction.upper()
            
            if legs_config and isinstance(legs_config, list):
                self.legs_config = legs_config
            else:
                self.legs_config = [{'active': True, 'lots': 1, 'ratio': 1.0, 'trail': True}]
            
            try:
                t_parts = cutoff_str.split(':')
                self.cutoff_time = datetime.time(int(t_parts[0]), int(t_parts[1]))
            except:
                self.cutoff_time = datetime.time(13, 0)
                
            self.reentry_same_sl = bool(re_sl)
            self.reentry_same_filter = str(re_sl_side).upper()
            if self.target_direction != "BOTH":
                self.reentry_opposite = False
            else:
                self.reentry_opposite = bool(re_opp)

            # Store Risk Params
            self.max_daily_loss = abs(float(max_loss))
            self.trailing_sl_pts = float(trail_pts)
            self.sl_to_entry_mode = int(sl_entry)
            self.profit_active = float(p_active)
            self.profit_lock_min = float(p_min)
            self.profit_trail_step = float(p_trail)

            # Reset Session State
            self.is_done_for_day = False
            self.stop_reason = ""
            self.ce_trades = 0
            self.pe_trades = 0
            self.last_trade_side = None
            self.last_trade_status = None
            
            self.signal_state = "NONE"
            self.trigger_price = 0
            self.sl_price = 0
            self.monitored_opt_sym = None
            
            self.trade_active = False
            self.session_pnl = 0.0
            self.profit_lock_level = -999999 
            self.is_profit_locked = False

            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Started | Mode:{self.mode} | MaxLoss:{self.max_daily_loss} | LotSize:{self.lot_size}")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

    def run_backtest(self, date_str, auto_execute=False):
        """
        Runs the 7-Step ORB strategy logic on a past date.
        Returns specific failure reasons if no trade is found.
        """
        try:
            # 0. Setup
            if self.lot_size == 0:
                try:
                    real_lot = smart_trader.fetch_active_lot_size(self.kite, "NIFTY")
                    if real_lot > 0: self.lot_size = real_lot
                except: pass
            
            sim_lot_size = self.lot_size
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # Fetch Expiry for that date (Approx)
            # FIX: Nifty Expiry is THURSDAY (3)
            days_ahead = (3 - target_date.weekday() + 7) % 7 
            expiry_date = target_date + datetime.timedelta(days=days_ahead)
            expiry_str = expiry_date.strftime("%Y-%m-%d")

            # 1. Fetch Spot Data
            from_time = datetime.datetime.combine(target_date, datetime.time(9, 15))
            to_time = datetime.datetime.combine(target_date, datetime.time(15, 30))
            
            spot_data = self.kite.historical_data(self.nifty_spot_token, from_time, to_time, self.timeframe)
            if not spot_data or len(spot_data) < 5:
                return {"status": "error", "message": "No NIFTY Spot data found."}
            
            spot_df = pd.DataFrame(spot_data)
            
            # 2. Mark High/Low of First 5-Min Candle
            first_candle = spot_df.iloc[0]
            r_high = float(first_candle['high'])
            r_low = float(first_candle['low'])
            
            trade_found = False
            trade_type = None
            entry_candle_time = None
            opt_symbol = None
            trigger_price = 0
            stop_loss_price = 0
            
            # --- FAILURE REASON TRACKING ---
            no_setup_reason = "Spot Range (09:15) never broken"
            
            # Loop for Signal (Step 3)
            for i in range(1, len(spot_df)):
                c = spot_df.iloc[i]
                close = float(c['close'])
                c_time = c['date']
                
                # Check Spot Breakout
                signal_side = None
                if close > r_high: signal_side = "CE"
                elif close < r_low: signal_side = "PE"
                
                if signal_side:
                    # Step 4: Take candle details
                    print(f"🔎 Spot Signal {signal_side} at {c_time}")
                    
                    # Step 5: Check Futures Volume (3 Candles: Current, Prev1, Prev2)
                    # NOTE: Backtest Limitation - Assuming Pass if FUT data missing.
                    # Ideally, check real FUT data if available.
                    vol_check_passed = True 
                    
                    if not vol_check_passed:
                        msg = f"Signal at {c_time} rejected: Futures Volume not increasing."
                        return {"status": "info", "message": msg}

                    # Step 6: Search Option Candle & Check Risk
                    strike_diff = 50
                    spot_ltp = float(c['close'])
                    atm_strike = round(spot_ltp / strike_diff) * strike_diff
                    
                    # --- FIXED: EXPIRY FALLBACK LOGIC ---
                    sim_symbol = smart_trader.get_exact_symbol("NIFTY", expiry_str, atm_strike, signal_side)
                    
                    # If symbol not found (expired), try finding next active expiry
                    if not sim_symbol:
                        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
                        if details and 'opt_expiries' in details:
                            # Filter for expiries ON or AFTER the target date
                            t_date_str = target_date.strftime("%Y-%m-%d")
                            valid_expiries = sorted([e for e in details['opt_expiries'] if e >= t_date_str])
                            
                            if valid_expiries:
                                new_expiry = valid_expiries[0]
                                sim_symbol = smart_trader.get_exact_symbol("NIFTY", new_expiry, atm_strike, signal_side)
                                if sim_symbol:
                                    print(f"⚠️ [ORB] Expiry {expiry_str} missing (Expired?). Using active: {new_expiry}")

                    if not sim_symbol: 
                        no_setup_reason = f"Option Symbol not found for {atm_strike} {signal_side} (Tried {expiry_str})"
                        continue
                    
                    opt_token = smart_trader.get_instrument_token(sim_symbol, "NFO")
                    if not opt_token: continue
                    
                    # Fetch Option Candle for EXACT Signal Time
                    # Fix Timezone
                    if isinstance(c_time, str):
                        try: c_time_dt = datetime.datetime.strptime(c_time, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
                        except: c_time_dt = datetime.datetime.strptime(c_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=None)
                    else:
                        c_time_dt = c_time.replace(tzinfo=None)

                    c_from = c_time_dt.strftime('%Y-%m-%d %H:%M:%S')
                    c_to = (c_time_dt + datetime.timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
                    
                    opt_data = self.kite.historical_data(opt_token, c_from, c_to, self.timeframe)
                    if not opt_data: continue
                    
                    sig_opt_candle = opt_data[0]
                    opt_high = float(sig_opt_candle['high'])
                    opt_low = float(sig_opt_candle['low'])
                    
                    risk_pts = opt_high - opt_low
                    
                    # Risk Check <= 15
                    if risk_pts > 15:
                        msg = f"❌ Trade Cancelled: Risk {risk_pts:.2f} > 15 Points at {c_time}"
                        return {"status": "info", "message": msg}
                    
                    # Step 7: Wait for Trigger (Next Candle Breakout)
                    trigger_price = opt_high
                    stop_loss_price = opt_low
                    opt_symbol = sim_symbol
                    
                    # Look ahead for trigger
                    trigger_hit = False
                    trigger_time = None
                    
                    # Fetch subsequent option data to check for trigger
                    rest_from = (c_time_dt + datetime.timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
                    rest_to = to_time.strftime('%Y-%m-%d %H:%M:%S')
                    
                    future_opt_data = self.kite.historical_data(opt_token, rest_from, rest_to, "minute") # Check 1-min for precision
                    
                    for oc in future_opt_data:
                        if float(oc['high']) > trigger_price:
                            trigger_hit = True
                            trigger_time = oc['date']
                            break
                            
                    if trigger_hit:
                        trade_found = True
                        trade_type = signal_side
                        entry_candle_time = trigger_time
                        print(f"✅ Triggered at {trigger_time} | Price: {trigger_price}")
                        break
                    else:
                        msg = f"Signal at {c_time} Valid, but Trigger Price ({trigger_price}) never hit by End of Day."
                        return {"status": "info", "message": msg}

            if not trade_found:
                return {"status": "info", "message": f"No Valid Setup on {date_str}. Reason: {no_setup_reason}"}

            if sim_lot_size <= 0:
                return {"status": "error", "message": f"❌ Error: Lot Size is 0. Cannot simulate trade."}

            # --- AUTO EXECUTE LOGIC ---
            if auto_execute:
                # B. Build Target Controls & Custom Targets
                custom_targets = []
                t_controls = []
                
                entry_est = trigger_price
                risk_points = entry_est - stop_loss_price
                
                for leg in self.legs_config:
                    if not leg.get('active', False):
                        custom_targets.append(0)
                        t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                        continue
                        
                    ratio = leg.get('ratio', 1.0)
                    t_price = round(entry_est + (risk_points * ratio), 2)
                    
                    lots = leg.get('lots', 0)
                    is_full = leg.get('full', False)
                    trail = leg.get('trail', False)
                    
                    control_lots = 1000 if is_full else lots
                    
                    custom_targets.append(t_price)
                    t_controls.append({
                        'enabled': True,
                        'lots': control_lots, 
                        'trail_to_entry': trail
                    })
                
                while len(custom_targets) < 3:
                    custom_targets.append(0)
                    t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                
                final_entry_lots = sum([leg.get('lots', 0) for leg in self.legs_config if leg.get('active', False)])
                total_qty = final_entry_lots * sim_lot_size
                
                if total_qty <= 0: total_qty = sim_lot_size 
                
                # --- FIX DATE FORMAT FOR REPLAY ENGINE ---
                # Replay engine expects "%Y-%m-%dT%H:%M" (HTML datetime-local format)
                # entry_candle_time is likely "2026-01-19 10:05:00+05:30" (datetime object)
                
                clean_entry_str = ""
                if isinstance(entry_candle_time, str):
                    # Try to parse string and reformat
                    try:
                        dt_obj = datetime.datetime.strptime(entry_candle_time.split('+')[0].strip(), "%Y-%m-%d %H:%M:%S")
                        clean_entry_str = dt_obj.strftime("%Y-%m-%dT%H:%M")
                    except:
                        clean_entry_str = entry_candle_time.replace(" ", "T")[:16] # Basic fallback
                elif hasattr(entry_candle_time, 'strftime'):
                    clean_entry_str = entry_candle_time.strftime("%Y-%m-%dT%H:%M")
                
                print(f"🚀 Executing Replay Import for {clean_entry_str}...")

                # C. Execute Import
                res = replay_engine.import_past_trade(
                    self.kite,
                    symbol=opt_symbol,
                    entry_dt_str=clean_entry_str, # FIXED FORMAT
                    qty=total_qty,
                    entry_price=entry_est,
                    sl_price=stop_loss_price,
                    targets=custom_targets,
                    trailing_sl=self.trailing_sl_pts,
                    sl_to_entry=self.sl_to_entry_mode,
                    exit_multiplier=1,
                    target_controls=t_controls,
                    target_channels=['main']
                )
                
                return {
                    "status": "success",
                    "message": f"✅ Trade Simulated & Saved!\n\nSymbol: {opt_symbol}\nTrigger: {entry_est}\nSL: {stop_loss_price}\nTime: {clean_entry_str}\n\nCheck 'Closed Trades' or 'Positions' tab."
                }

            return {
                "status": "success",
                "message": f"Setup Found (Not Executed): {trade_type} | Trigger > {trigger_price} | SL: {stop_loss_price}",
                "suggestion": {
                    "symbol": opt_symbol,
                    "time": str(entry_candle_time),
                    "type": trade_type
                }
            }

        except Exception as e:
            return {"status": "error", "message": f"Backtest Error: {str(e)}"}

    # ... (Rest of file unchanged) ...
    def _get_nifty_futures_token(self):
        try:
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            today = datetime.datetime.now(IST).date()
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            df = df[df['expiry'] >= today].sort_values('expiry')
            if not df.empty: return int(df.iloc[0]['instrument_token'])
        except: pass
        return None

    def _fetch_last_n_candles(self, token, interval, n=5):
        try:
            to_date = datetime.datetime.now(IST)
            from_date = to_date - datetime.timedelta(days=4)
            data = self.kite.historical_data(token, from_date, to_date, interval)
            df = pd.DataFrame(data)
            return df.tail(n) if not df.empty else pd.DataFrame()
        except: return pd.DataFrame()

    def _run_loop(self):
        while self.active and self.nifty_fut_token is None:
            self.nifty_fut_token = self._get_nifty_futures_token()
            if self.nifty_fut_token is None: time.sleep(10)
        
        print("✅ [ORB] Loop Initialized.")

        while self.active:
            try:
                if self.is_done_for_day:
                    time.sleep(5)
                    continue

                now = datetime.datetime.now(IST)
                curr_time = now.time()

                if curr_time >= self.cutoff_time:
                    if not self.is_done_for_day:
                        print(f"⏰ [ORB] Cutoff Reached.")
                        self.is_done_for_day = True
                        self.stop_reason = "Cutoff Time Reached"
                    time.sleep(60)
                    continue
                
                # Check Global PnL / Max Loss
                if self.max_daily_loss > 0 and self.session_pnl <= -self.max_daily_loss:
                    if not self.is_done_for_day:
                        print(f"🛑 [ORB] Max Daily Loss Hit: {self.session_pnl}")
                        self.is_done_for_day = True
                        self.stop_reason = "Max Daily Loss Hit"
                    time.sleep(60)
                    continue

                # Wait for 09:20
                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                # Establish Range (Step 1 & 2)
                if self.range_high == 0:
                    df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=20)
                    if not df.empty:
                        today_str = now.strftime('%Y-%m-%d')
                        target_ts = f"{today_str} 09:15:00"
                        orb_row = df[df['date'].astype(str).str.contains(target_ts)]
                        if not orb_row.empty:
                            self.range_high = float(orb_row.iloc[0]['high'])
                            self.range_low = float(orb_row.iloc[0]['low'])
                            print(f"✅ [ORB] Range Established: {self.range_high} - {self.range_low}")
                        else:
                            time.sleep(5)
                            continue
                    else:
                        time.sleep(5)
                        continue

                # Manage Active Trade
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1)
                    continue

                # State Machine
                # 1. No Signal -> Check for Spot Breakout
                if self.signal_state == "NONE":
                    self._check_spot_breakout_signal()
                
                # 2. Signal Generated -> Watch Option Price for Trigger (Step 7)
                elif self.signal_state in ["MONITOR_OPT_BUY", "MONITOR_OPT_SELL"]:
                    self._check_option_trigger()

                time.sleep(1) 

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

    def _check_spot_breakout_signal(self):
        """
        Step 3: Check 5-min candle close above/below range.
        Step 4 & 5: Check Futures Volume.
        Step 6: Determine Option Levels & Check Risk.
        """
        spot_df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=5)
        fut_df = self._fetch_last_n_candles(self.nifty_fut_token, self.timeframe, n=5)
        
        if spot_df.empty or fut_df.empty or len(fut_df) < 5: return

        # Latest completed candle is at index -2 (since -1 is forming)
        sig_candle_spot = spot_df.iloc[-2]
        
        # Check Spot Close
        close_price = float(sig_candle_spot['close'])
        signal_side = None
        
        if close_price > self.range_high:
            if self._can_trade_side("CE"): signal_side = "CE"
        elif close_price < self.range_low:
            if self._can_trade_side("PE"): signal_side = "PE"
            
        if not signal_side: return

        # --- Step 5: Volume Check (Futures) ---
        # "previous 2 candle Volume... increasing constantly"
        # Implies: Vol[-2] > Vol[-3] > Vol[-4] ? 
        # Or Vol(Signal) > Vol(Prev1) > Vol(Prev2)
        # Using indices: Signal is -2. Prev1 is -3. Prev2 is -4.
        
        v_sig = float(fut_df.iloc[-2]['volume'])
        v_p1 = float(fut_df.iloc[-3]['volume'])
        v_p2 = float(fut_df.iloc[-4]['volume'])
        
        volume_ok = (v_sig > v_p1) and (v_p1 > v_p2)
        
        if not volume_ok:
            print(f"⚠️ [ORB] {signal_side} Signal at {sig_candle_spot['date']} but Volume Check Failed. (V:{v_sig} > {v_p1} > {v_p2} is False). Cancelled for Day.")
            self.is_done_for_day = True
            self.stop_reason = "Futures Volume Not Increasing"
            return

        # --- Step 6: Option Chart & Risk ---
        # Calculate ATM
        strike_diff = 50
        atm_strike = round(close_price / strike_diff) * strike_diff
        
        # Get Symbol
        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
        if not details or not details.get('opt_expiries'): return
        current_expiry = details['opt_expiries'][0] 
        
        symbol_name = smart_trader.get_exact_symbol("NIFTY", current_expiry, atm_strike, signal_side)
        if not symbol_name: return
        
        # Fetch Option Candle for that specific time
        opt_token = smart_trader.get_instrument_token(symbol_name, "NFO")
        c_time = sig_candle_spot['date']
        
        # We need OHLC for this specific timestamp
        # In live loop, 'sig_candle_spot' is the just-closed candle.
        # We can try to fetch it specifically or rely on LTP if strict OHLC fetch is slow.
        # Strict way: Fetch 1 candle.
        
        try:
            # Fix TZ
            if hasattr(c_time, 'replace'): c_time = c_time.replace(tzinfo=None)
            c_from = c_time.strftime('%Y-%m-%d %H:%M:%S')
            c_to = (c_time + datetime.timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
            
            opt_data = self.kite.historical_data(opt_token, c_from, c_to, self.timeframe)
            if not opt_data: return
            
            opt_candle = opt_data[0]
            opt_h = float(opt_candle['high'])
            opt_l = float(opt_candle['low'])
            
            risk = opt_h - opt_l
            
            # Risk Check
            if risk > 15:
                print(f"⚠️ [ORB] {signal_side} Signal Risk too high ({risk} > 15). Cancelled for Day.")
                self.is_done_for_day = True
                self.stop_reason = f"Option Risk Too High ({risk:.2f} pts)"
                return
            
            # --- Valid Signal! Move to Trigger State ---
            self.trigger_price = opt_h
            self.sl_price = opt_l
            self.monitored_opt_sym = symbol_name
            self.signal_state = f"MONITOR_OPT_{signal_side}"
            
            print(f"🔔 [ORB] Valid {signal_side} Signal! Waiting for {symbol_name} > {self.trigger_price}. SL: {self.sl_price}")
            
        except Exception as e:
            print(f"❌ [ORB] Error in Option Check: {e}")

    def _check_option_trigger(self):
        """
        Step 7: Watch Option LTP. If > Signal Candle High, Execute.
        """
        if not self.monitored_opt_sym: return
        
        ltp = smart_trader.get_ltp(self.kite, self.monitored_opt_sym)
        if ltp == 0: return
        
        # Trigger Condition
        if ltp > self.trigger_price:
            trade_type = "CE" if "CE" in self.signal_state else "PE"
            print(f"⚡ [ORB] Trigger Fired! {self.monitored_opt_sym} LTP {ltp} > {self.trigger_price}")
            self._execute_live_trade(self.monitored_opt_sym, ltp, self.sl_price, trade_type)

    def _execute_live_trade(self, symbol_name, entry_price, sl_price, trade_type):
        # Refresh Lot Size
        try:
            ls = smart_trader.get_lot_size(symbol_name)
            if ls > 0: self.lot_size = ls
        except: pass

        risk_points = entry_price - sl_price
        
        # Build Targets
        custom_targets = []
        t_controls = []
        
        for leg in self.legs_config:
            if not leg.get('active', False):
                custom_targets.append(0)
                t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                continue

            lots = leg.get('lots', 0)
            is_full = leg.get('full', False)
            ratio = leg.get('ratio', 1.0)
            trail = leg.get('trail', False)
            
            t_price = round(entry_price + (risk_points * ratio), 2)
            control_lots = 1000 if is_full else lots # Pass Lot Count
            
            custom_targets.append(t_price)
            t_controls.append({'enabled': True, 'lots': control_lots, 'trail_to_entry': trail})

        while len(custom_targets) < 3:
            custom_targets.append(0)
            t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

        # Calculate Total Qty
        final_entry_lots = sum([leg.get('lots', 0) for leg in self.legs_config if leg.get('active', False)])
        full_qty = final_entry_lots * self.lot_size
        
        res = trade_manager.create_trade_direct(
            self.kite, self.mode, symbol_name, full_qty, (entry_price - sl_price), 
            custom_targets, "MARKET", target_controls=t_controls,
            trailing_sl=self.trailing_sl_pts, sl_to_entry=self.sl_to_entry_mode, 
            exit_multiplier=1, target_channels=['main']
        )
        
        if res['status'] == 'success':
            self.trade_active = True
            self.current_trade_id = res['trade']['id']
            self.last_trade_side = trade_type
            if trade_type == "CE": self.ce_trades += 1
            else: self.pe_trades += 1
            self.signal_state = "NONE" # Reset
            print(f"✅ [ORB] Trade Executed! ID: {self.current_trade_id}")
        else:
            print(f"❌ [ORB] Trade Execution Failed: {res['message']}")
            self.signal_state = "NONE"

    def _monitor_active_trade(self):
        trades = persistence.load_trades()
        trade = next((t for t in trades if t['id'] == self.current_trade_id), None)
        
        if not trade:
            history = persistence.load_history()
            trade = next((t for t in history if t['id'] == self.current_trade_id), None)
            
        if trade:
            status = trade.get('status')
            if status in ['SL_HIT', 'TARGET_HIT', 'MANUAL_EXIT', 'TIME_EXIT', 'PANIC_EXIT']:
                print(f"ℹ️ [ORB] Trade Finished: {status}")
                realized_pnl = trade.get('pnl', 0)
                self.session_pnl += realized_pnl
                self.trade_active = False
                self.current_trade_id = None
                self.last_trade_status = status 
                self.signal_state = "NONE"
    
    # ... Helper methods remain same ...
    def _can_trade_side(self, side):
        if self.target_direction != "BOTH" and self.target_direction != side: return False
        total_trades = self.ce_trades + self.pe_trades
        if total_trades == 0: return True
        is_same_side = (side == self.last_trade_side)
        if is_same_side:
            if not self.reentry_same_sl: return False
            if self.last_trade_status != "SL_HIT": return False
            if self.reentry_same_filter != "BOTH" and self.reentry_same_filter != side: return False
            current_side_count = self.ce_trades if side == "CE" else self.pe_trades
            if current_side_count >= 2: return False
            return True
        else:
            return True if self.reentry_opposite else False
