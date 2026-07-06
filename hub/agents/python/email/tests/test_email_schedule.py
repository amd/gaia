# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Scheduled send + snooze tests (#1609).

Acceptance criteria covered:
- AC: a scheduled send fires once at/after its time and NOT before.
- AC: a snoozed message is removed from INBOX and re-surfaced at the
  scheduled time.
- AC: cancel prevents both the send and the re-surface.
- Fail-loudly: a firing send must not swallow a send failure (the job is
  marked failed with the error persisted); cancelling a non-pending job is
  an error, never a silent no-op.

Most tests drive ``EmailJobScheduler.fire_due_jobs(now=...)`` with an
injected clock — deterministic, no sleeps. One thread smoke test exercises
the real polling driver.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.scheduler import EmailJobScheduler  # noqa: E402
from gaia_agent_email.tools.schedule_tools import (  # noqa: E402
    _parse_future_ts,
    cancel_scheduled_job_impl,
    list_scheduled_jobs_impl,
    schedule_send_impl,
    snooze_message_impl,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DB(DatabaseMixin):
    """Bare DatabaseMixin host for store-level tests (no agent needed)."""


def _make_db(tmp_path: Path) -> _DB:
    db = _DB()
    db.init_db(str(tmp_path / "state.db"))
    schedule_store.init_schema(db)
    # schedule_send_impl also writes the email_drafts audit row.
    from gaia_agent_email import action_store

    action_store.init_schema(db)
    return db


def _inbox_message(message_id: str = "msg_1") -> dict:
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": str(int(time.time() * 1000)),
        "snippet": "hello",
        "payload": {
            "headers": [
                {"name": "From", "value": "Boss <boss@example.com>"},
                {"name": "Subject", "value": "Need your input"},
            ],
        },
    }


def _iso_in(seconds: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(time.time() + seconds).isoformat()


def _calls(backend: FakeGmailBackend, name: str) -> list:
    return [c for c in backend.transport.calls if c[0] == name]


def _scheduler_for(tmp_path: Path, backend) -> EmailJobScheduler:
    """Scheduler with the same executor composition the agent wires up.

    Executors receive the scheduler's own per-pass db connection (never the
    test's), mirroring the agent wiring.
    """
    from gaia_agent_email.tools.schedule_tools import (
        execute_scheduled_send_impl,
        execute_snooze_impl,
    )

    return EmailJobScheduler(
        str(tmp_path / "state.db"),
        executors={
            schedule_store.KIND_SCHEDULED_SEND: (
                lambda job, db: execute_scheduled_send_impl(backend, db, job=job)
            ),
            schedule_store.KIND_SNOOZE: (
                lambda job, db: execute_snooze_impl(backend, job=job)
            ),
        },
        poll_seconds=30.0,
    )


# ---------------------------------------------------------------------------
# Tests — scheduled send
# ---------------------------------------------------------------------------


class TestScheduledSend:
    def test_fires_once_at_or_after_time_not_before(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        scheduler = _scheduler_for(tmp_path, backend)
        due = time.time() + 3600

        result = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="Later",
            body="scheduled body",
            send_at=_iso_in(3600),
            mailbox="google",
        )
        job_id = result["job_id"]
        # The body lives in a backend draft, not in SQLite.
        assert backend.list_drafts(), "schedule_send must create a draft"
        job = schedule_store.get_job(db, job_id=job_id)
        assert "scheduled body" not in json.dumps(job["payload"])

        # NOT before its time.
        out = scheduler.fire_due_jobs(now=due - 10)
        assert out == {"fired": [], "failed": []}
        assert not _calls(backend, "send_draft")
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "pending"

        # Fires at/after its time.
        out = scheduler.fire_due_jobs(now=due + 10)
        assert out["fired"] == [job_id]
        assert len(_calls(backend, "send_draft")) == 1
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "fired"

        # Exactly once — a later pass must not re-fire.
        out = scheduler.fire_due_jobs(now=due + 20)
        assert out == {"fired": [], "failed": []}
        assert len(_calls(backend, "send_draft")) == 1

    def test_send_failure_is_loud_never_swallowed(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        scheduler = _scheduler_for(tmp_path, backend)
        due = time.time() + 3600

        result = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="Later",
            body="body",
            send_at=_iso_in(3600),
            mailbox="google",
        )
        job_id = result["job_id"]
        # The user deleted the draft from their mail client before it fired.
        backend._drafts.clear()

        out = scheduler.fire_due_jobs(now=due + 10)
        assert out["failed"] == [job_id]
        job = schedule_store.get_job(db, job_id=job_id)
        assert job["status"] == "failed"
        assert job["error"], "the send failure must be persisted on the job row"

    def test_rejects_past_and_garbage_times(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")

        with pytest.raises(ValueError, match="not in the future"):
            schedule_send_impl(
                backend,
                db,
                to="a@example.com",
                subject="s",
                body="b",
                send_at=_iso_in(-60),
            )
        with pytest.raises(ValueError, match="ISO-8601"):
            _parse_future_ts("tomorrow-ish")
        with pytest.raises(ValueError, match="no time given"):
            _parse_future_ts("")
        # Neither attempt may have created a draft or a job.
        assert not backend.list_drafts()
        assert schedule_store.list_jobs(db) == []


# ---------------------------------------------------------------------------
# Tests — snooze
# ---------------------------------------------------------------------------


class TestSnooze:
    def test_leaves_inbox_now_and_resurfaces_at_time(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        scheduler = _scheduler_for(tmp_path, backend)
        due = time.time() + 3600

        result = snooze_message_impl(
            backend,
            db,
            message_id="msg_1",
            until=_iso_in(3600),
            mailbox="google",
        )
        job_id = result["job_id"]
        # Out of INBOX immediately.
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]

        # NOT re-surfaced before its time.
        scheduler.fire_due_jobs(now=due - 10)
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]

        # Re-surfaces at/after its time.
        out = scheduler.fire_due_jobs(now=due + 10)
        assert out["fired"] == [job_id]
        assert "INBOX" in backend.get_message("msg_1")["labelIds"]
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "fired"

    def test_snooze_rolls_back_archive_when_job_write_fails(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))

        with patch.object(
            schedule_store, "create_job", side_effect=RuntimeError("disk full")
        ):
            with pytest.raises(RuntimeError, match="restored to INBOX"):
                snooze_message_impl(
                    backend, db, message_id="msg_1", until=_iso_in(3600)
                )
        # The archive was rolled back — the message must NOT silently vanish.
        assert "INBOX" in backend.get_message("msg_1")["labelIds"]
        assert schedule_store.list_jobs(db) == []

    def test_snooze_requires_inbox_membership(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        msg = _inbox_message("msg_1")
        msg["labelIds"] = ["UNREAD"]  # already archived
        backend.add_message(msg)

        with pytest.raises(ValueError, match="not in INBOX"):
            snooze_message_impl(
                backend, db, message_id="msg_1", until=_iso_in(3600)
            )
        assert schedule_store.list_jobs(db) == []


# ---------------------------------------------------------------------------
# Tests — cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_prevents_send_and_resurface(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        scheduler = _scheduler_for(tmp_path, backend)
        due = time.time() + 3600

        send_job = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="Later",
            body="b",
            send_at=_iso_in(3600),
            mailbox="google",
        )["job_id"]
        snooze_job = snooze_message_impl(
            backend,
            db,
            message_id="msg_1",
            until=_iso_in(3600),
            mailbox="google",
        )["job_id"]

        assert cancel_scheduled_job_impl(db, job_id=send_job)["cancelled"]
        assert cancel_scheduled_job_impl(db, job_id=snooze_job)["cancelled"]

        out = scheduler.fire_due_jobs(now=due + 10)
        assert out == {"fired": [], "failed": []}
        # No send fired; the snoozed message did NOT re-surface.
        assert not _calls(backend, "send_draft")
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]
        assert schedule_store.get_job(db, job_id=send_job)["status"] == "cancelled"
        assert schedule_store.get_job(db, job_id=snooze_job)["status"] == "cancelled"

    def test_cancel_non_pending_is_loud(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        scheduler = _scheduler_for(tmp_path, backend)
        due = time.time() + 3600

        job_id = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="s",
            body="b",
            send_at=_iso_in(3600),
            mailbox="google",
        )["job_id"]
        scheduler.fire_due_jobs(now=due + 10)

        with pytest.raises(ValueError, match="no longer be"):
            cancel_scheduled_job_impl(db, job_id=job_id)
        with pytest.raises(ValueError, match="no scheduled job"):
            cancel_scheduled_job_impl(db, job_id="nope")


# ---------------------------------------------------------------------------
# Tests — scheduler mechanics
# ---------------------------------------------------------------------------


class TestSchedulerMechanics:
    def test_missing_executor_marks_job_failed(self, tmp_path):
        db = _make_db(tmp_path)
        job_id = schedule_store.create_job(
            db, kind="unknown_kind", due_at=time.time() - 1, payload={}
        )
        scheduler = EmailJobScheduler(str(tmp_path / "state.db"), executors={}, poll_seconds=30.0)

        out = scheduler.fire_due_jobs()
        assert out["failed"] == [job_id]
        job = schedule_store.get_job(db, job_id=job_id)
        assert job["status"] == "failed"
        assert "no executor" in job["error"]

    def test_jobs_persist_across_restart_and_fire_after(self, tmp_path):
        # Session 1: schedule, then "crash" (close the DB without firing).
        db1 = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        due = time.time() + 0.5
        job_id = schedule_send_impl(
            backend,
            db1,
            to="a@example.com",
            subject="s",
            body="b",
            send_at=_iso_in(0.5),
            mailbox="google",
        )["job_id"]
        db1.close_db()

        # Session 2: a fresh scheduler on the same path (it opens its own
        # connection) sees the pending job and fires it once past-due
        # ("at/after its time").
        scheduler = _scheduler_for(tmp_path, backend)
        out = scheduler.fire_due_jobs(now=due + 10)
        assert out["fired"] == [job_id]
        assert len(_calls(backend, "send_draft")) == 1

    def test_polling_thread_fires_due_job(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        job_id = snooze_message_impl(
            backend,
            db,
            message_id="msg_1",
            until=_iso_in(0.2),
            mailbox="google",
        )["job_id"]
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]

        from gaia_agent_email.tools.schedule_tools import execute_snooze_impl

        scheduler = EmailJobScheduler(
            str(tmp_path / "state.db"),
            executors={
                schedule_store.KIND_SNOOZE: (
                    lambda job, db: execute_snooze_impl(backend, job=job)
                )
            },
            poll_seconds=0.05,
        )
        scheduler.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if schedule_store.get_job(db, job_id=job_id)["status"] == "fired":
                    break
                time.sleep(0.05)
        finally:
            scheduler.stop()
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "fired"
        assert "INBOX" in backend.get_message("msg_1")["labelIds"]

    def test_polling_thread_fires_scheduled_send_with_db_write(self, tmp_path):
        # Regression guard for the cross-thread sqlite crash: a scheduled
        # send fired FROM THE POLLING THREAD writes its audit row through the
        # scheduler's own per-pass connection, never the creator's.
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        job_id = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="s",
            body="b",
            send_at=_iso_in(0.2),
            mailbox="google",
        )["job_id"]

        scheduler = _scheduler_for(tmp_path, backend)
        scheduler._poll_seconds = 0.05
        scheduler.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if schedule_store.get_job(db, job_id=job_id)["status"] == "fired":
                    break
                time.sleep(0.05)
        finally:
            scheduler.stop()
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "fired"
        assert len(_calls(backend, "send_draft")) == 1
        # The audit row was marked sent by the thread's own connection.
        from gaia_agent_email import action_store

        draft_id = _calls(backend, "send_draft")[0][1]["draft_id"]
        assert action_store.fetch_draft(db, draft_id=draft_id)["sent_at"]

    def test_list_scheduled_jobs_shows_cancel_handles(self, tmp_path):
        db = _make_db(tmp_path)
        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        send_job = schedule_send_impl(
            backend,
            db,
            to="a@example.com",
            subject="Later",
            body="b",
            send_at=_iso_in(3600),
            mailbox="google",
        )["job_id"]
        snooze_job = snooze_message_impl(
            backend,
            db,
            message_id="msg_1",
            until=_iso_in(3600),
            mailbox="google",
        )["job_id"]

        out = list_scheduled_jobs_impl(db)
        assert out["count"] == 2
        ids = {j["job_id"] for j in out["pending"]}
        assert ids == {send_job, snooze_job}


# ---------------------------------------------------------------------------
# Tests — agent wiring
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768


def _fake_embed(text: str) -> np.ndarray:
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path, backend: FakeGmailBackend):
    """EmailTriageAgent with fakes, tmp DBs, and the polling thread OFF
    (tests drive fire_due_jobs deterministically)."""
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    class _MinimalCalendarBackend:
        pass

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        start_scheduler=False,
    )

    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text",
            side_effect=_fake_embed,
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings",
            return_value=0,
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(cfg)


class TestAgentWiring:
    def test_tools_registered_and_send_gated(self, tmp_path):
        from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
        from gaia.agents.base.tools import _TOOL_REGISTRY

        backend = FakeGmailBackend(user_email="me@example.com")
        _build_agent(tmp_path, backend)

        for name in (
            "schedule_send",
            "snooze_message",
            "cancel_scheduled_job",
            "list_scheduled_jobs",
        ):
            assert name in _TOOL_REGISTRY, f"{name} not registered"
        # Tier-2: confirmation at creation (#1264), unattended fire after.
        assert "schedule_send" in TOOLS_REQUIRING_CONFIRMATION
        assert "snooze_message" not in TOOLS_REQUIRING_CONFIRMATION

    def test_end_to_end_through_agent_tools_and_scheduler(self, tmp_path):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        backend = FakeGmailBackend(user_email="me@example.com")
        backend.add_message(_inbox_message("msg_1"))
        agent = _build_agent(tmp_path, backend)
        # The agent routes snooze via message provenance.
        agent._remember_message_mailbox("msg_1", "google")
        due = time.time() + 3600

        send_env = json.loads(
            _TOOL_REGISTRY["schedule_send"]["function"](
                "a@example.com", "Later", "body", _iso_in(3600)
            )
        )
        assert send_env["ok"], send_env
        snooze_env = json.loads(
            _TOOL_REGISTRY["snooze_message"]["function"]("msg_1", _iso_in(3600))
        )
        assert snooze_env["ok"], snooze_env
        assert "INBOX" not in backend.get_message("msg_1")["labelIds"]

        out = agent._scheduler.fire_due_jobs(now=due + 10)
        assert sorted(out["fired"]) == sorted(
            [send_env["data"]["job_id"], snooze_env["data"]["job_id"]]
        )
        assert len(_calls(backend, "send_draft")) == 1
        assert "INBOX" in backend.get_message("msg_1")["labelIds"]

    def test_cancel_through_agent_tool(self, tmp_path):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        backend = FakeGmailBackend(user_email="me@example.com")
        agent = _build_agent(tmp_path, backend)
        due = time.time() + 3600

        send_env = json.loads(
            _TOOL_REGISTRY["schedule_send"]["function"](
                "a@example.com", "Later", "body", _iso_in(3600)
            )
        )
        job_id = send_env["data"]["job_id"]
        cancel_env = json.loads(
            _TOOL_REGISTRY["cancel_scheduled_job"]["function"](job_id)
        )
        assert cancel_env["ok"], cancel_env

        out = agent._scheduler.fire_due_jobs(now=due + 10)
        assert out == {"fired": [], "failed": []}
        assert not _calls(backend, "send_draft")
