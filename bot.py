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

        # Вставляємо один відступ перед секцією 100+ (як у прикладі)
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

# ==== Шаблони підсумку ====
ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

# ---------- ПОВЕРНУТО ОПЕРАТОРИ ДЛЯ АНГЛІЇ ----------
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
# ----------------------------------------------------

@dataclass
class OrderItem:
    country: str
    qty: int
    # Оператор — опціонально (лише для Англії показуємо в підсумку)
    operator: Optional[str] = None

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

        # додаємо "(оператор X)" ТІЛЬКИ для Великобританії, якщо оператор заданий і валідний
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
        f"{order.full_name} \n"
        f"{order.phone}\n"
        f"{order.city} № {order.np}  \n\n"
    )

    body = "".join(lines) + "\n"

    if counted_countries >= 2:
        footer = f"Загальна сумма: {grand_total} грн\n"
    else:
        footer = ""

    return header + body + footer

# ==== JSON парсер відповіді моделі ====
ORDER_JSON_RE = re.compile(r"\{[\s\S]*\}")

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
                operator=i.get("operator")  # ← оператор приймаємо опціонально
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

# ==== Парсер запиту на ціни ====
PRICE_JSON_RE = re.compile(r"\{[\s\S]*\}")

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

# ==== СИСТЕМНИЙ ПРОМПТ (посилено під семантику GPT) ====

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

        # Нове правило для старту оформлення БЕЗ жодних полів
        "Якщо користувач явно хоче оформити замовлення (будь-якою мовою), але в контексті розмови ще НЕ вказано ЖОДНОГО з 4 пунктів — "
        "відповідай САМЕ цим текстом (буква в букву):\n"
        "🛒 Для оформлення замовлення напишіть:\n\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення \"Нової Пошти\".\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # Правило для часткових даних
        "Якщо бракує ДЕЯКИХ пунктів — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після, однаковий формат незалежно від мови користувача):\n"
        "📝 Залишилось вказати:\n\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        # Повне замовлення — лише JSON
        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N,"operator":"O2|Lebara|Vodafone"}, ... ]\n'
        "}\n\n"

        # Режим цін/наявності
        "Якщо користувач запитує ПРО ЦІНИ або про наявність країн — ВІДПОВІДАЙ ЛИШЕ JSON:\n"
        "{\n"
        '  "ask_prices": true,\n'
        '  "countries": ["ALL" або перелік ключів, напр. "ВЕЛИКОБРИТАНІЯ","США"]\n'
        "}\n\n"
        "Правила:\n"
        "• Визначай країни семантично за змістом повідомлення (без ключових слів).\n"
        "• У масиві countries використовуй ТІЛЬКИ ці ключі: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Якщо запит загальний (типу: які є країни і ціни?) — поверни countries: [\"ALL\"].\n"
        "• Не наводь самі ціни — лише JSON, без зайвого тексту.\n\n"

        # Інструкції для семантичного парсингу
        "Семантика:\n"
        "• Розумій країни за синонімами/містами/мовою (UK/United Kingdom/Британія/Лондон → ВЕЛИКОБРИТАНІЯ; USA/Америка/Штати → США).\n"
        "• Якщо клієнт для Англії явно називає оператора (O2, Lebara або Vodafone) — додай у відповідний елемент масиву items поле \"operator\" з точним значенням \"O2\"/\"Lebara\"/\"Vodafone\". Якщо НЕ називає — не додавай поле \"operator\".\n"
        "• До назв операторів застосовуй канонічні форми: O2, Lebara, Vodafone (без здогадок).\n"
        "• Країну в JSON бажано повертай у вигляді одного з ключів: ВЕЛИКОБРИТАНІЯ, НІДЕРЛАНДИ, НІМЕЧЧИНА, ФРАНЦІЯ, ІСПАНІЯ, ЧЕХІЯ, ПОЛЬЩА, ЛИТВА, ЛАТВІЯ, КАЗАХСТАН, МАРОККО, США.\n"
        "• Текстові кількості (\"пара\", \"десяток\", \"кілька\") конвертуй у конкретне число або попроси уточнити через пункт 4.\n"
        "• Якщо дані суперечливі — попроси саме відсутні/неясні пункти через формат \"📝 Залишилось вказати: ...\".\n\n"

        # Підсумки/ціни рахує бекенд
        "Після того, як ти віддаєш JSON, бекенд сам порахує ціни/суми та сформує підсумок у потрібному форматі. "
        "«Загальна сумма» показується тільки якщо країн 2 або більше.\n\n"

        # Ціни — лише як довідка для розуміння номенклатури (не цитувати користувачу)
        "Ціни (штучно/оптом):\n"
        "🇬🇧 ВЕЛИКОБРИТАНІЯ: 1 — 350; 2–3 — 325; 4–9 — 300; 10–19 — 275; 20–99 — 250; 100+ — 210; 1000+ — договірна\n"
        "🇳🇱 НІДЕРЛАНДИ: 1–3 — 800; 4–19 — 750; 20–99 — 700\n"
        "🇩🇪 НІМЕЧЧИНА: 1–3 — 1100; 4–9 — 1000; 10–99 — 900\n"
        "🇫🇷 ФРАНЦІЯ: 1–3 — 1400; 4–9 — 1200; 10–99 — 1100\n"
        "🇪🇸 ІСПАНІЯ: 1–3 — 900; 4–9 — 850; 10–99 — 800\n"
        "🇨🇿 ЧЕХІЯ: 1–3 — 750; 4–9 — 700; 10–99 — 650\n"
        "🇵🇱 ПОЛЬЩА: 1–3 — 500; 4–9 — 450; 10–99 — 400\n"
        "🇱🇹 ЛИТВА: 1–3 — 750; 4–9 — 700; 10–99 — 650\n"
        "🇱🇻 ЛАТВІЯ: 1–3 — 750; 4–9 — 700; 10–99 — 650\n"
        "🇰🇿 КАЗАХСТАН: 1 — 1200; 2–3 — 1100; 4–9 — 1000; 10–99 — 900\n"
        "🇲🇦 МАРОККО: 1 — 1000; 2–3 — 900; 4–9 — 800; 10–99 — 750\n"
        "🇺🇸 США (для дзвінків потрібно поповнення): 1–3 — 1400; 4–9 — 1300; 10–99 — 1000\n\n"

        # FAQ (залишено без змін)
        "FAQ — відповідай цими формулюваннями, коротко й по суті:\n"
        "Як активувати SIM-карту?\n"
        "Просто вставте в телефон і почекайте поки сім-карта підключиться до мережі (або підключіться до мережі вручну через налаштування в телефоні).\n\n"
        "Чи зможу я зареєструвати месенджери?\n"
        "Так! Ви одразу зможете зареєструвати WhatsApp, Telegram, Viber та інші месенджери, а також прийняти SMS з будь-яких інших сервісів.\n\n"
        "Чи потрібно поповнювати?\n"
        "Ні. Сім-карта одразу працює на прийом SMS, але для вхідних та вихідних дзвінків потребує поповнення. Ми поповненнями, на жаль, не займаємось, при потребі ви можете зробити це самостійно використавши сервіс ding.com та платіжну систему PayPal.\n\n"
        "Скільки SIM-карта буде активна?\n"
        "Зазвичай після вставки в телефон до півроку. Встановленні ж месенджери будуть працювати навіть після деактивації сімки. Щоб сім-карта працювала більше ніж півроку, потрібно кожні 6 місяців поповнювати на 10 фунтів чи євро.\n\n"
        "Які тарифи?\n"
        "По тарифам ми нажаль не консультуємо, всю необхідну інформацію ви можете знайти на сайті вашого оператора.\n\n"

        "Стиль: дружелюбно, чітко, без води. Не повторюй уже надані дані."
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

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": price_msg})
        _prune_history(history)
        await update.message.reply_text(price_msg)
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
