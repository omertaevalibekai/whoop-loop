from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Token(Base):
    """Single-row table holding the current Whoop OAuth token."""

    __tablename__ = "oauth_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str] = mapped_column(Text, default="")
    whoop_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Cycle(Base):
    """A Whoop physiological cycle: one day of strain and energy burn."""

    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_offset: Mapped[str] = mapped_column(String(16), default="")
    score_state: Mapped[str] = mapped_column(String(32), default="")
    strain: Mapped[float | None] = mapped_column(Float, nullable=True)
    kilojoule: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    @property
    def kcal(self) -> float | None:
        # Whoop reports energy in kilojoules; 1 kcal is 4.184 kJ.
        return None if self.kilojoule is None else self.kilojoule / 4.184


class Sleep(Base):
    __tablename__ = "sleeps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone_offset: Mapped[str] = mapped_column(String(16), default="")
    nap: Mapped[bool] = mapped_column(Boolean, default=False)
    score_state: Mapped[str] = mapped_column(String(32), default="")

    in_bed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awake_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    light_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sws_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rem_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_data_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_cycle_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disturbance_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    need_baseline_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    need_debt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    need_strain_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    need_nap_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    respiratory_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    performance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    consistency_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    efficiency_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    @property
    def asleep_ms(self) -> int | None:
        parts = [self.light_ms, self.sws_ms, self.rem_ms]
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)


class Recovery(Base):
    __tablename__ = "recoveries"

    cycle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    sleep_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score_state: Mapped[str] = mapped_column(String(32), default="")
    user_calibrating: Mapped[bool] = mapped_column(Boolean, default=False)

    recovery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_heart_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_rmssd_milli: Mapped[float | None] = mapped_column(Float, nullable=True)
    spo2_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    skin_temp_celsius: Mapped[float | None] = mapped_column(Float, nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Workout(Base):
    __tablename__ = "workouts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sport_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sport_name: Mapped[str] = mapped_column(String(64), default="")
    score_state: Mapped[str] = mapped_column(String(32), default="")

    strain: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kilojoule: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_meter: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_gain_meter: Mapped[float | None] = mapped_column(Float, nullable=True)
    percent_recorded: Mapped[float | None] = mapped_column(Float, nullable=True)
    zone_ms: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class WeightLog(Base):
    __tablename__ = "weight_log"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    kg: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="telegram")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class IntakeLog(Base):
    __tablename__ = "intake_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    kcal: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="telegram")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DiaryEntry(Base):
    __tablename__ = "diary_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="text")
    transcript: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DayFactors(Base):
    """Aggregated lifestyle factors for one day, merged from diary entries."""

    __tablename__ = "day_factors"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    alcohol_units: Mapped[float] = mapped_column(Float, default=0.0)
    caffeine_mg: Mapped[float] = mapped_column(Float, default=0.0)
    last_caffeine_hhmm: Mapped[str | None] = mapped_column(String(5), nullable=True)
    last_meal_hhmm: Mapped[str | None] = mapped_column(String(5), nullable=True)
    stress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sick: Mapped[bool] = mapped_column(Boolean, default=False)
    travel: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class CalibrationWeek(Base):
    """One weekly pass of the energy-balance feedback loop."""

    __tablename__ = "calibration_weeks"

    week_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    days_counted: Mapped[int] = mapped_column(Integer, default=0)
    mean_intake_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_burn_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_slope_kg_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    tdee_observed: Mapped[float | None] = mapped_column(Float, nullable=True)
    factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_alert_dedupe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
