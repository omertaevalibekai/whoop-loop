"""The illness rule, case by case, plus the full detector on generated history."""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from app.analytics.illness import Signal, decide


def sig(metric: str, flagged: bool, primary: bool = True) -> Signal:
    return Signal(metric, 1.0, 1.0, None, flagged, primary)


def run(rhr=False, yellow=False, rr=False, temp=False, hrv=False, confounders=()):
    return decide(
        sig("rhr", rhr), yellow, sig("resp_rate", rr), sig("skin_temp", temp),
        sig("hrv", hrv, primary=False), list(confounders),
    )


# --- decision rule ---------------------------------------------------------

def test_quiet_night_is_green():
    assert run() == ("green", [])


def test_resting_hr_alone_is_only_a_watch():
    # [ALAVI]: a single stream also fires on stress, alcohol, travel.
    level, reasons = run(rhr=True)
    assert level == "watch"
    assert "2 ночи подряд" in reasons[0]


@pytest.mark.parametrize("other", ["rr", "temp"])
def test_resting_hr_plus_an_independent_sign_is_an_alert(other):
    assert run(rhr=True, **{other: True})[0] == "alert"


def test_resting_hr_plus_low_hrv_is_not_an_alert():
    # Both read the same autonomic state; alcohol or stress moves them together.
    assert run(rhr=True, hrv=True)[0] == "watch"


def test_breathing_and_temperature_together_alert_without_heart_rate():
    assert run(rr=True, temp=True)[0] == "alert"


def test_one_secondary_sign_alone_stays_green():
    assert run(rr=True)[0] == "green"
    assert run(temp=True)[0] == "green"
    assert run(hrv=True)[0] == "green"


def test_breathing_with_low_hrv_is_a_watch():
    assert run(rr=True, hrv=True)[0] == "watch"


def test_breathing_with_a_single_high_heart_rate_night_is_a_watch():
    level, reasons = run(rr=True, yellow=True)
    assert level == "watch"
    assert any("3+" in r for r in reasons)


def test_alcohol_the_night_before_turns_an_alert_into_a_watch():
    # [PIET]: a moderate dose alone raises sleeping HR by ~4 bpm.
    assert run(rhr=True, temp=True, confounders=["алкоголь (3 ед.)"])[0] == "watch"


def test_confounder_does_not_hide_a_watch_or_invent_one():
    assert run(rhr=True, confounders=["поездка"])[0] == "watch"
    assert run(confounders=["поездка"])[0] == "green"


# --- whole detector on generated history ------------------------------------

def _fresh_db(monkeypatch, tmp_path):
    from app import config
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setattr(config.settings, "db_url", url, raising=False)
    import app.db as db
    if hasattr(db, "reset_engine"):
        db.reset_engine()
    return db


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """Run cli.py demo into a throwaway database in a subprocess."""
    import subprocess
    import sys

    db_url = f"sqlite:///{(tmp_path / 'demo.db').as_posix()}"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def make(*flags: str) -> str:
        env = dict(os.environ, DB_URL=db_url, PYTHONIOENCODING="utf-8")
        subprocess.run([sys.executable, "cli.py", "demo", "--days", "120", *flags],
                       cwd=root, env=env, check=True, capture_output=True)
        code = (
            "import json; from app.db import session_scope; from app.analytics import illness\n"
            "with session_scope() as s:\n"
            "    r = illness.check(s)\n"
            "    print(json.dumps({'level': r.level, 'flags': [x.metric for x in r.signals if x.flagged],"
            " 'conf': r.confounders}))"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                             check=True, capture_output=True, text=True).stdout
        return out.strip().splitlines()[-1]

    return make, db_url, root


def test_healthy_history_raises_nothing(demo):
    import json
    make, _, _ = demo
    assert json.loads(make())["level"] == "green"


def test_generated_illness_raises_an_alert(demo):
    import json
    make, _, _ = demo
    result = json.loads(make("--with-illness"))
    assert result["level"] == "alert"
    assert {"resp_rate", "skin_temp"} & set(result["flags"])
