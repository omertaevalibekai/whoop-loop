from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.analytics.energy import compute_state
from app.diary.extract import DiaryExtraction, to_payload
from app.models import DayFactors, DiaryEntry, IntakeLog, WeightLog
from app.util import fmt_num, remaining_text, today_local


def _factors(session: Session, day: dt.date) -> DayFactors:
    row = session.get(DayFactors, day)
    if row is None:
        row = DayFactors(day=day)
        session.add(row)
        session.flush()
    return row


def log_weight(session: Session, kg: float, day: dt.date | None = None,
               source: str = "telegram") -> None:
    day = day or today_local()
    row = session.get(WeightLog, day)
    if row is None:
        session.add(WeightLog(day=day, kg=kg, source=source))
    else:
        row.kg = kg
        row.source = source


def log_intake(session: Session, kcal: float, protein_g: float | None = None,
               label: str = "", day: dt.date | None = None,
               source: str = "telegram") -> None:
    day = day or today_local()
    session.add(
        IntakeLog(day=day, kcal=kcal, protein_g=protein_g, label=label, source=source)
    )


def ingest(
    session: Session,
    extraction: DiaryExtraction,
    raw_text: str,
    kind: str = "text",
    day: dt.date | None = None,
) -> str:
    """Persist one diary note and return a human confirmation for the chat."""
    day = day or today_local()
    recorded: list[str] = []

    session.add(
        DiaryEntry(
            day=day,
            kind=kind,
            transcript=raw_text,
            payload=to_payload(extraction),
        )
    )

    if extraction.weight_kg is not None:
        log_weight(session, extraction.weight_kg, day)
        recorded.append(f"вес {extraction.weight_kg:.1f} кг")

    total_kcal = 0.0
    total_protein = 0.0
    for meal in extraction.meals:
        log_intake(session, meal.kcal, meal.protein_g, meal.label, day)
        total_kcal += meal.kcal
        total_protein += meal.protein_g or 0
    if total_kcal:
        recorded.append(f"{fmt_num(total_kcal)} ккал")
    if total_protein:
        recorded.append(f"{fmt_num(total_protein)} г белка")

    needs_factors = any(
        value is not None
        for value in (
            extraction.alcohol_units,
            extraction.caffeine_mg,
            extraction.last_caffeine_hhmm,
            extraction.last_meal_hhmm,
            extraction.stress,
            extraction.mood,
            extraction.sick,
            extraction.travel,
        )
    ) or bool(extraction.notes)

    if needs_factors:
        factors = _factors(session, day)
        if extraction.alcohol_units:
            factors.alcohol_units = (factors.alcohol_units or 0) + extraction.alcohol_units
            recorded.append(f"алкоголь {factors.alcohol_units:g} порц.")
        if extraction.caffeine_mg:
            factors.caffeine_mg = (factors.caffeine_mg or 0) + extraction.caffeine_mg
            recorded.append(f"кофеин {factors.caffeine_mg:.0f} мг")
        if extraction.last_caffeine_hhmm:
            factors.last_caffeine_hhmm = extraction.last_caffeine_hhmm
        if extraction.last_meal_hhmm:
            factors.last_meal_hhmm = extraction.last_meal_hhmm
        if extraction.stress is not None:
            factors.stress = extraction.stress
            recorded.append(f"стресс {extraction.stress}/10")
        if extraction.mood is not None:
            factors.mood = extraction.mood
        if extraction.sick is not None:
            factors.sick = bool(extraction.sick)
            if extraction.sick:
                recorded.append("отметка о недомогании")
        if extraction.travel is not None:
            factors.travel = bool(extraction.travel)
        if extraction.notes:
            existing = factors.notes or ""
            factors.notes = (existing + "\n" + extraction.notes).strip()

    session.flush()

    if not recorded:
        return "Записал заметку, но извлекать из неё было нечего."

    lines = ["✅ Записал: " + ", ".join(recorded)]

    state = compute_state(session, day)
    if state.target_kcal is not None and state.eaten_kcal:
        lines.append(
            f"Съедено сегодня {fmt_num(state.eaten_kcal)} из {fmt_num(state.target_kcal)} ккал, "
            f"{remaining_text(state.remaining_kcal)}."
        )
        if state.protein_g:
            lines.append(
                f"Белок {fmt_num(state.protein_g)}/{fmt_num(state.protein_target_g)} г."
            )
    return "\n".join(lines)
