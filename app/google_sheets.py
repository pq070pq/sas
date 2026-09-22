import json
import asyncio
from .config import settings

def _service():
    if not settings.google_sheets_id or not settings.google_service_account_json:
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        info = json.loads(settings.google_service_account_json)
        creds = Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    except Exception:
        return None

def _append_row(row):
    service = _service()
    if service is None:
        return
    service.spreadsheets().values().append(
        spreadsheetId=settings.google_sheets_id,
        range=f"{settings.google_sheets_range}!A:K",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

async def sync_payment(row):
    await asyncio.to_thread(_append_row, row)
