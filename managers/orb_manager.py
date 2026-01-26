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
        self.lot_size = 50 
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
        self.signal_state = "NONE" 
        self.trigger_level = 0
        self.signal_candle_time = None 
        
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
            try:
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                fetched_lot = int(det.get('lot_size', 0))
                if fetched_lot > 0: self.lot_size = fetched_lot
            except: pass

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
            self.trade_active = False
            self.session_pnl = 0.0
            self.profit_lock_level = -999999
            self.is_profit_locked = False

            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Started | Mode:{self.mode} | MaxLoss:{self.max_daily_loss}")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

    def run_backtest(self, date_str, auto_execute=False):
        """
        Runs the ORB strategy logic on a past date.
        If auto_execute is True, it imports the trade into the system.
        """
        try:
            # Ensure Lot Size is current (Fix for 0 qty issue if bot wasn't started)
            try:
                ls = smart_trader.get_lot_size("NIFTY")
                if ls > 0: self.lot_size = ls
            except: pass

            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # 1. Fetch Spot Data
            from_time = datetime.datetime.combine(target_date, datetime.time(9, 15))
            to_time = datetime.datetime.combine(target_date, datetime.time(15, 30))
            
            spot_data = self.kite.historical_data(self.nifty_spot_token, from_time, to_time, self.timeframe)
            if not spot_data or len(spot_data) < 5:
                return {"status": "error", "message": "No NIFTY Spot data found for date."}
            
            df = pd.DataFrame(spot_data)
            
            # 2. Identify Range (09:15 candle)
            first_candle = df.iloc[0]
            r_high = float(first_candle['high'])
            r_low = float(first_candle['low'])
            
            # 3. Find Signal
            signal_found = False
            signal_type = None
            signal_candle = None
            
            for i in range(1, len(df)):
                c = df.iloc[i]
                close = c['close']
                c_dt = c['date']
                
                # Normalize Timestamp if string
                if isinstance(c_dt, str): 
                    try: c_dt = datetime.datetime.strptime(c_dt, "%Y-%m-%dT%H:%M:%S%z")
                    except: 
                        try: c_dt = datetime.datetime.strptime(c_dt, "%Y-%m-%d %H:%M:%S")
                        except: pass
                
                if hasattr(c_dt, 'time') and c_dt.time() >= self.cutoff_time: break
                
                if close > r_high:
                    if self.target_direction in ["BOTH", "CE"]:
                        signal_found = True; signal_type = "CE"; signal_candle = c; break
                elif close < r_low:
                    if self.target_direction in ["BOTH", "PE"]:
                        signal_found = True; signal_type = "PE"; signal_candle = c; break
            
            if not signal_found:
                return {"status": "info", "message": f"No ORB Breakout found on {date_str} (Range: {r_high}-{r_low})"}
            
            # 4. Find Expiry (Auto or Tuesday Fallback)
            api_expiry = None
            try:
                if hasattr(smart_trader, 'get_next_weekly_expiry'):
                    api_expiry = smart_trader.get_next_weekly_expiry("NIFTY", target_date)
            except: pass

            if api_expiry:
                expiry_str = api_expiry
                print(f"✅ [ORB] Auto-Fetched Expiry: {expiry_str}")
            else:
                # Fallback: Tuesday Calculation (Weekday 1)
                days_ahead = (1 - target_date.weekday() + 7) % 7 
                expiry_date = target_date + datetime.timedelta(days=days_ahead)
                expiry_str = expiry_date.strftime("%Y-%m-%d")
                print(f"⚠️ [ORB] Using Fallback Expiry (Tuesday): {expiry_str}")
            
            # 5. Build Symbol Details
            close_price = float(signal_candle['close'])
            atm_strike = round(close_price / 50) * 50
            entry_time_str = str(signal_candle['date'])
            if hasattr(signal_candle['date'], 'strftime'):
                entry_time_str = signal_candle['date'].strftime("%Y-%m-%dT%H:%M")

            # 6. Resolve Symbol
            sim_symbol = smart_trader.get_exact_symbol("NIFTY", expiry_str, atm_strike, signal_type)
            
            if not sim_symbol:
                return {
                    "status": "warning",
                    "message": f"✅ Signal Detected (SPOT) but Option Expired/Missing.\nCannot Execute Trade."
                }

            # --- AUTO EXECUTE LOGIC ---
            if auto_execute:
                # A. Fetch Option Data to get Entry Price & SL
                opt_token = smart_trader.get_instrument_token(sim_symbol, "NFO")
                if not opt_token:
                    return {"status": "error", "message": "Active Token not found for Symbol. Cannot Execute."}
                
                # Fetch candle at signal time
                s_time = signal_candle['date']
                
                # FIX: Remove Timezone Info explicitly to avoid "Invalid from date" API error
                if isinstance(s_time, str):
                    try: s_time = datetime.datetime.strptime(s_time, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
                    except: 
                        try: s_time = datetime.datetime.strptime(s_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=None)
                        except: pass
                elif hasattr(s_time, 'replace'):
                    s_time = s_time.replace(tzinfo=None)
                
                # Format strictly as string for Kite API
                from_str = s_time.strftime('%Y-%m-%d %H:%M:%S')
                to_str = (s_time + datetime.timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
                
                opt_data = self.kite.historical_data(opt_token, from_str, to_str, self.timeframe)
                
                if not opt_data:
                    return {"status": "error", "message": "Could not fetch Option Candle for Pricing."}
                
                opt_candle = opt_data[0]
                entry_est = float(opt_candle['close']) 
                sl_price = float(opt_candle['low'])    
                
                risk_points = entry_est - sl_price
                if risk_points < 5: risk_points = 5
                
                # B. Build Target Controls & Custom Targets
                custom_targets = []
                t_controls = []
                total_qty = 0
                
                for leg in self.legs_config:
                    if not leg.get('active', False):
                        custom_targets.append(0)
                        t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                        continue
                        
                    ratio = leg.get('ratio', 1.0)
                    t_price = round(entry_est + (risk_points * ratio), 2)
                    
                    lots = leg.get('lots', 0)
                    is_full = leg.get('full', False)
                    
                    # Ensure lot size is valid
                    if self.lot_size <= 0: self.lot_size = 50
                    qty_leg = 1000 if is_full else (lots * self.lot_size)
                    
                    if not is_full: total_qty += (lots * self.lot_size)
                    
                    custom_targets.append(t_price)
                    t_controls.append({
                        'enabled': True,
                        'lots': qty_leg,
                        'trail_to_entry': leg.get('trail', False)
                    })
                
                # Fill up to 3 targets if needed
                while len(custom_targets) < 3:
                    custom_targets.append(0)
                    t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                
                if total_qty == 0: total_qty = 50 # Default fallback
                
                # C. Execute Import
                res = replay_engine.import_past_trade(
                    self.kite,
                    symbol=sim_symbol,
                    entry_dt_str=entry_time_str, # Passed correct parameter name
                    qty=total_qty,
                    entry_price=entry_est,
                    sl_price=sl_price,
                    targets=custom_targets,
                    trailing_sl=self.trailing_sl_pts,
                    sl_to_entry=self.sl_to_entry_mode,
                    exit_multiplier=1,
                    target_controls=t_controls,
                    target_channels=['main']
                )
                
                return {
                    "status": "success",
                    "message": f"✅ Trade Simulated & Executed!\n\nSymbol: {sim_symbol}\nEntry: {entry_est}\nSL: {sl_price}\nTime: {entry_time_str}\n\nCheck Dashboard."
                }

            # If Auto Execute is False, return suggestion
            return {
                "status": "success",
                "message": f"Signal Found: {signal_type} @ {entry_time_str}",
                "suggestion": {
                    "symbol": sim_symbol,
                    "time": entry_time_str,
                    "type": signal_type
                }
            }

        except Exception as e:
            return {"status": "error", "message": f"Backtest Error: {str(e)}"}

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
                        print(f"⏰ [ORB] Cutoff ({self.cutoff_time}) Reached. Done for Day.")
                        self.is_done_for_day = True
                        self.stop_reason = "CUTOFF_TIME"
                        self.signal_state = "NONE"
                    time.sleep(60)
                    continue
                
                # --- SESSION PNL & RISK CHECKS ---
                if self.max_daily_loss > 0 and self.session_pnl <= -self.max_daily_loss:
                    if not self.is_done_for_day:
                        print(f"🛑 [ORB] Max Daily Loss Hit: {self.session_pnl}")
                        self.is_done_for_day = True
                        self.stop_reason = "MAX_LOSS_HIT"
                        self.signal_state = "NONE"
                    time.sleep(60)
                    continue

                if self.profit_active > 0 and not self.trade_active:
                     if self.session_pnl >= self.profit_active:
                         if not self.is_profit_locked:
                             self.is_profit_locked = True
                             self.profit_lock_level = self.profit_lock_min
                             print(f"🔒 [ORB] Profit Locked Activated. Floor: {self.profit_lock_level}")
                         
                         if self.profit_trail_step > 0:
                             diff = self.session_pnl - self.profit_active
                             steps = int(diff / self.profit_trail_step)
                             new_floor = self.profit_lock_min + (steps * self.profit_trail_step)
                             if new_floor > self.profit_lock_level:
                                 self.profit_lock_level = new_floor
                                 print(f"📈 [ORB] Profit Floor Trailed to: {self.profit_lock_level}")

                     if self.is_profit_locked and self.session_pnl < self.profit_lock_level:
                         print(f"🛑 [ORB] Profit Floor Breached. Stopping.")
                         self.is_done_for_day = True
                         self.stop_reason = "PROFIT_LOCK_BREACH"
                         continue

                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                if self.range_high == 0:
                    df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=20)
                    if not df.empty:
                        today_str = now.strftime('%Y-%m-%d')
                        target_ts = f"{today_str} 09:15:00"
                        orb_row = df[df['date'].astype(str).str.contains(target_ts)]
                        if not orb_row.empty:
                            self.range_high = float(orb_row.iloc[0]['high'])
                            self.range_low = float(orb_row.iloc[0]['low'])
                            print(f"✅ [ORB] Range: {self.range_high} - {self.range_low}")
                        else:
                            time.sleep(5)
                            continue
                    else:
                        time.sleep(5)
                        continue

                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1)
                    continue

                self._check_signals()

                if self.signal_state != "NONE":
                    self._check_trigger()

                time.sleep(1) 

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

    def _can_trade_side(self, side):
        if self.target_direction != "BOTH" and self.target_direction != side:
            return False

        total_trades = self.ce_trades + self.pe_trades

        if total_trades == 0:
            return True

        is_same_side = (side == self.last_trade_side)
        
        if is_same_side:
            if not self.reentry_same_sl: return False
            if self.last_trade_status != "SL_HIT": return False
            if self.reentry_same_filter != "BOTH" and self.reentry_same_filter != side: return False
            current_side_count = self.ce_trades if side == "CE" else self.pe_trades
            if current_side_count >= 2: return False
            return True
        else:
            if self.reentry_opposite: return True
            else: return False

        return False

    def _check_signals(self):
        spot_df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=5)
        fut_df = self._fetch_last_n_candles(self.nifty_fut_token, self.timeframe, n=10)
        
        if spot_df.empty or fut_df.empty or len(fut_df) < 5: return

        sig_candle_spot = spot_df.iloc[-2]
        sig_candle_fut = fut_df.iloc[-2]
        
        vol_avg = fut_df['volume'].iloc[-5:-2].mean()
        curr_vol = sig_candle_fut['volume']
        volume_ok = curr_vol > vol_avg
        
        close_price = sig_candle_spot['close']
        candle_high = sig_candle_spot['high']
        candle_low = sig_candle_spot['low']
        candle_time = sig_candle_spot['date']

        if close_price > self.range_high:
            if self._can_trade_side("CE"):
                if volume_ok:
                    if self.signal_state != "WAIT_BUY":
                        print(f"🔔 [ORB] Call Signal. Waiting for break of {candle_high}")
                        self.signal_state = "WAIT_BUY"
                        self.trigger_level = candle_high
                        self.signal_candle_time = candle_time
                else:
                    if self.signal_state == "WAIT_SELL": self.signal_state = "NONE"

        elif close_price < self.range_low:
            if self._can_trade_side("PE"):
                if volume_ok:
                    if self.signal_state != "WAIT_SELL":
                        print(f"🔔 [ORB] Put Signal. Waiting for break of {candle_low}")
                        self.signal_state = "WAIT_SELL"
                        self.trigger_level = candle_low
                        self.signal_candle_time = candle_time
                else:
                    if self.signal_state == "WAIT_BUY": self.signal_state = "NONE"

    def _check_trigger(self):
        ltp = smart_trader.get_ltp(self.kite, "NSE:NIFTY 50")
        if ltp == 0: return

        triggered = False
        trade_type = ""
        
        if self.signal_state == "WAIT_BUY" and ltp > self.trigger_level:
            triggered = True
            trade_type = "CE"
        elif self.signal_state == "WAIT_SELL" and ltp < self.trigger_level:
            triggered = True
            trade_type = "PE"

        if triggered:
            print(f"⚡ [ORB] Trigger! {trade_type} | LTP: {ltp}")
            self._execute_entry(ltp, trade_type)

    def _execute_entry(self, spot_ltp, trade_type):
        strike_diff = 50
        atm_strike = round(spot_ltp / strike_diff) * strike_diff
        
        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
        if not details or not details.get('opt_expiries'): return
        
        current_expiry = details['opt_expiries'][0] 
        symbol_name = smart_trader.get_exact_symbol("NIFTY", current_expiry, atm_strike, trade_type)
        if not symbol_name: return

        sl_price = 0
        entry_est = smart_trader.get_ltp(self.kite, symbol_name)
        opt_token = smart_trader.get_instrument_token(symbol_name, "NFO")
        
        if opt_token and self.signal_candle_time:
            try:
                ohlc = self.kite.historical_data(opt_token, self.signal_candle_time, self.signal_candle_time + datetime.timedelta(minutes=10), self.timeframe)
                if ohlc: sl_price = float(ohlc[0]['low']) 
            except: pass

        if sl_price == 0 or sl_price >= entry_est:
            sl_price = entry_est * 0.90 

        risk_points = entry_est - sl_price
        if risk_points < 5: risk_points = 5 
        
        # --- NEW: Build Targets from Full Config ---
        custom_targets = []
        t_controls = []
        total_quantity_lots = 0
        
        for leg in self.legs_config:
            # Check Active
            if not leg.get('active', False):
                custom_targets.append(0)
                t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                continue

            lots = leg.get('lots', 0)
            is_full = leg.get('full', False)
            ratio = leg.get('ratio', 1.0)
            trail = leg.get('trail', False)
            
            target_price = entry_est + (risk_points * ratio)
            target_price = round(target_price, 2)
            
            qty_for_leg = 1000 if is_full else (lots * self.lot_size)
            if not is_full: total_quantity_lots += lots
            
            custom_targets.append(target_price)
            t_controls.append({
                'enabled': True, 
                'lots': qty_for_leg, 
                'trail_to_entry': trail
            })

        while len(custom_targets) < 3:
            custom_targets.append(0)
            t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

        # Calculate Total Qty
        final_entry_lots = sum([leg.get('lots', 0) for leg in self.legs_config if leg.get('active', False)])
        full_qty = final_entry_lots * self.lot_size
        
        if full_qty <= 0:
            print("❌ [ORB] Execution Error: Total Quantity is 0.")
            return

        res = trade_manager.create_trade_direct(
            self.kite,
            mode=self.mode, 
            specific_symbol=symbol_name,
            quantity=full_qty,
            sl_points=(entry_est - sl_price), 
            custom_targets=custom_targets,
            order_type="MARKET",
            target_controls=t_controls,
            trailing_sl=self.trailing_sl_pts, 
            sl_to_entry=self.sl_to_entry_mode, 
            exit_multiplier=1,
            target_channels=['main']
        )
        
        if res['status'] == 'success':
            self.trade_active = True
            self.current_trade_id = res['trade']['id']
            self.last_trade_side = trade_type
            
            if trade_type == "CE": self.ce_trades += 1
            else: self.pe_trades += 1
            
            self.signal_state = "NONE"
            print(f"✅ [ORB] Trade Executed. ID: {self.current_trade_id} | Qty: {full_qty}")
        else:
            print(f"❌ [ORB] Trade Failed: {res['message']}")
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
                print(f"💰 [ORB] Session PnL: {self.session_pnl}")
                self.trade_active = False
                self.current_trade_id = None
                self.last_trade_status = status 
                self.signal_state = "NONE"
