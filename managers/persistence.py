import json
import threading
import time
from datetime import datetime
from database import db, ActiveTrade, TradeHistory, RiskState, TelegramMessage

# Global Lock for thread safety
TRADE_LOCK = threading.Lock()

# --- CACHING GLOBALS ---
# We use this cache to serve the Frontend instantly without hitting the DB
_ACTIVE_TRADES_CACHE = None
_LAST_SAVE_TIME = 0
SAVE_INTERVAL = 2.0  # Only write to DB every 2 seconds to prevent Disk I/O Lag

# --- Risk State Persistence ---
def get_risk_state(mode):
    try:
        record = RiskState.query.filter_by(id=mode).first()
        if record:
            return json.loads(record.data)
    except Exception as e:
        print(f"Error fetching risk state for {mode}: {e}")
    return {'high_pnl': float('-inf'), 'global_sl': float('-inf'), 'active': False}

def save_risk_state(mode, state):
    try:
        record = RiskState.query.filter_by(id=mode).first()
        if not record:
            record = RiskState(id=mode, data=json.dumps(state))
            db.session.add(record)
        else:
            record.data = json.dumps(state)
        db.session.commit()
    except Exception as e:
        print(f"Risk State Save Error: {e}")
        db.session.rollback()

# --- Active Trades Persistence ---
def load_trades():
    """
    Returns cached trades from RAM if available (Instant).
    Falls back to DB only if cache is empty (e.g. on Startup).
    """
    global _ACTIVE_TRADES_CACHE
    
    # Return Cache if warm (Super Fast)
    if _ACTIVE_TRADES_CACHE is not None:
        return _ACTIVE_TRADES_CACHE

    try:
        # Initial Load from DB
        db.session.remove() 
        raw_rows = ActiveTrade.query.all()
        _ACTIVE_TRADES_CACHE = [json.loads(r.data) for r in raw_rows]
        return _ACTIVE_TRADES_CACHE
    except Exception as e:
        print(f"[DEBUG] Load Trades Error: {e}")
        return []

def save_trades(trades):
    """
    Updates RAM instantly. Writes to DB only if 2 seconds have passed since last save.
    This prevents the database from choking the WebSocket/Risk Engine loop.
    """
    global _ACTIVE_TRADES_CACHE, _LAST_SAVE_TIME
    
    try:
        # 1. Update Memory Cache Immediately (REAL-TIME)
        _ACTIVE_TRADES_CACHE = trades
        
        # 2. Check if we should write to DB (THROTTLING)
        now = time.time()
        # If we have trades and it's been less than 2s, skip DB write
        if trades and (now - _LAST_SAVE_TIME < SAVE_INTERVAL):
            return  # Skip DB write, keep system fast
            
        _LAST_SAVE_TIME = now

        # 3. Sync to DB (Background Backup)
        existing_records = ActiveTrade.query.all()
        existing_map = {r.id: r for r in existing_records}
        new_ids = set()

        for t in trades:
            t_id = int(t['id'])
            new_ids.add(t_id)
            json_data = json.dumps(t)
            
            # Extract fields for SQL Columns
            sym = t.get('symbol')
            mod = t.get('mode')
            sta = t.get('status')
            
            if t_id in existing_map:
                rec = existing_map[t_id]
                # Optimization: Only SQL update if string changed
                if rec.data != json_data:
                    rec.data = json_data
                    rec.symbol = sym
                    rec.mode = mod
                    rec.status = sta
            else:
                new_record = ActiveTrade(id=t_id, data=json_data, symbol=sym, mode=mod, status=sta)
                db.session.add(new_record)
        
        # Remove deleted trades from DB
        for old_id, record in existing_map.items():
            if old_id not in new_ids:
                db.session.delete(record)
        
        db.session.commit()
        
    except Exception as e:
        print(f"Save Trades Error: {e}")
        db.session.rollback()

# --- Trade History Persistence ---
def load_history():
    """
    Legacy load all (used for History Tab).
    """
    try:
        db.session.commit()
        return [json.loads(r.data) for r in TradeHistory.query.order_by(TradeHistory.id.desc()).all()]
    except Exception as e:
        print(f"Load History Error: {e}")
        return []

def load_todays_history():
    """
    Optimized loader: Only loads TODAY's closed trades.
    Used by Risk Engine to track Highs of closed trades without loading the entire DB.
    """
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # SQL Filter: fast retrieval of today's data using LIKE query
        rows = TradeHistory.query.filter(TradeHistory.exit_time.like(f"{today_str}%")).all()
        return [json.loads(r.data) for r in rows]
    except Exception as e:
        print(f"Load Today History Error: {e}")
        return []

def delete_trade(trade_id):
    from managers.telegram_manager import bot as telegram_bot
    with TRADE_LOCK:
        try:
            # Delete associated Telegram messages first
            telegram_bot.delete_trade_messages(trade_id)
            
            # Delete from History DB
            TradeHistory.query.filter_by(id=int(trade_id)).delete()
            db.session.commit()
            return True
        except Exception as e:
            print(f"Delete Trade Error: {e}")
            db.session.rollback()
            return False

def save_to_history_db(trade_data):
    """
    Saves a closed trade to the history table.
    Called when a trade exits or is manually closed.
    """
    try:
        t_id = trade_data['id']
        json_str = json.dumps(trade_data)
        
        existing = TradeHistory.query.get(t_id)
        if existing:
            existing.data = json_str
            existing.symbol = trade_data.get('symbol')
            existing.mode = trade_data.get('mode')
            existing.pnl = trade_data.get('pnl')
            existing.exit_time = trade_data.get('exit_time')
        else:
            rec = TradeHistory(
                id=t_id, 
                data=json_str,
                symbol=trade_data.get('symbol'),
                mode=trade_data.get('mode'),
                pnl=trade_data.get('pnl'),
                exit_time=trade_data.get('exit_time')
            )
            db.session.add(rec)
            
        db.session.commit()
    except Exception as e:
        print(f"Save History DB Error: {e}")
        db.session.rollback()
