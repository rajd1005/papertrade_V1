import pandas as pd
from datetime import datetime, timedelta
import pytz
import re
from managers import zerodha_ticker

# Global IST Timezone
IST = pytz.timezone('Asia/Kolkata')

instrument_dump = None 
symbol_map = {} # FAST LOOKUP CACHE

def fetch_instruments(kite):
    global instrument_dump, symbol_map
    
    if instrument_dump is not None and not instrument_dump.empty and symbol_map: 
        return

    print("📥 Downloading Instrument List...")
    try:
        instruments = kite.instruments()
        if not instruments:
            print("⚠️ Warning: Kite returned empty instrument list.")
            return

        instrument_dump = pd.DataFrame(instruments)
        
        # Optimize Dates
        if 'expiry' in instrument_dump.columns:
            instrument_dump['expiry_str'] = pd.to_datetime(instrument_dump['expiry'], errors='coerce').dt.strftime('%Y-%m-%d')
            instrument_dump['expiry_date'] = pd.to_datetime(instrument_dump['expiry'], errors='coerce').dt.date
        
        print("⚡ Building Fast Lookup Cache...")
        temp_df = instrument_dump.copy()
        exchange_priority = {'NFO': 0, 'MCX': 1, 'CDS': 2, 'NSE': 3, 'BSE': 4, 'BFO': 5}
        temp_df['priority'] = temp_df['exchange'].map(exchange_priority).fillna(99)
        temp_df.sort_values('priority', inplace=True)
        unique_symbols = temp_df.drop_duplicates(subset=['tradingsymbol'])
        symbol_map = unique_symbols.set_index('tradingsymbol').to_dict('index')
        print(f"✅ Instruments Downloaded & Indexed. Count: {len(instrument_dump)}")
        
    except Exception as e:
        print(f"❌ Failed to fetch instruments: {e}")
        if instrument_dump is None: instrument_dump = pd.DataFrame()
        symbol_map = {}

def get_exchange_name(symbol):
    global symbol_map
    if ":" in symbol: return symbol.split(":")[0]
    if symbol_map and symbol in symbol_map: return symbol_map[symbol]['exchange']
    if "NIFTY" in symbol or "BANKNIFTY" in symbol:
        if any(x in symbol for x in ["FUT", "CE", "PE"]): return "NFO"
    return "NSE"

def get_ltp(kite, symbol):
    try:
        exch = get_exchange_name(symbol)
        token = get_instrument_token(symbol, exch)
        if token and zerodha_ticker.ticker:
            cached_price = zerodha_ticker.ticker.get_ltp(token)
            if cached_price: return cached_price

        if ":" in symbol:
            quote = kite.quote(symbol)
            if quote and symbol in quote: return quote[symbol]['last_price']

        exch = get_exchange_name(symbol)
        full_sym = f"{exch}:{symbol}"
        quote = kite.quote(full_sym)
        if quote and full_sym in quote: return quote[full_sym]['last_price']
        return 0
    except Exception as e:
        print(f"⚠️ Error fetching LTP for {symbol}: {e}")
        return 0

def get_indices_ltp(kite):
    try:
        q = kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK", "BSE:SENSEX"])
        return {
            "NIFTY": q.get("NSE:NIFTY 50", {}).get('last_price', 0),
            "BANKNIFTY": q.get("NSE:NIFTY BANK", {}).get('last_price', 0),
            "SENSEX": q.get("BSE:SENSEX", {}).get('last_price', 0)
        }
    except: return {"NIFTY":0, "BANKNIFTY":0, "SENSEX":0}

def get_zerodha_symbol(common_name):
    if not common_name: return ""
    cleaned = common_name.split("(")[0]
    u = cleaned.upper().strip()
    if u in ["BANKNIFTY", "NIFTY BANK", "BANK NIFTY"]: return "BANKNIFTY"
    if u in ["NIFTY", "NIFTY 50", "NIFTY50"]: return "NIFTY"
    if u == "SENSEX": return "SENSEX"
    if u == "FINNIFTY": return "FINNIFTY"
    return u

def get_lot_size(tradingsymbol):
    global symbol_map
    if not symbol_map: return 1
    data = symbol_map.get(tradingsymbol)
    if data: return int(data.get('lot_size', 1))
    return 1

def get_display_name(tradingsymbol):
    global symbol_map
    if not symbol_map: return tradingsymbol
    try:
        data = symbol_map.get(tradingsymbol)
        if data:
            name = data['name']
            inst_type = data['instrument_type']
            expiry_str = ""
            if 'expiry_date' in data:
                ed = data['expiry_date']
                if pd.notnull(ed):
                    expiry_str = ed.strftime('%d %b').upper() if hasattr(ed, 'strftime') else str(ed)

            if inst_type in ["CE", "PE"]:
                strike = int(data['strike'])
                return f"{name} {strike} {inst_type} {expiry_str}"
            elif inst_type == "FUT": return f"{name} FUT {expiry_str}"
            else: return f"{name} {inst_type}"
        return tradingsymbol
    except: return tradingsymbol

def search_symbols(kite, keyword, allowed_exchanges=None):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: 
        fetch_instruments(kite)
        if instrument_dump is None or instrument_dump.empty: return []

    k = keyword.upper()
    if not allowed_exchanges: allowed_exchanges = ['NSE', 'NFO', 'MCX', 'CDS', 'BSE', 'BFO']
    try:
        mask = (instrument_dump['exchange'].isin(allowed_exchanges)) & (instrument_dump['name'].str.startswith(k, na=False))
        matches = instrument_dump[mask]
        if matches.empty: return []
        unique_matches = matches.drop_duplicates(subset=['name', 'exchange']).head(10)
        items_to_quote = [f"{row['exchange']}:{row['tradingsymbol']}" for _, row in unique_matches.iterrows()]
        
        quotes = {}
        try:
            if items_to_quote: quotes = kite.quote(items_to_quote)
        except: pass
        
        results = []
        for _, row in unique_matches.iterrows():
            key = f"{row['exchange']}:{row['tradingsymbol']}"
            ltp = quotes.get(key, {}).get('last_price', 0)
            results.append(f"{row['name']} ({row['exchange']}) : {ltp}")
        return results
    except Exception as e:
        print(f"Search Logic Error: {e}")
        return []

def adjust_cds_lot_size(symbol, lot_size):
    s = symbol.upper()
    if lot_size == 1:
        if "JPYINR" in s: return 100000
        if any(x in s for x in ["USDINR", "EURINR", "GBPINR", "USDJPY", "EURUSD", "GBPUSD"]): return 1000
    return lot_size

def get_symbol_details(kite, symbol, preferred_exchange=None):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: fetch_instruments(kite)
    if instrument_dump is None or instrument_dump.empty: return {}
    
    clean = get_zerodha_symbol(symbol)
    today = datetime.now(IST).date()
    rows = instrument_dump[instrument_dump['name'] == clean]
    if rows.empty: return {}

    # Basic LTP Logic
    exchanges = rows['exchange'].unique().tolist()
    exchange_to_use = preferred_exchange if preferred_exchange and preferred_exchange in exchanges else ("NSE" if "NSE" in exchanges else exchanges[0])
    quote_sym = f"{exchange_to_use}:{clean}"
    if clean == "NIFTY": quote_sym = "NSE:NIFTY 50"
    if clean == "BANKNIFTY": quote_sym = "NSE:NIFTY BANK"
    
    ltp = 0
    try:
        q = kite.quote(quote_sym)
        if quote_sym in q: ltp = q[quote_sym]['last_price']
    except: pass
        
    lot = 1
    # Try getting lot from FUT first
    futs = rows[(rows['exchange'] == 'NFO') & (rows['instrument_type'] == 'FUT')]
    if not futs.empty:
        lot = int(futs.iloc[0]['lot_size'])
    else:
        # Fallback to Options if FUT not found
        opts = rows[(rows['exchange'] == 'NFO') & (rows['instrument_type'].isin(['CE', 'PE']))]
        if not opts.empty:
            lot = int(opts.iloc[0]['lot_size'])

    f_exp, o_exp = [], []
    if 'expiry_str' in rows.columns:
        f_exp = sorted(rows[(rows['instrument_type'] == 'FUT') & (rows['expiry_date'] >= today)]['expiry_str'].unique().tolist())
        o_exp = sorted(rows[(rows['instrument_type'].isin(['CE', 'PE'])) & (rows['expiry_date'] >= today)]['expiry_str'].unique().tolist())
    
    return {"symbol": clean, "ltp": ltp, "lot_size": lot, "fut_expiries": f_exp, "opt_expiries": o_exp}

# --- NEW FUNCTION: SPECIFIC LOT SIZE FETCHER ---
def fetch_active_lot_size(kite, symbol_name):
    """
    Specifically finds the Lot Size for a symbol (like NIFTY) from active NFO contracts.
    Checks FUT first, then Options.
    """
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: fetch_instruments(kite)
    if instrument_dump is None or instrument_dump.empty: return 0
    
    clean = get_zerodha_symbol(symbol_name)
    
    try:
        # 1. Look for Futures (Most reliable)
        mask_fut = (instrument_dump['name'] == clean) & (instrument_dump['exchange'] == 'NFO') & (instrument_dump['instrument_type'] == 'FUT')
        df_fut = instrument_dump[mask_fut]
        if not df_fut.empty:
            return int(df_fut.iloc[0]['lot_size'])
            
        # 2. Look for Options (Fallback)
        mask_opt = (instrument_dump['name'] == clean) & (instrument_dump['exchange'] == 'NFO') & (instrument_dump['instrument_type'].isin(['CE', 'PE']))
        df_opt = instrument_dump[mask_opt]
        if not df_opt.empty:
            return int(df_opt.iloc[0]['lot_size'])
            
    except Exception as e:
        print(f"Lot Size Fetch Error: {e}")
        
    return 0

def get_chain_data(symbol, expiry_date, option_type, ltp):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return []
    clean = get_zerodha_symbol(symbol)
    c = instrument_dump[(instrument_dump['name'] == clean) & (instrument_dump['expiry_str'] == expiry_date) & (instrument_dump['instrument_type'] == option_type)]
    if c.empty: return []
    strikes = sorted(c['strike'].unique().tolist())
    if not strikes: return []
    atm = min(strikes, key=lambda x: abs(x - ltp))
    res = []
    for s in strikes:
        lbl = "ATM" if s == atm else ("ITM" if (option_type == "CE" and ltp > s) or (option_type == "PE" and ltp < s) else "OTM")
        res.append({"strike": s, "label": lbl})
    return res

def get_exact_symbol(symbol, expiry, strike, option_type):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return None
    clean = get_zerodha_symbol(symbol)
    if option_type == "FUT":
        mask = (instrument_dump['name'] == clean) & (instrument_dump['expiry_str'] == expiry) & (instrument_dump['instrument_type'] == "FUT")
    else:
        try: strike_price = float(strike)
        except: return None
        mask = (instrument_dump['name'] == clean) & (instrument_dump['expiry_str'] == expiry) & (instrument_dump['strike'] == strike_price) & (instrument_dump['instrument_type'] == option_type)
    if not mask.any(): return None
    return instrument_dump[mask].iloc[0]['tradingsymbol']

def get_specific_ltp(kite, symbol, expiry, strike, inst_type):
    ts = get_exact_symbol(symbol, expiry, strike, inst_type)
    if not ts: return 0
    try:
        global symbol_map
        exch = "NFO"
        if symbol_map and ts in symbol_map: exch = symbol_map[ts]['exchange']
        return kite.quote(f"{exch}:{ts}")[f"{exch}:{ts}"]['last_price']
    except: return 0

def get_instrument_token(tradingsymbol, exchange):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return None
    try:
        row = instrument_dump[(instrument_dump['tradingsymbol'] == tradingsymbol) & (instrument_dump['exchange'] == exchange)]
        if not row.empty: return int(row.iloc[0]['instrument_token'])
    except: pass
    return None

def fetch_historical_data(kite, token, from_date, to_date, interval='minute'):
    try:
        data = kite.historical_data(token, from_date, to_date, interval)
        for c in data:
            if 'date' in c and hasattr(c['date'], 'strftime'): c['date'] = c['date'].strftime('%Y-%m-%d %H:%M:%S')
        return data
    except Exception as e:
        print(f"History Fetch Error: {e}")
        return []

def get_next_weekly_expiry(symbol, from_date):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return None
    try:
        clean = get_zerodha_symbol(symbol)
        mask = (instrument_dump['name'] == clean) & (instrument_dump['instrument_type'].isin(['CE', 'PE', 'FUT']))
        df = instrument_dump[mask].copy() # Explicit copy
        if df.empty: return None
        df['expiry_dt'] = pd.to_datetime(df['expiry'], errors='coerce').dt.date
        future_expiries = df[df['expiry_dt'] >= from_date]['expiry_dt'].unique()
        if len(future_expiries) == 0: return None
        nearest = min(future_expiries)
        return nearest.strftime('%Y-%m-%d')
    except Exception as e:
        print(f"Expiry Fetch Error: {e}")
        return None
