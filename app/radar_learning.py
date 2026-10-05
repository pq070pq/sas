"""SAS PRO adaptive radar learning.

The learner never edits source code or bypasses safety gates. It only derives a
bounded profile from completed radar outcomes and stores that profile in the
settings table. The profile is deliberately conservative to avoid overfitting.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from .db import SessionLocal, Setting, RadarOutcome, RadarSignal


DEFAULT_PROFILE = {
    "version": 1,
    "samples": 0,
    "win_rate": 0.0,
    "rvol_floor": 0.50,
    "change_floor": 0.00,
    "high_confidence_bonus": 0.0,
    "updated_at": None,
}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def learn_radar_profile() -> dict:
    """Learn only from completed outcomes; unresolved signals never teach the model."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(RadarOutcome, RadarSignal)
            .join(RadarSignal, RadarSignal.id == RadarOutcome.radar_signal_id)
            .where(RadarOutcome.session_date >= cutoff)
            .order_by(RadarOutcome.created_at.desc())
            .limit(250)
        )).all()

        completed = []
        for outcome, signal in rows:
            if outcome.status == "failed":
                completed.append((outcome, signal, False))
            elif int(outcome.achieved_target or 0) >= 1:
                completed.append((outcome, signal, True))

        profile = dict(DEFAULT_PROFILE)
        profile["samples"] = len(completed)

        if completed:
            wins = sum(1 for _, _, win in completed if win)
            profile["win_rate"] = round(wins / len(completed), 4)

        # Start conservative. Adapt only after enough completed observations.
        if len(completed) >= 20:
            wr = profile["win_rate"]
            if wr < 0.35:
                profile["rvol_floor"] = 0.65
                profile["change_floor"] = 0.50
                profile["high_confidence_bonus"] = 6.0
            elif wr > 0.70:
                profile["rvol_floor"] = 0.45
                profile["change_floor"] = 0.00
                profile["high_confidence_bonus"] = 2.0
            else:
                profile["rvol_floor"] = 0.50
                profile["change_floor"] = 0.00
                profile["high_confidence_bonus"] = 0.0

        # Never let learning create extreme behaviour.
        profile["rvol_floor"] = min(0.75, max(0.45, profile["rvol_floor"]))
        profile["change_floor"] = min(1.0, max(0.0, profile["change_floor"]))
        profile["updated_at"] = datetime.now(timezone.utc).isoformat()

        setting = (await db.execute(
            select(Setting).where(Setting.key == "radar_learning_profile")
        )).scalars().first()
        raw = json.dumps(profile, ensure_ascii=False)
        if setting:
            setting.value = raw
            setting.updated_at = datetime.now(timezone.utc)
        else:
            db.add(Setting(key="radar_learning_profile", value=raw, updated_at=datetime.now(timezone.utc)))
        await db.commit()

    return profile


async def get_radar_profile() -> dict:
    async with SessionLocal() as db:
        setting = (await db.execute(
            select(Setting).where(Setting.key == "radar_learning_profile")
        )).scalars().first()
        if not setting:
            return dict(DEFAULT_PROFILE)
        try:
            data = json.loads(setting.value or "{}")
        except Exception:
            return dict(DEFAULT_PROFILE)

    profile = dict(DEFAULT_PROFILE)
    profile.update(data)
    profile["rvol_floor"] = min(0.75, max(0.45, _num(profile.get("rvol_floor"), 0.50)))
    profile["change_floor"] = min(1.0, max(0.0, _num(profile.get("change_floor"), 0.0)))
    return profile
