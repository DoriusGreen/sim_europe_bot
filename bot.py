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

# ===== Конфіг =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")

# Група, куди відправляються замовлення
ORDER_GROUP_CHAT_ID = int(os.getenv("ORDER_GROUP_CHAT_ID", "-1001234567890"))

# Менеджери (можуть реплаяти “Оплачено”, вказувати оператора, тощо)
MANAGER_USER_IDS: Set[int] = set()
MANAGER_USERNAMES: Set[str] = set()

# ===== OpenAI ключ =====
openai.api_key = OPENAI_API_KEY

# ===== Допоміжні типи =====
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

# ===== Стандартні повідомлення =====
ORDER_INFO_REQUEST = (
    "🛒 Для оформлення замовлення напишіть:\n\n"
    "1. Ім'я та прізвище.\n"
    "2. Номер телефону.\n"
    "3. Адреса для курʼєрської (або місто та № відділення \"Нової Пошти\").\n"
    "4. Країна(и) та кількість sim-карт."
)

# ==== Прайси й мапи країн (ДОСТУПНІ ТІЛЬКИ ЦІ) ====
PRICE_TIERS = {
    "ВЕЛИКОБРИТАНІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "НІДЕРЛАНДИ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ІСПАНІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ПОРТУГАЛІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ІТАЛІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ФРАНЦІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "НИМЕЧЧИНА": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ГЕРМАНІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "БЕЛЬГІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ЧЕХІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ПОЛЬЩА": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ЛИТВА": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "ЛАТВІЯ": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "КАЗАХСТАН": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "МАРОККО": [(1000, None), (100, 250), (20, 300), (1, 350)],
    "США": [(1000, None), (100, 250), (20, 300), (1, 350)],
}

FLAGS = {
    "ВЕЛИКОБРИТАНІЯ": "🇬🇧",
    "НІДЕРЛАНДИ": "🇳🇱",
    "ІСПАНІЯ": "🇪🇸",
    "ПОРТУГАЛІЯ": "🇵🇹",
    "ІТАЛІЯ": "🇮🇹",
    "ФРАНЦІЯ": "🇫🇷",
    "НИМЕЧЧИНА": "🇩🇪",
    "ГЕРМАНІЯ": "🇩🇪",
    "БЕЛЬГІЯ": "🇧🇪",
    "ЧЕХІЯ": "🇨🇿",
    "ПОЛЬЩА": "🇵🇱",
    "ЛИТВА": "🇱🇹",
    "ЛАТВІЯ": "🇱🇻",
    "КАЗАХСТАН": "🇰🇿",
    "МАРОККО": "🇲🇦",
    "США": "🇺🇸",
}

DISPLAY = {
    "ВЕЛИКОБРИТАНІЯ": "Англія",
    "НИМЕЧЧИНА": "Німеччина",
    "ГЕРМАНІЯ": "Німеччина",
}

# ==== Перевірки доступності країн ====
def is_country_supported(country: str) -> bool:
    return country in PRICE_TIERS

# ==== Нормалізація вводу ====
def _smart_title(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    return s[0].upper() + s[1:]

def normalize_country(n: str) -> str:
    n = (n or "").strip().upper()
    if n in ("ENGLAND", "UNITED KINGDOM", "UK", "U.K.", "GREAT BRITAIN", "BRITAIN", "АНГЛІЯ", "АНГЛИЯ", "БРИТАНІЯ", "БРИТАНИЯ"):
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
    if o in ("o2", "о2", "o-2"):
        return "O2"
    if o in ("lebara", "лебара", "лебараа"):
        return "Lebara"
    if o in ("vodafone", "водафон", "водафонн"):
        return "Vodafone"
    if o in ("three", "3", "три"):
        return "Three"
    return None

# ==== Форматери ====
def format_full_name(name: str) -> str:
    return _smart_title(name)

def format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("380") and len(digits) == 12:
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

# ---------- Адресна/курʼєрська доставка ----------
ADDRESS_HINT_RE = re.compile(
    r"(кур'?єр|курьер|адресн|на адресу|адресу|вул\.|вулиц|просп|бульвар|бул\.|пров\.|буд\.|будинок|кв\.?|квартира|площа|село|смт|місто|м\.)",
    re.IGNORECASE
)

def is_address_delivery(city: str, np_str: str) -> bool:
    """Грубе евристичне визначення, що користувач вказав адресу для курʼєрської доставки."""
    t = f"{city or ''} {np_str or ''}".lower()
    if ADDRESS_HINT_RE.search(t):
        return True
    # Якщо дуже схоже на адресу (довгий текст і не просто номер відділення)
    np_raw = (np_str or "").strip()
    digits_only = re.sub(r"[^\d]", "", np_raw)
    if len(np_raw) >= 10 and not re.fullmatch(r"\d{1,4}", digits_only):
        return True
    return False

def format_delivery_line(city: str, np_str: str) -> str:
    """Повертає рядок для 3-го пункту: або 📫: <сира адреса>, або 'Місто № Відділення'."""
    if is_address_delivery(city, np_str) or not (city or "").strip():
        raw = (np_str or city or "").strip()
        raw = re.sub(r"\s+", " ", raw)
        return f"📫: {raw}"
    return f"{format_city(city)} № {format_np(np_str)}"

ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

@dataclass
class OrderItem:
    country: str
    qty: int
    operator: Optional[str] = None

# ==== Прайс-логіка ====
def unit_price(country: str, qty: int) -> Optional[int]:
    tiers = PRICE_TIERS.get(country)
    if not tiers:
        return None
    for min_qty, price in tiers:
        if min_qty is None:
            return price
        if qty >= min_qty:
            return price
    return None

# ==== Порівняння замовлень (антидубль) ====
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
        f"{format_delivery_line(order.city, order.np)}  \n\n"
    )
    body = "".join(lines) + "\n"
    footer = f"Загальна сума: {grand_total} грн\n" if counted_countries >= 2 else ""
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

def try_parse_price_json(text: str) -> Optional[List[str]]:
    m = PRICE_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("ask_prices") is True and isinstance(data.get("countries"), list):
            out = []
            for c in data["countries"]:
                cc = normalize_country(str(c))
                if cc == "ALL":
                    return ["ALL"]
                out.append(cc)
            return out
        return None
    except Exception:
        return None

def try_parse_ussd_json(text: str) -> Optional[List[Tuple[str, Optional[str]]]]:
    m = USSD_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if data.get("ask_ussd") is True and isinstance(data.get("targets"), list):
            out: List[Tuple[str, Optional[str]]] = []
            for t in data["targets"]:
                country = normalize_country(str(t.get("country", "")))
                op = canonical_operator(t.get("operator"))
                out.append((country, op))
            return out
        return None
    except Exception:
        return None

def try_parse_order_json(text: str) -> Optional[OrderData]:
    m = ORDER_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if not isinstance(data.get("items"), list) or not data["items"]:
            return None
        items: List[OrderItem] = []
        for it in data["items"]:
            country = normalize_country(str(it.get("country", "")))
            try:
                qty = int(it.get("qty", 0))
            except Exception:
                qty = 0
            if qty <= 0:
                return None
            op = canonical_operator(it.get("operator"))
            items.append(OrderItem(country=country, qty=qty, operator=op))

        return OrderData(
            full_name=str(data.get("full_name", "")).strip(),
            phone=str(data.get("phone", "")).strip(),
            city=str(data.get("city", "")).strip(),
            np=str(data.get("np", "")).strip(),
            items=items
        )
    except Exception as e:
        logger.warning(f"Не вдалося розпарсити JSON замовлення: {e}")
        return None

# ==== Ціни (рендер) ====
def render_prices(countries: List[str]) -> str:
    if countries and countries[0] == "ALL":
        keys = list(PRICE_TIERS.keys())
    else:
        keys = [c for c in countries if c in PRICE_TIERS]
    if not keys:
        return "На жаль, цієї країни зараз немає в наявності."

    lines: List[str] = []
    for c in keys:
        tiers = PRICE_TIERS[c]
        flag = FLAGS.get(c, "")
        disp = DISPLAY.get(c, c.title())
        prices: List[str] = []
        for min_qty, price in tiers:
            if min_qty is None:
                continue
            prices.append(f"{min_qty}+ — {price} грн")
        if any(t[1] is None for t in tiers):
            prices.append("договірна")
        lines.append(f"{flag} {disp}: " + ", ".join(prices))
    return "\n".join(lines)

# ==== USSD (довідка) ====
USSD_DICT: Dict[str, Dict[Optional[str], str]] = {
    "ВЕЛИКОБРИТАНІЯ": {
        None: "*#100#",
        "O2": "*#100#",
        "Lebara": "*#100#",
        "Vodafone": "*#100#",
        "Three": "*#100#",
    },
    "ІСПАНІЯ": {None: "*#102#"},
    "ПОЛЬЩА": {None: "*100#"},
    "ЧЕХІЯ": {None: "*#62#"},
    "ФРАНЦІЯ": {None: "*100#"},
    "США": {None: "*#611#"},
}

def render_ussd(targets: List[Tuple[str, Optional[str]]]) -> str:
    lines: List[str] = []
    for country, op in targets:
        m = USSD_DICT.get(country, {})
        code = m.get(op) or m.get(None)
        if not code:
            disp = DISPLAY.get(country, country.title())
            if op:
                lines.append(f"{DISPLAY.get(country, country.title())} ({op}): немає довідки.")
            else:
                lines.append(f"{disp}: немає довідки.")
        else:
            if op:
                lines.append(f"{DISPLAY.get(country, country.title())} ({op}): {code}")
            else:
                lines.append(f"{DISPLAY.get(country, country.title())}: {code}")
    return "\n".join(lines)

# ==== США: інструкція та детект ====
US_ACTIVATION_MSG = (
    "США, на відміну від інших країн, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
    "Як активувати та поповнити сім-карту США?\n\n"
    "https://www.lycamobile.us/en/activate-sim\n"
    "https://www.lycamobile.us/en/faq/\n\n"
    "Після активації ви зможете отримати SMS на цю карту."
)

# ==== Менеджери ====
def is_manager(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return False
    if MANAGER_USER_IDS and u.id in MANAGER_USER_IDS:
        return True
    if MANAGER_USERNAMES and u.username and u.username.lower() in MANAGER_USERNAMES:
        return True
    return False

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
def is_usa_activation_request(text: str) -> bool:
    t = (text or "").lower()
    if "інструкц" in t and "сша" in t:
        return True
    if "активац" in t and "сша" in t:
        return True
    if "activate" in t and "usa" in t:
        return True
    return False

# ==== Відповіді на прайси ====
def _countries_from_text(text: str) -> List[str]:
    # Дуже груба евристика: шукаємо відомі ключі
    t = (text or "").upper()
    out: List[str] = []
    for k in PRICE_TIERS.keys():
        if k in t:
            out.append(k)
    synonyms = {
        "АНГЛ": "ВЕЛИКОБРИТАНІЯ",
        "ENGL": "ВЕЛИКОБРИТАНІЯ",
        "UK": "ВЕЛИКОБРИТАНІЯ",
        "USA": "США",
        "US": "США",
        "AMERICA": "США",
        "NEM": "НИМЕЧЧИНА",
        "GERMANY": "НИМЕЧЧИНА",
        "DEUTSCH": "НИМЕЧЧИНА",
        "ITAL": "ІТАЛІЯ",
        "ESP": "ІСПАНІЯ",
        "POL": "ПОЛЬЩА",
        "CZE": "ЧЕХІЯ",
        "FR": "ФРАНЦІЯ",
        "PT": "ПОРТУГАЛІЯ",
        "NL": "НІДЕРЛАНДИ",
    }
    for k, v in synonyms.items():
        if k in t and v not in out:
            out.append(v)
    return out

# ==== Системні промпти ====
def build_system_prompt() -> str:
    return (
         # === РОЛЬ ТА КОНТЕКСТ ===
        "Ти — дружелюбний і корисний Telegram-бот в інтернет-магазині SIM-карт. Чітко та суворо дотримуйся прописаних інструкцій нижче. Коли сумніваєшся — не вигадуй, а краще перепитай клієнта що він мав на увазі.\n"
        "На початку чату клієнт уже отримує від акаунта власника повідомлення про перелік країн і ціни на них. Якщо клієнт запитує «ціни», «набори», «скільки коштує» — відповідаєш цінами на країни, які він назвав. Якщо перелік десь далеко, то надаєш клієнту перелік цін.\n"
        "Якщо клієнт питає про якусь конкретну країну, чи по якійсь кількості, або просить зробити підбір (наприклад, «можете порекомендувати, щоб приймала SMS банку для Англії/Німеччини?»), або називає одразу декілька країн, то ЗАВЖДИ надавай йому ціни на ці країни.\n"
        "Завжди аналізуй кожне повідомлення на наявність елементів замовлення, і паралельно з відповідями запускати збір даних: показуй, що бракує, або формуй JSON, якщо все є.\n"
        "Не чекай явного 'хочу оформити' — починай збір одразу при виявленні даних.\n\n"

        # === РОБОТА З REPLY/ЦИТАТАМИ ===
        "Якщо поточне повідомлення є відповіддю (reply) на інше повідомлення клієнта — завжди враховуй і текст повідомлення, на яке клієнт відповів (цитує), зважаючи на контекст. Якщо там є дані для оформлення — використовуй його для заповнення пунктів 1–3, якщо це доречно.\n\n"

        # === СТРУКТУРА ЗАМОВЛЕННЯ ===
        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище (Не плутай ім'я клієнта з по-батькові! Записуй лише імя та прізвище.).\n"
        "2. Номер телефону.\n"
        "3. Адреса для курʼєрської (або місто та № відділення «Нової Пошти»).\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # === ЯК ПИТАТИ ПРО НЕСТАЧУ ДАНИХ ===
        "Пункт 4 може бути у довільній формі/порядку («Англія 2 шт», «2 Англії», «2 UK», «O2 2» і т.д.). Ти повинен це зрозуміти. Якщо клієнт називає тільки країну/кількість — не роздавай зайвої інформації, просто додай її в чернетку замовлення і продовжуй збір.\n"
        "Якщо виявлено хоча б один пункт, але ще НЕ вказано ВСІ (з пунктів 1–3), то відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо жодного пункту не виявлено — дай «🛒 Для оформлення замовлення…» з пунктами 1–4. Не пиши нічого зайвого до/після. Якщо користувач уже писав щось із 1–3 — не дублюй «🛒 Для оформлення…», а покажи лише «📝 Залишилось вказати».\n\n"

        # === ЯК ФОРМУВАТИ ЗАМОВЛЕННЯ ===
        "Якщо всі чотири пункти є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення/поштомату АБО ПОВНА адреса для курʼєрської",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n\n"
        "Якщо клієнт просить курʼєрську/адресну доставку — НЕ структуруй пункт 3. Запиши повну адресу так, як написав клієнт, у поле \"np\" (поле \"city\" можна лишити порожнім).\n\n"

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
        "Для прайсу/наявності доступні ЛИШЕ: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, ІСПАНІЯ, ПОРТУГАЛІЯ, ІТАЛІЯ, ФРАНЦІЯ, НИМЕЧЧИНА, БЕЛЬГІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "Не стверджуй наявність/ціну для інших країн (але довідку USSD можна давати і для інших, якщо відома комбінація).\n\n"

        # === СЕМАНТИКА ===
        "Не дублюй ціни якщо вже надав їх у попередньому повідомленні або вони дуже близько в чаті. Уникай зайвої балаканини. Відповідай максимально стисло, але інформативно.\n"
        "Якщо клієнт задає будь-яке питання і в його тексті присутні дані для замовлення — все одно додавай їх у чернетку замовлення і паралельно відповідай на питання.\n"
        "Коли виникає двозначність — не вигадуй, перепитай.\n\n"

        # === Часті питання (FAQ) ===
        "Чи приймають SIM банківські SMS?\n"
        "Так, ми спеціально добираємо операторів, що приймають SMS банків. Якщо у вас специфічний банк — уточніть, і ми підкажемо оператора.\n\n"
        "Lebara чи O2 — що краще?\n"
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
        "Ти не пропонуєш операторів сам, тільки відповідаєш, коли конкретно запитають. Для Англії (UK) можливі O2, Lebara, Vodafone, Three; якщо клієнт питає «який буде оператор?» — не вигадуй, а скажи, що в роботі оператор Lebara (або той, що є) нічим не гірший.\n"
    )

# ==== OpenAI (основні функції) ====
async def _openai_chat(messages: List[Dict[str, str]]) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            temperature=0.2,
            max_tokens=800
        )
        return response.choices[0].message["content"]
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return ""

async def _ask_gpt_main(history: List[Dict[str, str]], user_payload: Dict[str, Any]) -> str:
    sys_prompt = build_system_prompt()
    messages = [{"role": "system", "content": sys_prompt}] + history
    return await _openai_chat(messages)

# ==== Менеджерські хелпери ====
def _is_ack_only(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"ок", "окей", "дякую", "дякую!", "дяка", "thx", "thanks", "tx", "спс", "спасибо", "дякую.", "✅", "👍", "дякую❤️", "дякую дуже"} or t.endswith("дякую") or t.endswith("дякую!")

# ==== Антидубль (вікно 20 хв) ====
_last_order_signature: Dict[int, Tuple[str, float]] = {}

def is_duplicate_order(chat_id: int, order: OrderData) -> bool:
    sig = _order_signature(order)
    now = time.time()
    last = _last_order_signature.get(chat_id)
    if last and last[0] == sig and now - last[1] <= 20 * 60:
        return True
    _last_order_signature[chat_id] = (sig, now)
    return False

# ==== Рендер для групи ====
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
    header = (
        f"{format_full_name(order.full_name)} \n"
        f"{format_phone(order.phone)}\n"
        f"{format_delivery_line(order.city, order.np)}  \n\n"
    )
    footer = ""
    if not paid and counted >= 2:
        footer = f"\nЗагальна сума: {grand_total} грн\n"
    return header + "".join(lines) + footer

# ==== OpenAI (основні функції) ====
async def _openai_prices(history: List[Dict[str, str]]) -> str:
    sys_prompt = build_system_prompt()
    messages = [{"role": "system", "content": sys_prompt}] + history
    return await _openai_chat(messages)

# ==== Головний хендлер ====
PRICE_LINE_RE = re.compile(r"— (\d+ грн|договірна)")
TOTAL_LINE_RE = re.compile(r"^Загальна сума: \d+ грн")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        logger.warning("No message")
        return
    raw_user_message = msg.text or msg.caption or ""
    if not raw_user_message:
        return

    # Ігнор «дякую/ок» (уникаємо зайвих відповідей)
    if _is_ack_only(raw_user_message):
        return

    # Менеджерські тригери в групі
    if is_manager(update) and update.effective_chat and update.effective_chat.id == ORDER_GROUP_CHAT_ID:
        await handle_manager_command(update, context)
        return

    # Історія для GPT
    history: List[Dict[str, str]] = []
    # Витяг reply-текста, якщо є
    if msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        reply_text = msg.reply_to_message.text or msg.reply_to_message.caption
        history.append({"role": "user", "content": reply_text})
    history.append({"role": "user", "content": raw_user_message})

    # 1) Спец-кейс: інструкція для США
    if is_usa_activation_request(raw_user_message):
        await msg.reply_text(US_ACTIVATION_MSG)
        return

    # 2) Спроба зчитати JSON-прайс / JSON-USSD / JSON-замовлення
    reply_text = await _ask_gpt_main(history, {})
    # Нормалізуємо префікс чек-листа, якщо GPT забув емодзі
    if "Залишилось вказати:" in reply_text and "📝 Залишилось вказати:" not in reply_text:
        reply_text = reply_text.replace("Залишилось вказати:", "📝 Залишилось вказати:")

    # Якщо GPT повернув US-інструкцію через JSON
    if try_parse_usa_activation_json(reply_text):
        await msg.reply_text(US_ACTIVATION_MSG)
        return

    # Якщо запитували ціни
    price_countries = try_parse_price_json(reply_text)
    if price_countries:
        prices = render_prices(price_countries)
        await msg.reply_text(prices)
        return

    # Якщо запитували USSD-коди
    ussd_targets = try_parse_ussd_json(reply_text)
    if ussd_targets:
        ussd = render_ussd(ussd_targets)
        await msg.reply_text(ussd)
        return

    # Якщо GPT повернув повний JSON замовлення
    order = try_parse_order_json(reply_text)
    if order and order.items:
        if is_duplicate_order(update.effective_chat.id, order):
            await msg.reply_text("Дякуємо! Замовлення вже зафіксоване. Очікуйте на відповідь менеджера.")
            return

        # Рендер підсумку для клієнта
        summary = render_order(order)
        await msg.reply_text(summary)

        # Надсилаємо в групу (без «дякуємо» і з можливістю «оплачено»)
        try:
            await context.bot.send_message(
                chat_id=ORDER_GROUP_CHAT_ID,
                text=render_order_for_group(order, paid=False),
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.warning(f"Не вдалося надіслати замовлення в групу: {e}")
        return

    # Якщо GPT повернув «інформаційне прелоад» замість чек-листа (інколи)
    if reply_text.strip().startswith("🛒 Для оформлення замовлення") and context.chat_data.get("awaiting_missing") == {1, 2, 3}:
        reply_text = (
            "📝 Залишилось вказати:\n\n"
            "1. Ім'я та прізвище.\n"
            "2. Номер телефону.\n"
            "3. Адреса для курʼєрської (або місто та № відділення \"Нової Пошти\").\n"
        )

    # Якщо нічого не вийшло — повертаємо, що дав GPT (можливо, це чек-лист)
    await msg.reply_text(reply_text)

# ==== Менеджерські дії у групі ====
async def handle_manager_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return
    text = (msg.text or "").strip().lower()

    # Позначити оплату
    if "оплачено" in text or "без нал" in text or "безнал" in text:
        await mark_paid(update, context)
        return

    # Встановити оператора для UK
    for op_key, op_canon in [("o2", "O2"), ("lebara", "Lebara"), ("vodafone", "Vodafone"), ("three", "Three")]:
        if op_key in text:
            await set_operator_for_uk(update, context, op_canon)
            return

async def mark_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_msg = update.effective_message.reply_to_message
    if not reply_msg:
        return

    lines = (reply_msg.text or "").splitlines(keepends=True)
    new_lines: List[str] = []
    for ln in lines:
        if PRICE_LINE_RE.search(ln):
            new_lines.append(re.sub(r"— (\d+ грн|договірна)", "— (замовлення оплачене)", ln))
        elif TOTAL_LINE_RE.match(ln.strip()):
            continue
        else:
            new_lines.append(ln)
    try:
        await reply_msg.edit_text("".join(new_lines))
    except Exception as e:
        logger.warning(f"Не вдалося позначити 'оплачено': {e}")

async def set_operator_for_uk(update: Update, context: ContextTypes.DEFAULT_TYPE, operator: str):
    reply_msg = update.effective_message.reply_to_message
    if not reply_msg:
        return
    lines = (reply_msg.text or "").splitlines(keepends=True)
    new_lines: List[str] = []
    for ln in lines:
        # 🇬🇧 Англія, 2 шт — 650 грн
        if ln.strip().startswith("🇬🇧") and "Англія" in ln:
            ln = ln.replace("Англія", f"Англія (оператор {operator})")
        new_lines.append(ln)
    try:
        await reply_msg.edit_text("".join(new_lines))
    except Exception as e:
        logger.warning(f"Не вдалося поставити оператора UK: {e}")

# ==== Команди ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Допоможу вам оформити замовлення на SIM-карти, а також постараюсь надати відповіді на всі ваші запитання.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(ORDER_INFO_REQUEST)

# ==== Запуск бота ====
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_message))

    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
