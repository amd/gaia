# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An agent schedules its own follow-ups, and the daemon clock fires them.

Runs the real clock and store on a temp SQLite file; only the agent call and
the desktop notification are stubbed.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.daemon import followup_routes, followups
from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock

NOW = datetime(2026, 10, 23, 14, 30).astimezone()


@pytest.fixture
def clock(tmp_path):
    asked, notified = [], []

    def ask(agent_id, prompt, model, session_id):
        asked.append((agent_id, prompt, model, session_id))
        return "The build passed in 12 minutes.\nAll 340 tests green."

    def notify(title, text):
        notified.append((title, text))

    c = DaemonClock(
        str(tmp_path / "scheduler.db"),
        executors={followups.KIND: followups.make_executor(ask, notify)},
    )
    c.asked, c.notified = asked, notified
    return c


def _db(clock):
    db = clock.open_db()
    followups.init_schema(db)
    return db


# -- when -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("in 2 hours", NOW + timedelta(hours=2)),
        ("in a minute", NOW + timedelta(minutes=1)),
        ("tomorrow at 9am", NOW.replace(day=24, hour=9, minute=0)),
        ("at 16:30", NOW.replace(hour=16, minute=30)),
        ("at 1pm", NOW.replace(day=24, hour=13, minute=0)),
        ("2026-10-25T09:00", datetime(2026, 10, 25, 9, 0).astimezone()),
    ],
)
def test_when_reads_common_phrases(text, expected):
    assert followups.parse_when(text, NOW) == expected


@pytest.mark.parametrize("text", ["in 8 days", "in 10 seconds", "yesterday", ""])
def test_when_refuses_what_it_cannot_schedule_and_says_how(text):
    with pytest.raises(followups.FollowUpError) as e:
        followups.parse_when(text, NOW)
    assert "in 2 hours" in str(e.value) or "7 days" in str(e.value)


# -- limits -----------------------------------------------------------------


def test_create_refuses_an_eleventh_waiting_follow_up(clock):
    db = _db(clock)
    now = time.time()
    for i in range(followups.MAX_PENDING):
        followups.create(db, agent_id="gaia", task=f"check {i}", fire_at=now + 3600)
    with pytest.raises(followups.FollowUpError, match="already waiting"):
        followups.create(db, agent_id="gaia", task="one more", fire_at=now + 3600)


def test_create_refuses_a_time_too_near_or_too_far(clock):
    db = _db(clock)
    now = time.time()
    for fire_at in (now + 5, now + followups.MAX_DELAY_SECONDS + 60):
        with pytest.raises(followups.FollowUpError):
            followups.create(db, agent_id="gaia", task="x", fire_at=fire_at, now=now)


def test_one_agent_cannot_cancel_another_agents_follow_up(clock):
    db = _db(clock)
    job = followups.create(db, agent_id="gaia", task="x", fire_at=time.time() + 3600)
    assert not followups.cancel(db, agent_id="email", job_id=job["id"])
    assert followups.cancel(db, agent_id="gaia", job_id=job["id"])
    assert followups.pending(db, agent_id="gaia") == []


# -- firing -----------------------------------------------------------------


def test_a_due_follow_up_runs_notifies_and_keeps_its_answer(clock):
    db = _db(clock)
    job = followups.create(
        db,
        agent_id="gaia",
        task="Check CI run 4012",
        fire_at=time.time() + 120,
        model="fireworks.deepseek-v4p1-flash",
    )

    fired = clock.fire_due(now=job["fire_at"] + 1)

    assert fired["fired"] == [job["id"]]
    agent_id, prompt, model, session_id = clock.asked[0]
    assert followups.is_run_session(session_id)
    assert session_id == followups.run_session_id(job["id"])
    assert agent_id == "gaia" and "Check CI run 4012" in prompt
    assert model == "fireworks.deepseek-v4p1-flash"
    assert "search for files" in prompt and "shell commands" in prompt
    assert clock.notified == [("GAIA follow-up", "The build passed in 12 minutes.")]
    [result] = followups.unseen_results(_db(clock), agent_id="gaia")
    assert result["answer"].startswith("The build passed")


def test_a_follow_up_that_is_not_due_does_not_run(clock):
    db = _db(clock)
    followups.create(db, agent_id="gaia", task="later", fire_at=time.time() + 3600)
    assert clock.fire_due(now=time.time())["fired"] == []
    assert clock.asked == []


def test_a_failed_follow_up_is_kept_so_the_user_hears_about_it(tmp_path):
    def ask(agent_id, prompt, model, session_id):
        raise RuntimeError("the gaia agent's stream ended without an answer")

    c = DaemonClock(
        str(tmp_path / "s.db"),
        executors={followups.KIND: followups.make_executor(ask, lambda t, x: None)},
    )
    db = _db(c)
    job = followups.create(db, agent_id="gaia", task="x", fire_at=time.time() + 120)

    assert c.fire_due(now=job["fire_at"] + 1)["failed"] == [job["id"]]
    [result] = followups.unseen_results(_db(c), agent_id="gaia")
    assert "without an answer" in result["error"] and result["answer"] is None


def test_a_notification_that_cannot_post_still_keeps_the_answer(tmp_path):
    def notify(title, text):
        raise NotImplementedError("notification sink is not implemented for 'Windows'")

    c = DaemonClock(
        str(tmp_path / "s.db"),
        executors={
            followups.KIND: followups.make_executor(lambda a, p, m, s: "Done.", notify)
        },
    )
    db = _db(c)
    job = followups.create(db, agent_id="gaia", task="x", fire_at=time.time() + 120)

    assert c.fire_due(now=job["fire_at"] + 1)["fired"] == [job["id"]]
    [result] = followups.unseen_results(_db(c), agent_id="gaia")
    assert result["answer"] == "Done." and "not implemented" in result["note"]


def test_seen_results_are_not_reported_again(clock):
    db = _db(clock)
    job = followups.create(db, agent_id="gaia", task="x", fire_at=time.time() + 120)
    clock.fire_due(now=job["fire_at"] + 1)

    assert followups.mark_seen(_db(clock), agent_id="gaia", job_ids=[job["id"]]) == 1
    assert followups.unseen_results(_db(clock), agent_id="gaia") == []


# -- routes -----------------------------------------------------------------


@pytest.fixture
def api(clock):
    app = FastAPI()
    followup_routes.mount(app, lambda: None, clock)
    return TestClient(app)


def test_the_routes_create_list_and_cancel(api):
    made = api.post(
        "/daemon/v1/followups",
        json={"agent_id": "gaia", "task": "check", "fire_at": time.time() + 600},
    )
    assert made.status_code == 200, made.text
    listed = api.get("/daemon/v1/followups", params={"agent_id": "gaia"}).json()
    assert [p["id"] for p in listed["pending"]] == [made.json()["id"]]

    gone = api.post(
        f"/daemon/v1/followups/{made.json()['id']}/cancel", params={"agent_id": "gaia"}
    )
    assert gone.status_code == 200
    again = api.post(
        f"/daemon/v1/followups/{made.json()['id']}/cancel", params={"agent_id": "gaia"}
    )
    assert again.status_code == 404 and "List them" in again.json()["detail"]


def test_the_create_route_says_why_it_refused(api):
    r = api.post(
        "/daemon/v1/followups",
        json={"agent_id": "gaia", "task": "", "fire_at": time.time() + 600},
    )
    assert r.status_code == 422 and "needs a task" in r.json()["detail"]


def test_cancel_job_leaves_a_fired_job_alone(clock):
    db = _db(clock)
    job = followups.create(db, agent_id="gaia", task="x", fire_at=time.time() + 120)
    clock.fire_due(now=job["fire_at"] + 1)
    assert not store.cancel_job(_db(clock), job_id=job["id"])


def test_a_wall_clock_time_keeps_its_hour_across_a_dst_change(monkeypatch):
    """9am the day after the clocks go back is 9am PST, not 9am PDT (8am PST)."""
    import time as time_module

    if not hasattr(time_module, "tzset"):
        pytest.skip("needs tzset to set the system time zone")
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time_module.tzset()

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 31, 10, 0)

    monkeypatch.setattr(followups, "datetime", _Clock)
    try:
        when = followups.parse_when("tomorrow at 9am")
    finally:
        monkeypatch.undo()
        time_module.tzset()

    assert (when.month, when.day, when.hour, when.minute) == (11, 1, 9, 0)
    assert when.utcoffset() == timedelta(hours=-8)


def test_a_run_session_is_a_valid_agent_session_id():
    from gaia_agent_chat.session import validate_session_id

    validate_session_id(followups.run_session_id("0" * 32))
    assert not followups.is_run_session("2b6f0a1c-9d3e-4f5a-8b7c-1e2d3f4a5b6c")
    assert not followups.is_run_session(None)
