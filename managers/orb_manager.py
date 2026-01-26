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
        self.lot_size = 50 
        self.lots = 2      # Default 2 lots (Multiple of 2)
        self.mode = "PAPER" # Default Mode
        
        # --- New User Controls ---
        self.target_direction = "BOTH" # BOTH, CE, PE
        self.cutoff_time = datetime.time(13, 0) # Default 1:00 PM
        
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

    def start(self, lots=2, mode="PAPER", direction="BOTH", cutoff_str="13:00"):
        """Starts the strategy with specific lot count, mode, direction, and time limit"""
        if not self.active:
            # 1. Fetch Dynamic Lot Size
            try:
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                fetched_lot = int(det.get('lot_size', 0))
                if fetched_lot > 0:
                    self.lot_size = fetched_lot
                    print(f"ℹ️ [ORB] Updated Nifty Lot Size: {self.lot_size}")
                else:
                    print(f"⚠️ [ORB] Could not fetch Lot Size. Using default: {self.lot_size}")
            except Exception as e:
                print(f"⚠️ [ORB] Failed to fetch lot size, using default {self.lot_size}: {e}")

            # 2. Enforce Multiple of 2 Rule
            self.lots = int(lots)
            if self.lots < 2: 
                self.lots = 2
            
            if self.lots % 2 != 0:
                self.lots += 1 # Auto-correct to next even number
                print(f"⚠️ [ORB] Odd lots detected. Adjusted to {self.lots} (Multiple of 2 required)")

            self.mode = mode.upper()
            
            # 3. Set Direction & Cutoff
            self.target_direction = direction.upper()
            try:
                # Parse string "HH:MM" to datetime.time
                t_parts = cutoff_str.split(':')
                self.cutoff_time = datetime.time(int(t_parts[0]), int(t_parts[1]))
            except:
                print(f"⚠️ [ORB] Invalid time format '{cutoff_str}', defaulting to 13:00")
                self.cutoff_time = datetime.time(13, 0)

            total_qty = self.lots * self.lot_size
            
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Strategy Started | Mode: {self.mode} | Dir: {self.target_direction} | Cutoff: {self.cutoff_time} | Qty: {total_qty}")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

    def _get_nifty_futures_token(self):
        """Finds the current month Nifty Futures token for Volume checks."""
        try:
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            today = datetime.datetime.now(IST).date()
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            df = df[df['expiry'] >= today].sort_values('expiry')
            if not df.empty:
                token = int(df.iloc[0]['instrument_token'])
                return token
        except Exception as e:
            print(f"⚠️ [ORB] Error fetching Futures Token: {e}")
        return None

    def _fetch_last_n_candles(self, token, interval, n=5):
        to_date = datetime.datetime.now(IST)
        from_date = to_date - datetime.timedelta(days=4)
        try:
            data = self.kite.historical_data(token, from_date, to_date, interval)
            df = pd.DataFrame(data)
            if not df.empty:
                return df.tail(n)
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

                # --- 1. Universal Time Cutoff (User Defined) ---
                # This applies regardless of profit/loss or trade history
                if curr_time >= self.cutoff_time:
                    if not self.is_done_for_day:
                        print(f"⏰ [ORB] Cutoff Time ({self.cutoff_time}) Reached. Stopping Strategy.")
                        self.is_done_for_day = True
                        self.signal_state = "NONE"
                    
                    # Wait 1 minute to avoid CPU spin, then re-check
                    time.sleep(60)
                    continue

                # --- 2. Phase 2: The Setup (Wait until 09:20) ---
                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                # Capture ORB Range (High/Low of 09:15 candle)
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

                # --- 3. Check Active Trade ---
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1)
                    continue

                # --- 4. Signal Generation ---
                # Note: Direction filtering happens inside _check_signals
                self._check_signals()

                # --- 5. Entry Trigger ---
                if self.signal_state != "NONE":
                    self._check_trigger()

                time.sleep(1) 

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

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

        # Call Signal (ONLY if Direction is BOTH or CE)
        if close_price > self.range_high and self.target_direction in ["BOTH", "CE"]:
            if self.last_trade_side == "CE" and self.sl_hit_count > 0: return 
            if volume_ok:
                if self.signal_state != "WAIT_BUY":
                    print(f"🔔 [ORB] Call Signal (Volume OK). Waiting for break of {candle_high}")
                    self.signal_state = "WAIT_BUY"
                    self.trigger_level = candle_high
                    self.signal_candle_time = candle_time
            else:
                if self.signal_state == "WAIT_SELL":
                    print("⚠️ [ORB] Switch Rule: Sell Setup Invalidated. Resetting.")
                    self.signal_state = "NONE"

        # Put Signal (ONLY if Direction is BOTH or PE)
        elif close_price < self.range_low and self.target_direction in ["BOTH", "PE"]:
            if self.last_trade_side == "PE" and self.sl_hit_count > 0: return
            if volume_ok:
                if self.signal_state != "WAIT_SELL":
                    print(f"🔔 [ORB] Put Signal (Volume OK). Waiting for break of {candle_low}")
                    self.signal_state = "WAIT_SELL"
                    self.trigger_level = candle_low
                    self.signal_candle_time = candle_time
            else:
                if self.signal_state == "WAIT_BUY":
                    print("⚠️ [ORB] Switch Rule: Buy Setup Invalidated. Resetting.")
                    self.signal_state = "NONE"

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
            print(f"⚡ [ORB] Trigger Fired! Type: {trade_type} | Spot LTP: {ltp}")
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
        
        print(f"🎯 [ORB] Plan: Entry~{entry_est} | SL:{sl_price} | T1:{target_1} | T2:{target_2}")

        # --- Quantity & Mode Config ---
        total_qty = self.lots * self.lot_size
        half_qty = int(total_qty / 2)
        
        t_controls = [
            {'enabled': True, 'lots': half_qty, 'trail_to_entry': True}, 
            {'enabled': True, 'lots': 1000, 'trail_to_entry': False},    
            {'enabled': False, 'lots': 0, 'trail_to_entry': False}
        ]
        
        # Use user-selected MODE (LIVE/PAPER/SHADOW)
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
            self.signal_state = "NONE"
            print(f"✅ [ORB] Trade Executed. ID: {self.current_trade_id} | Qty: {total_qty} | Mode: {self.mode}")
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
                if status == 'SL_HIT':
                    self.sl_hit_count += 1
