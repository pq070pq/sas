"""Quality Score Backtest / Forward Test aggregation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

BUCKETS = (
    (85, 101, "85-100"),
    (75, 85, "75-84"),
    (65, 75, "65-74"),
    (0, 65, "under-65"),
)
ACTIVE_STATUSES = {"active", "target1", "target2", "target3", "target4"}
TERMINAL_FAILURE_STATUSES = {"failed"}
EXPIRED_STATUSES = {"expired"}


def quality_bucket(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    for low, high, label in BUCKETS:
        if low <= value < high:
            return label
    return "unknown"


def summarize_quality_outcomes(records: Iterable[dict]) -> dict:
    """Summarize published signal outcomes without treating expiry as failure.

    A signal that has reached at least one target is counted as a target hit,
    even if later monitoring expires. Expired signals with no target hit are
    reported separately and excluded from the success-rate denominator.
    Active signals (including those currently between targets) are not counted
    as completed. Unknown statuses are surfaced rather than silently counted.
    """
    empty = {
        "completed": 0,
        "success": 0,
        "failure": 0,
        "active": 0,
        "expired": 0,
        "unclassified": 0,
    }
    buckets = defaultdict(lambda: dict(empty))
    for record in records:
        bucket = quality_bucket(record.get("quality_score"))
        if bucket == "unknown":
            continue
        item = buckets[bucket]
        status = str(record.get("status") or "active").strip().lower()
        try:
            achieved = max(0, int(record.get("achieved_target") or 0))
        except (TypeError, ValueError):
            achieved = 0

        if achieved > 0:
            item["success"] += 1
            item["completed"] += 1
        elif status in TERMINAL_FAILURE_STATUSES:
            item["failure"] += 1
            item["completed"] += 1
        elif status in EXPIRED_STATUSES:
            item["expired"] += 1
        elif status in ACTIVE_STATUSES:
            item["active"] += 1
        else:
            item["unclassified"] += 1

    result = {}
    for _, _, label in BUCKETS:
        item = buckets[label]
        completed = item["completed"]
        result[label] = {
            **item,
            "success_rate": round(item["success"] / completed * 100, 1) if completed else None,
        }
    return result
