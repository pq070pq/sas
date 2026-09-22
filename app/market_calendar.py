from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
DISPLAY_TZ = ZoneInfo("Asia/Riyadh")

PREMARKET_START = (4, 0)
REGULAR_START = (9, 30)
REGULAR_END = (16, 0)
AFTERHOURS_END = (20, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_market_holidays(year: int) -> set[date]:
    easter = easter_sunday(year)
    return {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        easter - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }


def early_close_time(local_date: date):
    if local_date == _day_after_thanksgiving(local_date.year):
        return (13, 0)
    if local_date.month == 12 and local_date.day == 24 and local_date.weekday() < 5:
        return (13, 0)
    if local_date.month == 7 and local_date.day == 3 and local_date.weekday() < 5:
        return (13, 0)
    return REGULAR_END


def _day_after_thanksgiving(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4) + timedelta(days=1)


def _dt_at(local: datetime, hour: int, minute: int) -> datetime:
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _minutes_between(a: datetime, b: datetime) -> int:
    return max(0, int((b - a).total_seconds() // 60))


def market_status(now: datetime | None = None) -> dict:
    now = now or datetime.now(MARKET_TZ)
    local = now.astimezone(MARKET_TZ)
    d = local.date()

    if d in us_market_holidays(local.year):
        return {
            "open": False, "holiday": True, "session": "holiday",
            "label_ar": "مغلق — عطلة السوق 🇺🇸", "short_ar": "عطلة",
            "reason": "US market holiday", "date": d.isoformat(),
            "local_time": local.isoformat(),
        }

    if local.weekday() >= 5:
        return {
            "open": False, "holiday": False, "session": "weekend",
            "label_ar": "مغلق — لا يوجد تداول", "short_ar": "لا تداول",
            "reason": "weekend", "date": d.isoformat(),
            "local_time": local.isoformat(),
        }

    regular_end_h, regular_end_m = early_close_time(d)
    pre_start = _dt_at(local, *PREMARKET_START)
    regular_start = _dt_at(local, *REGULAR_START)
    regular_end = _dt_at(local, regular_end_h, regular_end_m)
    after_end = _dt_at(local, *AFTERHOURS_END)

    if pre_start <= local < regular_start:
        return {
            "open": False, "holiday": False, "session": "premarket",
            "label_ar": "قبل الافتتاح 🟡", "short_ar": "قبل الافتتاح",
            "reason": "premarket", "date": d.isoformat(),
            "local_time": local.isoformat(),
            "minutes_to_open": _minutes_between(local, regular_start),
            "open_time_et": "09:30",
            "close_time_et": f"{regular_end_h:02d}:{regular_end_m:02d}",
        }

    if regular_start <= local < regular_end:
        return {
            "open": True, "holiday": False, "session": "regular",
            "label_ar": "السوق مفتوح الآن 🟢", "short_ar": "مفتوح الآن",
            "reason": "regular session" if (regular_end_h, regular_end_m) == REGULAR_END else "early close",
            "date": d.isoformat(), "local_time": local.isoformat(),
            "close_time_et": f"{regular_end_h:02d}:{regular_end_m:02d}",
            "minutes_to_close": _minutes_between(local, regular_end),
        }

    if regular_end <= local < after_end:
        return {
            "open": False, "holiday": False, "session": "afterhours",
            "label_ar": "بعد الإغلاق 🟠", "short_ar": "بعد الإغلاق",
            "reason": "after hours", "date": d.isoformat(),
            "local_time": local.isoformat(),
            "minutes_to_afterhours_close": _minutes_between(local, after_end),
        }

    return {
        "open": False, "holiday": False, "session": "overnight",
        "label_ar": "مغلق — لا تداول ليلي 🔴", "short_ar": "لا تداول ليلي",
        "reason": "overnight", "date": d.isoformat(),
        "local_time": local.isoformat(), "next_premarket_et": "04:00",
    }


def is_market_open(now: datetime | None = None) -> bool:
    return market_status(now)["open"]


def display_now() -> datetime:
    return datetime.now(DISPLAY_TZ)
