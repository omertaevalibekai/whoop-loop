from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.insights import GREEN, RED, bedtime_series
from app.analytics.series import get_series
from app.config import settings
from app.models import Sleep
from app.util import now_local, plural, today_local

# Clock hours are kept on a "night scale" where evening runs past 24:
# 23:30 is 23.5, 01:00 is 25.0, 04:00 is 28.0. Wake times sit on the same
# scale shifted a day, so sleep length is a plain subtraction.
NIGHT_PIVOT = 12.0

MIN_MODEL_DAYS = 14
FALLBACK_NEED_HOURS = 7.5


def to_night_scale(hour: float) -> float:
    return hour + 24 if hour < NIGHT_PIVOT else hour


def fmt_clock(value: float) -> str:
    value = value % 24
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    if minutes == 60:
        hours, minutes = hours + 1, 0
    return f"{hours % 24:02d}:{minutes:02d}"


def fmt_duration(hours: float) -> str:
    total = int(round(hours * 60))
    h, m = divmod(max(0, total), 60)
    if h == 0:
        return f"{m} мин"
    return f"{h} ч {m:02d} мин"


@dataclass
class EveningPlan:
    need_hours: float
    need_source: str            # "личная" | "оценка"
    wake_hour: float            # night scale
    efficiency: float
    debt_hours: float
    target_bed: float           # night scale
    minutes_left: float
    usual_bed: float | None
    forecast_now: float | None
    forecast_usual: float | None
    forecast_error: float | None
    model_days: int

    def render(self) -> str:
        lines = ["🌙 <b>План на ночь</b>", ""]

        target_text = fmt_clock(self.target_bed)
        if self.minutes_left > 0:
            left = fmt_duration(self.minutes_left / 60)
            lines.append(
                f"Чтобы выспаться к своему обычному подъёму "
                f"({fmt_clock(self.wake_hour)}), надо быть в постели "
                f"к <b>{target_text}</b> — это через {left}."
            )
        else:
            lines.append(
                f"Время отбоя под нормальный сон было в <b>{target_text}</b>. "
                f"Оно прошло — каждый час сверху теперь снимается с утра."
            )

        source = "твоя личная норма" if self.need_source == "личная" else "оценка"
        lines.append(
            f"<i>Считаю от {self.need_hours:.1f} ч сна ({source}) "
            f"и эффективности {self.efficiency * 100:.0f}%.</i>"
        )

        if self.debt_hours >= 0.5:
            lines += [
                "",
                f"Долг сна: <b>{fmt_duration(self.debt_hours)}</b>. "
                f"Чтобы его сокращать, а не копить, добавь сверху ещё "
                f"{fmt_duration(min(1.0, self.debt_hours / 2))}.",
            ]

        if self.forecast_now is not None and self.forecast_usual is not None:
            gap = self.forecast_now - self.forecast_usual
            lines += ["", "<b>Что будет утром</b>"]
            lines.append(
                f"Ляжешь в ближайший час → около <b>{self.forecast_now:.0f}%</b>"
            )
            if self.usual_bed is not None:
                lines.append(
                    f"Ляжешь как обычно ({fmt_clock(self.usual_bed)}) → "
                    f"около <b>{self.forecast_usual:.0f}%</b>"
                )
            if gap >= 5:
                word = plural(gap, "пункт", "пункта", "пунктов")
                lines.append(f"Разница — <b>{gap:.0f} {word}</b> восстановления.")
            if self.forecast_error:
                word = plural(self.forecast_error, "пункт", "пункта", "пунктов")
                lines.append(
                    f"<i>Точность прогноза ±{self.forecast_error:.0f} {word}, "
                    f"модель на {self.model_days} днях. Прогноз не выходит за "
                    f"пределы тех ночей, что уже были.</i>"
                )

        return "\n".join(lines)


def personal_need(session: Session, days: int, end_day: dt.date) -> tuple[float, str]:
    """Hours of sleep that separate your green mornings from your red ones."""
    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    hours = get_series(session, "sleep_hours", days=days, end_day=end_day)

    good = [hours[d] for d in recovery if recovery[d] >= GREEN and d in hours]
    if len(good) >= 3:
        return statistics.median(good), "личная"

    # Not enough green days yet: fall back on what Whoop thinks you need.
    rows = session.execute(
        select(Sleep.need_baseline_ms)
        .where(Sleep.day <= end_day, Sleep.nap.is_(False),
               Sleep.need_baseline_ms.is_not(None))
        .order_by(Sleep.day.desc())
        .limit(14)
    ).scalars().all()
    if rows:
        return statistics.median(rows) / 3_600_000.0, "оценка"
    return FALLBACK_NEED_HOURS, "оценка"


def wake_and_efficiency(
    session: Session, days: int, end_day: dt.date
) -> tuple[float, float]:
    start = end_day - dt.timedelta(days=days - 1)
    rows = session.execute(
        select(Sleep.end, Sleep.efficiency_pct)
        .where(Sleep.day >= start, Sleep.day <= end_day,
               Sleep.nap.is_(False), Sleep.end.is_not(None))
    ).all()

    wakes, effs = [], []
    for moment, eff in rows:
        local = moment.astimezone(settings.tz)
        wakes.append(local.hour + local.minute / 60)
        if eff:
            effs.append(eff / 100.0)

    wake = statistics.median(wakes) if wakes else 8.0
    efficiency = statistics.median(effs) if effs else 0.90
    # Keep the correction sane if a night reports oddly.
    efficiency = min(0.98, max(0.80, efficiency))
    # Waking happens the morning after the bedtime scale starts, hence +24:
    # a 10:45 wake-up sits at 34.75 against a 03:59 bedtime at 27.98.
    return wake + 24, efficiency


def current_debt_hours(session: Session, end_day: dt.date) -> float:
    row = session.execute(
        select(Sleep.need_debt_ms)
        .where(Sleep.day <= end_day, Sleep.nap.is_(False))
        .order_by(Sleep.day.desc())
        .limit(1)
    ).scalar()
    return (row or 0) / 3_600_000.0


def fit_model(session: Session, days: int, end_day: dt.date):
    """Least squares on your own nights: sleep length and bedtime -> recovery.

    Returns (predict, residual_std, n) or None when there is too little history
    to say anything that is not noise.
    """
    import numpy as np

    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    hours = get_series(session, "sleep_hours", days=days, end_day=end_day)
    beds = bedtime_series(session, days, end_day)

    rows = [
        (hours[d], beds[d], recovery[d])
        for d in sorted(recovery)
        if d in hours and d in beds
    ]
    if len(rows) < MIN_MODEL_DAYS:
        return None

    X = np.array([[1.0, h, b] for h, b, _ in rows])
    y = np.array([r for _, _, r in rows])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    residuals = y - X @ coef
    error = float(np.std(residuals, ddof=min(3, len(rows) - 1)))

    h_lo, h_hi = float(X[:, 1].min()), float(X[:, 1].max())
    b_lo, b_hi = float(X[:, 2].min()), float(X[:, 2].max())

    def predict(sleep_hours: float, bedtime: float) -> float:
        # Never extrapolate past the nights the model has actually seen.
        sleep_hours = min(h_hi, max(h_lo, sleep_hours))
        bedtime = min(b_hi, max(b_lo, bedtime))
        value = float(coef @ np.array([1.0, sleep_hours, bedtime]))
        return max(1.0, min(99.0, value))

    return predict, error, len(rows)


def build_plan(session: Session, moment: dt.datetime | None = None,
               days: int = 60) -> EveningPlan:
    moment = moment or now_local()
    end_day = today_local()

    need, source = personal_need(session, days, end_day)
    wake, efficiency = wake_and_efficiency(session, 21, end_day)
    debt = current_debt_hours(session, end_day)

    # Time in bed has to exceed time asleep by the efficiency gap.
    time_in_bed = need / efficiency
    target_bed = wake - time_in_bed

    now_scale = to_night_scale(moment.hour + moment.minute / 60)
    minutes_left = (target_bed - now_scale) * 60

    beds = bedtime_series(session, days, end_day)
    usual_bed = statistics.median(beds.values()) if beds else None

    forecast_now = forecast_usual = error = None
    model_days = 0
    model = fit_model(session, days, end_day)
    if model is not None:
        predict, error, model_days = model
        # "Now" means actually asleep about half an hour from this message.
        bed_now = now_scale + 0.5
        forecast_now = predict(max(0.0, (wake - bed_now) * efficiency), bed_now)
        if usual_bed is not None:
            forecast_usual = predict(
                max(0.0, (wake - usual_bed) * efficiency), usual_bed
            )

    return EveningPlan(
        need_hours=need,
        need_source=source,
        wake_hour=wake,
        efficiency=efficiency,
        debt_hours=debt,
        target_bed=target_bed,
        minutes_left=minutes_left,
        usual_bed=usual_bed,
        forecast_now=forecast_now,
        forecast_usual=forecast_usual,
        forecast_error=error,
        model_days=model_days,
    )


def render(session: Session, moment: dt.datetime | None = None) -> str:
    return build_plan(session, moment).render()
