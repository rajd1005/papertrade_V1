import os

basedir = os.path.abspath(os.path.dirname(__file__))

# Zerodha Credentials
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

# Auto Login Credentials
TOTP_SECRET = os.getenv("TOTP_SECRET")
ZERODHA_USER_ID = os.getenv("ZERODHA_USER_ID")
ZERODHA_PASSWORD = os.getenv("ZERODHA_PASSWORD")

# Secure Admin Page Password
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    print("⚠️ WARNING: ADMIN_PASSWORD environment variable not set. Using default 'admin123'.")
    ADMIN_PASSWORD = "admin123"

# Flask Settings
SECRET_KEY = "super_secret_algo_key_v3"
PORT = int(os.environ.get("PORT", 5000))

# Trade Defaults
DEFAULT_SL_POINTS = 20

# Database Config
uri = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(basedir, "algo.db"))
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

SQLALCHEMY_DATABASE_URI = uri
SQLALCHEMY_TRACK_MODIFICATIONS = False

connect_args = {}
if "postgresql" in uri:
    connect_args = {'options': '-c timezone=Asia/Kolkata'}

SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': connect_args}
