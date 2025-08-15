# logic.py
import json

def load_products():
    return json.load(open("products.json","r",encoding="utf-8"))

def find_product(products, pid):
    return next((p for p in products if p["id"] == pid), None)

def unit_price_by_tiers(qty, tiers, fallback=None):
    # вибирає найвищий min_qty, що <= qty
    best = None
    for t in (tiers or []):
        if qty >= int(t["min_qty"]) and (best is None or int(t["min_qty"]) > int(best["min_qty"])):
            best = t
    if best and best.get("price") is not None:
        return int(best["price"])
    if fallback is not None:
        return int(fallback)
    # якщо ціна договірна/немає — кидай виняток, щоб повідомити користувача
    raise ValueError("NEGOTIABLE_OR_NO_PRICE")

def format_reply_plain(items_calced, grand_total, negotiable=False):
    lines = [
        f"{it['name']} — {it['qty']} шт × {it['unit_price']} грн = {it['line_total']} грн"
        for it in items_calced
    ]
    if items_calced:
        lines.append(f"\nРазом: {grand_total} грн")
    if negotiable:
        lines.append("\nДля деяких позицій ціна договірна. Написати менеджеру?")
    lines.append("Оформити замовлення?")
    return "\n".join(lines)
