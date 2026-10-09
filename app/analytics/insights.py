from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.series import get_series
from app.config import settings
from app.models import Sleep, Workout
from app.util import today_local

# Whoop's own banding: a day is "green" from 67 and "red" below 34.
GREEN, RED = 67.0, 34.0

# Below this many paired days a correlation is decoration, not evidence.
MIN_PAIRS = 12
# Below this strength it is indistinguishable from noise at our sample sizes.
MIN_R = 0.35


@dataclass
class Finding:
    title: str
    body: str
    strength: float = 0.0   # |r|, used only for ordering


def pearson(xs: list[float], ys: list[float]) -> tuple[float | None, int]:
    n = len(xs)
    if n < 3:
        return None, n
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx == 0 or dy == 0:
        return None, n
    return num / (dx * dy) ** 0.5, n


def _paired(a: dict[dt.date, float], b: dict[dt.date, float],
            lag: int = 0) -> tuple[list[float], list[float]]:
    """Pair two daily series, optionally shifting `a` back by `lag` days."""
    xs, ys = [], []
    for day, value in sorted(b.items()):
        source = day - dt.timedelta(days=lag)
        if source in a:
            xs.append(a[source])
            ys.append(value)
    return xs, ys


def bedtime_series(session: Session, days: int, end_day: dt.date) -> dict[dt.date, float]:
    """Hour of falling asleep, in local time, as a sortable number.

    Past-midnight bedtimes are expressed as 24+ so that "later" always means
    a larger number: 23:30 is 23.5, 01:00 is 25.0.
    """
    start = end_day - dt.timedelta(days=days - 1)
    rows = session.execute(
        select(Sleep.day, Sleep.start)
        .where(Sleep.day >= start, Sleep.day <= end_day, Sleep.nap.is_(False))
    ).all()
    out: dict[dt.date, float] = {}
    for day, moment in rows:
        local = moment.astimezone(settings.tz)
        hour = local.hour + local.minute / 60
        if hour < 12:
            hour += 24
        out[day] = hour
    return out


def _fmt_hour(value: float) -> str:
    value = value % 24
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    if minutes == 60:
        hours, minutes = hours + 1, 0
    return f"{hours % 24:02d}:{minutes:02d}"


def _sleep_need(session: Session, days: int, end_day: dt.date) -> Finding | None:
    """How much sleep actually separates your good days from your bad ones."""
    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    hours = get_series(session, "sleep_hours", days=days, end_day=end_day)

    good = [hours[d] for d in recovery if recovery[d] >= GREEN and d in hours]
    bad = [hours[d] for d in recovery if recovery[d] < RED and d in hours]
    if len(good) < 3 or len(bad) < 3:
        return None

    median_good, median_bad = statistics.median(good), statistics.median(bad)
    gap = median_good - median_bad
    if gap < 0.25:
        return None

    return Finding(
        title="Сколько сна тебе нужно",
        body=(
            f"В зелёные дни ты спишь <b>{median_good:.1f} ч</b>, в красные — "
            f"{median_bad:.1f} ч. Разница {gap:.1f} ч.\n"
            f"Порог, ниже которого день почти всегда выходит тяжёлым: "
            f"около <b>{median_bad + gap / 2:.1f} ч</b>."
        ),
        strength=min(1.0, gap / 2),
    )


def _correlation_finding(
    session: Session, days: int, end_day: dt.date,
    source: dict[dt.date, float], label: str, unit: str,
    lag: int, higher_means: str, lower_means: str,
) -> Finding | None:
    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    xs, ys = _paired(source, recovery, lag=lag)
    r, n = pearson(xs, ys)
    if r is None or n < MIN_PAIRS or abs(r) < MIN_R:
        return None

    direction = higher_means if r < 0 else lower_means
    strength_word = "заметная" if abs(r) >= 0.5 else "умеренная"
    return Finding(
        title=label,
        body=(
            f"{strength_word.capitalize()} связь (r = {r:+.2f}, дней: {n}).\n{direction}"
        ),
        strength=abs(r),
    )


def _walking_effect(session: Session, days: int, end_day: dt.date) -> Finding | None:
    """Whether yesterday's activity helps or costs you the next morning."""
    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    start = end_day - dt.timedelta(days=days)
    rows = session.execute(
        select(Workout.day).where(Workout.day >= start, Workout.day <= end_day)
    ).all()
    active = {d for (d,) in rows}

    after_active = [v for d, v in recovery.items() if (d - dt.timedelta(days=1)) in active]
    after_rest = [v for d, v in recovery.items() if (d - dt.timedelta(days=1)) not in active]
    if len(after_active) < 4 or len(after_rest) < 4:
        return None

    mean_active = sum(after_active) / len(after_active)
    mean_rest = sum(after_rest) / len(after_rest)
    delta = mean_active - mean_rest
    if abs(delta) < 5:
        return None

    if delta > 0:
        body = (
            f"После дней с активностью восстановление <b>выше</b> на "
            f"{delta:.0f} пунктов ({mean_active:.0f}% против {mean_rest:.0f}%). "
            f"Движение тебе не стоит ничего — наоборот."
        )
    else:
        body = (
            f"После дней с активностью восстановление <b>ниже</b> на "
            f"{abs(delta):.0f} пунктов ({mean_active:.0f}% против {mean_rest:.0f}%). "
            f"Нагрузка не успевает окупаться — смотри на объём."
        )
    return Finding(title="Что делает с тобой нагрузка", body=body,
                   strength=min(1.0, abs(delta) / 20))


def collect(session: Session, days: int = 60,
            end_day: dt.date | None = None) -> list[Finding]:
    end_day = end_day or today_local()
    findings: list[Finding] = []

    need = _sleep_need(session, days, end_day)
    if need:
        findings.append(need)

    hours = get_series(session, "sleep_hours", days=days, end_day=end_day)
    found = _correlation_finding(
        session, days, end_day, hours, "Длительность сна", " ч", 0,
        higher_means="Чем меньше спишь, тем хуже утро.",
        lower_means="Чем дольше спишь, тем лучше восстановление.",
    )
    if found:
        findings.append(found)

    bedtime = bedtime_series(session, days, end_day)
    if bedtime:
        med = statistics.median(bedtime.values())
        found = _correlation_finding(
            session, days, end_day, bedtime, "Время отбоя", "", 0,
            higher_means=f"Поздний отбой бьёт по утру. Твоя медиана — {_fmt_hour(med)}.",
            lower_means=f"Ранний отбой окупается. Твоя медиана — {_fmt_hour(med)}.",
        )
        if found:
            findings.append(found)

    strain = get_series(session, "strain", days=days, end_day=end_day)
    found = _correlation_finding(
        session, days, end_day, strain, "Вчерашняя нагрузка", "", 1,
        higher_means="Тяжёлый день заметно съедает следующее утро.",
        lower_means="Чем выше была нагрузка, тем лучше следующее утро — "
                    "похоже, ты недобираешь движения.",
    )
    if found:
        findings.append(found)

    disturbances = get_series(session, "disturbances", days=days, end_day=end_day)
    found = _correlation_finding(
        session, days, end_day, disturbances, "Пробуждения за ночь", "", 0,
        higher_means="Рваный сон стоит дороже, чем кажется.",
        lower_means="Спокойные ночи заметно лучше рваных.",
    )
    if found:
        findings.append(found)

    walking = _walking_effect(session, days, end_day)
    if walking:
        findings.append(walking)

    findings.sort(key=lambda f: f.strength, reverse=True)
    return findings


def render(session: Session, days: int = 60,
           end_day: dt.date | None = None) -> str:
    end_day = end_day or today_local()
    recovery = get_series(session, "recovery", days=days, end_day=end_day)
    n = len(recovery)

    if n < MIN_PAIRS:
        return (
            f"<b>Разбор</b>\n\nПока мало истории: {n} дн. "
            f"Связи между сном и восстановлением видно примерно от {MIN_PAIRS} — "
            f"осталось подождать."
        )

    findings = collect(session, days, end_day)
    lines = [f"<b>Что видно в твоих данных</b>", f"<i>По {n} дням истории</i>", ""]

    if not findings:
        lines.append(
            "Устойчивых связей пока не видно. Это нормально: либо разброс "
            "твоих дней слишком ровный, либо истории ещё мало для выводов."
        )
        return "\n".join(lines)

    for finding in findings:
        lines += [f"<b>{finding.title}</b>", finding.body, ""]

    lines.append(
        "<i>Это корреляции, а не доказанные причины: они показывают, что идёт "
        "вместе, но не что чем вызвано.</i>"
    )
    return "\n".join(lines)
