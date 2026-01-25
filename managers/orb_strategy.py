import time
import json
import os
import threading
from datetime import datetime, time as dtime
import pytz

# Correct Imports
import smart_trader
from managers import trade_manager, common

IST = pytz.timezone('Asia/Kolkata')
LOCK = threading.Lock()

CONFIG_FILE = "orb_config.json"
STATE_FILE = "orb_state.json"

DEFAULT_CONFIG = {
    "status": "DISABLED",
    "selected_index": "NIFTY",
    "order_type": "MARKET",         # NEW: User can select LIMIT / MARKET / SL-M
    "qty_map": {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40},
    "min_range": 10,
    "max_range": 300,
    "buffer_points": 1.0,
    "risk_reward": [1.0, 3.0]
}

INDEX_MAP = {
    "NIFTY": {"spot": "NIFTY 50", "fut_fmt": "NIFTY", "strike_diff": 50},
    "BANKNIFTY": {"spot": "NIFTY BANK", "fut_fmt": "BANKNIFTY", "strike_diff": 100},
    "FINNIFTY": {"spot": "NIFTY FIN SERVICE", "fut_fmt": "FINNIFTY", "strike_diff": 50}
}

class OrbSniperBot:
    def __init__(self):
        self.config = self._load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.state = self._load_json(STATE_FILE, {})
        self.logs = []
        self.last_candle_check = {}

    def _load_json(self, filename, default):
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f: return json.load(f)
            except: pass
        return default

    def _save_json(self, filename, data):
        with open(filename, 'w') as f: json.dump(data, f, indent=4)

    def log(self, msg):
        timestamp = datetime.now(IST).strftime("%H:%M:%S")
        self.logs.insert(0, {"time": timestamp, "msg": msg})
        if len(self.logs) > 50: self.logs.pop()
        print(f"[ORB] {msg}")

    def update_config(self, new_config):
        with LOCK:
            self.config.update(new_config)
            self._save_json(CONFIG_FILE, self.config)
            self.log(f"Config Updated. Status: {self.config['status']}")

    def get_active_symbols(self):
        selection = self.config.get("selected_index", "NIFTY")
        if selection == "ALL": return ["NIFTY", "BANKNIFTY"]
        return [selection]

    def _init_symbol_state(self, key):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if key not in self.state or self.state[key].get("date") != today:
            self.state[key] = {
                "date": today, "status": "WAITING_RANGE", 
                "range_high": 0, "range_low": 0, 
                "signal_side": None, "trigger_level": 0
            }
            self._save_json(STATE_FILE, self.state)

    def execute_logic(self, kite):
        if self.config.get("status") != "ENABLED": return

        with LOCK:
            now = datetime.now(IST)
            if now.time() < dtime(9, 15): return

            for key in self.get_active_symbols():
                self._process_symbol(kite, key, now)

    def _process_symbol(self, kite, key, now):
        self._init_symbol_state(key)
        state = self.state[key]
        meta = INDEX_MAP.get(key)
        spot_sym = meta['spot']

        # PHASE 1: MARK RANGE (09:15-09:20)
        if state["status"] == "WAITING_RANGE":
            if now.time() >= dtime(9, 20, 5):
                self.log(f"{key}: Fetching First Candle...")
                token = smart_trader.get_instrument_token(spot_sym, "NSE")
                from_t = now.replace(hour=9, minute=15, second=0)
                to_t = now.replace(hour=9, minute=20, second=0)
                data = kite.historical_data(token, from_t, to_t, "5minute")
                
                if data:
                    c = data[0]
                    r_high = c['high']
                    r_low = c['low']
                    size = r_high - r_low
                    self.log(f"{key}: Range {r_high}-{r_low} ({size}pts)")
                    
                    if size > self.config['max_range'] or size < self.config['min_range']:
                        state["status"] = "STOPPED"
                        self.log(f"{key}: ⛔ Filter Rejection (Size: {size})")
                    else:
                        state["range_high"] = r_high
                        state["range_low"] = r_low
                        state["status"] = "SCANNING"
                    self._save_json(STATE_FILE, self.state)

        # PHASE 2: SCAN
        elif state["status"] == "SCANNING":
            window = f"{key}_{now.hour}:{now.minute}"
            # Check only on candle close
            if (now.minute % 5 == 0) and (5 <= now.second <= 20) and self.last_candle_check.get(key) != window:
                self.last_candle_check[key] = window
                
                token = smart_trader.get_instrument_token(spot_sym, "NSE")
                # Fetch last 2 candles
                data = smart_trader.fetch_historical_data(kite, token, now - __import__('datetime').timedelta(minutes=15), now, "5minute")
                
                if data:
                    last = data[-1]
                    close = last['close']
                    side = None
                    
                    if close > state['range_high']: side = "CALL"
                    elif close < state['range_low']: side = "PUT"
                    
                    if side:
                        # HIGH/LOW TRIGGER LOGIC
                        base = last['high'] if side == "CALL" else last['low']
                        trigger = base + self.config['buffer_points'] if side == "CALL" else base - self.config['buffer_points']
                        
                        state["signal_side"] = side
                        state["trigger_level"] = trigger
                        state["status"] = "TRIGGER_PENDING"
                        
                        self.log(f"{key}: ⚠️ {side} Signal! Waiting for Trigger @ {trigger}")
                        self._save_json(STATE_FILE, self.state)

        # PHASE 3: EXECUTE
        elif state["status"] == "TRIGGER_PENDING":
            ltp = smart_trader.get_ltp(kite, spot_sym)
            triggered = False
            
            if state["signal_side"] == "CALL" and ltp >= state["trigger_level"]: triggered = True
            elif state["signal_side"] == "PUT" and ltp <= state["trigger_level"]: triggered = True
            
            if triggered:
                self.log(f"{key}: 🚀 TRIGGER HIT! Executing...")
                self._execute_trade(kite, key, state["signal_side"], ltp)
                state["status"] = "DONE"
                self._save_json(STATE_FILE, self.state)

    def _execute_trade(self, kite, key, side, spot_ltp):
        try:
            qty = self.config['qty_map'].get(key, 50)
            order_type = self.config.get("order_type", "MARKET") # Uses Configured Order Type
            meta = INDEX_MAP[key]
            
            strike = round(spot_ltp / meta['strike_diff']) * meta['strike_diff']
            details = smart_trader.get_symbol_details(kite, meta['spot'])
            expiry = details['opt_expiries'][0]
            
            opt_type = "CE" if side == "CALL" else "PE"
            symbol = smart_trader.get_exact_symbol(meta['fut_fmt'], expiry, strike, opt_type)
            
            # Use Trade Manager (Same as your manual system)
            # This ensures Telegram Notifications work automatically
            trade_manager.create_trade_direct(
                kite=kite,
                mode="PAPER", # Change to LIVE if needed, or add toggle in UI
                specific_symbol=symbol,
                quantity=qty,
                sl_points=30, # Default SL, user can modify in Trade Tab after entry
                custom_targets=[], 
                order_type=order_type, # Using user selection
                target_controls=[],
                risk_ratios=self.config['risk_reward']
            )
            self.log(f"✅ Trade Placed: {symbol} [{order_type}]")
            
        except Exception as e:
            self.log(f"❌ Execution Error: {e}")

bot = OrbSniperBot()
