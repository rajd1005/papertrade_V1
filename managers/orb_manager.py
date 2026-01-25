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
        
        # --- Dynamic Quantity Management ---
        self.lot_size = 50 # Default, will update on start
        self.lots = 1      # User defined multiplier
        
        # --- Strategy State ---
        self.range_high = 0
        self.range_low = 0
        self.signal_state = "NONE" 
        self.trigger_level = 0
        self.signal_candle_time = None 
        
        # Trade Management State
        self.trade_active = False
        self.current_trade_id = None
        
        # Reversal / Constraints
        self.sl_hit_count = 0
        self.last_trade_side = None # "CE" or "PE"
        self.is_done_for_day = False
        
        # Cached Tokens
        self.nifty_fut_token = None

    def start(self, lots=1):
        """Starts the strategy with specific lot count"""
        if not self.active:
            # 1. Fetch Dynamic Lot Size
            try:
                # Fetch NIFTY details to get current lot size
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                fetched_lot = int(det.get('lot_size', 0))
                if fetched_lot > 0:
                    self.lot_size = fetched_lot
                    print(f"ℹ️ [ORB] Updated Nifty Lot Size: {self.lot_size}")
                else:
                    print(f"⚠️ [ORB] Could not fetch Lot Size. Using default: {self.lot_size}")
            except Exception as e:
                print(f"⚠️ [ORB] Failed to fetch lot size, using default {self.lot_size}: {e}")

            self.lots = int(lots)
            total_qty = self.lots * self.lot_size
            
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Strategy Engine Started | Lots: {self.lots} | Lot Size: {self.lot_size} | Total Qty: {total_qty}")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

    def _get_nifty_futures_token(self):
        """Finds the current month Nifty Futures token for Volume checks."""
        try:
            # We search for "NIFTY" in NFO and filter for FUT
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            
            # Filter for NIFTY Futures
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            
            # Sort by expiry to get the nearest (current month)
            today = datetime.datetime.now(IST).date()
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            df = df[df['expiry'] >= today].sort_values('expiry')
            
            if not df.empty:
                token = int(df.iloc[0]['instrument_token'])
                print(f"ℹ️ [ORB] Found Nifty Future Token: {token} (Expiry: {df.iloc[0]['expiry']})")
                return token
        except Exception as e:
            print(f"⚠️ [ORB] Error fetching Futures Token: {e}")
        return None

    def _fetch_last_n_candles(self, token, interval, n=5):
        """Fetches the last N candles for logic checks."""
        to_date = datetime.datetime.now(IST)
        from_date = to_date - datetime.timedelta(days=4)
        try:
            data = self.kite.historical_data(token, from_date, to_date, interval)
            df = pd.DataFrame(data)
            if not df.empty:
                return df.tail(n) # Return only last N
            return pd.DataFrame()
        except Exception as e:
            return pd.DataFrame()

    def _run_loop(self):
        # 1. Initialize Futures Token
        while self.active and self.nifty_fut_token is None:
            self.nifty_fut_token = self._get_nifty_futures_token()
            if self.nifty_fut_token is None:
                time.sleep(10)
        
        print("✅ [ORB] Loop Initialized. Waiting for Market Data...")

        while self.active:
            try:
                now = datetime.datetime.now(IST)
                curr_time = now.time()

                # --- 1. EOD Force Close (15:15) ---
                if curr_time >= datetime.time(15, 15):
                    if not self.is_done_for_day:
                        print("⏰ [ORB] EOD Reached. Stopping Strategy.")
                        self.is_done_for_day = True
                        self.signal_state = "NONE"
                    time.sleep(60)
                    continue

                # --- 2. Phase 2: The Setup (Wait until 09:20 for First Candle) ---
                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                # Capture ORB Range (High/Low of 09:15 candle)
                if self.range_high == 0:
                    df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=20)
                    if not df.empty:
                        # Find the 09:15 candle specifically
                        today_str = now.strftime('%Y-%m-%d')
                        target_ts = f"{today_str} 09:15:00"
                        
                        # Convert dataframe date to string to match
                        orb_row = df[df['date'].astype(str).str.contains(target_ts)]
                        
                        if not orb_row.empty:
                            self.range_high = float(orb_row.iloc[0]['high'])
                            self.range_low = float(orb_row.iloc[0]['low'])
                            print(f"✅ [ORB] Range Established: {self.range_high} - {self.range_low}")
                        else:
                            # Candle not formed yet
                            time.sleep(5)
                            continue
                    else:
                        time.sleep(5)
                        continue

                # --- 3. Check Active Trade (Phase 5) ---
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1)
                    continue

                # --- 4. Reversal Time Filter (Phase 6) ---
                # Only applies if SL was hit previously.
                if self.sl_hit_count > 0:
                    if curr_time >= datetime.time(13, 0):
                        if not self.is_done_for_day:
                            print("🛑 [ORB] SL Hit & Time > 1:00 PM. No Reversals allowed.")
                            self.is_done_for_day = True
                        continue

                # --- 5. Phase 3: Signal Generation (Every Candle Close) ---
                self._check_signals()

                # --- 6. Phase 4: Entry Trigger (Real-Time) ---
                if self.signal_state != "NONE":
                    self._check_trigger()

                time.sleep(1) 

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

    def _check_signals(self):
        """
        Runs on every candle close (effectively).
        Checks for Breakout + Volume Confirmation.
        Implements 'Switch Rule' and 'Reversal Constraint'.
        """
        # Fetch last few candles for Spot and Futures
        spot_df = self._fetch_last_n_candles(self.nifty_spot_token, self.timeframe, n=5)
        fut_df = self._fetch_last_n_candles(self.nifty_fut_token, self.timeframe, n=10)
        
        if spot_df.empty or fut_df.empty: return
        if len(fut_df) < 5: return

        # The "Signal Candle" is the last COMPLETED candle (iloc[-2])
        sig_candle_spot = spot_df.iloc[-2]
        sig_candle_fut = fut_df.iloc[-2]
        
        # --- Volume Check Logic ---
        # "Is Volume(Futures) > Average(Volume, 3)?"
        vol_avg = fut_df['volume'].iloc[-5:-2].mean()
        curr_vol = sig_candle_fut['volume']
        volume_ok = curr_vol > vol_avg
        
        close_price = sig_candle_spot['close']
        candle_high = sig_candle_spot['high']
        candle_low = sig_candle_spot['low']
        candle_time = sig_candle_spot['date']

        # --- CONDITION A: Potential Call Signal ---
        if close_price > self.range_high:
            # Reversal Constraint: If SL hit on CE previously, ignore CE signals
            if self.last_trade_side == "CE" and self.sl_hit_count > 0:
                return 

            if volume_ok:
                # "Switch" Rule: If we were waiting for SELL, overwrite it
                if self.signal_state != "WAIT_BUY":
                    print(f"🔔 [ORB] Call Signal (Volume OK). Waiting for break of {candle_high}")
                    self.signal_state = "WAIT_BUY"
                    self.trigger_level = candle_high
                    self.signal_candle_time = candle_time
            else:
                if self.signal_state == "WAIT_SELL":
                    print("⚠️ [ORB] Switch Rule: Sell Setup Invalidated (High Broken). Resetting.")
                    self.signal_state = "NONE"

        # --- CONDITION B: Potential Put Signal ---
        elif close_price < self.range_low:
            # Reversal Constraint: If SL hit on PE previously, ignore PE signals
            if self.last_trade_side == "PE" and self.sl_hit_count > 0:
                return

            if volume_ok:
                # "Switch" Rule: If we were waiting for BUY, overwrite it
                if self.signal_state != "WAIT_SELL":
                    print(f"🔔 [ORB] Put Signal (Volume OK). Waiting for break of {candle_low}")
                    self.signal_state = "WAIT_SELL"
                    self.trigger_level = candle_low
                    self.signal_candle_time = candle_time
            else:
                if self.signal_state == "WAIT_BUY":
                    print("⚠️ [ORB] Switch Rule: Buy Setup Invalidated (Low Broken). Resetting.")
                    self.signal_state = "NONE"

    def _check_trigger(self):
        """
        Checks real-time LTP against the Trigger Level.
        """
        # Get Real-Time Spot Price
        ltp = smart_trader.get_ltp(self.kite, "NSE:NIFTY 50")
        if ltp == 0: return

        triggered = False
        trade_type = ""
        
        # Scenario 1: WAIT_BUY
        if self.signal_state == "WAIT_BUY":
            if ltp > self.trigger_level:
                triggered = True
                trade_type = "CE"

        # Scenario 2: WAIT_SELL
        elif self.signal_state == "WAIT_SELL":
            if ltp < self.trigger_level:
                triggered = True
                trade_type = "PE"

        if triggered:
            print(f"⚡ [ORB] Trigger Fired! Type: {trade_type} | Spot LTP: {ltp} | Trigger: {self.trigger_level}")
            self._execute_entry(ltp, trade_type)

    def _execute_entry(self, spot_ltp, trade_type):
        """
        Executes the trade:
        1. Selects ATM Strike
        2. Calculates SL based on OPTION CHART of the Signal Candle
        3. Places Order via TradeManager with Dynamic Quantity
        """
        
        # 1. Identify ATM Strike
        strike_diff = 50
        atm_strike = round(spot_ltp / strike_diff) * strike_diff
        
        # 2. Find Symbol (Current Week)
        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
        if not details or not details.get('opt_expiries'):
            print("❌ [ORB] Expiry Fetch Failed")
            return
        
        current_expiry = details['opt_expiries'][0] # Nearest Expiry
        symbol_name = smart_trader.get_exact_symbol("NIFTY", current_expiry, atm_strike, trade_type)
        
        if not symbol_name:
            print(f"❌ [ORB] Symbol Construction Failed for {atm_strike} {trade_type}")
            return

        # 3. Calculate Option Stop Loss
        # "Set Signal_Candle_SL = Low of this Current Candle (mapped to Option Chart)"
        sl_price = 0
        entry_est = smart_trader.get_ltp(self.kite, symbol_name)
        
        # Fetch Option History for the Signal Candle Time
        opt_token = smart_trader.get_instrument_token(symbol_name, "NFO")
        
        if opt_token and self.signal_candle_time:
            try:
                # We fetch a small window around the signal time
                from_t = self.signal_candle_time
                to_t = from_t + datetime.timedelta(minutes=10)
                ohlc = self.kite.historical_data(opt_token, from_t, to_t, self.timeframe)
                if ohlc:
                    # The first record should correspond to the signal candle time
                    ref_candle = ohlc[0]
                    sl_price = float(ref_candle['low']) 
                    print(f"📉 [ORB] Option SL Found: {sl_price} (Low of candle at {ref_candle['date']})")
                else:
                    print("⚠️ [ORB] Option History Empty. Using default SL.")
            except Exception as e:
                print(f"⚠️ [ORB] SL Fetch Error: {e}")

        # Safety Fallback
        if sl_price == 0 or sl_price >= entry_est:
            sl_price = entry_est * 0.90 # 10% SL fallback
            print(f"⚠️ [ORB] Using Fallback SL: {sl_price}")

        # 4. Calculate Targets
        risk_points = entry_est - sl_price
        if risk_points < 5: risk_points = 5 # Minimum risk buffer
        
        target_1 = entry_est + risk_points       # 1:1
        target_2 = entry_est + (3 * risk_points) # 1:3
        
        print(f"🎯 [ORB] Plan: Entry~{entry_est} | SL:{sl_price} | T1:{target_1} | T2:{target_2}")

        # 5. Quantity & Targets Config
        # Calculate Total Qty based on Lot Multiplier (self.lots) and Current Lot Size (self.lot_size)
        total_qty = self.lots * self.lot_size
        half_qty = int(total_qty / 2)
        
        t_controls = [
            {'enabled': True, 'lots': half_qty, 'trail_to_entry': True}, # Target 1
            {'enabled': True, 'lots': 1000, 'trail_to_entry': False},    # Target 2 (1000 = Remainder)
            {'enabled': False, 'lots': 0, 'trail_to_entry': False}
        ]
        
        res = trade_manager.create_trade_direct(
            self.kite,
            mode="LIVE", # Mandated by requirements
            specific_symbol=symbol_name,
            quantity=total_qty,
            sl_points=(entry_est - sl_price), # Initial SL points
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
            self.signal_state = "NONE" # Reset Signal
            print(f"✅ [ORB] Trade Executed. ID: {self.current_trade_id} | Qty: {total_qty}")
        else:
            print(f"❌ [ORB] Trade Failed: {res['message']}")
            self.signal_state = "NONE"

    def _monitor_active_trade(self):
        """
        Monitors the active trade to update internal counters (SL hits).
        """
        trades = persistence.load_trades()
        trade = next((t for t in trades if t['id'] == self.current_trade_id), None)
        
        # If not in active trades, check history
        if not trade:
            history = persistence.load_history()
            trade = next((t for t in history if t['id'] == self.current_trade_id), None)
            
        if trade:
            status = trade.get('status')
            
            # Check if trade is finished
            if status in ['SL_HIT', 'TARGET_HIT', 'MANUAL_EXIT', 'TIME_EXIT', 'PANIC_EXIT']:
                print(f"ℹ️ [ORB] Trade {self.current_trade_id} Finished with Status: {status}")
                self.trade_active = False
                self.current_trade_id = None
                
                if status == 'SL_HIT':
                    self.sl_hit_count += 1
                    print(f"⚠️ [ORB] SL Hit Count: {self.sl_hit_count}")
