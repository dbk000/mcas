"""US equities session calendar helpers (NYSE, free/open fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

RTH_OPEN_ET = time(9, 30)
RTH_CLOSE_ET = time(16, 0)


@dataclass(frozen=True)
class SessionInfo:
    session_state: str  # RTH | ETH | CLOSED
    next_open_ts: datetime
    last_close_ts: datetime


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _easter_date(year: int) -> date:
    # Anonymous Gregorian algorithm.
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


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday -> Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday -> Monday
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set[date]:
    holidays: set[date] = set()

    # New Year's Day (observed)
    holidays.add(_observed(date(year, 1, 1)))
    # MLK Day (3rd Monday Jan)
    holidays.add(_nth_weekday(year, 1, 0, 3))
    # Presidents' Day (3rd Monday Feb)
    holidays.add(_nth_weekday(year, 2, 0, 3))
    # Good Friday
    holidays.add(_easter_date(year) - timedelta(days=2))
    # Memorial Day (last Monday May)
    holidays.add(_last_weekday(year, 5, 0))
    # Juneteenth (observed, modern NYSE holiday)
    holidays.add(_observed(date(year, 6, 19)))
    # Independence Day (observed)
    holidays.add(_observed(date(year, 7, 4)))
    # Labor Day (1st Monday Sep)
    holidays.add(_nth_weekday(year, 9, 0, 1))
    # Thanksgiving (4th Thursday Nov)
    holidays.add(_nth_weekday(year, 11, 3, 4))
    # Christmas Day (observed)
    holidays.add(_observed(date(year, 12, 25)))

    # If Jan 1 observed for next year falls in this year, include it.
    next_new_year_observed = _observed(date(year + 1, 1, 1))
    if next_new_year_observed.year == year:
        holidays.add(next_new_year_observed)

    return holidays


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def _day_open_close_utc(d: date) -> tuple[datetime, datetime]:
    open_et = datetime.combine(d, RTH_OPEN_ET, tzinfo=ET)
    close_et = datetime.combine(d, RTH_CLOSE_ET, tzinfo=ET)
    return open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


def session_state(ts: datetime) -> str:
    ts_utc = ts.astimezone(timezone.utc)
    ts_et = ts_utc.astimezone(ET)
    d = ts_et.date()
    if not is_trading_day(d):
        return "CLOSED"

    open_utc, close_utc = _day_open_close_utc(d)
    if open_utc <= ts_utc < close_utc:
        return "RTH"
    return "ETH"


def session_relation(published_ts: datetime) -> str:
    ts_utc = published_ts.astimezone(timezone.utc)
    ts_et = ts_utc.astimezone(ET)
    d = ts_et.date()

    if not is_trading_day(d):
        return "weekend"

    open_et = datetime.combine(d, RTH_OPEN_ET, tzinfo=ET)
    close_et = datetime.combine(d, RTH_CLOSE_ET, tzinfo=ET)
    if ts_et < open_et:
        return "published_preopen"
    if open_et <= ts_et < close_et:
        return "intraday"
    return "postclose"


def next_open_ts(ts: datetime) -> datetime:
    ts_utc = ts.astimezone(timezone.utc)
    cur_day = ts_utc.astimezone(ET).date()

    for i in range(0, 15):
        d = cur_day + timedelta(days=i)
        if not is_trading_day(d):
            continue
        open_utc, _ = _day_open_close_utc(d)
        if open_utc > ts_utc:
            return open_utc

    raise RuntimeError("next_open_ts search exhausted")


def last_close_ts(ts: datetime) -> datetime:
    ts_utc = ts.astimezone(timezone.utc)
    cur_day = ts_utc.astimezone(ET).date()

    for i in range(0, 15):
        d = cur_day - timedelta(days=i)
        if not is_trading_day(d):
            continue
        _, close_utc = _day_open_close_utc(d)
        if close_utc <= ts_utc:
            return close_utc

    raise RuntimeError("last_close_ts search exhausted")


def session_info(ts: datetime) -> SessionInfo:
    return SessionInfo(
        session_state=session_state(ts),
        next_open_ts=next_open_ts(ts),
        last_close_ts=last_close_ts(ts),
    )


def open_gap_pct(prev_close: float, next_open: float) -> float | None:
    if prev_close <= 0:
        return None
    return (next_open - prev_close) / prev_close


def overnight_return(prev_close: float, next_open: float) -> float | None:
    return open_gap_pct(prev_close, next_open)
