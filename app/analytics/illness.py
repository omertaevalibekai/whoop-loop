from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.analytics.baseline import build_baseline
from app.analytics.series import LABELS, get_series
from app.util import today_local

# How far from the personal norm counts as a flag.
Z_FLAG = 1.5

# Signals that move first and most reliably when an infection is brewing.
PRIMARY = ("rhr", "skin_temp", "resp_rate", "spo2")
SUPPORTING = ("hrv", "recovery")

# +1 means a rise is the worrying direction, -1 means a fall is.
DIRECTION = {
    "rhr": 1,
    "skin_temp": 1,
    "resp_rate": 1,
    "spo2": -1,
    "hrv": -1,
    "recovery": -1,
}

UNITS = {
    "rhr": " уд/мин",
    "skin_temp": " °C",
    "resp_rate": "",
    "spo2": " %",
    "hrv": " мс",
    "recovery": " %",
}


@dataclass
class Signal:
    metric: str
    value: float | None
    center: float | None
    z: float | None
    flagged: bool
    primary: bool

    @property
    def label(self) -> str:
        # LABELS carry their unit after a comma; the unit is printed separately.
        return LABELS.get(self.metric, self.metric).split(",")[0]

    def describe(self) -> str:
        if self.value is None:
            return f"{self.label}: нет данных"
        unit = UNITS.get(self.metric, "")
        digits = 1 if self.metric in ("skin_temp", "resp_rate") else 0
        value_text = f"{self.value:.{digits}f}{unit}"
        if self.center is None:
            return f"{self.label}: {value_text}"
        delta = self.value - self.center
        sign = "+" if delta > 0 else "−"
        mark = "⚠️" if self.flagged else "·"
        return (
            f"{mark} {self.label}: {value_text} "
            f"(норма {self.center:.{digits}f}, {sign}{abs(delta):.{digits}f})"
        )


@dataclass
class IllnessReport:
    day: dt.date
    signals: list[Signal] = field(default_factory=list)
    level: str = "green"        # green | watch | alert
    flags_primary: int = 0
    has_data: bool = True
    no_today_data: bool = False

    @property
    def headline(self) -> str:
        if self.no_today_data:
            return "Данных за сегодня ещё нет"
        if not self.has_data:
            return "Недостаточно данных для базовой линии"
        if self.level == "alert":
            return "Похоже, ты заболеваешь"
        if self.level == "watch":
            return "Организм ведёт себя необычно"
        return "Показатели в норме"

    def render(self) -> str:
        icon = {"green": "🟢", "watch": "🟡", "alert": "🔴"}[self.level]
        if self.no_today_data:
            icon = "⏳"  # green would read as "all clear", which is not the case
        lines = [f"{icon} <b>{self.headline}</b>"]
        if self.no_today_data:
            lines.append(
                "Whoop оценит ночь после того, как ты подтвердишь сон в приложении. "
                "Открой его — и проверка сработает."
            )
            return "\n".join(lines)
        if not self.has_data:
            lines.append(
                "Нужно минимум 10 дней истории, чтобы понять, что для тебя норма."
            )
            return "\n".join(lines)

        lines.append("")
        lines.extend(signal.describe() for signal in self.signals)

        if self.level == "alert":
            lines += [
                "",
                "Три и более показателя вне нормы одновременно — обычно так "
                "выглядит начало болезни за сутки-двое до симптомов.",
                "Сегодня: без тяжёлых тренировок, больше воды и сна, "
                "по возможности разгрузи календарь.",
            ]
        elif self.level == "watch":
            lines += [
                "",
                "Два показателя вне нормы. Пока это может быть стресс, алкоголь "
                "или поздний отбой — но стоит присмотреться.",
            ]
        return "\n".join(lines)


def check(session: Session, day: dt.date | None = None) -> IllnessReport:
    day = day or today_local()
    report = IllnessReport(day=day)

    usable_baselines = 0
    for metric in PRIMARY + SUPPORTING:
        # Strictly today's reading. Comparing an older day against the baseline
        # and calling the result "today" is how a stale alarm gets raised.
        series = get_series(session, metric, days=2, end_day=day)
        value = series.get(day)

        baseline = build_baseline(session, metric, day)
        if baseline.usable:
            usable_baselines += 1
        z = baseline.z(value)

        flagged = False
        if z is not None:
            flagged = z * DIRECTION[metric] >= Z_FLAG

        signal = Signal(
            metric=metric,
            value=value,
            center=baseline.center,
            z=z,
            flagged=flagged,
            primary=metric in PRIMARY,
        )
        report.signals.append(signal)

    report.flags_primary = sum(1 for s in report.signals if s.primary and s.flagged)
    measured_today = sum(1 for s in report.signals if s.primary and s.value is not None)

    # Two ways to have nothing to say: no personal norm yet, or no reading yet.
    report.has_data = usable_baselines >= 3 and measured_today >= 3
    report.no_today_data = measured_today < 3

    if not report.has_data:
        report.level = "green"
    elif report.flags_primary >= 3:
        report.level = "alert"
    elif report.flags_primary == 2:
        report.level = "watch"
    else:
        report.level = "green"
    return report
