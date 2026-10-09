from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db import session_scope, set_setting
from app.models import Cycle, Recovery, Sleep, Workout
from app.util import local_day, parse_iso
from app.whoop.client import WhoopClient
from app.whoop.constants import sport_name

log = logging.getLogger(__name__)


def _score(record: dict) -> dict:
    value = record.get("score")
    return value if isinstance(value, dict) else {}


def upsert_cycle(session: Session, record: dict) -> None:
    start = parse_iso(record.get("start"))
    if start is None:
        return
    score = _score(record)
    row = session.get(Cycle, record["id"])
    if row is None:
        row = Cycle(id=record["id"], start=start, day=local_day(start))
        session.add(row)
    row.start = start
    row.day = local_day(start)
    row.end = parse_iso(record.get("end"))
    row.timezone_offset = record.get("timezone_offset") or ""
    row.score_state = record.get("score_state") or ""
    row.strain = score.get("strain")
    row.kilojoule = score.get("kilojoule")
    row.average_heart_rate = score.get("average_heart_rate")
    row.max_heart_rate = score.get("max_heart_rate")


def upsert_sleep(session: Session, record: dict) -> None:
    sleep_id = str(record.get("id"))
    start = parse_iso(record.get("start"))
    end = parse_iso(record.get("end"))
    if start is None:
        return
    score = _score(record)
    stages = score.get("stage_summary") or {}
    needed = score.get("sleep_needed") or {}

    row = session.get(Sleep, sleep_id)
    if row is None:
        row = Sleep(id=sleep_id, start=start, day=local_day(end or start))
        session.add(row)
    # A night is filed under the day you wake up on.
    row.day = local_day(end or start)
    row.start = start
    row.end = end
    row.timezone_offset = record.get("timezone_offset") or ""
    row.nap = bool(record.get("nap"))
    row.score_state = record.get("score_state") or ""

    row.in_bed_ms = stages.get("total_in_bed_time_milli")
    row.awake_ms = stages.get("total_awake_time_milli")
    row.light_ms = stages.get("total_light_sleep_time_milli")
    row.sws_ms = stages.get("total_slow_wave_sleep_time_milli")
    row.rem_ms = stages.get("total_rem_sleep_time_milli")
    row.no_data_ms = stages.get("total_no_data_time_milli")
    row.sleep_cycle_count = stages.get("sleep_cycle_count")
    row.disturbance_count = stages.get("disturbance_count")

    row.need_baseline_ms = needed.get("baseline_milli")
    row.need_debt_ms = needed.get("need_from_sleep_debt_milli")
    row.need_strain_ms = needed.get("need_from_recent_strain_milli")
    row.need_nap_ms = needed.get("need_from_recent_nap_milli")

    row.respiratory_rate = score.get("respiratory_rate")
    row.performance_pct = score.get("sleep_performance_percentage")
    row.consistency_pct = score.get("sleep_consistency_percentage")
    row.efficiency_pct = score.get("sleep_efficiency_percentage")


def upsert_recovery(session: Session, record: dict) -> None:
    cycle_id = record.get("cycle_id")
    if cycle_id is None:
        return
    score = _score(record)

    cycle = session.get(Cycle, cycle_id)
    day = cycle.day if cycle is not None else local_day(parse_iso(record.get("created_at")))
    if day is None:
        return

    row = session.get(Recovery, cycle_id)
    if row is None:
        row = Recovery(cycle_id=cycle_id, day=day)
        session.add(row)
    row.day = day
    row.sleep_id = record.get("sleep_id")
    row.score_state = record.get("score_state") or ""
    row.user_calibrating = bool(score.get("user_calibrating"))
    row.recovery_score = score.get("recovery_score")
    row.resting_heart_rate = score.get("resting_heart_rate")
    row.hrv_rmssd_milli = score.get("hrv_rmssd_milli")
    row.spo2_percentage = score.get("spo2_percentage")
    row.skin_temp_celsius = score.get("skin_temp_celsius")


def upsert_workout(session: Session, record: dict) -> None:
    workout_id = str(record.get("id"))
    start = parse_iso(record.get("start"))
    if start is None:
        return
    score = _score(record)
    sport_id = record.get("sport_id")

    row = session.get(Workout, workout_id)
    if row is None:
        row = Workout(id=workout_id, start=start, day=local_day(start))
        session.add(row)
    row.day = local_day(start)
    row.start = start
    row.end = parse_iso(record.get("end"))
    row.sport_id = sport_id
    row.sport_name = record.get("sport_name") or sport_name(sport_id)
    row.score_state = record.get("score_state") or ""
    row.strain = score.get("strain")
    row.average_heart_rate = score.get("average_heart_rate")
    row.max_heart_rate = score.get("max_heart_rate")
    row.kilojoule = score.get("kilojoule")
    row.distance_meter = score.get("distance_meter")
    row.altitude_gain_meter = score.get("altitude_gain_meter")
    row.percent_recorded = score.get("percent_recorded")
    row.zone_ms = score.get("zone_duration")


def sync_range(
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    include_profile: bool = False,
) -> dict[str, int]:
    """Pull every collection for a time range and upsert it. Returns per-kind counts."""
    counts = {"cycles": 0, "sleeps": 0, "recoveries": 0, "workouts": 0}

    with WhoopClient() as client, session_scope() as session:
        # Cycles first: recoveries are filed against a cycle's day.
        for record in client.cycles(start, end):
            upsert_cycle(session, record)
            counts["cycles"] += 1
        session.flush()

        for record in client.sleeps(start, end):
            upsert_sleep(session, record)
            counts["sleeps"] += 1

        for record in client.recoveries(start, end):
            upsert_recovery(session, record)
            counts["recoveries"] += 1

        for record in client.workouts(start, end):
            upsert_workout(session, record)
            counts["workouts"] += 1

        if include_profile:
            try:
                profile = client.profile()
                set_setting(session, "whoop_profile", profile)
            except Exception as exc:  # profile is optional, never fail the sync on it
                log.warning("Не удалось получить профиль: %s", exc)
            try:
                body = client.body_measurement()
                set_setting(session, "whoop_body", body)
            except Exception as exc:
                log.warning("Не удалось получить измерения тела: %s", exc)

    log.info("Синхронизация завершена: %s", counts)
    return counts


def backfill(days: int = 365) -> dict[str, int]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    return sync_range(start, end, include_profile=True)


def sync_recent(days: int = 7) -> dict[str, int]:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=days)
    return sync_range(start, end)


def verify_endpoints() -> list[tuple[str, str]]:
    """Ping every endpoint once and report status. Used by `cli.py verify`."""
    results: list[tuple[str, str]] = []
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=7)

    with WhoopClient() as client:
        checks: list[tuple[str, Any]] = [
            ("cycle", lambda: next(iter(client.cycles(start, end, max_records=1)), None)),
            ("sleep", lambda: next(iter(client.sleeps(start, end, max_records=1)), None)),
            ("recovery", lambda: next(iter(client.recoveries(start, end, max_records=1)), None)),
            ("workout", lambda: next(iter(client.workouts(start, end, max_records=1)), None)),
            ("profile", client.profile),
            ("body_measurement", client.body_measurement),
        ]
        for name, call in checks:
            try:
                value = call()
                results.append((name, "OK" if value else "OK (пусто за 7 дней)"))
            except Exception as exc:
                results.append((name, f"ОШИБКА: {exc}"))
    return results
