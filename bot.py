# bot.py
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
        return f"На жаль, {names[0]} SIM-карти наразі недоступні. У наявності: {available_list_text()}."
    return f"На жаль, {', '.join(names)} наразі недоступні. У наявності: {available_list_text()}."

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

    header = (
        f"{format_full_name(order.full_name)} \n"
        f"{format_phone(order.phone)}\n"
        f"{format_city(order.city)} № {format_np(order.np)}  \n\n"
    )
    body = "".join(lines) + "\n"
    footer = f"Загальна сумма: {grand_total} грн\n" if counted_countries >= 2 else ""
    return header + body + footer

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
    "США, на відміну від інших, потребують поповнення для активації.\n"
    "Після поповнення SIM працюватиме на прийом SMS.\n\n"
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
def build_system_prompt() -> str:
    return (
        "Ти — дружелюбний і корисний Telegram-бот-магазин SIM-карт. Чітко та суворо дотримуйся прописаних інструкцій, якщо щось не зрозуміло, то не вигадуй, а краще перепитай клієнта що він мав на увазі.\n"
        "На початку чату клієнт уже отримує від акаунта власника перелік країн у наявності та інформацію для доставки — ти це НЕ ДУБЛЮЄШ.\n"
        "Завжди аналізуй кожне повідомлення на наявність елементів замовлення (пункти 1-4 нижче). Якщо в повідомленні або історії є хоча б один пункт, починай процес збору даних: показуй, що бракує, або формуй JSON, якщо все є.\n"
        "Не чекай явного 'хочу оформити' — починай збір одразу при виявленні даних.\n\n"
        "Якщо поточне повідомлення є відповіддю (reply) на інше — вважай текст процитованого повідомлення частиною актуальних даних і використовуй його для заповнення пунктів 1–4, якщо це доречно.\n\n"
        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення «Нової Пошти».\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Пункт 4 може бути у довільній формі/порядку («Англія 2 шт», «дві UK», «UK x2» тощо).\n"
        "Якщо пункт 4 прийшов окремим повідомленням — поєднуй його з пунктами 1–3 з контексту.\n\n"
        "Якщо виявлено хоча б один пункт, але ще НЕ вказано ВСІ 4 — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо жодного пункту ще не виявлено, відповідай як на звичайне запитання, без чек-листа.\n\n"
        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n\n"
        "Якщо користувач запитує ПРО ЦІНИ або про наявність — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_prices": true,\n'
        '  "countries": ["ALL" або перелік ключів, напр. "ВЕЛИКОБРИТАНІЯ","США"]\n'
        "}\n\n"
        "Якщо користувач запитує, як дізнатися/перевірити свій номер на SIM — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_ussd": true,\n'
        '  "targets": [ {"country":"КРАЇНА","operator":"Опціонально: O2|Lebara|Vodafone|Movistar|Lycamobile|T-mobile|Kaktus"}, ... ]\n'
        "}\n"
        "Якщо країна не вказана — УТОЧНИ.\n\n"
        "Для прайсу/наявності доступні ЛИШЕ: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "Не стверджуй наявність/ціну для інших країн (але довідку USSD можна давати і для інших, якщо відома комбінація).\n\n"
        "• Розумій країни за синонімами/містами/мовою (UK/United Kingdom/+44/Британія → ВЕЛИКОБРИТАНІЯ; USA/Америка/Штати → США).\n"
        "• Для items використовуй ключі: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Якщо клієнт для Англії називає оператора (O2, Lebara, Vodafone) — додай поле \"operator\" з канонічним значенням; інакше — не додавай це поле.\n"
        "• Текстові кількості (пара/десяток/кілька) перетворюй у число або попроси уточнення через пункт 4.\n\n"
        "Запити «зв’язатися з людиною/менеджером/оператором» — це звернення до МЕНЕДЖЕРА магазину. Відповідай: «Очікуйте відповіді менеджера.»\n"
        "Лише якщо явно питають про дзвінки через SIM — розповідай про поповнення/дзвінки.\n\n"
        "Після JSON бекенд сам рахує суми та формує підсумок. «Загальна сумма» показується лише якщо країн 2+.\n\n"
        "FAQ — використвуй цю інформацію для відповідей на ці, або дуже схожі, запитання, відповіді давай короткі та по суті:\n\n"
        "Як активувати SIM-карту?\n"
        "Просто вставте в телефон і почекайте поки сім-карта підключиться до мережі (або підключіться до мережі вручну через налаштування в телефоні).\n\n"
        "Чи зможу я зареєструвати месенджери?\n"
        "Так! Ви одразу зможете зареєструвати WhatsApp, Telegram, Viber та інші, а також прийняти SMS.\n\n"
        "Чи потрібно поповнювати?\n"
        "Ні. Сім-карта працює на прийом SMS, але для дзвінків потрібне поповнення (ding.com, PayPal).\n\n"
        "Скільки SIM-карта буде активна?\n"
        "Зазвичай до півроку; щоб довше — раз на 6 міс поповнюйте на 10 фунтів/євро.\n\n"
        "Які тарифи?\n"
        "По тарифам не консультуємо — дивіться сайт оператора.\n\n"
        "Чи є різниця між країнами та операторами?\n"
        "Принципової різниці немає: усі SIM зареєстровані (якщо країна вимагає) і працюють на прийом SMS.\n\n"
        "Це нові сім-карти?\n"
        "Так, нові, не використовувались.\n\n"
        "Чи даєте гарантії?\n"
        "Якщо SIM не працюватиме (рідко), зробимо заміну або повернемо кошти.\n\n"
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
        "# === США — ОСОБЛИВО ===\n"
        "США — на відміну від інших, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
        "Якщо користувача цікавить активація, поповнення або деталі використання SIM-карт США — ВІДПОВІДАЙ ЛИШЕ JSON-об'єктом: {\"ask_usa_activation\": true}"
    )

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
    r"^\s*(ок(ей)?|добре|чудово|гарно|дякую!?|спасибі|спасибо|жду|чекаю|ок,?\s*жду|ок,?\s*чекаю)\s*[\.\!]*\s*$",
    r"^\s*[👍🙏✅👌]+\s*$",
]
def is_ack_only(text: str) -> bool:
    if not text:
        return False
    if re.match(r"^\s*ух\s*ты\b", text.strip().lower()):
        return True
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
