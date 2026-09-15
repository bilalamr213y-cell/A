import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_FILE = os.getenv("DATABASE_FILE", "bot_data.db")

GARENA_BASE_URL = "https://100067.connect.garena.com"
GARENA_APP_ID = "100067"
GARENA_USER_AGENT = "GarenaMSDK/4.0.42(22101316I ;Android 14;en;US;app 2.131.1 2019118334;)"

TIMEZONE = "Africa/Algiers"
START_TIME_HOUR = 4
INTERVAL_SECONDS = 0.001
MAX_EMAILS = 10
MAX_DAILY_SENDS_PER_EMAIL = 20
REQUIRED_REFERRALS_PER_BURN = 5
MAX_BIND_SEARCHES_PER_DAY = 100

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"