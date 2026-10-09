from __future__ import annotations

import datetime as dt
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Cycle, IntakeLog, Recovery, Sleep, WeightLog, Workout
from app.util import kj_to_kcal, today_local

Series = dict[dt.date, float]

# Metrics whose *rise* is a bad sign. Used by the illness detector and the
# deficit guard so a single sign convention does not have to be repeated.
HIGHER_IS_WORSE = {"rhr", "resp_rate", "skin_temp", "disturbances"}

LABELS: dict[str, str] = {
    "recovery": "Восстановление, %",
    "hrv": "HRV, мс",
    "rhr": "Пульс покоя, уд/мин",
    "spo2": "Сатурация, %",
    "skin_temp": "Температура кожи, °C",
    "resp_rate": "Частота дыхания",
    "sleep_perf": "Качество сна, %",
    "sleep_eff": "Эффективность сна, %",
    "sleep_hours": "Сон, ч",
    "disturbances": "Пробуждения",
    "sleep_debt": "Долг сна, ч",
    "strain": "Нагрузка",
    "burn_kcal": "Расход, ккал",
    "weight": "Вес, кг",
    "intake_kcal": "Съедено, ккал",
    "protein_g": "Белок, г",
}


def _recovery_series(field: str) -> Callable[[Session, dt.date, dt.date], Series]:
    column = getattr(Recovery, field)

    def loader(session: Session, start: dt.date, end: dt.date) -> Series:
        rows = session.execute(
            select(Recovery.day, column)
            .where(Recovery.day >= start, Recovery.day <= end, column.is_not(None))
            .order_by(Recovery.day)
        ).all()
        return {day: float(value) for day, value in rows}

    return loader


def _sleep_series(expr) -> Callable[[Session, dt.date, dt.date], Series]:
    def loader(session: Session, start: dt.date, end: dt.date) -> Series:
        rows = session.execute(
            select(Sleep.day, expr)
            .where(
                Sleep.day >= start,
                Sleep.day <= end,
                Sleep.nap.is_(False),
                expr.is_not(None),
            )
            .order_by(Sleep.day)
        ).all()
        return {day: float(value) for day, value in rows}

    return loader


def _cycle_series(field: str) -> Callable[[Session, dt.date, dt.date], Series]:
    column = getattr(Cycle, field)

    def loader(session: Session, start: dt.date, end: dt.date) -> Series:
        rows = session.execute(
            select(Cycle.day, column)
            .where(Cycle.day >= start, Cycle.day <= end, column.is_not(None))
            .order_by(Cycle.day)
        ).all()
        return {day: float(value) for day, value in rows}

    return loader


def _burn_series(session: Session, start: dt.date, end: dt.date) -> Series:
    rows = session.execute(
        select(Cycle.day, Cycle.kilojoule)
        .where(Cycle.day >= start, Cycle.day <= end, Cycle.kilojoule.is_not(None))
        .order_by(Cycle.day)
    ).all()
    return {day: float(kj_to_kcal(value)) for day, value in rows}


def _sleep_hours(session: Session, start: dt.date, end: dt.date) -> Series:
    rows = session.execute(
        select(Sleep.day, Sleep.light_ms, Sleep.sws_ms, Sleep.rem_ms)
        .where(Sleep.day >= start, Sleep.day <= end, Sleep.nap.is_(False))
        .order_by(Sleep.day)
    ).all()
    out: Series = {}
    for day, light, sws, rem in rows:
        parts = [p for p in (light, sws, rem) if p is not None]
        if len(parts) == 3:
            out[day] = sum(parts) / 3_600_000.0
    return out


def _sleep_debt(session: Session, start: dt.date, end: dt.date) -> Series:
    rows = session.execute(
        select(Sleep.day, Sleep.need_debt_ms)
        .where(
            Sleep.day >= start,
            Sleep.day <= end,
            Sleep.nap.is_(False),
            Sleep.need_debt_ms.is_not(None),
        )
        .order_by(Sleep.day)
    ).all()
    return {day: float(value) / 3_600_000.0 for day, value in rows}


def _weight_series(session: Session, start: dt.date, end: dt.date) -> Series:
    rows = session.execute(
        select(WeightLog.day, WeightLog.kg)
        .where(WeightLog.day >= start, WeightLog.day <= end)
        .order_by(WeightLog.day)
    ).all()
    return {day: float(value) for day, value in rows}


def _intake_series(column) -> Callable[[Session, dt.date, dt.date], Series]:
    def loader(session: Session, start: dt.date, end: dt.date) -> Series:
        rows = session.execute(
            select(IntakeLog.day, func.sum(column))
            .where(IntakeLog.day >= start, IntakeLog.day <= end)
            .group_by(IntakeLog.day)
            .order_by(IntakeLog.day)
        ).all()
        return {day: float(value) for day, value in rows if value is not None}

    return loader


LOADERS: dict[str, Callable[[Session, dt.date, dt.date], Series]] = {
    "recovery": _recovery_series("recovery_score"),
    "hrv": _recovery_series("hrv_rmssd_milli"),
    "rhr": _recovery_series("resting_heart_rate"),
    "spo2": _recovery_series("spo2_percentage"),
    "skin_temp": _recovery_series("skin_temp_celsius"),
    "resp_rate": _sleep_series(Sleep.respiratory_rate),
    "sleep_perf": _sleep_series(Sleep.performance_pct),
    "sleep_eff": _sleep_series(Sleep.efficiency_pct),
    "disturbances": _sleep_series(Sleep.disturbance_count),
    "sleep_hours": _sleep_hours,
    "sleep_debt": _sleep_debt,
    "strain": _cycle_series("strain"),
    "burn_kcal": _burn_series,
    "weight": _weight_series,
    "intake_kcal": _intake_series(IntakeLog.kcal),
    "protein_g": _intake_series(IntakeLog.protein_g),
}


def get_series(
    session: Session,
    metric: str,
    days: int = 90,
    end_day: dt.date | None = None,
) -> Series:
    if metric not in LOADERS:
        raise KeyError(f"Неизвестная метрика: {metric}")
    end_day = end_day or today_local()
    start_day = end_day - dt.timedelta(days=days - 1)
    return LOADERS[metric](session, start_day, end_day)


def latest(series: Series) -> tuple[dt.date, float] | None:
    if not series:
        return None
    day = max(series)
    return day, series[day]


def workouts_for_day(session: Session, day: dt.date) -> list[Workout]:
    return list(
        session.scalars(
            select(Workout).where(Workout.day == day).order_by(Workout.start)
        )
    )


def has_workout(session: Session, day: dt.date) -> bool:
    return session.scalar(
        select(func.count()).select_from(Workout).where(Workout.day == day)
    ) > 0
