from __future__ import annotations

import datetime as dt

from app.config import settings


def now_local() -> dt.datetime:
    return dt.datetime.now(settings.tz)


def today_local() -> dt.date:
    return now_local().date()


def parse_iso(value: str | None) -> dt.datetime | None:
    """Parse Whoop timestamps, which are ISO-8601 with a trailing Z."""
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def local_day(moment: dt.datetime | None) -> dt.date | None:
    if moment is None:
        return None
    return moment.astimezone(settings.tz).date()


def week_start(day: dt.date) -> dt.date:
    """Monday of the week containing `day`."""
    return day - dt.timedelta(days=day.weekday())


def ms_to_hm(ms: int | float | None) -> str:
    if ms is None:
        return "—"
    total_minutes = int(round(ms / 60000))
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"{minutes}м"
    return f"{hours}ч {minutes:02d}м"


def fmt_num(value: float | int | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "—"
    if digits == 0:
        return f"{round(value):,}".replace(",", " ") + suffix
    return f"{value:.{digits}f}{suffix}"


def fmt_signed(value: float | None, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    body = f"{abs(value):.{digits}f}" if digits else f"{round(abs(value)):,}".replace(",", " ")
    return f"{sign}{body}{suffix}"


def remaining_text(remaining: float | None) -> str:
    """Calories left for today, phrased the way a person would say it."""
    if remaining is None:
        return "—"
    if remaining >= 0:
        return f"осталось {fmt_num(remaining)}"
    return f"перебор на {fmt_num(abs(remaining))}"


def plural(n: float, one: str, few: str, many: str) -> str:
    """Russian numeral agreement: 1 пункт, 2 пункта, 5 пунктов."""
    count = int(abs(round(n)))
    if count % 100 in range(11, 15):
        return many
    last = count % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def kj_to_kcal(kilojoule: float | None) -> float | None:
    return None if kilojoule is None else kilojoule / 4.184


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
