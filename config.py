import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables desde el archivo .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "trading_bot.db"

CLOB_API_URL = os.getenv("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com")
DEFAULT_ORDER_AMOUNT_USDC = 10.0

MIN_NET_EV = 0.12
MIN_ENTRY_PRICE = 0.15
MAX_ENTRY_PRICE = 0.40
MIN_EXECUTABLE_PRICE = float(os.getenv("MIN_EXECUTABLE_PRICE", 0.15))
MAX_EXECUTABLE_PRICE = float(os.getenv("MAX_EXECUTABLE_PRICE", 0.40))
ESTIMATED_FEE = 0.0
ESTIMATED_SLIPPAGE = 0.005

MAX_PENDING_ORDERS = 20
MAX_HOURS_TO_EXPIRY = float(os.getenv("MAX_HOURS_TO_EXPIRY", 1.0))
MIN_MARKET_DURATION_MINUTES = float(
    os.getenv("MIN_MARKET_DURATION_MINUTES", 15.0)
)

# Telegram Credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
