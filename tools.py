import re
import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Tuple
from config import PRICE_TIERS, FLAGS, DISPLAY, DIAL_CODES, USSD_DATA, POST_ORDER_USSD, get_availability, CRYPTO_WALLET, CRYPTO_UAH_RATE, CRYPTO_FEE_USD

logger = logging.getLogger(__name__)

# ==== Класи даних ====
@dataclass
class OrderItem:
    country: str
    qty: int
    operator: Optional[str] = None

@dataclass
class OrderData:
    full_name: str
    phone: str
    city: str
    np: str
    items: List[OrderItem]
    address: Optional[str] = None
    edited: bool = False

# ==== Регулярні вирази ====
def _extract_json_block(text: str) -> Optional[str]:
    """Знаходить перший збалансований {...} блок у тексті."""
    if not text:
        return None
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return None
QTY_ONLY_RE = re.compile(r"(?:\bпо\b\s*)?(\d{1,4})\s*(шт|штук|шт\.?|сим(?:-?карт[аи])?|sim-?card|sim|pieces?)\b", re.IGNORECASE)
NUM_POS_RE = re.compile(r"\d{1,4}")
PO_QTY_RE = re.compile(r"\bпо\s*(\d{1,4})\b", re.IGNORECASE)
PAID_HINT_RE = re.compile(r"\b(без\s*нал|безнал|оплачено|передоплат|оплата\s*на\s*карт[уі])\b", re.IGNORECASE)
NOTE_REPLY_RE = re.compile(r'^\s*примітка[:\s]*(.+)', re.IGNORECASE | re.DOTALL)
PRICE_LINE_RE = re.compile(r"— (\d+ грн|договірна)")
TOTAL_LINE_RE = re.compile(r"^Загальна сума: \d+ грн")
ACK_PATTERNS = [
    r"^\s*(ок(ей)?|добре|чудово|гарно|дякую!?|спасибі|спасибо|жду|чекаю|ок,?\s*жду|ок,?\s*чекаю|ого|ух\s*ты|супер|зрозуміло|ясно|прийнято|клас|круто|ладно|хорошо|понятно|понял|зрозумів|got\s*it|ok|okay|thanks|thx)\s*[\.\!\,]*\s*$",
    r"^\s*[👍🙏✅👌🔥💪👏😊🤝]+\s*$",
    r"^\s*\+\s*$",
]

_ACK_COMPILED = [re.compile(p, re.IGNORECASE) for p in ACK_PATTERNS]

def is_ack_message(text: str) -> bool:
    """Перевіряє, чи повідомлення є простим підтвердженням (ок, дякую, +, 👍 тощо)."""
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    return any(r.match(t) for r in _ACK_COMPILED)

FALLBACK_PLASTIC_MSG = "Номер вказаний на пластику сім-карти"

COUNTRY_KEYWORDS = {
    "ВЕЛИКОБРИТАНІЯ": ["англ", "британ", "великобритан", "uk", "u.k", "great britain", "+44"],
    "ФРАНЦІЯ": ["франц", "france", "+33"],
    "ІСПАНІЯ": ["іспан", "испан", "spain", "+34"],
    "НІМЕЧЧИНА": ["німеч", "герман", "german", "+49", "deutsch"],
    "НІДЕРЛАНДИ": ["нідерлан", "голланд", "holland", "nether", "+31"],
    "ІТАЛІЯ": ["італ", "итал", "ital", "+39"],
    "ЧЕХІЯ": ["чех", "czech", "+420"],
    "ПОЛЬЩА": ["польщ", "польш", "poland"],
    "ЛИТВА": ["литв", "lithuan"],
    "ЛАТВІЯ": ["латв", "latvia"],
    "КАЗАХСТАН": ["казах", "kazakh", "+7"],
    "МАРОККО": ["марок", "morocc"],
    "ЕСТОНІЯ": ["естон", "эстон", "eston", "+372"],
}

# ==== Допоміжні функції нормалізації ====
def normalize_country(name: str) -> str:
    n = (name or "").strip().upper()
    # Пряме співпадіння з ключами PRICE_TIERS / DISPLAY
    if n in PRICE_TIERS or n in DISPLAY:
        return n
    # Явні маппінги
    ALIASES = {
        "ВЕЛИКОБРИТАНІЯ": ["АНГЛІЯ", "БРИТАНІЯ", "UK", "U.K.", "UNITED KINGDOM", "ВБ", "GREAT BRITAIN", "+44", "ЮК", "У.К."],
        "США": ["USA", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA", "ШТАТИ", "АМЕРИКА", "US", "U.S."],
        "ІТАЛІЯ": ["ITALY", "ИТАЛИЯ", "ITALIA", "+39"],
        "МОЛДОВА": ["MOLDOVA", "+373"],
        "НІДЕРЛАНДИ": ["ГОЛЛАНДІЯ", "HOLLAND", "NETHERLANDS", "+31"],
        "НІМЕЧЧИНА": ["ГЕРМАНІЯ", "GERMANY", "DEUTSCHLAND", "+49"],
        "ФРАНЦІЯ": ["FRANCE", "+33"],
        "ІСПАНІЯ": ["ИСПАНІЯ", "SPAIN", "+34"],
        "ЧЕХІЯ": ["CZECH", "CZECH REPUBLIC", "CZECHIA", "+420"],
        "ПОЛЬЩА": ["POLAND", "ПОЛЬША"],
        "ЛИТВА": ["LITHUANIA"],
        "ЛАТВІЯ": ["LATVIA"],
        "КАЗАХСТАН": ["KAZAKHSTAN", "+7"],
        "МАРОККО": ["MOROCCO"],
        "ЕСТОНІЯ": ["ESTONIA", "ЭСТОНИЯ", "+372"],
    }
    for canonical, aliases in ALIASES.items():
        if n in aliases:
            return canonical
    # Підстрочний пошук за COUNTRY_KEYWORDS
    n_low = n.lower()
    for key, subs in COUNTRY_KEYWORDS.items():
        for s in subs:
            if s in n_low:
                return key
    return n

def canonical_operator(op: Optional[str]) -> Optional[str]:
    if not op: return None
    o = op.strip().lower()
    if o in ("o2", "о2"): return "O2"
    if o in ("vodafone", "водафон", "водофон"): return "Vodafone"
    if o in ("three", "трі", "3"): return "Three"
    return None

def canonical_operator_any(op: Optional[str]) -> Optional[str]:
    if not op: return None
    o = op.strip().lower()
    mapping = {
        "O2": ["o2","о2"], "Lebara": ["lebara","лебара"], "Vodafone": ["vodafone","водафон","водофон"],
        "Movistar": ["movistar","мовістар","мовистар"], "Lycamobile": ["lycamobile","lyca","lyka","лайкамобайл","лайка"],
        "T-mobile": ["t-mobile","t mobile","т-мобайл","т мобайл","tmobile","tмобайл"], "Kaktus": ["kaktus","кактус"],
    }
    for canon, alts in mapping.items():
        if o in alts: return canon
    return None

def unit_price(country_norm: str, qty: int) -> Optional[int]:
    tiers = PRICE_TIERS.get(country_norm)
    if not tiers: return None
    for min_q, price in tiers:
        if qty >= min_q: return price
    return None

def _cap_word(w: str) -> str:
    return w[:1].upper() + w[1:].lower() if w else w

def _smart_title(s: str) -> str:
    parts = re.split(r"\s+", (s or "").strip())
    return " ".join("-".join(_cap_word(x) for x in p.split("-")) for p in parts)

def format_full_name(name: str) -> str:
    tokens = [t for t in (name or "").strip().split() if t]
    if len(tokens) >= 2: return f"{_smart_title(tokens[0])} {_smart_title(tokens[-1])}"
    return _smart_title(tokens[0]) if tokens else ""

def format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("380"): digits = "0" + digits[3:]
    if len(digits) == 10: return f"{digits[0:3]} {digits[3:6]} {digits[6:10]}"
    return (phone or "").strip()

def format_city(city: str) -> str:
    return (city or "").strip()

def format_np(np_str: str) -> str:
    s = (np_str or "").strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else re.sub(r"[^\d]", "", s) or s

# ==== Рендеринг тексту ====
def render_out_of_stock(unavailable_items: Dict[str, Optional[str]]) -> str:
    lines = []
    for country_key, reason in unavailable_items.items():
        disp_name = DISPLAY.get(country_key, country_key.title())
        lines.append(f"❌ {disp_name}: {reason}" if reason else f"❌ {disp_name}: Наразі немає в наявності.")
    if len(lines) == 1: return f"На жаль, {lines[0][2:]}"
    return "На жаль, ці позиції наразі недоступні:\n" + "\n".join(lines)

def render_price_block(country_key: str) -> str:
    flag = FLAGS.get(country_key, "")
    header_name = DISPLAY.get(country_key, country_key.title())
    header = f"{flag} {header_name} {flag}\n\n"
    status, reason = get_availability(country_key)
    if status == "-": return header + f"❌ {reason or 'Наразі немає в наявності.'}\n\n"
    
    tiers = sorted(PRICE_TIERS.get(country_key, []), key=lambda x: x[0])
    if not tiers: return header + "Немає даних.\n\n"
    
    lines, inserted_gap = [], False
    for idx, (min_q, price) in enumerate(tiers):
        next_min = tiers[idx + 1][0] if idx + 1 < len(tiers) else None
        max_q = (next_min - 1) if next_min else None
        if not inserted_gap and min_q >= 100 and idx > 0:
            lines.append("")
            inserted_gap = True
        qty_part = f"{min_q}+ шт." if max_q is None else (f"{min_q} шт." if min_q == max_q else f"{min_q}-{max_q} шт.")
        lines.append(f"{qty_part} — {'договірна' if price is None else str(price) + ' грн'}")
    return header + "\n".join(lines) + "\n\n"

def render_prices(countries: List[str]) -> str:
    blocks = []
    for c in countries:
        key = normalize_country(c).upper()
        if key in PRICE_TIERS: blocks.append(render_price_block(key))
    return "".join(blocks)

def available_list_text() -> str:
    names = [DISPLAY[k] for k in PRICE_TIERS.keys() if get_availability(k)[0] == "+"]
    if not names: return "наразі нічого немає"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " та " + names[-1]

def render_unavailable(unavail: List[str]) -> str:
    names = [str(x).strip() for x in unavail if str(x).strip()]
    if not names: return ""
    list_text = f"{names[0]}" if len(names) == 1 else ", ".join(names)
    return f"На жаль, {list_text} наразі недоступні.\nУ наявності: {available_list_text()}."

ORDER_LINE = "{flag} {disp}, {qty} шт — {line_total} грн    \n"

def render_order(order: OrderData) -> str:
    lines, grand_total, counted_countries = [], 0, 0
    for it in order.items:
        c_norm = normalize_country(it.country)
        disp = DISPLAY.get(c_norm, it.country.strip().title())
        # Більше не додаємо (оператор ...), навіть якщо він є в даних
        
        flag = FLAGS.get(c_norm, "")
        price = unit_price(c_norm, it.qty)
        if price is None: line_total_str = "договірна"
        else:
            line_total = price * it.qty
            grand_total += line_total
            counted_countries += 1
            line_total_str = str(line_total)
        lines.append(ORDER_LINE.format(flag=flag, disp=disp, qty=it.qty, line_total=line_total_str))
        
    np_display = "0" if order.address else format_np(order.np)
    header = f"{format_full_name(order.full_name)} \n{format_phone(order.phone)}\n{format_city(order.city)} № {np_display}  \n"
    if order.address: header += f"⚠️ Адресна доставка: {order.address}\n"
    header += "\n"
    footer = f"\nЗагальна сума: {grand_total} грн\n" if counted_countries >= 2 else ""
    return header + "".join(lines) + footer

def render_order_for_group(order: OrderData, paid: bool) -> str:
    lines, grand_total, counted = [], 0, 0
    for it in order.items:
        c_norm = normalize_country(it.country)
        disp = DISPLAY.get(c_norm, it.country.strip().title())
        # Більше не додаємо (оператор ...)
        
        flag = FLAGS.get(c_norm, "")
        if paid:
            lines.append(f"{flag} {disp}, {it.qty} шт — (замовлення оплачене)  \n")
        else:
            price = unit_price(c_norm, it.qty)
            if price is None: lines.append(f"{flag} {disp}, {it.qty} шт — договірна  \n")
            else:
                line_total = price * it.qty
                grand_total += line_total
                counted += 1
                lines.append(f"{flag} {disp}, {it.qty} шт — {line_total} грн  \n")
                
    np_display = "0" if order.address else format_np(order.np)
    header = f"{format_full_name(order.full_name)} \n{format_phone(order.phone)}\n{format_city(order.city)} № {np_display}  \n"
    if order.address: header += f"⚠️ Адресна доставка: {order.address}\n"
    header += "\n"
    footer = f"\n\nЗагальна сума: {grand_total} грн\n" if not paid and counted >= 2 else ""
    return header + "".join(lines).strip() + footer

def render_ussd_targets(targets: List[Dict[str, str]]) -> str:
    result_lines = []
    for t in targets:
        country = normalize_country(t.get("country", "")).upper()
        if not country: continue
        op_req = canonical_operator_any(t.get("operator"))
        code_prefix = DIAL_CODES.get(country, "")
        flag = FLAGS.get(country, "")
        disp = DISPLAY.get(country, country.title())
        pairs = USSD_DATA.get(country, [])
        if op_req and pairs: pairs = [p for p in pairs if (p[0] and canonical_operator_any(p[0]) == op_req)]
        
        if not pairs:
            base = f"{code_prefix} {flag} {disp}"
            result_lines.append(f"{base} (оператор {op_req}) — {FALLBACK_PLASTIC_MSG}" if op_req else f"{base} — {FALLBACK_PLASTIC_MSG}")
            result_lines.append("")
            continue
        for op, code in pairs:
            base = f"{code_prefix} {flag} {disp}"
            result_lines.append(f"{base} (оператор {op}) — {code}" if op else f"{base} — {code}")
        result_lines.append("")
    while result_lines and result_lines[-1] == "": result_lines.pop()
    return "\n".join(result_lines).strip()

def render_post_order_info(order: OrderData) -> Optional[str]:
    """Формує повідомлення з USSD-кодами для країн у замовленні"""
    # Збираємо унікальні країни з замовлення
    unique_countries = {normalize_country(item.country).upper() for item in order.items}
    lines = []
    
    for c_key in unique_countries:
        code = POST_ORDER_USSD.get(c_key)
        if code:
            flag = FLAGS.get(c_key, "")
            lines.append(f"{flag} {code} — комбінація щоб дізнатись номер")
    
    if not lines:
        return None
        
    return "\n".join(lines)

def calc_order_total(order: OrderData) -> int:
    """Підраховує загальну суму замовлення в грн."""
    total = 0
    for it in order.items:
        c_norm = normalize_country(it.country).upper()
        price = unit_price(c_norm, it.qty)
        if price is not None:
            total += price * it.qty
    return total

def order_signature(order: OrderData) -> str:
    items_sig = ";".join(f"{normalize_country(it.country)}:{it.qty}:{canonical_operator(it.operator) or ''}" for it in order.items)
    return f"{format_full_name(order.full_name)}|{format_phone(order.phone)}|{format_city(order.city)}|{format_np(order.np)}|{order.address or ''}|{items_sig}"

# ==== Крипто-оплата ====
def try_parse_crypto_json(text: str) -> bool:
    """Перевіряє, чи GPT повернув JSON з запитом крипто-оплати."""
    json_str = _extract_json_block(text or "")
    if not json_str: return False
    try:
        data = json.loads(json_str)
        return data.get("crypto_payment") is True
    except Exception: return False

def calc_crypto_amount(total_uah: int) -> int:
    """Розраховує суму в USDT: (сума_грн / курс) + комісія, округлення вгору."""
    import math
    return math.ceil(total_uah / CRYPTO_UAH_RATE) + CRYPTO_FEE_USD

def render_crypto_payment(total_uah: int) -> str:
    """Формує повідомлення з реквізитами для крипто-оплати."""
    usdt_amount = calc_crypto_amount(total_uah)
    return (
        f"💰 Оплата USDT (TRC-20):\n\n"
        f"Сума: {usdt_amount} USDT\n\n"
        f"Адреса гаманця:\n"
        f"`{CRYPTO_WALLET}`\n\n"
        f"Після оплати надішліть, будь ласка, скріншот підтвердження."
    )

def items_signature(order: OrderData) -> str:
    """Сигнатура лише товарів (країна+кількість), без персональних даних."""
    parts = sorted(f"{normalize_country(it.country).upper()}:{it.qty}" for it in order.items)
    return ";".join(parts)

def items_signature_from_sig(full_sig: str) -> str:
    """Витягує сигнатуру товарів з повної order_signature."""
    # Формат: name|phone|city|np|address|item1;item2;...
    parts = full_sig.rsplit("|", 1)
    if len(parts) < 2:
        return ""
    items_part = parts[-1]  # "ВЕЛИКОБРИТАНІЯ:3:Vodafone;НІМЕЧЧИНА:2:"
    # Нормалізуємо: прибираємо оператор, лишаємо країна:кількість
    normalized = []
    for item_str in items_part.split(";"):
        if not item_str.strip():
            continue
        item_parts = item_str.split(":")
        if len(item_parts) >= 2:
            normalized.append(f"{item_parts[0]}:{item_parts[1]}")
    return ";".join(sorted(normalized))

# ==== Парсинг JSON в об'єкти ====
def try_parse_order_json(text: str) -> Optional[OrderData]:
    json_str = _extract_json_block(text or "")
    if not json_str: return None
    try:
        data = json.loads(json_str)
        items = [OrderItem(country=i["country"], qty=int(i["qty"]), operator=i.get("operator")) for i in data.get("items", [])]
        return OrderData(
            full_name=data.get("full_name", "").strip(),
            phone=data.get("phone", "").strip(),
            city=data.get("city", "").strip(),
            np=str(data.get("np", "")).strip(),
            items=items,
            address=(data.get("address") or "").strip() or None,
            edited=bool(data.get("edited", False))
        )
    except Exception as e:
        logger.warning(f"JSON parse error: {e}")
        return None

def try_parse_price_json(text: str) -> Optional[List[str]]:
    json_str = _extract_json_block(text or "")
    if not json_str: return None
    try:
        data = json.loads(json_str)
        return data["countries"] if data.get("ask_prices") is True and isinstance(data.get("countries"), list) else None
    except Exception: return None

def try_parse_ussd_json(text: str) -> Optional[List[Dict[str, str]]]:
    json_str = _extract_json_block(text or "")
    if not json_str: return None
    try:
        data = json.loads(json_str)
        if data.get("ask_ussd") is True and isinstance(data.get("targets"), list):
            return [t for t in data["targets"] if isinstance(t, dict) and t.get("country")] or None
        return None
    except Exception: return None

def try_parse_manager_order_json(json_text: str) -> Optional[OrderData]:
    return try_parse_order_json(json_text)

# ==== Евристики аналізу тексту ====
def detect_qty_only(text: str) -> Optional[int]:
    if not text: return None
    m = QTY_ONLY_RE.search(text)
    try: return int(m.group(1)) if m else None
    except: return None

def _country_mentions_with_pos(text: str) -> List[Tuple[str, int]]:
    low = (text or "").lower()
    found = []
    for key, subs in COUNTRY_KEYWORDS.items():
        best = None
        for s in subs:
            i = low.find(s)
            if i != -1: best = i if best is None else min(best, i)
        if best is not None: found.append((key, best))
    return sorted(found, key=lambda x: x[1])

def detect_point4_items(text: str) -> List[Tuple[str, int]]:
    if not text: return []
    mentions = _country_mentions_with_pos(text)
    if not mentions: return []
    nums = [(m.group(0), m.start()) for m in NUM_POS_RE.finditer(text)]
    items, used_num_idx, used_country_idx = [], set(), set()
    for ci, (country, cpos) in enumerate(mentions):
        best_idx, best_dist = None, 9999
        for ni, (nstr, npos) in enumerate(nums):
            if ni in used_num_idx: continue
            dist = abs(npos - cpos)
            if dist <= 20 and dist < best_dist:
                best_dist = dist
                best_idx = ni
        if best_idx is not None:
            items.append((country, int(nums[best_idx][0])))
            used_num_idx.add(best_idx)
            used_country_idx.add(ci)
    if len(items) == 0 or len(items) < len(mentions):
        m = PO_QTY_RE.search(text)
        if m:
            q = int(m.group(1))
            for ci, (country, _) in enumerate(mentions):
                if ci not in used_country_idx: items.append((country, q))
    return items

def missing_points_from_reply(text: str) -> Set[int]:
    out = set()
    if "Залишилось вказати" not in (text or ""): return out
    for ln in (text or "").splitlines():
        m = re.match(r"\s*([1-4])\.\s", ln)
        if m: out.add(int(m.group(1)))
    return out

def extract_quoted_text(message) -> Optional[str]:
    if not message or not message.reply_to_message: return None
    rt = message.reply_to_message
    return (rt.text or rt.caption or "").strip() or None

def is_meaningful_followup(text: str) -> bool:
    t = (text or "").strip()
    if not t: return False
    low = t.lower()
    if any(w in low for w in ["ціни", "прайс", "надіслано", "див. вище", "вище", "повторю"]): return False
    if "грн" in low or re.search(r"\bшт\.?\b", low): return False
    for p in [r"^підтверджую( наявність)?\.?$", r"^є в наявності\.?$", r"^в наявності\.?$", r"^available\.?$", r"^так, є\.?$", r"^так\.?$"]:
        if re.match(p, low): return False
    return len(t) >= 4
