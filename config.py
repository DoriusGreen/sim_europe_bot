import os
from typing import Set, Tuple, Optional

# ===== Ключі та налаштування =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8443"))

# ==== Константи пам'яті/міток ====
MAX_TURNS = 10
ORDER_DUP_WINDOW_SEC = 20 * 60  # 20 хвилин
ORDER_COOLDOWN_SEC = 3 * 60     # 3 хвилини — вікно після замовлення, в якому ack-повідомлення не підуть до GPT

# ==== ГРУПА ДЛЯ ЗАМОВЛЕНЬ ====
ORDER_FORWARD_CHAT_ID = int(os.getenv("ORDER_FORWARD_CHAT_ID", "-1003062477534"))

# ==== Менеджери ====
def _parse_ids(env: Optional[str]) -> Set[int]:
    out: Set[int] = set()
    if not env: return out
    for x in env.split(","):
        try:
            if x.strip(): out.add(int(x.strip()))
        except ValueError: pass
    return out

def _parse_usernames(env: Optional[str]) -> Set[str]:
    out: Set[str] = set()
    if not env: return out
    for x in env.split(","):
        x = x.strip().lstrip("@").lower()
        if x: out.add(x)
    return out

DEFAULT_OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Sim_Card_Three")
DEFAULT_OWNER_USER_ID = os.getenv("OWNER_USER_ID", "")

MANAGER_USER_IDS = _parse_ids(os.getenv("MANAGER_USER_IDS"))
if DEFAULT_OWNER_USER_ID:
    MANAGER_USER_IDS |= _parse_ids(DEFAULT_OWNER_USER_ID)

MANAGER_USERNAMES = _parse_usernames(os.getenv("MANAGER_USERNAMES"))
if DEFAULT_OWNER_USERNAME:
    MANAGER_USERNAMES.add(DEFAULT_OWNER_USERNAME.strip().lstrip("@").lower())

# ==== Прайси й мапи країн ====
PRICE_TIERS = {
    "ВЕЛИКОБРИТАНІЯ": [(1000, None), (100, 275), (20, 300), (10, 325), (4, 350), (2, 375), (1, 450)],
    "НІДЕРЛАНДИ":     [(20, 850), (4, 900), (1, 950)],
    "НІМЕЧЧИНА":      [(10, 1100), (4, 1200), (1, 1300)],
    "ФРАНЦІЯ":        [(10, 1100), (4, 1200), (1, 1400)],
    "ІСПАНІЯ":        [(10, 1500), (4, 1800), (1, 2000)],
    "ЧЕХІЯ":          [(10, 650), (4, 700), (1, 750)],
    "ПОЛЬЩА":         [(10, 400), (4, 450), (1, 500)],
    "ЛИТВА":          [(10, 650), (4, 700), (1, 750)],
    "ЛАТВІЯ":         [(10, 650), (4, 700), (1, 750)],
    "КАЗАХСТАН":      [(10, 1100), (4, 1200), (2, 1300), (1, 1500)],
    "МАРОККО":        [(10, 750), (4, 800), (2, 900), (1, 1000)],
}

FLAGS = {
    "ВЕЛИКОБРИТАНІЯ": "🇬🇧", "НІДЕРЛАНДИ": "🇳🇱", "НІМЕЧЧИНА": "🇩🇪",
    "ФРАНЦІЯ": "🇫🇷", "ІСПАНІЯ": "🇪🇸", "ЧЕХІЯ": "🇨🇿",
    "ПОЛЬЩА": "🇵🇱", "ЛИТВА": "🇱🇹", "ЛАТВІЯ": "🇱🇻",
    "КАЗАХСТАН": "🇰🇿", "МАРОККО": "🇲🇦", "ІТАЛІЯ": "🇮🇹", "МОЛДОВА": "🇲🇩",
}

DISPLAY = {
    "ВЕЛИКОБРИТАНІЯ": "Англія", "НІДЕРЛАНДИ": "Нідерланди", "НІМЕЧЧИНА": "Німеччина",
    "ФРАНЦІЯ": "Франція", "ІСПАНІЯ": "Іспанія", "ЧЕХІЯ": "Чехія",
    "ПОЛЬЩА": "Польща", "ЛИТВА": "Литва", "ЛАТВІЯ": "Латвія",
    "КАЗАХСТАН": "Казахстан", "МАРОККО": "Марокко", "ІТАЛІЯ": "Італія", "МОЛДОВА": "Молдова",
}

DIAL_CODES = {
    "ВЕЛИКОБРИТАНІЯ": "+44", "ІСПАНІЯ": "+34", "ФРАНЦІЯ": "+33",
    "НІМЕЧЧИНА": "+49", "НІДЕРЛАНДИ": "+31", "ІТАЛІЯ": "+39",
    "ЧЕХІЯ": "+420", "МОЛДОВА": "+373", "КАЗАХСТАН": "+7", "ЛАТВІЯ": "+371",
}

# ==== НАЯВНІСТЬ КРАЇН =====
COUNTRY_AVAILABILITY = {
    "ВЕЛИКОБРИТАНІЯ": {"status": "+", "reason": ""},
    "НІДЕРЛАНДИ":     {"status": "+", "reason": ""},
    "НІМЕЧЧИНА":       {"status": "+", "reason": ""},
    "ФРАНЦІЯ":         {"status": "+", "reason": ""},
    "ІСПАНІЯ":         {"status": "+", "reason": ""},
    "ЧЕХІЯ":           {"status": "+", "reason": ""},
    "ПОЛЬЩА":          {"status": "+", "reason": ""},
    "ЛИТВА":           {"status": "-", "reason": "Нараз сім-карти недоступні через проблему з активацією."},
    "ЛАТВІЯ":          {"status": "-", "reason": "Нараз сім-карти недоступні через проблему з активацією."},
    "КАЗАХСТАН":       {"status": "+", "reason": ""},
    "МАРОККО":         {"status": "+", "reason": ""},
}

USSD_DATA = {
    "ВЕЛИКОБРИТАНІЯ": [("Vodafone", "*#100#")],
    "ІСПАНІЯ": [("Lebara", "*321#"), ("Movistar", "*133#"), ("Lycamobile", "*321#")],
    "НІМЕЧЧИНА": [("Vodafone", "*135#"), ("Lebara", "*135#"), ("Lycamobile", "*132#")],
    "НІДЕРЛАНДИ": [("Lycamobile", "*102#")],
    "ІТАЛІЯ": [("Lycamobile", "*132#")],
    "ФРАНЦІЯ": [("Lebara", "*144*1#")],
    "ЧЕХІЯ": [("T-mobile", "*101#"), ("Kaktus", "*103#")],
    "МОЛДОВА": [(None, "*444# (потім 3)")],
    "КАЗАХСТАН": [(None, "*120#")],
    "ЛАТВІЯ": [(None, "Киньте виклик на український номер — ваш латвійський номер відобразиться у виклику/на екрані.")],
}

def get_availability(country_norm: str) -> Tuple[str, Optional[str]]:
    entry = COUNTRY_AVAILABILITY.get(country_norm)
    if not entry: return ("+", None)
    return (entry.get("status", "+"), entry.get("reason", "").strip() or None)

# ==== КОДИ ДЛЯ АВТО-ВІДПОВІДІ ПІСЛЯ ЗАМОВЛЕННЯ ====
POST_ORDER_USSD = {
    "ВЕЛИКОБРИТАНІЯ": "*#100#",
    "НІМЕЧЧИНА": "*135#",
    "ФРАНЦІЯ": "*144*1#",
    "ІСПАНІЯ": "*321#",
    "КАЗАХСТАН": "*120#",
    "МОЛДОВА": "*444#",
}

# ==== КРИПТО-ОПЛАТА ====
CRYPTO_WALLET = "TJmcPRu2b4Sstup1hBnNGZHB8qWnhGKTd5"
CRYPTO_UAH_RATE = 43    # 1 USD = 43 UAH
CRYPTO_FEE_USD = 1      # комісія/округлення +1 USD
