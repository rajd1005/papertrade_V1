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

# --- REFACTORED IMPORTS ---
from managers import persistence, trade_manager, risk_engine, replay_engine, common, broker_ops
from managers.telegram_manager import bot as telegram_bot
# --------------------------
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

def run_auto_login_process():
    global bot_active, login_state, login_error_msg
    
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
                
                # --- NEW: Start WebSocket Ticker ---
                zerodha_ticker.initialize_ticker(config.API_KEY, data["access_token"])
                # -----------------------------------

                smart_trader.fetch_instruments(kite)
                
                bot_active = True
                login_state = "IDLE"
                gc.collect()
                
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
    
    # [FIXED] Wrapped Startup Notification in App Context
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
                        # [FIX] Skip Token Check if using Mock Broker
                        if not hasattr(kite, "mock_instruments"):
                            if not kite.access_token: 
                                raise Exception("No Access Token Found")

                        # Force a simple API call to validate the token 
                        try:
                            # Mock Broker might not have profile(), so we skip this check for Mock
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
                    # [NEW] DETECT MOCK BROKER & BYPASS LOGIN
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
    global bot_active
    t = request.args.get("request_token")
    if t:
        try:
            data = kite.generate_session(t, api_secret=config.API_SECRET)
            kite.set_access_token(data["access_token"])
            
            # --- NEW: Start WebSocket Ticker (Critical Fix) ---
            zerodha_ticker.initialize_ticker(config.API_KEY, data["access_token"])
            # --------------------------------------------------

            bot_active = True
            smart_trader.fetch_instruments(kite)
            gc.collect()
            
            # [NOTIFICATION] Manual Login Success
            telegram_bot.notify_system_event("LOGIN_SUCCESS", "Manual Login (Callback) Successful.")
            
            flash("✅ System Online")
        except Exception as e:
            flash(f"Login Error: {e}")
    return redirect('/')

@app.route('/api/settings/load')
def api_settings_load():
    # Load base settings
    s = settings.load_settings()
    
    # --- FIXED: 1st Trade Logic using IST Timezone ---
    try:
        from managers.common import IST
        from datetime import datetime
        
        # Fetch current date in IST instead of server local time
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        
        # Load Trades & History to count today's trades
        trades = persistence.load_trades()
        history = persistence.load_history()
        
        count = 0
        # Check Active Trades
        if trades:
            for t in trades:
                if t.get('entry_time', '').startswith(today_str): 
                    count += 1
        
        # Check History
        if history:
            for t in history:
                if t.get('entry_time', '').startswith(today_str): 
                    count += 1
            
        s['is_first_trade'] = (count == 0)
    except Exception as e:
        print(f"Error checking first trade: {e}")
        # Default to False on error to prevent unwanted mode switching
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

# --- ROUTES FOR TELEGRAM REPORTS ---

@app.route('/api/manual_trade_report', methods=['POST'])
def api_manual_trade_report():
    """
    Triggered by the 'Speaker' icon on a closed trade card.
    Sends detailed stats of that specific trade to Telegram.
    """
    trade_id = request.json.get('trade_id')
    if not trade_id:
        return jsonify({"status": "error", "message": "Trade ID missing"})
    
    # Calls the function in risk_engine.py
    result = risk_engine.send_manual_trade_report(trade_id)
    return jsonify(result)

@app.route('/api/manual_summary', methods=['POST'])
def api_manual_summary():
    """
    Triggered by the 'Send Daily Summary' button in History tab.
    Sends the aggregate P/L, Wins/Loss report to Telegram.
    """
    mode = request.json.get('mode', 'PAPER')
    # Calls the function in risk_engine.py
    result = risk_engine.send_manual_summary(mode)
    return jsonify(result)

@app.route('/api/manual_trade_status', methods=['POST'])
def api_manual_trade_status():
    """
    Triggered by the 'Final Trade Status' button.
    Sends the detailed status list of all trades to Telegram.
    """
    mode = request.json.get('mode', 'PAPER')
    # Calls the function in risk_engine.py
    result = risk_engine.send_manual_trade_status(mode)
    return jsonify(result)

# -------------------------------------------------------------

# --- NEW TELEGRAM TEST ROUTE ---
@app.route('/api/test_telegram', methods=['POST'])
def test_telegram():
    token = request.form.get('token')
    chat = request.form.get('chat_id')
    if not token or not chat:
        return jsonify({"status": "error", "message": "Missing credentials"})
    
    # Direct test via Requests (bypassing stored settings to test new input)
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
        
        # --- NEW: Extract Channel Selection from Request ---
        # Default to 'main' if missing. Logic handles single selection.
        selected_channel = data.get('target_channel', 'main')
        
        # Construct target_channels list. 
        # 'main' is always primary for internal logic, but we want to broadcast to the selected one.
        # If user selected VIP, Free, or Z2H, we pass that along with 'main' logic (or replace it depending on telegram manager logic).
        # Typically, telegram_manager broadcasts to 'main' AND any others in the list.
        # If the requirement is "only one telegram channel", we pass that specific one.
        
        # However, to maintain system compatibility (where 'main' might be used for logging), 
        # we usually pass ['main', 'vip'] etc. 
        # BUT, if the user explicitly wants ONLY one channel (e.g. Free), we can pass just that if the backend supports it.
        # Assuming standard logic: Main is always required for system logs? 
        # Let's stick to the requested behavior: "only one telegram channel can be selected".
        
        target_channels = [selected_channel] 
        # Note: If 'main' was not selected, we might miss system logs if the bot logic relies on 'main' key.
        # To be safe, usually we send ['main', selected_channel] if selected != main. 
        # But if the user strictly wants ONE channel, we pass that list.
        
        # Call Replay Engine with channels
        result = replay_engine.import_past_trade(
            kite, final_sym, data['entry_time'], 
            int(data['qty']), float(data['price']), 
            float(data['sl']), [float(t) for t in data['targets']],
            data.get('trailing_sl', 0), data.get('sl_to_entry', 0),
            data.get('exit_multiplier', 1), data.get('target_controls'),
            target_channels=target_channels
        )
        
        # --- SEQUENTIAL TELEGRAM SENDER (UPDATED) ---
        queue = result.get('notification_queue', [])
        trade_ref = result.get('trade_ref', {})
        
        if queue and trade_ref:
            def send_seq_notifications():
                # Wrap thread in app_context to access DB
                with app.app_context():
                    # 1. Send Initial "NEW_TRADE" message -> Returns Dict of IDs
                    msg_ids = telegram_bot.notify_trade_event(trade_ref, "NEW_TRADE")
                    
                    if msg_ids:
                        from managers.persistence import load_trades, save_trades, save_to_history_db
                        
                        trade_id = trade_ref['id']
                        updated_ref = False
                        
                        # Handle Structure: If dict, save dict. If int (legacy), wrap it.
                        if isinstance(msg_ids, dict):
                            ids_dict = msg_ids
                            # If we sent to 'free', main_id might be None or the id for free channel
                            # We grab the ID corresponding to the selected channel as the "primary" reference
                            main_id = msg_ids.get(selected_channel) or msg_ids.get('main')
                        else:
                            ids_dict = {'main': msg_ids}
                            main_id = msg_ids
                        
                        # Try updating Active Trades
                        trades = load_trades()
                        for t in trades:
                            # Robust comparison: Convert both to strings
                            if str(t['id']) == str(trade_id):
                                t['telegram_msg_ids'] = ids_dict
                                t['telegram_msg_id'] = main_id # Legacy fallback
                                save_trades(trades)
                                updated_ref = True
                                break
                        
                        # If not active (e.g., trade closed immediately), update History
                        if not updated_ref:
                            trade_ref['telegram_msg_ids'] = ids_dict
                            trade_ref['telegram_msg_id'] = main_id
                            save_to_history_db(trade_ref)
                            
                        # Update local ref so subsequent events know where to reply
                        trade_ref['telegram_msg_ids'] = ids_dict
                        trade_ref['telegram_msg_id'] = main_id
                    
                    # 2. Process the rest of the queue
                    for item in queue:
                        evt = item['event']
                        if evt == 'NEW_TRADE': continue # Already sent
                        
                        # Small delay to ensure sequence order in Telegram
                        time.sleep(1.0)
                        
                        dat = item.get('data')
                        t_obj = item.get('trade', trade_ref).copy() 
                        
                        # --- CRITICAL FIX: INJECT ID IF MISSING ---
                        # The replay engine often creates snapshot objects without IDs.
                        if 'id' not in t_obj:
                            t_obj['id'] = trade_ref['id']
                        
                        # Inject IDs so manager knows where to reply for all channels
                        t_obj['telegram_msg_ids'] = trade_ref.get('telegram_msg_ids')
                        t_obj['telegram_msg_id'] = trade_ref.get('telegram_msg_id')
                        
                        telegram_bot.notify_trade_event(t_obj, evt, dat)

            # Start thread
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

# --- Aggregated Sync Route for High Performance ---
@app.route('/api/sync', methods=['POST'])
def api_sync():
    # 1. Base Data (Status & Indices)
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

    # 2. Fetch Indices (Only if active)
    if bot_active:
        try:
            response["indices"] = smart_trader.get_indices_ltp(kite)
        except: pass

    # 3. Active Positions
    trades = persistence.load_trades()
    for t in trades:
        t['lot_size'] = smart_trader.get_lot_size(t['symbol'])
        t['symbol'] = smart_trader.get_display_name(t['symbol'])
    response["positions"] = trades

    # 4. Closed Trades (Only if requested to save bandwidth)
    if request.json.get('include_closed'):
        history = persistence.load_history()
        for t in history:
            t['symbol'] = smart_trader.get_display_name(t['symbol'])
        response["closed_trades"] = history

    # 5. Specific LTP (For Trade Panel)
    req_ltp = request.json.get('ltp_req')
    if bot_active and req_ltp and req_ltp.get('symbol'):
        try:
            response["specific_ltp"] = smart_trader.get_specific_ltp(
                kite, 
                req_ltp['symbol'], 
                req_ltp['expiry'], 
                req_ltp['strike'], 
                req_ltp['type']
            )
        except: pass

    return jsonify(response)

@app.route('/trade', methods=['POST'])
def place_trade():
    if not bot_active: return redirect('/')
    try:
        # --- DEBUG LOG: INCOMING REQUEST ---
        raw_mode = request.form['mode']
        print(f"\n[DEBUG MAIN] Received Trade Request. RAW Mode: '{raw_mode}'")
        
        # --- FIX: Clean Mode Input ---
        mode_input = raw_mode.strip().upper()
        print(f"[DEBUG MAIN] Cleaned Mode: '{mode_input}'")
        
        sym = request.form['index']
        type_ = request.form['type']
        # mode_input defined above
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

        # --- TELEGRAM BROADCAST CHANNELS ---
        target_channels = ['main'] # Main is mandatory
        
        # [UPDATED] Single Selector Logic (Radio Buttons)
        selected_channel = request.form.get('target_channel')
        if selected_channel in ['vip', 'free', 'z2h']:
            target_channels.append(selected_channel)
        
        # --- PREPARE TRADE FUNCTION ARGS ---
        can_trade, reason = common.can_place_order("LIVE" if mode_input == "LIVE" else "PAPER")
        # Note: Shadow mode checks both inside execution block
        
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

        # --- EXECUTION LOGIC (SHADOW MODE IMPLEMENTATION) ---
        
        # Load settings to get multipliers
        app_settings = settings.load_settings()
        
        # Helper to execute trade with optional overrides
        def execute(ex_mode, ex_qty, ex_channels, overrides=None):
            # Default to form values
            use_sl_points = sl_points
            use_target_controls = target_controls
            use_custom_targets = custom_targets # Default to form prices
            use_ratios = None
            
            # Apply Overrides if provided (from Global Settings)
            if overrides:
                use_trail = float(overrides.get('trailing_sl', trailing_sl))
                use_sl_entry = int(overrides.get('sl_to_entry', sl_to_entry))
                use_exit_mult = int(overrides.get('exit_multiplier', exit_multiplier))
                
                # New: Symbol SL Override
                if 'sl_points' in overrides: use_sl_points = float(overrides['sl_points'])
                
                # New: Target Controls Override
                if 'target_controls' in overrides: use_target_controls = overrides['target_controls']
                
                # New: Ratios Override
                if 'ratios' in overrides: use_ratios = overrides['ratios']
                
                # New: Custom Targets Override (Important to clear form prices)
                if 'custom_targets' in overrides: use_custom_targets = overrides['custom_targets']
            else:
                use_trail = trailing_sl
                use_sl_entry = sl_to_entry
                use_exit_mult = exit_multiplier
            
            print(f"[DEBUG MAIN] Executing Helper: Mode={ex_mode}, Qty={ex_qty}, Trail={use_trail}, Mult={use_exit_mult}")
            
            return trade_manager.create_trade_direct(
                kite, ex_mode, final_sym, ex_qty, use_sl_points, use_custom_targets, 
                order_type, limit_price, use_target_controls, 
                use_trail, use_sl_entry, use_exit_mult, 
                target_channels=ex_channels,
                risk_ratios=use_ratios
            )
        
        # --- PREPARE OVERRIDES (Check for Symbol Specific Settings) ---
        # Determine which mode config to check for symbol settings
        # Shadow uses LIVE settings by default here for Leg 1 logic
        target_mode_conf = "LIVE" if mode_input == "SHADOW" else mode_input
        mode_conf = app_settings['modes'].get(target_mode_conf, {})
        
        clean_sym = sym.split(':')[0].strip().upper()
        symbol_override = {}
        
        if 'symbol_sl' in mode_conf and clean_sym in mode_conf['symbol_sl']:
            s_data = mode_conf['symbol_sl'][clean_sym]
            
            # Handle Legacy (int/float) vs New (dict)
            if isinstance(s_data, (int, float)):
                symbol_override['sl_points'] = float(s_data)
            elif isinstance(s_data, dict):
                # Extract SL
                s_sl = float(s_data.get('sl', 0))
                if s_sl > 0:
                    symbol_override['sl_points'] = s_sl
                    
                    # Extract Targets (Points) -> Convert to Ratios
                    t_points = s_data.get('targets', [])
                    if len(t_points) == 3:
                        # Ratios = TargetPoint / SLPoint
                        new_ratios = [t / s_sl for t in t_points]
                        symbol_override['ratios'] = new_ratios
                        
                        # Force empty custom targets so ratios are used
                        symbol_override['custom_targets'] = []
                        
                        print(f"[DEBUG] Symbol Override for {clean_sym}: SL={s_sl}, Ratios={new_ratios}")

        if mode_input == "SHADOW":
            print("[DEBUG MAIN] Entering SHADOW Logic Block...")
            
            # 1. Check Live Feasibility
            can_live, reason = common.can_place_order("LIVE")
            if not can_live:
                flash(f"❌ Shadow Blocked: LIVE Mode is Disabled/Blocked ({reason})")
                return redirect('/')

            # ==========================================
            # LEG 1: EXECUTE LIVE (Uses Specific "Live" Form Inputs)
            # ==========================================
            
            # 1. Quantity (Use form input or fallback)
            try:
                val = request.form.get('live_qty')
                live_qty = int(val) if val else input_qty
            except (ValueError, TypeError):
                live_qty = input_qty

            # 2. Target Controls (Live)
            live_controls = []
            for i in range(1, 4):
                enabled = request.form.get(f'live_t{i}_active') == 'on'
                try:
                    lots = int(request.form.get(f'live_t{i}_lots'))
                except:
                    lots = 0
                full = request.form.get(f'live_t{i}_full') == 'on'
                cost = request.form.get(f'live_t{i}_cost') == 'on'
                
                if full: lots = 1000
                
                live_controls.append({
                    'enabled': enabled,
                    'lots': lots,
                    'trail_to_entry': cost
                })

            # 3. Parameters (Try fetching live inputs, fallback to base inputs)
            try:
                live_sl_points = float(request.form.get('live_sl_points'))
            except:
                live_sl_points = sl_points

            try:
                live_trail = float(request.form.get('live_trailing_sl'))
            except:
                live_trail = trailing_sl 

            try:
                live_entry_sl = int(request.form.get('live_sl_to_entry'))
            except:
                live_entry_sl = sl_to_entry

            try:
                live_exit_mult = int(request.form.get('live_exit_multiplier'))
            except:
                live_exit_mult = exit_multiplier

            # 4. Custom Targets (Live Prices)
            try:
                lt1 = float(request.form.get('live_t1_price', 0))
                lt2 = float(request.form.get('live_t2_price', 0))
                lt3 = float(request.form.get('live_t3_price', 0))
                live_custom_targets = [lt1, lt2, lt3]
            except:
                live_custom_targets = custom_targets

            # Construct Overrides
            live_overrides = {
                'trailing_sl': live_trail,
                'sl_to_entry': live_entry_sl,
                'exit_multiplier': live_exit_mult,
                'sl_points': live_sl_points,
                'target_controls': live_controls,
                'custom_targets': live_custom_targets,
                'ratios': None 
            }
            
            # Execute LIVE (Silent)
            print("[DEBUG MAIN] calling execute('LIVE') with Form Overrides...")
            res_live = execute("LIVE", live_qty, [], overrides=live_overrides)
            
            if res_live['status'] != 'success':
                flash(f"❌ Shadow Failed: LIVE Execution Error ({res_live['message']})")
                return redirect('/')
            
            # 3. Wait for DB Safety (1s to ensure ID separation)
            print("[DEBUG MAIN] LIVE Success. Waiting 1s...")
            time.sleep(1)
            
            # ==========================================
            # LEG 2: EXECUTE PAPER (Uses Standard Paper Form Inputs)
            # ==========================================
            paper_qty = input_qty
            
            # Execute PAPER (Broadcast with Main Form Settings)
            print("[DEBUG MAIN] calling execute('PAPER') with Main Form Inputs...")
            res_paper = execute("PAPER", paper_qty, target_channels, overrides=None)
            
            if res_paper['status'] == 'success':
                flash(f"👻 Shadow Executed: ✅ LIVE | ✅ PAPER")
            else:
                flash(f"⚠️ Shadow Partial: ✅ LIVE | ❌ PAPER Failed ({res_paper['message']})")

        else:
            # Standard Execution (PAPER or LIVE)
            print(f"[DEBUG MAIN] Entering STANDARD Logic Block (Mode: {mode_input})...")
            
            can_trade, reason = common.can_place_order(mode_input)
            if not can_trade:
                flash(f"⛔ Trade Blocked: {reason}")
                return redirect('/')
            
            # Calculate Qty based on mode multiplier
            final_qty = input_qty
            
            # Use Symbol Overrides if available
            std_overrides = None
            if symbol_override:
                std_overrides = symbol_override.copy()
            
            res = execute(mode_input, final_qty, target_channels, overrides=std_overrides)
            
            if res['status'] == 'success':
                flash(f"✅ Order Placed: {final_sym}")
            else:
                flash(f"❌ Error: {res['message']}")
            
    except Exception as e:
        print(f"[DEBUG MAIN] Exception: {e}")
        flash(f"Error: {e}")
    return redirect('/')

@app.route('/promote/<trade_id>')
def promote(trade_id):
    if trade_manager.promote_to_live(kite, trade_id):
        flash("✅ Promoted!")
    else:
        flash("❌ Error")
    return redirect('/')

@app.route('/close_trade/<trade_id>')
def close_trade(trade_id):
    if trade_manager.close_trade_manual(kite, trade_id):
        flash("✅ Closed")
    else:
        flash("❌ Error")
    return redirect('/')

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    t = threading.Thread(target=background_monitor, daemon=True)
    t.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=config.PORT, threaded=True)
