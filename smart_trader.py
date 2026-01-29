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
    """
    Downloads the master instrument list, optimizes dates, and builds a fast lookup map.
    Prioritizes specific exchanges (NFO > MCX > NSE) to handle duplicate symbols.
    """
    global instrument_dump, symbol_map
    
    # If already loaded and map exists, skip to save bandwidth
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
        
        # --- CRITICAL FIX: Handle Duplicates for Hash Map ---
        print("⚡ Building Fast Lookup Cache...")
        
        # Create a copy to sort and deduplicate without affecting the main search dump
        temp_df = instrument_dump.copy()
        
        # Prioritize exchanges: NFO > MCX > CDS > NSE > BSE
        # This ensures 'RELIANCE' maps to NSE, not BSE
        exchange_priority = {'NFO': 0, 'MCX': 1, 'CDS': 2, 'NSE': 3, 'BSE': 4, 'BFO': 5}
        temp_df['priority'] = temp_df['exchange'].map(exchange_priority).fillna(99)
        
        # Sort by priority so the "best" exchange comes first
        temp_df.sort_values('priority', inplace=True)
        
        # Drop duplicates on tradingsymbol, keeping the first (highest priority)
        unique_symbols = temp_df.drop_duplicates(subset=['tradingsymbol'])
        
        # NOW it is safe to set index
        symbol_map = unique_symbols.set_index('tradingsymbol').to_dict('index')
        
        print(f"✅ Instruments Downloaded & Indexed. Count: {len(instrument_dump)}")
        
    except Exception as e:
        print(f"❌ Failed to fetch instruments: {e}")
        # Do not reset to None here if partial data exists
        if instrument_dump is None:
             instrument_dump = pd.DataFrame()
        symbol_map = {}

def get_exchange_name(symbol):
    """
    Determines the exchange (NSE, NFO, MCX) for a given symbol.
    """
    global symbol_map
    
    # 1. Check if symbol already has exchange prefix (e.g. "NSE:RELIANCE")
    if ":" in symbol:
        return symbol.split(":")[0]

    # 2. Fast Lookup via Map
    if symbol_map and symbol in symbol_map:
        return symbol_map[symbol]['exchange']
        
    # 3. Fallback Heuristics
    if "NIFTY" in symbol or "BANKNIFTY" in symbol:
        if any(x in symbol for x in ["FUT", "CE", "PE"]): 
            return "NFO"
            
    return "NSE"

def get_ltp(kite, symbol):
    """
    Fetches LTP from WebSocket Cache (Fast) -> Fallback to API (Slow).
    Auto-subscribes to Ticker if token is known but not subscribed.
    """
    try:
        # 1. Resolve Exchange & Token
        exch = get_exchange_name(symbol)
        token = get_instrument_token(symbol, exch)
        
        # 2. Try fetching from WebSocket Cache first (FASTEST)
        if token and zerodha_ticker.ticker:
            cached_price = zerodha_ticker.ticker.get_ltp(token)
            if cached_price:
                return cached_price
            
            # If valid token but missing in cache, Subscribe for NEXT time
            zerodha_ticker.ticker.subscribe([token])

        # 3. Fallback: API Method (SLOW, but guaranteed)
        full_sym = f"{exch}:{symbol}"
        if ":" in symbol: full_sym = symbol
        
        quote = kite.quote(full_sym)
        if quote and full_sym in quote:
            return quote[full_sym]['last_price']
            
        return 0
    except Exception as e:
        print(f"⚠️ Error fetching LTP for {symbol}: {e}")
        return 0

def get_indices_ltp(kite):
    """
    Fetches Indices LTP using Ticker first, falling back to API only if necessary.
    """
    indices_map = {
        "NSE:NIFTY 50": "NIFTY",
        "NSE:NIFTY BANK": "BANKNIFTY",
        "BSE:SENSEX": "SENSEX"
    }
    
    response = {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0}
    missing_for_api = []

    # 1. Try Ticker Cache
    if zerodha_ticker.ticker:
        for full_name, short_name in indices_map.items():
            exch, sym = full_name.split(':')
            token = get_instrument_token(sym, exch)
            
            val = 0
            if token:
                val = zerodha_ticker.ticker.get_ltp(token)
                if not val:
                    # Subscribe if missing so next call works
                    zerodha_ticker.ticker.subscribe([token])
            
            if val:
                response[short_name] = val
            else:
                missing_for_api.append(full_name)
    else:
        missing_for_api = list(indices_map.keys())

    # 2. API Fallback for missing
    if missing_for_api:
        try:
            q = kite.quote(missing_for_api)
            for full_name in missing_for_api:
                short_name = indices_map[full_name]
                if full_name in q:
                    response[short_name] = q[full_name]['last_price']
        except: pass
        
    return response

def get_zerodha_symbol(common_name):
    if not common_name: return ""
    cleaned = common_name
    if "(" in cleaned: cleaned = cleaned.split("(")[0]
    u = cleaned.upper().strip()
    if u in ["BANKNIFTY", "NIFTY BANK", "BANK NIFTY"]: return "BANKNIFTY"
    if u in ["NIFTY", "NIFTY 50", "NIFTY50"]: return "NIFTY"
    if u == "SENSEX": return "SENSEX"
    if u == "FINNIFTY": return "FINNIFTY"
    return u

def get_lot_size(tradingsymbol):
    global symbol_map
    if not symbol_map: return 1
    
    # Fast Lookup
    data = symbol_map.get(tradingsymbol)
    if data:
        return int(data.get('lot_size', 1))
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
                    if hasattr(ed, 'strftime'): expiry_str = ed.strftime('%d %b').upper()
                    else: expiry_str = str(ed)

            if inst_type in ["CE", "PE"]:
                strike = int(data['strike'])
                return f"{name} {strike} {inst_type} {expiry_str}"
            elif inst_type == "FUT":
                 return f"{name} FUT {expiry_str}"
            else:
                 return f"{name} {inst_type}"
        return tradingsymbol
    except:
        return tradingsymbol

def search_symbols(kite, keyword, allowed_exchanges=None):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: 
        fetch_instruments(kite)
        if instrument_dump is None or instrument_dump.empty: return []

    k = keyword.upper()
    if not allowed_exchanges: 
        allowed_exchanges = ['NSE', 'NFO', 'MCX', 'CDS', 'BSE', 'BFO']
    
    try:
        mask = (instrument_dump['exchange'].isin(allowed_exchanges)) & (instrument_dump['name'].str.startswith(k, na=False))
        matches = instrument_dump[mask]
        
        if matches.empty: return []
            
        unique_matches = matches.drop_duplicates(subset=['name', 'exchange']).head(10)
        items_to_quote = [f"{row['exchange']}:{row['tradingsymbol']}" for _, row in unique_matches.iterrows()]
        
        # Use API quote here as search is on-demand user action
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
    
    if "(" in symbol and ")" in symbol:
        try:
            parts = symbol.split('(')
            if len(parts) > 1: preferred_exchange = parts[1].split(')')[0].strip()
        except: pass

    clean = get_zerodha_symbol(symbol)
    today = datetime.now(IST).date()
    
    rows = instrument_dump[instrument_dump['name'] == clean]
    if rows.empty: return {}

    exchanges = rows['exchange'].unique().tolist()
    exchange_to_use = "NSE"
    if preferred_exchange and preferred_exchange in exchanges:
        exchange_to_use = preferred_exchange
    else:
        for p in ['MCX', 'CDS', 'BSE', 'NSE']:
             if p in exchanges: exchange_to_use = p; break
    
    quote_sym = f"{exchange_to_use}:{clean}"
    if clean == "NIFTY": quote_sym = "NSE:NIFTY 50"
    if clean == "BANKNIFTY": quote_sym = "NSE:NIFTY BANK"
    if clean == "SENSEX": quote_sym = "BSE:SENSEX"
    
    # Use centralized Ticker-aware LTP
    ltp = get_ltp(kite, quote_sym)

    lot = 1
    for ex in ['MCX', 'CDS', 'BFO', 'NFO']:
        futs = rows[(rows['exchange'] == ex) & (rows['instrument_type'] == 'FUT')]
        if not futs.empty:
            lot = int(futs.iloc[0]['lot_size'])
            if ex == 'CDS': lot = adjust_cds_lot_size(clean, lot)
            break
            
    f_exp = []
    o_exp = []
    
    if 'expiry_str' in rows.columns and 'expiry_date' in rows.columns:
        f_exp = sorted(rows[(rows['instrument_type'] == 'FUT') & (rows['expiry_date'] >= today)]['expiry_str'].unique().tolist())
        o_exp = sorted(rows[(rows['instrument_type'].isin(['CE', 'PE'])) & (rows['expiry_date'] >= today)]['expiry_str'].unique().tolist())
    
    return {"symbol": clean, "ltp": ltp, "lot_size": lot, "fut_expiries": f_exp, "opt_expiries": o_exp}

def get_chain_data(symbol, expiry_date, option_type, ltp):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return []
    clean = get_zerodha_symbol(symbol)
    
    if 'expiry_str' not in instrument_dump.columns: return []
    
    c = instrument_dump[(instrument_dump['name'] == clean) & (instrument_dump['expiry_str'] == expiry_date) & (instrument_dump['instrument_type'] == option_type)]
    if c.empty: return []
    
    strikes = sorted(c['strike'].unique().tolist())
    if not strikes: return []
    atm = min(strikes, key=lambda x: abs(x - ltp))
    
    res = []
    for s in strikes:
        lbl = "OTM"
        if s == atm: lbl = "ATM"
        elif option_type == "CE": lbl = "ITM" if ltp > s else "OTM"
        elif option_type == "PE": lbl = "ITM" if ltp < s else "OTM"
        res.append({"strike": s, "label": lbl})
    return res

def get_exact_symbol(symbol, expiry, strike, option_type):
    global instrument_dump
    if instrument_dump is None or instrument_dump.empty: return None
    if option_type == "EQ": return symbol
    clean = get_zerodha_symbol(symbol)
    
    if 'expiry_str' not in instrument_dump.columns: return None

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
    # Use the robust Ticker-aware LTP fetcher
    return get_ltp(kite, ts) 

def get_instrument_token(tradingsymbol, exchange):
    global symbol_map, instrument_dump
    
    # 1. Fast Path: Check Hash Map first (Priority Exchange Match)
    if symbol_map and tradingsymbol in symbol_map:
        data = symbol_map[tradingsymbol]
        if data['exchange'] == exchange:
            return int(data['instrument_token'])
            
    # 2. Slow Path: Check DataFrame (Safe fallback for non-priority exchanges)
    if instrument_dump is not None and not instrument_dump.empty:
        try:
            row = instrument_dump[(instrument_dump['tradingsymbol'] == tradingsymbol) & (instrument_dump['exchange'] == exchange)]
            if not row.empty:
                return int(row.iloc[0]['instrument_token'])
        except: pass
        
    return None

def fetch_historical_data(kite, token, from_date, to_date, interval='minute'):
    try:
        data = kite.historical_data(token, from_date, to_date, interval)
        clean_data = []
        for candle in data:
            c = candle.copy()
            if 'date' in c and hasattr(c['date'], 'strftime'):
                c['date'] = c['date'].strftime('%Y-%m-%d %H:%M:%S')
            clean_data.append(c)
        return clean_data
    except Exception as e:
        print(f"History Fetch Error: {e}")
        return []

def get_telegram_symbol(tradingsymbol):
    try:
        weekly_pattern = r"^([A-Z]+)(\d{2})([1-9OND])(\d{2})(\d+)(CE|PE)$"
        w_match = re.match(weekly_pattern, tradingsymbol)
        if w_match:
            name, yy, m_char, dd, strike, opt_type = w_match.groups()
            m_map = {'1':'JAN', '2':'FEB', '3':'MAR', '4':'APR', '5':'MAY', '6':'JUN', 
                     '7':'JUL', '8':'AUG', '9':'SEP', 'O':'OCT', 'N':'NOV', 'D':'DEC'}
            month_str = m_map.get(m_char, '???')
            return f"{name} {strike} {opt_type} {dd}{month_str}"

        monthly_pattern = r"^([A-Z]+)(\d{2})([A-Z]{3})(\d+)(CE|PE)$"
        m_match = re.match(monthly_pattern, tradingsymbol)
        if m_match:
            name, yy, mon, strike, opt_type = m_match.groups()
            return f"{name} {strike} {opt_type} {mon}"
            
        fut_pattern = r"^([A-Z]+)(\d{2})([A-Z]{3})FUT$"
        f_match = re.match(fut_pattern, tradingsymbol)
        if f_match:
             name, yy, mon = f_match.groups()
             return f"{name} FUT {mon}"

        return tradingsymbol
    except:
        return tradingsymbol
