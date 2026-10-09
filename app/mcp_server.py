from __future__ import annotations

import datetime as dt
import json
import re

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select, text

from app.analytics import guard as guard_mod
from app.analytics import illness as illness_mod
from app.analytics.digest import build_digest, build_short_status
from app.analytics.energy import compute_state, metabolic_adaptation
from app.analytics.series import LABELS, LOADERS, get_series
from app.db import init_db, session_scope
from app.models import DiaryEntry, Workout
from app.util import today_local

mcp = MCPServer("whoop-loop")

SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace)\b", re.IGNORECASE
)


@mcp.tool()
def whoop_status() -> str:
    """Текущее состояние: восстановление, сон, питание и вес на сегодня."""
    with session_scope() as session:
        return build_short_status(session)


@mcp.tool()
def whoop_digest() -> str:
    """Полная утренняя сводка со всеми выводами и рекомендацией на день."""
    with session_scope() as session:
        return build_digest(session)


@mcp.tool()
def whoop_metric(metric: str, days: int = 30) -> str:
    """Ряд значений одной метрики по дням.

    Доступные метрики: recovery, hrv, rhr, spo2, skin_temp, resp_rate,
    sleep_perf, sleep_eff, sleep_hours, disturbances, strain, burn_kcal,
    weight, intake_kcal, protein_g.
    """
    if metric not in LOADERS:
        return f"Неизвестная метрика. Доступны: {', '.join(sorted(LOADERS))}"
    with session_scope() as session:
        series = get_series(session, metric, days=days)
    payload = {
        "metric": metric,
        "label": LABELS.get(metric, metric),
        "days": days,
        "points": {day.isoformat(): round(value, 2) for day, value in sorted(series.items())},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def whoop_illness_check() -> str:
    """Проверка на раннюю болезнь: отклонение показателей от личной нормы."""
    with session_scope() as session:
        report = illness_mod.check(session)
    return json.dumps(
        {
            "level": report.level,
            "headline": report.headline,
            "primary_flags": report.flags_primary,
            "signals": [
                {
                    "metric": s.metric,
                    "value": s.value,
                    "baseline": s.center,
                    "z": None if s.z is None else round(s.z, 2),
                    "flagged": s.flagged,
                }
                for s in report.signals
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def whoop_deficit_guard() -> str:
    """Стоп-кран дефицита: не пережат ли режим, судя по физиологии."""
    with session_scope() as session:
        report = guard_mod.check(session)
    return json.dumps(
        {
            "level": report.level,
            "signals": [
                {
                    "label": s.label,
                    "recent": None if s.recent is None else round(s.recent, 2),
                    "reference": None if s.reference is None else round(s.reference, 2),
                    "delta": None if s.delta is None else round(s.delta, 2),
                    "flagged": s.flagged,
                }
                for s in report.signals
            ],
            "metabolic_adaptation": report.adaptation,
            "recommendation": report.recommendation,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def whoop_energy() -> str:
    """Энергетический контур: расход, цель, съеденное, вес и коэффициент калибровки."""
    with session_scope() as session:
        state = compute_state(session)
        adapted, change, note = metabolic_adaptation(session)
    return json.dumps(
        {
            "day": state.day.isoformat(),
            "goal_mode": state.goal_mode,
            "rate_kg_per_week": state.rate_kg_per_week,
            "burn_today_kcal": state.burn_today_kcal,
            "burn_reference_kcal": state.burn_reference_kcal,
            "calibration_factor": state.factor,
            "tdee_estimate_kcal": state.tdee_estimate,
            "target_kcal": state.target_kcal,
            "eaten_kcal": state.eaten_kcal,
            "remaining_kcal": state.remaining_kcal,
            "protein_g": state.protein_g,
            "weight_latest_kg": state.weight_latest_kg,
            "weight_trend_kg": state.weight_smoothed_kg,
            "slope_kg_per_week": state.slope_kg_per_week,
            "intake_days_logged": state.intake_days,
            "metabolic_adaptation": {
                "detected": adapted,
                "change": None if change is None else round(change, 3),
                "note": note,
            },
        },
        ensure_ascii=False,
        indent=2,
        default=float,
    )


@mcp.tool()
def whoop_workouts(days: int = 14) -> str:
    """Тренировки за период с нагрузкой, пульсом и затраченной энергией."""
    end = today_local()
    start = end - dt.timedelta(days=days - 1)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(Workout)
                .where(Workout.day >= start, Workout.day <= end)
                .order_by(Workout.start)
            )
        )
    return json.dumps(
        [
            {
                "day": w.day.isoformat(),
                "sport": w.sport_name,
                "strain": w.strain,
                "avg_hr": w.average_heart_rate,
                "max_hr": w.max_heart_rate,
                "kcal": None if w.kilojoule is None else round(w.kilojoule / 4.184),
                "distance_m": w.distance_meter,
            }
            for w in rows
        ],
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def whoop_diary(days: int = 7) -> str:
    """Записи голосового и текстового дневника за период."""
    end = today_local()
    start = end - dt.timedelta(days=days - 1)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DiaryEntry)
                .where(DiaryEntry.day >= start, DiaryEntry.day <= end)
                .order_by(DiaryEntry.created_at)
            )
        )
    return json.dumps(
        [
            {
                "day": r.day.isoformat(),
                "kind": r.kind,
                "text": r.transcript,
                "extracted": r.payload,
            }
            for r in rows
        ],
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def whoop_sql(query: str, limit: int = 200) -> str:
    """Произвольный SELECT по базе — для вопросов, под которые нет готового инструмента.

    Таблицы: cycles, sleeps, recoveries, workouts, weight_log, intake_log,
    diary_entries, day_factors, calibration_weeks, alerts, settings.
    Разрешены только читающие запросы.
    """
    if not SELECT_ONLY.match(query) or FORBIDDEN.search(query):
        return "Разрешены только SELECT-запросы."

    with session_scope() as session:
        result = session.execute(text(query))
        columns = list(result.keys())
        rows = result.fetchmany(limit)

    return json.dumps(
        {
            "columns": columns,
            "rows": [
                [v.isoformat() if hasattr(v, "isoformat") else v for v in row]
                for row in rows
            ],
            "row_count": len(rows),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
