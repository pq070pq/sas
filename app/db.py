from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .config import settings
from .radar_expiry import expiry_at

engine = create_async_engine(
    settings.database_url,
    future=True,
    connect_args={"timeout": 10} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    trial_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subscription_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subscription_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    plan: Mapped[str | None] = mapped_column(String(32))
    free_access: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    warning_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(32))
    trial_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel_join_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class AccessRequest(Base):
    __tablename__ = "access_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    terms_version: Mapped[str] = mapped_column(String(32))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_days: Mapped[int | None] = mapped_column(Integer)
    admin_note: Mapped[str | None] = mapped_column(Text)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    plan: Mapped[str] = mapped_column(String(32))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    warning_3d_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_charge_id: Mapped[str | None] = mapped_column(String(255), unique=True)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    plan: Mapped[str] = mapped_column(String(32))
    stars: Mapped[int] = mapped_column(Integer)
    sar_amount: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(String(128), default="")
    telegram_charge_id: Mapped[str] = mapped_column(String(255), unique=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    channel_id: Mapped[str] = mapped_column(String(128))
    invite_link: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    member_limit: Mapped[int] = mapped_column(Integer, default=1)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AdminRole(Base):
    __tablename__ = "admin_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32), default="moderator", index=True)
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_telegram_id: Mapped[int | None] = mapped_column(Integer, index=True)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class RadarRun(Base):
    __tablename__ = "radar_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[str] = mapped_column(String(16), index=True)
    session: Mapped[str] = mapped_column(String(24), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), index=True)
    candidates: Mapped[int] = mapped_column(Integer, default=0)
    shortlist: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    filtered: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    channel_gate_passed: Mapped[int] = mapped_column(Integer, default=0)
    channel_sent: Mapped[int] = mapped_column(Integer, default=0)
    channel_app_only: Mapped[int] = mapped_column(Integer, default=0)
    channel_gate_rejections: Mapped[str] = mapped_column(Text, default="{}")
    top_opportunities: Mapped[str] = mapped_column(Text, default="[]")
    diagnostics: Mapped[str] = mapped_column(Text, default="{}")
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)

class RadarSignal(Base):
    __tablename__ = "radar_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    session_date: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: expiry_at(), index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    __table_args__ = (UniqueConstraint("symbol", "session_date", name="uq_radar_symbol_session"),)

class RadarOutcome(Base):
    __tablename__ = "radar_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    radar_signal_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    session_date: Mapped[str] = mapped_column(String(16), index=True)
    target1: Mapped[float | None] = mapped_column()
    target2: Mapped[float | None] = mapped_column()
    target3: Mapped[float | None] = mapped_column()
    target4: Mapped[float | None] = mapped_column()
    target5: Mapped[float | None] = mapped_column()
    exit_level: Mapped[float | None] = mapped_column()
    entry_price: Mapped[float | None] = mapped_column()
    current_stop: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    achieved_target: Mapped[int] = mapped_column(Integer, default=0)
    current_price: Mapped[float | None] = mapped_column()
    last_alert_target: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class StockAnalysis(Base):
    __tablename__ = "stock_analyses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

async def _sqlite_add_columns(conn, table, columns):
    existing = {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
    for col, definition in columns.items():
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}"))

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.url.get_backend_name() == "sqlite":
            # Mini App requests must not stall behind radar/subscription writes.
            # WAL allows readers to continue while background jobs commit, and the
            # busy timeout gives short-lived SQLite write contention time to clear.
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA busy_timeout=5000"))
            await _sqlite_add_columns(conn, "users", {
                "last_name": "VARCHAR(128)",
                "status": "VARCHAR(24) DEFAULT 'new'",
                "trial_start": "DATETIME",
                "trial_expires": "DATETIME",
                "subscription_start": "DATETIME",
                "subscription_expires": "DATETIME",
                "plan": "VARCHAR(32)",
                "free_access": "BOOLEAN DEFAULT 0",
                "warning_sent_at": "DATETIME",
                "updated_at": "DATETIME",
                "terms_accepted_at": "DATETIME",
                "terms_version": "VARCHAR(32)",
                "trial_used_at": "DATETIME",
                "channel_join_requested_at": "DATETIME",
            })
            await _sqlite_add_columns(conn, "radar_signals", {
                "telegram_message_id": "INTEGER",
                "expires_at": "DATETIME",
            })
            # Backfill existing rows once; later rescans never extend expiry.
            await conn.execute(text("""
                UPDATE radar_signals
                SET expires_at = datetime(created_at, '+24 hours')
                WHERE expires_at IS NULL AND created_at IS NOT NULL
            """))
            await _sqlite_add_columns(conn, "radar_outcomes", {
                "target4": "FLOAT",
                "target5": "FLOAT",
                "entry_price": "FLOAT",
                "current_stop": "FLOAT",
                "last_alert_target": "INTEGER DEFAULT 0",
            })
            await _sqlite_add_columns(conn, "payments", {
                "sar_amount": "INTEGER DEFAULT 0",
                "payload": "VARCHAR(128) DEFAULT ''",
                "paid_at": "DATETIME",
            })
            # أي مستخدم بدأ تجربة قديمة يُعتبر قد استخدم التجربة بالفعل.
            # هذا يمنع إعادة التجربة بعد التحديث إلى النظام النهائي.
            await conn.execute(text("""
                UPDATE users
                SET trial_used_at = COALESCE(trial_used_at, trial_start, trial_expires)
                WHERE trial_used_at IS NULL
                  AND (trial_start IS NOT NULL OR trial_expires IS NOT NULL)
            """))
            # توحيد إعداد التجربة الافتراضي القديم إلى 3 أيام للمستخدمين الجدد.
            # لا نغيّر إعدادًا مخصصًا بقيمة أخرى سبق أن ضبطها المالك.
            await conn.execute(text("""
                UPDATE settings
                SET value = '30', updated_at = CURRENT_TIMESTAMP
                WHERE key = 'trial_days' AND value = '3'
            """))
            # ترحيل آمن لقيم الاشتراكات القديمة فقط. إذا غيّر المشرف قيمة يدويًا،
            # لا نعتبرها قيمة قديمة ولا نستبدلها.
            for key, old_value, new_value in (
                ("monthly_sar", "100", "150"),
                ("3month_sar", "250", "405"),
                ("6month_sar", "500", "720"),
                ("yearly_sar", "1000", "1260"),
                ("monthly_stars", "0", "2250"),
                ("3month_stars", "0", "6075"),
                ("6month_stars", "0", "10800"),
                ("yearly_stars", "0", "18900"),
            ):
                await conn.execute(text("""
                    UPDATE settings
                    SET value = :new_value, updated_at = CURRENT_TIMESTAMP
                    WHERE key = :key AND value = :old_value
                """), {"key": key, "old_value": old_value, "new_value": new_value})

async def get_session():
    async with SessionLocal() as session:
        yield session
