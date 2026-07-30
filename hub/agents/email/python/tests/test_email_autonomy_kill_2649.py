# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Kill-switch propagation to the scheduler (#2649).

``gaia email autonomy kill`` used to only reach a live REST/CLI session's
own agent object (#2624's mid-cycle pre-emption, in
``test_email_autonomy_cycle.py::test_kill_mid_cycle_stops_the_run``). The
scheduler builds a brand-new, throwaway ``EmailTriageAgent`` per fire and
never shared that object, so a kill issued elsewhere never reached it.

These tests drive the propagation two ways, both through a SEPARATE agent
instance sharing the same ``state.db`` — never by mutating the running
agent's own ``config.autonomy_level`` in place, which is exactly the
same-object shortcut that let this bug exist:

1. Directly against ``EmailTriageAgent._run_email_autonomy_cycle`` — a kill
   mid-flight (AC1) and a kill before the cycle starts (the inbox-scan
   short-circuit).
2. Through ``autonomy_scheduler.run_autonomy_job`` and the real
   ``AutonomyScheduler`` asyncio loop — the scheduler's actual dispatcher
   seam (AC2/AC3): two independent fires, a kill lands between them, and
   the second fire — built fresh, called with the SAME stale level the
   scheduler's own config still holds — must not act.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytest.importorskip("gaia_agent_email")

from gaia.database.mixin import DatabaseMixin  # noqa: E402

from gaia_agent_email import autonomy_kill  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.autonomy_scheduler import (  # noqa: E402
    AutonomyScheduleConfig,
    AutonomyScheduler,
    run_autonomy_job,
)
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.trust import LEVEL_EARN_TRUST, LEVEL_FULL, LEVEL_OFF  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _fake_embed(text, *args, **kwargs):
    return np.zeros(768, dtype=np.float32)


class _MinimalCalendarBackend:
    def list_events(self, *a, **k):
        return {"events": []}


def _promo_message(message_id: str, sender: str = "deals@shop.com") -> dict:
    """A message the heuristic classifies confidently as PROMOTIONAL — an
    auto-archive candidate at LEVEL_FULL, no LLM needed."""
    internal_ms = int(time.time() * 1000)
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
        "internalDate": str(internal_ms),
        "snippet": "50% off everything this weekend",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Deals <{sender}>"},
                {"name": "Subject", "value": "Weekend sale inside"},
                {"name": "Message-ID", "value": f"<{message_id}@x.com>"},
                {"name": "Date", "value": "Mon, 12 Jun 2026 10:00:00 +0000"},
            ],
        },
    }


def _ordered_promo_messages(n: int, sender: str = "deals@shop.com") -> list:
    """``n`` PROMOTIONAL messages with strictly decreasing ``internalDate`` so
    ``FakeGmailBackend.list_messages``'s newest-first sort yields a
    deterministic row order — needed to pin "the Nth call" below."""
    base_ms = int(time.time() * 1000)
    messages = []
    for i in range(n):
        msg = _promo_message(f"m{i}", sender)
        msg["internalDate"] = str(base_ms - i)
        messages.append(msg)
    return messages


def _build_agent(
    tmp_path: Path,
    *,
    level: str,
    backend: "FakeGmailBackend | None" = None,
    messages: "list | None" = None,
    **cfg_kw,
) -> EmailTriageAgent:
    """Build a real, hermetic EmailTriageAgent (heuristic-only, no Lemonade).

    ``backend`` lets a caller share ONE mailbox across multiple agent
    instances built against the SAME ``tmp_path`` (same ``state.db``) — the
    production topology a scheduler fire and a REST/CLI session actually
    share (see ``config.resolved_db_path()``).
    """
    if backend is None:
        backend = FakeGmailBackend(user_email="me@example.com")
    for msg in messages or []:
        backend.add_message(msg)

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        autonomy_level=level,
        **cfg_kw,
    )

    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text", side_effect=_fake_embed
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    # Heuristic-only: disable the LLM classifier path entirely.
    agent.chat = None
    return agent


# ---------------------------------------------------------------------------
# autonomy_kill.py — the store's own contract, isolated from the full agent
# ---------------------------------------------------------------------------


class _TempDB(DatabaseMixin):
    pass


def _connected_db(path: str) -> _TempDB:
    db = _TempDB()
    db.init_db(path)
    autonomy_kill.init_schema(db)
    return db


class TestAutonomyKillStore:
    def test_starts_not_killed(self, tmp_path):
        db = _connected_db(str(tmp_path / "state.db"))
        assert autonomy_kill.is_killed(db) is False

    def test_set_killed_true_persists(self, tmp_path):
        db = _connected_db(str(tmp_path / "state.db"))
        autonomy_kill.set_killed(db, killed=True)
        assert autonomy_kill.is_killed(db) is True

    def test_set_killed_false_clears(self, tmp_path):
        db = _connected_db(str(tmp_path / "state.db"))
        autonomy_kill.set_killed(db, killed=True)
        autonomy_kill.set_killed(db, killed=False)
        assert autonomy_kill.is_killed(db) is False

    def test_visible_across_separate_connections_to_the_same_file(self, tmp_path):
        """The whole point of this store: two independent DatabaseMixin
        objects pointed at the SAME file must agree — this is what makes a
        kill issued by one EmailTriageAgent visible to another."""
        db_path = str(tmp_path / "shared.db")
        writer = _connected_db(db_path)
        autonomy_kill.set_killed(writer, killed=True)

        reader = _connected_db(db_path)  # a second, independent connection
        assert autonomy_kill.is_killed(reader) is True

    def test_init_schema_does_not_reset_an_existing_kill(self, tmp_path):
        """A fresh EmailTriageAgent calls init_schema in __init__ on every
        construction — it must be idempotent and never clobber a kill a
        PRIOR instance already wrote to the same file."""
        db_path = str(tmp_path / "shared.db")
        db = _connected_db(db_path)
        autonomy_kill.set_killed(db, killed=True)

        autonomy_kill.init_schema(db)  # re-run, as __init__ does on every build
        assert autonomy_kill.is_killed(db) is True


# ---------------------------------------------------------------------------
# set_autonomy_level persists (and clears) the flag
# ---------------------------------------------------------------------------


class TestSetAutonomyLevelPersistsKill:
    def test_off_persists_the_kill_flag(self, tmp_path):
        agent = _build_agent(tmp_path, level=LEVEL_FULL)
        agent.set_autonomy_level(LEVEL_OFF)
        assert autonomy_kill.is_killed(agent) is True

    def test_resume_clears_the_kill_flag(self, tmp_path):
        """Without this, a kill would be permanent — 'resume' must actually
        un-block the scheduler, not just the session that issued it."""
        agent = _build_agent(tmp_path, level=LEVEL_FULL)
        agent.set_autonomy_level(LEVEL_OFF)
        assert autonomy_kill.is_killed(agent) is True
        agent.set_autonomy_level(LEVEL_EARN_TRUST)
        assert autonomy_kill.is_killed(agent) is False


# ---------------------------------------------------------------------------
# #2649 AC1 — a kill issued against a DIFFERENT agent object stops a
# currently-running (scheduler-shaped) cycle mid-batch.
# ---------------------------------------------------------------------------


def test_kill_from_a_separate_agent_stops_a_running_cycle_mid_batch(tmp_path):
    """Mirrors test_email_autonomy_cycle.py::test_kill_mid_cycle_stops_the_run
    (#2624), but the kill comes from a SEPARATE EmailTriageAgent object
    sharing the same state.db — simulating a REST/CLI kill landing while a
    scheduler-built agent's cycle is mid-flight. Must fail against a fix
    that only re-checks ``self.config.autonomy_level``: agent_a's own field
    is never touched here, only agent_b's is."""
    messages = _ordered_promo_messages(10)
    agent_a = _build_agent(tmp_path, level=LEVEL_FULL, messages=messages)
    # A second, independent agent/connection onto the SAME state.db — the
    # kill-issuing side (e.g. a REST/CLI session), never referenced by
    # agent_a's code at all.
    agent_b = _build_agent(tmp_path, level=LEVEL_FULL)

    real_execute = agent_a._autonomy_execute
    calls = {"n": 0}

    def _side_effecting_execute(action_type, row):
        calls["n"] += 1
        result = real_execute(action_type, row)
        if calls["n"] == 3:
            agent_b.set_autonomy_level(LEVEL_OFF)
        return result

    with patch.object(
        agent_a, "_autonomy_execute", side_effect=_side_effecting_execute
    ):
        report = agent_a._run_email_autonomy_cycle()

    assert len(report["executed"]) == 3, report["executed"]
    assert report["stopped"] == "autonomy_off"
    rows = agent_a.query("SELECT action_id FROM email_autonomy_actions")
    assert len(rows) == 3, rows
    # agent_a's OWN config was never mutated — only the shared flag was.
    assert agent_a.config.autonomy_level == LEVEL_FULL


def test_killed_agent_skips_the_inbox_scan_entirely(tmp_path):
    """A cycle that starts already-killed must not scan the mailbox at all
    — a killed schedule would otherwise keep hitting the backend every fire
    forever for no reason. Isolates the cycle-start check from the per-row
    one above: kills BEFORE any row is processed, at a level that would
    otherwise be enabled (``policy.enabled`` alone would let this through)."""
    agent = _build_agent(tmp_path, level=LEVEL_FULL, messages=[_promo_message("m1")])
    killer = _build_agent(tmp_path, level=LEVEL_FULL)
    killer.set_autonomy_level(LEVEL_OFF)

    with patch.object(agent, "_triage_all_backends") as mock_triage:
        report = agent._run_email_autonomy_cycle()

    mock_triage.assert_not_called()
    assert report["executed"] == []
    assert report["stopped"] == "autonomy_off"


# ---------------------------------------------------------------------------
# #2649 AC2 — the kill also blocks the NEXT scheduled fire, driven through
# autonomy_scheduler.run_autonomy_job — the real dispatcher seam (AC3).
# ---------------------------------------------------------------------------


def test_next_fire_via_run_autonomy_job_does_not_execute_at_the_old_level(tmp_path):
    """Two INDEPENDENT run_autonomy_job calls (two fresh agents, one shared
    mailbox + state.db) — the second is a genuinely separate fire, not a
    re-inspection of the first's state. The first fire proves the setup
    executes normally; a kill lands between fires (from a THIRD, unrelated
    agent); the second fire is called with the SAME stale ``level="full"``
    the scheduler's own config would still be holding, and must still
    refuse to act."""
    shared_backend = FakeGmailBackend(user_email="me@example.com")
    shared_backend.add_message(_promo_message("m1"))

    def _factory1(level):
        return _build_agent(tmp_path, level=level, backend=shared_backend)

    first = run_autonomy_job(level=LEVEL_FULL, max_messages=25, build_agent=_factory1)
    assert [e["message_id"] for e in first["executed"]] == ["m1"]

    # A kill lands (e.g. `gaia email autonomy kill` against a live session)
    # — independent of both fires, sharing only the state.db file.
    killer = _build_agent(tmp_path, level=LEVEL_FULL)
    killer.set_autonomy_level(LEVEL_OFF)

    # A fresh message the second fire would otherwise auto-archive.
    shared_backend.add_message(_promo_message("m2"))

    def _factory2(level):
        return _build_agent(tmp_path, level=level, backend=shared_backend)

    second = run_autonomy_job(level=LEVEL_FULL, max_messages=25, build_agent=_factory2)

    assert second["executed"] == [], second["executed"]
    assert second["stopped"] == "autonomy_off"
    # m2 is still sitting in the inbox, untouched.
    assert "INBOX" in shared_backend.get_message("m2").get("labelIds", [])


def test_scheduler_loop_suppresses_the_second_fire_after_a_kill(tmp_path):
    """Full end-to-end proof through the REAL AutonomyScheduler asyncio
    interval loop (not just run_autonomy_job called directly) — the same
    wiring server.py starts. Two fires; a kill lands between them; the
    second fire's report shows nothing executed."""
    shared_backend = FakeGmailBackend(user_email="me@example.com")
    shared_backend.add_message(_promo_message("m1"))
    fire_reports: list = []

    async def _run():
        first_fired = asyncio.Event()
        second_fired = asyncio.Event()
        fire_count = {"n": 0}

        def _build(level):
            return _build_agent(tmp_path, level=level, backend=shared_backend)

        def _job(*, level, max_messages):
            report = run_autonomy_job(
                level=level, max_messages=max_messages, build_agent=_build
            )
            fire_reports.append(report)
            fire_count["n"] += 1
            if fire_count["n"] == 1:
                first_fired.set()
            elif fire_count["n"] == 2:
                second_fired.set()
            return report

        cfg = AutonomyScheduleConfig(
            enabled=True, level=LEVEL_FULL, interval_minutes=1, max_messages=25
        )
        sched = AutonomyScheduler(cfg, run_job=_job)
        sched._delay_seconds = 0.01  # test seam: fire almost immediately
        assert sched.start() is True
        await asyncio.wait_for(first_fired.wait(), timeout=2.0)

        # Kill between fires, from a separate agent sharing state.db — the
        # scheduler's own `cfg.level` is untouched (frozen dataclass).
        killer = _build_agent(tmp_path, level=LEVEL_FULL)
        killer.set_autonomy_level(LEVEL_OFF)
        shared_backend.add_message(_promo_message("m2"))

        await asyncio.wait_for(second_fired.wait(), timeout=2.0)
        await sched.stop()

    asyncio.run(_run())

    assert len(fire_reports) >= 2
    assert [e["message_id"] for e in fire_reports[0]["executed"]] == ["m1"]
    assert fire_reports[1]["executed"] == []
    assert fire_reports[1]["stopped"] == "autonomy_off"
