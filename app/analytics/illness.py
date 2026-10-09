"""Early illness warning, built on published wearable studies.

Each rule cites the study it comes from. Where a study gives no threshold
and one had to be chosen, the comment says so plainly.

Sources
-------
[ALAVI]  Alavi A. et al. Real-time alerting system for COVID-19 and other
         stress events using wearable data. Nature Medicine 28, 175–184 (2022).
         doi:10.1038/s41591-021-01593-2. NightSignal: overnight resting heart
         rate vs. a personal median; red when it is >= 4 bpm above it for two
         consecutive nights, yellow at 3 bpm. 80% of COVID-19 cases alerted at
         or before symptom onset, median 3 days early; specificity 87.7%.
         Single-night elevations did not alert. Alcohol, stress, intense
         exercise, travel and vaccination also raised alerts.
[MILLER] Miller D.J. et al. Analyzing changes in respiratory rate to predict
         the risk of COVID-19 infection. PLOS ONE 15(12): e0243693 (2020).
         WHOOP data. Respiratory rate baseline: median and SD over the 14
         nights from 21 to 7 nights back; features built on the mean of the
         last two nights. Healthy within-person SD is only 0.51 breaths/min.
[MASON]  Mason A.E. et al. Detection of COVID-19 using multimodal data from
         a wearable device: results from the first TemPredict Study.
         Scientific Reports 12, 3463 (2022). Skin temperature z-scored against
         a 21-day personal baseline; adding temperature raised ROC AUC from
         0.770 to 0.819; HRV was the stream whose removal hurt most.
[NATAR]  Natarajan A. et al. Assessment of physiological signs associated with
         COVID-19 measured using wearable devices. npj Digital Medicine 3, 156
         (2020). Respiration rate and heart rate rise with illness, HRV falls;
         combining signals predicted illness on a given day with AUC 0.77.
[HIRTEN] Hirten R.P. et al. Use of physiological data from a wearable device
         to identify SARS-CoV-2 infection. J Med Internet Res 23(2): e26107
         (2021). HRV changed in the 7 days before diagnosis.
[PIET]   Pietilä J. et al. Acute effect of alcohol intake on cardiovascular
         autonomic regulation during the first hours of sleep. JMIR Mental
         Health 5(1): e23 (2018). n=4098. Sleeping heart rate +1.4 / +4.0 /
         +8.7 bpm and RMSSD −2.0 / −5.7 / −12.9 ms after low / moderate / high
         alcohol doses — a moderate dose alone reaches the [ALAVI] red line.

What is our own choice, not a study's: the 2.0 SD line for respiratory rate
and skin temperature, −1.5 SD for HRV, and how the signals are combined.
The studies show the signals move and that combining them helps; they do not
publish a rule for this exact combination on WHOOP data.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.baseline import build_baseline
from app.analytics.series import LABELS, get_series
from app.util import today_local

# --- [ALAVI] resting heart rate, in bpm above the personal median ----------
RHR_YELLOW_BPM = 3.0
RHR_RED_BPM = 4.0
RHR_RED_NIGHTS = 2

# --- [MILLER] respiratory rate baseline window: nights 21..7 back ----------
RR_WINDOW, RR_EXCLUDE = 14, 7
RR_SD_FLOOR = 0.3          # below the 0.51 typical SD; keeps z finite
RR_Z = 2.0                 # our choice: ~1 breath/min at the typical SD

# --- [MASON] skin temperature baseline: 21 days ----------------------------
TEMP_WINDOW, TEMP_EXCLUDE = 21, 2
TEMP_SD_FLOOR = 0.1        # °C; sensor resolution is ~0.07 °C
TEMP_Z = 2.0               # our choice

# --- [HIRTEN]/[MASON] HRV: supporting signal, robust 28-day baseline -------
HRV_Z = -1.5               # our choice; HRV falls from sleep, alcohol, training too

MIN_POINTS = 10            # a "norm" from fewer nights is noise

# Confounders named by [ALAVI] and quantified by [PIET].
ALCOHOL_UNITS = 1.0
HARD_STRAIN = 16.0         # WHOOP strain; "all out" days sit above this

SOURCES_SHORT = "Alavi 2022 (Nature Medicine), Miller 2020 (PLOS ONE), Mason 2022 (Sci Rep)"

PRIMARY = ("rhr", "resp_rate", "skin_temp")
SUPPORTING = ("hrv",)
# +1: a rise is the worrying direction; −1: a fall is. Used by the chart too.
DIRECTION = {"rhr": 1, "resp_rate": 1, "skin_temp": 1, "hrv": -1}

UNITS = {"rhr": " уд/мин", "skin_temp": " °C", "resp_rate": "", "hrv": " мс"}


@dataclass
class Signal:
    metric: str
    value: float | None          # tonight
    center: float | None         # personal norm
    z: float | None              # SD units (None for the bpm-based RHR rule)
    flagged: bool
    primary: bool
    score: float | None = None   # share of the alert threshold reached; 1.0 = at the line
    detail: str = ""

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
        tail = f"; {self.detail}" if self.detail else ""
        return (
            f"{mark} {self.label}: {value_text} "
            f"(норма {self.center:.{digits}f}, {sign}{abs(delta):.{digits}f}{tail})"
        )


@dataclass
class IllnessReport:
    day: dt.date
    signals: list[Signal] = field(default_factory=list)
    level: str = "green"        # green | watch | alert
    flags_primary: int = 0
    has_data: bool = True
    no_today_data: bool = False
    reasons: list[str] = field(default_factory=list)
    confounders: list[str] = field(default_factory=list)

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
                f"Нужно минимум {MIN_POINTS} ночей истории, чтобы понять, что для тебя норма."
            )
            return "\n".join(lines)

        lines.append("")
        lines.extend(signal.describe() for signal in self.signals)

        if self.reasons:
            lines += ["", "<b>Почему:</b> " + "; ".join(self.reasons) + "."]
        if self.confounders:
            lines += [
                "",
                "Накануне было: " + ", ".join(self.confounders) + ". Это само по себе "
                "поднимает пульс во сне и снижает HRV, поэтому вывод осторожнее.",
            ]
        if self.level == "alert":
            lines += [
                "",
                "Так в исследованиях носимых устройств выглядит начало инфекции — "
                "в среднем за 2–3 дня до симптомов. Сегодня: без тяжёлых тренировок, "
                "больше воды и сна, по возможности разгрузи календарь.",
            ]
        elif self.level == "watch":
            lines += [
                "",
                "Один сигнал или сигнал с понятной причиной. Стоит присмотреться и "
                "лечь пораньше; если завтра картина повторится — будет тревога.",
            ]
        if self.level != "green":
            lines += ["", f"<i>Правила: {SOURCES_SHORT}. Это не диагноз.</i>"]
        return "\n".join(lines)


# ---------------------------------------------------------------- signals

def _two_night_mean(series: dict, day: dt.date) -> float | None:
    """[MILLER] builds on the mean of the last two nights: one odd night is diluted."""
    today = series.get(day)
    if today is None:
        return None
    previous = series.get(day - dt.timedelta(days=1))
    return today if previous is None else (today + previous) / 2


def _sd_baseline(
    session: Session, metric: str, day: dt.date, window: int, exclude: int,
    floor: float, center_fn,
) -> tuple[float | None, float | None]:
    end = day - dt.timedelta(days=exclude)
    values = list(get_series(session, metric, days=window, end_day=end).values())
    if len(values) < MIN_POINTS:
        return None, None
    return center_fn(values), max(statistics.pstdev(values), floor)


def _rhr_signal(session: Session, day: dt.date) -> tuple[Signal, bool]:
    """[ALAVI] NightSignal on WHOOP's resting heart rate (measured during sleep)."""
    series = get_series(session, "rhr", days=3, end_day=day)
    base = build_baseline(session, "rhr", day)
    value = series.get(day)
    center = base.center if base.n >= MIN_POINTS else None
    if value is None or center is None:
        return Signal("rhr", value, center, None, False, True), False

    nights = [series.get(day - dt.timedelta(days=i)) for i in range(RHR_RED_NIGHTS)]
    above = [n is not None and n - center >= RHR_RED_BPM for n in nights]
    red = all(above)
    yellow = value - center >= RHR_YELLOW_BPM
    detail = ""
    if red:
        detail = f"{RHR_RED_NIGHTS} ночи подряд выше нормы на {RHR_RED_BPM:.0f}+"
    elif above[0]:
        detail = "одна ночь — пока не тревога"
    signal = Signal(
        "rhr", value, center, None, red, True,
        score=(value - center) / RHR_RED_BPM, detail=detail,
    )
    return signal, yellow


def _sd_signal(
    session: Session, metric: str, day: dt.date, window: int, exclude: int,
    floor: float, center_fn, threshold: float, primary: bool,
) -> Signal:
    series = get_series(session, metric, days=3, end_day=day)
    value = series.get(day)
    recent = _two_night_mean(series, day)
    center, sd = _sd_baseline(session, metric, day, window, exclude, floor, center_fn)
    if recent is None or center is None:
        return Signal(metric, value, center, None, False, primary)
    z = (recent - center) / sd
    flagged = z <= threshold if threshold < 0 else z >= threshold
    return Signal(
        metric, value, center, z, flagged, primary,
        score=z / threshold, detail=f"{abs(z):.1f}σ за 2 ночи" if flagged else "",
    )


def _hrv_signal(session: Session, day: dt.date) -> Signal:
    series = get_series(session, "hrv", days=3, end_day=day)
    value = series.get(day)
    recent = _two_night_mean(series, day)
    base = build_baseline(session, "hrv", day)
    z = base.z(recent)
    if z is None:
        return Signal("hrv", value, base.center, None, False, False)
    flagged = z <= HRV_Z
    return Signal(
        "hrv", value, base.center, z, flagged, False,
        score=z / HRV_Z, detail=f"{abs(z):.1f}σ ниже за 2 ночи" if flagged else "",
    )


def _confounders(session: Session, day: dt.date) -> list[str]:
    """What happened the evening before this night ([ALAVI], [PIET])."""
    from app.models import DayFactors, Workout

    evening = day - dt.timedelta(days=1)
    found: list[str] = []
    factors = session.get(DayFactors, evening)
    if factors is not None:
        if (factors.alcohol_units or 0) >= ALCOHOL_UNITS:
            found.append(f"алкоголь ({factors.alcohol_units:g} ед.)")
        if factors.travel:
            found.append("поездка")
    strains = session.scalars(
        select(Workout.strain).where(Workout.day == evening, Workout.strain.is_not(None))
    ).all()
    if strains and max(strains) >= HARD_STRAIN:
        found.append(f"очень тяжёлая тренировка (strain {max(strains):.1f})")
    return found


# --------------------------------------------------------------- decision

def decide(
    rhr: Signal, rhr_yellow: bool, rr: Signal, temp: Signal, hrv: Signal,
    confounders: list[str],
) -> tuple[str, list[str]]:
    """Combine the signals. Pure function: the rule lives here and is tested.

    Single signals are noisy ([ALAVI]: 87.7% specificity alone, alerts also
    from stress, alcohol, travel), and combining streams adds accuracy
    ([NATAR], [MASON]). So an alert needs two *independent* lines of evidence.

    HRV does not count as independent of resting heart rate: both read the
    same autonomic state and move together — alcohol shifts both at once
    ([PIET]), and so does stress. Pairing them produced every false alert on
    synthetic healthy history. Breathing and skin temperature are separate
    channels, so only they can confirm a raised heart rate; HRV can only
    back up one of them into a watch.
    """
    independent = [s for s in (rr, temp) if s.flagged]
    reasons: list[str] = []
    if rhr.flagged:
        reasons.append(f"пульс покоя выше нормы на {RHR_RED_BPM:.0f}+ уд {RHR_RED_NIGHTS} ночи подряд")
    reasons += [f"{s.label.lower()} вне нормы" for s in (rr, temp, hrv) if s.flagged]

    if rhr.flagged and independent:
        level = "alert"
    elif rr.flagged and temp.flagged:
        level = "alert"
    elif rhr.flagged:
        level = "watch"
    elif (rr.flagged or temp.flagged) and (hrv.flagged or rhr_yellow):
        level = "watch"
        if rhr_yellow and not rhr.flagged:
            reasons.append(f"пульс покоя выше нормы на {RHR_YELLOW_BPM:.0f}+ уд")
    else:
        return "green", []

    # A known cause the night before ([PIET]: moderate alcohol alone is +4 bpm)
    # turns an alert into a watch instead of crying illness.
    if confounders and level == "alert":
        level = "watch"
    return level, reasons


def check(session: Session, day: dt.date | None = None) -> IllnessReport:
    day = day or today_local()
    report = IllnessReport(day=day)

    rhr, rhr_yellow = _rhr_signal(session, day)
    rr = _sd_signal(session, "resp_rate", day, RR_WINDOW, RR_EXCLUDE, RR_SD_FLOOR,
                    statistics.median, RR_Z, True)
    temp = _sd_signal(session, "skin_temp", day, TEMP_WINDOW, TEMP_EXCLUDE, TEMP_SD_FLOOR,
                      statistics.mean, TEMP_Z, True)
    hrv = _hrv_signal(session, day)
    report.signals = [rhr, rr, temp, hrv]
    report.flags_primary = sum(1 for s in report.signals if s.primary and s.flagged)

    # Strictly tonight's reading: comparing an older night against the norm and
    # calling it "today" is how a stale alarm gets raised.
    report.no_today_data = rhr.value is None
    measured = sum(1 for s in report.signals if s.value is not None and s.center is not None)
    report.has_data = rhr.center is not None and measured >= 2
    if report.no_today_data or not report.has_data:
        report.level = "green"
        return report

    report.confounders = _confounders(session, day)
    report.level, report.reasons = decide(rhr, rhr_yellow, rr, temp, hrv, report.confounders)
    if report.level == "green":
        report.confounders = []
    return report
