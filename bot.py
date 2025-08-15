# bot.py
import os
import logging
from typing import List, Dict

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

# ==== Константи пам'яті ====
MAX_TURNS = 14  # скільки останніх реплік зберігати в історії (користувач+бот = 1 "turn")
THANK_YOU_TAG = "<SEND_THANK_YOU>"  # якщо модель поверне цей тег — надішлемо окреме "дякуємо"

def build_system_prompt() -> str:
    """
    Весь бізнес-процес у промпті: ціни, правила, FAQ, логіка діалогу.
    """
    return (
        "Ти — дружелюбний і корисний Telegram-бот-магазин SIM-карт. "
        "На початку чату клієнт уже отримав прайси від аккаунта власника — ти їх не дублюєш, а підхоплюєш діалог. "
        "Відповідай по суті, запамʼятовуй контекст, не перепитуй одне й те саме.\n\n"

        "ПОВНЕ замовлення складається з 4 пунктів:\n"
        "1. Ім'я та прізвище.\n"
        "2. Номер телефону.\n"
        "3. Місто та № відділення «Нової Пошти».\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        "Коли бракує ДЕЯКИХ пунктів — відповідай СУВОРО в такому вигляді (без зайвого тексту до/після):\n"
        "Залишилось вказати такі пункти:\n\n"
        "<залиши лише відсутні рядки в точному форматі з їхніми номерами з переліку вище, "
        "напр.:>\n"
        "2. Номер телефону.\n"
        "4. Країна(и) та кількість sim-карт.\n\n"

        "Коли ВСІ дані є — відправ ПІДСУМОК замовлення у ТОЧНОМУ форматі нижче (дотримуйся пробілів і порожніх рядків):\n"
        "Імʼя Прізвище \n"
        "0XX-XXXX-XXX\n"
        "Місто № N  \n"
        "\n"
        "🇬🇧 Англія, 2 шт — 650 грн  \n"
        "🇩🇪 Німеччина, 4 шт — 4000 грн  \n"
        "\n"
        "Загальна сумма: 4650 грн\n"
        "<Пояснення: у рядках з країнами став суму за цією країною (кількість × ціна за одиницю), "
        "а внизу — загальну суму. Порожні рядки і подвійні пробіли наприкінці рядків збережи як у зразку.>\n"
        "Після підсумку на новому рядку додай тег <SEND_THANK_YOU>.\n\n"

        "Як ставити уточнення:\n"
        "• Якщо клієнт уже дав частину даних — НЕ повторюй їх. Перелічуй лише відсутні пункти у форматі вище.\n"
        "• Якщо клієнт просить «ціна?» — відповідай конкретно по згаданій країні(ях), без довгих таблиць.\n\n"

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

        "Правила підрахунку:\n"
        "• Вибирай ціну за одиницю по КОЖНІЙ країні окремо (залежно від кількості по цій країні), рядок країни показує саме "
        "суму за країною (qty × unit). У «Загальна сумма» — підсумовуй усі країни.\n"
        "• Якщо 1000+ по Великій Британії — пиши «договірна» у рядку цієї країни і не рахуйте її в загальну суму.\n\n"

        "FAQ (відповідай коротко):\n"
        "• Активація: вставити SIM, дочекатися мережі (або вибрати мережу вручну).\n"
        "• Месенджери: WhatsApp/Telegram/Viber — так; SMS з інших сервісів прийдуть.\n"
        "• Поповнення: для SMS — не потрібно; для дзвінків — потрібно (ding.com + PayPal). Ми поповнення не робимо.\n"
        "• Активність: зазвичай до півроку після вставки; месенджери працюватимуть і після деактивації. "
        "Щоб подовжити — раз на 6 міс поповнюйте ~10 GBP/EUR.\n"
        "• Тарифи: не консультуємо, дивіться сайт оператора.\n"

        "Стиль: дружелюбно, чітко, без води. Не повторюй уже надані дані.\n"
    )

def _ensure_history(ctx: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, str]]:
    if "history" not in ctx.chat_data:
        ctx.chat_data["history"] = []
    return ctx.chat_data["history"]

def _prune_history(history: List[Dict[str, str]]) -> None:
    # обрізаємо старі репліки, залишаємо MAX_TURNS*2 повідомлень (user+assistant)
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
    # Не дублюємо прайси. Коротко представляємося і пропонуємо допомогу.
    text = (
        "Привіт! Я допоможу з SIM-картами: підкажу по країнах, цінах та оформлю замовлення. "
        "Напишіть, будь ласка, для якої країни(країн) і скільки штук потрібно — і, якщо готові, "
        "одразу вкажіть дані для доставки (ПІБ, телефон, місто й № відділення/поштомату НП)."
    )
    await update.message.reply_text(text)

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip() if update.message and update.message.text else ""
    history = _ensure_history(context)

    # Виклик до GPT з пам'яттю
    reply_text = await _ask_gpt(history, user_message)

    # Оновлюємо пам’ять
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": reply_text})
    _prune_history(history)

    # Відправляємо відповідь(і)
    if THANK_YOU_TAG in reply_text:
        # Розділяємо: все до тега — як основна відповідь
        main_reply = reply_text.replace(THANK_YOU_TAG, "").rstrip()
        if main_reply:
            await update.message.reply_text(main_reply)
        # Друге повідомлення — дякуємо
        await update.message.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
    else:
        await update.message.reply_text(reply_text)

# ===== Запуск програми =====
def main():
    if not TELEGRAM_TOKEN or not OPENAI_API_KEY or not WEBHOOK_URL:
        raise RuntimeError("Не задано TELEGRAM_BOT_TOKEN, OPENAI_API_KEY або WEBHOOK_URL")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск вебхука
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",            # якщо у тебе є конкретний шлях — вкажи його і додай до WEBHOOK_URL
        webhook_url=WEBHOOK_URL # має бути повна https URL твого вебхука
    )

if __name__ == "__main__":
    main()
