# bot.py
import os
import logging
from typing import List, Dict, Optional, Set, Any, Tuple
from dataclasses import dataclass
import json
import re

from telegram import Update
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
MAX_TURNS = 14

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
    "ВЕЛИКОБРИТАНІЯ": "🇬🇧", "НІДЕРЛАНДИ": "🇳🇱", "НІМЕЧЧИНА": "🇩🇪",
    "ФРАНЦІЯ": "🇫🇷", "ІСПАНІЯ": "🇪🇸", "ЧЕХІЯ": "🇨🇿", "ПОЛЬЩА": "🇵🇱",
    "ЛИТВА": "🇱🇹", "ЛАТВІЯ": "🇱🇻", "КАЗАХСТАН": "🇰🇿", "МАРОККО": "🇲🇦", "США": "🇺🇸",
    # нижче — лише для USSD-відповідей (не впливає на прайс/наявність)
    "ІТАЛІЯ": "🇮🇹",
    "МОЛДОВА": "🇲🇩",
}
DISPLAY = {
    "ВЕЛИКОБРИТАНІЯ": "Англія", "НІДЕРЛАНДИ": "Нідерланди", "НІМЕЧЧИНА": "Німеччина",
    "ФРАНЦІЯ": "Франція", "ІСПАНІЯ": "Іспанія", "ЧЕХІЯ": "Чехія", "ПОЛЬЩА": "Польща",
    "ЛИТВА": "Литва", "ЛАТВІЯ": "Латвія", "КАЗАХСТАН": "Казахстан",
    "МАРОККО": "Марокко", "США": "США",
    # для USSD
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
    "США": "+1",  # додано
}

def normalize_country(name: str) -> str:
    n = (name or "").strip().upper()
    if n in ("АНГЛІЯ","БРИТАНІЯ","UK","U.K.","UNITED KINGDOM","ВБ","GREAT BRITAIN","+44","ЮК","У.К."):
        return "ВЕЛИКОБРИТАНІЯ"
    if n in ("USA","U.S.A.","UNITED STATES","UNITED STATES OF AMERICA","ШТАТИ","АМЕРИКА","US","U.S."):
        return "США"
    if n in ("ITALY","ИТАЛИЯ","ІТАЛІЯ","ITALIA","+39"):  # тільки для USSD
        return "ІТАЛІЯ"
    if n in ("МОЛДОВА","MOLDOVA","+373"):
        return "МОЛДОВА"
    return n

# ---------- ОПЕРАТОРИ ДЛЯ АНГЛІЇ (для замовлення) ----------
def canonical_operator(op: Optional[str]) -> Optional[str]:
    if not op:
        return None
    o = op.strip().lower()
    if o in ("o2","о2"): return "O2"
    if o in ("lebara","лебара"): return "Lebara"
    if o in ("vodafone","водафон","водофон"): return "Vodafone"
    return None
# -----------------------------------------------------------

# ---------- ОПЕРАТОРИ ДЛЯ USSD (розширено) ----------
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
# ------------------------------------------------------

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

# ==== Форматування ПІДСУМКУ (імʼя/місто/тел/№) ====
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

def _split_city_extra(city: str):
    s = " ".join((city or "").split())
    m = re.match(r"(.+?)\s*\((.+)\)\s*$", s)
    if m:
        return m.group(1), m.group(2)
    parts = re.split(r"\s*[,;/—–-]\s*", s, maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return s, None

def format_city(city: str) -> str:
    base, extra = _split_city_extra(city)
    base_fmt = _smart_title(base)
    if extra:
        extra_fmt = _smart_title(extra)
        return f"{base_fmt} ({extra_fmt})"
    return base_fmt

def format_np(np_str: str) -> str:
    s = (np_str or "").strip()
    m = re.search(r"\d+", s)
    if m:
        return m.group(0)
    s = re.sub(r"[^\d]", "", s)
    return s or (np_str or "").strip()

# ==== Шаблон рядка замовлення ====
ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

@dataclass
class OrderItem:
    country: str
    qty: int
    operator: Optional[str] = None  # для UK

@dataclass
class OrderData:
    full_name: str
    phone: str
    city: str
    np: str
    items: List[OrderItem]

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
        logger.warning(f"Не вдалося розпарсити JSON: {e}")
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
    """
    Очікуємо:
    {
      "ask_ussd": true,
      "targets": [ {"country":"ІСПАНІЯ","operator":"Lebara"}, {"country":"ФРАНЦІЯ"} ]
    }
    """
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
    "США, на відміну від інших, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
    "Як активувати та поповнити сім-карту США?\n\n"
    "https://www.lycamobile.us/en/activate-sim\n"
    "1. На цьому сайті вводите дані сімки для її попередньої активації. Отриманий на сайті номер сім-карти записуєте.\n\n"
    "https://www.lycamobile.us/en/quick-top-up/\n"
    "2. Далі, ось тут, вказавши номер, отриманий на попередньому сайті, поповнюєте сім-карту, після поповнення (мінімум на 23$) вона стане активною та буде приймати SMS."
)

def user_mentions_usa(text: str) -> bool:
    t = (text or "").lower()
    return bool(
        re.search(r"\b(сша|usa|u\.s\.a\.|united states|штат[а-яіїє]+|америк[аи])\b", t)
        or re.search(r"(^|\s)\+1(\s|$)", t)
    )

def contains_us_activation_block(text: str) -> bool:
    t = (text or "").lower()
    return ("lycamobile.us/en/activate-sim" in t) or ("як активувати та поповнити сім-карту сша" in t)

# ==== ДОВІДНИК USSD КОМБІНАЦІЙ ====
# (operator=None означає, що код загальний без прив’язки до оператора)
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
    # США — навмисно відсутні коди: буде застосовано фолбек нижче
}

FALLBACK_PLASTIC_MSG = "Номер вказаний на пластику сім-карти"

def render_ussd_targets(targets: List[Dict[str, str]]) -> str:
    """Строге форматування USSD-відповіді з фолбеком, якщо коду немає."""
    result_lines: List[str] = []

    for t in targets:
        country = normalize_country(t.get("country", "")).upper()
        if not country:
            continue

        op_req = canonical_operator_any(t.get("operator"))

        # Базові атрибути рядка
        code_prefix = DIAL_CODES.get(country, "")
        flag = FLAGS.get(country, "")
        disp = DISPLAY.get(country, country.title())

        pairs = USSD_DATA.get(country, [])
        # Якщо є конкретний оператор — фільтруємо; якщо після фільтру пусто, спрацює фолбек
        if op_req and pairs:
            pairs = [p for p in pairs if (p[0] and canonical_operator_any(p[0]) == op_req)]

        if not pairs:
            # ФОЛБЕК: коли країни/оператора немає у таблиці — не вигадуємо коди
            if op_req:
                result_lines.append(f"{code_prefix} {flag} {disp} (оператор {op_req}) — {FALLBACK_PLASTIC_MSG}")
            else:
                result_lines.append(f"{code_prefix} {flag} {disp} — {FALLBACK_PLASTIC_MSG}")
            # Переходимо до наступної країни
            continue

        # Інакше — рендеримо всі наявні коди
        for op, code in pairs:
            if op and code.startswith("*"):
                result_lines.append(f"{code_prefix} {flag} {disp} (оператор {op}) — {code}")
            elif op and not code.startswith("*"):
                result_lines.append(f"{code_prefix} {flag} {disp} (оператор {op}) — {code}")
            else:
                result_lines.append(f"{code_prefix} {flag} {disp} — {code}")

        # Порожній рядок між країнами
        result_lines.append("")

    # Приберемо останній зайвий розрив, якщо є
    while result_lines and result_lines[-1] == "":
        result_lines.pop()

    return "\n".join(result_lines).strip()

# ==== СИСТЕМНІ ПРОМПТИ ====
def build_system_prompt() -> str:
    return (
        # === РОЛЬ ТА КОНТЕКСТ ===
        "Ти — дружелюбний і корисний Telegram-бот-магазин SIM-карт. Чітко та суворо дотримуйся прописаних інструкцій, якщо щось не зрозуміло, то не вигадуй, а краще перепитай клієнта що він мав на увазі.\n"
        "На початку чату клієнт уже отримує від акаунта власника перелік країн у наявності та інформацію для доставки — ти це НЕ ДУБЛЮЄШ.\n"
        "Не надсилай прайси чи чек-лист автоматично; роби це лише коли користувач прямо просить або вже надіслав хоча б один із пунктів замовлення.\n\n"

        # === СТРУКТУРА ЗАМОВЛЕННЯ (4 ПУНКТИ) ===
        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення «Нової Пошти».\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # === КОЛИ ПОКАЗУВАТИ ЧЕК-ЛИСТ АБО «ЗАЛИШИЛОСЬ ВКАЗАТИ» ===
        "Пункт 4 може бути у довільній формі/порядку («Англія 2 шт», «дві UK», «UK x2» тощо).\n"
        "Якщо пункт 4 прийшов окремим повідомленням — поєднуй його з пунктами 1–3 з контексту.\n\n"
        "Якщо користувач ЯВНО хоче оформити замовлення, але ще НЕ вказано ЖОДНОГО з 4 пунктів — відповідай САМЕ цим текстом (буква в букву):\n"
        "🛒 Для оформлення замовлення напишіть:\n\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення \"Нової Пошти\".\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо бракує ДЕЯКИХ пунктів — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # === ФОРМАТ ПОВНОГО JSON ДЛЯ БЕКЕНДА ===
        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n\n"

        # === РЕЖИМ ПРАЙСУ / НАЯВНОСТІ (JSON) ===
        "Якщо користувач запитує ПРО ЦІНИ або про наявність — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_prices": true,\n'
        '  "countries": ["ALL" або перелік ключів, напр. "ВЕЛИКОБРИТАНІЯ","США"]\n'
        "}\n\n"

        # === РЕЖИМ ДОВІДКИ USSD (ЯК ДІЗНАТИСЯ НОМЕР) ===
        "Якщо користувач запитує, як дізнатися/перевірити свій номер на SIM — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_ussd": true,\n'
        '  "targets": [ {"country":"КРАЇНА","operator":"Опціонально: O2|Lebara|Vodafone|Movistar|Lycamobile|T-mobile|Kaktus"}, ... ]\n'
        "}\n"
        "Якщо країна в повідомленні не вказана — УТОЧНИ її запитом, не вигадуй.\n\n"

        # === ДОСТУПНІ КРАЇНИ ДЛЯ ПРОДАЖУ (БІЛИЙ СПИСОК) ===
        "Для прайсу/наявності доступні ЛИШЕ: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США. "
        "Не стверджуй наявність/ціну для інших країн (але довідку USSD можна давати і для інших, якщо відома комбінація).\n\n"

        # === СЕМАНТИКА РОЗПІЗНАВАННЯ ===
        "• Розумій країни за синонімами/містами/мовою (UK/United Kingdom/+44/Британія → ВЕЛИКОБРИТАНІЯ; USA/Америка/Штати → США).\n"
        "• Для items використовуй ключі: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Якщо клієнт для Англії називає оператора (O2, Lebara, Vodafone) — додай поле \"operator\" з канонічним значенням; інакше — не додавай це поле.\n"
        "• Текстові кількості (пара/десяток/кілька) перетворюй у число або попроси уточнення через пункт 4.\n\n"

        # === ПІСЛЯ JSON ===
        "Після JSON бекенд сам рахує суми та формує підсумок. «Загальна сумма» показується лише якщо країн 2+.\n\n"

        # === FAQ (КОРОТКО І ПО СУТІ) ===
        "FAQ — використвуй цю інформацію для відповідей на ці, або дуже схожі, запитання, відповіді давай короткі та по суті:\n\n"
        "Як активувати SIM-карту?\n"
        "Просто вставте в телефон і почекайте поки сім-карта підключиться до мережі (або підключіться до мережі вручну через налаштування в телефоні).\n\n"
        "Чи зможу я зареєструвати месенджери?\n"
        "Так! Ви одразу зможете зареєструвати WhatsApp, Telegram, Viber та інші месенджери, а також прийняти SMS з будь-яких інших сервісів.\n\n"
        "Чи потрібно поповнювати?\n"
        "Ні. Сім-карта одразу працює на прийом SMS, але для вхідних та вихідних дзвінків потребує поповнення. Ми поповненнями, на жаль, не займаємось, при потребі ви можете зробити це самостійно використавши сервіс ding.com та PayPal.\n\n"
        "Скільки SIM-карта буде активна?\n"
        "Зазвичай після вставки в телефон до півроку. Встановлені месенджери працюватимуть і після деактивації сімки. Щоб сім-карта працювала понад півроку, раз на 6 міс поповнюйте на 10 фунтів/євро.\n\n"
        "Які тарифи?\n"
        "По тарифам не консультуємо — дивіться сайт вашого оператора.\n\n"
        "Чи є різниця між країнами та операторами?\n"
        "Принципової різниці немає: усі SIM одразу зареєстровані (якщо країна цього потребує) і працюють на прийом SMS.\n\n"
        "Це нові сім-карти?\n"
        "Так, нові, ніде не використовувались.\n\n"
        "Чи даєте гарантії?\n"
        "Якщо SIM не працюватиме (рідко трапляється), зробимо заміну або повернемо кошти.\n\n"
        "Коли відправите?\n"
        "Зазвичай у день замовлення або протягом 24 годин. «Нова Пошта» переважно доставляє за добу.\n\n"
        "Чи вже відправили? Чи є ТТН/трек-номер?\n"
        "Зазвичай відправляємо в день замовлення або протягом 24 годин. Якщо потрібна ТТН — очікуйте відповіді менеджера.\n\n"
        "Як оплатити?\n"
        "Зазвичай накладений платіж. За бажанням — оплата на карту або USDT (TRC-20).\n\n"
        "Чи можлива відправка в інші країни?\n"
        "Так, від 3 шт, повна передоплата, відправка через «Нову Пошту».\n\n"

        # === США — ОСОБЛИВОСТІ АКТИВАЦІЇ ===
        "США — особливості активації: США, на відміну від інших, потребують поповнення для активації. Після поповнення SIM працюватиме на прийом SMS.\n\n"
        "Інструкція (надсилай як є, зі збереженням відступів):\n\n"
        "Як активувати та поповнити сім-карту США?\n\n"
        "https://www.lycamobile.us/en/activate-sim\n"
        "1. На цьому сайті вводите дані сімки для її попередньої активації. Отриманий на сайті номер сім-карти записуєте.\n\n"
        "https://www.lycamobile.us/en/quick-top-up/\n"
        "2. Далі, ось тут, вказавши номер, отриманий на попередньому сайті, поповнюєте сім-карту, після поповнення (мінімум на 23$) вона стане активною та буде приймати SMS.\n\n"

        # === СТИЛЬ ===
        "Стиль: дружелюбно, чітко, без води. Не повторюй уже надані дані."
    )

def build_followup_prompt() -> str:
    return (
        "Прайс або інше повідомлення щойно надіслано окремо. "
        "Відповідай КОРОТКО на інші частини останнього повідомлення, що НЕ стосуються вже надісланих даних.\n\n"
        "Якщо користувач просив ЛИШЕ ціну/вартість/прайс і нічого більше — поверни порожній рядок. "
        "НЕ пиши підтвердження наявності («підтверджую наявність», «є в наявності», «available»).\n\n"
        "Якщо користувач питає, як дізнатися/перевірити свій номер — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_ussd": true,\n'
        '  "targets": [ {"country":"КРАЇНА","operator":"Опціонально: O2|Lebara|Vodafone|Movistar|Lycamobile|T-mobile|Kaktus"}, ... ]\n'
        "}\n\n"
        "Якщо питали про терміни відправки — «Відправляємо протягом 24 годин.»; "
        "якщо про ТТН/трек-номер — «Зазвичай відправляємо в день замовлення або протягом 24 годин. Якщо потрібна ТТН — очікуйте відповіді менеджера.» "
    )

def build_force_point4_prompt() -> str:
    return (
        "Ти — той самий бот. У контексті вже є пункти 1–3 (ПІБ, телефон, місто+№). "
        "Останнє повідомлення користувача ймовірно містить лише пункт 4 (країни та кількість sim-карт) у довільному порядку. "
        "Твоє завдання — витягти пункт 4, поєднати з пунктами 1–3 з контексту і ПОВЕРНУТИ ЛИШЕ ПОВНИЙ JSON замовлення."
    )

# ---- фільтр follow-up
def is_meaningful_followup(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()

    # не слати службові/зайві фрази
    banned_words = ["ціни", "прайс", "надіслано", "див. вище", "вище", "повторю"]
    if any(w in low for w in banned_words):
        return False

    # маркери прайсу
    if "грн" in low or re.search(r"\bшт\.?\b", low):
        return False

    # відсікаємо шаблонні підтвердження наявності
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

async def _ask_gpt(messages: List[Dict[str, str]]) -> str:
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=600,
            temperature=0.2,
        )
        return response.choices[0].message["content"]
    except Exception as e:
        logger.error(f"Помилка при зверненні до OpenAI: {e}")
        return "Вибачте, сталася технічна помилка. Спробуйте, будь ласка, ще раз."

async def _ask_gpt_main(history: List[Dict[str, str]], user_message: str) -> str:
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return await _ask_gpt(messages)

async def _ask_gpt_followup(history: List[Dict[str, str]], user_message: str) -> str:
    messages = [{"role": "system", "content": build_followup_prompt()}]
    tail = history[-4:] if len(history) > 4 else history[:]
    messages.extend(tail)
    messages.append({"role": "user", "content": user_message})
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=300,
            temperature=0.2,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error(f"Помилка follow-up до OpenAI: {e}")
        return ""

async def _ask_gpt_force_point4(history: List[Dict[str, str]], user_message: str) -> str:
    messages = [{"role": "system", "content": build_force_point4_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.1,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error(f"Помилка force-point4 до OpenAI: {e}")
        return ""

# ===== Команда /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        logger.warning("No effective_message in /start update: %s", update)
        return
    await msg.reply_text(
        "Вітаю! Я допоможу вам оформити замовлення на SIM-карти, а також постараюсь надати відповіді на всі ваші запитання. Бажаєте оформити замовлення?"
    )

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        logger.warning("No effective_message in update: %s", update)
        return

    user_message = msg.text.strip() if msg.text else ""
    history = _ensure_history(context)

    # 1) Основний виклик GPT
    reply_text = await _ask_gpt_main(history, user_message)

    # Уніфікуємо заголовок, якщо модель забула емодзі
    if "Залишилось вказати:" in reply_text and "📝 Залишилось вказати:" not in reply_text:
        reply_text = reply_text.replace("Залишилось вказати:", "📝 Залишилось вказати:")

    # 2) Якщо прийшов JSON повного замовлення — парсимо, рахуємо, рендеримо
    parsed = try_parse_order_json(reply_text)
    if parsed and parsed.items and parsed.full_name and parsed.phone and parsed.city and parsed.np:
        summary = render_order(parsed)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": summary})
        _prune_history(history)

        await msg.reply_text(summary)
        await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
        return

    # 3) Режим цін/наявності
    price_countries = try_parse_price_json(reply_text)
    if price_countries is not None:
        want_all = any(str(c).upper() == "ALL" for c in price_countries)
        normalized = [normalize_country(str(c)).upper() for c in price_countries if str(c).strip()]
        valid = [k for k in normalized if k in PRICE_TIERS]
        invalid = [price_countries[i] for i, k in enumerate(normalized)
                   if k not in PRICE_TIERS and str(price_countries[i]).upper() != "ALL"]

        if want_all:
            price_msg = "".join(render_price_block(k) for k in PRICE_TIERS.keys())
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": price_msg})
            _prune_history(history)
            await msg.reply_text(price_msg)
        elif valid:
            price_msg = render_prices(valid)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": price_msg})
            _prune_history(history)
            await msg.reply_text(price_msg)
            if invalid:
                await msg.reply_text(render_unavailable(invalid))
        else:
            unavailable_msg = render_unavailable(invalid if invalid else price_countries)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": unavailable_msg})
            _prune_history(history)
            await msg.reply_text(unavailable_msg)

        # 3a) США — надіслати інструкцію, якщо питали саме про США
        usa_intent = (("США" in valid) and (len(valid) == 1 or user_mentions_usa(user_message)))
        usa_activation_sent = False
        if usa_intent:
            await msg.reply_text(US_ACTIVATION_MSG)
            usa_activation_sent = True

        # 3b) Фоллоу-ап: якщо GPT повернув USSD JSON — рендеримо; інакше коротка відповідь.
        follow = await _ask_gpt_followup(history, user_message)
        ussd_targets = try_parse_ussd_json(follow)
        if ussd_targets:
            formatted = render_ussd_targets(ussd_targets) or FALLBACK_PLASTIC_MSG
            history.append({"role": "assistant", "content": formatted})
            _prune_history(history)
            await msg.reply_text(formatted)
            return
        if is_meaningful_followup(follow):
            if not (usa_activation_sent and contains_us_activation_block(follow)):
                history.append({"role": "assistant", "content": follow})
                _prune_history(history)
                await msg.reply_text(follow)
        return

    # 4) Якщо GPT одразу повернув USSD JSON — рендеримо (з фолбеком), щоб не відправляти «сирий» JSON
    ussd_targets = try_parse_ussd_json(reply_text)
    if ussd_targets:
        formatted = render_ussd_targets(ussd_targets) or FALLBACK_PLASTIC_MSG
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": formatted})
        _prune_history(history)
        await msg.reply_text(formatted)
        return

    # 5) Якщо бракує лише пункту 4 — пробуємо «force point 4»
    if missing_points_from_reply(reply_text) == {4}:
        force_json = await _ask_gpt_force_point4(history, user_message)
        forced = try_parse_order_json(force_json)
        if forced and forced.items and forced.full_name and forced.phone and forced.city and forced.np:
            summary = render_order(forced)
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": summary})
            _prune_history(history)
            await msg.reply_text(summary)
            await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
            return

    # 6) Інакше — звичайна відповідь моделі (включно з 🛒/📝/FAQ)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply_text})
    _prune_history(history)
    await msg.reply_text(reply_text)

# ===== Error handler =====
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling update: %s", update, exc_info=context.error)

# ===== Запуск програми =====
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
