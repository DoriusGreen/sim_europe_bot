# bot.py
import os
import logging
from typing import List, Dict, Optional
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
THANK_YOU_TAG = "<SEND_THANK_YOU>"

# ==== Стандартні повідомлення ====
ORDER_INFO_REQUEST = (
    "🛒 Для оформлення замовлення напишіть:\n\n"
    "1. Ім'я та прізвище.\n"
    "2. Номер телефону.\n"
    "3. Місто та № відділення \"Нової Пошти\".\n"
    "4. Країна(и) та кількість sim-карт."
)

# ==== Прайси й мапи країн ====
# Сходинки: (мін.к-ть, ціна за 1). None = договірна
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
}

def normalize_country(name: str) -> str:
    n = (name or "").strip().upper()
    # Синоніми (розширено)
    if n in ("АНГЛІЯ", "БРИТАНІЯ", "UK", "U.K.", "UNITED KINGDOM", "ВБ", "GREAT BRITAIN"):
        return "ВЕЛИКОБРИТАНІЯ"
    if n in ("USA", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA", "ШТАТИ", "АМЕРИКА", "US", "U.S."):
        return "США"
    return n

# ---------- ОПЕРАТОРИ ДЛЯ АНГЛІЇ ----------
def canonical_operator(op: Optional[str]) -> Optional[str]:
    """Повертає канонічні назви операторів для Англії або None."""
    if not op:
        return None
    o = op.strip().lower()
    if o in ("o2", "о2"):
        return "O2"
    if o in ("lebara", "лебара"):
        return "Lebara"
    if o in ("vodafone", "водафон", "водофон"):
        return "Vodafone"
    return None  # якщо GPT/клієнт передав щось інше — не показуємо
# -----------------------------------------

def unit_price(country_norm: str, qty: int) -> Optional[int]:
    tiers = PRICE_TIERS.get(country_norm)
    if not tiers:
        return None
    for min_q, price in tiers:
        if qty >= min_q:
            return price
    return None

# ==== Прайс-рендеринг ====

def _format_range(min_q: int, max_q: Optional[int]) -> str:
    if max_q is None:
        return f"{min_q}+ шт."
    if min_q == max_q:
        return f"{min_q} шт."
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
        if price is None:
            line = f"{qty_part} — договірна"
        else:
            line = f"{qty_part} — {price} грн"
        lines.append(line)

    return header + "\n".join(lines) + "\n\n"

def render_prices(countries: List[str]) -> str:
    if not countries:
        countries = list(PRICE_TIERS.keys())
    blocks = []
    for c in countries:
        key = normalize_country(c).upper()
        if key in PRICE_TIERS:
            blocks.append(render_price_block(key))
    if not blocks:
        blocks = [render_price_block(k) for k in PRICE_TIERS.keys()]
    return "".join(blocks)

# ==== Форматування ПІДСУМКУ (імʼя/місто/тел/№) ====

def _cap_word(w: str) -> str:
    return w[:1].upper() + w[1:].lower() if w else w

def _smart_title(s: str) -> str:
    # Капіталізація слів та частин через дефіс
    s = (s or "").strip()
    parts = re.split(r"\s+", s)
    out = []
    for p in parts:
        sub = "-".join(_cap_word(x) for x in p.split("-"))
        out.append(sub)
    return " ".join(out)

def format_full_name(name: str) -> str:
    # Прибираємо по-батькові: беремо перше і останнє слово
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

# ==== Шаблони підсумку ====
ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

@dataclass
class OrderItem:
    country: str
    qty: int
    operator: Optional[str] = None  # опціонально (лише для Англії показуємо в підсумку)

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

    full_name_fmt = format_full_name(order.full_name)
    phone_fmt = format_phone(order.phone)
    city_fmt = format_city(order.city)
    np_fmt = format_np(order.np)

    header = (
        f"{full_name_fmt} \n"
        f"{phone_fmt}\n"
        f"{city_fmt} № {np_fmt}  \n\n"
    )

    body = "".join(lines) + "\n"

    if counted_countries >= 2:
        footer = f"Загальна сумма: {grand_total} грн\n"
    else:
        footer = ""

    return header + body + footer

# ==== JSON парсери ====
ORDER_JSON_RE = re.compile(r"\{[\s\S]*\}")
PRICE_JSON_RE = re.compile(r"\{[\s\S]*\}")

def try_parse_order_json(text: str) -> Optional[OrderData]:
    m = ORDER_JSON_RE.search(text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        items = [
            OrderItem(
                country=i["country"],
                qty=int(i["qty"]),
                operator=i.get("operator")
            )
            for i in data.get("items", [])
        ]
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

# ==== СИСТЕМНІ ПРОМПТИ ====

def build_system_prompt() -> str:
    return (
        "Ти — дружелюбний і корисний Telegram-бот-магазин SIM-карт. "
        "На початку чату клієнт уже отримав прайси від аккаунта власника — ти їх не дублюєш, а підхоплюєш діалог. "
        "Відповідай по суті, запамʼятовуй контекст (історію чату), не перепитуй одне й те саме.\n\n"
        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення «Нової Пошти».\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо користувач явно хоче оформити замовлення (будь-якою мовою), але ще НЕ вказано ЖОДНОГО з 4 пунктів — "
        "відповідай САМЕ цим текстом (буква в букву):\n"
        "🛒 Для оформлення замовлення напишіть:\n\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення \"Нової Пошти\".\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Якщо бракує ДЕЯКИХ пунктів — відповідай СУВОРО в такому вигляді:\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"
        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою:\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n\n"
        "Якщо користувач запитує ПРО ЦІНИ або про наявність країн — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_prices": true,\n'
        '  "countries": ["ALL" або перелік ключів, напр. "ВЕЛИКОБРИТАНІЯ","США"]\n'
        "}\n\n"
        "Правила:\n"
        "• Визначай країни семантично (без ключових слів).\n"
        "• Ключі країн: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Якщо запит загальний — поверни countries: [\"ALL\"].\n"
        "• Не наводь самі ціни — лише JSON.\n\n"
        "Семантика:\n"
        "• UK/United Kingdom/Британія/Лондон → ВЕЛИКОБРИТАНІЯ; USA/Америка/Штати → США.\n"
        "• Якщо клієнт для Англії називає оператора (O2, Lebara, Vodafone) — додай поле \"operator\" в item; інакше — ні.\n"
        "• До операторів застосовуй канонічні форми: O2, Lebara, Vodafone.\n"
        "• Якщо дані суперечливі — попроси саме відсутні/неясні пункти.\n\n"
        "Після JSON бекенд сам рахує суми. «Загальна сумма» показується лише якщо країн 2+.\n"
        "FAQ і стилістика — коротко, дружелюбно, без води."
    )

def build_followup_prompt() -> str:
    return (
        "Ти — той самий Telegram-бот магазину SIM. Блок(и) цін щойно надіслано окремим повідомленням. "
        "Зараз відповідай КОРОТКО на інші частини останнього повідомлення користувача, які НЕ стосуються цін/прайсу. "
        "Не повторюй і не перефразовуй ціни, не надсилай JSON. Якщо питали про терміни відправки — відповідай: "
        "\"Відправляємо протягом 24 годин.\" Якщо питали про наявність — підтверди, що є в наявності. "
        "Якщо додаткових питань нема — поверни порожній рядок."
    )

def _ensure_history(ctx: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, str]]:
    if "history" not in ctx.chat_data:
        ctx.chat_data["history"] = []
    return ctx.chat_data["history"]

def _prune_history(history: List[Dict[str, str]]) -> None:
    if len(history) > MAX_TURNS * 2:
        del history[: len(history) - MAX_TURNS * 2]

async def _ask_gpt(history: List[Dict[str, str]], user_message: str) -> str:
    messages = [{"role": "system", "content": build_system_prompt()}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
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

async def _ask_gpt_followup(history: List[Dict[str, str]], user_message: str) -> str:
    # окремий короткий промпт для відповіді після прайсу
    messages = [{"role": "system", "content": build_followup_prompt()}]
    # для контексту дамо останні 2-4 репліки діалогу
    tail = history[-4:] if len(history) > 4 else history[:]
    messages.extend(tail)
    messages.append({"role": "user", "content": user_message})
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=200,
            temperature=0.2,
        )
        return response.choices[0].message["content"].strip()
    except Exception as e:
        logger.error(f"Помилка follow-up до OpenAI: {e}")
        return ""

# ===== Команда /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Вітаю! Я допоможу вам оформити замовлення на SIM-карти, а також постараюсь надати відповіді на всі ваші запитання. Бажаєте оформити замовлення?"
    )
    await update.message.reply_text(text)

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip() if update.message and update.message.text else ""
    history = _ensure_history(context)

    # Весь розбір наміру/полів віддаємо на GPT
    reply_text = await _ask_gpt(history, user_message)

    # Уніфікуємо заголовок, якщо модель забула емодзі
    if "Залишилось вказати:" in reply_text and "📝 Залишилось вказати:" not in reply_text:
        reply_text = reply_text.replace("Залишилось вказати:", "📝 Залишилось вказати:")

    # Якщо прийшов JSON повного замовлення — парсимо, рахуємо, рендеримо шаблон
    parsed = try_parse_order_json(reply_text)
    if parsed and parsed.items and parsed.full_name and parsed.phone and parsed.city and parsed.np:
        summary = render_order(parsed)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": summary})
        _prune_history(history)

        await update.message.reply_text(summary)
        await update.message.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
        return

    # Перевіряємо, чи це запит на ціни
    price_countries = try_parse_price_json(reply_text)
    if price_countries is not None:
        want_all = any(str(c).upper() == "ALL" for c in price_countries)
        if want_all:
            countries = list(PRICE_TIERS.keys())
        else:
            norm = [normalize_country(str(c)).upper() for c in price_countries]
            countries = [c for c in norm if c in PRICE_TIERS]
        price_msg = render_prices(countries)

        # 1) надсилаємо прайс
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": price_msg})
        _prune_history(history)
        await update.message.reply_text(price_msg)

        # 2) коротко відповідаємо на інші частини запитання (без цін)
        follow = await _ask_gpt_followup(history, user_message)
        if follow:
            history.append({"role": "assistant", "content": follow})
            _prune_history(history)
            await update.message.reply_text(follow)
        return

    # Інакше — звичайна відповідь моделі (включно з 🛒 ORDER_INFO_REQUEST або 📝 список відсутніх полів)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply_text})
    _prune_history(history)
    await update.message.reply_text(reply_text)

# ===== Запуск програми =====

def main():
    if not TELEGRAM_TOKEN or not OPENAI_API_KEY or not WEBHOOK_URL:
        raise RuntimeError("Не задано TELEGRAM_BOT_TOKEN, OPENAI_API_KEY або WEBHOOK_URL")

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
