import requests
import json
import time
import threading
import settings
import smart_trader
from managers.common import get_time_str
from database import db, TelegramMessage

class TelegramManager:
    def __init__(self):
        self.base_url = "https://api.telegram.org/bot"

    def _get_config(self):
        s = settings.load_settings()
        return s.get('telegram', {})

    def _format_msg(self, template_key, trade, extra_data=None, action_time=None):
        """
        Helper: Formats message strings based on settings.py templates.
        Supports GLOBAL Placeholders for all messages.
        """
        conf = self._get_config()
        templates = conf.get('templates', {})
        raw_tpl = templates.get(template_key, "")
        
        if not raw_tpl: return None

        # --- GLOBAL PLACEHOLDERS (Available in ALL templates) ---
        raw_symbol = trade.get('symbol', 'Unknown')
        entry_price = float(trade.get('entry_price', 0) or 0)
        qty = float(trade.get('quantity', 0) or 0)
        curr_time = action_time or get_time_str()
        
        # Determine Entry Time (Use trade entry time if avail, else current)
        entry_time_str = trade.get('entry_time', curr_time)

        data = {
            # Basic Trade Info
            "symbol": smart_trader.get_telegram_symbol(raw_symbol),
            "raw_symbol": raw_symbol,
            "mode": trade.get('mode', 'PAPER'),
            "order_type": trade.get('order_type', 'MARKET'),
            "qty": qty,
            "entry": entry_price,
            "sl": trade.get('sl', 0),
            "targets": str(trade.get('targets', [])),
            
            # Time Info
            "time": curr_time,
            "entry_time": entry_time_str,
            
            # Aesthetics
            "icon": "🔴" if trade.get('mode') == "LIVE" else "🟡"
        }

        # --- CONTEXT SPECIFIC DATA ---
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
            try:
                data["pot_pnl"] = f"{(float(data['price']) - entry_price) * qty:.2f}"
            except: 
                data["pot_pnl"] = "0.00"

        elif template_key == "HIGH_MADE":
            h_price = extra_data.get('price') if isinstance(extra_data, dict) else extra_data
            data["price"] = h_price
            try:
                data["pot_pnl"] = f"{(float(h_price) - entry_price) * qty:.2f}"
            except:
                data["pot_pnl"] = "0.00"
            
        elif template_key == "EXIT":
             data["reason"] = extra_data.get('reason', 'Manual') if isinstance(extra_data, dict) else 'Manual'
             data["exit_price"] = extra_data.get('exit_price', 0) if isinstance(extra_data, dict) else 0
             data["pnl"] = f"{extra_data.get('pnl', 0):.2f}" if isinstance(extra_data, dict) else "0.00"

        # Perform Replacement safely
        try:
            return raw_tpl.format(**data)
        except Exception as e:
            print(f"Template Error ({template_key}): {e}")
            return f"Template Error: {template_key}"

    def send_message(self, text, reply_to_id=None, override_chat_id=None):
        """
        Sends a message to the configured Telegram Channel.
        Allows overriding the chat_id for specific alerts (like System Alerts).
        Returns the Message ID of the sent message.
        """
        conf = self._get_config()
        if not conf.get('enable_notifications', False):
            return None
        
        token = conf.get('bot_token')
        
        # Use the specific channel if provided, otherwise fallback to the default trade channel
        chat_id = override_chat_id if override_chat_id else conf.get('channel_id')

        if not token or not chat_id:
            return None

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

    def notify_system_event(self, event_type, message=""):
        """
        Sends system status alerts (Online, Offline, Login Success/Fail).
        Uses 'system_channel_id' if set, otherwise uses the default 'channel_id'.
        """
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
        
        # Format the message
        text = f"{icon} <b>SYSTEM ALERT: {event_type}</b>\n{message}\nTime: {get_time_str()}"
        
        # Send immediately using the system channel (if configured) or default
        self.send_message(text, override_chat_id=sys_channel_id)

    def notify_trade_event(self, trade, event_type, extra_data=None):
        """
        Constructs and sends notifications to ALL configured channels based on rules.
        """
        conf = self._get_config()
        if not conf.get('enable_notifications', False):
            return {}

        # --- NEW: Check Individual Event Toggle ---
        toggles = conf.get('event_toggles', {})
        # If key is missing, default to True (Safe Default)
        if not toggles.get(event_type, True): 
            return {}
        # ------------------------------------------

        raw_symbol = trade.get('symbol', 'Unknown')
        symbol = smart_trader.get_telegram_symbol(raw_symbol)
        
        # --- THREAD IDS ---
        if 'telegram_msg_ids' not in trade or not isinstance(trade['telegram_msg_ids'], dict):
            trade['telegram_msg_ids'] = {}
            legacy_id = trade.get('telegram_msg_id')
            if legacy_id:
                trade['telegram_msg_ids']['main'] = legacy_id
        
        stored_ids = trade['telegram_msg_ids']

        # --- DETERMINE ACTION TIME ---
        action_time = get_time_str() 
        if isinstance(extra_data, dict) and 'time' in extra_data:
            action_time = extra_data['time']
        elif event_type == "NEW_TRADE" and trade.get('entry_time'):
            action_time = trade.get('entry_time')
            
        # Entry time for Header
        entry_time_str = trade.get('entry_time', action_time)

        # --- DEFINE CHANNELS ---
        all_channels = [
            {'key': 'main', 'id': conf.get('channel_id'), 'allow_all': True},
            {'key': 'vip', 'id': conf.get('vip_channel_id'), 'allow_all': False},
            {'key': 'free', 'id': conf.get('free_channel_id'), 'allow_all': False},
            {'key': 'z2h', 'id': conf.get('z2h_channel_id'), 'allow_all': False, 'custom_name': conf.get('z2h_channel_name')}
        ]
        
        # Event Categories
        early_events = ['NEW_TRADE', 'ACTIVE', 'UPDATE']
        result_events = ['TARGET_HIT', 'HIGH_MADE', 'SL_HIT']
        
        # User Selection (e.g., ['vip'])
        target_list = trade.get('target_channels') 

        new_msg_ids = {} 

        # Loop through channels
        for ch in all_channels:
            key = ch['key']
            chat_id = ch['id']
            if not chat_id: continue 
            
            # --- FILTER 1: User Selection & Premium Spillover ---
            is_allowed = True
            if target_list is not None:
                if key in target_list:
                    # User explicitly selected this channel
                    is_allowed = True
                else:
                    # User did NOT select this channel.
                    # CHECK SPILLOVER: If this is 'free' channel, and the trade is VIP/Z2H,
                    # we ALLOW Result Events.
                    is_premium_trade = any(x in target_list for x in ['vip', 'z2h'])
                    if key == 'free' and is_premium_trade and event_type in result_events:
                        is_allowed = True
                    else:
                        is_allowed = False
            
            if not is_allowed:
                continue

            # --- FILTER 2: Event Type Segmentation ---
            
            # VIP & Z2H: STRICTLY Only Early Events
            if key in ['vip', 'z2h']:
                allowed_vip = ['NEW_TRADE', 'ACTIVE', 'UPDATE']
                if event_type not in allowed_vip:
                    continue
            
            # Free: Only allow Results (unless it was explicitly selected for Entry)
            elif key == 'free':
                # Double check specific allowed events to avoid noise
                allowed_free = ['NEW_TRADE', 'ACTIVE', 'UPDATE', 'TARGET_HIT', 'HIGH_MADE']
                if event_type not in allowed_free:
                    continue
            
            # --- THREAD MANAGEMENT ---
            reply_to = stored_ids.get(key)
            is_new_thread_start = False

            # NEW_TRADE always starts a new thread
            if event_type == "NEW_TRADE":
                reply_to = None 

            # Special Logic for FREE Channel (Lazy Threading):
            # If we are sending a message (e.g. TARGET_HIT) but have no Thread ID yet,
            # this means we skipped the Entry (Spillover mode).
            # So this message becomes the Header/Parent.
            if key == 'free' and not reply_to:
                is_new_thread_start = True

            # If it's a reply event (not NEW_TRADE) but we don't have a thread ID 
            # AND it's not the start of the Free Channel thread -> SKIP
            if event_type != "NEW_TRADE" and not reply_to and not is_new_thread_start:
                continue

            # --- BUILD MESSAGE CONTENT (UPDATED: USES TEMPLATE) ---
            msg = self._format_msg(event_type, trade, extra_data, action_time)
            
            if not msg: continue # Skip if template failed or empty

            # Add Channel Name prefix for NEW_TRADE if configured
            if event_type == "NEW_TRADE" and ch.get('custom_name'):
                msg = f"🚀 <b>[{ch['custom_name']}]</b>\n" + msg

            # --- FREE CHANNEL HEADER INJECTION (TEMPLATE BASED) ---
            # If this is the start of a thread (e.g. T1 hit in Spillover mode), inject Header.
            if is_new_thread_start and key == 'free' and event_type != "NEW_TRADE":
                header = self._format_msg("FREE_HEADER", trade, extra_data, action_time)
                if header:
                    msg = header + msg

            # --- SEND & SAVE ---
            if msg:
                sent_id = self.send_message(msg, reply_to_id=reply_to, override_chat_id=chat_id)
                if sent_id:
                    self._save_msg_to_db(trade.get('id'), sent_id, chat_id)
                    
                    # If NEW_TRADE or First Free Msg, update stored IDs
                    if event_type == "NEW_TRADE" or is_new_thread_start:
                        new_msg_ids[key] = sent_id
                        # Update trade object for persistence
                        trade['telegram_msg_ids'][key] = sent_id

        return new_msg_ids

    def _save_msg_to_db(self, trade_id, msg_id, chat_id):
        """Helper to safely save message ID to database"""
        if not trade_id or not msg_id or not chat_id:
            return
            
        try:
            rec = TelegramMessage(trade_id=str(trade_id), message_id=msg_id, chat_id=str(chat_id))
            db.session.add(rec)
            db.session.commit()
        except Exception as e:
            print(f"⚠️ Failed to save Telegram Msg ID: {e}")
            try: db.session.rollback()
            except: pass

    def delete_trade_messages(self, trade_id):
        """
        Deletes messages associated with a trade from the database immediately,
        then spawns a background thread to call the Telegram API for cleanup.
        Prevents Worker Timeout on slow network calls.
        """
        try:
            # 1. Fetch messages
            messages = TelegramMessage.query.filter_by(trade_id=str(trade_id)).all()
            if not messages: return

            # 2. Extract data for background processing
            msg_data_list = [{"chat_id": m.chat_id, "message_id": m.message_id} for m in messages]

            # 3. Delete from DB immediately (Fast)
            for msg in messages:
                db.session.delete(msg)
            
            db.session.commit()
            print(f"🗑️ Deleted {len(messages)} Telegram messages from DB for Trade {trade_id}")

            # 4. Background Task Definition
            def bg_cleanup(token, items):
                delete_url = f"{self.base_url}{token}/deleteMessage"
                for item in items:
                    try:
                        requests.post(delete_url, json=item, timeout=5)
                        # Small sleep to prevent rate limiting if many messages
                        time.sleep(0.1) 
                    except Exception as req_err:
                        print(f"BG Delete Request Error: {req_err}")

            # 5. Launch Background Thread
            conf = self._get_config()
            token = conf.get('bot_token')
            
            if token and msg_data_list:
                t = threading.Thread(target=bg_cleanup, args=(token, msg_data_list))
                t.daemon = True # Ensure thread doesn't block app shutdown
                t.start()

        except Exception as e:
            print(f"❌ Error deleting Telegram messages: {e}")
            try: db.session.rollback()
            except: pass

# Singleton Instance
bot = TelegramManager()
