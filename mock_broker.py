# mock_broker.py
import datetime
import random
import threading
import time
import re

# --- Global Data ---
MOCK_MARKET_DATA = {
    "NSE:NIFTY 50": 22000.0,
    "NSE:NIFTY BANK": 48000.0,
    "BSE:SENSEX": 72000.0,
    "NSE:RELIANCE": 2400.0,
}

# Map Token -> Symbol for the Ticker
TOKEN_TO_SYMBOL = {}

# Configuration
SIM_CONFIG = {
    "active": True, 
    "volatility": 0.05,
    "speed": 1.0,
    "trend": "SIDEWAYS"
}

# --- UPDATED: EXPIRY LOGIC (0DTE Daily) ---
def get_mock_expiry():
    return datetime.date.today().strftime("%Y-%m-%d")

CURRENT_EXPIRY = get_mock_expiry()

# --- OPTION PRICING ENGINE ---
def calculate_option_price(spot_price, strike_price, option_type):
    intrinsic = 0.0
    if option_type == "CE": intrinsic = max(0.0, spot_price - strike_price)
    else: intrinsic = max(0.0, strike_price - spot_price)
    
    distance = abs(spot_price - strike_price)
    time_value = 150 * (0.995 ** distance) 
    noise = random.uniform(-2, 2)
    return round(max(0.05, intrinsic + time_value + noise), 2)

# --- Background Market Simulator ---
def _market_heartbeat():
    print(f"💓 [MOCK MARKET] Simulation Heartbeat Started.", flush=True)
    while True:
        if SIM_CONFIG["active"]:
            trend = SIM_CONFIG["trend"]
            vol = SIM_CONFIG["volatility"]
            
            # 1. Update INDICES
            indices = ["NSE:NIFTY 50", "NSE:NIFTY BANK", "BSE:SENSEX", "NSE:RELIANCE"]
            for sym in indices:
                curr = MOCK_MARKET_DATA.get(sym, 10000)
                bias = 0
                if trend == "BULLISH": bias = vol * 0.5
                elif trend == "BEARISH": bias = -vol * 0.5
                change = random.uniform(-vol, vol) + bias
                MOCK_MARKET_DATA[sym] = round(curr * (1 + change/100.0), 2)

            # 2. Update FUTURES & OPTIONS
            keys = list(MOCK_MARKET_DATA.keys())
            for sym in keys:
                if "FUT" in sym:
                    idx_key = "NSE:NIFTY 50"
                    if "BANK" in sym: idx_key = "NSE:NIFTY BANK"
                    if idx_key in MOCK_MARKET_DATA:
                        MOCK_MARKET_DATA[sym] = round(MOCK_MARKET_DATA[idx_key] + 10, 2)

                if ("NIFTY" in sym or "BANKNIFTY" in sym) and ("CE" in sym or "PE" in sym):
                    try:
                        match = re.search(r'(CE|PE)(\d+(\.\d+)?)', sym)
                        if match:
                            type_ = match.group(1)
                            strike = float(match.group(2))
                            spot = MOCK_MARKET_DATA["NSE:NIFTY 50"]
                            if "BANKNIFTY" in sym: spot = MOCK_MARKET_DATA["NSE:NIFTY BANK"]
                            MOCK_MARKET_DATA[sym] = calculate_option_price(spot, strike, type_)
                    except: pass
        time.sleep(SIM_CONFIG["speed"])

t = threading.Thread(target=_market_heartbeat, daemon=True)
t.start()

# --- Mock Kite Class ---
class MockKiteConnect:
    def __init__(self, api_key=None, **kwargs):
        print(f"⚠️ [MOCK BROKER] Initialized.", flush=True)
        self.mock_instruments = self._generate_instruments()

    def _generate_instruments(self):
        inst_list = []
        global TOKEN_TO_SYMBOL
        
        def add_inst(token, sym, name, exch, type_, lot, strike=0, expiry=None):
            TOKEN_TO_SYMBOL[token] = f"{exch}:{sym}"
            inst_list.append({
                "instrument_token": token, "tradingsymbol": sym, "name": name, 
                "exchange": exch, "last_price": 0, "instrument_type": type_, 
                "lot_size": lot, "expiry": expiry, "strike": strike
            })

        # 1. Indices
        add_inst(256265, "NIFTY 50", "NIFTY", "NSE", "EQ", 1)
        add_inst(260105, "NIFTY BANK", "BANKNIFTY", "NSE", "EQ", 1)
        add_inst(738561, "RELIANCE", "RELIANCE", "NSE", "EQ", 1)

        # 2. Futures
        add_inst(888888, f"NIFTY{CURRENT_EXPIRY.replace('-','')}FUT", "NIFTY", "NFO", "FUT", 65, 0, CURRENT_EXPIRY)
        MOCK_MARKET_DATA[f"NFO:NIFTY{CURRENT_EXPIRY.replace('-','')}FUT"] = 22010.0
        
        add_inst(999999, f"BANKNIFTY{CURRENT_EXPIRY.replace('-','')}FUT", "BANKNIFTY", "NFO", "FUT", 15, 0, CURRENT_EXPIRY)
        MOCK_MARKET_DATA[f"NFO:BANKNIFTY{CURRENT_EXPIRY.replace('-','')}FUT"] = 48010.0

        # 3. Options
        base = 22000
        for strike in range(base - 1000, base + 1000, 50):
            ce_sym = f"NIFTY{CURRENT_EXPIRY.replace('-','')}CE{strike}"
            pe_sym = f"NIFTY{CURRENT_EXPIRY.replace('-','')}PE{strike}"
            
            add_inst(strike, ce_sym, "NIFTY", "NFO", "CE", 65, float(strike), CURRENT_EXPIRY)
            add_inst(strike+10000, pe_sym, "NIFTY", "NFO", "PE", 65, float(strike), CURRENT_EXPIRY)

            spot = MOCK_MARKET_DATA["NSE:NIFTY 50"]
            if f"NFO:{ce_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{ce_sym}"] = calculate_option_price(spot, strike, "CE")
            if f"NFO:{pe_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{pe_sym}"] = calculate_option_price(spot, strike, "PE")

        return inst_list

    def login_url(self): return "/mock-login-trigger"
    def generate_session(self, request_token, api_secret): return {"access_token": "mock_token_123", "user_id": "DEMO_USER"}
    def set_access_token(self, access_token): pass
    def instruments(self, exchange=None): return self.mock_instruments
    def profile(self): return {"user_id": "DEMO"}

    def quote(self, instruments):
        if isinstance(instruments, str): instruments = [instruments]
        res = {}
        for x in instruments:
            if x not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[x] = 100.0
            p = MOCK_MARKET_DATA[x]
            res[x] = {"last_price": p, "ohlc": {"open": p, "high": p, "low": p, "close": p}}
        return res

    def ltp(self, instruments): return self.quote(instruments)

    def place_order(self, **kwargs): 
        print(f"✅ [MOCK] Order Placed: {kwargs.get('tradingsymbol')}")
        return f"ORD_{random.randint(10000,99999)}"
        
    def modify_order(self, **kwargs):
        print(f"✅ [MOCK] Order Modified: {kwargs.get('order_id')}")
        return True
        
    def cancel_order(self, **kwargs):
        print(f"✅ [MOCK] Order Cancelled: {kwargs.get('order_id')}")
        return True

    def historical_data(self, *args, **kwargs): 
        data = []
        base = 100.0
        now = datetime.datetime.now()
        for i in range(60):
            dt = now - datetime.timedelta(minutes=60-i)
            base += random.uniform(-1, 1)
            data.append({
                'date': dt,
                'open': base, 'high': base+2, 'low': base-2, 'close': base+1,
                'volume': 1000
            })
        return data

# --- NEW: Mock Ticker for WebSocket ---
class MockKiteTicker:
    def __init__(self, api_key, access_token):
        self.api_key = api_key
        self.access_token = access_token
        self.subscribed_tokens = set()
        self.is_connected_flag = False
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self._stop_event = threading.Event()

    def connect(self, threaded=True):
        self.is_connected_flag = True
        print("🔌 [MOCK TICKER] Connected")
        if self.on_connect:
            self.on_connect(self, {})
        
        if threaded:
            t = threading.Thread(target=self._tick_loop, daemon=True)
            t.start()

    def is_connected(self):
        return self.is_connected_flag

    def subscribe(self, tokens):
        self.subscribed_tokens.update(tokens)
        print(f"📡 [MOCK TICKER] Subscribed to {len(tokens)} tokens")

    def set_mode(self, mode, tokens):
        pass 

    def _tick_loop(self):
        while not self._stop_event.is_set():
            if not self.subscribed_tokens:
                time.sleep(1)
                continue
            
            ticks = []
            for token in self.subscribed_tokens:
                sym = TOKEN_TO_SYMBOL.get(token)
                if sym and sym in MOCK_MARKET_DATA:
                    ltp = MOCK_MARKET_DATA[sym]
                    ticks.append({
                        'instrument_token': token,
                        'last_price': ltp,
                        'mode': 'full',
                        'tradable': True
                    })
            
            if ticks and self.on_ticks:
                self.on_ticks(self, ticks)
            
            time.sleep(1)
