import os
import json
import threading
import time
import gc 
import requests
from flask import Flask, render_template, request, redirect, flash, jsonify, url_for
from kiteconnect import KiteConnect
import config
from managers import zerodha_ticker

# --- IMPORTS ---
from managers import persistence, trade_manager, risk_engine, replay_engine, common, broker_ops
from managers.telegram_manager import bot as telegram_bot
from managers.orb_manager import ORBStrategyManager
import smart_trader
import settings
from database import db, AppSetting
import auto_login 

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config.from_object(config)

# Initialize Database
db.init_app(app)
with app.app_context():
    db.create_all()

kite = KiteConnect(api_key=config.API_KEY)

# --- GLOBAL STATE MANAGEMENT ---
bot_active = False
login_state = "IDLE" 
login_error_msg = None 
orb_bot = None  # Global instance for ORB Strategy

def run_auto_login_process():
    global bot_active, login_state, login_error_msg, orb_bot
    
    if not config.ZERODHA_USER_ID or not config.TOTP_SECRET:
        login_state = "FAILED"
        login_error_msg = "Missing Credentials in Config"
        return

    login_state = "WORKING"
    login_error_msg = None
    
    try:
        # Pass the kite instance to get the login URL
        token, error = auto_login.perform_auto_login(kite)
        gc.collect() # Cleanup memory after selenium usage
        
        if token:
            try:
                data = kite.generate_session(token, api_secret=config.API_SECRET)
                kite.set_access_token(data["access_token"])
                
                # --- Start WebSocket Ticker ---
                zerodha_ticker.initialize_ticker(config.API_KEY, data["access_token"])
                
                smart_trader.fetch_instruments(kite)
                
                bot_active = True
                login_state = "IDLE"
                gc.collect()

                # --- START ORB STRATEGY (Manual Init) ---
                if orb_bot is None:
                    orb_bot = ORBStrategyManager(kite)
                # NOTE: Auto-start removed as per user request. 
                # Bot waits for manual start from Strategy Page.
                # --------------------------------------
                
                # [NOTIFICATION] Success
                telegram_bot.notify_system_event("LOGIN_SUCCESS", "Auto-Login Successful. Session Renewed.")
                print("✅ Session Generated Successfully & Instruments Fetched")
                
            except Exception as e:
                # [NOTIFICATION] Session Gen Failure
                telegram_bot.notify_system_event("LOGIN_FAIL", f"Session Gen Failed: {str(e)}")
                
                if "Token is invalid" in str(e):
                    print("⚠️ Generated Token Expired or Invalid. Retrying...")
                    login_state = "FAILED" 
                else:
                    print(f"❌ Session Generation Error: {e}")
                    login_state = "FAILED"
                    login_error_msg = str(e)
        else:
            if bot_active:
                print("✅ Auto-Login: Handled via Callback Route. System Online.")
                login_state = "IDLE"
            else:
                # [NOTIFICATION] Auto-Login Failure
                telegram_bot.notify_system_event("LOGIN_FAIL", f"Auto-Login Failed: {error}")
                
                print(f"❌ Auto-Login Failed: {error}")
                login_state = "FAILED"
                login_error_msg = error
            
    except Exception as e:
        # [NOTIFICATION] Critical Error
        telegram_bot.notify_system_event("LOGIN_FAIL", f"Critical Login Error: {str(e)}")
        
        print(f"❌ Critical Session Error: {e}")
        login_state = "FAILED"
        login_error_msg = str(e)

def background_monitor():
    global bot_active, login_state
    
    with app.app_context():
        try:
            telegram_bot.notify_system_event("STARTUP", "Server Deployed & Monitor Started.")
            print("🖥️ Background Monitor Started")
        except Exception as e:
            print(f"❌ Startup Notification Failed: {e}")
    
    time.sleep(5) # Allow Flask to start up
    
    while True:
        with app.app_context():
            try:
                # 1. Active Bot Check
                if bot_active:
                    try:
                        # Skip Token Check if using Mock Broker
                        if not hasattr(kite, "mock_instruments"):
                            if not kite.access_token: 
                                raise Exception("No Access Token Found")

                        # Force a simple API call to validate the token 
                        try:
                            if not hasattr(kite, "mock_instruments"):
                                kite.profile() 
                        except Exception as e:
                            raise e 
                        
                        # Run Strategy Logic (Risk Engine)
                        risk_engine.update_risk_engine(kite)
                        
                    except Exception as e:
                        err = str(e)
                        if "Token is invalid" in err or "Network" in err or "No Access Token" in err or "access_token" in err:
                            print(f"⚠️ Connection Lost: {err}")
                            if bot_active:
                                telegram_bot.notify_system_event("OFFLINE", f"Connection Lost: {err}")
                            bot_active = False 
                        else:
                            print(f"⚠️ Risk Loop Warning: {err}")

                # 2. Reconnection Logic (Only if Bot is NOT active)
                if not bot_active:
                    # DETECT MOCK BROKER & BYPASS LOGIN
                    if hasattr(kite, "mock_instruments"):
                        print("⚠️ [MONITOR] Mock Broker Detected. Bypassing Auto-Login. System Online.")
                        bot_active = True
                        continue

                    if login_state == "IDLE":
                        print("🔄 Monitor: System Offline. Initiating Auto-Login...")
                        run_auto_login_process()
                    
                    elif login_state == "FAILED":
                        print("⚠️ Auto-Login previously failed. Retrying in 60s...")
                        time.sleep(60)
                        login_state = "IDLE" 
                        
            except Exception as e:
                print(f"❌ Monitor Loop Critical Error: {e}")
            finally:
                db.session.remove()
        
        time.sleep(0.5) 

@app.route('/')
def home():
    global bot_active, login_state
    if bot_active:
        trades = persistence.load_trades()
        for t in trades: 
            t['symbol'] = smart_trader.get_display_name(t['symbol'])
        active = [t for t in trades if t['status'] in ['OPEN', 'PROMOTED_LIVE', 'PENDING', 'MONITORING']]
        return render_template('dashboard.html', is_active=True, trades=active)
    
    return render_template('dashboard.html', is_active=False, state=login_state, error=login_error_msg, login_url=kite.login_url())

@app.route('/strategies')
def strategies():
    global bot_active, login_state
    # Reuses the login/active logic but renders the new Strategy Page
    if bot_active:
        return render_template('strategies.html', is_active=True)
    
    return render_template('strategies.html', is_active=False, state=login_state, error=login_error_msg, login_url=kite.login_url())

@app.route('/secure', methods=['GET', 'POST'])
def secure_login_page():
    if request.method == 'POST':
        if request.form.get('password') == config.ADMIN_PASSWORD:
            return redirect(kite.login_url())
        else:
            return render_template('secure_login.html', error="Invalid Password! Access Denied.")
    return render_template('secure_login.html')

@app.route('/api/status')
def api_status():
    return jsonify({"active": bot_active, "state": login_state, "login_url": kite.login_url()})

# --- ORB STRATEGY ROUTES ---

@app.route('/api/orb/params')
def api_orb_params():
    """Provides status and all advanced config from server"""
    # FIX: Initialize lot_size to 0, NOT 50.
    ls = 0 
    try:
        # 1. Attempt to fetch specific active lot size (Prioritize FUT)
        fetched_ls = smart_trader.fetch_active_lot_size(kite, "NIFTY")
        if fetched_ls > 0:
            ls = fetched_ls
        else:
            # 2. Fallback to generic details
            det = smart_trader.get_symbol_details(kite, "NIFTY")
            if det and det.get('lot_size'):
                ls = int(det['lot_size'])
    except: 
        ls = 0 # Remain 0 if fetch fails (e.g. Offline)
    
    active = False
    current_mode = "PAPER"
    current_direction = "BOTH"
    current_cutoff = "13:00"
    
    # 1. Default Leg Configuration
    legs_config = [
        {'active': True, 'lots': 1, 'full': False, 'ratio': 1.0, 'trail': True},
        {'active': True, 'lots': 1, 'full': False, 'ratio': 2.0, 'trail': False},
        {'active': False, 'lots': 1, 'full': True, 'ratio': 3.0, 'trail': False}
    ]
    
    # 2. Default Risk Configuration
    risk_settings = {
        'max_loss': 0, 
        'trail_pts': 0, 
        'sl_entry': 0,
        'p_active': 0, 
        'p_min': 0, 
        'p_trail': 0,
        'session_pnl': 0.0
    }
    
    re_sl = False
    re_sl_filter = "BOTH"
    re_opp = False
    
    if orb_bot:
        active = orb_bot.active
        
        # Populate Legs from Bot
        if hasattr(orb_bot, 'legs_config') and orb_bot.legs_config:
            legs_config = orb_bot.legs_config
            
        current_mode = orb_bot.mode
        current_direction = orb_bot.target_direction
        current_cutoff = orb_bot.cutoff_time.strftime("%H:%M")
        
        re_sl = orb_bot.reentry_same_sl
        re_sl_filter = orb_bot.reentry_same_filter
        re_opp = orb_bot.reentry_opposite
        
        # Populate Risk from Bot
        risk_settings = {
            'max_loss': getattr(orb_bot, 'max_daily_loss', 0),
            'trail_pts': getattr(orb_bot, 'trailing_sl_pts', 0),
            'sl_entry': getattr(orb_bot, 'sl_to_entry_mode', 0),
            'p_active': getattr(orb_bot, 'profit_active', 0),
            'p_min': getattr(orb_bot, 'profit_lock_min', 0),
            'p_trail': getattr(orb_bot, 'profit_trail_step', 0),
            'session_pnl': getattr(orb_bot, 'session_pnl', 0.0)
        }
    
    return jsonify({
        "active": active,
        "lot_size": ls,
        "current_mode": current_mode,
        "current_direction": current_direction,
        "current_cutoff": current_cutoff,
        "re_sl": re_sl,
        "re_sl_filter": re_sl_filter,
        "re_opp": re_opp,
        "legs_config": legs_config, # <-- Sent to Frontend
        "risk": risk_settings       # <-- Sent to Frontend
    })

@app.route('/api/orb/toggle', methods=['POST'])
def api_orb_toggle():
    global orb_bot
    action = request.json.get('action') 
    
    # Basic Params
    mode = request.json.get('mode', 'PAPER')
    direction = request.json.get('direction', 'BOTH')
    cutoff = request.json.get('cutoff', '13:00')
    
    # Re-entry Params
    re_sl = request.json.get('re_sl', False)
    re_sl_filter = request.json.get('re_sl_filter', 'BOTH')
    re_opp = request.json.get('re_opp', False)
    
    # Legs Configuration
    legs_config = request.json.get('legs_config')
    
    # Risk Configuration
    risk = request.json.get('risk', {})
    
    if not orb_bot:
        orb_bot = ORBStrategyManager(kite)
        
    if action == 'start':
        orb_bot.start(
            mode=mode, 
            direction=direction, 
            cutoff_str=cutoff,
            re_sl=re_sl,
            re_sl_side=re_sl_filter,
            re_opp=re_opp,
            legs_config=legs_config,
            # Pass Risk Parameters to Start
            max_loss=risk.get('max_loss', 0),
            trail_pts=risk.get('trail_pts', 0),
            sl_entry=risk.get('sl_entry', 0),
            p_active=risk.get('p_active', 0),
            p_min=risk.get('p_min', 0),
            p_trail=risk.get('p_trail', 0)
        )
        return jsonify({"status": "success", "message": f"✅ ORB Started ({mode})"})
    elif action == 'stop':
        orb_bot.stop()
        return jsonify({"status": "success", "message": "🛑 ORB Stopped"})
    
    return jsonify({"active": orb_bot.active})

@app.route('/api/orb/backtest', methods=['POST'])
def api_orb_backtest():
    """
    Updates ALL Bot parameters (General, Risk, Targets) from UI before running backtest.
    Ensures backtest simulation perfectly matches User Config.
    """
    global orb_bot
    data = request.json
    date = data.get('date')
    auto_exec = data.get('execute', False) 
    
    if not orb_bot:
        orb_bot = ORBStrategyManager(kite)
    
    if not date:
        return jsonify({"status": "error", "message": "Date is required"})
    
    # --- 1. APPLY GENERAL SETTINGS ---
    # Direction
    direction = data.get('direction')
    if direction:
        orb_bot.target_direction = str(direction).upper()
        
    # Cutoff Time
    cutoff = data.get('cutoff')
    if cutoff:
        try:
            from datetime import time as dt_time
            t_parts = cutoff.split(':')
            orb_bot.cutoff_time = dt_time(int(t_parts[0]), int(t_parts[1]))
        except: pass

    # --- 2. APPLY TARGET CONFIGURATION (LEGS) ---
    legs = data.get('legs_config')
    if legs:
        orb_bot.legs_config = legs
        
    # --- 3. APPLY RISK SETTINGS ---
    risk = data.get('risk')
    if risk:
        # Standard Trade Risk
        orb_bot.trailing_sl_pts = float(risk.get('trail_pts', 0))
        orb_bot.sl_to_entry_mode = int(risk.get('sl_entry', 0))
        orb_bot.max_daily_loss = abs(float(risk.get('max_loss', 0)))
        
        # Profit Locking (Less relevant for single-trade backtest but good for consistency)
        orb_bot.profit_active = float(risk.get('p_active', 0))
        orb_bot.profit_lock_min = float(risk.get('p_min', 0))
        orb_bot.profit_trail_step = float(risk.get('p_trail', 0))

    # --- 4. APPLY RE-ENTRY SETTINGS ---
    # (Updated for context, though backtest usually simulates single entry)
    orb_bot.reentry_same_sl = bool(data.get('re_sl', False))
    orb_bot.reentry_same_filter = str(data.get('re_sl_filter', 'BOTH')).upper()
    orb_bot.reentry_opposite = bool(data.get('re_opp', False))
        
    # --- RUN SIMULATION ---
    result = orb_bot.run_backtest(date, auto_execute=auto_exec)
    return jsonify(result)

# -------------------------------------

@app.route('/reset_connection')
def reset_connection():
    global bot_active, login_state
    
    # [NOTIFICATION] Manual Reset
    telegram_bot.notify_system_event("RESET", "Manual Connection Reset Initiated.")
    
    bot_active = False
    login_state = "IDLE"
    flash("🔄 Connection Reset. Login Monitor will retry.")
    return redirect('/')

@app.route('/callback')
def callback():
    global bot_active, orb_bot
    t = request.args.get("request_token")
    if t:
        try:
            data = kite.generate_session(t, api_secret=config.API_SECRET)
            kite.set_access_token(data["access_token"])
            
            # --- Start WebSocket Ticker ---
            zerodha_ticker.initialize_ticker(config.API_KEY, data["access_token"])

            bot_active = True
            smart_trader.fetch_instruments(kite)
            gc.collect()

            # --- START ORB STRATEGY (Manual Init) ---
            if orb_bot is None:
                orb_bot = ORBStrategyManager(kite)
            # Auto-start removed
            # --------------------------------------
            
            telegram_bot.notify_system_event("LOGIN_SUCCESS", "Manual Login (Callback) Successful.")
            flash("✅ System Online")
        except Exception as e:
            flash(f"Login Error: {e}")
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
        print(f"Error checking first trade: {e}")
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
        return jsonify({"status": "success", "message": "🚨 PANIC MODE EXECUTED. ALL TRADES CLOSED."})
    return jsonify({"status": "error", "message": "Failed to execute panic mode"})

# --- ROUTES FOR TELEGRAM REPORTS ---

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
    payload = {
        "chat_id": chat,
        "text": "✅ <b>RD Algo Terminal:</b> Test Message Received!\nConfiguration is valid.",
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=5)
        if r.status_code == 200:
            return jsonify({"status": "success"})
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
        
        queue = result.get('notification_queue', [])
        trade_ref = result.get('trade_ref', {})
        
        if queue and trade_ref:
            def send_seq_notifications():
                with app.app_context():
                    msg_ids = telegram_bot.notify_trade_event(trade_ref, "NEW_TRADE")
                    
                    if msg_ids:
                        from managers.persistence import load_trades, save_trades, save_to_history_db
                        trade_id = trade_ref['id']
                        updated_ref = False
                        
                        if isinstance(msg_ids, dict):
                            ids_dict = msg_ids
                            main_id = msg_ids.get(selected_channel) or msg_ids.get('main')
                        else:
                            ids_dict = {'main': msg_ids}
                            main_id = msg_ids
                        
                        trades = load_trades()
                        for t in trades:
                            if str(t['id']) == str(trade_id):
                                t['telegram_msg_ids'] = ids_dict
                                t['telegram_msg_id'] = main_id 
                                save_trades(trades)
                                updated_ref = True
                                break
                        
                        if not updated_ref:
                            trade_ref['telegram_msg_ids'] = ids_dict
                            trade_ref['telegram_msg_id'] = main_id
                            save_to_history_db(trade_ref)
                            
                        trade_ref['telegram_msg_ids'] = ids_dict
                        trade_ref['telegram_msg_id'] = main_id
                    
                    for item in queue:
                        evt = item['event']
                        if evt == 'NEW_TRADE': continue 
                        time.sleep(1.0)
                        dat = item.get('data')
                        t_obj = item.get('trade', trade_ref).copy() 
                        if 'id' not in t_obj: t_obj['id'] = trade_ref['id']
                        t_obj['telegram_msg_ids'] = trade_ref.get('telegram_msg_ids')
                        t_obj['telegram_msg_id'] = trade_ref.get('telegram_msg_id')
                        telegram_bot.notify_trade_event(t_obj, evt, dat)

            t = threading.Thread(target=send_seq_notifications)
            t.start()
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/simulate_scenario', methods=['POST'])
def api_simulate_scenario():
    if not bot_active: return jsonify({"status": "error", "message": "Bot offline"})
    data = request.json
    trade_id = data.get('trade_id')
    config = data.get('config')
    result = replay_engine.simulate_trade_scenario(kite, trade_id, config)
    return jsonify(result)

@app.route('/api/sync', methods=['POST'])
def api_sync():
    response = {
        "status": {
            "active": bot_active, 
            "state": login_state, 
            "login_url": kite.login_url()
        },
        "indices": {"NIFTY": 0, "BANKNIFTY": 0, "SENSEX": 0},
        "positions": [],
        "closed_trades": [],
        "specific_ltp": 0
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
        for t in history:
            t['symbol'] = smart_trader.get_display_name(t['symbol'])
        response["closed_trades"] = history

    req_ltp = request.json.get('ltp_req')
    if bot_active and req_ltp and req_ltp.get('symbol'):
        try:
            response["specific_ltp"] = smart_trader.get_specific_ltp(
                kite, req_ltp['symbol'], req_ltp['expiry'], req_ltp['strike'], req_ltp['type']
            )
        except: pass

    return jsonify(response)

@app.route('/close_trade/<trade_id>', methods=['POST'])
def close_trade(trade_id):
    if trade_manager.close_trade_manual(kite, trade_id):
        return jsonify({"status": "success", "message": "✅ Trade Closed/Cancelled"})
    else:
        return jsonify({"status": "error", "message": "❌ Failed to Close Trade"})

@app.route('/promote/<trade_id>', methods=['POST'])
def promote(trade_id):
    if trade_manager.promote_to_live(kite, trade_id):
        return jsonify({"status": "success", "message": "✅ Promoted to LIVE!"})
    else:
        return jsonify({"status": "error", "message": "❌ Promotion Failed"})

@app.route('/trade', methods=['POST'])
def place_trade():
    if not bot_active: 
        return jsonify({"status": "error", "message": "Bot not connected"})
    
    try:
        raw_mode = request.form['mode']
        print(f"\n[DEBUG MAIN] Received Trade Request. RAW Mode: '{raw_mode}'")
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
        if selected_channel in ['vip', 'free', 'z2h']:
            target_channels.append(selected_channel)
        
        final_sym = smart_trader.get_exact_symbol(sym, request.form.get('expiry'), request.form.get('strike', 0), type_)
        if not final_sym:
            return jsonify({"status": "error", "message": "Symbol Generation Failed"})

        app_settings = settings.load_settings()
        
        def execute(ex_mode, ex_qty, ex_channels, overrides=None):
            use_sl_points = sl_points
            use_target_controls = target_controls
            use_custom_targets = custom_targets
            use_ratios = None
            
            if overrides:
                use_trail = float(overrides.get('trailing_sl', trailing_sl))
                use_sl_entry = int(overrides.get('sl_to_entry', sl_to_entry))
                use_exit_mult = int(overrides.get('exit_multiplier', exit_multiplier))
                if 'sl_points' in overrides: use_sl_points = float(overrides['sl_points'])
                if 'target_controls' in overrides: use_target_controls = overrides['target_controls']
                if 'ratios' in overrides: use_ratios = overrides['ratios']
                if 'custom_targets' in overrides: use_custom_targets = overrides['custom_targets']
            else:
                use_trail = trailing_sl
                use_sl_entry = sl_to_entry
                use_exit_mult = exit_multiplier
            
            print(f"[DEBUG MAIN] Executing Helper: Mode={ex_mode}, Qty={ex_qty}")
            
            return trade_manager.create_trade_direct(
                kite, ex_mode, final_sym, ex_qty, use_sl_points, use_custom_targets, 
                order_type, limit_price, use_target_controls, 
                use_trail, use_sl_entry, use_exit_mult, 
                target_channels=ex_channels,
                risk_ratios=use_ratios
            )
        
        custom_targets = [t1, t2, t3] if t1 > 0 else []
        
        target_controls = []
        for i in range(1, 4):
            enabled = request.form.get(f't{i}_active') == 'on'
            lots = int(request.form.get(f't{i}_lots') or 0)
            trail_cost = request.form.get(f't{i}_cost') == 'on'
            if i == 3 and lots == 0: lots = 1000 
            target_controls.append({'enabled': enabled, 'lots': lots, 'trail_to_entry': trail_cost})

        target_mode_conf = "LIVE" if mode_input == "SHADOW" else mode_input
        mode_conf = app_settings['modes'].get(target_mode_conf, {})
        
        clean_sym = sym.split(':')[0].strip().upper()
        symbol_override = {}
        
        if 'symbol_sl' in mode_conf and clean_sym in mode_conf['symbol_sl']:
            s_data = mode_conf['symbol_sl'][clean_sym]
            if isinstance(s_data, (int, float)):
                symbol_override['sl_points'] = float(s_data)
            elif isinstance(s_data, dict):
                s_sl = float(s_data.get('sl', 0))
                if s_sl > 0:
                    symbol_override['sl_points'] = s_sl
                    t_points = s_data.get('targets', [])
                    if len(t_points) == 3:
                        new_ratios = [t / s_sl for t in t_points]
                        symbol_override['ratios'] = new_ratios
                        symbol_override['custom_targets'] = []

        if mode_input == "SHADOW":
            print("[DEBUG MAIN] Entering SHADOW Logic Block...")
            can_live, reason = common.can_place_order("LIVE")
            if not can_live:
                return jsonify({"status": "error", "message": f"Shadow Blocked: LIVE Mode is Disabled ({reason})"})

            try:
                val = request.form.get('live_qty')
                live_qty = int(val) if val else input_qty
            except: live_qty = input_qty

            live_controls = []
            for i in range(1, 4):
                enabled = request.form.get(f'live_t{i}_active') == 'on'
                try: lots = int(request.form.get(f'live_t{i}_lots'))
                except: lots = 0
                full = request.form.get(f'live_t{i}_full') == 'on'
                cost = request.form.get(f'live_t{i}_cost') == 'on'
                if full: lots = 1000
                live_controls.append({'enabled': enabled, 'lots': lots, 'trail_to_entry': cost})

            try: live_sl_points = float(request.form.get('live_sl_points'))
            except: live_sl_points = sl_points
            try: live_trail = float(request.form.get('live_trailing_sl'))
            except: live_trail = trailing_sl 
            try: live_entry_sl = int(request.form.get('live_sl_to_entry'))
            except: live_entry_sl = sl_to_entry
            try: live_exit_mult = int(request.form.get('live_exit_multiplier'))
            except: live_exit_mult = exit_multiplier

            try:
                lt1 = float(request.form.get('live_t1_price', 0))
                lt2 = float(request.form.get('live_t2_price', 0))
                lt3 = float(request.form.get('live_t3_price', 0))
                live_custom_targets = [lt1, lt2, lt3]
            except: live_custom_targets = custom_targets

            live_overrides = {
                'trailing_sl': live_trail,
                'sl_to_entry': live_entry_sl,
                'exit_multiplier': live_exit_mult,
                'sl_points': live_sl_points,
                'target_controls': live_controls,
                'custom_targets': live_custom_targets,
                'ratios': None 
            }
            
            print("[DEBUG MAIN] calling execute('LIVE')...")
            res_live = execute("LIVE", live_qty, [], overrides=live_overrides)
            
            if res_live['status'] != 'success':
                return jsonify({"status": "error", "message": f"Shadow Failed: LIVE Error ({res_live['message']})"})
            
            print("[DEBUG MAIN] calling execute('PAPER')...")
            res_paper = execute("PAPER", input_qty, target_channels, overrides=None)
            
            if res_paper['status'] == 'success':
                return jsonify({"status": "success", "message": "👻 Shadow Executed: ✅ LIVE | ✅ PAPER"})
            else:
                return jsonify({"status": "warning", "message": f"Shadow Partial: ✅ LIVE | ❌ PAPER ({res_paper['message']})"})

        else:
            print(f"[DEBUG MAIN] Entering STANDARD Logic (Mode: {mode_input})...")
            can_trade, reason = common.can_place_order(mode_input)
            if not can_trade:
                return jsonify({"status": "error", "message": f"Trade Blocked: {reason}"})
            
            std_overrides = symbol_override.copy() if symbol_override else None
            res = execute(mode_input, input_qty, target_channels, overrides=std_overrides)
            
            if res['status'] == 'success':
                return jsonify({"status": "success", "message": f"✅ Order Placed: {final_sym}"})
            else:
                return jsonify({"status": "error", "message": f"❌ Error: {res['message']}"})
            
    except Exception as e:
        print(f"[DEBUG MAIN] Exception: {e}")
        return jsonify({"status": "error", "message": str(e)})

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    t = threading.Thread(target=background_monitor, daemon=True)
    t.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=config.PORT, threaded=True)
