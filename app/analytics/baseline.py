from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.analytics.series import Series, get_series

# Median absolute deviation to standard-deviation conversion for normal data.
MAD_TO_SIGMA = 1.4826

# Days of history that form the personal baseline, and how many recent days are
# held out of it so today is compared against a period it did not influence.
BASELINE_DAYS = 28
EXCLUDE_RECENT_DAYS = 2
MIN_BASELINE_POINTS = 10


@dataclass
class Baseline:
    metric: str
    center: float | None
    scale: float | None
    n: int

    @property
    def usable(self) -> bool:
        return (
            self.center is not None
            and self.scale is not None
            and self.scale > 0
            and self.n >= MIN_BASELINE_POINTS
        )

    def z(self, value: float | None) -> float | None:
        """Robust z-score: how far a value sits from the personal norm."""
        if value is None or not self.usable:
            return None
        return (value - self.center) / self.scale

    def deviation(self, value: float | None) -> float | None:
        if value is None or self.center is None:
            return None
        return value - self.center


def robust_stats(values: list[float]) -> tuple[float | None, float | None]:
    """Median and MAD-derived scale. Robust to the odd travel day or bender."""
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return (statistics.median(clean) if clean else None, None)
    center = statistics.median(clean)
    mad = statistics.median([abs(v - center) for v in clean])
    scale = mad * MAD_TO_SIGMA
    if scale == 0:
        # Degenerate spread (e.g. an integer metric that barely moves):
        # fall back to stdev so the z-score stays defined.
        scale = statistics.pstdev(clean) or None
    return center, scale


def build_baseline(
    session: Session,
    metric: str,
    day: dt.date,
    window: int = BASELINE_DAYS,
    exclude_recent: int = EXCLUDE_RECENT_DAYS,
) -> Baseline:
    end = day - dt.timedelta(days=exclude_recent)
    series = get_series(session, metric, days=window, end_day=end)
    center, scale = robust_stats(list(series.values()))
    return Baseline(metric=metric, center=center, scale=scale, n=len(series))


def trend_slope(series: Series, days: int | None = None) -> float | None:
    """Least-squares slope in units per day. `days` limits to the newest window."""
    if not series:
        return None
    items = sorted(series.items())
    if days is not None:
        cutoff = items[-1][0] - dt.timedelta(days=days - 1)
        items = [(d, v) for d, v in items if d >= cutoff]
    if len(items) < 3:
        return None

    origin = items[0][0]
    xs = [(d - origin).days for d, _ in items]
    ys = [v for _, v in items]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return numerator / denominator


def ewma(series: Series, alpha: float = 0.15) -> Series:
    """Exponentially weighted mean over calendar days, gaps included.

    Daily weight is mostly water; the smoothed line is the part worth reacting to.
    """
    if not series:
        return {}
    items = sorted(series.items())
    out: Series = {}
    current: float | None = None
    previous_day: dt.date | None = None
    for day, value in items:
        if current is None:
            current = value
        else:
            gap = (day - previous_day).days if previous_day else 1
            # A longer gap should let the new reading count for more.
            effective = 1 - (1 - alpha) ** max(1, gap)
            current = effective * value + (1 - effective) * current
        out[day] = current
        previous_day = day
    return out


def mean_of(series: Series, days: int, end_day: dt.date | None = None) -> float | None:
    if not series:
        return None
    end_day = end_day or max(series)
    start = end_day - dt.timedelta(days=days - 1)
    values = [v for d, v in series.items() if start <= d <= end_day]
    return sum(values) / len(values) if values else None
