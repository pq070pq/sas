"""Quality Score Backtest / Forward Test aggregation."""
from __future__ import annotations
from collections import defaultdict
from typing import Iterable, Any

BUCKETS = (
    (85, 101, "85-100"),
    (75, 85, "75-84"),
    (65, 75, "65-74"),
    (0, 65, "under-65"),
)

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
    buckets = defaultdict(lambda: {"completed": 0, "success": 0, "failure": 0, "active": 0})
    for record in records:
        bucket = quality_bucket(record.get("quality_score"))
        if bucket == "unknown":
            continue
        item = buckets[bucket]
        status = str(record.get("status") or "active")
        achieved = int(record.get("achieved_target") or 0)
        if status == "failed":
            item["completed"] += 1
            item["failure"] += 1
        elif achieved > 0:
            item["completed"] += 1
            item["success"] += 1
        else:
            item["active"] += 1
    result = {}
    for _, _, label in BUCKETS:
        item = buckets[label]
        completed = item["completed"]
        result[label] = {
            **item,
            "success_rate": round(item["success"] / completed * 100, 1) if completed else None,
        }
    return result
