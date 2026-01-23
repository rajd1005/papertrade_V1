import pytz
from datetime import datetime
import settings
from managers.persistence import load_history, load_trades

# Global Timezone
IST = pytz.timezone('Asia/Kolkata')

def get_time_str():
    """
    Returns the current time in IST as a formatted string.
    Format: YYYY-MM-DD HH:MM:SS
    """
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

def log_event(trade, message):
    """
    Appends a timestamped message to the trade's log list.
    """
    if 'logs' not in trade:
        trade['logs'] = []
    trade['logs'].append(f"[{get_time_str()}] {message}")

def get_exchange(symbol):
    """
    Determines the exchange (NSE, NFO, MCX, CDS, BSE) based on the symbol name.
    """
    s = symbol.upper()
    if any(x in s for x in ['CRUDEOIL', 'GOLD', 'SILVER', 'COPPER', 'NATURALGAS']):
        return "MCX"
    if any(x in s for x in ['USDINR', 'EURINR', 'GBPINR', 'JPYINR']):
        return "CDS"
    if "SENSEX" in s or "BANKEX" in s:
        # Check if it has digits (Futures/Options usually have dates/strikes) to distinguish BFO
        return "BFO" if any(char.isdigit() for char in s) else "BSE"
    if symbol.endswith("CE") or symbol.endswith("PE") or "FUT" in symbol:
        return "NFO"
    return "NSE"

def get_day_pnl(mode):
    """
    Calculates the Total Daily P&L for a specific mode (LIVE/PAPER).
    Includes:
    1. Realized P&L from closed trades today.
    2. Unrealized P&L from currently active trades.
    """
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    total = 0.0
    
    # 1. Sum Realized P&L from History
    history = load_history()
    for t in history:
        # Check if exit_time exists and matches today
        if t.get('exit_time') and t['exit_time'].startswith(today_str) and t['mode'] == mode:
            total += t.get('pnl', 0)
            
    # 2. Sum Unrealized P&L from Active Trades
    active = load_trades()
    for t in active:
        if t['mode'] == mode and t['status'] != 'PENDING':
            # Use current_ltp if available, else fallback to entry_price (0 PnL)
            current_price = t.get('current_ltp', t['entry_price'])
            unrealized = (current_price - t['entry_price']) * t['quantity']
            total += unrealized
            
    return total

def can_place_order(mode):
    """
    Checks if a new order is allowed based on Global Risk Settings (Max Daily Loss).
    Returns: (Boolean allowed, String reason)
    """
    current_settings = settings.load_settings()
    
    if mode not in current_settings['modes']:
        return True, "OK"
        
    mode_conf = current_settings['modes'][mode]
    max_loss_limit = float(mode_conf.get('max_loss', 0))
    
    # If Max Loss is set (greater than 0)
    if max_loss_limit > 0:
        limit = -abs(max_loss_limit) # Ensure it's treated as a negative number
        current_pnl = get_day_pnl(mode)
        
        if current_pnl <= limit:
            return False, f"Max Daily Loss Reached ({current_pnl:.2f} <= {limit})"
            
    return True, "OK"
