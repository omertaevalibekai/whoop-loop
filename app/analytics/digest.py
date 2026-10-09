from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.analytics import guard, illness
from app.analytics.baseline import build_baseline
from app.analytics.energy import compute_state
from app.analytics.series import get_series, workouts_for_day
from app.util import fmt_num, ms_to_hm, remaining_text, today_local

WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def human_date(day: dt.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}, {WEEKDAYS[day.weekday()]}"


def recovery_bucket(score: float | None) -> tuple[str, str]:
    """Whoop's own banding: green from 67, yellow from 34, red below."""
    if score is None:
        return "⚪", "нет данных"
    if score >= 67:
        return "🟢", "зелёная зона"
    if score >= 34:
        return "🟡", "жёлтая зона"
    return "🔴", "красная зона"


def trains_hard(session: Session, day: dt.date, days: int = 30) -> bool:
    """Whether this person actually does structured training.

    Advice about "heavy sessions" is noise for someone whose whole activity
    log is walking, so the wording follows what the data says they do.
    """
    from sqlalchemy import select

    from app.models import Workout

    start = day - dt.timedelta(days=days)
    rows = session.execute(
        select(Workout.sport_name, Workout.strain)
        .where(Workout.day >= start, Workout.day <= day)
    ).all()
    casual = {"walking", "activity", "ходьба", "активность", ""}
    serious = 0
    for name, strain in rows:
        if (strain or 0) >= 12 or (name or "").strip().lower() not in casual:
            serious += 1
    # One stray session in a month is not a training habit; three is.
    return serious >= 3


def training_advice(
    session: Session, day: dt.date, score: float | None,
    guard_level: str, sick: bool,
) -> str:
    if sick:
        return "<b>Сегодня:</b> никаких нагрузок. Организм уже занят — не мешай ему."
    if score is None:
        return "<b>Сегодня:</b> данных по восстановлению нет, ориентируйся по себе."
    if guard_level == "red":
        return (
            "<b>Сегодня:</b> только лёгкое движение — прогулка или растяжка. "
            "Показатели говорят, что ты в минусе не первую неделю."
        )

    athlete = trains_hard(session, day)

    if score >= 67:
        if athlete:
            return (
                "<b>Сегодня:</b> можно тяжёлую тренировку и самую сложную работу — "
                "тело готово. Такие дни лучше не тратить на рутину."
            )
        return (
            "<b>Сегодня:</b> тело готово — это день под самую сложную работу. "
            "Если хочешь добавить нагрузки, сегодня она обойдётся дёшево: "
            "длинная прогулка или что-то интенсивнее обычного."
        )
    if score >= 34:
        if athlete:
            return (
                "<b>Сегодня:</b> средняя нагрузка — зона 2, техника, объём без "
                "интенсивности. Для работы день нормальный, но без героизма."
            )
        return (
            "<b>Сегодня:</b> обычный день. Прогулка в удовольствие, работа без "
            "героизма, и постарайся лечь раньше обычного."
        )
    if athlete:
        return (
            "<b>Сегодня:</b> восстановление. Прогулка, растяжка, ранний отбой. "
            "Тяжёлая тренировка сегодня заберёт больше, чем даст."
        )
    return (
        "<b>Сегодня:</b> разгрузка. Ничего сверх обычного, сложные задачи — "
        "на завтра, отбой пораньше. Сегодня всё даётся дороже, чем обычно."
    )


def has_today_recovery(session: Session, day: dt.date) -> bool:
    """True once Whoop has scored the night that belongs to `day`."""
    return day in get_series(session, "recovery", days=2, end_day=day)


def waiting_block(session: Session, day: dt.date) -> list[str]:
    """What to say when Whoop has not scored today's night yet."""
    lines = [
        "⏳ <b>Whoop ещё не прислал восстановление за сегодня</b>",
        "",
        "Оценка сна и восстановления появляется только после того, как ты "
        "подтвердишь сон в приложении Whoop. Открой его — и данные приедут "
        "в течение пары минут.",
    ]
    previous = get_series(session, "recovery", days=4, end_day=day - dt.timedelta(days=1))
    if previous:
        last_day = max(previous)
        lines += [
            "",
            f"<i>Последнее известное: {previous[last_day]:.0f}% за "
            f"{last_day.strftime('%d.%m')}. На сегодня это не переносится.</i>",
        ]
    return lines


def _recovery_block(session: Session, day: dt.date) -> list[str]:
    recovery = get_series(session, "recovery", days=3, end_day=day)
    hrv = get_series(session, "hrv", days=3, end_day=day)
    rhr = get_series(session, "rhr", days=3, end_day=day)

    # Strictly today's numbers. Falling back to the freshest available value
    # used to present yesterday's recovery as if it were this morning's.
    score = recovery.get(day)
    if score is None:
        return waiting_block(session, day)

    icon, zone = recovery_bucket(score)
    lines = [f"{icon} <b>Восстановление: {fmt_num(score)}%</b> — {zone}"]

    hrv_value = hrv.get(day)
    rhr_value = rhr.get(day)

    def _with_norm(metric: str, value: float, template: str) -> str:
        # Only quote a deviation once the baseline rests on enough days;
        # a "norm" from a handful of readings is noise wearing a number.
        base = build_baseline(session, metric, day)
        if not base.usable:
            return template.format(value=value, suffix="")
        delta = base.deviation(value)
        if delta is None or abs(delta) < 0.5:
            return template.format(value=value, suffix="")
        sign = "+" if delta > 0 else "−"
        return template.format(value=value, suffix=f" ({sign}{abs(delta):.0f} к норме)")

    details = []
    if hrv_value is not None:
        details.append(_with_norm("hrv", hrv_value, "HRV {value:.0f} мс{suffix}"))
    if rhr_value is not None:
        details.append(_with_norm("rhr", rhr_value, "пульс покоя {value:.0f}{suffix}"))
    if details:
        lines.append("   " + ", ".join(details))

    baseline_days = len(get_series(session, "recovery", days=28, end_day=day))
    if baseline_days < 12:
        lines.append(
            f"   <i>История: {baseline_days} дн. Личная норма считается "
            f"примерно от 12 — до этого сравнивать не с чем.</i>"
        )
    return lines


def _sleep_block(session: Session, day: dt.date) -> list[str]:
    from sqlalchemy import select

    from app.models import Sleep

    row = session.scalar(
        select(Sleep)
        .where(Sleep.day == day, Sleep.nap.is_(False))
        .order_by(Sleep.start.desc())
        .limit(1)
    )
    if row is None:
        return ["😴 <b>Сон:</b> данных за прошлую ночь пока нет"]

    asleep = ms_to_hm(row.asleep_ms)
    performance = fmt_num(row.performance_pct)
    lines = [f"😴 <b>Сон: {asleep}</b> — качество {performance}%"]

    parts = []
    if row.sws_ms is not None:
        parts.append(f"глубокий {ms_to_hm(row.sws_ms)}")
    if row.rem_ms is not None:
        parts.append(f"быстрый {ms_to_hm(row.rem_ms)}")
    if row.disturbance_count is not None:
        parts.append(f"пробуждений {row.disturbance_count}")
    if parts:
        lines.append("   " + ", ".join(parts))

    if row.need_debt_ms and row.need_debt_ms > 30 * 60 * 1000:
        debt = get_series(session, "sleep_debt", days=8, end_day=day)
        trend = ""
        if len(debt) >= 4:
            week_ago = [v for d, v in debt.items() if d <= day - dt.timedelta(days=4)]
            if week_ago:
                change = debt[day] - (sum(week_ago) / len(week_ago)) if day in debt else None
                if change is not None and abs(change) >= 0.3:
                    arrow = "растёт" if change > 0 else "сокращается"
                    trend = f", {arrow} ({change:+.1f} ч за неделю)"
        lines.append(f"   долг сна: {ms_to_hm(row.need_debt_ms)}{trend}")
    return lines


def _energy_block(session: Session, day: dt.date) -> list[str]:
    state = compute_state(session, day)
    lines: list[str] = []

    if state.target_kcal is None:
        lines.append("🍽 <b>Питание:</b> ещё нет данных по расходу от Whoop")
        return lines

    goal_label = {"cut": "снижение", "maintain": "поддержка", "gain": "набор"}.get(
        state.goal_mode, state.goal_mode
    )
    lines.append(
        f"🍽 <b>Цель на сегодня: {fmt_num(state.target_kcal)} ккал</b> "
        f"({goal_label})"
    )

    detail = f"   расход ≈ {fmt_num(state.tdee_estimate)} ккал"
    if state.factor != 1.0:
        detail += f", коэффициент {state.factor:.2f}"
    else:
        detail += ", коэффициент ещё не откалиброван"
    lines.append(detail)

    if state.eaten_kcal:
        lines.append(
            f"   съедено {fmt_num(state.eaten_kcal)}, {remaining_text(state.remaining_kcal)}"
            f" · белок {fmt_num(state.protein_g)}/{fmt_num(state.protein_target_g)} г"
        )

    if state.weight_smoothed_kg is not None:
        trend = ""
        if state.slope_kg_per_week is not None:
            arrow = "↓" if state.slope_kg_per_week < 0 else (
                "↑" if state.slope_kg_per_week > 0 else "→"
            )
            trend = f", {arrow} {abs(state.slope_kg_per_week):.2f} кг/нед"
        lines.append(
            f"⚖️ <b>Вес:</b> {state.weight_latest_kg:.1f} кг "
            f"(сглаженный тренд {state.weight_smoothed_kg:.1f}{trend})"
        )
    else:
        lines.append("⚖️ <b>Вес:</b> нет замеров — пришли число, например 82.4")

    return lines


def build_digest(session: Session, day: dt.date | None = None) -> str:
    day = day or today_local()

    illness_report = illness.check(session, day)
    guard_report = guard.check(session, day)
    score = get_series(session, "recovery", days=2, end_day=day).get(day)

    lines = [f"<b>Доброе утро. {human_date(day)}</b>", ""]
    lines += _recovery_block(session, day)
    lines.append("")
    lines += _sleep_block(session, day)
    lines.append("")
    lines += _energy_block(session, day)

    if illness_report.level != "green":
        lines += ["", illness_report.render()]

    if guard_report.level != "green":
        icon = {"yellow": "🟡", "red": "🔴"}[guard_report.level]
        lines += ["", f"{icon} <b>Стоп-кран дефицита</b>"]
        flagged = [s.text for s in guard_report.signals if s.flagged]
        lines += flagged[:3]
        if guard_report.recommendation:
            lines.append(guard_report.recommendation)

    lines += [
        "",
        training_advice(
            session, day, score, guard_report.level,
            illness_report.level == "alert",
        ),
    ]

    workouts = workouts_for_day(session, day - dt.timedelta(days=1))
    if workouts:
        names = ", ".join(f"{w.sport_name} ({w.strain:.1f})" if w.strain else w.sport_name
                          for w in workouts)
        lines += ["", f"Вчера: {names}"]

    return "\n".join(lines)


def build_short_status(session: Session, day: dt.date | None = None) -> str:
    day = day or today_local()
    lines = [f"<b>{human_date(day)}</b>", ""]
    lines += _recovery_block(session, day)
    lines += _sleep_block(session, day)
    lines += _energy_block(session, day)
    return "\n".join(lines)
