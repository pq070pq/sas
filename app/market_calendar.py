from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
DISPLAY_TZ = ZoneInfo("Asia/Riyadh")

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
        _observed(date(year, 1, 1)),                                      # New Year
        _nth_weekday(year, 1, 0, 3),                                     # MLK
        _nth_weekday(year, 2, 0, 3),                                     # Presidents' Day
        easter - timedelta(days=2),                                      # Good Friday
        _last_weekday(year, 5, 0),                                       # Memorial Day
        _observed(date(year, 6, 19)),                                    # Juneteenth
        _observed(date(year, 7, 4)),                                     # Independence Day
        _nth_weekday(year, 9, 0, 1),                                     # Labor Day
        _nth_weekday(year, 11, 3, 4),                                    # Thanksgiving
        _observed(date(year, 12, 25)),                                   # Christmas
    }

def market_status(now: datetime | None = None) -> dict:
    now = now or datetime.now(MARKET_TZ)
    local = now.astimezone(MARKET_TZ)
    d = local.date()
    if d in us_market_holidays(local.year):
        return {"open": False, "holiday": True, "reason": "US market holiday", "date": d.isoformat()}
    if local.weekday() >= 5:
        return {"open": False, "holiday": True, "reason": "weekend", "date": d.isoformat()}
    start = local.replace(hour=9, minute=30, second=0, microsecond=0)
    end = local.replace(hour=16, minute=0, second=0, microsecond=0)
    return {
        "open": start <= local < end,
        "holiday": False,
        "reason": "regular session",
        "date": d.isoformat(),
        "local_time": local.isoformat(),
    }

def is_market_open(now: datetime | None = None) -> bool:
    return market_status(now)["open"]

def display_now() -> datetime:
    return datetime.now(DISPLAY_TZ)
