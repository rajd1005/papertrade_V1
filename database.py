from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False) # Stores JSON string

class ActiveTrade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False) # Stores JSON string

class TradeHistory(db.Model):
    # BigInteger to handle timestamp IDs safely
    id = db.Column(db.BigInteger, primary_key=True)
    data = db.Column(db.Text, nullable=False) # Stores JSON string

class RiskState(db.Model):
    # Stores persistent state for Profit Locking (High PnL, Global SL)
    id = db.Column(db.String(10), primary_key=True) # "LIVE" or "PAPER"
    data = db.Column(db.Text, nullable=False) # JSON string

# --- NEW TABLE: Telegram Message Tracking ---
class TelegramMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.String(50), nullable=False, index=True)
    message_id = db.Column(db.Integer, nullable=False)
    chat_id = db.Column(db.String(50), nullable=False)
