from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .config import settings

engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
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
    telegram_charge_id: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class RadarSignal(Base):
    __tablename__ = "radar_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    session_date: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
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
    exit_level: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    achieved_target: Mapped[int] = mapped_column(Integer, default=0)
    current_price: Mapped[float | None] = mapped_column()
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

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe SQLite migration for existing production databases.
        if engine.url.get_backend_name() == "sqlite":
            cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()}
            if "terms_accepted_at" not in cols:
                await conn.execute(text("ALTER TABLE users ADD COLUMN terms_accepted_at DATETIME"))
            if "terms_version" not in cols:
                await conn.execute(text("ALTER TABLE users ADD COLUMN terms_version VARCHAR(32)"))
            if "trial_used_at" not in cols:
                await conn.execute(text("ALTER TABLE users ADD COLUMN trial_used_at DATETIME"))
            if "channel_join_requested_at" not in cols:
                await conn.execute(text("ALTER TABLE users ADD COLUMN channel_join_requested_at DATETIME"))

async def get_session():
    async with SessionLocal() as session:
        yield session
