import os
import kiteconnect

# 1. Monkey Patch
from mock_broker import MockKiteConnect, MOCK_MARKET_DATA, SIM_CONFIG
kiteconnect.KiteConnect = MockKiteConnect

# 2. Import App and SocketIO
os.environ["FLASK_ENV"] = "development"
from main import app, socketio # <--- IMPORT SOCKETIO

# 3. Inject Demo Routes
from flask import request, jsonify, render_template

@app.route('/demo')
def demo_ui():
    return render_template('demo_panel.html')

@app.route('/mock-login-trigger')
def mock_login():
    return '<script>window.location.href="/callback?request_token=mock&status=success";</script>'

# --- CONTROLS ---
@app.route('/demo/toggle_sim', methods=['POST'])
def toggle_sim():
    SIM_CONFIG["active"] = not SIM_CONFIG["active"]
    status = "RUNNING 🟢" if SIM_CONFIG["active"] else "PAUSED 🔴"
    return jsonify({"status": "success", "message": f"Market is now {status}", "active": SIM_CONFIG["active"]})

@app.route('/demo/set_volatility', methods=['POST'])
def set_vol():
    vol = float(request.form.get('volatility'))
    SIM_CONFIG["volatility"] = vol
    return jsonify({"status": "success", "message": f"Volatility set to {vol}%"})

@app.route('/demo/set_trend', methods=['POST'])
def set_trend():
    trend = request.form.get('trend') # BULLISH, BEARISH, SIDEWAYS
    SIM_CONFIG["trend"] = trend
    return jsonify({"status": "success", "message": f"Market Trend set to {trend}"})

@app.route('/demo/set_price', methods=['POST'])
def demo_set_price():
    sym = request.form.get('symbol')
    price = float(request.form.get('price'))
    MOCK_MARKET_DATA[sym] = price
    return jsonify({"status": "success", "message": f"Set {sym} to {price}"})

@app.route('/demo/get_state')
def demo_get_state():
    return jsonify({"prices": MOCK_MARKET_DATA, "config": SIM_CONFIG})

if __name__ == "__main__":
    print("🚀 STARTING DEMO MODE WITH WEBSOCKETS...")
    # UPDATED: Use socketio.run instead of app.run
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)
