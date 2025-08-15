# bot.py
# -*- coding: utf-8 -*-
"""
AI-помічник для продажу SIM-карт у Telegram з ПАМ'ЯТТЮ (SQLite).
- Пам'ять по chat_id: стан замовлення, остання країна, greeted, історія (останні 8 реплік)
- Не дублює прайс і привітання; просить усі відсутні поля ОДНИМ повідомленням
- Підтримує сценарій: спочатку країна → потім лише кількість
- GPT як fallback з короткою історією

python-telegram-bot v20+, openai==0.28.0
"""

import os, re, json, logging, sqlite3
from contextlib import closing
from typing import Dict, Any, List, Optional, Tuple

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import openai

# ==== Конфіг ====
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "8443"))
openai.api_key = OPENAI_API_KEY

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("sim-bot")

DB_PATH = os.getenv("STATE_DB_PATH", "state.db")

# ==== Довідкові дані ====
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

# межа (вкл.) → ціна за шт.
PRICE_TABLE: Dict[str, List[Tuple[int, int]]] = {
    "gb": [(1,350),(3,325),(9,300),(19,275),(99,250),(999,210)],
    "nl": [(3,800),(19,750),(99,700)],
    "de": [(3,1100),(9,1000),(99,900)],
    "fr": [(3,1400),(9,1200),(99,1100)],
    "es": [(3,900),(9,850),(99,800)],
    "cz": [(3,750),(9,700),(99,650)],
    "pl": [(3,500),(9,450),(99,400)],
    "lt": [(3,750),(9,700),(99,650)],
    "lv": [(3,750),(9,700),(99,650)],
    "kz": [(1,1200),(3,1100),(9,1000),(99,900)],
    "ma": [(1,1000),(3,900),(9,800),(99,750)],
    "us": [(3,1400),(9,1300),(99,1000)],
}
US_NOTE = "Примітка по 🇺🇸 США: для активації потрібне поповнення."

FAQ = {
    "активація": ("Як активувати SIM-карту?", "Просто вставте в телефон і дочекайтеся підключення (або вручну)."),
    "месенджери": ("Чи зможу я зареєструвати месенджери?", "Так. Можна реєструвати WhatsApp/Telegram/Viber і приймати SMS з будь-яких сервісів."),
    "поповнення": ("Чи потрібно поповнювати?", "Для SMS — ні. Для дзвінків потрібне поповнення (самостійно через ding.com і PayPal)."),
    "активність": ("Скільки SIM-карта буде активна?", "Зазвичай до півроку; щоб >6 міс — раз на 6 міс поповнення на 10 фунтів/євро."),
    "тарифи": ("Які тарифи?", "По тарифах не консультуємо — дивіться сайт оператора."),
}

# ==== Прайс як текст для промта (не шлемо клієнту без запиту) ====
def _format_price_list() -> str:
    return (
        "🇬🇧 ВЕЛИКОБРИТАНІЯ\\n1 шт. — 350 грн\\n2-3 шт. — 325 грн\\n4-9 шт. — 300 грн\\n10-19 шт. — 275 грн\\n20-99 шт. — 250 грн\\n\\n100+ шт. — 210 грн\\n1000+ шт — договірна\\n\\n"
        "🇳🇱 НІДЕРЛАНДИ\\n1-3 шт. — 800 грн\\n4-19 шт. — 750 грн\\n20-99 шт. — 700 грн\\n\\n"
        "🇩🇪 НІМЕЧЧИНА\\n1-3 шт. — 1100 грн\\n4-9 шт. — 1000 грн\\n10-99 шт. — 900 грн\\n\\n"
        "🇫🇷 ФРАНЦІЯ\\n1-3 шт. — 1400 грн\\n4-9 шт. — 1200 грн\\n10-99 шт. — 1100 грн\\n\\n"
        "🇪🇸 ІСПАНІЯ\\n1-3 шт. — 900 грн\\n4-9 шт. — 850 грн\\n10-99 шт. — 800 грн\\n\\n"
        "🇨🇿 ЧЕХІЯ\\n1-3 шт. — 750 грн\\n4-9 шт. — 700 грн\\n10-99 шт. — 650 грн\\n\\n"
        "🇵🇱 ПОЛЬЩА\\n1-3 шт. — 500 грн\\n4-9 шт. — 450 грн\\n10-99 шт. — 400 грн\\n\\n"
        "🇱🇹 ЛИТВА\\n1-3 шт. — 750 грн\\n4-9 шт. — 700 грн\\n10-99 шт. — 650 грн\\n\\n"
        "🇱🇻 ЛАТВІЯ\\n1-3 шт. — 750 грн\\n4-9 шт. — 700 грн\\n10-99 шт. — 650 грн\\n\\n"
        "🇰🇿 КАЗАХСТАН\\n1 шт. — 1200 грн\\n2-3 шт. — 1100 грн\\n4-9 шт. — 1000 грн\\n10-99 шт. — 900 грн\\n\\n"
        "🇲🇦 МАРОККО\\n1 шт. — 1000 грн\\n2-3 шт. — 900 грн\\n4-9 шт. — 800 грн\\n10-99 шт. — 750 грн\\n\\n"
        "🇺🇸 США\\n1-3 шт. — 1400 грн\\n4-9 шт. — 1300 грн\\n10-99 шт. — 1000 грн\\n\\n" + US_NOTE
    )
PRICE_LIST_TEXT = _format_price_list()

# ==== Утиліти парсингу ====
def normalize(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())

def find_country_key(text: str) -> Optional[str]:
    t = normalize(text)
    for key, meta in COUNTRIES.items():
        if meta["flag"] in text or meta["name"].lower() in t or any(a in t for a in meta["aliases"]):
            return key
    return None

def extract_quantity(text: str) -> Optional[int]:
    m = re.search(r"(\d{1,4})\s*(шт|штук|штуки|x)?", text, flags=re.I)
    return int(m.group(1)) if m else None

def parse_items_line(text: str) -> List[Tuple[str, int]]:
    items = []
    for ch in re.split(r"[,;\n]", text):
        ck = find_country_key(ch)
        if ck:
            q = extract_quantity(ch) or 1
            items.append((ck, q))
    if not items:
        ck = find_country_key(text)
        if ck:
            items.append((ck, extract_quantity(text) or 1))
    return items

def detect_quantity_only(text: str) -> Optional[int]:
    t = normalize(text)
    m = re.match(r"^(\d{1,4})\s*(шт|штук|штуки|x)?$", t)
    return int(m.group(1)) if m else None

# ==== Пам'ять (SQLite) ====
def db_init():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS dialogs (
            chat_id INTEGER PRIMARY KEY,
            state_json   TEXT NOT NULL,
            history_json TEXT NOT NULL,
            greeted      INTEGER NOT NULL,
            last_country TEXT
        )""")
        conn.commit()

DEFAULT_STATE = {"order": {"name": None, "phone": None, "np": None, "items": []}}

def load_dialog(chat_id: int) -> Dict[str, Any]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute("SELECT state_json, history_json, greeted, last_country FROM dialogs WHERE chat_id=?", (chat_id,))
        row = cur.fetchone()
        if not row:
            return {"state": json.loads(json.dumps(DEFAULT_STATE)), "history": [], "greeted": False, "last_country": None}
        return {"state": json.loads(row[0]), "history": json.loads(row[1]), "greeted": bool(row[2]), "last_country": row[3]}

def save_dialog(chat_id: int, state, history, greeted: bool, last_country):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
        INSERT INTO dialogs(chat_id, state_json, history_json, greeted, last_country)
        VALUES(?,?,?,?,?)
        ON CONFLICT(chat_id) DO UPDATE SET
          state_json=excluded.state_json,
          history_json=excluded.history_json,
          greeted=excluded.greeted,
          last_country=excluded.last_country
        """, (json.dumps(state, ensure_ascii=False), json.dumps(history, ensure_ascii=False), int(greeted), last_country, chat_id))
        conn.commit()

# ==== Логіка замовлення ====
ORDER_FIELDS = ["name", "phone", "np", "items"]
ORDER_KEYWORDS = ["замов", "купит", "придбат", "оформ", "відправ", "оплат", "order", "buy", "purchase"]

def ensure_order(d: Dict[str, Any]) -> Dict[str, Any]:
    return d.setdefault("order", {"name": None, "phone": None, "np": None, "items": []})

def add_or_update_item(order: Dict[str, Any], country_key: str, qty: int) -> None:
    for it in order["items"]:
        if it["country"] == country_key:
            it["qty"] = qty
            return
    order["items"].append({"country": country_key, "qty": qty})

def unit_price(country_key: str, qty: int) -> Optional[int]:
    if country_key == "gb" and qty >= 1000:
        return None
    tiers = PRICE_TABLE.get(country_key, [])
    for upper, price in tiers:
        if qty <= upper:
            return price
    return tiers[-1][1] if tiers else None

def format_order_summary(order: Dict[str, Any]) -> Tuple[str, str]:
    lines: List[str] = []
    if order.get("name"):  lines.append(order["name"])
    if order.get("phone"): lines.append(order["phone"])
    if order.get("np"):    lines.append(order["np"])
    if lines: lines.append("")
    total = 0
    contract = False
    for it in order.get("items", []):
        ck, qty = it["country"], it["qty"]
        meta = COUNTRIES[ck]
        up = unit_price(ck, qty)
        if ck == "gb" and qty >= 1000:
            contract = True
            lines.append(f"{meta['flag']} {meta['name']}, {qty} шт — договірна")
        else:
            lines.append(f"{meta['flag']} {meta['name']}, {qty} шт — {up} грн")
            total += (up or 0) * qty
        if ck == "us":
            lines.append(US_NOTE)
    if total:
        lines += ["", f"Разом до сплати: {total} грн"]
    if contract:
        lines.append("Для 1000+ шт. по 🇬🇧 — договірна ціна, уточнимо особисто.")
    return "\n".join(lines).strip(), json.dumps(order, ensure_ascii=False)

def all_required_filled(order: Dict[str, Any]) -> bool:
    return bool(order.get("name") and order.get("phone") and order.get("np") and order.get("items"))

def missing_fields(order: Dict[str, Any]) -> List[str]:
    return [f for f in ORDER_FIELDS if not order.get(f)]

def user_intends_order(text: str, items_detected: List[Tuple[str, int]]) -> bool:
    t = normalize(text)
    return bool(items_detected) or any(k in t for k in ORDER_KEYWORDS)

def parse_contact_block(text: str) -> Dict[str, Optional[str]]:
    name = None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if re.match(r"^[A-Za-zА-Яа-яІіЇїЄєҐґ'`\\-]+\\s+[A-Za-zА-Яа-яІіЇїЄєҐґ'`\\-]+$", l):
            name = l; break
    m = re.search(r"(\\+?\\d[\\d\\s()\\-]{7,}\\d)", text)
    phone = m.group(1).strip() if m else None
    np = None
    for l in lines:
        if any(k in l.lower() for k in ["нова пошта","нової пошти","відділення","поштомат","№","no","#"]):
            np = l; break
    return {"name": name, "phone": phone, "np": np}

# ==== Команди ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_init()
    chat_id = update.effective_chat.id
    dlg = load_dialog(chat_id)
    dlg["greeted"] = True
    save_dialog(chat_id, dlg["state"], dlg["history"], True, dlg.get("last_country"))
    await update.message.reply_text("Привіт! Я допоможу відповісти на запитання та оформити замовлення. Чим можу бути корисним?")

# ==== Обробка ====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    db_init()
    chat_id = update.effective_chat.id
    dlg = load_dialog(chat_id)
    state, history = dlg["state"], dlg["history"]
    greeted, last_country = dlg["greeted"], dlg.get("last_country")
    order = ensure_order(state)

    user_text = update.message.text.strip()
    history.append({"role": "user", "content": user_text})
    history = history[-8:]

    # 1) FAQ
    t = normalize(user_text)
    faq_key = ("активація" if any(k in t for k in ["актив","встав","підключ","подключ","активац"])
               else "месенджери" if any(k in t for k in ["ватсап","whatsapp","телеграм","viber","месендж","sms","смс"])
               else "поповнення" if any(k in t for k in ["поповн","оплат","ding","paypal"])
               else "активність" if any(k in t for k in ["активн","скільки","сколько","півроку","полгода","6 міс"])
               else "тарифи" if any(k in t for k in ["тариф","план","operator","оператор"])
               else None)
    if faq_key:
        title, answer = FAQ[faq_key]
        await update.message.reply_text(f"{title}\n\n{answer}")
        history.append({"role": "assistant", "content": f"{title}\n\n{answer}"})
        save_dialog(chat_id, state, history[-8:], greeted, last_country)
        return

    # 2) Контакти (якщо прийшли блоком)
    guessed = parse_contact_block(user_text)
    for k in ["name","phone","np"]:
        if guessed.get(k) and not order.get(k):
            order[k] = guessed[k]

    # 3) Позиції та «тільки кількість»
    items = parse_items_line(user_text)
    for ck, qty in items:
        add_or_update_item(order, ck, qty)
        last_country = ck
    qty_only = detect_quantity_only(user_text)
    if qty_only and last_country:
        add_or_update_item(order, last_country, qty_only)

    # 4) Завершення
    if all_required_filled(order):
        summary, _ = format_order_summary(order)
        await update.message.reply_text(summary)
        await update.message.reply_text("Дякуємо за замовлення! Воно буде відправлене протягом 24 годин. 😊")
        history.append({"role": "assistant", "content": summary})
        history.append({"role": "assistant", "content": "Дякуємо за замовлення! Воно буде відправлене протягом 24 годин. 😊"})
        state["order"] = {"name": None, "phone": None, "np": None, "items": []}
        last_country = None
        save_dialog(chat_id, state, history[-8:], greeted, last_country)
        return

    # 5) Намір оформити — попросимо ВСІ відсутні поля ОДНИМ повідомленням
    if user_intends_order(user_text, items) or qty_only:
        miss = missing_fields(order)
        if miss:
            ask = ["Щоб оформити замовлення, будь ласка, надішліть:"]
            if "name" in miss:  ask.append("• Ім'я та прізвище")
            if "phone" in miss: ask.append("• Номер телефону у форматі +380…")
            if "np" in miss:    ask.append("• Місто і № відділення/поштомату Нової Пошти")
            if "items" in miss and not items:
                if last_country and not any(it["qty"] for it in order["items"] if it["country"] == last_country):
                    meta = COUNTRIES.get(last_country, {})
                    ask = [f"Скільки штук потрібно для {meta.get('flag','')} {meta.get('name','')}?"]
                else:
                    ask.append("• Країну(и) та кількість SIM-карт (можна так: 🇬🇧 1; 🇵🇱 2)")
            msg = "\n".join(ask)
            await update.message.reply_text(msg)
            history.append({"role": "assistant", "content": msg})
            save_dialog(chat_id, state, history[-8:], greeted, last_country)
            return

    # 6) GPT-fallback (без повторних вітань і прайсу)
    try:
        sys_prompt = f"""
Ти — дружелюбний Telegram-помічник магазину SIM-карт. Відповідай українською стисло.
Не вітайся повторно (вітання лише на /start).
Не надсилай повний прайс без прямого запиту («прайс», «ціни», «price»).
Якщо країну названо без кількості — запитай лише кількість, без прайсу.
Особисті дані (ПІБ/телефон/НП) проси тільки якщо намір оформити замовлення очевидний.

Довідка ДЛЯ ТЕБЕ (не вставляй у відповідь без запиту):
{PRICE_LIST_TEXT}
{US_NOTE}
FAQ: активація/месенджери/поповнення/активність/тарифи.
"""
        messages = [{"role": "system", "content": sys_prompt}] + history + [{"role": "user", "content": user_text}]
        resp = openai.ChatCompletion.create(model="gpt-4o", messages=messages[-10:], max_tokens=350, temperature=0.3)
        ai_text = resp.choices[0].message["content"].strip()
        if ai_text:
            cleaned = re.sub(r"^(привіт|вітаю)[^\n]*\n+", "", ai_text, flags=re.I)
            await update.message.reply_text(cleaned)
            history.append({"role": "assistant", "content": cleaned})
            save_dialog(chat_id, state, history[-8:], greeted, last_country)
    except Exception as e:
        logger.warning(f"GPT fallback error: {e}")

# ==== Запуск ====
def main():
    if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN не встановлено")
    if not WEBHOOK_URL:   raise RuntimeError("WEBHOOK_URL не встановлено")
    db_init()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path="", webhook_url=WEBHOOK_URL)

if __name__ == "__main__":
    main()
