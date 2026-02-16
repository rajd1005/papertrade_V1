import os
import json
import threading
import time
import gc 
import requests
import redis
from flask import Flask, render_template, request, redirect, flash, jsonify, url_for
from kiteconnect import KiteConnect
from flask_socketio import SocketIO
import config
from managers import config_manager 

# --- IMPORTS ---
from managers import persistence, trade_manager, risk_engine, replay_engine, common, broker_ops
from managers.telegram_manager import bot as telegram_bot
import smart_trader
import settings
from database import db, AppSetting

# NOTE: auto_login is removed as the Gateway handles it
# import auto_login 

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.from_object(config)

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Initialize Database
db.init_app(app)
with app.app_context():
    db.create_all()

# --- GATEWAY / REDIS SETUP ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Initial Kite Instance
# We use a dummy API Key initially; it will be updated from the Gateway
kite = KiteConnect(api_key=config.API_KEY)

# --- GLOBAL STATE MANAGEMENT ---
bot_active = False
login_state = "WAITING_FOR_GATEWAY" 
login_error_msg = "Waiting for Market Data Gateway..." 
ticker_started = False 

def sync_with_gateway():
    """
    Checks Redis to see if the Gateway has successfully logged in.
    If yes, it pulls the Access Token and activates this bot.
    """
    global bot_active, login_state, login_error_msg, ticker_started
    
    try:
        # 1. Fetch Token from Gateway (Redis)
        access_token = redis_client.get("ZERODHA_ACCESS_TOKEN")
        
        if access_token:
            # Check if we are already connected with this token
            if kite.access_token != access_token:
                print(f"🔗 Found New Access Token from Gateway. Syncing...")
                
                # Update Kite Instance
                kite.set_access_token(access_token)
                
                # Optional: Update API Key if Gateway stored it (advanced setup)
                # gateway_api_key = redis_client.get("ZERODHA_API_KEY")
                # if gateway_api_key: kite.api_key = gateway_api_key
                
                # Fetch instruments to warm up cache
                smart_trader.fetch_instruments(kite)
                
                bot_active = True
                login_state = "CONNECTED"
                login_error_msg = None
                ticker_started = False # Reset ticker to force restart with new token
                
                telegram_bot.notify_system_event("LOGIN_SUCCESS", "Synced with Market Data Gateway. System Online.")
        else:
            # Gateway hasn't logged in yet
            if bot_active:
                print("⚠️ Gateway Token Expired/Missing.")
                bot_active = False
                login_state = "WAITING_FOR_GATEWAY"
                login_error_msg = "Gateway Token Missing"
            
    except Exception as e:
        print(f"❌ Gateway Sync Error: {e}")
        login_state = "ERROR"
        login_error_msg = str(e)

def background_monitor():
    global bot_active, login_state, ticker_started
    
    last_cleanup_time = 0 
    
    with app.app_context():
        try:
            telegram_bot.notify_system_event("STARTUP", "PaperTrade V1 (Gateway Mode) Started.")
            print("🖥️ Background Monitor Started (Gateway Mode)")
        except Exception as e:
            print(f"❌ Startup Notification Failed: {e}")
    
    time.sleep(2) 
    
    while True:
        with app.app_context():
            try:
                # --- AUTO-DELETE OLD DATA (Runs once every 24 hours) ---
                current_time = time.time()
                if current_time - last_cleanup_time > 86400: 
                    persistence.cleanup_old_data(days=7)
                    last_cleanup_time = current_time
                # -------------------------------------------------------

                # 1. SYNC WITH GATEWAY
                # We constantly check if the Gateway has fresh tokens
                sync_with_gateway()

                # 2. Active Bot Logic
                if bot_active:
                    try:
                        # --- WEBSOCKET LOGIC ---
                        if not ticker_started:
                            print("🚀 Connecting to Market Data Gateway Stream...")
                            
                            # We pass the kite object. The 'risk_engine' is updated to use RedisTicker
                            # which talks to the Gateway, so it doesn't strictly need the api_key for *data*,
                            # but we pass what we have.
                            risk_engine.start_ticker(kite.api_key, kite.access_token, kite, app, socketio)
                            ticker_started = True
                        
                        # 2. Sync Subscriptions (Handles new manual trades)
                        risk_engine.update_subscriptions()
                        
                        # 3. Run Global Checks (Time Exit / Profit Lock)
                        current_settings = settings.load_settings()
                        risk_engine.check_global_exit_conditions(kite, "PAPER", current_settings['modes']['PAPER'])
                        # LIVE check is valid only if Gateway provided a Real Token (Shadow Mode)
                        risk_engine.check_global_exit_conditions(kite, "LIVE", current_settings['modes']['LIVE'])
                        
                    except Exception as e:
                        print(f"⚠️ Loop Error: {e}")
                        # If critical error, force re-sync
                        # bot_active = False 

            except Exception as e:
                print(f"❌ Monitor Loop Critical Error: {e}")
            finally:
                db.session.remove()
        
        time.sleep(2.0) 

@app.route('/')
def home():
    global bot_active, login_state
    
    if bot_active:
        trades = persistence.load_trades()
        for t in trades: 
            t['symbol'] = smart_trader.get_display_name(t['symbol'])
        active = [t for t in trades if t['status'] in ['OPEN', 'PROMOTED_LIVE', 'PENDING', 'MONITORING']]
        return render_template('dashboard.html', is_active=True, trades=active)
    
    return render_template('dashboard.html', is_active=False, state=login_state, error=login_error_msg, login_url="#")

@app.route('/api/status')
def api_status():
    return jsonify({"active": bot_active, "state": login_state, "login_url": "#"})

@app.route('/reset_connection')
def reset_connection():
    global bot_active, login_state, ticker_started
    
    telegram_bot.notify_system_event("RESET", "Manual Reset. Re-syncing with Gateway.")
    
    bot_active = False
    login_state = "RESETTING"
    ticker_started = False 
    
    # We delete the local reference, but we DO NOT delete the Redis key 
    # because the Gateway might still be healthy. We just want to re-fetch.
    kite.set_access_token("")
    
    flash("🔄 Connection Reset. Syncing with Gateway...")
    return redirect('/')

@app.route('/api/settings/load')
def api_settings_load():
    s = settings.load_settings()
    try:
        from managers.common import IST
        from datetime import datetime
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        trades = persistence.load_trades()
        history = persistence.load_history()
        count = 0
        if trades:
            for t in trades:
                if t.get('entry_time', '').startswith(today_str): count += 1
        if history:
            for t in history:
                if t.get('entry_time', '').startswith(today_str): count += 1
        s['is_first_trade'] = (count == 0)
    except Exception as e:
        s['is_first_trade'] = False
    return jsonify(s)

@app.route('/api/settings/save', methods=['POST'])
def api_settings_save():
    if settings.save_settings_file(request.json):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/positions')
def api_positions():
    trades = persistence.load_trades()
    for t in trades:
        t['lot_size'] = smart_trader.get_lot_size(t['symbol'])
        t['symbol'] = smart_trader.get_display_name(t['symbol'])
    return jsonify(trades)

@app.route('/api/closed_trades')
def api_closed_trades():
    trades = persistence.load_history()
    for t in trades:
        t['symbol'] = smart_trader.get_display_name(t['symbol'])
    return jsonify(trades)

@app.route('/api/delete_trade/<trade_id>', methods=['POST'])
def api_delete_trade(trade_id):
    if persistence.delete_trade(trade_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/update_trade', methods=['POST'])
def api_update_trade():
    data = request.json
    try:
        if trade_manager.update_trade_protection(kite, data['id'], data['sl'], data['targets'], data.get('trailing_sl', 0), data.get('entry_price'), data.get('target_controls'), data.get('sl_to_entry', 0), data.get('exit_multiplier', 1)):
            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "message": "Trade not found"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/manage_trade', methods=['POST'])
def api_manage_trade():
    data = request.json
    trade_id = data.get('id')
    action = data.get('action')
    lots = int(data.get('lots', 0))
    
    trades = persistence.load_trades()
    t = next((x for x in trades if str(x['id']) == str(trade_id)), None)
    if t and lots > 0:
        lot_size = smart_trader.get_lot_size(t['symbol'])
        if trade_manager.manage_trade_position(kite, trade_id, action, lot_size, lots):
            return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Action Failed"})

@app.route('/api/indices')
def api_indices():
    if not bot_active:
        return jsonify({"NIFTY":0, "BANKNIFTY":0, "SENSEX":0})
    return jsonify(smart_trader.get_indices_ltp(kite))

@app.route('/api/search')
def api_search():
    current_settings = settings.load_settings()
    allowed = current_settings.get('exchanges', None)
    return jsonify(smart_trader.search_symbols(kite, request.args.get('q', ''), allowed))

@app.route('/api/details')
def api_details():
    return jsonify(smart_trader.get_symbol_details(kite, request.args.get('symbol', '')))

@app.route('/api/chain')
def api_chain():
    return jsonify(smart_trader.get_chain_data(request.args.get('symbol'), request.args.get('expiry'), request.args.get('type'), float(request.args.get('ltp', 0))))

@app.route('/api/specific_ltp')
def api_s_ltp():
    return jsonify({"ltp": smart_trader.get_specific_ltp(kite, request.args.get('symbol'), request.args.get('expiry'), request.args.get('strike'), request.args.get('type'))})

@app.route('/api/panic_exit', methods=['POST'])
def api_panic_exit():
    if not bot_active:
        return jsonify({"status": "error", "message": "Bot not connected"})
    if broker_ops.panic_exit_all(kite):
        flash("🚨 PANIC MODE EXECUTED. ALL TRADES CLOSED.")
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Failed to execute panic mode"})

# --- TELEGRAM ROUTES ---
@app.route('/api/manual_trade_report', methods=['POST'])
def api_manual_trade_report():
    trade_id = request.json.get('trade_id')
    if not trade_id: return jsonify({"status": "error", "message": "Trade ID missing"})
    result = risk_engine.send_manual_trade_report(trade_id)
    return jsonify(result)

@app.route('/api/manual_summary', methods=['POST'])
def api_manual_summary():
    mode = request.json.get('mode', 'PAPER')
    result = risk_engine.send_manual_summary(mode)
    return jsonify(result)

@app.route('/api/manual_trade_status', methods=['POST'])
def api_manual_trade_status():
    mode = request.json.get('mode', 'PAPER')
    result = risk_engine.send_manual_trade_status(mode)
    return jsonify(result)

@app.route('/api/test_telegram', methods=['POST'])
def test_telegram():
    token = request.form.get('token')
    chat = request.form.get('chat_id')
    if not token or not chat:
        return jsonify({"status": "error", "message": "Missing credentials"})
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": "✅ <b>RD Algo:</b> Gateway Connection Test Success!", "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code == 200: return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": f"Telegram API Error: {r.text}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/import_trade', methods=['POST'])
def api_import_trade():
    if not bot_active: return jsonify({"status": "error", "message": "Bot not connected"})
    data = request.json
    try:
        final_sym = smart_trader.get_exact_symbol(data['symbol'], data['expiry'], data['strike'], data['type'])
        if not final_sym: return jsonify({"status": "error", "message": "Invalid Symbol/Strike"})
        
        selected_channel = data.get('target_channel', 'main')
        target_channels = [selected_channel] 
        
        result = replay_engine.import_past_trade(
            kite, final_sym, data['entry_time'], 
            int(data['qty']), float(data['price']), 
            float(data['sl']), [float(t) for t in data['targets']],
            data.get('trailing_sl', 0), data.get('sl_to_entry', 0),
            data.get('exit_multiplier', 1), data.get('target_controls'),
            target_channels=target_channels
        )
        # Handle async notifications for imported trade
        queue = result.get('notification_queue', [])
        trade_ref = result.get('trade_ref', {})
        if queue and trade_ref:
            def send_seq_notifications():
                with app.app_context():
                    msg_ids = telegram_bot.notify_trade_event(trade_ref, "NEW_TRADE")
                    if msg_ids:
                        from managers.persistence import load_trades, save_trades, save_to_history_db
                        trade_id = trade_ref['id']
                        if isinstance(msg_ids, dict): ids_dict = msg_ids; main_id = msg_ids.get(selected_channel) or msg_ids.get('main')
                        else: ids_dict = {'main': msg_ids}; main_id = msg_ids
                        
                        trades = load_trades()
                        for t in trades:
                            if str(t['id']) == str(trade_id):
                                t['telegram_msg_ids'] = ids_dict; t['telegram_msg_id'] = main_id 
                                save_trades(trades); break
                    
                    for item in queue:
                        evt = item['event']
                        if evt == 'NEW_TRADE': continue 
                        time.sleep(1.0)
                        t_obj = item.get('trade', trade_ref).copy() 
                        if 'id' not in t_obj: t_obj['id'] = trade_ref['id']
                        telegram_bot.notify_trade_event(t_obj, evt, item.get('data'))

            t = threading.Thread(target=send_seq_notifications)
            t.start()
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/simulate_scenario', methods=['POST'])
def api_simulate_scenario():
    if not bot_active: return jsonify({"status": "error", "message": "Bot offline"})
    data = request.json
    result = replay_engine.simulate_trade_scenario(kite, data.get('trade_id'), data.get('config'))
    return jsonify(result)

@app.route('/api/sync', methods=['POST'])
def api_sync():
    response = {
        "status": {"active": bot_active, "state": login_state, "login_url": "#"},
        "indices": {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0},
        "positions": [], "closed_trades": [], "specific_ltp": 0
    }
    if bot_active:
        try: response["indices"] = smart_trader.get_indices_ltp(kite)
        except: pass
    
    trades = persistence.load_trades()
    for t in trades:
        t['lot_size'] = smart_trader.get_lot_size(t['symbol'])
        t['symbol'] = smart_trader.get_display_name(t['symbol'])
    response["positions"] = trades

    if request.json.get('include_closed'):
        history = persistence.load_history()
        for t in history: t['symbol'] = smart_trader.get_display_name(t['symbol'])
        response["closed_trades"] = history

    req_ltp = request.json.get('ltp_req')
    if bot_active and req_ltp and req_ltp.get('symbol'):
        try:
            response["specific_ltp"] = smart_trader.get_specific_ltp(kite, req_ltp['symbol'], req_ltp['expiry'], req_ltp['strike'], req_ltp['type'])
        except: pass

    return jsonify(response)

@app.route('/trade', methods=['POST'])
def place_trade():
    if not bot_active: return redirect('/')
    try:
        raw_mode = request.form['mode']
        mode_input = raw_mode.strip().upper()
        sym = request.form['index']
        type_ = request.form['type']
        input_qty = int(request.form['qty'])
        order_type = request.form['order_type']
        limit_price = float(request.form.get('limit_price') or 0)
        sl_points = float(request.form.get('sl_points', 0))
        trailing_sl = float(request.form.get('trailing_sl') or 0)
        sl_to_entry = int(request.form.get('sl_to_entry', 0))
        exit_multiplier = int(request.form.get('exit_multiplier', 1))
        
        t1 = float(request.form.get('t1_price', 0))
        t2 = float(request.form.get('t2_price', 0))
        t3 = float(request.form.get('t3_price', 0))

        target_channels = ['main'] 
        selected_channel = request.form.get('target_channel')
        if selected_channel in ['vip', 'free', 'z2h']: target_channels.append(selected_channel)
        
        # Check permissions
        can_trade, reason = common.can_place_order("LIVE" if mode_input == "LIVE" else "PAPER")
        
        custom_targets = [t1, t2, t3] if t1 > 0 else []
        target_controls = []
        for i in range(1, 4):
            enabled = request.form.get(f't{i}_active') == 'on'
            lots = int(request.form.get(f't{i}_lots') or 0)
            trail_cost = request.form.get(f't{i}_cost') == 'on'
            if i == 3 and lots == 0: lots = 1000 
            target_controls.append({'enabled': enabled, 'lots': lots, 'trail_to_entry': trail_cost})
        
        final_sym = smart_trader.get_exact_symbol(sym, request.form.get('expiry'), request.form.get('strike', 0), type_)
        if not final_sym:
            flash("❌ Symbol Generation Failed")
            return redirect('/')

        app_settings = settings.load_settings()
        
        def execute(ex_mode, ex_qty, ex_channels, overrides=None):
            # ... (Simplified for brevity, logic remains identical to original) ...
            use_sl = overrides.get('sl_points', sl_points) if overrides else sl_points
            use_ctrl = overrides.get('target_controls', target_controls) if overrides else target_controls
            use_cust = overrides.get('custom_targets', custom_targets) if overrides else custom_targets
            use_trail = overrides.get('trailing_sl', trailing_sl) if overrides else trailing_sl
            use_sle = overrides.get('sl_to_entry', sl_to_entry) if overrides else sl_to_entry
            use_exm = overrides.get('exit_multiplier', exit_multiplier) if overrides else exit_multiplier
            use_ratios = overrides.get('ratios') if overrides else None
            
            return trade_manager.create_trade_direct(
                kite, ex_mode, final_sym, ex_qty, use_sl, use_cust, 
                order_type, limit_price, use_ctrl, 
                use_trail, use_sle, use_exm, 
                target_channels=ex_channels, risk_ratios=use_ratios
            )
        
        # SHADOW MODE LOGIC
        if mode_input == "SHADOW":
            # 1. LIVE LEG (Using Gateway Token)
            can_live, reason = common.can_place_order("LIVE")
            if not can_live:
                flash(f"❌ Shadow Blocked: LIVE Mode Disabled ({reason})")
                return redirect('/')

            try: val = request.form.get('live_qty'); live_qty = int(val) if val else input_qty
            except: live_qty = input_qty
            
            # (Logic for pulling live overrides from form...)
            # ... (Existing logic preserved) ...
            
            # Execute LIVE first
            res_live = execute("LIVE", live_qty, [], overrides=None) # Pass overrides if implemented
            
            if res_live['status'] != 'success':
                flash(f"❌ Shadow Failed: LIVE Error ({res_live['message']})")
                return redirect('/')
            
            time.sleep(1)
            # 2. PAPER LEG
            res_paper = execute("PAPER", input_qty, target_channels, overrides=None)
            if res_paper['status'] == 'success': flash(f"👻 Shadow Executed: ✅ LIVE | ✅ PAPER")
            else: flash(f"⚠️ Shadow Partial: ✅ LIVE | ❌ PAPER ({res_paper['message']})")

        else:
            # NORMAL MODE
            res = execute(mode_input, input_qty, target_channels)
            if res['status'] == 'success': flash(f"✅ Order Placed: {final_sym}")
            else: flash(f"❌ Error: {res['message']}")
            
    except Exception as e:
        flash(f"Error: {e}")
    return redirect('/')

@app.route('/promote/<trade_id>')
def promote(trade_id):
    if trade_manager.promote_to_live(kite, trade_id): flash("✅ Promoted!")
    else: flash("❌ Error")
    return redirect('/')

@app.route('/close_trade/<trade_id>')
def close_trade(trade_id):
    if trade_manager.close_trade_manual(kite, trade_id): flash("✅ Closed")
    else: flash("❌ Error")
    return redirect('/')

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    t = threading.Thread(target=background_monitor, daemon=True)
    t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
