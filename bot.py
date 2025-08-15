# bot.py
import os
import logging
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import openai

# ===== Логи =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Ключі/налаштування =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8443"))

openai.api_key = OPENAI_API_KEY

# ===== Наша логіка =====
from logic import load_products, find_product, unit_price_by_tiers, format_reply_plain
from intent_llm import build_intent_messages

# Якщо захочеш "красивий текст" від GPT для фінального повідомлення — постав True
USE_GPT_TEXT = False

# ===== Команди =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Напишіть країну та кількість (наприклад: «2 шт Англія, 2 шт Німеччина»).\n"
        "Я визначу позиції, прорахую ціну та запропоную оформлення."
    )

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

# ===== Обробка повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = (update.message.text or "").strip()
    if not user_message:
        return

    try:
        # 1) GPT → витяг наміру (JSON з items)
        products = load_products()
        messages = build_intent_messages(user_message, products)
        gpt_resp = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=messages,
            temperature=0,
            max_tokens=200
        )
        intent_raw = gpt_resp.choices[0].message["content"]

        try:
            intent = json.loads(intent_raw)
        except Exception:
            await update.message.reply_text("Не зрозумів запит. Уточніть країну/кількість, будь ласка.")
            return

        items = intent.get("items", [])
        if not items:
            await update.message.reply_text("Не знайшов позицій у каталозі. Напишіть, будь ласка, конкретніше.")
            return

        # 2) Локальна математика по tiers (і обробка договірної ціни)
        calced, grand_total = [], 0
        has_negotiable = False

        for it in items:
            pid = it.get("product_id")
            qty = max(1, int(it.get("qty", 1)))
            product = find_product(products, pid) if pid else None
            if not product:
                continue

            try:
                unit = unit_price_by_tiers(qty, product.get("tiers"), product.get("base_price"))
                total = unit * qty
                grand_total += total
                calced.append({
                    "id": pid,
                    "name": product["name"],
                    "qty": qty,
                    "unit_price": unit,
                    "line_total": total
                })
            except ValueError as ex:
                # NEGOTIABLE_OR_NO_PRICE
                has_negotiable = True
                calced.append({
                    "id": pid,
                    "name": product["name"],
                    "qty": qty,
                    "unit_price": "договірна",
                    "line_total": "договірна"
                })

        if not calced:
            await update.message.reply_text("Каталог не містить таких позицій. Спробуйте інакше сформулювати запит.")
            return

        # 3A) Простий текст (без GPT №2) — рекомендовано на старті
        reply_text = format_reply_plain(calced, grand_total, negotiable=has_negotiable)
        await update.message.reply_text(reply_text)

        # 3B) Якщо захочеш «красиву» подачу — увімкни USE_GPT_TEXT = True
        # if USE_GPT_TEXT:
        #     pretty = openai.ChatCompletion.create(
        #         model="gpt-4o",
        #         temperature=0,
        #         messages=[
        #             {"role": "system",
        #              "content": "Сформуй короткий акуратний рахунок українською. "
        #                         "НЕ змінюй жодні числа, назви чи суми. "
        #                         "Якщо у позиції 'договірна' — просто познач це."},
        #             {"role": "user",
        #              "content": json.dumps({"items": calced, "grand_total": grand_total}, ensure_ascii=False)}
        #         ],
        #         max_tokens=250
        #     ).choices[0].message["content"]
        #     await update.message.reply_text(pretty + "\nОформити замовлення?")

    except Exception as e:
        logger.exception("Помилка при обробці повідомлення")
        await update.message.reply_text("Сталася помилка при обробці запиту. Спробуйте ще раз, будь ласка.")

# ===== Запуск програми =====
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задано")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY не задано")
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL не задано")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обробники
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Вебхук
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",
        webhook_url=WEBHOOK_URL
    )

if __name__ == "__main__":
    main()
