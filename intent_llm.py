# intent_llm.py
import json

def build_intent_messages(user_text: str, products: list[dict]) -> list[dict]:
    catalog_short = [{"id": p["id"], "name": p["name"], "tags": p.get("tags", [])} for p in products]
    system = {
        "role": "system",
        "content": (
            "Ти парсер намірів магазину SIM/eSIM. "
            "Отримаєш 'catalog' і 'message'. Поверни ВИКЛЮЧНО JSON:\n"
            "{\"items\":[{\"product_id\":\"<id або null>\",\"qty\":<int>,\"matched_tag\":\"<або null>\"}]}\n"
            "Використовуй ТІЛЬКИ id з catalog. Якщо не впевнений — не додавай у items. qty=1 за замовчуванням."
        )
    }
    user = {
        "role": "user",
        "content": json.dumps({"catalog": catalog_short, "message": user_text}, ensure_ascii=False)
    }
    return [system, user]
