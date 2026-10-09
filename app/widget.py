"""Snapshot for the iPhone home-screen widget.

The server takes no inbound connections, so the widget cannot ask it for
anything. Instead the bot pushes a small JSON file to a secret GitHub Gist and
the Scriptable widget reads the gist's raw URL. Only what the widget draws goes
out — no diary, no tokens, no history beyond a week of recovery.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import illness
from app.analytics.digest import recovery_bucket
from app.analytics.energy import compute_state
from app.analytics.series import get_series
from app.config import settings
from app.db import get_setting, session_scope, set_setting
from app.models import Sleep
from app.util import now_local, today_local

log = logging.getLogger(__name__)

GIST_API = "https://api.github.com/gists"
FILE_NAME = "whoop.json"

ZONES = {"🟢": "green", "🟡": "yellow", "🔴": "red", "⚪": "none"}


def _round(value: float | None, digits: int = 0) -> float | int | None:
    if value is None:
        return None
    return round(value) if digits == 0 else round(value, digits)


SLEEP_GOAL_HOURS = 7.0
GREEN_FROM = 67


def streak(series: dict[dt.date, float], day: dt.date, ok) -> int:
    """Consecutive days ending at `day` whose value passes `ok`.

    Today often has no score yet (the night is unconfirmed until late
    morning), so a missing today does not break the run, it just isn't
    counted. Any other missing day does break it.
    """
    cursor = day if day in series else day - dt.timedelta(days=1)
    count = 0
    while cursor in series and ok(series[cursor]):
        count += 1
        cursor -= dt.timedelta(days=1)
    return count


def build_snapshot(session: Session, day: dt.date | None = None) -> dict:
    day = day or today_local()

    recovery = get_series(session, "recovery", days=8, end_day=day)
    hrv = get_series(session, "hrv", days=8, end_day=day)
    rhr = get_series(session, "rhr", days=8, end_day=day)
    strain = get_series(session, "strain", days=2, end_day=day)

    # Until the night is confirmed in the Whoop app there is no score for
    # today. Show the last known one, but say which day it belongs to so the
    # widget can mark it as old instead of passing it off as this morning's.
    recovery_day = max(recovery) if recovery else None
    score = recovery.get(recovery_day) if recovery_day else None

    sleep = session.scalar(
        select(Sleep)
        .where(Sleep.day <= day, Sleep.nap.is_(False))
        .order_by(Sleep.day.desc(), Sleep.start.desc())
        .limit(1)
    )
    energy = compute_state(session, day)
    illness_report = illness.check(session, day)
    sleep_hours = get_series(session, "sleep_hours", days=120, end_day=day)
    recovery_long = get_series(session, "recovery", days=120, end_day=day)

    week = [
        _round(recovery.get(day - dt.timedelta(days=offset)))
        for offset in range(6, -1, -1)
    ]

    return {
        "day": day.isoformat(),
        "recovery": {
            "score": _round(score),
            "day": recovery_day.isoformat() if recovery_day else None,
            "fresh": recovery_day == day,
            "zone": ZONES[recovery_bucket(score)[0]],
            "hrv": _round(hrv.get(recovery_day)) if recovery_day else None,
            "rhr": _round(rhr.get(recovery_day)) if recovery_day else None,
            "week": week,
        },
        "sleep": None if sleep is None else {
            "day": sleep.day.isoformat(),
            "hours": _round((sleep.asleep_ms or 0) / 3_600_000, 1) or None,
            "performance": _round(sleep.performance_pct),
            "debt_hours": _round((sleep.need_debt_ms or 0) / 3_600_000, 1),
            "week": [
                _round(sleep_hours.get(day - dt.timedelta(days=offset)), 1)
                for offset in range(6, -1, -1)
            ],
        },
        "strain": _round(strain.get(day), 1),
        "energy": {
            "target_kcal": _round(energy.target_kcal),
            "eaten_kcal": _round(energy.eaten_kcal),
            "remaining_kcal": _round(energy.remaining_kcal),
        },
        "weight": {
            "kg": _round(energy.weight_latest_kg, 1),
            "trend_kg": _round(energy.weight_smoothed_kg, 1),
            "slope_kg_week": _round(energy.slope_kg_per_week, 2),
        },
        "illness": illness_report.level,
        # What the widget shows in its alarm mode: the headline and which
        # metrics are off, so "why" is readable without opening the bot.
        "alarm": None if illness_report.level == "green" else {
            "headline": illness_report.headline,
            "signals": [s.label for s in illness_report.signals if s.flagged],
        },
        "streaks": {
            "sleep_goal_hours": SLEEP_GOAL_HOURS,
            "sleep": streak(sleep_hours, day, lambda v: v >= SLEEP_GOAL_HOURS),
            "green": streak(recovery_long, day, lambda v: v >= GREEN_FROM),
        },
    }


def _fingerprint(snapshot: dict) -> str:
    return hashlib.sha1(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.widget_github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gist_id() -> str | None:
    if settings.widget_gist_id:
        return settings.widget_gist_id
    with session_scope() as session:
        return get_setting(session, "widget_gist_id")


def raw_url(gist: str, owner: str) -> str:
    # Without a commit hash the raw link always points at the latest revision.
    return f"https://gist.githubusercontent.com/{owner}/{gist}/raw/{FILE_NAME}"


def create_gist(content: str) -> tuple[str, str]:
    """Create the secret gist and return (id, raw url)."""
    response = httpx.post(
        GIST_API,
        headers=_headers(),
        json={
            "description": "whoop-loop widget",
            "public": False,
            "files": {FILE_NAME: {"content": content}},
        },
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    with session_scope() as session:
        set_setting(session, "widget_gist_id", body["id"])
        set_setting(session, "widget_gist_owner", body["owner"]["login"])
    return body["id"], raw_url(body["id"], body["owner"]["login"])


def publish(force: bool = False) -> bool:
    """Push a fresh snapshot if anything changed. Returns True if it went out."""
    if not settings.widget_github_token:
        return False
    gist = gist_id()
    if not gist:
        log.info("Виджет: gist ещё не создан, запусти python cli.py widget setup")
        return False

    with session_scope() as session:
        snapshot = build_snapshot(session)
        fingerprint = _fingerprint(snapshot)
        if not force and get_setting(session, "widget_fingerprint") == fingerprint:
            return False

    # Stamped after the comparison, so a new time alone never causes a push.
    snapshot["updated"] = now_local().isoformat(timespec="minutes")
    response = httpx.patch(
        f"{GIST_API}/{gist}",
        headers=_headers(),
        json={"files": {FILE_NAME: {"content": json.dumps(snapshot, ensure_ascii=False, indent=1)}}},
        timeout=20,
    )
    response.raise_for_status()

    with session_scope() as session:
        set_setting(session, "widget_fingerprint", fingerprint)
    return True


# ---------------------------------------------------------------- scripts
# The widget scripts in the repository carry placeholders, not addresses:
# the gist URL is a key to the owner's health data and the bot name invites
# strangers. Real values are filled in only in the copies uploaded to the gist.

WIDGET_DIR = Path(__file__).resolve().parent.parent / "widget"
CORE_FILE = "whoop-core.js"
LOADER_FILE = "whoop-widget.js"


def gist_owner(gist: str) -> str:
    with session_scope() as session:
        owner = get_setting(session, "widget_gist_owner")
    if owner:
        return owner
    response = httpx.get(f"{GIST_API}/{gist}", headers=_headers(), timeout=20)
    response.raise_for_status()
    owner = response.json()["owner"]["login"]
    with session_scope() as session:
        set_setting(session, "widget_gist_owner", owner)
    return owner


def bot_url() -> str:
    """tg:// link to this bot, asked from Telegram by token; empty if unknown."""
    if not settings.telegram_bot_token:
        return ""
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe", timeout=10
        )
        username = response.json().get("result", {}).get("username")
    except Exception:
        return ""
    return f"tg://resolve?domain={username}" if username else ""


def render_scripts(gist: str, owner: str, bot: str) -> dict[str, str]:
    base = f"https://gist.githubusercontent.com/{owner}/{gist}/raw"
    values = {
        "__DATA_URL__": f"{base}/{FILE_NAME}",
        "__CORE_URL__": f"{base}/{CORE_FILE}",
        "__BOT_URL__": bot,
    }
    out = {}
    for name in (CORE_FILE, LOADER_FILE):
        text = (WIDGET_DIR / name).read_text(encoding="utf-8")
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        out[name] = text
    return out


def publish_scripts() -> str:
    """Upload the filled-in widget scripts; returns the loader's raw URL."""
    gist = gist_id()
    if not gist:
        raise RuntimeError("gist ещё не создан: python cli.py widget setup")
    owner = gist_owner(gist)
    files = render_scripts(gist, owner, bot_url())
    response = httpx.patch(
        f"{GIST_API}/{gist}",
        headers=_headers(),
        json={"files": {name: {"content": text} for name, text in files.items()}},
        timeout=30,
    )
    response.raise_for_status()
    return f"https://gist.githubusercontent.com/{owner}/{gist}/raw/{LOADER_FILE}"
