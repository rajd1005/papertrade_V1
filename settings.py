import json
from database import db, AppSetting

def get_defaults():
    # Define default settings for a mode
    default_mode_settings = {
        "qty_mult": 1, 
        "ratios": [0.5, 1.0, 1.5], 
        "symbol_sl": {}, 
        "trailing_sl": 0,
        "sl_to_entry": 0,
        "order_type": "MARKET",
        "exit_multiplier": 1,
        "universal_exit_time": "15:25", 
        "max_loss": 0,       
        "profit_lock": 0,    
        "profit_min": 0,     
        "profit_trail": 0    
    }
    
    return {
        "exchanges": ["NSE", "NFO", "MCX", "CDS", "BSE", "BFO"],
        "watchlist": [],
        # --- NEW: Default Broadcast Channels (Default: All Checked) ---
        "broadcast_defaults": ["vip", "free", "z2h"], 
        # --------------------------------------------------------------
        "modes": {
            "LIVE": default_mode_settings.copy(),
            "PAPER": default_mode_settings.copy()
            # NOTE: SHADOW mode is a macro in main.py, it does not need a separate config key here.
        },
        "import_config": {
            "enable_history_check": True,
            "default_interval": "minute"
        },
        # --- NEW TELEGRAM CONFIG (UPDATED) ---
        "telegram": {
            "bot_token": "",
            "enable_notifications": False,
            
            # 1. Main Channel (Receives ALL updates)
            "channel_id": "", 
            "system_channel_id": "",
            
            # 2. VIP Channel (New/Active/Update Only)
            "vip_channel_id": "",
            
            # 3. Free Channel (New/Active/Update Only)
            "free_channel_id": "",
            
            # 4. ZeroToHero Channel (New/Active/Update Only + Custom Name)
            "z2h_channel_id": "",
            "z2h_channel_name": "Zero To Hero" # Default Name
        }
    }

def load_settings():
    defaults = get_defaults()
    try:
        setting = AppSetting.query.first()
        if setting:
            saved = json.loads(setting.data)
            
            # Integrity Check
            if "modes" not in saved:
                old_mult = saved.get("qty_mult", 1)
                old_ratios = saved.get("ratios", [0.5, 1.0, 1.5])
                old_sl = saved.get("symbol_sl", {})
                saved["modes"] = {
                    "LIVE": {"qty_mult": old_mult, "ratios": old_ratios, "symbol_sl": old_sl.copy()},
                    "PAPER": {"qty_mult": old_mult, "ratios": old_ratios, "symbol_sl": old_sl.copy()}
                }

            # Merge Defaults (Only LIVE and PAPER)
            for m in ["LIVE", "PAPER"]:
                if m in saved["modes"]:
                    for key, val in defaults["modes"][m].items():
                        if key not in saved["modes"][m]: saved["modes"][m][key] = val
                    if "symbol_sl" not in saved["modes"][m]: saved["modes"][m]["symbol_sl"] = {}
                else: saved["modes"][m] = defaults["modes"][m].copy()

            if "exchanges" not in saved: saved["exchanges"] = defaults["exchanges"]
            if "watchlist" not in saved: saved["watchlist"] = []
            
            # --- MERGE NEW KEY ---
            if "broadcast_defaults" not in saved: saved["broadcast_defaults"] = defaults["broadcast_defaults"]
            
            if "import_config" not in saved: saved["import_config"] = defaults["import_config"]

            # Merge Telegram (Recursive merge for new keys)
            if "telegram" not in saved: 
                saved["telegram"] = defaults["telegram"]
            else:
                for k, v in defaults["telegram"].items():
                    if k not in saved["telegram"]:
                        saved["telegram"][k] = v

            return saved
    except Exception as e: print(f"Error loading settings: {e}")
    return defaults

def save_settings_file(data):
    try:
        setting = AppSetting.query.first()
        if not setting:
            setting = AppSetting(data=json.dumps(data))
            db.session.add(setting)
        else: setting.data = json.dumps(data)
        db.session.commit()
        return True
    except Exception as e:
        print(f"Settings Save Error: {e}")
        db.session.rollback()
        return False
