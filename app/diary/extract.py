from __future__ import annotations

import datetime as dt
import json
import logging
import re

from pydantic import BaseModel, Field

from app.config import settings

log = logging.getLogger(__name__)


class Meal(BaseModel):
    label: str = Field(description="Что съедено, коротко и по-русски")
    kcal: int = Field(description="Оценка калорийности порции")
    protein_g: int | None = Field(default=None, description="Белок в граммах, если оценим")
    time_hhmm: str | None = Field(default=None, description="Время приёма пищи, ЧЧ:ММ")


class DiaryExtraction(BaseModel):
    """Everything the diary can pull out of one spoken or typed note."""

    weight_kg: float | None = Field(default=None, description="Вес, если назван")
    meals: list[Meal] = Field(default_factory=list)
    alcohol_units: float | None = Field(
        default=None,
        description="Стандартные порции алкоголя: бокал вина, банка пива или 40 мл крепкого — одна порция",
    )
    caffeine_mg: float | None = Field(
        default=None, description="Кофеин в мг: чашка кофе ~90, эспрессо ~65, чай ~40"
    )
    last_caffeine_hhmm: str | None = None
    last_meal_hhmm: str | None = None
    stress: int | None = Field(default=None, description="Уровень стресса 1-10, если упомянут")
    mood: int | None = Field(default=None, description="Настроение 1-10, если упомянуто")
    sick: bool | None = Field(default=None, description="Жалобы на самочувствие или болезнь")
    travel: bool | None = Field(default=None, description="Перелёт, поездка, смена часового пояса")
    notes: str = Field(default="", description="Всё остальное, что стоит сохранить")
    summary: str = Field(default="", description="Одна строка подтверждения для пользователя")


SYSTEM = """Ты разбираешь короткие дневниковые заметки о дне: еда, вес, алкоголь, кофе, стресс, самочувствие.

Правила:
- Заполняй только те поля, которые реально следуют из текста. Ничего не выдумывай.
- Калории и белок оценивай по типичным порциям, если человек не назвал числа. Лучше разумная оценка, чем пропуск.
- Вес бери только если он назван явно как вес тела.
- Время приводи к формату ЧЧ:ММ. "Утром" — 09:00, "днём" — 13:00, "вечером" — 20:00.
- summary — одна короткая строка на русском о том, что записано. Без приветствий."""


def _fallback(text: str) -> DiaryExtraction:
    """Regex-only parsing so the diary still works without an API key."""
    result = DiaryExtraction(notes=text)

    weight = re.search(r"\b(\d{2,3}[.,]\d)\s*(?:кг|kg)?\b", text)
    if weight:
        candidate = float(weight.group(1).replace(",", "."))
        if 30 <= candidate <= 250:
            result.weight_kg = candidate

    kcal = re.findall(r"\b(\d{2,4})\s*(?:ккал|kcal|калор)", text, flags=re.IGNORECASE)
    if kcal:
        total = sum(int(value) for value in kcal)
        result.meals = [Meal(label="Запись вручную", kcal=total)]

    parts = []
    if result.weight_kg:
        parts.append(f"вес {result.weight_kg} кг")
    if result.meals:
        parts.append(f"{result.meals[0].kcal} ккал")
    result.summary = ("Записал: " + ", ".join(parts)) if parts else "Записал заметку"
    return result


def extract(text: str, now: dt.datetime | None = None) -> DiaryExtraction:
    """Structure a free-form note. Falls back to regex if Claude is unavailable."""
    text = (text or "").strip()
    if not text:
        return DiaryExtraction(summary="Пустая заметка")

    if not settings.anthropic_api_key:
        return _fallback(text)

    now = now or dt.datetime.now(settings.tz)
    context = f"Сейчас {now.strftime('%Y-%m-%d %H:%M')} ({settings.tz_name})."

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.parse(
            model=settings.anthropic_model,
            max_tokens=4000,
            system=SYSTEM,
            messages=[{"role": "user", "content": f"{context}\n\nЗаметка: {text}"}],
            output_format=DiaryExtraction,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise ValueError("Модель не вернула структурированный результат")
        return parsed
    except Exception as exc:
        log.warning("Структурный разбор через Claude не удался (%s), откат к regex", exc)
        return _fallback(text)


def to_payload(extraction: DiaryExtraction) -> dict:
    return json.loads(extraction.model_dump_json())
