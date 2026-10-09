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
    heart = sig("rhr", rhr)
    if rhr:
        heart.detail = "2 ночи подряд выше нормы на 4+"
    return decide(
        heart, yellow, sig("resp_rate", rr), sig("skin_temp", temp),
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


# --- confounders and feedback on a real (throwaway) database ----------------

def _run_in_db(db_url: str, root: str, code: str) -> str:
    import subprocess
    import sys
    env = dict(os.environ, DB_URL=db_url, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                          check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1]


CHECK = (
    "import json; from app.db import session_scope; from app.analytics import illness\n"
    "with session_scope() as s:\n"
    "    r = illness.check(s); print(json.dumps({'level': r.level, 'conf': r.confounders}))"
)


def test_time_zone_change_downgrades_the_alert(demo):
    import json
    make, db_url, root = demo
    assert json.loads(make("--with-illness"))["level"] == "alert"
    _run_in_db(db_url, root, (
        "from sqlalchemy import select; from app.db import session_scope; from app.models import Sleep\n"
        "from app.util import today_local\n"
        "with session_scope() as s:\n"
        "    for sl in s.scalars(select(Sleep).where(Sleep.day == today_local())): sl.timezone_offset = '+09:00'\n"
        "    for sl in s.scalars(select(Sleep).where(Sleep.day < today_local())): sl.timezone_offset = '+05:00'\n"
        "print('ok')"
    ))
    result = json.loads(_run_in_db(db_url, root, CHECK))
    assert result["level"] == "watch"
    assert any("часового пояса" in c for c in result["conf"])


def test_vaccine_dose_downgrades_the_alert(demo):
    import json
    make, db_url, root = demo
    make("--with-illness")
    _run_in_db(db_url, root, (
        "import datetime as dt; from app.db import session_scope; from app.analytics import feedback\n"
        "from app.util import today_local\n"
        "with session_scope() as s: feedback.record(s, today_local() - dt.timedelta(days=1), 'vaccine')\n"
        "print('ok')"
    ))
    result = json.loads(_run_in_db(db_url, root, CHECK))
    assert result["level"] == "watch"
    assert "прививка" in result["conf"]


def test_feedback_question_answer_and_track_record(demo):
    import json
    make, db_url, root = demo
    make()
    out = _run_in_db(db_url, root, (
        "import datetime as dt, json\n"
        "from app.db import session_scope; from app.models import Alert\n"
        "from app.analytics import feedback\n"
        "from app.util import today_local\n"
        "t = today_local(); d = lambda n: t - dt.timedelta(days=n)\n"
        "with session_scope() as s:\n"
        "    s.add(Alert(day=d(1), kind='illness', level='alert', message='', dedupe_key='a1'))\n"
        "    s.add(Alert(day=d(20), kind='illness', level='watch', message='', dedupe_key='a2'))\n"
        "with session_scope() as s:\n"
        "    q = feedback.pending_question(s, t)\n"
        "    feedback.record(s, d(1), 'sick', 'feedback')\n"
        "    feedback.record(s, d(20), 'other_cause', 'feedback')\n"
        "    feedback.record(s, d(40), 'sick', 'self')\n"
        "    feedback.record(s, d(39), 'sick', 'self')\n"
        "with session_scope() as s:\n"
        "    again = feedback.pending_question(s, t)\n"
        "    r = feedback.track_record(s, t)\n"
        "print(json.dumps({'q': [str(q[0]), q[1]], 'again': again, 'alerts': r.alerts,"
        " 'confirmed': r.confirmed, 'other': r.other_cause, 'ill': r.illnesses, 'caught': r.caught}))"
    ))
    r = json.loads(out)
    assert r["q"][1] == "alert"            # asked about yesterday's alert
    assert r["again"] is None              # answered — not asked twice
    assert (r["alerts"], r["confirmed"], r["other"]) == (2, 1, 1)
    # Two illnesses: the alerted one (caught) and a two-day one with no alert (missed).
    assert (r["ill"], r["caught"]) == (2, 1)
