import json
from .config import settings
from .db import SessionLocal, AdminRole, AuditLog

PERMISSIONS = {
    "users": "إدارة المشتركين",
    "subscriptions": "إدارة الاشتراكات",
    "channel": "إدارة القناة",
    "radar": "إدارة الرصد",
    "payments": "إدارة المدفوعات",
    "admins": "إدارة المشرفين",
    "settings": "إعدادات النظام",
    "audit": "سجل العمليات",
}
ROLE_DEFAULTS = {
    "admin": list(PERMISSIONS.keys()),
    "moderator": ["users", "channel"],
    "radar_manager": ["radar"],
    "subscription_manager": ["users", "subscriptions", "payments"],
    "viewer": [],
}

def is_owner(telegram_id: int) -> bool:
    return int(telegram_id) == int(settings.owner_telegram_id)

async def get_admin(telegram_id: int):
    if is_owner(telegram_id):
        return {"telegram_id": int(telegram_id), "role": "owner", "permissions": list(PERMISSIONS), "enabled": True}
    async with SessionLocal() as db:
        row = await db.get(AdminRole, int(telegram_id))
        if not row or not row.enabled:
            return None
        try:
            perms = json.loads(row.permissions or "[]")
        except Exception:
            perms = []
        return {"telegram_id": row.telegram_id, "role": row.role, "permissions": perms, "enabled": row.enabled}

async def has_permission(telegram_id: int, permission: str) -> bool:
    admin = await get_admin(telegram_id)
    return bool(admin and (is_owner(telegram_id) or permission in admin["permissions"]))

async def audit(actor: int, action: str, target: int | None = None, details: dict | str | None = None):
    payload = json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else (details or "")
    async with SessionLocal() as db:
        db.add(AuditLog(actor_telegram_id=int(actor), action=action, target_telegram_id=int(target) if target else None, details=payload))
        await db.commit()
