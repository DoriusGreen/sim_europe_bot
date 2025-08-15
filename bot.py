# bot.py
import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import openai

# ===== Налаштування логів =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Ключі та налаштування =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Токен бота від BotFather
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")      # API ключ OpenAI
WEBHOOK_URL = os.getenv("WEBHOOK_URL")            # Повна URL-адреса вебхука (https://...)
PORT = int(os.getenv("PORT", "8443"))

openai.api_key = OPENAI_API_KEY

# ===== Команда /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я GPT-бот. Напиши мені повідомлення, і я відповім.")

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    try:
        # Виклик до GPT-4o
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ти — дружелюбний і корисний Telegram-бот."},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500
        )

        reply_text = response.choices[0].message["content"]
        await update.message.reply_text(reply_text)

    except Exception as e:
        logger.error(f"Помилка при зверненні до OpenAI: {e}")
        await update.message.reply_text("Сталася помилка при отриманні відповіді від GPT.")

# ===== Запуск програми =====
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск вебхука
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
