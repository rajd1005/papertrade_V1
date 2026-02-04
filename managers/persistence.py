import json
from database import db, ActiveTrade, TradeHistory, RiskState, TelegramMessage
from datetime import datetime, timedelta
import time

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
    Loads all currently active trades from the database.
    INCLUDES DEBUG LOGGING AND SESSION RESET.
    """
    try:
        # [DEBUG] Reset session to force fresh read
        db.session.remove() 
        
        raw_rows = ActiveTrade.query.all()
        trades = [json.loads(r.data) for r in raw_rows]
        
        return trades
    except Exception as e:
        print(f"[DEBUG] Load Trades Error: {e}")
        return []

def save_trades(trades):
    """
    Overwrites the ActiveTrade table with the provided list of trades.
    """
    try:
        # [DEBUG LOG] - Optional: You can comment this out too if needed
        # ids = [t.get('id') for t in trades]
        # modes = [t.get('mode') for t in trades]
        # print(f"[DEBUG] DB SAVE: Saving {len(trades)} trades. IDs: {ids} | Modes: {modes}")

        db.session.query(ActiveTrade).delete()
        for t in trades: 
            db.session.add(ActiveTrade(data=json.dumps(t)))
        db.session.commit()
        # print(f"[DEBUG] DB SAVE: Commit Successful.")
    except Exception as e:
        print(f"[DEBUG] Save Trades Error: {e}")
        db.session.rollback()

# --- Trade History Persistence ---
def load_history():
    try:
        db.session.commit() # Ensure fresh
        return [json.loads(r.data) for r in TradeHistory.query.order_by(TradeHistory.id.desc()).all()]
    except Exception as e:
        print(f"Load History Error: {e}")
        return []

def delete_trade(trade_id):
    from managers.telegram_manager import bot as telegram_bot
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
    try:
        db.session.merge(TradeHistory(id=trade_data['id'], data=json.dumps(trade_data)))
        db.session.commit()
    except Exception as e:
        print(f"Save History DB Error: {e}")
        db.session.rollback()

def cleanup_old_data(days=7):
    """
    Deletes trade history and associated telegram messages older than X days.
    """
    try:
        # Calculate the threshold timestamp (IDs in this DB are timestamp-based)
        # 86400 seconds = 1 day
        threshold_id = int((time.time() - (days * 86400)) * 1000) 
        
        # 1. Delete associated Telegram messages first (Foreign Key/Reference cleanup)
        # We fetch trade IDs that are about to be deleted
        old_trades = TradeHistory.query.filter(TradeHistory.id < threshold_id).all()
        for t in old_trades:
            TelegramMessage.query.filter_by(trade_id=str(t.id)).delete()
        
        # 2. Delete from TradeHistory
        deleted_count = TradeHistory.query.filter(TradeHistory.id < threshold_id).delete()
        
        db.session.commit()
        if deleted_count > 0:
            print(f"🧹 Database Cleanup: Removed {deleted_count} records older than {days} days.")
        return True
    except Exception as e:
        print(f"❌ Cleanup Error: {e}")
        db.session.rollback()
        return False
