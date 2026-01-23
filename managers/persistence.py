import json
import threading
from datetime import datetime
from database import db, ActiveTrade, TradeHistory, RiskState, TelegramMessage

# Global Lock for thread safety
TRADE_LOCK = threading.Lock()

# [FIX] Global In-Memory Cache
_ACTIVE_TRADES_CACHE = None

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
    [FIX] Returns cached trades if available to reduce DB I/O.
    """
    global _ACTIVE_TRADES_CACHE
    
    # Return Cache if warm
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
    [FIX] Updates Cache AND Database (including new SQL columns).
    """
    global _ACTIVE_TRADES_CACHE
    try:
        # 1. Update Memory Cache Immediately
        _ACTIVE_TRADES_CACHE = trades

        # 2. Sync to DB
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
                # Update existing record
                rec = existing_map[t_id]
                rec.data = json_data
                rec.symbol = sym
                rec.mode = mod
                rec.status = sta
            else:
                # Insert new record with columns
                new_record = ActiveTrade(
                    id=t_id, 
                    data=json_data,
                    symbol=sym,
                    mode=mod,
                    status=sta
                )
                db.session.add(new_record)
        
        # 3. Delete removed records
        for old_id, record in existing_map.items():
            if old_id not in new_ids:
                db.session.delete(record)
        
        db.session.commit()
    except Exception as e:
        print(f"Save Trades Error: {e}")
        db.session.rollback()

# --- Trade History Persistence ---
def load_history():
    # Legacy load all (used for History Tab)
    try:
        db.session.commit()
        return [json.loads(r.data) for r in TradeHistory.query.order_by(TradeHistory.id.desc()).all()]
    except Exception as e:
        print(f"Load History Error: {e}")
        return []

def load_todays_history():
    """
    [FIX] Optimized loader for Risk Engine. 
    Only loads trades where exit_time matches today's date using SQL filter.
    """
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # SQL Filter: exit_time LIKE '2023-10-27%'
        rows = TradeHistory.query.filter(TradeHistory.exit_time.like(f"{today_str}%")).all()
        return [json.loads(r.data) for r in rows]
    except Exception as e:
        print(f"Load Today History Error: {e}")
        return []

def delete_trade(trade_id):
    from managers.telegram_manager import bot as telegram_bot
    with TRADE_LOCK:
        try:
            telegram_bot.delete_trade_messages(trade_id)
            TradeHistory.query.filter_by(id=int(trade_id)).delete()
            db.session.commit()
            return True
        except Exception as e:
            print(f"Delete Trade Error: {e}")
            db.session.rollback()
            return False

def save_to_history_db(trade_data):
    """
    [FIX] Populates SQL columns (pnl, exit_time) for efficient reporting.
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
