import time
import threading
import datetime
import pytz
import pandas as pd
import pandas_ta as ta
import smart_trader
from managers import trade_manager, persistence, replay_engine
from managers.common import IST

class MomentumStrategyManager:
    def __init__(self, kite):
        self.kite = kite
        self.active = False
        self.thread = None
        self.lock = threading.Lock()
        
        # --- Strategy Constants ---
        self.timeframe = "5minute"
        self.nifty_spot_token = 256265  # Nifty 50 Index
        self.nifty_fut_token = None     # Will fetch dynamically
        
        # --- Config State ---
        self.mode = "PAPER"
        self.lot_size = 0
        self.target_direction = "BOTH"
        
        # Parameters
        self.ema_period = 9
        self.rsi_period = 14
        self.rsi_buy = 55
        self.rsi_sell = 45
        
        # Risk Management
        self.max_daily_loss = 0
        self.trailing_sl_pts = 0
        self.sl_to_entry_mode = 0
        self.legs_config = []
        
        # Session State
        self.session_pnl = 0.0
        self.is_done_for_day = False
        self.stop_reason = ""
        self.current_trade_id = None
        self.trade_active = False

        # --- SIGNAL STATE MACHINE (New for Trigger Logic) ---
        self.signal_stage = "NONE"      # NONE, WAIT_TRIGGER_CE, WAIT_TRIGGER_PE
        self.trigger_spot_price = 0.0   # The High/Low to break
        self.sl_spot_price = 0.0        # The Low/High for SL (Invalidation)
        self.pending_symbol = None      # The Option Symbol to Buy
        self.signal_candle_time = None

    def start(self, mode="PAPER", direction="BOTH", legs_config=None, risk_settings=None):
        if not self.active:
            # 1. Fetch Lot Size
            try:
                fetched_lot = smart_trader.fetch_active_lot_size(self.kite, "NIFTY")
                self.lot_size = fetched_lot if fetched_lot > 0 else 50
            except: self.lot_size = 50

            # 2. Apply Config
            self.mode = mode.upper()
            self.target_direction = direction.upper()
            self.legs_config = legs_config if legs_config else []
            
            if risk_settings:
                self.max_daily_loss = abs(float(risk_settings.get('max_loss', 0)))
                self.trailing_sl_pts = float(risk_settings.get('trail_pts', 0))
                self.sl_to_entry_mode = int(risk_settings.get('sl_entry', 0))

            # 3. Reset Session
            self.is_done_for_day = False
            self.stop_reason = ""
            self.trade_active = False
            self.current_trade_id = None
            
            # Reset Trigger State
            self.signal_stage = "NONE"
            self.trigger_spot_price = 0
            self.pending_symbol = None
            
            # 4. Start Loop
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [MOMENTUM] Started | Mode: {self.mode}")

    def stop(self):
        self.active = False
        print("🛑 [MOMENTUM] Stopped")

    def run_backtest(self, date_str, auto_execute=False):
        """
        Runs the VWAP+EMA+RSI Momentum strategy logic on a past date.
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
            
            # Calculate Expiry (Thursday)
            days_ahead = (3 - target_date.weekday() + 7) % 7 
            expiry_date = target_date + datetime.timedelta(days=days_ahead)
            expiry_str = expiry_date.strftime("%Y-%m-%d")

            # 1. Fetch Data (Need warmup for indicators)
            # Fetch 3 days prior + target date to ensure VWAP/RSI are ready at 9:15
            warmup_date = target_date - datetime.timedelta(days=3)
            
            from_time = datetime.datetime.combine(warmup_date, datetime.time(9, 15))
            to_time = datetime.datetime.combine(target_date, datetime.time(15, 30))
            
            # Use Futures if possible for Volume, else Spot
            token = self._get_nifty_futures_token()
            if not token: token = self.nifty_spot_token
            
            raw_data = self.kite.historical_data(token, from_time, to_time, self.timeframe)
            if not raw_data:
                return {"status": "error", "message": "No historical data found."}
            
            df = pd.DataFrame(raw_data)
            
            # 2. Calculate Indicators
            df['EMA_9'] = ta.ema(df['close'], length=self.ema_period)
            df['RSI'] = ta.rsi(df['close'], length=self.rsi_period)
            
            if 'volume' in df.columns and df['volume'].sum() > 0:
                df.set_index(pd.DatetimeIndex(df['date']), inplace=True)
                df['VWAP'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
                df.reset_index(drop=True, inplace=True)
            else:
                # Fallback if no volume (Spot)
                df['VWAP'] = ta.sma(df['close'], length=20)

            # Filter for Target Date Only
            target_day_str = target_date.strftime('%Y-%m-%d')
            day_df = df[df['date'].astype(str).str.contains(target_day_str)].copy()
            
            if day_df.empty:
                return {"status": "error", "message": f"No candles found for {target_day_str}"}

            trade_found = False
            trade_details = None
            
            # 3. Loop through candles to find SETUP
            rows = day_df.reset_index(drop=True)
            
            for i in range(len(rows) - 1): # -1 because we need a next candle for trigger
                curr = rows.iloc[i]
                
                # Signal Logic
                close = curr['close']
                vwap = curr['VWAP']
                ema = curr['EMA_9']
                rsi = curr['RSI']
                
                signal_side = None
                
                # BUY (CE) Setup
                if close > vwap and close > ema and rsi > self.rsi_buy:
                    signal_side = "CE"
                    trigger_price = float(curr['high'])
                    sl_price = float(curr['low']) # Swing Low (candle low)
                
                # BUY (PE) Setup for Bearish View
                elif close < vwap and close < ema and rsi < self.rsi_sell:
                    signal_side = "PE"
                    trigger_price = float(curr['low'])
                    sl_price = float(curr['high']) # Swing High (candle high)
                    
                if signal_side:
                    # Check Next Candles for Trigger
                    found_trigger = False
                    trigger_time = None
                    
                    # Look ahead up to end of day
                    for j in range(i + 1, len(rows)):
                        future = rows.iloc[j]
                        
                        if signal_side == "CE":
                            if float(future['high']) > trigger_price:
                                found_trigger = True
                                trigger_time = future['date']
                                break
                            if float(future['low']) < sl_price: # SL hit before trigger
                                break 
                        elif signal_side == "PE":
                            if float(future['low']) < trigger_price:
                                found_trigger = True
                                trigger_time = future['date']
                                break
                            if float(future['high']) > sl_price: # SL hit before trigger
                                break
                                
                    if found_trigger:
                        trade_found = True
                        
                        # Get Option Symbol
                        strike_diff = 50
                        spot_ltp = close
                        atm_strike = round(spot_ltp / strike_diff) * strike_diff
                        
                        opt_symbol = smart_trader.get_exact_symbol("NIFTY", expiry_str, atm_strike, signal_side)
                        
                        # Fallback Expiry logic
                        if not opt_symbol:
                             det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                             if det and 'opt_expiries' in det:
                                 valid = sorted([e for e in det['opt_expiries'] if e >= date_str])
                                 if valid:
                                     opt_symbol = smart_trader.get_exact_symbol("NIFTY", valid[0], atm_strike, signal_side)

                        # Simulate Option Price (Approximate for Replay)
                        opt_entry = 100.0 # Default
                        opt_sl = 80.0
                        
                        if opt_symbol:
                            opt_token = smart_trader.get_instrument_token(opt_symbol, "NFO")
                            if opt_token:
                                # Fetch 1 min candle at trigger time to get Entry Price
                                if isinstance(trigger_time, str):
                                    t_dt = datetime.datetime.strptime(trigger_time, "%Y-%m-%d %H:%M:%S%z").replace(tzinfo=None)
                                else:
                                    t_dt = trigger_time.replace(tzinfo=None)
                                    
                                o_start = t_dt
                                o_end = t_dt + datetime.timedelta(minutes=5)
                                o_data = self.kite.historical_data(opt_token, o_start, o_end, "minute")
                                if o_data:
                                    opt_entry = float(o_data[0]['open']) 
                                    spot_risk = abs(trigger_price - sl_price)
                                    opt_risk = spot_risk * 0.5 # Delta approx
                                    opt_sl = opt_entry - opt_risk

                        trade_details = {
                            "symbol": opt_symbol if opt_symbol else "NIFTY-FUT-SIM",
                            "type": signal_side,
                            "time": str(trigger_time),
                            "price": opt_entry,
                            "sl": opt_sl,
                            "spot_trigger": trigger_price
                        }
                        break # Stop after first valid trade
            
            if not trade_found:
                 return {"status": "info", "message": f"No Momentum Setup triggered on {date_str}."}

            # --- AUTO EXECUTE ---
            if auto_execute:
                # Build Controls
                custom_targets = []
                t_controls = []
                
                risk_pts = trade_details['price'] - trade_details['sl']
                if risk_pts <= 5: risk_pts = 20 # Minimum Risk Buffer
                
                for leg in self.legs_config:
                    if not leg.get('active', False):
                        custom_targets.append(0)
                        t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                        continue
                        
                    ratio = leg.get('ratio', 1.0)
                    t_price = round(trade_details['price'] + (risk_pts * ratio), 2)
                    
                    lots = leg.get('lots', 0)
                    is_full = leg.get('full', False)
                    trail = leg.get('trail', False)
                    control_lots = 1000 if is_full else lots
                    
                    custom_targets.append(t_price)
                    t_controls.append({'enabled': True, 'lots': control_lots, 'trail_to_entry': trail})

                while len(custom_targets) < 3:
                    custom_targets.append(0)
                    t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

                final_entry_lots = sum([leg.get('lots', 0) for leg in self.legs_config if leg.get('active', False)])
                total_qty = final_entry_lots * sim_lot_size
                if total_qty <= 0: total_qty = sim_lot_size

                clean_entry_str = trade_details['time'].replace(" ", "T")[:16]

                res = replay_engine.import_past_trade(
                    self.kite,
                    symbol=trade_details['symbol'],
                    entry_dt_str=clean_entry_str, 
                    qty=total_qty,
                    entry_price=trade_details['price'],
                    sl_price=trade_details['sl'],
                    targets=custom_targets,
                    trailing_sl=self.trailing_sl_pts,
                    sl_to_entry=self.sl_to_entry_mode,
                    exit_multiplier=1,
                    target_controls=t_controls,
                    target_channels=['main']
                )

                return {
                    "status": "success", 
                    "message": f"✅ Trade Simulated!\nSym: {trade_details['symbol']}\nTime: {clean_entry_str}\nPrice: {trade_details['price']}"
                }

            return {
                "status": "success",
                "message": f"Setup Found: {trade_details['type']} on {trade_details['symbol']} at {trade_details['time']}. Spot Trigger: {trade_details['spot_trigger']}"
            }

        except Exception as e:
            return {"status": "error", "message": f"Backtest Error: {str(e)}"}

    def _fetch_data(self, token, period=50):
        try:
            to_date = datetime.datetime.now(IST)
            from_date = to_date - datetime.timedelta(days=5)
            data = self.kite.historical_data(token, from_date, to_date, self.timeframe)
            df = pd.DataFrame(data)
            return df.tail(period) if not df.empty else pd.DataFrame()
        except: return pd.DataFrame()

    def _run_loop(self):
        print("✅ [MOMENTUM] Loop Initialized.")
        
        while self.active:
            try:
                # 1. Basic Checks
                if self.is_done_for_day:
                    time.sleep(10)
                    continue
                
                # Check Active Trade
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(5)
                    continue
                
                # 2. State Machine
                # If No Signal, Scan for Setup
                if self.signal_stage == "NONE":
                    self._scan_for_setup()
                
                # If Waiting for Trigger, Monitor LTP
                elif self.signal_stage.startswith("WAIT_TRIGGER"):
                    self._monitor_for_trigger()

                time.sleep(2) # Fast poll for trigger

            except Exception as e:
                print(f"❌ [MOMENTUM] Error: {e}")
                time.sleep(5)

    def _scan_for_setup(self):
        # Fetch Data
        if not self.nifty_fut_token: self.nifty_fut_token = self._get_nifty_futures_token()
        calc_token = self.nifty_fut_token if self.nifty_fut_token else self.nifty_spot_token
        
        df = self._fetch_data(calc_token)
        if df.empty or len(df) < 20: return

        # Indicators
        df['EMA_9'] = ta.ema(df['close'], length=self.ema_period)
        df['RSI'] = ta.rsi(df['close'], length=self.rsi_period)
        
        if 'volume' in df.columns and df['volume'].sum() > 0:
            df.set_index(pd.DatetimeIndex(df['date']), inplace=True)
            df['VWAP'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
        else:
            df['VWAP'] = ta.sma(df['close'], length=20) 

        # Check Last Completed Candle
        curr = df.iloc[-2]
        close = curr['close']
        vwap = curr['VWAP']
        ema = curr['EMA_9']
        rsi = curr['RSI']
        
        signal = None
        
        # BUY Setup
        if close > vwap and close > ema and rsi > self.rsi_buy:
            if self.target_direction in ["BOTH", "CE"]:
                signal = "CE"
                self.trigger_spot_price = float(curr['high'])
                self.sl_spot_price = float(curr['low'])
        
        # SELL Setup (Buy PE)
        elif close < vwap and close < ema and rsi < self.rsi_sell:
            if self.target_direction in ["BOTH", "PE"]:
                signal = "PE"
                self.trigger_spot_price = float(curr['low'])
                self.sl_spot_price = float(curr['high'])
                
        if signal:
            # Prepare Symbol
            strike_gap = 50
            atm_strike = round(close / strike_gap) * strike_gap
            
            # Find Expiry
            det = smart_trader.get_symbol_details(self.kite, "NIFTY")
            if det and 'opt_expiries' in det:
                expiry = det['opt_expiries'][0]
                self.pending_symbol = smart_trader.get_exact_symbol("NIFTY", expiry, atm_strike, signal)
                
                if self.pending_symbol:
                    self.signal_stage = f"WAIT_TRIGGER_{signal}"
                    print(f"🔔 [MOMENTUM] {signal} Setup! Trigger: {self.trigger_spot_price} | SL: {self.sl_spot_price}")
                    time.sleep(1) # Prevent double read

    def _monitor_for_trigger(self):
        # Fetch Spot LTP
        try:
            # Use Futures if we calculated on Futures, else Spot
            ltp = smart_trader.get_ltp(self.kite, "NIFTY 50" if not self.nifty_fut_token else self.nifty_fut_token) 
            if ltp == 0: return # Retry
            
            # Logic for CE (Bullish)
            if "CE" in self.signal_stage:
                # Trigger Condition
                if ltp > self.trigger_spot_price:
                    print(f"⚡ [MOMENTUM] Trigger Hit: {ltp} > {self.trigger_spot_price}")
                    self._execute_trade("CE", ltp)
                    self.signal_stage = "NONE"
                # Invalidation Condition (Hit SL before Trigger)
                elif ltp < self.sl_spot_price:
                    print(f"🚫 [MOMENTUM] Setup Invalidated: {ltp} < {self.sl_spot_price}")
                    self.signal_stage = "NONE"

            # Logic for PE (Bearish)
            elif "PE" in self.signal_stage:
                # Trigger Condition
                if ltp < self.trigger_spot_price:
                    print(f"⚡ [MOMENTUM] Trigger Hit: {ltp} < {self.trigger_spot_price}")
                    self._execute_trade("PE", ltp)
                    self.signal_stage = "NONE"
                # Invalidation Condition
                elif ltp > self.sl_spot_price:
                    print(f"🚫 [MOMENTUM] Setup Invalidated: {ltp} > {self.sl_spot_price}")
                    self.signal_stage = "NONE"
                    
        except Exception as e:
            print(f"Monitor Error: {e}")

    def _execute_trade(self, direction, spot_price):
        if not self.pending_symbol: return
        
        print(f"🚀 Executing {direction} on {self.pending_symbol}")
        
        # Calculate Quantity
        active_legs = [leg for leg in self.legs_config if leg.get('active')]
        if not active_legs: return 
        
        total_lots = sum(l['lots'] for l in active_legs)
        qty = total_lots * self.lot_size
        
        # Get Option LTP for Targets
        opt_ltp = smart_trader.get_ltp(self.kite, self.pending_symbol)
        if opt_ltp == 0: return
        
        # Estimate Risk on Option (Delta 0.5 approx)
        spot_risk = abs(self.trigger_spot_price - self.sl_spot_price)
        opt_risk = spot_risk * 0.5 
        if opt_risk < 5: opt_risk = 10 # Min buffer
        
        # Build Targets
        target_controls = []
        custom_targets = []
        
        for leg in self.legs_config:
             if leg.get('active'):
                 ratio = leg.get('ratio', 1.5)
                 t_price = round(opt_ltp + (opt_risk * ratio), 2)
                 custom_targets.append(t_price)
                 target_controls.append({
                     'enabled': True, 
                     'lots': leg['lots'] if not leg.get('full') else 1000, 
                     'trail_to_entry': leg.get('trail', False)
                 })
             else:
                 custom_targets.append(0)
                 target_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

        # Place Trade (BUY by default)
        res = trade_manager.create_trade_direct(
            self.kite, self.mode, self.pending_symbol, qty, opt_risk, custom_targets, 
            "MARKET", target_controls=target_controls, 
            trailing_sl=self.trailing_sl_pts, sl_to_entry=self.sl_to_entry_mode,
            target_channels=['main']
        )
        
        if res['status'] == 'success':
            self.trade_active = True
            self.current_trade_id = res['trade']['id']

    def _monitor_active_trade(self):
        # Checks if the current trade is closed to resume scanning
        trades = persistence.load_trades()
        t = next((x for x in trades if x['id'] == self.current_trade_id), None)
        if t and t['status'] in ['SL_HIT', 'TARGET_HIT', 'MANUAL_EXIT']:
            self.trade_active = False
            self.current_trade_id = None
            self.signal_stage = "NONE"
            print("ℹ️ [MOMENTUM] Trade Closed. Resuming Scan.")

    def _get_nifty_futures_token(self):
        try:
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            today = datetime.datetime.now(IST).date()
            df = df[pd.to_datetime(df['expiry']).dt.date >= today].sort_values('expiry')
            if not df.empty: return int(df.iloc[0]['instrument_token'])
        except: pass
        return None
