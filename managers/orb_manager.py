import time
import threading
import datetime
import pytz
import pandas as pd
import smart_trader
import settings
from managers import trade_manager, persistence
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
        self.nifty_spot_token = 256265 # NSE:NIFTY 50
        
        # --- Strategy Settings ---
        self.lot_size = 50 
        self.lots = 2      
        self.mode = "PAPER"
        
        # 1. Global Filters
        self.target_direction = "BOTH" # BOTH, CE, PE
        self.cutoff_time = datetime.time(13, 0) 
        
        # 2. Re-entry Logic Controls
        self.reentry_same_sl = False      # Rule 1: Allow re-entry if SL hit on same side
        self.reentry_same_filter = "BOTH" # Filter for Rule 1: BOTH/CE/PE
        self.reentry_opposite = False     # Rule 2: Allow trade on opposite side
        
        # --- Strategy State ---
        self.range_high = 0
        self.range_low = 0
        self.signal_state = "NONE" 
        self.trigger_level = 0
        self.signal_candle_time = None 
        
        # Trade Management State
        self.trade_active = False
        self.current_trade_id = None
        
        # Execution History (For Logic)
        self.ce_trades = 0
        self.pe_trades = 0
        self.last_trade_side = None 
        self.last_trade_status = None # 'SL_HIT', 'TARGET_HIT', etc.
        self.is_done_for_day = False
        
        self.nifty_fut_token = None

    def start(self, lots=2, mode="PAPER", direction="BOTH", cutoff_str="13:00", 
              re_sl=False, re_sl_side="BOTH", re_opp=False):
        """Starts strategy with advanced re-entry parameters"""
        if not self.active:
            # 1. Fetch Dynamic Lot Size
            try:
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                fetched_lot = int(det.get('lot_size', 0))
                if fetched_lot > 0: self.lot_size = fetched_lot
            except: pass

            # 2. Settings
            self.lots = int(lots)
            if self.lots < 2: self.lots = 2
            if self.lots % 2 != 0: self.lots += 1

            self.mode = mode.upper()
            self.target_direction = direction.upper()
            try:
                t_parts = cutoff_str.split(':')
                self.cutoff_time = datetime.time(int(t_parts[0]), int(t_parts[1]))
            except:
                self.cutoff_time = datetime.time(13, 0)
                
            # 3. New Re-entry Settings
            self.reentry_same_sl = bool(re_sl)
            self.reentry_same_filter = str(re_sl_side).upper()
            self.reentry_opposite = bool(re_opp)

            # 4. Reset State
            self.is_done_for_day = False
            self.ce_trades = 0
            self.pe_trades = 0
            self.last_trade_side = None
            self.last_trade_status = None
            self.signal_state = "NONE"
            self.trade_active = False

            total_qty = self.lots * self.lot_size
            
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Started | Mode:{self.mode} | Dir:{self.target_direction} | Re-SL:{self.reentry_same_sl}({self.reentry_same_filter}) | Re-Opp:{self.reentry_opposite}")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

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
                # --- 0. Hard Stop Check ---
                if self.is_done_for_day:
                    time.sleep(5)
                    continue

                now = datetime.datetime.now(IST)
                curr_time = now.time()

                # --- 1. Universal Cutoff Time ---
                if curr_time >= self.cutoff_time:
                    if not self.is_done_for_day:
                        print(f"⏰ [ORB] Cutoff ({self.cutoff_time}) Reached. Done for Day.")
                        self.is_done_for_day = True
                        self.signal_state = "NONE"
                    time.sleep(60)
                    continue

                # --- 2. Wait for 09:20 ---
                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                # --- 3. Establish Range ---
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

                # --- 4. Active Trade Monitor ---
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1)
                    continue

                # --- 5. Signals ---
                self._check_signals()

                # --- 6. Trigger ---
                if self.signal_state != "NONE":
                    self._check_trigger()

                time.sleep(1) 

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

    def _can_trade_side(self, side):
        """
        Master Logic for Permissions.
        side: 'CE' or 'PE'
        """
        # 1. Global Direction Filter
        if self.target_direction != "BOTH" and self.target_direction != side:
            return False

        total_trades = self.ce_trades + self.pe_trades

        # 2. First Trade of the Day? Always ALLOW (if direction matches)
        if total_trades == 0:
            return True

        # 3. Context: We have traded before.
        # Check against Last Trade
        is_same_side = (side == self.last_trade_side)
        
        # --- A. Same Side Re-entry Logic ---
        if is_same_side:
            # Only if Rule 1 Enabled
            if not self.reentry_same_sl: 
                return False
            
            # Only if Last Trade was SL Hit
            if self.last_trade_status != "SL_HIT":
                return False
            
            # Only if Side Filter matches
            if self.reentry_same_filter != "BOTH" and self.reentry_same_filter != side:
                return False

            # Limit: Max 2 trades per side (1 initial + 1 re-entry) to prevent infinite loops
            current_side_count = self.ce_trades if side == "CE" else self.pe_trades
            if current_side_count >= 2:
                return False
                
            return True

        # --- B. Opposite Side Logic ---
        else:
            # Only if Rule 2 Enabled
            if self.reentry_opposite:
                return True
            else:
                return False

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

        # Call Signal
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

        # Put Signal
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

        # SL Calculation
        sl_price = 0
        entry_est = smart_trader.get_ltp(self.kite, symbol_name)
        opt_token = smart_trader.get_instrument_token(symbol_name, "NFO")
        
        if opt_token and self.signal_candle_time:
            try:
                ohlc = self.kite.historical_data(opt_token, self.signal_candle_time, self.signal_candle_time + datetime.timedelta(minutes=10), self.timeframe)
                if ohlc: sl_price = float(ohlc[0]['low']) 
            except: pass

        if sl_price == 0 or sl_price >= entry_est:
            sl_price = entry_est * 0.90 # 10% SL fallback

        risk_points = entry_est - sl_price
        if risk_points < 5: risk_points = 5 
        
        target_1 = entry_est + risk_points       
        target_2 = entry_est + (3 * risk_points) 
        
        total_qty = self.lots * self.lot_size
        half_qty = int(total_qty / 2)
        
        t_controls = [
            {'enabled': True, 'lots': half_qty, 'trail_to_entry': True}, 
            {'enabled': True, 'lots': 1000, 'trail_to_entry': False},    
            {'enabled': False, 'lots': 0, 'trail_to_entry': False}
        ]
        
        res = trade_manager.create_trade_direct(
            self.kite,
            mode=self.mode, 
            specific_symbol=symbol_name,
            quantity=total_qty,
            sl_points=(entry_est - sl_price), 
            custom_targets=[target_1, target_2, 0],
            order_type="MARKET",
            target_controls=t_controls,
            trailing_sl=0, 
            sl_to_entry=0,
            exit_multiplier=1,
            target_channels=['main']
        )
        
        if res['status'] == 'success':
            self.trade_active = True
            self.current_trade_id = res['trade']['id']
            self.last_trade_side = trade_type
            
            # Update Counters
            if trade_type == "CE": self.ce_trades += 1
            else: self.pe_trades += 1
            
            self.signal_state = "NONE"
            print(f"✅ [ORB] Trade Executed. ID: {self.current_trade_id} | Qty: {total_qty}")
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
                self.trade_active = False
                self.current_trade_id = None
                self.last_trade_status = status 
                self.signal_state = "NONE"

                # Check if we are done based on rules
                # We do NOT force is_done_for_day=True here anymore. 
                # Instead, we rely on _can_trade_side() to block future signals if limits are reached.
                # However, if both CE and PE re-entries are exhausted, we can mark done.
                
                # Simple logic: If no more trades are logically possible, stop loop to save resources.
                # (Optional optimization, but let's keep loop running to allow _can_trade_side to decide dynamically)
