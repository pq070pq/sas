from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)

def _nth_weekday(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(days=7 * (n - 1))

def _last_weekday(year, month, weekday):
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)

def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def us_market_holidays(year):
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),   # MLK
        _nth_weekday(year, 2, 0, 3),   # Presidents Day
        _last_weekday(year, 5, 0),      # Memorial Day
        _observed(date(year, 6, 19)),   # Juneteenth
        _observed(date(year, 7, 4)),    # Independence Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    # Good Friday is an NYSE full-day closure but not a US federal holiday.
    easter = _easter(year)
    holidays.add(easter - timedelta(days=2))
    return holidays

def _easter(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    month = (h + l - 7*m + 114) // 31
    day = ((h + l - 7*m + 114) % 31) + 1
    return date(year, month, day)

def market_status(now=None):
    now = now or datetime.now(NY)
    d = now.date()
    if d.weekday() >= 5:
        return {"open": False, "mode": "closed", "reason": "weekend", "local_time": now.isoformat()}
    if d in us_market_holidays(d.year):
        return {"open": False, "mode": "holiday", "reason": "US market holiday", "local_time": now.isoformat()}
    if OPEN <= now.time() < CLOSE:
        return {"open": True, "mode": "open", "reason": "regular_session", "local_time": now.isoformat()}
    return {"open": False, "mode": "closed", "reason": "outside_regular_session", "local_time": now.isoformat()}
