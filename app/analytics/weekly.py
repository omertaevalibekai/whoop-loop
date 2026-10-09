from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import insights
from app.analytics.series import get_series
from app.models import Workout
from app.util import today_local


def _window(series: dict[dt.date, float], start: dt.date,
            end: dt.date) -> list[float]:
    return [v for d, v in series.items() if start <= d <= end]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _delta_line(label: str, now: float | None, before: float | None,
                unit: str = "", digits: int = 0) -> str:
    if now is None:
        return f"{label}: нет данных"
    text = f"{label}: <b>{now:.{digits}f}{unit}</b>"
    if before is not None:
        change = now - before
        if abs(change) >= 0.5 * 10 ** -digits:
            arrow = "↑" if change > 0 else "↓"
            text += f"  {arrow} {abs(change):.{digits}f} к прошлой неделе"
        else:
            text += "  = как на прошлой"
    return text


def build_weekly(session: Session, end_day: dt.date | None = None) -> str:
    """A week in review, built only from what Whoop collects on its own."""
    end_day = end_day or today_local()
    start = end_day - dt.timedelta(days=6)
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=6)

    recovery = get_series(session, "recovery", days=21, end_day=end_day)
    hours = get_series(session, "sleep_hours", days=21, end_day=end_day)
    perf = get_series(session, "sleep_perf", days=21, end_day=end_day)
    strain = get_series(session, "strain", days=21, end_day=end_day)
    hrv = get_series(session, "hrv", days=21, end_day=end_day)
    rhr = get_series(session, "rhr", days=21, end_day=end_day)

    week_recovery = _window(recovery, start, end_day)
    if not week_recovery:
        return (
            "<b>Неделя</b>\n\nЗа последние семь дней Whoop не прислал ни одной "
            "оценки восстановления. Проверь, что браслет носится и сон "
            "подтверждается в приложении."
        )

    lines = [
        f"<b>Итоги недели</b>",
        f"<i>{start.strftime('%d.%m')} — {end_day.strftime('%d.%m')}</i>",
        "",
        _delta_line("Восстановление", _mean(week_recovery),
                    _mean(_window(recovery, prev_start, prev_end)), " %"),
        _delta_line("Сон", _mean(_window(hours, start, end_day)),
                    _mean(_window(hours, prev_start, prev_end)), " ч", 1),
        _delta_line("Качество сна", _mean(_window(perf, start, end_day)),
                    _mean(_window(perf, prev_start, prev_end)), " %"),
        _delta_line("HRV", _mean(_window(hrv, start, end_day)),
                    _mean(_window(hrv, prev_start, prev_end)), " мс"),
        _delta_line("Пульс покоя", _mean(_window(rhr, start, end_day)),
                    _mean(_window(rhr, prev_start, prev_end)), " уд/мин"),
        _delta_line("Нагрузка", _mean(_window(strain, start, end_day)),
                    _mean(_window(strain, prev_start, prev_end)), "", 1),
    ]

    # Best and worst night, which is usually the most actionable line here.
    week_days = {d: v for d, v in recovery.items() if start <= d <= end_day}
    best = max(week_days, key=week_days.get)
    worst = min(week_days, key=week_days.get)
    lines += [
        "",
        f"Лучшее утро: <b>{week_days[best]:.0f}%</b> — {best.strftime('%d.%m')}"
        + (f", спал {hours[best]:.1f} ч" if best in hours else ""),
        f"Худшее утро: <b>{week_days[worst]:.0f}%</b> — {worst.strftime('%d.%m')}"
        + (f", спал {hours[worst]:.1f} ч" if worst in hours else ""),
    ]

    rows = session.execute(
        select(Workout.sport_name, func.count(), func.sum(Workout.strain))
        .where(Workout.day >= start, Workout.day <= end_day)
        .group_by(Workout.sport_name)
        .order_by(func.count().desc())
    ).all()
    if rows:
        parts = [f"{name} ×{count}" for name, count, _ in rows]
        lines += ["", "Активность: " + ", ".join(parts)]

    debt = get_series(session, "sleep_hours", days=7, end_day=end_day)
    if len(debt) < 7:
        lines.append(f"<i>Ночей с данными: {len(debt)} из 7.</i>")

    top = insights.collect(session, days=60, end_day=end_day)
    if top:
        lines += ["", "— — —", "", f"<b>{top[0].title}</b>", top[0].body]

    return "\n".join(lines)
