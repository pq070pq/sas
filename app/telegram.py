import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
import httpx
from .config import settings

def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    if not init_data or not settings.telegram_bot_token:
        raise ValueError("Telegram WebApp authentication is not configured")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise ValueError("Missing Telegram WebApp hash")
    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > max_age:
        raise ValueError("Expired Telegram WebApp initData")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("Invalid Telegram WebApp signature")
    user = json.loads(pairs.get("user", "{}"))
    if not user.get("id"):
        raise ValueError("Telegram user missing")
    return user

async def bot_api(method: str, payload: dict):
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
        try:
            data = r.json()
        except Exception:
            data = {}
        if not r.is_success or not data.get("ok"):
            description = data.get("description") or f"Telegram API HTTP {r.status_code}"
            raise RuntimeError(description)
        return data["result"]

async def send_message(
    chat_id: int | str,
    text: str,
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_parameters"] = {
            "message_id": int(reply_to_message_id),
            "allow_sending_without_reply": True,
        }
    return await bot_api("sendMessage", payload)

async def edit_message(chat_id: int | str, message_id: int, text: str):
    return await bot_api(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
        },
    )
