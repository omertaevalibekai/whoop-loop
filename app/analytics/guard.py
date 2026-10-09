from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.analytics.energy import KCAL_PER_KG, compute_state, metabolic_adaptation
from app.analytics.series import get_series
from app.db import setting_or_default

# Recent stretch under evaluation, and the earlier stretch it is compared to.
RECENT_DAYS = 14
REFERENCE_DAYS = 28
REFERENCE_GAP = RECENT_DAYS  # reference window ends where the recent one starts

MIN_POINTS = 5


@dataclass
class GuardSignal:
    label: str
    recent: float | None
    reference: float | None
    delta: float | None
    flagged: bool
    text: str


@dataclass
class GuardReport:
    day: dt.date
    level: str = "green"           # green | yellow | red
    signals: list[GuardSignal] = field(default_factory=list)
    adaptation: str = ""
    recommendation: str = ""
    has_data: bool = True

    def render(self) -> str:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[self.level]
        headline = {
            "green": "Организм держит нагрузку",
            "yellow": "Появились признаки перегиба",
            "red": "Дефицит слишком агрессивный",
        }[self.level]

        lines = [f"{icon} <b>{headline}</b>"]
        if not self.has_data:
            return "\n".join(
                lines
                + [
                    "",
                    "Нужно около 6 недель истории, чтобы сравнивать текущий период "
                    "с прошлым. Пока просто копим данные.",
                ]
            )

        lines.append("")
        lines.append("<i>Последние 2 недели против предыдущего месяца:</i>")
        lines.extend(signal.text for signal in self.signals)

        if self.adaptation:
            lines += ["", self.adaptation]
        if self.recommendation:
            lines += ["", self.recommendation]
        return "\n".join(lines)


def _window_mean(
    series: dict[dt.date, float], end_day: dt.date, days: int, offset: int = 0
) -> tuple[float | None, int]:
    end = end_day - dt.timedelta(days=offset)
    start = end - dt.timedelta(days=days - 1)
    values = [v for d, v in series.items() if start <= d <= end]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)


def _signal(
    session: Session,
    day: dt.date,
    metric: str,
    label: str,
    unit: str,
    worse_when_rising: bool,
    abs_threshold: float,
    digits: int = 0,
) -> GuardSignal | None:
    series = get_series(session, metric, days=RECENT_DAYS + REFERENCE_DAYS + 5, end_day=day)
    recent, n_recent = _window_mean(series, day, RECENT_DAYS)
    reference, n_ref = _window_mean(series, day, REFERENCE_DAYS, offset=REFERENCE_GAP)

    if recent is None or reference is None or n_recent < MIN_POINTS or n_ref < MIN_POINTS:
        return None

    delta = recent - reference
    bad = delta if worse_when_rising else -delta
    flagged = bad >= abs_threshold

    mark = "⚠️" if flagged else "·"
    step = 0.5 * 10 ** -digits  # anything below half a printed unit reads as noise
    if abs(delta) < step:
        change = "без изменений"
    else:
        sign = "+" if delta > 0 else "−"
        change = f"{sign}{abs(delta):.{digits}f}"
    text = (
        f"{mark} {label}: {recent:.{digits}f}{unit} "
        f"(было {reference:.{digits}f}{unit}, {change})"
    )
    return GuardSignal(
        label=label, recent=recent, reference=reference, delta=delta,
        flagged=flagged, text=text,
    )


def check(session: Session, day: dt.date | None = None) -> GuardReport:
    day = day or dt.date.today()
    report = GuardReport(day=day)

    specs = [
        # metric, label, unit, worse_when_rising, threshold, digits
        ("hrv", "HRV", " мс", False, 4.0, 0),
        ("rhr", "Пульс покоя", " уд/мин", True, 3.0, 0),
        ("recovery", "Восстановление", " %", False, 7.0, 0),
        ("sleep_perf", "Качество сна", " %", False, 6.0, 0),
        ("sleep_hours", "Сон", " ч", False, 0.5, 1),
    ]

    for metric, label, unit, rising, threshold, digits in specs:
        signal = _signal(session, day, metric, label, unit, rising, threshold, digits)
        if signal is not None:
            report.signals.append(signal)

    if len(report.signals) < 3:
        report.has_data = False
        return report

    flags = sum(1 for s in report.signals if s.flagged)
    if flags >= 3:
        report.level = "red"
    elif flags == 2:
        report.level = "yellow"

    adapted, _change, adaptation_text = metabolic_adaptation(session, day)
    if adapted:
        report.adaptation = adaptation_text
        if report.level == "green":
            report.level = "yellow"
        elif report.level == "yellow":
            report.level = "red"

    report.recommendation = _recommend(session, day, report.level)
    return report


def _recommend(session: Session, day: dt.date, level: str) -> str:
    if level == "green":
        return ""

    state = compute_state(session, day)
    goal_mode = setting_or_default(session, "goal_mode")

    if goal_mode != "cut":
        return (
            "Ты не в дефиците, значит дело не в еде — ищи причину в нагрузке, "
            "сне или стрессе."
        )

    rate = float(setting_or_default(session, "rate_kg_per_week"))

    if level == "red":
        if state.target_kcal is not None:
            new_target = round(state.target_kcal + 300)
            return (
                f"<b>Что делать:</b> подними цель до ~{new_target} ккал и держи "
                f"неделю. Если через неделю HRV и пульс покоя не вернутся — "
                f"делай полный перерыв на поддержке (~{round(state.tdee_estimate or 0)} "
                f"ккал) на 10–14 дней.\n"
                f"Продолжать резать сейчас — это терять мышцы и сон, а не жир."
            )
        return (
            "<b>Что делать:</b> подними калории примерно на 300 в день и держи "
            "неделю, либо сделай перерыв на поддержке."
        )

    new_rate = max(0.25, round(rate * 0.6, 2))
    extra_kcal = round((rate - new_rate) * KCAL_PER_KG / 7)
    if extra_kcal <= 0:
        return (
            "<b>Что делать:</b> темп уже щадящий, так что дело скорее в нагрузке "
            "или сне. Добавь день отдыха и ложись раньше."
        )
    return (
        f"<b>Что делать:</b> сбавь темп с {rate} до {new_rate} кг/неделю "
        f"(это примерно +{extra_kcal} ккал в день) и посмотри неделю. "
        f"Пока это может быть просто тяжёлая неделя, а не пережатый дефицит."
    )
