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

# ==== Евристики наміру/даних ====
ORDER_INTENT_KEYWORDS = [
    # UA
    "замовити", "замовлення", "оформити", "оформлення",
    "потрібна", "потрібні", "потрібно", "візьму", "купити",
    # RU
    "заказать", "заказ", "сделать заказ", "оформить", "оформление",
    "оформить заказ", "купить", "нужно", "нужна", "нужны", "возьму", "могу заказать"
]

COUNTRY_KEYWORDS = [
    "англія","велика британія","великобританія","uk","нідерланди","німеччина","франція",
    "іспанія","чехія","польща","литва","латвія","казахстан","марокко","сша","usa","америка"
]

def looks_like_order_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ORDER_INTENT_KEYWORDS)

def contains_any_required_field(text: str) -> tuple[bool, list]:
    t = (text or "").lower()
    # телефон
    has_phone = bool(re.search(r'(\+?3?8?0?\D*\d{2}\D*\d{3,4}\D*\d{3,4})', t)) or bool(re.search(r'\b0\d{2}\D*\d{3,4}\D*\d{3,4}\b', t))
    # відділення/поштомат
    has_np = ("поштомат" in t and re.search(r'\d{3,6}', t)) or ("№" in t and re.search(r'\d+', t)) or ("нової пошти" in t or "нова пошта" in t)
    # країна + кількість
    has_country_word = any(k in t for k in COUNTRY_KEYWORDS)
    has_qty = bool(re.search(r'\d+', t))
    has_country_qty = has_country_word and has_qty

    missing = []
    if not has_phone:
        missing.append("2. Номер телефону.")
    if not has_np:
        missing.append("3. Місто та № відділення \"Нової Пошти\".")
    if not has_country_qty:
        missing.append("4. Країна(и) та кількість sim-карт.")
    return (has_phone or has_np or has_country_qty, missing)

def normalize_country(name: str) -> str:
    n = (name or "").strip().upper()
    # Синоніми
    if n in ("АНГЛІЯ", "UK", "UNITED KINGDOM", "ВБ", "GREAT BRITAIN"):
        return "ВЕЛИКОБРИТАНІЯ"
    if n in ("USA", "UNITED STATES", "ШТАТИ"):
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

# ==== Шаблони підсумку ====
ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн  \n"

@dataclass
class OrderItem:
    country: str
    qty: int

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
        disp = DISPLAY.get(c_norm, it.country.strip().title())
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
        items = [OrderItem(country=i["country"], qty=int(i["qty"])) for i in data.get("items", [])]
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

# ==== СИСТЕМНИЙ ПРОМПТ ====
def build_system_prompt() -> str:
    return (
        "Ти — дружелюбний і корисний Telegram-бот-магазин SIM-карт. "
        "На початку чату клієнт уже отримав прайси від аккаунта власника — ти їх не дублюєш, а підхоплюєш діалог. "
        "Відповідай по суті, запамʼятовуй контекст, не перепитуй одне й те саме.\n\n"

        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення «Нової Пошти».\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        "Якщо бракує ДЕЯКИХ пунктів — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "📝 Залишилось вказати:\n"
        "\n"
        "<залиши лише відсутні рядки з їхніми номерами, напр.>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        "Коли ВСІ дані є — ВІДПОВІДАЙ ЛИШЕ JSON за схемою (без підсумку, без зайвого тексту):\n"
        "{\n"
        '  "full_name": "Імʼя Прізвище",\n'
        '  "phone": "0XX-XXXX-XXX",\n'
        '  "city": "Місто",\n'
        '  "np": "Номер відділення або поштомат",\n'
        '  "items": [ {"country":"КРАЇНА","qty":N}, ... ]\n'
        "}\n\n"

        "Після того, як ти віддаєш JSON, бекенд сам порахує ціни/суми та сформує підсумок у потрібному форматі. "
        "«Загальна сумма» показується тільки якщо країн 2 або більше.\n\n"

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
        "Привіт! Я допоможу з SIM-картами: підкажу по країнах, цінах та оформлю замовлення. "
        "Напишіть, будь ласка, для якої країни( країн) і скільки штук потрібно — і, якщо готові, "
        "одразу вкажіть дані для доставки (ПІБ, телефон, місто й № відділення/поштомату НП)."
    )
    await update.message.reply_text(text)

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip() if update.message and update.message.text else ""
    history = _ensure_history(context)

    # Перевірка наявності даних у поточному повідомленні
    has_fields, missing_fields = contains_any_required_field(user_message)

    # Якщо намір замовлення і немає жодних полів — просимо все
    if looks_like_order_intent(user_message) and not has_fields:
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": ORDER_INFO_REQUEST})
        _prune_history(history)
        await update.message.reply_text(ORDER_INFO_REQUEST)
        return

    # Якщо є хоча б одне поле і є відсутні — просимо тільки їх
    if has_fields and missing_fields:
        response = "📝 Залишилось вказати:\n\n" + "\n".join(missing_fields)
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": response})
        _prune_history(history)
        await update.message.reply_text(response)
        return

    # Виклик до GPT для інших випадків (наприклад, FAQ або уточнень)
    reply_text = await _ask_gpt(history, user_message)

    # Якщо модель віддала "просимо відсутні пункти", виправляємо заголовок на емодзі
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

    # Інакше — звичайна відповідь моделі
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
