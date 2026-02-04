# managers/config_manager.py
import os
import json
from database import db, AppSetting
import settings

def get_auth_config():
    """Retrieves current auth credentials from the database or environment."""
    current_settings = settings.load_settings()
    auth = current_settings.get('auth_credentials', {
        "ZERODHA_USER_ID": os.getenv("ZERODHA_USER_ID", ""),
        "ZERODHA_PASSWORD": os.getenv("ZERODHA_PASSWORD", ""),
        "API_KEY": os.getenv("API_KEY", ""),
        "API_SECRET": os.getenv("API_SECRET", ""),
        "TOTP_SECRET": os.getenv("TOTP_SECRET", "")
    })
    return auth

def update_auth_config(new_data):
    """Updates the credentials in the AppSetting table."""
    try:
        current_settings = settings.load_settings()
        current_settings['auth_credentials'] = new_data
        
        # Update the AppSetting record in DB
        from settings import save_settings_file
        save_settings_file(current_settings)
        return True
    except Exception as e:
        print(f"Error updating auth config: {e}")
        return False

def get_dynamic_callback_url(request_url):
    """Generates the callback URL based on the current host."""
    # Parses the base URL (e.g., https://your-app.railway.app) and appends /callback
    from urllib.parse import urljoin, urlparse
    parsed_uri = urlparse(request_url)
    base_url = f"{parsed_uri.scheme}://{parsed_uri.netloc}"
    return urljoin(base_url, "/callback")
