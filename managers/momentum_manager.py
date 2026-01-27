import time
import threading
import datetime
import pytz
import pandas as pd
import pandas_ta as ta  # Make sure to install this: pip install pandas_ta
import smart_trader
from managers import trade_manager, persistence
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
            
            # 4. Start Loop
            self.active = True
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()
            print(f"🚀 [MOMENTUM] Started | Mode: {self.mode}")

    def stop(self):
        self.active = False
        print("🛑 [MOMENTUM] Stopped")

    def _fetch_data(self, token, period=50):
        """Fetches last N candles for Indicator Calculation"""
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
                
                # 2. Fetch Data (Nifty Spot)
                df = self._fetch_data(self.nifty_spot_token)
                if len(df) < 20: 
                    time.sleep(5)
                    continue

                # 3. Calculate Indicators
                # VWAP requires High, Low, Close, Volume. 
                # Note: Nifty Spot often has 0 volume. If so, we use Close for VWAP or fetch Futures.
                # For this strategy, using FUTURES data is better for Volume/VWAP.
                
                # Try fetching Futures Token if missing
                if not self.nifty_fut_token:
                    self.nifty_fut_token = self._get_nifty_futures_token()
                
                # Use Futures for Calculation if available, else fallback to Spot
                calc_token = self.nifty_fut_token if self.nifty_fut_token else self.nifty_spot_token
                df_calc = self._fetch_data(calc_token)
                
                if df_calc.empty: continue

                df_calc['EMA_9'] = ta.ema(df_calc['close'], length=self.ema_period)
                df_calc['RSI'] = ta.rsi(df_calc['close'], length=self.rsi_period)
                
                # VWAP Calculation (Pandas TA handles it)
                # If volume is 0 (Spot), VWAP might fail. 
                if 'volume' in df_calc.columns and df_calc['volume'].sum() > 0:
                    df_calc.set_index(pd.DatetimeIndex(df_calc['date']), inplace=True)
                    df_calc['VWAP'] = ta.vwap(df_calc['high'], df_calc['low'], df_calc['close'], df_calc['volume'])
                else:
                    # Fallback if no volume: Use SMA as proxy or skip
                    df_calc['VWAP'] = ta.sma(df_calc['close'], length=20) 

                # 4. Check Signal on COMPLETED candle (Index -2)
                # Index -1 is current forming candle (Repaints), Index -2 is confirmed.
                curr = df_calc.iloc[-2]
                
                close = curr['close']
                vwap = curr['VWAP']
                ema = curr['EMA_9']
                rsi = curr['RSI']
                
                signal = None
                
                # BUY Logic
                if close > vwap and close > ema and rsi > self.rsi_buy:
                    signal = "CE"
                # SELL Logic
                elif close < vwap and close < ema and rsi < self.rsi_sell:
                    signal = "PE"
                    
                if signal:
                    print(f"🔔 [MOMENTUM] Signal Found: {signal} at {curr.name} (RSI: {rsi:.2f})")
                    self._execute_signal(signal, close)
                    # Sleep to prevent duplicate entries on same candle
                    time.sleep(300) 

                time.sleep(5) # Wait before next check

            except Exception as e:
                print(f"❌ [MOMENTUM] Error: {e}")
                time.sleep(5)

    def _execute_signal(self, direction, spot_price):
        if self.target_direction != "BOTH" and self.target_direction != direction:
            return

        # 1. Select Strike (ATM)
        strike_gap = 50
        atm_strike = round(spot_price / strike_gap) * strike_gap
        
        # 2. Get Symbol
        details = smart_trader.get_symbol_details(self.kite, "NIFTY")
        if not details or not details.get('opt_expiries'): return
        expiry = details['opt_expiries'][0]
        
        symbol = smart_trader.get_exact_symbol("NIFTY", expiry, atm_strike, direction)
        if not symbol: return
        
        print(f"⚡ [MOMENTUM] Executing {direction}: {symbol}")
        
        # 3. Calculate Quantity & Targets
        active_legs = [leg for leg in self.legs_config if leg.get('active')]
        if not active_legs: return # No legs configured
        
        total_lots = sum(l['lots'] for l in active_legs)
        qty = total_lots * self.lot_size
        
        # Get LTP for Entry
        ltp = smart_trader.get_ltp(self.kite, symbol)
        if ltp == 0: return
        
        # Calculate Targets based on legs
        # For simplicity, we use the first active leg's ratio for SL/Target or fixed points
        # Strategy defined SL as Swing Low. Here we use a fixed pts or % for automation simplicity
        sl_pts = 20.0 # Default conservative SL for Options
        
        # Build Target Controls
        target_controls = []
        custom_targets = []
        
        for leg in self.legs_config:
             if leg.get('active'):
                 ratio = leg.get('ratio', 1.5)
                 t_price = ltp + (sl_pts * ratio)
                 custom_targets.append(t_price)
                 target_controls.append({
                     'enabled': True, 
                     'lots': leg['lots'] if not leg.get('full') else 1000, 
                     'trail_to_entry': leg.get('trail', False)
                 })
             else:
                 custom_targets.append(0)
                 target_controls.append({'enabled': False, 'lots': 0, 'trail_to_entry': False})

        # 4. Place Trade
        res = trade_manager.create_trade_direct(
            self.kite, self.mode, symbol, qty, sl_pts, custom_targets, 
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
            print("ℹ️ [MOMENTUM] Trade Closed. Resuming Scan.")

    def _get_nifty_futures_token(self):
        # Helper to find current month futures token
        try:
            instruments = self.kite.instruments("NFO")
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == 'NIFTY') & (df['instrument_type'] == 'FUT')]
            today = datetime.datetime.now(IST).date()
            df = df[pd.to_datetime(df['expiry']).dt.date >= today].sort_values('expiry')
            if not df.empty: return int(df.iloc[0]['instrument_token'])
        except: pass
        return None
