import os

basedir = os.path.abspath(os.path.dirname(__file__))

# API Keys (Still needed for Order Placement object)
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

# App Security
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_algo_key_v3")
PORT = int(os.environ.get("PORT", 5000))

# Database
uri = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(basedir, "algo.db"))
if uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

SQLALCHEMY_DATABASE_URI = uri
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options': '-c timezone=Asia/Kolkata'}} if "postgresql" in uri else {}
