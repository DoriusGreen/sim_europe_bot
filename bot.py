# telegram_sim_bot.py
# -*- coding: utf-8 -*-
"""
AI‑бот для продажу SIM‑карт у Telegram з логікою оформлення замовлень,
автовідправкою прайсу, відповідями на FAQ та інтеграцією з OpenAI
для вільного діалогу (fallback).

Сумісний із python-telegram-bot v20+ та openai==0.28.0 (класичний ChatCompletion).
Під вебхук (Render та ін.).
"""

import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import openai  # openai==0.28.0

# ===== Налаштування логів =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("sim-bot")

# ===== Ключі та налаштування =====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Повна HTTPS URL для вебхука
PORT = int(os.getenv("PORT", "8443"))

openai.api_key = OPENAI_API_KEY

# ===== Дані бізнес-логіки =====
COUNTRIES = {
    "gb": {"flag": "🇬🇧", "name": "ВЕЛИКОБРИТАНІЯ", "aliases": ["англія", "великобританія", "британия", "англия", "uk", "great britain", "britain"]},
    "nl": {"flag": "🇳🇱", "name": "НІДЕРЛАНДИ", "aliases": ["нідерланди", "голландія", "голландия", "netherlands", "holland"]},
    "de": {"flag": "🇩🇪", "name": "НІМЕЧЧИНА", "aliases": ["німеччина", "германія", "германия", "germany", "de"]},
    "fr": {"flag": "🇫🇷", "name": "ФРАНЦІЯ", "aliases": ["франція", "france", "fr"]},
    "es": {"flag": "🇪🇸", "name": "ІСПАНІЯ", "aliases": ["іспанія", "испания", "spain", "es"]},
    "cz": {"flag": "🇨🇿", "name": "ЧЕХІЯ", "aliases": ["чехія", "чехия", "czech", "czechia", "cz"]},
    "pl": {"flag": "🇵🇱", "name": "ПОЛЬЩА", "aliases": ["польща", "польша", "poland", "pl"]},
    "lt": {"flag": "🇱🇹", "name": "ЛИТВА", "aliases": ["литва", "lithuania", "lt"]},
    "lv": {"flag": "🇱🇻", "name": "ЛАТВІЯ", "aliases": ["латвія", "латвия", "latvia", "lv"]},
    "kz": {"flag": "🇰🇿", "name": "КАЗАХСТАН", "aliases": ["казахстан", "kazakhstan", "kz"]},
    "ma": {"flag": "🇲🇦", "name": "МАРОККО", "aliases": ["марокко", "morocco", "ma"]},
    "us": {"flag": "🇺🇸", "name": "США", "aliases": ["сша", "usa", "штати", "us"]},
}

# Ціни у вигляді "межа: ціна за шт.", де межа — це inclusive верхня межа діапазону
PRICE_TABLE: Dict[str, List[Tuple[int, int]]] = {
    # GB
    "gb": [
        (1, 350),
        (3, 325),
        (9, 300),
        (19, 275),
        (99, 250),
        (999, 210),  # 100+ — 210; 1000+ — договірна (обробимо окремо)
    ],
    # NL
    "nl": [
        (3, 800),
        (19, 750),
        (99, 700),
    ],
    # DE
    "de": [
        (3, 1100),
        (9, 1000),
        (99, 900),
    ],
    # FR
    "fr": [
        (3, 1400),
        (9, 1200),
        (99, 1100),
    ],
    # ES
    "es": [
        (3, 900),
        (9, 850),
        (99, 800),
    ],
    # CZ
    "cz": [
        (3, 750),
        (9, 700),
        (99, 650),
    ],
    # PL
    "pl": [
        (3, 500),
        (9, 450),
        (99, 400),
    ],
    # LT
    "lt": [
        (3, 750),
        (9, 700),
        (99, 650),
    ],
    # LV
    "lv": [
        (3, 750),
        (9, 700),
        (99, 650),
    ],
    # KZ
    "kz": [
        (1, 1200),
        (3, 1100),
        (9, 1000),
        (99, 900),
    ],
    # MA
    "ma": [
        (1, 1000),
        (3, 900),
        (9, 800),
        (99, 750),
    ],
    # US (needs top-up for activation)
    "us": [
        (3, 1400),
        (9, 1300),
        (99, 1000),
    ],
}

FAQ = {
    "активація": (
        "Як активувати SIM‑карту?",
        "Просто вставте в телефон і дочекайтеся підключення до мережі (або підключіться вручну в налаштуваннях).",
    ),
    "месенджери": (
        "Чи зможу я зареєструвати месенджери?",
        "Так. Можна реєструвати WhatsApp, Telegram, Viber та ін., і приймати SMS з будь‑яких сервісів.",
    ),
    "поповнення": (
        "Чи потрібно поповнювати?",
        "Для прийому SMS — не потрібно. Для дзвінків потрібне поповнення. Ми поповненнями не займаємось; можна через ding.com та PayPal.",
    ),
    "активність": (
        "Скільки SIM‑карта буде активна?",
        "Зазвичай до півроку після вставки. Встановлені месенджери працюють і після деактивації. Щоб працювала >6 міс — раз на 6 міс поповнювати на 10 фунтів/євро.",
    ),
    "тарифи": (
        "Які тарифи?",
        "По тарифах не консультуємо — див. сайт оператора вашої країни.",
    ),
}

US_NOTE = (
    "Примітка по 🇺🇸 США: для активації потрібне поповнення."
)

ORDER_FIELDS = ["name", "phone", "np", "items"]  # обов'язкові поля

# ===== Допоміжні функції =====

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def find_country_key(text: str) -> Optional[str]:
    t = normalize(text)
    for key, meta in COUNTRIES.items():
        if meta["name"].lower() in t:
            return key
        for a in meta["aliases"]:
            if a in t:
                return key
        if meta["flag"] in text:
            return key
    return None


def extract_quantity(text: str) -> Optional[int]:
    # Шукаємо число (шт, штуки тощо)
    m = re.search(r"(\d{1,4})\s*(шт|штук|штуки)?", text, flags=re.IGNORECASE)
    if m:
        try:
            q = int(m.group(1))
            return q if q > 0 else None
        except Exception:
            return None
    return None


def unit_price(country_key: str, qty: int) -> Optional[int]:
    # Договірна для 1000+
    if country_key == "gb" and qty >= 1000:
        return None
    tiers = PRICE_TABLE.get(country_key, [])
    for upper, price in tiers:
        if qty <= upper:
            return price
    # Якщо вийшли за межі — остання відома ціна (для safety)
    return tiers[-1][1] if tiers else None


def format_price_list() -> str:
    lines = ["Ось актуальний прайс на SIM‑карти:", ""]
    for key in [
        "gb", "nl", "de", "fr", "es", "cz", "pl", "lt", "lv", "kz", "ma", "us"
    ]:
        meta = COUNTRIES[key]
        lines.append(f"{meta['flag']} {meta['name']}")
        tiers = PRICE_TABLE[key]
        # Особливий рядок для GB 1000+
        for upper, price in tiers:
            if key == "gb" and upper == 999:
                lines.append("100+ шт. — 210 грн")
                lines.append("1000+ шт — договірна")
                break
            # Форматуємо діапазони як в ТЗ
            if key == "gb":
                # gb має 1; 2-3; 4-9; 10-19; 20-99; 100+; 1000+
                pass
        # Вивід узгодимо вручну під конкретну країну для точності:
        if key == "gb":
            lines.extend([
                "",
                "1 шт. — 350 грн",
                "2-3 шт. — 325 грн",
                "4-9 шт. — 300 грн",
                "10-19 шт. — 275 грн",
                "20-99 шт. — 250 грн",
                "",
                "100+ шт. — 210 грн",
                "1000+ шт — договірна",
            ])
        elif key == "nl":
            lines.extend([
                "",
                "1-3 шт. — 800 грн",
                "4-19 шт. — 750 грн",
                "20-99 шт. — 700 грн",
            ])
        elif key == "de":
            lines.extend([
                "",
                "1-3 шт. — 1100 грн",
                "4-9 шт. — 1000 грн",
                "10-99 шт. — 900 грн",
            ])
        elif key == "fr":
            lines.extend([
                "",
                "1-3 шт. — 1400 грн",
                "4-9 шт. — 1200 грн",
                "10-99 шт. — 1100 грн",
            ])
        elif key == "es":
            lines.extend([
                "",
                "1-3 шт. — 900 грн",
                "4-9 шт. — 850  грн",
                "10-99 шт. — 800 грн",
            ])
        elif key == "cz":
            lines.extend([
                "",
                "1-3 шт. — 750 грн",
                "4-9 шт. — 700  грн",
                "10-99 шт. — 650 грн",
            ])
        elif key == "pl":
            lines.extend([
                "",
                "1-3 шт. — 500 грн",
                "4-9 шт. — 450 грн",
                "10-99 шт. — 400 грн",
            ])
        elif key == "lt":
            lines.extend([
                "",
                "1-3 шт. — 750 грн",
                "4-9 шт. — 700  грн",
                "10-99 шт. — 650 грн",
            ])
        elif key == "lv":
            lines.extend([
                "",
                "1-3 шт. — 750 грн",
                "4-9 шт. — 700  грн",
                "10-99 шт. — 650 грн",
            ])
        elif key == "kz":
            lines.extend([
                "",
                "1 шт. — 1200 грн.",
                "2-3 шт. — 1100 грн.",
                "4-9 шт. — 1000 грн",
                "10-99 шт.  — 900 грн",
            ])
        elif key == "ma":
            lines.extend([
                "",
                "1 шт. — 1000 грн.",
                "2-3 шт. — 900 грн.",
                "4-9 шт. — 800 грн",
                "10-99 шт.  — 750 грн",
            ])
        elif key == "us":
            lines.extend([
                "",
                "1-3 шт. — 1400 грн",
                "4-9 шт. —  1300 грн",
                "10-99 шт. — 1000 грн",
                "",
                US_NOTE,
            ])
        lines.append("")
    return "\n".join(lines).strip()


PRICE_LIST_TEXT = format_price_list()


def detect_faq(text: str) -> Optional[str]:
    t = normalize(text)
    if any(k in t for k in ["актив", "встав", "підключ", "подключ", "активац"]):
        return "активація"
    if any(k in t for k in ["ватсап", "whatsapp", "телеграм", "viber", "месендж", "sms", "смс"]):
        return "месенджери"
    if any(k in t for k in ["поповн", "оплат", "ding", "paypal"]):
        return "поповнення"
    if any(k in t for k in ["активн", "скільки", "сколько", "півроку", "полгода", "6 міс"]):
        return "активність"
    if any(k in t for k in ["тариф", "план", "operator", "оператор"]):
        return "тарифи"
    return None


def ensure_user_state(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    # user_data: {
    #   "order": {"name": str, "phone": str, "np": str, "items": [{"country":"gb","qty":1}]}
    # }
    if "order" not in context.user_data:
        context.user_data["order"] = {"name": None, "phone": None, "np": None, "items": []}
    return context.user_data["order"]


def parse_contact_block(text: str) -> Dict[str, Optional[str]]:
    # Шукаємо ім'я/прізвище (рядок із двох слів, що починаються з літери)
    name = None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if re.match(r"^[A-Za-zА-Яа-яІіЇїЄєҐґ'`\-]+\s+[A-Za-zА-Яа-яІіЇїЄєҐґ'`\-]+$", l):
            name = l
            break

    # Телефон у форматі +380 ... або ін.
    phone_match = re.search(r"(\+?\d[\d\s()\-]{7,}\d)", text)
    phone = phone_match.group(1).strip() if phone_match else None

    # Нова пошта: шукаємо місто + ключові слова відділення/поштомат/№
    np = None
    for l in lines:
        if any(k in l.lower() for k in ["нова пошта", "нової пошти", "відділення", "№", "поштомат", "поштомата", "postomat", "поштомат "]):
            np = l
            break
        # Спрощено: місто + слово поштомат/відділення + номер
        if re.search(r"(місто|ирпін|ірпін|київ|львів|одеса|дніпро|харк|полтава).*(відділен|поштомат|№|#|n|no)", l, flags=re.I):
            np = l
            break
    return {"name": name, "phone": phone, "np": np}


def add_or_update_item(order: Dict[str, Any], country_key: str, qty: int) -> None:
    # Якщо вже є така країна — апдейтимо кількість
    for it in order["items"]:
        if it["country"] == country_key:
            it["qty"] = qty
            return
    order["items"].append({"country": country_key, "qty": qty})


def parse_items_line(text: str) -> List[Tuple[str, int]]:
    """Витягуємо (країна, кількість) із довільного тексту. Повертає список пар."""
    items = []
    # Спершу намагаємось знайти патерни типу "Англія 2", "🇬🇧 1", "PL x3" тощо
    chunks = re.split(r"[,;\n]", text)
    for ch in chunks:
        ck = find_country_key(ch)
        if ck:
            q = extract_quantity(ch) or 1
            items.append((ck, q))
    # Якщо нічого — пробуємо одну країну на весь текст
    if not items:
        ck = find_country_key(text)
        if ck:
            q = extract_quantity(text) or 1
            items.append((ck, q))
    return items


def format_order_summary(order: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Повертає (summary_text, admin_json)."""
    lines = []
    if order.get("name"):
        lines.append(order["name"])  # Ім'я Прізвище окремим рядком
    if order.get("phone"):
        lines.append(order["phone"])  # Телефон
    if order.get("np"):
        lines.append(order["np"])     # Місто + НП
    lines.append("")

    # Позиції
    total_sum = 0
    contract_needed = False
    for it in order.get("items", []):
        ck, qty = it["country"], it["qty"]
        meta = COUNTRIES.get(ck, {})
        up = unit_price(ck, qty)
        if ck == "gb" and qty >= 1000:
            contract_needed = True
            price_line = f"{meta.get('flag','')} {meta.get('name','')}, {qty} шт — договірна"
            lines.append(price_line)
        else:
            if up is None:
                price_line = f"{meta.get('flag','')} {meta.get('name','')}, {qty} шт — ціну уточнимо"
            else:
                price_line = f"{meta.get('flag','')} {meta.get('name','')}, {qty} шт — {up} грн"
                total_sum += up * qty
            lines.append(price_line)
        if ck == "us":
            lines.append(US_NOTE)

    # Підсумок
    lines.append("")
    if total_sum > 0:
        lines.append(f"Разом до сплати: {total_sum} грн")
    if contract_needed:
        lines.append("Для 1000+ шт. по 🇬🇧 — договірна ціна, уточнимо в особистих повідомленнях.")

    summary = "\n".join([l for l in lines if l is not None]).strip()

    admin_json = json.dumps(order, ensure_ascii=False)
    return summary, admin_json


def all_required_filled(order: Dict[str, Any]) -> bool:
    return all(order.get(f) for f in ORDER_FIELDS)


def missing_fields(order: Dict[str, Any]) -> List[str]:
    return [f for f in ORDER_FIELDS if not order.get(f)]


# ===== Команди =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order = ensure_user_state(context)
    welcome = (
        "Привіт! Я допоможу оформити замовлення на SIM‑карти.\n\n"
        + PRICE_LIST_TEXT
        + "\n\nЩоб оформити замовлення, надішліть будь ласка дані у довільному форматі або як список:\n\n"
        "1) Ім'я та прізвище\n2) Номер телефону\n3) Місто і № відділення/поштомату Нової Пошти\n4) Країна(и) та кількість SIM‑карт"
    )
    await update.message.reply_text(welcome)


# ===== Головний обробник повідомлень =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()
    order = ensure_user_state(context)

    # 1) Перевірка FAQ
    faq_key = detect_faq(text)
    if faq_key:
        title, answer = FAQ[faq_key]
        await update.message.reply_text(f"{title}\n\n{answer}")
        return

    # 2) Парсимо контактні дані (ім'я/телефон/НП), якщо користувач їх кинув одним блоком
    contact_guess = parse_contact_block(text)
    for k in ["name", "phone", "np"]:
        if contact_guess.get(k) and not order.get(k):
            order[k] = contact_guess[k]

    # 3) Парсимо позиції (країни + кількість)
    items = parse_items_line(text)
    for ck, qty in items:
        add_or_update_item(order, ck, qty)

    # 4) Якщо замовлення вже повне — формуємо підтвердження
    if all_required_filled(order):
        summary, _admin = format_order_summary(order)
        await update.message.reply_text(summary)
        # Подяка другим повідомленням
        await update.message.reply_text("Дякуємо за замовлення! Воно буде відправлене протягом 24 годин. 😊")

        # Зберігаємо останнє замовлення й очищаємо форму, щоб дозволити нове
        context.user_data["last_order"] = order.copy()
        context.user_data["order"] = {"name": None, "phone": None, "np": None, "items": []}
        return

    # 5) Інакше — запитуємо відсутні поля по черзі
    miss = missing_fields(order)
    prompts = {
        "name": "Будь ласка, вкажіть Ім'я та Прізвище (наприклад: Бондар Анастасія).",
        "phone": "Вкажіть, будь ласка, номер телефону у форматі +380...",
        "np": "Напишіть місто та № відділення/поштомату Нової Пошти (наприклад: Ірпінь поштомат 34863).",
        "items": "Які країни та скільки SIM‑карт потрібно? (можна так: 🇬🇧 1; 🇵🇱 2)"
    }

    # Якщо користувач натякнув на країну, але нема кількості — попросимо кількість
    if items and any(it[1] is None for it in items):
        await update.message.reply_text("Скільки штук потрібно?")
        return

    # Питаємо перше з відсутніх
    next_field = miss[0] if miss else "items"
    await update.message.reply_text(prompts[next_field])

    # 6) Якщо не вдалося класифікувати повідомлення —
    #    передаємо у GPT для ввічливої відповіді, але з підказкою бізнес-даних
    #    (робимо це ПІСЛЯ нашої логіки, щоб не заважало процесу оформлення)
    try:
        sys_prompt = (
            "Ти — дружелюбний і корисний Telegram‑бот магазину SIM‑карт.\n"
            "Відповідай лаконічно українською.\n"
            "Спершу намагайся допомогти з оформленням замовлення: збери ім'я+прізвище, телефон, місто+№ НП, країну та кількість.\n"
            "Ось прайс та правила:\n\n" + PRICE_LIST_TEXT + "\n\n"
            + US_NOTE + "\n\n"
            "FAQ:\n"
            "1) Активація: вставити SIM та дочекатися мережі (або вручну).\n"
            "2) Месенджери: можна реєструвати WhatsApp/Telegram/Viber, приймає SMS.\n"
            "3) Поповнення: для SMS не треба; для дзвінків потрібно (через ding.com/PayPal, самостійно).\n"
            "4) Активність: до півроку; для >6 міс — раз на 6 міс поповнення 10 фунтів/євро.\n"
            "5) Тарифи: дивитися на сайті оператора.\n"
        )
        resp = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=400,
            temperature=0.4,
        )
        ai_text = resp.choices[0].message["content"].strip()
        if ai_text:
            await update.message.reply_text(ai_text)
    except Exception as e:
        logger.warning(f"GPT fallback error: {e}")


# ===== Запуск застосунку =====
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не встановлено")
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL не встановлено (повинен бути повний HTTPS URL)")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="",  # порожній шлях теж підтримується в PTB v20
        webhook_url=WEBHOOK_URL,
    )


if __name__ == "__main__":
    main()
