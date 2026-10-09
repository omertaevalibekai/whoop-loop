"""Was the alert real? Labels from the owner and the detector's own track record.

Synthetic history can check the logic of the illness detector, not whether it
is right about a real person. Only the owner knows that, so after every
illness alert the bot asks, and the answers become the first honest numbers:
how many alerts were real, how many had another cause, how many illnesses
went unnoticed.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, HealthEvent

SICK, OTHER, FINE, VACCINE = "sick", "other_cause", "fine", "vaccine"
LABELS = {SICK: "🤒 Заболел", OTHER: "😐 Была другая причина", FINE: "🙂 Всё нормально"}

ASK_WITHIN_DAYS = 3        # an answer about last week's night is a guess
# An alert up to this many days before getting sick counts as catching it:
# the studies report signals a median of ~3 days before symptoms.
CATCH_WINDOW_DAYS = 4


def record(session: Session, day: dt.date, kind: str, source: str = "self") -> None:
    """Store a label. One verdict per alert day: a new answer replaces the old."""
    if kind in LABELS:
        for row in session.scalars(
            select(HealthEvent).where(HealthEvent.day == day, HealthEvent.kind.in_(LABELS))
        ):
            session.delete(row)
        session.flush()
    existing = session.scalar(
        select(HealthEvent).where(HealthEvent.day == day, HealthEvent.kind == kind)
    )
    if existing is None:
        session.add(HealthEvent(day=day, kind=kind, source=source))


def _alert_days(session: Session, since: dt.date) -> dict[dt.date, str]:
    """Illness alerts by day, keeping the strongest level of the day."""
    rank = {"watch": 1, "alert": 2}
    days: dict[dt.date, str] = {}
    for alert in session.scalars(
        select(Alert).where(Alert.kind == "illness", Alert.day >= since)
    ):
        if alert.level in rank and rank[alert.level] > rank.get(days.get(alert.day, ""), 0):
            days[alert.day] = alert.level
    return days


def _labels(session: Session, since: dt.date) -> dict[dt.date, str]:
    return {
        e.day: e.kind
        for e in session.scalars(
            select(HealthEvent).where(HealthEvent.day >= since, HealthEvent.kind.in_(LABELS))
        )
    }


def pending_question(session: Session, today: dt.date) -> tuple[dt.date, str] | None:
    """The most recent unanswered alert worth asking about, if any."""
    since = today - dt.timedelta(days=ASK_WITHIN_DAYS)
    labels = _labels(session, since)
    alerts = _alert_days(session, since)
    for day in sorted(alerts, reverse=True):
        if day < today and day not in labels:
            return day, alerts[day]
    return None


def recent_vaccine(session: Session, night: dt.date) -> bool:
    """A dose 1–2 nights before: resting HR peaks then ([ALAVI] in illness.py)."""
    window = [night - dt.timedelta(days=i) for i in (0, 1, 2)]
    return session.scalar(
        select(HealthEvent.id).where(HealthEvent.kind == VACCINE, HealthEvent.day.in_(window))
    ) is not None


@dataclass
class TrackRecord:
    alerts: int = 0          # alert days (watch or alert)
    confirmed: int = 0       # owner said: got sick
    other_cause: int = 0     # owner said: alcohol, stress, travel…
    fine: int = 0            # owner said: nothing happened
    unanswered: int = 0
    illnesses: int = 0       # episodes the owner reported as sick
    caught: int = 0          # of those, preceded by an alert

    @property
    def answered(self) -> int:
        return self.confirmed + self.other_cause + self.fine

    def render(self, days: int) -> str:
        lines = [f"<b>Точность детектора болезни за {days} дн.</b>", ""]
        if not self.alerts and not self.illnesses:
            lines.append(
                "Пока нечего считать: не было ни тревог, ни отмеченных болезней. "
                "Если заболеешь — напиши /sick, это тоже данные."
            )
            return "\n".join(lines)
        lines.append(f"Тревог и наблюдений: {self.alerts}")
        if self.answered:
            share = round(100 * self.confirmed / self.answered)
            lines += [
                f"  🤒 оказались болезнью: {self.confirmed}",
                f"  😐 была другая причина: {self.other_cause}",
                f"  🙂 ничего не было: {self.fine}",
                f"  → верных: <b>{share}%</b> из отвеченных",
            ]
        if self.unanswered:
            lines.append(f"  без ответа: {self.unanswered}")
        if self.illnesses:
            lines += [
                "",
                f"Болезней отмечено: {self.illnesses}, из них бот заметил заранее: "
                f"<b>{self.caught}</b>",
            ]
        lines += [
            "",
            "<i>Это твоя личная статистика, а не клиническая валидация. "
            "Чем больше ответов, тем ей больше веры.</i>",
        ]
        return "\n".join(lines)


def track_record(session: Session, today: dt.date, days: int = 180) -> TrackRecord:
    since = today - dt.timedelta(days=days)
    alerts = _alert_days(session, since)
    labels = _labels(session, since)
    record_ = TrackRecord(alerts=len(alerts))
    for day in alerts:
        label = labels.get(day)
        if label == SICK:
            record_.confirmed += 1
        elif label == OTHER:
            record_.other_cause += 1
        elif label == FINE:
            record_.fine += 1
        else:
            record_.unanswered += 1

    # An illness is a run of consecutive "sick" days; it counts as caught when
    # an alert came in the days before its first day, or on it.
    sick_days = sorted(d for d, k in labels.items() if k == SICK)
    starts = [d for i, d in enumerate(sick_days)
              if i == 0 or (d - sick_days[i - 1]).days > 1]
    record_.illnesses = len(starts)
    for start in starts:
        window = {start - dt.timedelta(days=i) for i in range(CATCH_WINDOW_DAYS + 1)}
        if window & set(alerts):
            record_.caught += 1
    return record_
