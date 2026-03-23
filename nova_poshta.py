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


async def find_ttns_by_phone(phone: str, days: int = 10) -> List[Dict]:
    """
    Шукає ТТН за номером телефону отримувача за останні N днів.
    Повертає список dict з ключами: ttn, status, city_recipient, date_created
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

    payload = {
        "apiKey": api_key,
        "modelName": "InternetDocument",
        "calledMethod": "getDocumentList",
        "methodProperties": {
            "DateTimeFrom": date_from.strftime("%d.%m.%Y"),
            "DateTimeTo": date_to.strftime("%d.%m.%Y"),
            "RecipientsPhone": phone_normalized,
            "Page": "1",
            "Limit": "10",
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(NP_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        if not data.get("success"):
            errors = data.get("errors", [])
            logger.warning(f"NP API error: {errors}")
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

    except Exception as e:
        logger.error(f"NP API request failed: {e}")
        return []


def render_ttn_results(results: List[Dict]) -> str:
    """Форматує результати пошуку ТТН для відправки клієнту."""
    if not results:
        return "На жаль, за вашим номером телефону не знайдено відправлень за останні 10 днів."

    lines = ["📦 Знайдені відправлення:\n"]
    for r in results:
        lines.append(f"ТТН: {r['ttn']}")
        if r.get("status"):
            lines.append(f"Статус: {r['status']}")
        if r.get("date_created"):
            lines.append(f"Дата: {r['date_created']}")
        lines.append("")

    return "\n".join(lines).strip()
