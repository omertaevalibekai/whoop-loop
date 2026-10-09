from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.analytics.baseline import ewma, mean_of, trend_slope
from app.analytics.series import get_series, has_workout
from app.db import set_setting, setting_or_default
from app.models import CalibrationWeek
from app.util import clamp, today_local, week_start

# Energy density of body mass change. Pure fat is ~9400 kcal/kg; real-world
# loss carries some lean tissue and water, so 7700 is the working figure.
KCAL_PER_KG = 7700.0

# The calibration window. Shorter than this and water weight dominates.
CALIBRATION_DAYS = 14
MIN_INTAKE_DAYS = 10
MIN_WEIGHT_POINTS = 6

# Whoop's burn figure carries a systematic bias; the loop corrects for it, but
# refuses corrections beyond this range, which would mean the inputs are wrong.
FACTOR_MIN, FACTOR_MAX = 0.75, 1.25


@dataclass
class EnergyState:
    day: dt.date
    factor: float
    burn_today_kcal: float | None
    burn_reference_kcal: float | None
    tdee_estimate: float | None
    target_kcal: float | None
    eaten_kcal: float
    protein_g: float
    protein_target_g: float
    remaining_kcal: float | None
    weight_latest_day: dt.date | None
    weight_latest_kg: float | None
    weight_smoothed_kg: float | None
    slope_kg_per_week: float | None
    intake_days: int
    calibrated: bool
    goal_mode: str
    rate_kg_per_week: float


def daily_deficit_kcal(goal_mode: str, rate_kg_per_week: float) -> float:
    """Positive means eat below expenditure."""
    magnitude = abs(rate_kg_per_week) * KCAL_PER_KG / 7.0
    if goal_mode == "cut":
        return magnitude
    if goal_mode == "gain":
        return -magnitude
    return 0.0


def weight_state(
    session: Session, day: dt.date, window: int = CALIBRATION_DAYS
) -> tuple[dt.date | None, float | None, float | None, float | None]:
    """Return (latest day, latest kg, smoothed kg, slope in kg/week)."""
    series = get_series(session, "weight", days=90, end_day=day)
    if not series:
        return None, None, None, None

    latest_day = max(series)
    smoothed = ewma(series)
    slope_per_day = trend_slope(series, days=window)
    slope_per_week = None if slope_per_day is None else slope_per_day * 7
    return latest_day, series[latest_day], smoothed.get(latest_day), slope_per_week


def compute_state(session: Session, day: dt.date | None = None) -> EnergyState:
    day = day or today_local()

    goal_mode = setting_or_default(session, "goal_mode")
    rate = float(setting_or_default(session, "rate_kg_per_week"))
    protein_target = float(setting_or_default(session, "protein_target_g"))
    floor = float(setting_or_default(session, "min_kcal_floor"))
    factor = float(setting_or_default(session, "calibration_factor"))

    burn = get_series(session, "burn_kcal", days=30, end_day=day)
    burn_today = burn.get(day)
    # Today's cycle is still open, so plan against the recent average instead.
    burn_reference = mean_of(burn, days=7, end_day=day) or burn_today

    intake = get_series(session, "intake_kcal", days=CALIBRATION_DAYS, end_day=day)
    protein = get_series(session, "protein_g", days=CALIBRATION_DAYS, end_day=day)
    eaten = intake.get(day, 0.0)
    protein_today = protein.get(day, 0.0)

    tdee = None if burn_reference is None else burn_reference * factor
    target = None
    if tdee is not None:
        target = max(floor, tdee - daily_deficit_kcal(goal_mode, rate))

    latest_day, latest_kg, smoothed_kg, slope_week = weight_state(session, day)

    calibration = session.get(CalibrationWeek, week_start(day))

    return EnergyState(
        day=day,
        factor=factor,
        burn_today_kcal=burn_today,
        burn_reference_kcal=burn_reference,
        tdee_estimate=tdee,
        target_kcal=target,
        eaten_kcal=eaten,
        protein_g=protein_today,
        protein_target_g=protein_target,
        remaining_kcal=None if target is None else target - eaten,
        weight_latest_day=latest_day,
        weight_latest_kg=latest_kg,
        weight_smoothed_kg=smoothed_kg,
        slope_kg_per_week=slope_week,
        intake_days=len(intake),
        calibrated=calibration is not None or factor != 1.0,
        goal_mode=goal_mode,
        rate_kg_per_week=rate,
    )


def calibrate(session: Session, day: dt.date | None = None) -> CalibrationWeek | None:
    """Close the loop: compare predicted vs. actual weight change and adjust.

    This is what makes rough calorie logging good enough. Individual estimates
    can be off; as long as the error is roughly consistent, the factor absorbs it.
    """
    day = day or today_local()

    intake = get_series(session, "intake_kcal", days=CALIBRATION_DAYS, end_day=day)
    burn = get_series(session, "burn_kcal", days=CALIBRATION_DAYS, end_day=day)
    weight = get_series(session, "weight", days=CALIBRATION_DAYS, end_day=day)

    if len(intake) < MIN_INTAKE_DAYS or len(weight) < MIN_WEIGHT_POINTS or not burn:
        return None

    # Only days with both numbers can speak to the balance.
    shared = sorted(set(intake) & set(burn))
    if len(shared) < MIN_INTAKE_DAYS:
        return None

    mean_intake = sum(intake[d] for d in shared) / len(shared)
    mean_burn = sum(burn[d] for d in shared) / len(shared)
    slope_per_day = trend_slope(weight, days=CALIBRATION_DAYS)
    if slope_per_day is None or mean_burn <= 0:
        return None

    # Energy balance: intake - TDEE = weight change in kcal.
    tdee_observed = mean_intake - slope_per_day * KCAL_PER_KG
    raw_factor = tdee_observed / mean_burn
    factor = clamp(raw_factor, FACTOR_MIN, FACTOR_MAX)

    goal_mode = setting_or_default(session, "goal_mode")
    rate = float(setting_or_default(session, "rate_kg_per_week"))
    floor = float(setting_or_default(session, "min_kcal_floor"))
    target = max(floor, mean_burn * factor - daily_deficit_kcal(goal_mode, rate))

    note = ""
    if raw_factor != factor:
        note = (
            f"Расчётный коэффициент {raw_factor:.2f} вышел за допустимые границы "
            f"и обрезан до {factor:.2f}. Обычно это значит, что учёт еды или "
            f"взвешивания неполные."
        )

    start = week_start(day)
    row = session.get(CalibrationWeek, start)
    if row is None:
        row = CalibrationWeek(week_start=start)
        session.add(row)
    row.days_counted = len(shared)
    row.mean_intake_kcal = mean_intake
    row.mean_burn_kcal = mean_burn
    row.weight_slope_kg_per_day = slope_per_day
    row.tdee_observed = tdee_observed
    row.factor = factor
    row.target_kcal = target
    row.note = note

    set_setting(session, "calibration_factor", round(factor, 4))
    return row


def metabolic_adaptation(
    session: Session, day: dt.date | None = None
) -> tuple[bool, float | None, str]:
    """Detect expenditure falling on comparable (rest) days.

    Whoop's burn on days without a workout is the cleanest read on whether the
    body has quietly turned the thermostat down.
    """
    day = day or today_local()
    burn = get_series(session, "burn_kcal", days=42, end_day=day)
    if len(burn) < 24:
        return False, None, ""

    recent_cut = day - dt.timedelta(days=13)
    recent, earlier = [], []
    for d, value in burn.items():
        if has_workout(session, d):
            continue
        (recent if d >= recent_cut else earlier).append(value)

    if len(recent) < 4 or len(earlier) < 6:
        return False, None, ""

    mean_recent = sum(recent) / len(recent)
    mean_earlier = sum(earlier) / len(earlier)
    if mean_earlier <= 0:
        return False, None, ""

    change = (mean_recent - mean_earlier) / mean_earlier
    if change > -0.05:
        return False, change, ""

    message = (
        f"Расход в дни без тренировок упал на {abs(change) * 100:.0f}% "
        f"({mean_earlier:.0f} → {mean_recent:.0f} ккал). Это метаболическая "
        f"адаптация: тело экономит. Резать калории дальше — худшее, что можно "
        f"сделать; неделя на поддержке вернёт расход."
    )
    return True, change, message
