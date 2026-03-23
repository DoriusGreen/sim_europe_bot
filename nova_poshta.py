import aiohttp
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import re
import config

logger = logging.getLogger(__name__)

NP_API_URL = "https://api.novaposhta.ua/v2.0/json/"

def clean_city_name(city: str) -> str:
    """Очищає назву міста від префіксів (м., с., смт.) та областей в дужках."""
    if not city: return ""
    c = city.lower()
    # Видаляємо все що в дужках (області)
    c = re.sub(r'\(.*?\)', '', c)
    # Видаляємо префікси
    c = re.sub(r'\b(м\.|с\.|смт\.|місто|село|селище)\s*', '', c)
    # Видаляємо зайві символи (залишаємо букви, цифри, пробіли)
    c = re.sub(r'[^\w\sа-яієїґ]', '', c)
    return c.strip()

async def _np_request(payload: dict) -> Optional[dict]:
    """Робить запит до API НП і повертає відповідь."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(NP_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
        return data
    except Exception as e:
        logger.error(f"NP API request failed: {e}")
        return None

async def find_ttns_by_city_and_np(city: str, np_number: str, days: int = 10) -> List[Dict]:
    """
    Шукає ТТН за назвою міста та номером відділення (або адресою) отримувача за останні N днів.
    Завантажує загальний список ЕН за період і фільтрує локально.
    """
    api_key = config.NOVA_POSHTA_API_KEY
    if not api_key:
        logger.error("NOVA_POSHTA_API_KEY not set")
        return []

    search_city = clean_city_name(city)
    search_np = str(np_number).strip().lower()
    
    if not search_city or not search_np:
        return []

    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)
    date_from_str = date_from.strftime("%d.%m.%Y")
    date_to_str = date_to.strftime("%d.%m.%Y")

    logger.info(f"NP local search by city '{search_city}' and address/NP '{search_np}'")

    all_results = []
    for page in range(1, 6):  # максимум 5 сторінок по 100
        payload = {
            "apiKey": api_key,
            "modelName": "InternetDocument",
            "calledMethod": "getDocumentList",
            "methodProperties": {
                "DateTimeFrom": date_from_str,
                "DateTimeTo": date_to_str,
                "Page": str(page),
                "Limit": "100",
            }
        }
        data = await _np_request(payload)
        if not data or not data.get("success"):
            break

        docs = data.get("data", [])
        if not docs:
            break

        for doc in docs:
            doc_city = (doc.get("CityRecipientDescription", "")).lower()
            doc_address = (doc.get("RecipientAddressDescription", "")).lower()
            
            # Перевіряємо чи очищене місто є в назві міста НП
            if search_city in doc_city:
                # Перевіряємо чи номер відділення (або адреса) є в адресі НП
                if search_np in doc_address:
                    ttn = doc.get("IntDocNumber", "")
                    if ttn:
                        all_results.append({
                            "ttn": ttn,
                            "status": doc.get("StateName", ""),
                            "city_recipient": doc.get("CityRecipientDescription", ""),
                            "address": doc.get("RecipientAddressDescription", ""),
                            "date_created": doc.get("DateTime", ""),
                            "recipient": doc.get("RecipientFullName", ""),
                            "cost": doc.get("CostOnSite", 0)
                        })

        # Якщо документів менше 100 — це остання сторінка
        if len(docs) < 100:
            break

    logger.info(f"NP search found {len(all_results)} docs for city '{search_city}' and NP '{search_np}'")
    return all_results

def render_ttn_results(results: List[Dict]) -> str:
    """Форматує результати пошуку ТТН для відправки клієнту."""
    if not results:
        return "На жаль, за вашими даними не знайдено відправлень за останні 10 днів. Якщо ви оформляли замовлення нещодавно — можливо, воно ще не було створене в базі Нової Пошти. Очікуйте відповіді менеджера або сповіщення від пошти."

    lines = ["📦 Знайдені відправлення:\n"]
    for r in results:
        lines.append(f"ТТН: {r['ttn']}")
        if r.get("status"):
            lines.append(f"Статус: {r['status']}")
        if r.get("city_recipient") and r.get("address"):
            lines.append(f"Доставка: {r['city_recipient']}, {r['address']}")
        if r.get("date_created"):
            lines.append(f"Дата створення: {r['date_created']}")
        if r.get("cost") and float(r['cost']) > 0:
            lines.append(f"До сплати: {r['cost']} грн")
        lines.append("")

    return "\n".join(lines).strip()
