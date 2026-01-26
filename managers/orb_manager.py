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
        self.mode = "PAPER"
        
        # Target/Leg Configuration
        # Default: Leg 1 (1 Lot, 1:1, Trail), Leg 2 (1 Lot, 1:2, No Trail)
        self.legs_config = [
            {'lots': 1, 'ratio': 1.0, 'trail': True},
            {'lots': 1, 'ratio': 2.0, 'trail': False},
            {'lots': 0, 'ratio': 3.0, 'trail': False}
        ]
        
        # 1. Global Filters
        self.target_direction = "BOTH" 
        self.cutoff_time = datetime.time(13, 0) 
        
        # 2. Re-entry Logic Controls
        self.reentry_same_sl = False      
        self.reentry_same_filter = "BOTH" 
        self.reentry_opposite = False     
        
        # --- Strategy State ---
        self.range_high = 0
        self.range_low = 0
        self.signal_state = "NONE" 
        self.trigger_level = 0
        self.signal_candle_time = None 
        
        # Trade Management State
        self.trade_active = False
        self.current_trade_id = None
        
        # Execution History
        self.ce_trades = 0
        self.pe_trades = 0
        self.last_trade_side = None 
        self.last_trade_status = None 
        self.is_done_for_day = False
        
        self.nifty_fut_token = None

    def start(self, mode="PAPER", direction="BOTH", cutoff_str="13:00", 
              re_sl=False, re_sl_side="BOTH", re_opp=False, legs_config=None):
        """
        Starts strategy with advanced re-entry parameters and Multi-Leg support.
        legs_config: List of dicts [{'lots': 1, 'ratio': 1.5, 'trail': True}, ...]
        """
        if not self.active:
            # 1. Fetch Dynamic Lot Size
            try:
                det = smart_trader.get_symbol_details(self.kite, "NIFTY")
                fetched_lot = int(det.get('lot_size', 0))
                if fetched_lot > 0: self.lot_size = fetched_lot
            except: pass

            self.mode = mode.upper()
            self.target_direction = direction.upper()
            
            # 2. Parse Legs
            if legs_config and isinstance(legs_config, list):
                self.legs_config = legs_config
            
            # Calculate total lots for display/logging
            total_lots = sum([leg.get('lots', 0) for leg in self.legs_config])
            if total_lots < 1:
                print("⚠️ [ORB] Warning: Total Lots is 0. Defaulting to 1 lot.")
                self.legs_config[0]['lots'] = 1

            try:
                t_parts = cutoff_str.split(':')
                self.cutoff_time = datetime.time(int(t_parts[0]), int(t_parts[1]))
            except:
                self.cutoff_time = datetime.time(13, 0)
                
            # 3. New Re-entry Settings
            self.reentry_same_sl = bool(re_sl)
            self.reentry_same_filter = str(re_sl_side).upper()
            
            # Force Disable Opposite if Direction is NOT Both
            if self.target_direction != "BOTH":
                self.reentry_opposite = False
            else:
                self.reentry_opposite = bool(re_opp)

            # 4. Reset State
            self.is_done_for_day = False
            self.ce_trades = 0
            self.pe_trades = 0
            self.last_trade_side = None
            self.last_trade_status = None
            self.signal_state = "NONE"
            self.trade_active = False

            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [ORB] Started | Mode:{self.mode} | Lots:{total_lots} | Legs:{len(self.legs_config)}")

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
                if self.is_done_for_day:
                    time.sleep(5)
                    continue

                now = datetime.datetime.now(IST)
                curr_time = now.time()

                if curr_time >= self.cutoff_time:
                    if not self.is_done_for_day:
                        print(f"⏰ [ORB] Cutoff ({self.cutoff_time}) Reached. Done for Day.")
                        self.is_done_for_day = True
                        self.signal_state = "NONE"
                    time.sleep(60)
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
            if not self.reentry_same_sl: 
                return False
            if self.last_trade_status != "SL_HIT":
                return False
            if self.reentry_same_filter != "BOTH" and self.reentry_same_filter != side:
                return False
            current_side_count = self.ce_trades if side == "CE" else self.pe_trades
            if current_side_count >= 2:
                return False
            return True
        else:
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
            sl_price = entry_est * 0.90 

        risk_points = entry_est - sl_price
        if risk_points < 5: risk_points = 5 
        
        # --- NEW: Build Targets from Legs Configuration ---
        custom_targets = []
        t_controls = []
        
        total_quantity_lots = 0
        
        # Iterate up to 3 legs
        for leg in self.legs_config:
            lots = leg.get('lots', 0)
            if lots <= 0:
                # Add placeholder if needed to maintain list size of 3, or just skip
                custom_targets.append(0)
                t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})
                continue
                
            total_quantity_lots += lots
            ratio = leg.get('ratio', 1.0)
            trail = leg.get('trail', False)
            
            target_price = entry_est + (risk_points * ratio)
            target_price = round(target_price, 2)
            
            # Convert lot count to actual quantity for trade_manager
            qty_for_leg = lots * self.lot_size
            
            custom_targets.append(target_price)
            t_controls.append({
                'enabled': True, 
                'lots': qty_for_leg, 
                'trail_to_entry': trail
            })

        # Ensure lists are length 3 (Trade Manager Expectation)
        while len(custom_targets) < 3:
            custom_targets.append(0)
            t_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

        full_qty = total_quantity_lots * self.lot_size
        
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
            trailing_sl=0, 
            sl_to_entry=0,
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
            print(f"✅ [ORB] Trade Executed. ID: {self.current_trade_id} | Qty: {full_qty} | Legs: {total_quantity_lots} Lots")
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
