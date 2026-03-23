import aiohttp
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import re
import config

logger = logging.getLogger(__name__)

NP_API_URL = "https://api.novaposhta.ua/v2.0/json/"


def normalize_phone_for_np(phone: str) -> str:
    """Нормалізує телефон до формату 380XXXXXXXXX для Нової Пошти."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    elif len(digits) == 9:
        digits = "380" + digits
    return digits


def _phone_matches(doc_phone: str, search_phone: str) -> bool:
    """Порівнює два телефони ігноруючи формат."""
    a = re.sub(r"\D", "", doc_phone or "")
    b = re.sub(r"\D", "", search_phone or "")
    # Нормалізуємо обидва до 10-значного формату
    if len(a) == 12 and a.startswith("380"): a = "0" + a[3:]
    if len(b) == 12 and b.startswith("380"): b = "0" + b[3:]
    return a == b and len(a) == 10


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


async def find_ttns_by_phone(phone: str, days: int = 10) -> List[Dict]:
    """
    Шукає ТТН за номером телефону отримувача за останні N днів.
    Стратегія: 
      1) Спроба пошуку по RecipientsPhone
      2) Якщо пусто — завантажуємо всі ЕН за період і фільтруємо локально
    """
    api_key = config.NOVA_POSHTA_API_KEY
    if not api_key:
        logger.error("NOVA_POSHTA_API_KEY not set")
        return []

    phone_normalized = normalize_phone_for_np(phone)
    if len(phone_normalized) != 12 or not phone_normalized.startswith("380"):
        logger.warning(f"Invalid phone for NP: {phone} -> {phone_normalized}")
        return []

    date_to = datetime.now()
    date_from = date_to - timedelta(days=days)
    date_from_str = date_from.strftime("%d.%m.%Y")
    date_to_str = date_to.strftime("%d.%m.%Y")

    # --- Спроба 1: пошук по RecipientsPhone ---
    payload1 = {
        "apiKey": api_key,
        "modelName": "InternetDocument",
        "calledMethod": "getDocumentList",
        "methodProperties": {
            "DateTimeFrom": date_from_str,
            "DateTimeTo": date_to_str,
            "RecipientsPhone": phone_normalized,
            "Page": "1",
            "Limit": "20",
        }
    }

    data1 = await _np_request(payload1)
    logger.info(f"NP search by phone {phone_normalized}: success={data1.get('success') if data1 else 'N/A'}, "
                f"count={len(data1.get('data', [])) if data1 else 0}, "
                f"errors={data1.get('errors', []) if data1 else 'request_failed'}")

    results = _extract_results(data1)
    if results:
        return results

    # --- Спроба 2: завантажуємо всі ЕН за період, фільтруємо локально ---
    logger.info(f"NP phone search empty, trying full list + local filter")

    all_results = []
    for page in range(1, 6):  # максимум 5 сторінок по 100
        payload2 = {
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
        data2 = await _np_request(payload2)
        if not data2 or not data2.get("success"):
            break

        docs = data2.get("data", [])
        if not docs:
            break

        for doc in docs:
            rp = doc.get("RecipientsPhone", "") or doc.get("RecipientContactPhone", "")
            if _phone_matches(rp, phone_normalized):
                ttn = doc.get("IntDocNumber", "")
                if ttn:
                    all_results.append({
                        "ttn": ttn,
                        "status": doc.get("StateName", ""),
                        "city_recipient": doc.get("CityRecipient", ""),
                        "date_created": doc.get("DateTime", ""),
                        "recipient": doc.get("RecipientFullName", ""),
                    })

        # Якщо документів менше 100 — це остання сторінка
        if len(docs) < 100:
            break

    logger.info(f"NP local filter found {len(all_results)} docs for phone {phone_normalized}")
    return all_results


def _extract_results(data: Optional[dict]) -> List[Dict]:
    """Витягує результати з відповіді API."""
    if not data or not data.get("success"):
        return []

    results = []
    for doc in data.get("data", []):
        ttn = doc.get("IntDocNumber", "")
        if not ttn:
            continue
        results.append({
            "ttn": ttn,
            "status": doc.get("StateName", ""),
            "city_recipient": doc.get("CityRecipient", ""),
            "date_created": doc.get("DateTime", ""),
            "recipient": doc.get("RecipientFullName", ""),
        })
    return results


def render_ttn_results(results: List[Dict]) -> str:
    """Форматує результати пошуку ТТН для відправки клієнту."""
    if not results:
        return "На жаль, за вашим номером телефону не знайдено відправлень за останні 10 днів. Якщо ви оформляли замовлення нещодавно — можливо, воно ще не було відправлене. Очікуйте відповіді менеджера."

    lines = ["📦 Знайдені відправлення:\n"]
    for r in results:
        lines.append(f"ТТН: {r['ttn']}")
        if r.get("status"):
            lines.append(f"Статус: {r['status']}")
        if r.get("date_created"):
            lines.append(f"Дата: {r['date_created']}")
        lines.append("")

    return "\n".join(lines).strip()
