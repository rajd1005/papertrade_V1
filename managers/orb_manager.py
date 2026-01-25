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
        
        # Strategy State
        self.range_high = 0
        self.range_low = 0
        self.signal_state = "NONE"  # "WAIT_BUY", "WAIT_SELL", "NONE"
        self.trigger_level = 0
        self.signal_candle_sl_spot = 0 # Store Spot SL level initially
        self.signal_candle_time = None # Time of the signal candle to fetch Option SL later
        self.trade_active = False
        self.current_trade_id = None
        
        # Reversal Logic State
        self.sl_hit_count = 0
        self.last_trade_side = None # "CE" or "PE"
        self.is_done_for_day = False

        # Config
        self.timeframe = "5minute"
        self.nifty_spot_token = 256265 # NSE:NIFTY 50
        self.nifty_fut_token = None    # Will be fetched dynamically
        self.quantity = 50             # Default Lot Size (1 Lot) - Configurable
    
    def start(self):
        if not self.active:
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print("🚀 [ORB] Strategy Engine Started")

    def stop(self):
        self.active = False
        print("🛑 [ORB] Strategy Engine Stopped")

    def _get_nifty_futures_token(self):
        """Fetch current month Nifty Futures token for Volume check"""
        try:
            # Simple search or use smart_trader logic
            today = datetime.datetime.now(IST).date()
            # Logic to find current expiry futures (Simplified)
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            df = df[df['expiry'] >= today].sort_values('expiry')
            if not df.empty:
                return df.iloc[0]['instrument_token']
        except Exception as e:
            print(f"⚠️ [ORB] Error fetching Futures Token: {e}")
        return None

    def _fetch_candle_data(self, token, interval_str):
        """Fetch 5-minute candles"""
        to_date = datetime.datetime.now(IST)
        from_date = to_date - datetime.timedelta(days=5) # Buffer
        try:
            data = self.kite.historical_data(token, from_date, to_date, interval_str)
            return pd.DataFrame(data)
        except Exception as e:
            print(f"⚠️ [ORB] History API Error: {e}")
            return pd.DataFrame()

    def _run_loop(self):
        # 1. Initialization
        self.nifty_fut_token = self._get_nifty_futures_token()
        print(f"ℹ️ [ORB] Nifty Futures Token: {self.nifty_fut_token}")

        while self.active:
            try:
                now = datetime.datetime.now(IST)
                curr_time = now.time()

                # --- EOD Force Close (15:15) ---
                if curr_time >= datetime.time(15, 15):
                    if not self.is_done_for_day:
                        print("⏰ [ORB] EOD Reached. Stopping Strategy.")
                        self.is_done_for_day = True
                        # Panic exit handles squaring off active trades
                        # You might call trade_manager.close_trade_manual here if needed
                    time.sleep(60)
                    continue

                # --- Phase 2: The Setup (Wait for 09:20) ---
                if curr_time < datetime.time(9, 20):
                    time.sleep(5)
                    continue
                
                # Capture Range (Runs once)
                if self.range_high == 0:
                    df = self._fetch_candle_data(self.nifty_spot_token, self.timeframe)
                    if not df.empty:
                        # Find 09:15 candle
                        today_str = now.strftime('%Y-%m-%d')
                        orb_candle = df[df['date'].astype(str).str.contains(f"{today_str} 09:15")]
                        
                        if not orb_candle.empty:
                            self.range_high = orb_candle.iloc[0]['high']
                            self.range_low = orb_candle.iloc[0]['low']
                            print(f"✅ [ORB] Range Set: High {self.range_high} | Low {self.range_low}")
                        else:
                            time.sleep(10) # Wait for candle to form
                            continue

                # --- Check Active Trade Status (Phase 5 Management) ---
                if self.trade_active:
                    self._monitor_active_trade()
                    time.sleep(1) # Fast poll when trade is active
                    continue

                # --- Phase 6: Reversal Time Filter ---
                if self.sl_hit_count > 0:
                    if curr_time >= datetime.time(13, 0):
                        if not self.is_done_for_day:
                            print("🛑 [ORB] SL Hit & Time > 1:00 PM. No Reversals.")
                            self.is_done_for_day = True
                        continue

                # --- Phase 3: Signal Generation (Every Candle Close) ---
                # We check the latest completed 5-min candle
                self._check_signals()

                # --- Phase 4: Entry Trigger (Real-Time) ---
                if self.signal_state != "NONE":
                    self._check_trigger()

                time.sleep(1) # 1 Second heartbeat

            except Exception as e:
                print(f"❌ [ORB] Loop Error: {e}")
                time.sleep(5)

    def _check_signals(self):
        """Checks for Breakout + Volume confirmation on completed candles"""
        spot_df = self._fetch_candle_data(self.nifty_spot_token, self.timeframe)
        fut_df = self._fetch_candle_data(self.nifty_fut_token, self.timeframe)
        
        if spot_df.empty or fut_df.empty: return

        # Get last completed candle (ignoring current forming one)
        last_spot = spot_df.iloc[-2] 
        last_fut = fut_df.iloc[-2]
        
        # Volume Check (Avg of last 3 completed candles)
        # Note: iloc[-2] is the signal candle. We need avg of [-2, -3, -4]
        if len(fut_df) < 5: return
        vol_avg = fut_df['volume'].iloc[-4:-1].mean()
        vol_ok = last_fut['volume'] > vol_avg

        # Condition A: Call Signal (Close > Range High)
        if last_spot['close'] > self.range_high:
            # Reversal Constraint: If we took a CE trade and hit SL, ignore this
            if self.last_trade_side == "CE" and self.sl_hit_count > 0:
                return 

            if vol_ok:
                # The "Switch" Rule: Overwrite any pending SELL signal
                if self.signal_state != "WAIT_BUY":
                    print(f"🔔 [ORB] Call Signal Generated at {last_spot['date']}")
                    self.signal_state = "WAIT_BUY"
                    self.trigger_level = last_spot['high']
                    self.signal_candle_sl_spot = last_spot['low'] # Store spot SL for reference
                    self.signal_candle_time = last_spot['date']

        # Condition B: Put Signal (Close < Range Low)
        elif last_spot['close'] < self.range_low:
            # Reversal Constraint: If we took a PE trade and hit SL, ignore this
            if self.last_trade_side == "PE" and self.sl_hit_count > 0:
                return

            if vol_ok:
                # The "Switch" Rule: Overwrite any pending BUY signal
                if self.signal_state != "WAIT_SELL":
                    print(f"🔔 [ORB] Put Signal Generated at {last_spot['date']}")
                    self.signal_state = "WAIT_SELL"
                    self.trigger_level = last_spot['low']
                    self.signal_candle_sl_spot = last_spot['high'] # For Put, SL is the High of the candle
                    self.signal_candle_time = last_spot['date']
        
        # "Switch" Rule Logic for abandoning signals
        # If waiting for BUY, but a candle closes below Range Low -> Switch or Abandon
        if self.signal_state == "WAIT_BUY" and last_spot['close'] < self.range_low:
             print("⚠️ [ORB] Switch Rule: Buy Setup Invalidated. Waiting for new signal.")
             self.signal_state = "NONE"
        
        if self.signal_state == "WAIT_SELL" and last_spot['close'] > self.range_high:
             print("⚠️ [ORB] Switch Rule: Sell Setup Invalidated. Waiting for new signal.")
             self.signal_state = "NONE"

    def _check_trigger(self):
        """Real-time LTP check against Trigger Level"""
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
            print(f"⚡ [ORB] Trigger Fired! Type: {trade_type} @ Spot {ltp}")
            self._execute_entry(ltp, trade_type)

    def _execute_entry(self, spot_ltp, trade_type):
        # 1. Find ATM Strike
        strike_diff = 50
        atm_strike = round(spot_ltp / strike_diff) * strike_diff
        
        # 2. Get Expiry (Current Weekly)
        # Using smart_trader utils to find symbol
        # format: NIFTY 24 1 25 21500 CE
        # We need a robust way to get the exact symbol. 
        # Using smart_trader.search_symbols or chain logic
        
        # Hack: Get chain for NIFTY, pick expiry
        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
        if not details or not details.get('opt_expiries'):
            print("❌ [ORB] Could not fetch Expiry dates")
            return
            
        current_expiry = details['opt_expiries'][0] # Nearest expiry
        
        symbol_name = smart_trader.get_exact_symbol("NIFTY", current_expiry, atm_strike, trade_type)
        if not symbol_name:
            print("❌ [ORB] Could not construct Option Symbol")
            return

        print(f"🚀 [ORB] Entering Trade: {symbol_name}")

        # 3. Calculate Option SL
        # Requirement: "Low of Signal Candle (mapped to Option Chart)"
        # We fetch the option history for the specific signal candle time
        opt_token = smart_trader.get_instrument_token(symbol_name, "NFO")
        
        sl_price = 0
        entry_price_est = smart_trader.get_ltp(self.kite, symbol_name) # Approximate
        
        if opt_token and self.signal_candle_time:
            # Fetch the candle matching the signal time
            opt_hist = self.kite.historical_data(opt_token, self.signal_candle_time, self.signal_candle_time + datetime.timedelta(minutes=5), self.timeframe)
            if opt_hist:
                # If Call, SL is Low. If Put, SL is Low (Charts are inverted logic in Strategy, but price is absolute)
                # "remember Put chart is inverted, so logic applies to Option price low"
                # Standard Logic: For Long Option (Buy CE or Buy PE), SL is the Low of the candle.
                sl_price = opt_hist[0]['low']
                # Safety: If SL is too close or invalid
                if sl_price >= entry_price_est:
                    sl_price = entry_price_est - 20 # Fallback 20 pts
            else:
                sl_price = entry_price_est - 20 # Fallback
        else:
            sl_price = entry_price_est - 20 # Fallback

        # Calculate Targets based on Entry and SL
        risk = entry_price_est - sl_price
        if risk <= 0: risk = 20 # Safety
        
        target_1 = entry_price_est + risk       # 1:1
        target_2 = entry_price_est + (3 * risk) # 1:3
        
        # 4. Create Trade Payload for TradeManager
        # We configure Target 1 to exit 50% and trail, Target 2 to exit all
        # Qty is self.quantity (e.g. 50). 50% is 25.
        
        qty = self.quantity
        half_qty = int(qty / 2)
        rem_qty = qty - half_qty
        
        payload_sl_points = entry_price_est - sl_price
        
        # Call Trade Manager
        # Custom targets = [T1, T2, 0]
        # Controls:
        # T1: Active, Lots=half_qty, Trail_to_entry=True
        # T2: Active, Lots=1000(Full), Trail_to_entry=False
        
        t_controls = [
            {'enabled': True, 'lots': half_qty, 'trail_to_entry': True},
            {'enabled': True, 'lots': 1000, 'trail_to_entry': False}, # 1000 signifies FULL
            {'enabled': False, 'lots': 0, 'trail_to_entry': False}
        ]
        
        res = trade_manager.create_trade_direct(
            self.kite,
            mode="LIVE", # Or "PAPER" based on settings - Hardcoded LIVE per requirement "Trading Instrument"
            specific_symbol=symbol_name,
            quantity=qty,
            sl_points=payload_sl_points,
            custom_targets=[target_1, target_2, 0],
            order_type="MARKET",
            target_controls=t_controls,
            trailing_sl=0, # We rely on Target-based trailing
            sl_to_entry=0,
            exit_multiplier=1,
            target_channels=['main']
        )
        
        if res['status'] == 'success':
            self.trade_active = True
            self.current_trade_id = res['trade']['id']
            self.last_trade_side = trade_type
            self.signal_state = "NONE" # Reset
            print(f"✅ [ORB] Trade Placed ID: {self.current_trade_id}")
        else:
            print(f"❌ [ORB] Trade Placement Failed: {res['message']}")
            self.signal_state = "NONE"

    def _monitor_active_trade(self):
        """Checks if trade hit SL or Targets to update internal state"""
        # We read from persistence (Memory Cache)
        trades = persistence.load_trades()
        # Find our trade
        my_trade = next((t for t in trades if t['id'] == self.current_trade_id), None)
        
        if not my_trade:
            # Maybe moved to history?
            hist = persistence.load_history()
            my_trade = next((t for t in hist if t['id'] == self.current_trade_id), None)
        
        if my_trade:
            status = my_trade.get('status', 'OPEN')
            
            # Check if Closed
            if status in ['SL_HIT', 'TARGET_HIT', 'MANUAL_EXIT', 'TIME_EXIT', 'PANIC_EXIT']:
                print(f"ℹ️ [ORB] Trade {self.current_trade_id} Closed. Status: {status}")
                self.trade_active = False
                self.current_trade_id = None
                
                # Update Counters for Reversal Logic
                if status == 'SL_HIT':
                    self.sl_hit_count += 1
                    print(f"⚠️ [ORB] SL Hit Count: {self.sl_hit_count}")
