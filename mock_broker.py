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

# Configuration
SIM_CONFIG = {
    "active": False,
    "volatility": 0.05,
    "speed": 1.0,
    "trend": "SIDEWAYS"
}

# --- UPDATED: EXPIRY LOGIC (0DTE Daily) ---
def get_mock_expiry():
    """
    Returns TODAY's date as the expiry. 
    This allows testing Expiry logic (0DTE) every single day.
    """
    return datetime.date.today().strftime("%Y-%m-%d")

CURRENT_EXPIRY = get_mock_expiry()

# --- OPTION PRICING ENGINE ---
def calculate_option_price(spot_price, strike_price, option_type):
    """Calculates a realistic Option Price"""
    intrinsic = 0.0
    if option_type == "CE": intrinsic = max(0.0, spot_price - strike_price)
    else: intrinsic = max(0.0, strike_price - spot_price)
    
    distance = abs(spot_price - strike_price)
    time_value = 150 * (0.995 ** distance) 
    noise = random.uniform(-2, 2)
    
    return round(max(0.05, intrinsic + time_value + noise), 2)

# --- Background Market Simulator ---
def _market_heartbeat():
    print(f"💓 [MOCK MARKET] Simulation Heartbeat Started. Expiry: {CURRENT_EXPIRY}", flush=True)
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
                # Futures Logic
                if "FUT" in sym:
                    idx_key = "NSE:NIFTY 50"
                    if "BANK" in sym: idx_key = "NSE:NIFTY BANK"
                    if idx_key in MOCK_MARKET_DATA:
                        MOCK_MARKET_DATA[sym] = round(MOCK_MARKET_DATA[idx_key] + 10, 2)

                # Options Logic
                if ("NIFTY" in sym or "BANKNIFTY" in sym) and ("CE" in sym or "PE" in sym):
                    try:
                        match = re.search(r'(CE|PE)(\d+(\.\d+)?)', sym)
                        if match:
                            type_ = match.group(1)
                            strike = float(match.group(2))
                            
                            # Determine Spot Price based on Symbol Name
                            spot = MOCK_MARKET_DATA["NSE:NIFTY 50"] # Default
                            if "BANKNIFTY" in sym:
                                spot = MOCK_MARKET_DATA["NSE:NIFTY BANK"]
                                
                            MOCK_MARKET_DATA[sym] = calculate_option_price(spot, strike, type_)
                    except: pass
        time.sleep(SIM_CONFIG["speed"])

t = threading.Thread(target=_market_heartbeat, daemon=True)
t.start()

# --- Mock Kite Class ---
class MockKiteConnect:
    def __init__(self, api_key=None, **kwargs):
        print(f"⚠️ [MOCK BROKER] Initialized. Expiry Set To: {CURRENT_EXPIRY}", flush=True)
        self.mock_instruments = self._generate_instruments()

    def _generate_instruments(self):
        inst_list = []
        # 1. Indices
        inst_list.append({"instrument_token": 256265, "tradingsymbol": "NIFTY 50", "name": "NIFTY", "exchange": "NSE", "last_price": 0, "instrument_type": "EQ", "lot_size": 1, "expiry": None})
        inst_list.append({"instrument_token": 260105, "tradingsymbol": "NIFTY BANK", "name": "BANKNIFTY", "exchange": "NSE", "last_price": 0, "instrument_type": "EQ", "lot_size": 1, "expiry": None})
        inst_list.append({"instrument_token": 738561, "tradingsymbol": "RELIANCE", "name": "RELIANCE", "exchange": "NSE", "last_price": 0, "instrument_type": "EQ", "lot_size": 1, "expiry": None})

        # 2. Futures (NIFTY 65, BANKNIFTY 15)
        inst_list.append({"instrument_token": 888888, "tradingsymbol": f"NIFTY{CURRENT_EXPIRY.replace('-','')}FUT", "name": "NIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "FUT", "lot_size": 65, "expiry": CURRENT_EXPIRY, "strike": 0})
        MOCK_MARKET_DATA[f"NFO:NIFTY{CURRENT_EXPIRY.replace('-','')}FUT"] = 22010.0

        inst_list.append({"instrument_token": 999999, "tradingsymbol": f"BANKNIFTY{CURRENT_EXPIRY.replace('-','')}FUT", "name": "BANKNIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "FUT", "lot_size": 15, "expiry": CURRENT_EXPIRY, "strike": 0})
        MOCK_MARKET_DATA[f"NFO:BANKNIFTY{CURRENT_EXPIRY.replace('-','')}FUT"] = 48010.0

        # 3. NIFTY Options (Base 22000)
        base = 22000
        for strike in range(base - 1000, base + 1000, 50):
            ce_sym = f"NIFTY{CURRENT_EXPIRY.replace('-','')}CE{strike}"
            inst_list.append({"instrument_token": strike, "tradingsymbol": ce_sym, "name": "NIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "CE", "lot_size": 65, "expiry": CURRENT_EXPIRY, "strike": float(strike)})
            
            pe_sym = f"NIFTY{CURRENT_EXPIRY.replace('-','')}PE{strike}"
            inst_list.append({"instrument_token": strike+10000, "tradingsymbol": pe_sym, "name": "NIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "PE", "lot_size": 65, "expiry": CURRENT_EXPIRY, "strike": float(strike)})

            spot = MOCK_MARKET_DATA["NSE:NIFTY 50"]
            if f"NFO:{ce_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{ce_sym}"] = calculate_option_price(spot, strike, "CE")
            if f"NFO:{pe_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{pe_sym}"] = calculate_option_price(spot, strike, "PE")

        # 4. BANKNIFTY Options (Base 48000) - ADDED PER BEST PRACTICE
        bn_base = 48000
        for strike in range(bn_base - 1000, bn_base + 1000, 100):
            ce_sym = f"BANKNIFTY{CURRENT_EXPIRY.replace('-','')}CE{strike}"
            inst_list.append({"instrument_token": strike+50000, "tradingsymbol": ce_sym, "name": "BANKNIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "CE", "lot_size": 15, "expiry": CURRENT_EXPIRY, "strike": float(strike)})
            
            pe_sym = f"BANKNIFTY{CURRENT_EXPIRY.replace('-','')}PE{strike}"
            inst_list.append({"instrument_token": strike+60000, "tradingsymbol": pe_sym, "name": "BANKNIFTY", "exchange": "NFO", "last_price": 0, "instrument_type": "PE", "lot_size": 15, "expiry": CURRENT_EXPIRY, "strike": float(strike)})

            spot = MOCK_MARKET_DATA["NSE:NIFTY BANK"]
            if f"NFO:{ce_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{ce_sym}"] = calculate_option_price(spot, strike, "CE")
            if f"NFO:{pe_sym}" not in MOCK_MARKET_DATA: MOCK_MARKET_DATA[f"NFO:{pe_sym}"] = calculate_option_price(spot, strike, "PE")

        return inst_list

    # Mock Methods
    def login_url(self): return "/mock-login-trigger"
    def generate_session(self, request_token, api_secret): return {"access_token": "mock_token_123", "user_id": "DEMO_USER"}
    def set_access_token(self, access_token): pass
    def instruments(self, exchange=None): return self.mock_instruments

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
        print(f"✅ [MOCK] Order: {kwargs.get('transaction_type')} {kwargs.get('quantity')} {kwargs.get('tradingsymbol')}", flush=True)
        return f"ORD_{random.randint(10000,99999)}"

    def historical_data(self, *args, **kwargs): return []
