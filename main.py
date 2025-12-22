import time
import logging
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Імпорти з наших нових файлів
import config
import tools
import ai

# Налаштування логів
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== /start =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text("Вітаю! Я допоможу вам оформити замовлення на SIM-карти, а також постараюсь надати відповіді на всі ваші запитання.")

# ===== Менеджер повідомлень (Головна логіка) =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg: return
    raw_user_message = msg.text.strip() if msg.text else ""
    
    # --- Ініціалізація історії ---
    if "history" not in context.chat_data: context.chat_data["history"] = []
    history = context.chat_data["history"]

    # --- 1. Обробка команд МЕНЕДЖЕРА в групі замовлень ---
    if (msg.chat and msg.chat.id == config.ORDER_FORWARD_CHAT_ID and 
        msg.from_user and msg.from_user.username and 
        msg.from_user.username.lower() == (config.DEFAULT_OWNER_USERNAME or "").strip().lstrip("@").lower()):
        
        if msg.reply_to_message:
            # Редагування існуючого замовлення через reply
            is_paid = bool(tools.PAID_HINT_RE.search(raw_user_message))
            operator = tools.canonical_operator(raw_user_message)
            note_match = tools.NOTE_REPLY_RE.search(raw_user_message)
            orig_text = msg.reply_to_message.text or ""
            
            final_text = None
            if is_paid:
                lines = [l for l in orig_text.splitlines() if not tools.TOTAL_LINE_RE.search(l)]
                final_text = "\n".join(tools.PRICE_LINE_RE.sub("— (замовлення оплачене)", l) for l in lines)
            elif operator:
                lines = []
                for l in orig_text.splitlines():
                    if ("Англія" in l or "ВЕЛИКОБРИТАНІЯ" in l) and "оператор" not in l:
                        l = l.replace(",", f" (оператор {operator}),", 1)
                    lines.append(l)
                final_text = "\n".join(lines)
            elif note_match:
                note = note_match.group(1).strip()
                if note: final_text = orig_text.strip() + f"\n\n⚠️ Примітка: {note}"

            if final_text:
                try:
                    await context.bot.delete_message(msg.chat.id, msg.reply_to_message.message_id)
                    await context.bot.delete_message(msg.chat.id, msg.message_id)
                except Exception as e: logger.warning(f"Del msg error: {e}")
                await context.bot.send_message(msg.chat.id, final_text)
                return

        # Парсинг нового замовлення від менеджера через GPT
        parts = re.split(r'\bпримітка[:\s]*', raw_user_message, maxsplit=1, flags=re.IGNORECASE)
        text_for_gpt, note_text = (parts[0], parts[1].strip()) if len(parts) > 1 else (raw_user_message, None)
        
        json_resp = await ai.ask_gpt_to_parse_manager_order(text_for_gpt)
        parsed = tools.try_parse_manager_order_json(json_resp)
        if parsed:
            try: await context.bot.delete_message(msg.chat.id, msg.message_id)
            except: pass
            formatted = tools.render_order_for_group(parsed, paid=bool(tools.PAID_HINT_RE.search(raw_user_message))).strip()
            if note_text: formatted += f"\n\n⚠️ Примітка: {note_text}"
            await context.bot.send_message(msg.chat.id, formatted)
        return

    # --- 2. Якщо пише Менеджер (ігноруємо або додаємо в історію) ---
    if config.MANAGER_USER_IDS and msg.from_user and msg.from_user.id in config.MANAGER_USER_IDS: return

    # --- 3. Підготовка контексту для користувача ---
    user_payload = raw_user_message
    quoted = tools.extract_quoted_text(msg)
    if quoted: user_payload += f"\n\n[ЦЕ ПРОЦИТОВАНЕ ПОВІДОМЛЕННЯ КЛІЄНТА:]\n{quoted}"

    # Підказки для пункту 4 (кількість/країни)
    last_countries = context.chat_data.get("last_price_countries")
    qty_only = tools.detect_qty_only(raw_user_message)
    if qty_only and last_countries:
        context.chat_data["point4_hint"] = {"qty": qty_only, "countries": last_countries, "ts": time.time()}
    p4_items = tools.detect_point4_items(raw_user_message)
    if p4_items:
        context.chat_data["point4_hint"] = {"items": p4_items, "ts": time.time()}

    if context.chat_data.get("point4_hint"):
        h = context.chat_data["point4_hint"]
        if "qty" in h: user_payload += f"\n\n[НАГАДУВАННЯ: пункт 4 відомий: {', '.join(h['countries'])} по {h['qty']} шт.]"
        elif "items" in h: user_payload += f"\n\n[НАГАДУВАННЯ: пункт 4 відомий: {h['items']}]"

    # --- 4. Force Point 4 (Спроба дозбирати замовлення) ---
    if context.chat_data.get("awaiting_missing") == {4}:
        force_json = await ai.ask_gpt_force_point4(history, user_payload)
        forced = tools.try_parse_order_json(force_json)
        if forced and forced.items and all([forced.full_name, forced.phone, forced.city, forced.np]):
            valid_items, out_of_stock = [], {}
            for item in forced.items:
                c_key = tools.normalize_country(item.country).upper()
                if c_key not in config.PRICE_TIERS: continue
                stat, reas = config.get_availability(c_key)
                if stat == "+": valid_items.append(item)
                else: out_of_stock[c_key] = reas
            
            if out_of_stock: await msg.reply_text(tools.render_out_of_stock(out_of_stock))
            if not valid_items:
                context.chat_data.pop("awaiting_missing", None)
                context.chat_data.pop("point4_hint", None)
                return
            
            forced.items = valid_items
            summary = tools.render_order(forced)
            context.chat_data["last_order_sig"] = tools.order_signature(forced)
            context.chat_data["last_order_time"] = time.time()
            context.chat_data.pop("awaiting_missing", None)
            context.chat_data.pop("point4_hint", None)
            
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": summary})
            await msg.reply_text(summary)
            await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
            try: await context.bot.send_message(config.ORDER_FORWARD_CHAT_ID, f"@{msg.from_user.username}\n{summary}" if msg.from_user.username else summary)
            except Exception as e: logger.warning(f"Forward error: {e}")
            return

    # --- 5. Основний запит до GPT ---
    reply_text = await ai.ask_gpt_main(history, user_payload)
    
    # Виправлення "Залишилось вказати"
    if "Залишилось вказати:" in reply_text and "📝" not in reply_text:
        reply_text = reply_text.replace("Залишилось вказати:", "📝 Залишилось вказати:")
    if reply_text.strip().startswith("🛒 Для оформлення") and context.chat_data.get("awaiting_missing") == {1, 2, 3}:
        reply_text = "📝 Залишилось вказати:\n\n1. Ім'я та прізвище.\n2. Номер телефону.\n3. Місто та № відділення."

    # --- 6. Обробка відповідей GPT (JSON або текст) ---
    
    # А) Сформоване замовлення
    parsed = tools.try_parse_order_json(reply_text)
    if parsed and parsed.items and all([parsed.full_name, parsed.phone, parsed.city, parsed.np]):
        valid_items, out_of_stock = [], {}
        for item in parsed.items:
            c_key = tools.normalize_country(item.country).upper()
            if c_key not in config.PRICE_TIERS: continue
            stat, reas = config.get_availability(c_key)
            if stat == "+": valid_items.append(item)
            else: out_of_stock[c_key] = reas
        
        if out_of_stock:
            await msg.reply_text(tools.render_out_of_stock(out_of_stock))
            if valid_items: await msg.reply_text("Чи відправити лише ті позиції, що є в наявності, або бажаєте зробити заміну?")
            else: await msg.reply_text("Можливо, вас зацікавить якась інша країна з нашого асортименту?")
            return

        if not valid_items: return
        parsed.items = valid_items

        # Перевірка дублікатів
        sig = tools.order_signature(parsed)
        if sig == context.chat_data.get("last_order_sig") and (time.time() - context.chat_data.get("last_order_time", 0) <= config.ORDER_DUP_WINDOW_SEC):
             context.chat_data.pop("awaiting_missing", None); return

        summary = tools.render_order(parsed)
        context.chat_data["last_order_sig"] = sig
        context.chat_data["last_order_time"] = time.time()
        context.chat_data.pop("awaiting_missing", None)
        context.chat_data.pop("point4_hint", None)
        
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": summary})
        await msg.reply_text(summary)
        await msg.reply_text("Дякуємо за замовлення, воно буде відправлено протягом 24 годин. 😊")
        try: await context.bot.send_message(config.ORDER_FORWARD_CHAT_ID, f"@{msg.from_user.username}\n{summary}" if msg.from_user.username else summary)
        except Exception as e: logger.warning(f"Forward error: {e}")
        return

    # Б) Запит цін
    price_countries = tools.try_parse_price_json(reply_text)
    if price_countries is not None:
        want_all = any(str(c).upper() == "ALL" for c in price_countries)
        keys_to_show = list(config.PRICE_TIERS.keys()) if want_all else [tools.normalize_country(str(c)).upper() for c in price_countries if str(c).strip()]
        
        valid, out_of_stock, invalid = [], {}, []
        for k in set(keys_to_show):
            if k in config.PRICE_TIERS:
                st, r = config.get_availability(k)
                if st == "+": valid.append(k)
                else: out_of_stock[k] = r
            else:
                if not want_all: invalid.append(k)

        context.chat_data["last_price_countries"] = [k for k in (keys_to_show if want_all else valid) if k in config.PRICE_TIERS]

        if valid:
            txt = tools.render_prices(valid)
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": txt})
            await msg.reply_text(txt)
        if out_of_stock: await msg.reply_text(tools.render_out_of_stock(out_of_stock))
        if invalid: await msg.reply_text(tools.render_unavailable(invalid))
        if not valid and not out_of_stock and not invalid and want_all: await msg.reply_text("На жаль, наразі всі SIM-карти відсутні.")

        # Follow-up
        follow = await ai.ask_gpt_followup(history, user_payload)
        ussd = tools.try_parse_ussd_json(follow)
        if ussd:
            txt = tools.render_ussd_targets(ussd) or tools.FALLBACK_PLASTIC_MSG
            history.append({"role": "assistant", "content": txt})
            await msg.reply_text(txt)
            context.chat_data.pop("awaiting_missing", None)
            return
        if tools.is_meaningful_followup(follow):
            history.append({"role": "assistant", "content": follow})
            await msg.reply_text(follow)
        context.chat_data.pop("awaiting_missing", None)
        return

    # В) Запит USSD
    ussd_targets = tools.try_parse_ussd_json(reply_text)
    if ussd_targets is not None:
        if ussd_targets:
            txt = tools.render_ussd_targets(ussd_targets) or tools.FALLBACK_PLASTIC_MSG
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": txt})
            context.chat_data.pop("awaiting_missing", None)
            await msg.reply_text(txt)
        else:
            txt = "Будь ласка, уточніть, для якої країни вам потрібна USSD-комбінація?"
            history.append({"role": "user", "content": raw_user_message})
            history.append({"role": "assistant", "content": txt})
            await msg.reply_text(txt)
        return

    # Г) Звичайний текст або уточнення пунктів
    missing = tools.missing_points_from_reply(reply_text)
    if missing: context.chat_data["awaiting_missing"] = missing
    else: 
        if context.chat_data.get("awaiting_missing") != {1, 2, 3}: context.chat_data.pop("awaiting_missing", None)
    
    if reply_text:
        history.append({"role": "user", "content": raw_user_message})
        history.append({"role": "assistant", "content": reply_text})
        if len(history) > config.MAX_TURNS * 2: del history[: len(history) - config.MAX_TURNS * 2]
        await msg.reply_text(reply_text)

# ===== Запуск =====
def main():
    if not config.TELEGRAM_TOKEN or not config.OPENAI_API_KEY or not config.WEBHOOK_URL:
        raise RuntimeError("Не задано TELEGRAM_BOT_TOKEN, OPENAI_API_KEY або WEBHOOK_URL")
    
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_webhook(listen="0.0.0.0", port=config.PORT, url_path="", webhook_url=config.WEBHOOK_URL)

if __name__ == "__main__":
    main()
