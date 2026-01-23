from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Text, nullable=False) 

class ActiveTrade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # [FIX] Added Columns for Indexing/Querying
    symbol = db.Column(db.String(50), index=True)
    mode = db.Column(db.String(20))
    status = db.Column(db.String(20))
    data = db.Column(db.Text, nullable=False)

class TradeHistory(db.Model):
    id = db.Column(db.BigInteger, primary_key=True)
    # [FIX] Added Columns for Reporting efficiency
    symbol = db.Column(db.String(50), index=True)
    mode = db.Column(db.String(20))
    pnl = db.Column(db.Float)
    exit_time = db.Column(db.String(30), index=True) # YYYY-MM-DD HH:MM:SS
    data = db.Column(db.Text, nullable=False)

class RiskState(db.Model):
    id = db.Column(db.String(10), primary_key=True)
    data = db.Column(db.Text, nullable=False)

class TelegramMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.String(50), nullable=False, index=True)
    message_id = db.Column(db.Integer, nullable=False)
    chat_id = db.Column(db.String(50), nullable=False)
