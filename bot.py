import os
import time
import logging
from typing import List, Dict, Optional, Set, Any, Tuple
from dataclasses import dataclass
import json
import re

from telegram import Update, Message
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import openai

# ===== Налаштування логів =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Ключі та налаштування =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8443"))

openai.api_key = OPENAI_API_KEY

# ==== Константи пам'яті/міток ====
MAX_TURNS = 10
ORDER_DUP_WINDOW_SEC = 20 * 60  # 20 хвилин

# ==== КУДА ДУБУЮ Є ЗАМОВЛЕННЯ (ГРУПА) ====
ORDER_FORWARD_CHAT_ID = int(os.getenv("ORDER_FORWARD_CHAT_ID", "-1003062477534"))

# ==== Ігнор/врахування повідомлень менеджера ====
def _parse_ids(env: Optional[str]) -> Set[int]:
    out: Set[int] = set()
    if not env:
        return out
    for x in env.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.add(int(x))
        except ValueError:
            pass
    return out

def _parse_usernames(env: Optional[str]) -> Set[str]:
    out: Set[str] = set()
    if not env:
        return out
    for x in env.split(","):
        x = x.strip().lstrip("@").lower()
        if x:
            out.add(x)
    return out

DEFAULT_OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Sim_Card_Three")
DEFAULT_OWNER_USER_ID = os.getenv("OWNER_USER_ID", "")

MANAGER_USER_IDS = _parse_ids(os.getenv("MANAGER_USER_IDS"))
if DEFAULT_OWNER_USER_ID:
    MANAGER_USER_IDS |= _parse_ids(DEFAULT_OWNER_USER_ID)

MANAGER_USERNAMES = _parse_usernames(os.getenv("MANAGER_USERNAMES"))
if DEFAULT_OWNER_USERNAME:
    MANAGER_USERNAMES.add(DEFAULT_OWNER_USERNAME.strip().lstrip("@").lower())

def _is_manager_message(msg: Message) -> bool:
    u = msg.from_user
    if not u:
        return False
    if MANAGER_USER_IDS and u.id in MANAGER_USER_IDS:
        return True
    if MANAGER_USERNAMES and u.username and u.username.lower() in MANAGER_USERNAMES:
        return True
    return False

# ==== Стандартні повідомлення ====
ORDER_INFO_REQUEST = (
    "🛒 Для оформлення замовлення напишіть:\n\n"
    "1. Ім'я та прізвище.\n"
    "2. Номер телефону.\n"
    "3. Місто та № відділення \"Нової Пошти\".\n"
    "4. Країна(и) та кількість sim-карт."
)

# ==== Прайси й мапи країн (ДОСТУПНІ ТІЛЬКИ ЦІ) ====
PRICE_TIERS = {
    "ВЕЛИКОБРИТАНІЯ": [(1000, None), (100, 210), (20, 250), (10, 275), (4, 300), (2, 325), (1, 350)],
    "НІДЕРЛАНДИ":     [(20, 700), (4, 750), (1, 800)],
    "НІМЕЧЧИНА":      [(10, 900), (4, 1000), (1, 1100)],
    "ФРАНЦІЯ":        [(10, 1100), (4, 1200), (1, 1400)],
    "ІСПАНІЯ":        [(10, 800), (4, 850), (1, 900)],
    "ЧЕХІЯ":          [(10, 650), (4, 700), (1, 750)],
    "ПОЛЬЩА":         [(10, 400), (4, 450), (1, 500)],
    "ЛИТВА":          [(10, 650), (4, 700), (1, 750)],
    "ЛАТВІЯ":         [(10, 650), (4, 700), (1, 750)],
    "КАЗАХСТАН":      [(10, 900), (4, 1000), (2, 1100), (1, 1200)],
    "МАРОККО":        [(10, 750), (4, 800), (2, 900), (1, 1000)],
    "США":            [(10, 1000), (4, 1300), (1, 1400)],
}
FLAGS = {
    "ВЕЛИКОБРИТАНІЯ": "🇬🇧",
    "НІДЕРЛАНДИ": "🇳🇱",
    "НІМЕЧЧИНА": "🇩🇪",
    "ФРАНЦІЯ": "🇫🇷",
    "ІСПАНІЯ": "🇪🇸",
    "ЧЕХІЯ": "🇨🇿",
    "ПОЛЬЩА": "🇵🇱",
    "ЛИТВА": "🇱🇹",
    "ЛАТВІЯ": "🇱🇻",
    "КАЗАХСТАН": "🇰🇿",
    "МАРОККО": "🇲🇦",
    "США": "🇺🇸",
    "ІТАЛІЯ": "🇮🇹",
    "МОЛДОВА": "🇲🇩",
}
DISPLAY = {
    "ВЕЛИКОБРИТАНІЯ": "Англія",
    "НІДЕРЛАНДИ": "Нідерланди",
    "НІМЕЧЧИНА": "Німеччина",
    "ФРАНЦІЯ": "Франція",
    "ІСПАНІЯ": "Іспанія",
    "ЧЕХІЯ": "Чехія",
    "ПОЛЬЩА": "Польща",
    "ЛИТВА": "Литва",
    "ЛАТВІЯ": "Латвія",
    "КАЗАХСТАН": "Казахстан",
    "МАРОККО": "Марокко",
    "США": "США",
    "ІТАЛІЯ": "Італія",
    "МОЛДОВА": "Молдова",
}
DIAL_CODES = {
    "ВЕЛИКОБРИТАНІЯ": "+44",
    "ІСПАНІЯ": "+34",
    "ФРАНЦІЯ": "+33",
    "НІМЕЧЧИНА": "+49",
    "НІДЕРЛАНДИ": "+31",
    "ІТАЛІЯ": "+39",
    "ЧЕХІЯ": "+420",
    "МОЛДОВА": "+373",
    "КАЗАХСТАН": "+7",
    "ЛАТВІЯ": "+371",
    "США": "+1",
}

def normalize_country(name: str) -> str:
    n = (name or "").strip().upper()
    if n in ("АНГЛІЯ", "БРИТАНІЯ", "UK", "U.K.", "UNITED KINGDOM", "ВБ", "GREAT BRITAIN", "+44", "ЮК", "У.К."):
        return "ВЕЛИКОБРИТАНІЯ"
    if n in ("USA", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA", "ШТАТИ", "АМЕРИКА", "US", "U.S."):
        return "США"
    if n in ("ITALY","ИТАЛИЯ","ІТАЛІЯ","ITALIA","+39"):
        return "ІТАЛІЯ"
    if n in ("МОЛДОВА","MOLDOVA","+373"):
        return "МОЛДОВА"
    return n

# ---------- ОПЕРАТОРИ ДЛЯ АНГЛІЇ (для замовлення) ----------
def canonical_operator(op: Optional[str]) -> Optional[str]:
    if not op:
        return None
    o = op.strip().lower()
    if o in ("o2", "о2"):
        return "O2"
    if o in ("lebara", "лебара"):
        return "Lebara"
    if o in ("vodafone", "водафон", "водофон"):
        return "Vodafone"
    if o in ("three", "трі", "3"):
        return "Three"
    return None

# ---------- ОПЕРАТОРИ ДЛЯ USSD (довідка) ----------
def canonical_operator_any(op: Optional[str]) -> Optional[str]:
    if not op:
        return None
    o = op.strip().lower()
    mapping = {
        "O2": ["o2","о2"],
        "Lebara": ["lebara","лебара"],
        "Vodafone": ["vodafone","водафон","водофон"],
        "Movistar": ["movistar","мовістар","мовистар"],
        "Lycamobile": ["lycamobile","lyca","lyka","лайкамобайл","лайка"],
        "T-mobile": ["t-mobile","t mobile","т-мобайл","т мобайл","tmobile","tмобайл"],
        "Kaktus": ["kaktus","кактус"],
    }
    for canon, alts in mapping.items():
        if o in alts:
            return canon
    return None

def unit_price(country_norm: str, qty: int) -> Optional[int]:
    tiers = PRICE_TIERS.get(country_norm)
    if not tiers:
        return None
    for min_q, price in tiers:
        if qty >= min_q:
            return price
    return None

# ==== РЕНДЕР ПРАЙСІВ ====
def _format_range(min_q: int, max_q: Optional[int]) -> str:
    if max_q is None: return f"{min_q}+ шт."
    if min_q == max_q: return f"{min_q} шт."
    return f"{min_q}-{max_q} шт."

def render_price_block(country_key: str) -> str:
    flag = FLAGS.get(country_key, "")
    header = f"{flag} {country_key} {flag}\n\n"
    tiers = PRICE_TIERS.get(country_key, [])
    if not tiers:
        return header + "Немає даних.\n\n"
    tiers_sorted = sorted(tiers, key=lambda x: x[0])
    lines = []
    inserted_gap = False
    for idx, (min_q, price) in enumerate(tiers_sorted):
        next_min = tiers_sorted[idx + 1][0] if idx + 1 < len(tiers_sorted) else None
        max_q = (next_min - 1) if next_min else None
        if not inserted_gap and min_q >= 100 and idx > 0:
            lines.append("")
            inserted_gap = True
        qty_part = _format_range(min_q, max_q)
        line = f"{qty_part} — {'договірна' if price is None else str(price) + ' грн'}"
        lines.append(line)
    return header + "\n".join(lines) + "\n\n"

def available_list_text() -> str:
    names = [DISPLAY[k] for k in PRICE_TIERS.keys()]
    if len(names) == 1: return names[0]
    return ", ".join(names[:-1]) + " та " + names[-1]

def render_prices(countries: List[str]) -> str:
    blocks = []
    for c in countries:
        key = normalize_country(c).upper()
        if key in PRICE_TIERS:
            blocks.append(render_price_block(key))
    return "".join(blocks)

def render_unavailable(unavail: List[str]) -> str:
    names = [str(x).strip() for x in unavail if str(x).strip()]
    if not names:
        return ""
    if len(names) == 1:
        return f"На жаль, {names[0]} SIM-карти наразі недоступні.\nУ наявності: {available_list_text()}."
    return f"На жаль, {', '.join(names)} наразі недоступні.\nУ наявності: {available_list_text()}."

# ==== Форматування ПІДСУМКУ ====
def _cap_word(w: str) -> str:
    return w[:1].upper() + w[1:].lower() if w else w

def _smart_title(s: str) -> str:
    s = (s or "").strip()
    parts = re.split(r"\s+", s)
    out = []
    for p in parts:
        sub = "-".join(_cap_word(x) for x in p.split("-"))
        out.append(sub)
    return " ".join(out)

def format_full_name(name: str) -> str:
    tokens = [t for t in (name or "").strip().split() if t]
    if not tokens:
        return ""
    if len(tokens) >= 2:
        return f"{_smart_title(tokens[0])} {_smart_title(tokens[-1])}"
    return _smart_title(tokens[0])

def format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("380"):
        digits = "0" + digits[3:]
    if len(digits) == 10:
        return f"{digits[0:3]} {digits[3:6]} {digits[6:10]}"
    return (phone or "").strip()

def format_city(city: str) -> str:
    return _smart_title(city)

def format_np(np_str: str) -> str:
    s = (np_str or "").strip()
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    s = re.sub(r"[^\d]", "", s)
    return s or (np_str or "").strip()

ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

@dataclass
class OrderItem:
    country: str
    qty: int
    operator: Optional[str] = None

@dataclass
class OrderData:
    full_name: str
    phone: str
    city: str
    np: str
    items: List[OrderItem]

def _order_signature(order: OrderData) -> str:
    items_sig = ";".join(
        f"{normalize_country(it.country)}:{int(it.qty)}:{canonical_operator(it.operator) or ''}"
        for it in order.items
    )
    return f"{format_full_name(order.full_name)}|{format_phone(order.phone)}|{format_city(order.city)}|{format_np(order.np)}|{items_sig}"

# --- ПОЧАТОК ЗМІН ---
def render_order(order: OrderData) -> str:
    lines = []
    grand_total = 0
    counted_countries = 0

    for it in order.items:
        c_norm = normalize_country(it.country)
        disp_base = DISPLAY.get(c_norm, it.country.strip().title())
        op = canonical_operator(getattr(it, "operator", None))
        op_suf = f" (оператор {op})" if (op and c_norm == "ВЕЛИКОБРИТАНІЯ") else ""
        disp = disp_base + op_suf

        flag = FLAGS.get(c_norm, "")
        price = unit_price(c_norm, it.qty)

        if price is None:
            line_total_str = "договірна"
        else:
            line_total = price * it.qty
            grand_total += line_total
            counted_countries += 1
            line_total_str = str(line_total)

        lines.append(ORDER_LINE.format(
            flag=flag, disp=disp, qty=it.qty, line_total=line_total_str
        ))

    # Умовне форматування адреси
    if order.np and order.np.strip():
        # Стандартна доставка на відділення
        address_line = f"{format_city(order.city)} № {format_np(order.np)}"
    else:
        # Кур'єрська/адресна доставка
        raw_address = order.city.strip()
        address_line = f"📫: {raw_address}"

    header = (
        f"{format_full_name(order.full_name)} \n"
        f"{format_phone(order.phone)}\n"
        f"{address_line}  \n\n"
    )
    body = "".join(lines) + "\n"
    footer = f"Загальна сумма: {grand_total} грн\n" if counted_countries >= 2 else ""
    return header + body + footer
# --- КІНЕЦЬ ЗМІН ---

# ==== JSON парсери ====
ORDER_JSON_RE = re.compile(r"\{[\s\S]*\}")
PRICE_JSON_RE = re.compile(r"\{[\s\S]*\}")
USSD_JSON_RE = re.compile(r"\{[\s\S]*\}")

def try_parse_usa_activation_json(text: str) -> bool:
    """Перевіряє, чи повернув GPT команду на показ інструкції для США."""
    try:
        data = json.loads(text)
        return data.get("ask_usa_activation") is True
    except (json.JSONDecodeError, TypeError):
        return False

def try_parse_order_json(text: str) -> Optional[OrderData]:
    m = ORDER_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        items = [OrderItem(
            country=i["country"],
            qty=int(i["qty"]),
            operator=i.get("operator")
        ) for i in data.get("items", [])]
        return OrderData(
            full_name=data.get("full_name", "").strip(),
            phone=data.get("phone", "").strip(),
            city=data.get("city", "").strip(),
            np=str(data.get("np", "")).strip(),
            items=items
        )
    except Exception as e:
        logger.warning(f"Не вдалося розпарсити JSON замовлення: {e}")
        return None

def try_parse_price_json(text: str) -> Optional[List[str]]:
    m = PRICE_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("ask_prices") is True and isinstance(data.get("countries"), list):
            return data["countries"]
        return None
    except Exception:
        return None

def try_parse_ussd_json(text: str) -> Optional[List[Dict[str, str]]]:
    m = USSD_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("ask_ussd") is True and isinstance(data.get("targets"), list):
            out = []
            for t in data["targets"]:
                if not isinstance(t, dict):
                    continue
                c = t.get("country")
                o = t.get("operator")
                if c:
                    out.append({"country": c, "operator": o})
            return out or None
        return None
    except Exception:
        return None

# ==== Витяг «чого бракує» зі службового повідомлення ====
def missing_points_from_reply(text: str) -> Set[int]:
    out: Set[int] = set()
    if "Залишилось вказати" not in (text or ""):
        return out
    for ln in (text or "").splitlines():
        m = re.match(r"\s*([1-4])\.\s", ln)
        if m:
            out.add(int(m.group(1)))
    return out

# ==== США: інструкція та детект ====
US_ACTIVATION_MSG = (
    "США, на відміну від інших країн, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
    "Як активувати та поповнити сім-карту США?\n\n"
    "https://www.lycamobile.us/en/activate-sim\n"
    "1. На цьому сайті вводите дані сімки для її попередньої активації.\n"
    "Отриманий на сайті номер сім-карти записуєте.\n\n"
    "https://www.lycamobile.us/en/quick-top-up/\n"
    "2. Далі, ось тут, вказавши номер, отриманий на попередньому сайті, поповнюєте сім-карту, після поповнення (мінімум на 23$) вона стане активною та буде приймати SMS."
)

def contains_us_activation_block(text: str) -> bool:
    t = (text or "").lower()
    return ("lycamobile.us/en/activate-sim" in t) or ("як активувати та поповнити сім-карту сша" in t)

# ==== ДОВІДНИК USSD КОМБІНАЦІЙ ====
USSD_DATA: Dict[str, List[Tuple[Optional[str], str]]] = {
    "ВЕЛИКОБРИТАНІЯ": [("Vodafone", "*#100#"), ("Lebara", "*#100#"), ("O2", "комбінації немає, номер вказаний на упаковці.")],
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
FALLBACK_PLASTIC_MSG = "Номер вказаний на пластику сім-карти"

def render_ussd_targets(targets: List[Dict[str, str]]) -> str:
    result_lines: List[str] = []
    for t in targets:
        country = normalize_country(t.get("country", "")).upper()
        if not country:
            continue
        op_req = canonical_operator_any(t.get("operator"))
        code_prefix = DIAL_CODES.get(country, "")
        flag = FLAGS.get(country, "")
        disp = DISPLAY.get(country, country.title())
        pairs = USSD_DATA.get(country, [])
        if op_req and pairs:
            pairs = [p for p in pairs if (p[0] and canonical_operator_any(p[0]) == op_req)]
        if not pairs:
            if op_req:
                result_lines.append(f"{code_prefix} {flag} {disp} (оператор {op_req}) — {FALLBACK_PLASTIC_MSG}")
            else:
                result_lines.append(f"{code_prefix} {flag} {disp} — {FALLBACK_PLASTIC_MSG}")
            result_lines.append("")
            continue
        for op, code in pairs:
            if op:
                result_lines.append(f"{code_prefix} {flag} {disp} (оператор {op}) — {code}")
            else:
                result_lines.append(f"{code_prefix} {flag} {disp} — {code}")
        result_lines.append("")
    while result_lines and result_lines[-1] == "":
        result_lines.pop()
    return "\n".join(result_lines).strip()

# ==== Витяг процитованого тексту ====
def extract_quoted_text(message: Optional[Message]) -> Optional[str]:
    if not message:
        return None
    rt = message.reply_to_message
    if not rt:
        return None
    text = (rt.text or rt.caption or "").strip()
    return text or None

# ==== Детектор «тільки кількість» після прайсу ====
QTY_ONLY_RE = re.compile(
    r"(?:\bпо\b\s*)?(\d{1,4})\s*(шт|штук|шт\.?|сим(?:-?карт[аи])?|sim-?card|sim|pieces?)\b",
    re.IGNORECASE
)
def detect_qty_only(text: str) -> Optional[int]:
    if not text:
        return None
    m = QTY_ONLY_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

# ==== евристика «в самому тексті є і країни, і кількості» (п.4) ====
COUNTRY_KEYWORDS: Dict[str, List[str]] = {
    "ВЕЛИКОБРИТАНІЯ": ["англ", "британ", "великобритан", "uk", "u.k", "great britain", "+44"],
    "ФРАНЦІЯ": ["франц", "france", "+33"],
    "ІСПАНІЯ": ["іспан", "испан", "spain", "+34"],
    "НІМЕЧЧИНА": ["німеч", "герман", "german", "+49", "deutsch"],
    "НІДЕРЛАНДИ": ["нідерлан", "голланд", "holland", "nether", "+31"],
    "ІТАЛІЯ": ["італ", "итал", "ital", "+39"],
    "ЧЕХІЯ": ["чех", "czech", "+420"],
    "ПОЛЬЩА": ["польщ", "польш", "poland"],
    "ЛИТВА": ["литв", "lithuan"],
    "ЛАТВІЯ": ["латв", "latvia"],
    "КАЗАХСТАН": ["казах", "kazakh", "+7"],
    "МАРОККО": ["марок", "morocc"],
    "США": ["сша", "usa", "америк", "штат", "+1"],
}

def _country_mentions_with_pos(text: str) -> List[Tuple[str, int]]:
    low = (text or "").lower()
    found: List[Tuple[str, int]] = []
    for key, subs in COUNTRY_KEYWORDS.items():
        best = None
        for s in subs:
            i = low.find(s)
            if i != -1:
                best = i if best is None else min(best, i)
        if best is not None:
            found.append((key, best))
    found.sort(key=lambda x: x[1])
    return found

NUM_POS_RE = re.compile(r"\d{1,4}")
PO_QTY_RE = re.compile(r"\bпо\s*(\d{1,4})\b", re.IGNORECASE)

def detect_point4_items(text: str) -> List[Tuple[str, int]]:
    """Повертає список (CANON_COUNTRY, qty), якщо в одному повідомленні видно і країни, і кількості."""
    if not text:
        return []
    lows = text.lower()
    mentions = _country_mentions_with_pos(text)
    if not mentions:
        return []
    nums = [(m.group(0), m.start()) for m in NUM_POS_RE.finditer(text)]
    items: List[Tuple[str, int]] = []
    used_num_idx: Set[int] = set()
    used_country_idx: Set[int] = set()
    for ci, (country, cpos) in enumerate(mentions):
        best_idx = None
        best_dist = 9999
        for ni, (nstr, npos) in enumerate(nums):
            if ni in used_num_idx:
                continue
            dist = abs(npos - cpos)
            if dist <= 20 and dist < best_dist:
                best_dist = dist
                best_idx = ni
        if best_idx is not None:
            qty = int(nums[best_idx][0])
            items.append((country, qty))
            used_num_idx.add(best_idx)
            used_country_idx.add(ci)
    if len(items) == 0 or len(items) < len(mentions):
        m = PO_QTY_RE.search(text)
        if m:
            q = int(m.group(1))
            for ci, (country, _) in enumerate(mentions):
                if ci not in used_country_idx:
                    items.append((country, q))
    return items

# ==== СИСТЕМНІ ПРОМПТИ ====
# --- ПОЧАТОК ЗМІН ---
def build_system_prompt() -> str:
    return (
         # === РОЛЬ ТА КОНТЕКСТ ===
        "Ти — дружелюбний і корисний Telegram-бот в інтернет-магазині SIM-карт. Чітко та суворо дотримуйся прописаних інструкцій, якщо щось не зрозуміло, то не вигадуй, а краще перепитай клієнта що він мав на увазі.\n"
        "На початку чату клієнт уже отримує від акаунта власника перелік країн у наявності, цін на сім-карти цих країн та інформацію для доставки — ти це НЕ ДУБЛЮЄШ. Уважно все перевіряєш, якщо клієнт запитав про ціни, а ти бачиш, що перелік цін був в одному з останніх трьох повідомлень, то просто вказуєш, що перелік цін вище. Якщо перелік десь далеко, то надаєш клієнту перелік цін.\n"
        "Якщо клієнт питає про якусь конкретну країну, чи по якісь конкретні декілька країн, то ЗАВЖДИ надавай йому ціни на ці країни.\n"
        "Завжди аналізуй кожне повідомлення на наявність елементів замовлення (пункти 1-4 нижче). Якщо в повідомленні або історії є хоча б один пункт, починай процес збору даних: показуй, що бракує, або формуй JSON, якщо все є.\n"
        "Не чекай явного 'хочу оформити' — починай збір одразу при виявленні даних.\n\n"

        # === РОБОТА З REPLY/ЦИТАТАМИ ===
        "Якщо поточне повідомлення є відповіддю (reply) на інше — вважай текст процитованого повідомлення частиною актуальних даних і використовуй його для заповнення пунктів 1–3, якщо це доречно.\n\n"

        # === СТРУКТУРА ЗАМОВЛЕННЯ ===
        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище (Не плутай ім'я клієнта з по-батькові! Записуй лише імя та прізвище.).\n"
        "2. Номер телефону.\n"
        "3. Адреса доставки. Це може бути один з двох варіантів:\n"
        "   - Місто та № відділення «Нової Пошти».\n"
        "   - Повна адреса для кур'єрської доставки (вулиця, будинок, квартира). Якщо бачиш слова 'кв', 'офіс', 'адресна', 'курєрська' або повну адресу, вважай це кур'єрською доставкою.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # === ЯК ПИТАТИ ПРО НЕСТАЧУ ДАНИХ ===
        "Пункт 4 може бути у довільній формі/порядку («Англія 2 шт», «дві UK», «UK x2» тощо).\n"
        "Якщо клієнт каже, що дані для відправки ті самі, то спробуй знайти дані в попередніх повідомленях та використай їх, якщо не знайшов, уточни у клієнта потрібні дані.\n"
        "Якщо пункт 4 прийшов окремим повідомленням — поєднуй його з пунктами 1–3 з контексту.\n\n"
        "Якщо виявлено хоча б один пункт, але ще НЕ вказано ВСІ 4 — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо жодного пункту ще не виявлено, відповідай як на звичайне запитання, без чек-листа.\n\n"

        # === ФОРМАТ JSON ДЛЯ БЕКЕНДА ===
        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто або Повна Адреса",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n"
        "Правила для адреси в JSON: Для доставки на відділення, заповнюй 'city' та 'np'. Для кур'єрської доставки, записуй повну адресу в поле 'city' і залишай поле 'np' порожнім.\n\n"


        # === ПРАЙС/НАЯВНІСТЬ ===
        "Якщо користувач запитує ПРО ЦІНИ або про наявність — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_prices": true,\n'
        '  "countries": ["ALL" або перелік ключів, напр. "ВЕЛИКОБРИТАНІЯ","США"]\n'
        "}\n\n"

        # === ДОВІДКА USSD ===
        "Якщо користувач запитує, як дізнатися/перевірити свій номер на SIM — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_ussd": true,\n'
        '  "targets": [ {"country":"КРАЇНА","operator":"Опціонально: O2|Lebara|Vodafone|Movistar|Lycamobile|T-mobile|Kaktus"}, ... ]\n'
        "}\n"
        "Якщо країна не вказана — УТОЧНИ.\n\n"

        # === ДОСТУПНІ ДЛЯ ПРОДАЖУ ===
        "Для прайсу/наявності доступні ЛИШЕ: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "Не стверджуй наявність/ціну для інших країн (але довідку USSD можна давати і для інших, якщо відома комбінація).\n\n"

        # === СЕМАНТИКА ===
        "• Розумій країни за синонімами/містами/мовою (UK/United Kingdom/+44/Британія → ВЕЛИКОБРИТАНІЯ; USA/Америка/Штати → США).\n"
        "• Для items використовуй ключі: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Якщо клієнт для Англії називає оператора (O2, Lebara, Vodafone) — додай поле \"operator\" з канонічним значенням; інакше — не додавай це поле.\n"
        "• Текстові кількості (пара/десяток/кілька) перетворюй у число або попроси уточнення через пункт 4.\n\n"

        # === ЕСКАЛАЦІЯ ДО ЛЮДИНИ ===
        "Запити «зв’язатися з людиною/менеджером/оператором» — це звернення до МЕНЕДЖЕРА магазину. Відповідай: «Очікуйте відповіді менеджера.»\n"
        "Лише якщо явно питають про дзвінки через SIM — розповідай про поповнення/дзвінки.\n\n"

         # === VIP-номери ===
        "Інколи клієнти можуть запитувати про «гарні», «красиві», «VIP» номери з гарними цифрами.\n"
        "Відповідай, що в нас є такі номери, але лише по країні Англія. Коштують вони від 1500 грн до 2500 грн. Якщо цікаво, то менеджер надішле вам підбірку.\n\n" 

         # === Проблема з приймомо SMS ===
        "Іноді клієнти пишуть, що сім-карта не працює, або їм не приходить СМС. Ось яку інформацію в цьому випадку ти маєш їм відправити:\n\n" 
        "1. Перевірте чи підключена сім-карта до мережі. Якщо ні, спробуйте підключитись через налаштування в телефоні вручну (просто виберіть будь-яку мережу із запропонованих).\n\n" 
        "2. Якщо мережа є, але СМС не приходить, то це точно проблема на стороні сервісу, і потрібно спробувати пізніше, бажано через 24 години.\n\n" 
        "3. Щоб переконатись в тому, що сім-карта точно приймає СМС, можете спробувати відправити повідомлення з українського номеру.\n\n" 
        
        # === FAQ (КОРОТКО) ===
        "FAQ — використвуй цю інформацію для відповідей на ці, або дуже схожі, запитання, відповіді давай короткі та по суті:\n\n"
        "Як активувати SIM-карту?\n"
        "Просто вставте в телефон і почекайте поки сім-карта підключиться до мережі (або підключіться до мережі вручну через налаштування в телефоні).\n\n"
        "Чи зможу я зареєструвати месенджери?\n"
        "Так! Ви одразу зможете зареєструвати WhatsApp, Telegram, Viber та інші, а також прийняти SMS.\n\n"
        "Чи потрібно поповнювати?\n"
        "Сім-карта одразу працює на прийом SMS, поповнення потрібне лише для вхідних та вихідних дзвінків. Поповнити можна на сайті ding.com, використавши платіжку систему PayPal.\n\n"
        "Скільки SIM-карта буде активна?\n"
        "Зазвичай до півроку; щоб працювала довше — раз на 6 міс поповнюйте на 10 фунтів/євро.\n\n"
        "Які тарифи?\n"
        "По тарифам не консультуємо — дивіться сайт оператора.\n\n"
        "Чи є різниця між країнами та операторами?\n"
        "Принципової різниці немає: усі SIM зареєстровані (якщо країна вимагає) і працюють на прийом SMS.\n\n"
        "Це нові сім-карти?\n"
        "Так, нові, ніде не використовувались.\n\n"
        "Чи даєте гарантії?\n"
        "Якщо SIM не працюватиме (чого маже не трапляється), то ми зробимо заміну або повернемо кошти.\n\n"
        "Коли відправите?\n"
        "Зазвичай у день замовлення або протягом 24 годин.\n\n"
        "Чи вже відправили? Чи є ТТН/трек-номер?\n"
        "Зазвичай відправляємо в день замовлення або протягом 24 годин. Якщо потрібна ТТН — очікуйте відповіді менеджера.\n\n"
        "Як оплатити?\n"
        "Зазвичай накладений платіж. За бажанням — карта або USDT (TRC-20).\n\n"
        "Чи можлива відправка в інші країни?\n"
        "Так, від 3 шт, повна передоплата, «Нова Пошта».\n\n"
        "Який оператор для конкретної країни?\n"
        "Ти не пропонуєш операторів сам, тільки відповідаєш, коли клієнт сам конкретно запитує про оператора для країни. Якщо клієнт запитує про неіснуючого оператора для країни (наприклад, Vodafone для Німеччини, але є тільки Lebara), скажи, що в роботі оператор Lebara (або той, що є) нічим не відрізняється. Оператори для країн: Нідерланди - Lebara; Франція - Lebara; Іспанія - Lebara; Чехія - T-Mobile та Kaktus; Польща - Play (але потрібно уточнити у менеджера); Литва - Labas; Казахстан - Tele2; США - Lycamobile.\n\n"

        # === США — ОСОБЛИВО ===
        "США — на відміну від інших, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
        "Інструкцію надсилай дослівно (з відступами), якщо питають про США.\n"
        "Якщо користувача цікавить активація, поповнення або деталі використання SIM-карт США — ВІДПОВІДАЙ ЛИШЕ JSON-об'єктом: {\"ask_usa_activation\": true}"
    )
# --- КІНЕЦЬ ЗМІН ---

def build_followup_prompt() -> str:
    return (
        "Прайс або інше повідомлення щойно надіслано окремо. "
        "Відповідай КОРОТКО на інші частини останнього повідомлення, що НЕ стосуються вже надісланих даних.\n\n"
        "Якщо просили ЛИШЕ ціну/прайс — поверни порожній рядок. Не пиши шаблонні підтвердження наявності.\n\n"
        "Якщо користувач просить «зв’язатися з людиною/менеджером/оператором» — це звернення до МЕНЕДЖЕРА. Відповідай: «Очікуйте відповіді менеджера.»\n\n"
        "Якщо питають, як дізнатися/перевірити номер — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_ussd": true,\n'
        '  "targets": [ {"country":"КРАЇНА","operator":"Опціонально: O2|Lebara|Vodafone|Movistar|Lycamobile|T-mobile|Kaktus"}, ... ]\n'
        "}\n"
    )

def build_force_point4_prompt() -> str:
    return (
        "У контексті вже є пункти 1–3 (ПІБ, телефон, місто+№).\n"
        "Останнє повідомлення, включно з процитованим, ймовірно містить лише пункт 4 (країни та кількість).\n"
        "Витягни пункт 4, поєднай з 1–3 з контексту і ПОВЕРНИ ЛИШЕ ПОВНИЙ JSON замовлення."
    )

# ---- фільтр follow-up/«Ок/Дякую»
ACK_PATTERNS = [
    r"^\s*(ок(ей)?|добре|чудово|гарно|дякую!?|спасибі|спасибо|жду|чекаю|ок,?\s*жду|ок,?\s*чекаю|ого|ух\s*ты)\s*[\.\!]*\s*$",
    r"^\s*[👍🙏✅👌]+\s*$",
]
def is_ack_only(text: str) -> bool:
    if not text:
        return False
    low = text.strip().lower()
    for p in ACK_PATTERNS:
        if re.match(p, low):
            return True
    return False

def is_meaningful_followup(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    banned_words = ["ціни", "прайс", "надіслано", "див. вище", "вище", "повторю"]
    if any(w in low for w in banned_words):
        return False
    if "грн" in low or re.search(r"\bшт\.?\b", low):
        return False
    availability_patterns = [
        r"^підтверджую( наявність)?\.?$",
        r"^є в наявності\.?$",
        r"^в наявності\.?$",
        r"^available\.?$",
        r"^так, є\.?$",
        r"^так\.?$",
    ]
    for p in availability_patterns:
        if re.match(p, low):
            return False
    return len(t) >= 4

def _ensure_history(ctx: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, str]]:
    if "history" not in ctx.chat_data:
        ctx.chat_data["history"] = []
    return ctx.chat_data["history"]

def _prune_history(history: List[Dict[str, str]]) -> None:
    if len(history) > MAX_TURNS * 2:
        del history[: len(history) - MAX_TURNS * 2]

# ======== GPT-парсер для повідомлень менеджера ========
PAID_HINT_RE = re.compile(r"\b(без\s*нал|безнал|оплачено|передоплат|оплата\s*на\s*карт[уі])\b", re.IGNORECASE)

def build_manager_parser_prompt() -> str:
    country_keys = ", ".join(f'"{k}"' for k in PRICE_TIERS.keys())
    return (
        "Ти — сервіс для вилучення даних. Твоє завдання — розібрати неструктурований текст із даними замовлення та повернути їх у вигляді чіткого JSON-об'єкта.\n\n"
        "Вхідний текст буде містити (інколи дані будуть сильно розкидані, йти не по порядку, тому будь уважним):\n"
        "- ПІБ клієнта (Повне або часткове. Не плутай ім'я клієнта з по-батькові! Записуй лише імя та прізвище.).\n"
        "- Номер телефону.\n"
        "- Місто та номер відділення або поштомату «Нової Пошти».\n"
        "- Перелік замовлених SIM-карт (країна та кількість).\n"
        "- Назву оператора (напр. Three, Lebara, O2, Vodafone), зазвичай для Великобританії. Можуть вказуватись кирилицею, чи скорочено.\n"
        "- Сторонні коментарі, які потрібно ігнорувати.\n\n"
        "Правила:\n"
        "1. Твоя відповідь має бути ТІЛЬКИ JSON-об'єктом. Без жодних пояснень, тексту до чи після, чи markdown-форматування.\n"
        "2. Для країн використовуй СУВОРО канонічні назви з цього списку: " + country_keys + ".\n"
        "3. Якщо для країни ВЕЛИКОБРИТАНІЯ (Англія) вказаний оператор (напр. O2, Lebara, Vodafone, Three), додай в об'єкт товару поле `\"operator\"`. Використовуй канонічне значення: `\"O2\"`, `\"Lebara\"`, `\"Vodafone\"`, `\"Three\"`. Якщо оператор не вказаний, не додавай це поле.\n"
        "4. Поля `full_name`, `phone`, `city`, `np` мають бути рядками. Поле `items` — масивом об'єктів.\n"
        "5. Якщо якісь дані відсутні в тексті, залиш відповідне поле як порожній рядок \"\" або порожній масив [].\n\n"
        "Приклад:\n"
        "Вхідний текст: 'Так, це новий клієнт, Іван Франко, тел 0991234567. Хоче 2 сімки для Англії оператора водафон та 1 для США. Відправка в Київ, відділення 30. Каже, що оплатить на карту.'\n"
        "Твоя відповідь (лише цей JSON):\n"
        "{\n"
        '  "full_name": "Іван Франко",\n'
        '  "phone": "0991234567",\n'
        '  "city": "Київ",\n'
        '  "np": "30",\n'
        '  "items": [\n'
        '    {"country": "ВЕЛИКОБРИТАНІЯ", "qty": 2, "operator": "Vodafone"},\n'
        '    {"country": "США", "qty": 1}\n'
        '  ]\n'
        "}"
    )

async def _ask_gpt_to_parse_manager_order(text: str) -> str:
    """Використовує GPT для парсингу хаотичного тексту від менеджера в JSON."""
    messages = [
        {"role": "system", "content": build_manager_parser_prompt()},
        {"role": "user", "content": text}
    ]
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return response.choices[0].message["content"]
    except Exception as e:
        logger.error(f"Помилка GPT-парсера для менеджера: {e}")
        return ""

def try_parse_manager_order_json(json_text: str) -> Optional[OrderData]:
    """Парсить JSON-рядок від GPT у датаклас OrderData."""
    if not json_text:
        return None
    try:
        data = json.loads(json_text)
        if not all(k in data for k in ["full_name", "phone", "city", "np", "items"]):
            logger.warning("GPT-парсер повернув JSON без необхідних полів.")
            return None
        items = [OrderItem(
            country=i["country"],
            qty=int(i["qty"]),
            operator=i.get("operator")
        ) for i in data.get("items", [])]
        if not data.get("full_name") and not data.get("phone") and not items:
            logger.info("GPT-парсер не знайшов жодних суттєвих даних у тексті.")
            return None
        return OrderData(
            full_name=data.get("full_name", "").strip(),
            phone=data.get("phone", "").strip(),
            city=data.get("city", "").strip(),
            np=str(data.get("np", "")).strip(),
            items=items
        )
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
        logger.warning(f"Не вдалося розпарсити JSON від GPT-парсера: {e}\nТекст: {json_text}")
        return None

# --- ПОЧАТОК ЗМІН ---
def render_order_for_group(order: OrderData, paid: bool) -> str:
    """
    Спеціальний рендер для групи: без «дякуємо» та, якщо paid=True, замість ціни пише '(замовлення оплачене)'.
    """
    lines = []
    grand_total = 0
    counted = 0
    for it in order.items:
        c_norm = normalize_country(it.country)
        disp_base = DISPLAY.get(c_norm, it.country.strip().title())
        op = canonical_operator(getattr(it, "operator", None))
        op_suf = f" (оператор {op})" if (op and c_norm == "ВЕЛИКОБРИТАНІЯ") else ""
        disp = disp_base + op_suf
        flag = FLAGS.get(c_norm, "")
        if paid:
            line_total_str = "(замовлення оплачене)"
            line = f"{flag} {disp}, {it.qty} шт — {line_total_str}  \n"
        else:
            price = unit_price(c_norm, it.qty)
            if price is None:
                line_total_str = "договірна"
                line = f"{flag} {disp}, {it.qty} шт — {line_total_str}  \n"
            else:
                line_total = price * it.qty
                grand_total += line_total
                counted += 1
                line = f"{flag} {disp}, {it.qty} шт — {line_total} грн  \n"
        lines.append(line)

    # Умовне форматування адреси
    if order.np and order.np.strip():
        # Стандартна доставка на відділення
        address_line = f"{format_city(order.city)} № {format_np(order.np)}"
    else:
        # Кур'єрська/адресна доставка
        raw_address = order.city.strip()
        address_line = f"📫: {raw_address}"
        
    header = (
        f"{format_full_name(order.full_name)} \n"
        f"{format_phone(order.phone)}\n"
        f"{address_line}  \n\n"
    )
    footer = ""
    if not paid and counted >= 2:
        footer = f"\nЗагальна сумма: {grand_total} грн\n"
    return header + "".join(lines) + footer
# --- КІНЕЦЬ ЗМІН ---

# ==== OpenAI (основні функції) ====
async def _openai_chat(messages: List[Dict[str, str]]) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            max_tokens=600,
            temperature=0.2,
        )
        return response.choices[0].message["content"]
    except Exception as e:
        logger.error(f"Помилка при зверненні до OpenAI: {e}")
        return "Вибачте, сталася технічна помилка. Спробуйте, будь ласка, ще раз."

async def _ask_gpt_main(history: List[Dict[str, str]], user_payload: str) -> str:
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_payload})
    return await _openai_chat(messages)

async def _ask_gpt_followup(history: List[Dict[str, str]], user_payload: str) -> str:
    messages = [{"role": "system", "content": build_followup_prompt()}]
    tail = history[-4:] if len(history) > 4 else history[:]
    messages.extend(tail)
    messages.append({"role": "user", "content": user_payload})
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,
            temperature=0.2,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error(f"Помилка follow-up до OpenAI: {e}")
        return ""

async def _ask_gpt_force_point4(history: List[Dict[str, str]], user_payload: str) -> str:
    messages = [{"role": "system", "content": build_force_point4_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_payload})
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.1,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error(f"Помилка force-point4 до OpenAI: {e}")
        return ""

# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        logger.warning("No effective_message in /start update: %s", update)
        return
    await msg.reply_text(
        "Вітаю! Я допоможу вам оформити замовлення на SIM-карти, а також постараюсь надати відповіді на всі ваші запитання."
    )

# ===== Обробка повідомлень =====
PRICE_LINE_RE = re.compile(r"— (\d+ грн|договірна)")
TOTAL_LINE_RE = re.compile(r"^Загальна сумма: \d+ грн")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        logger.warning("No effective_message in update: %s", update)
        return
    raw_user_message = msg.text.strip() if msg.text else ""
    history = _ensure_history(context)
    
    # Видалено фільтр is_ack_only. Всі повідомлення йдуть на обробку.

    # Обробка команд менеджера в групі
    if (
        msg.chat and msg.chat.id == ORDER_FORWARD_CHAT_ID
        and msg.from_user and msg.from_user.username
        and msg.from_user.username.lower() == (DEFAULT_OWNER_USERNAME or "").strip().lstrip("@").lower()
    ):
        # Логіка обробки реплаїв від менеджера
        if msg.reply_to_message:
            is_paid_reply = bool(PAID_HINT_RE.search(raw_user_message))
            operator_reply = canonical_operator(raw_user_message)
            original_msg_id = msg.reply_to_message.message_id
            original_text = msg.reply_to_message.text or ""
            
            # Сценарій 1: Менеджер відповів "без нал" (або синонім)
            if is_paid_reply:
                modified_lines = []
                for line in original_text.splitlines():
                    if TOTAL_LINE_RE.search(line):
                        continue
                    modified_line = PRICE_LINE_RE.sub("— (замовлення оплачене)", line)
                    modified_lines.append(modified_line)
                final_text = "\n".join(modified_lines)
                try:
                    await context.bot.delete_message(chat_id=msg.chat.id, message_id=original_msg_id)
                    await context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
                except Exception as e:
                    logger.warning(f"Не вдалося видалити повідомлення при обробці оплати: {e}")
                await context.bot.send_message(chat_id=msg.chat.id, text=final_text)
                return

            # Сценарій 2: Менеджер відповів назвою оператора
            elif operator_reply:
                modified_lines = []
                found_uk = False
                for line in original_text.splitlines():
                    if ("Англія" in line or "ВЕЛИКОБРИТАНІЯ" in line) and "оператор" not in line:
                        # Вставляємо оператора після назви країни
                        line = line.replace(",", f" (оператор {operator_reply}),", 1)
                        found_uk = True
                    modified_lines.append(line)
                
                if found_uk:
                    final_text = "\n".join(modified_lines)
                    try:
                        await context.bot.delete_message(chat_id=msg.chat.id, message_id=original_msg_id)
                        await context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
                    except Exception as e:
                        logger.warning(f"Не вдалося видалити повідомлення при додаванні оператора: {e}")
                    await context.bot.send_message(chat_id=msg.chat.id, text=final_text)
                    return

        # Логіка створення нового замовлення з тексту (якщо це не реплай)
        note_text = None
        text_for_gpt = raw_user_message

        parts = re.split(r'\bпримітка[:\s]*', raw_user_message, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            text_for_gpt = parts[0]
            note_text = parts[1].strip()

        json_response_str = await _ask_gpt_to_parse_manager_order(text_for_gpt)
        parsed_order = try_parse_manager_order_json(json_response_str)
        if parsed_order:
            paid_flag = bool(PAID_HINT_RE.search(raw_user_message))
            try:
                await context.bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
            except Exception as e:
                logger.warning(f"Не вдалося видалити повідомлення менеджера: {e}")

            formatted = render_order_for_group(parsed_order, paid=paid_flag).strip()
            
            if note_text:
                formatted += f"\n\n⚠️ Примітка: {note_text}"

            await context.bot.send_message(chat_id=msg.chat.id, text=formatted)
            return
        else:
            logger.info("GPT-парсер не зміг структурувати повідомлення менеджера (або це не команда на створення замовлення).")
            return

    # Обробка повідомлень від клієнтів (далі без змін)
    if _is_manager_message(msg):
        text = (msg.text or msg.caption or "").strip()
        if text:
            history.append({"role": "assistant", "content": f"[Менеджер] {text}"})
            _prune_history(history)
        return

    quoted_text = extract_quoted_text(msg)
    user_payload = raw_user_message
    if quoted_text:
        user_payload = (
            raw_user_message
            + "\n\n[ЦЕ ПРОЦИТОВАНЕ ПОВІДОМЛЕННЯ КЛІЄНТА (вважай частиною актуальних даних):]\n"
            + quoted_text
        )

    last_price_countries: Optional[List[str]] = context.chat_data.get("last_price_countries")
    qty_only = detect_qty_only(raw_user_message)
    if qty_only and last_price_countries:
        hint_countries = ", ".join(last_price_countries)
        user_payload += (
            f"\n\n[ПІДСКАЗКА ДЛЯ ПАРСИНГУ: користувач вказав лише кількість {qty_only} шт. "
            f"Застосуй її до кожної з останніх країн, для яких щойно показували прайс: {hint_countries}. "
            f"Це відповідає пункту 4 (країна/кількість).]"
        )
        context.chat_data["awaiting_missing"] = {1, 2, 3}
        context.chat_data["point4_hint"] = {"qty": qty_only, "countries": last_price_countries, "ts": time.time()}

    p4_items = detect_point4_items(raw_user_message)
    if p4_items:
        pairs_text = "; ".join(f"{DISPLAY.get(c, c.title())} — {q}" for c, q in p4_items)
        user_payload += (
            f"\n\n[ПІДСКАЗКА ДЛЯ ПАРСИНГУ: пункт 4 уже заданий у цьому повідомленні: {pairs_text}. "
            f"Склей це з пунктами 1–3 з контексту та поверни ПОВНИЙ JSON.]"
        )
        context.chat_data["awaiting_missing"] = {1, 2, 3}
        context.chat_data["point4_hint"] = {"items": p4_items, "ts": time.time()}

    if context.chat_data.get("awaiting_missing") == {1, 2, 3} and context.chat_data.get("point4_hint"):
        h = context.chat_data["point4_hint"]
        if "qty" in h and "countries" in h:
            hc = ", ".join(h["countries"])
            user_payload += (
                f"\n\n[НАГАДУВАННЯ ДЛЯ ПАРСИНГУ: пункт 4 вже відомий: {hc} — по {h['qty']} шт. "
                f"Додай/склей із пунктами 1–3.]"
            )
        elif "items" in h:
            pairs_text = "; ".join(f"{DISPLAY.get(c, c.title())} — {q}" for c, q in h["items"])
            user_payload += (
                f"\n\n[НАГАДУВАННЯ ДЛЯ ПАРСИНГУ: пункт 4 вже відомий: {pairs_text}. "
                f"Додай/склей із пунктами 1–3.]"
            )

    awaiting = context.chat_data.get("awaiting_missing")
    if awaiting == {4}:
        force_json = await _ask_gpt_force_point4(history, user_payload)
        forced = try_parse_order_json(force_json)
        if forced and forced.items and all([forced.full_name, forced.phone, forced.city, forced.np]):
            summary = render_order(forced)
            sig = _order_signature(forced)
            context.chat_data["last_order_sig"] = sig
            context.chat_data["last_order_time"] = time.time()
            context.chat_data.pop("awaiting_missing", None)
            context.chat_data.pop("point4_hint", None)
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": summary})
            _prune_history(history)
            await msg.reply_text(summary)
            await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
            try:
                username = update.effective_user.username
                forward_text = f"@{username}\n{summary}" if username else summary
                await context.bot.send_message(
                    chat_id=ORDER_FORWARD_CHAT_ID,
                    text=forward_text
                )
            except Exception as e:
                logger.warning(f"Не вдалося надіслати замовлення в групу: {e}")
            return
    
    reply_text = await _ask_gpt_main(history, user_payload)

    if "Залишилось вказати:" in reply_text and "📝 Залишилось вказати:" not in reply_text:
        reply_text = reply_text.replace("Залишилось вказати:", "📝 Залишилось вказати:")

    if reply_text.strip().startswith("🛒 Для оформлення замовлення") and context.chat_data.get("awaiting_missing") == {1, 2, 3}:
        reply_text = (
            "📝 Залишилось вказати:\n\n"
            "1. Ім'я та прізвище.\n"
            "2. Номер телефону.\n"
            "3. Місто та № відділення \"Нової Пошти\".\n"
        )

    if try_parse_usa_activation_json(reply_text):
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": US_ACTIVATION_MSG})
        _prune_history(history)
        await msg.reply_text(US_ACTIVATION_MSG)
        return

    parsed = try_parse_order_json(reply_text)
    if parsed and parsed.items and all([parsed.full_name, parsed.phone, parsed.city, parsed.np]):
        current_sig = _order_signature(parsed)
        last_sig = context.chat_data.get("last_order_sig")
        last_time = context.chat_data.get("last_order_time", 0)
        if last_sig and current_sig == last_sig and (time.time() - last_time <= ORDER_DUP_WINDOW_SEC):
            # Оскільки фільтр is_ack_only видалено, додамо перевірку, щоб не спамити, якщо GPT повернув те саме замовлення на "дякую"
            logger.info(f"Проігноровано дублікат замовлення, ймовірно, на ACK-повідомлення: '{raw_user_message}'")
            context.chat_data.pop("awaiting_missing", None)
            context.chat_data.pop("point4_hint", None)
            return
        summary = render_order(parsed)
        context.chat_data["last_order_sig"] = current_sig
        context.chat_data["last_order_time"] = time.time()
        context.chat_data.pop("awaiting_missing", None)
        context.chat_data.pop("point4_hint", None)
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": summary})
        _prune_history(history)
        await msg.reply_text(summary)
        await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
        try:
            username = update.effective_user.username
            forward_text = f"@{username}\n{summary}" if username else summary
            await context.bot.send_message(
                chat_id=ORDER_FORWARD_CHAT_ID,
                text=forward_text
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати замовлення в групу: {e}")
        return

    price_countries = try_parse_price_json(reply_text)
    if price_countries is not None:
        want_all = any(str(c).upper() == "ALL" for c in price_countries)
        normalized = [normalize_country(str(c)).upper() for c in price_countries if str(c).strip()]
        valid = [k for k in normalized if k in PRICE_TIERS]
        invalid = [price_countries[i] for i, k in enumerate(normalized)
                   if k not in PRICE_TIERS and str(price_countries[i]).upper() != "ALL"]
        if want_all:
            context.chat_data["last_price_countries"] = list(PRICE_TIERS.keys())
        else:
            context.chat_data["last_price_countries"] = valid[:] if valid else []
        if want_all:
            price_msg = "".join(render_price_block(k) for k in PRICE_TIERS.keys())
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": price_msg})
            _prune_history(history)
            await msg.reply_text(price_msg)
        elif valid:
            price_msg = render_prices(valid)
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": price_msg})
            _prune_history(history)
            await msg.reply_text(price_msg)
            if invalid:
                await msg.reply_text(render_unavailable(invalid))
        else:
            unavailable_msg = render_unavailable(invalid if invalid else price_countries)
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": unavailable_msg})
            _prune_history(history)
            await msg.reply_text(unavailable_msg)
        
        follow = await _ask_gpt_followup(history, user_payload)
        ussd_targets_followup = try_parse_ussd_json(follow)
        if ussd_targets_followup:
            formatted = render_ussd_targets(ussd_targets_followup) or FALLBACK_PLASTIC_MSG
            history.append({"role": "assistant", "content": formatted})
            _prune_history(history)
            context.chat_data.pop("awaiting_missing", None)
            context.chat_data.pop("point4_hint", None)
            await msg.reply_text(formatted)
            return
        if is_meaningful_followup(follow):
            if not contains_us_activation_block(follow):
                history.append({"role": "assistant", "content": follow})
                _prune_history(history)
                await msg.reply_text(follow)
        context.chat_data.pop("awaiting_missing", None)
        return

    ussd_targets = try_parse_ussd_json(reply_text)
    if ussd_targets:
        formatted = render_ussd_targets(ussd_targets) or FALLBACK_PLASTIC_MSG
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": formatted})
        _prune_history(history)
        context.chat_data.pop("awaiting_missing", None)
        context.chat_data.pop("point4_hint", None)
        await msg.reply_text(formatted)
        return

    missing = missing_points_from_reply(reply_text)
    if missing == {4}:
        context.chat_data["awaiting_missing"] = {4}
    elif missing:
        context.chat_data["awaiting_missing"] = missing
    else:
        if context.chat_data.get("awaiting_missing") != {1, 2, 3}:
            context.chat_data.pop("awaiting_missing", None)
    
    # Захист від відправки пустої відповіді від GPT
    if reply_text:
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": reply_text})
        _prune_history(history)
        await msg.reply_text(reply_text)
    else:
        logger.info(f"GPT повернув пусту відповідь на повідомлення: '{raw_user_message}'. Відповідь не надіслано.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling update: %s", update, exc_info=context.error)

def main():
    if not TELEGRAM_TOKEN or not OPENAI_API_KEY or not WEBHOOK_URL:
        raise RuntimeError("Не задано TELEGRAM_BOT_TOKEN, OPENAI_API_KEY або WEBHOOK_URL")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
