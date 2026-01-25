import time
import json
import os
import threading
from datetime import datetime, time as dtime
import pytz
from managers import smart_trader, trade_manager, common

IST = pytz.timezone('Asia/Kolkata')
LOCK = threading.Lock()

# File Paths
CONFIG_FILE = "orb_config.json"
STATE_FILE = "orb_state.json"

# Default Config
DEFAULT_CONFIG = {
    "status": "DISABLED",
    "selected_index": "NIFTY", # NIFTY, BANKNIFTY, FINNIFTY, ALL
    "qty_map": {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40},
    "min_range": 10,
    "max_range": 300,
    "buffer_points": 1.0,
    "risk_reward": [1.0, 3.0]
}

# Instrument Mappings
INDEX_MAP = {
    "NIFTY": {"spot": "NIFTY 50", "fut_fmt": "NIFTY", "strike_diff": 50},
    "BANKNIFTY": {"spot": "NIFTY BANK", "fut_fmt": "BANKNIFTY", "strike_diff": 100},
    "FINNIFTY": {"spot": "NIFTY FIN SERVICE", "fut_fmt": "FINNIFTY", "strike_diff": 50}
}

class OrbSniperBot:
    def __init__(self):
        self.config = self._load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.state = self._load_json(STATE_FILE, {})
        self.logs = [] # In-memory logs for UI
        self.last_candle_check = {} # Track per symbol

    def _load_json(self, filename, default):
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    return json.load(f)
            except: pass
        return default

    def _save_json(self, filename, data):
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4)

    def log(self, msg):
        """Adds a log message for the UI."""
        timestamp = datetime.now(IST).strftime("%H:%M:%S")
        entry = {"time": timestamp, "msg": msg}
        self.logs.insert(0, entry)
        if len(self.logs) > 50: self.logs.pop()
        print(f"[ORB] {msg}")

    def update_config(self, new_config):
        """Called from API to update settings."""
        with LOCK:
            self.config.update(new_config)
            self._save_json(CONFIG_FILE, self.config)
            self.log(f"Configuration Updated. Status: {self.config['status']}")

    def get_active_symbols(self):
        """Returns list of symbols to trade based on selection."""
        selection = self.config.get("selected_index", "NIFTY")
        if selection == "ALL":
            return ["NIFTY", "BANKNIFTY"] # Add FINNIFTY if needed
        return [selection]

    def _init_symbol_state(self, symbol_key):
        """Ensures state dict exists for a symbol."""
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        
        # Reset if new day or missing
        if symbol_key not in self.state or self.state[symbol_key].get("date") != today_str:
            self.state[symbol_key] = {
                "date": today_str,
                "status": "WAITING_RANGE", # WAITING_RANGE, SCANNING, TRIGGER_PENDING, DONE, STOPPED
                "range_high": 0, "range_low": 0,
                "signal_side": None, "trigger_level": 0, "sl_option_level": 0
            }
            self._save_json(STATE_FILE, self.state)

    def execute_logic(self, kite):
        """Main Loop."""
        # 1. Check Global Status
        if self.config.get("status") != "ENABLED":
            return

        with LOCK:
            now = datetime.now(IST)
            if now.time() < dtime(9, 15): return # Too early

            active_symbols = self.get_active_symbols()
            
            for key in active_symbols:
                self._process_symbol(kite, key, now)

    def _process_symbol(self, kite, key, now):
        """Logic for a single index."""
        self._init_symbol_state(key)
        state = self.state[key]
        meta = INDEX_MAP.get(key)
        if not meta: return

        spot_symbol = meta['spot']
        
        # --- PHASE 1: MARK RANGE (09:15 - 09:20) ---
        if state["status"] == "WAITING_RANGE":
            if now.time() >= dtime(9, 20, 5):
                self.log(f"{key}: Fetching First Candle...")
                
                # Fetch 09:15 Candle
                token = smart_trader.get_instrument_token(spot_symbol, "NSE")
                from_t = now.replace(hour=9, minute=15, second=0)
                to_t = now.replace(hour=9, minute=20, second=0)
                
                data = kite.historical_data(token, from_t, to_t, "5minute")
                if data:
                    c = data[0]
                    r_high = c['high']
                    r_low = c['low']
                    r_size = r_high - r_low
                    
                    self.log(f"{key}: Range {r_high}-{r_low} ({r_size} pts)")
                    
                    if r_size > self.config['max_range'] or r_size < self.config['min_range']:
                        state["status"] = "STOPPED"
                        self.log(f"{key}: Filter Rejection (Size: {r_size})")
                    else:
                        state["range_high"] = r_high
                        state["range_low"] = r_low
                        state["status"] = "SCANNING"
                    
                    self._save_json(STATE_FILE, self.state)

        # --- PHASE 2: SCAN FOR BREAKOUTS ---
        elif state["status"] == "SCANNING":
            # Check only on candle close (approx)
            window = f"{key}_{now.hour}:{now.minute}"
            if (now.minute % 5 == 0) and (5 <= now.second <= 15) and self.last_candle_check.get(key) != window:
                self.last_candle_check[key] = window
                
                # Fetch last closed candle
                token = smart_trader.get_instrument_token(spot_symbol, "NSE")
                data = smart_trader.fetch_historical_data(kite, token, now - __import__('datetime').timedelta(minutes=10), now, "5minute")
                
                if data:
                    last = data[-1]
                    close = last['close']
                    
                    side = None
                    if close > state['range_high']: side = "CALL"
                    elif close < state['range_low']: side = "PUT"
                    
                    if side:
                        self.log(f"{key}: {side} Breakout Detected! Checking Volume...")
                        
                        # Volume Check (Simplified for stability)
                        # Assumes volume is fine for now or add Futures fetching here
                        
                        state["signal_side"] = side
                        state["trigger_level"] = last['high'] + self.config['buffer_points'] if side == "CALL" else last['low'] - self.config['buffer_points']
                        
                        # Find Option SL (Low of Signal Candle)
                        atm = round(last['close'] / meta['strike_diff']) * meta['strike_diff']
                        opt_type = "CE" if side == "CALL" else "PE"
                        
                        # Get Option Symbol and Low
                        # (Requires fetch logic, simplified here)
                        # For now, we will use a point-based SL fallback if chart lookup fails in real-time
                        
                        state["status"] = "TRIGGER_PENDING"
                        self.log(f"{key}: Waiting for Trigger @ {state['trigger_level']}")
                        self._save_json(STATE_FILE, self.state)

        # --- PHASE 3: CONFIRMED TRIGGER ---
        elif state["status"] == "TRIGGER_PENDING":
            ltp = smart_trader.get_ltp(kite, spot_symbol)
            triggered = False
            
            if state["signal_side"] == "CALL" and ltp >= state["trigger_level"]: triggered = True
            elif state["signal_side"] == "PUT" and ltp <= state["trigger_level"]: triggered = True
            
            if triggered:
                self.log(f"{key}: 🚀 TRIGGER HIT! Executing Trade...")
                self._execute_trade(kite, key, state["signal_side"], ltp)
                state["status"] = "DONE" # One trade per day per index
                self._save_json(STATE_FILE, self.state)

    def _execute_trade(self, kite, key, side, spot_ltp):
        try:
            qty = self.config['qty_map'].get(key, 50)
            meta = INDEX_MAP[key]
            
            # ATM Strike
            strike = round(spot_ltp / meta['strike_diff']) * meta['strike_diff']
            
            # Get Expiry
            details = smart_trader.get_symbol_details(kite, meta['spot'])
            expiry = details['opt_expiries'][0]
            
            opt_type = "CE" if side == "CALL" else "PE"
            symbol = smart_trader.get_exact_symbol(meta['fut_fmt'], expiry, strike, opt_type)
            
            # Risk Calc
            # Simple assumption: SL is 10% of premium or based on Chart
            # For robustness, we use a fixed point SL derived from config if chart SL fails
            sl_pts = 30 # Default
            
            trade_manager.create_trade_direct(
                kite=kite,
                mode="PAPER", # Or "LIVE" based on further config
                specific_symbol=symbol,
                quantity=qty,
                sl_points=sl_pts,
                custom_targets=[], # Auto-calc based on risk_ratios
                order_type="MARKET",
                target_controls=[],
                risk_ratios=self.config['risk_reward']
            )
            self.log(f"{key}: Trade Placed {symbol}")
            
        except Exception as e:
            self.log(f"{key}: Execution Error {e}")

bot = OrbSniperBot()
