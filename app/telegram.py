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
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram API error"))
        return data["result"]

async def send_message(chat_id: int | str, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await bot_api("sendMessage", payload)
