import requests
import json
import time
import threading
import queue
import sys
import settings
import smart_trader
from managers.common import get_time_str
from database import db, TelegramMessage, ActiveTrade

class TelegramManager:
    def __init__(self):
        self.base_url = "https://api.telegram.org/bot"
        # Initialize Async Queue and Worker
        self.msg_queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def _get_config(self):
        s = settings.load_settings()
        return s.get('telegram', {})

    def _get_flask_app(self):
        """
        Helper to locate the Flask app instance to establish a context 
        for database operations within the background thread.
        """
        # 1. Try finding 'app' in __main__ (Run as script)
        if '__main__' in sys.modules and hasattr(sys.modules['__main__'], 'app'):
            return sys.modules['__main__'].app
        
        # 2. Try importing 'main' (Run as module/gunicorn)
        try:
            import main
            if hasattr(main, 'app'):
                return main.app
        except ImportError:
            pass
        
        return None

    def _worker(self):
        """
        Background worker that consumes messages from the queue and sends them.
        """
        while True:
            try:
                task = self.msg_queue.get()
                # Unpack: function, args, kwargs, callback_data
                func, args, kwargs, cb_data = task
                
                # Execute Synchronously (in background thread)
                msg_id = func(*args, **kwargs)
                
                # Handle Callback (Save to DB) if successful
                if msg_id and cb_data:
                    self._handle_callback(msg_id, cb_data)
                    
            except Exception as e:
                print(f"❌ Telegram Worker Error: {e}")
            finally:
                self.msg_queue.task_done()

    def _handle_callback(self, msg_id, data):
        """
        Updates the database with the sent message ID safely using a fresh app context.
        """
        app = self._get_flask_app()
        if not app:
            print("⚠️ Telegram Worker: Could not find Flask App context for DB save.")
            return

        with app.app_context():
            try:
                trade_id = data.get('trade_id')
                chat_id = data.get('chat_id')
                key = data.get('key')
                
                # 1. Save to TelegramMessage Table
                self._save_msg_to_db(trade_id, msg_id, chat_id)
                
                # 2. Update ActiveTrade JSON (Best effort sync)
                if key and trade_id:
                    trade_row = ActiveTrade.query.get(trade_id)
                    if trade_row:
                        t_data = json.loads(trade_row.data)
                        if 'telegram_msg_ids' not in t_data: 
                            t_data['telegram_msg_ids'] = {}
                        
                        t_data['telegram_msg_ids'][key] = msg_id
                        # Legacy fallback for single channel support
                        if key == 'main': 
                            t_data['telegram_msg_id'] = msg_id
                        
                        trade_row.data = json.dumps(t_data)
                        db.session.commit()
            except Exception as e:
                print(f"⚠️ Telegram DB Callback Error: {e}")
                db.session.rollback()

    def _format_msg(self, template_key, trade, extra_data=None, action_time=None):
        """
        Helper: Formats message strings based on settings.py templates.
        """
        conf = self._get_config()
        templates = conf.get('templates', {})
        raw_tpl = templates.get(template_key, "")
        
        if not raw_tpl: return None

        raw_symbol = trade.get('symbol', 'Unknown')
        entry_price = float(trade.get('entry_price', 0) or 0)
        qty = float(trade.get('quantity', 0) or 0)
        curr_time = action_time or get_time_str()
        entry_time_str = trade.get('entry_time', curr_time)

        data = {
            "symbol": smart_trader.get_telegram_symbol(raw_symbol),
            "raw_symbol": raw_symbol,
            "mode": trade.get('mode', 'PAPER'),
            "order_type": trade.get('order_type', 'MARKET'),
            "qty": qty,
            "entry": entry_price,
            "sl": trade.get('sl', 0),
            "targets": str(trade.get('targets', [])),
            "time": curr_time,
            "entry_time": entry_time_str,
            "icon": "🔴" if trade.get('mode') == "LIVE" else "🟡"
        }

        if template_key == "ACTIVE":
            data["price"] = extra_data['price'] if isinstance(extra_data, dict) else extra_data
        elif template_key == "UPDATE":
            data["update_text"] = extra_data if extra_data else f"SL: {trade.get('sl')}, Trail: {trade.get('trailing_sl')}"
        elif template_key == "SL_HIT":
            val = extra_data.get('pnl') if isinstance(extra_data, dict) else extra_data
            data["pnl"] = f"{float(val or 0):.2f}"
            data["exit_price"] = trade.get('exit_price', 0)
        elif template_key == "TARGET_HIT":
            t_data = extra_data if isinstance(extra_data, dict) else {}
            data["t_num"] = t_data.get('t_num', '?')
            data["price"] = t_data.get('price', 0)
            try: data["pot_pnl"] = f"{(float(data['price']) - entry_price) * qty:.2f}"
            except: data["pot_pnl"] = "0.00"
        elif template_key == "HIGH_MADE":
            h_price = extra_data.get('price') if isinstance(extra_data, dict) else extra_data
            data["price"] = h_price
            try: data["pot_pnl"] = f"{(float(h_price) - entry_price) * qty:.2f}"
            except: data["pot_pnl"] = "0.00"
        elif template_key == "EXIT":
             data["reason"] = extra_data.get('reason', 'Manual') if isinstance(extra_data, dict) else 'Manual'
             data["exit_price"] = extra_data.get('exit_price', 0) if isinstance(extra_data, dict) else 0
             data["pnl"] = f"{extra_data.get('pnl', 0):.2f}" if isinstance(extra_data, dict) else "0.00"

        try:
            return raw_tpl.format(**data)
        except Exception as e:
            print(f"Template Error ({template_key}): {e}")
            return f"Template Error: {template_key}"

    def _send_raw_sync(self, text, chat_id, reply_to_id=None):
        """
        Internal synchronous method to execute the network request.
        Running inside the worker thread.
        """
        if not chat_id: return None
        conf = self._get_config()
        token = conf.get('bot_token')
        if not token: return None

        url = f"{self.base_url}{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_to_id:
            payload["reply_to_message_id"] = reply_to_id

        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                return resp.json().get('result', {}).get('message_id')
            else:
                print(f"❌ Telegram Error (Chat {chat_id}): {resp.text}")
        except Exception as e:
            print(f"❌ Telegram Request Failed: {e}")
        return None

    def send_message(self, text, reply_to_id=None, override_chat_id=None):
        """
        Public API: Enqueues a message to be sent asynchronously.
        Non-blocking.
        """
        conf = self._get_config()
        if not conf.get('enable_notifications', False):
            return None
        
        chat_id = override_chat_id if override_chat_id else conf.get('channel_id')
        
        # Enqueue the task
        self.msg_queue.put((
            self._send_raw_sync,       # Function
            [text, chat_id, reply_to_id], # Args
            {},                        # Kwargs
            None                       # No Callback needed for generic messages
        ))

    def notify_system_event(self, event_type, message=""):
        conf = self._get_config()
        sys_channel_id = conf.get('system_channel_id')
        icons = {
            "STARTUP": "🖥️",
            "ONLINE": "🟢",
            "OFFLINE": "🔴",
            "LOGIN_SUCCESS": "✅",
            "LOGIN_FAIL": "⚠️",
            "RESET": "🔄"
        }
        icon = icons.get(event_type, "ℹ️")
        text = f"{icon} <b>SYSTEM ALERT: {event_type}</b>\n{message}\nTime: {get_time_str()}"
        
        self.send_message(text, override_chat_id=sys_channel_id)

    def notify_trade_event(self, trade, event_type, extra_data=None):
        """
        Constructs and enqueues notifications for ALL configured channels.
        Returns empty dict because sending is now asynchronous.
        """
        conf = self._get_config()
        if not conf.get('enable_notifications', False):
            return {}

        toggles = conf.get('event_toggles', {})
        if not toggles.get(event_type, True): 
            return {}

        stored_ids = trade.get('telegram_msg_ids', {})
        if not stored_ids and 'telegram_msg_id' in trade:
            stored_ids['main'] = trade['telegram_msg_id']

        action_time = get_time_str() 
        if isinstance(extra_data, dict) and 'time' in extra_data:
            action_time = extra_data['time']
        elif event_type == "NEW_TRADE" and trade.get('entry_time'):
            action_time = trade.get('entry_time')

        all_channels = [
            {'key': 'main', 'id': conf.get('channel_id')},
            {'key': 'vip', 'id': conf.get('vip_channel_id')},
            {'key': 'free', 'id': conf.get('free_channel_id')},
            {'key': 'z2h', 'id': conf.get('z2h_channel_id'), 'custom_name': conf.get('z2h_channel_name')}
        ]
        
        early_events = ['NEW_TRADE', 'ACTIVE', 'UPDATE']
        result_events = ['TARGET_HIT', 'HIGH_MADE', 'SL_HIT']
        target_list = trade.get('target_channels') 

        for ch in all_channels:
            key = ch['key']
            chat_id = ch['id']
            if not chat_id: continue 
            
            # --- FILTER LOGIC ---
            is_allowed = True
            if target_list is not None:
                if key in target_list:
                    is_allowed = True
                else:
                    is_premium_trade = any(x in target_list for x in ['vip', 'z2h'])
                    if key == 'free' and is_premium_trade and event_type in result_events:
                        is_allowed = True
                    else:
                        is_allowed = False
            
            if not is_allowed: continue

            if key in ['vip', 'z2h'] and event_type not in early_events: continue
            elif key == 'free' and event_type not in ['NEW_TRADE', 'ACTIVE', 'UPDATE', 'TARGET_HIT', 'HIGH_MADE']: continue
            
            # --- THREAD LOGIC ---
            reply_to = stored_ids.get(key)
            is_new_thread_start = False

            if event_type == "NEW_TRADE":
                reply_to = None 

            if key == 'free' and not reply_to:
                is_new_thread_start = True

            if event_type != "NEW_TRADE" and not reply_to and not is_new_thread_start:
                continue

            # --- MESSAGE FORMATTING ---
            msg = self._format_msg(event_type, trade, extra_data, action_time)
            if not msg: continue

            if event_type == "NEW_TRADE" and ch.get('custom_name'):
                msg = f"🚀 <b>[{ch['custom_name']}]</b>\n" + msg

            if is_new_thread_start and key == 'free' and event_type != "NEW_TRADE":
                header = self._format_msg("FREE_HEADER", trade, extra_data, action_time)
                if header:
                    msg = header + msg

            # --- ENQUEUE WITH CALLBACK ---
            callback_data = {
                "trade_id": trade.get('id'),
                "key": key,
                "chat_id": chat_id
            }
            
            self.msg_queue.put((
                self._send_raw_sync,
                [msg, chat_id, reply_to],
                {},
                callback_data
            ))

        # Returns empty because IDs are updated asynchronously via callback
        return {}

    def _save_msg_to_db(self, trade_id, msg_id, chat_id):
        """Helper to safely save message ID to database table"""
        if not trade_id or not msg_id or not chat_id:
            return
            
        try:
            rec = TelegramMessage(trade_id=str(trade_id), message_id=msg_id, chat_id=str(chat_id))
            db.session.add(rec)
            db.session.commit()
        except Exception as e:
            # print(f"⚠️ Failed to save Telegram Msg ID: {e}")
            pass

    def delete_trade_messages(self, trade_id):
        """
        Deletes messages associated with a trade.
        Uses a separate thread for logic to avoid blocking, reusing the original robust pattern.
        """
        try:
            # Fetch messages
            messages = TelegramMessage.query.filter_by(trade_id=str(trade_id)).all()
            if not messages: return

            msg_data_list = [{"chat_id": m.chat_id, "message_id": m.message_id} for m in messages]

            for msg in messages:
                db.session.delete(msg)
            
            db.session.commit()
            print(f"🗑️ Deleted {len(messages)} Telegram messages from DB for Trade {trade_id}")

            def bg_cleanup(token, items):
                delete_url = f"{self.base_url}{token}/deleteMessage"
                for item in items:
                    try:
                        requests.post(delete_url, json=item, timeout=5)
                        time.sleep(0.1) 
                    except Exception as req_err:
                        print(f"BG Delete Request Error: {req_err}")

            conf = self._get_config()
            token = conf.get('bot_token')
            
            if token and msg_data_list:
                t = threading.Thread(target=bg_cleanup, args=(token, msg_data_list))
                t.daemon = True 
                t.start()

        except Exception as e:
            print(f"❌ Error deleting Telegram messages: {e}")
            try: db.session.rollback()
            except: pass

# Singleton Instance
bot = TelegramManager()
