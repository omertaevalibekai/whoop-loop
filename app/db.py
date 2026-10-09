from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, Setting

_ROOT = Path(__file__).resolve().parent.parent


def _resolved_url() -> str:
    """Turn a relative sqlite path into an absolute one rooted at the project."""
    url = settings.db_url
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix) :]
        if not os.path.isabs(raw):
            target = _ROOT / raw
            target.parent.mkdir(parents=True, exist_ok=True)
            return prefix + str(target).replace("\\", "/")
    return url


engine = create_engine(
    _resolved_url(),
    future=True,
    connect_args={"check_same_thread": False} if settings.db_url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --- key/value settings -------------------------------------------------


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    if row is None or row.value == "":
        return default
    try:
        return json.loads(row.value)
    except json.JSONDecodeError:
        return row.value


def set_setting(session: Session, key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False)
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=encoded))
    else:
        row.value = encoded


def all_settings(session: Session) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in session.scalars(select(Setting)):
        try:
            out[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            out[row.key] = row.value
    return out


# Defaults used by the energy loop until the user overrides them.
DEFAULTS: dict[str, Any] = {
    "goal_mode": "cut",              # cut | maintain | gain
    "rate_kg_per_week": 0.5,          # desired rate of change
    "protein_target_g": 140,
    "height_m": None,
    "calibration_factor": 1.0,        # Whoop burn -> real TDEE multiplier
    "min_kcal_floor": 1500,           # never recommend below this
}


def setting_or_default(session: Session, key: str) -> Any:
    return get_setting(session, key, DEFAULTS.get(key))
