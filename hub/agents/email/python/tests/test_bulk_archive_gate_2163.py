# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for #2163 — bulk archive left the earliest items unsafe:

The reported repro ("archive all suggested promos") looped the SINGLE
``archive_message`` tool six times. Each op recorded its own action with its own
30 s undo window, so during the ~26 s run the earliest items' undo windows had
already lapsed by the time the agent offered "undo within the window" — the
offer was false, and there was no single handle to undo the set as a whole.

The fix follows the issue's remedy (b): a loop of single archives in one turn
shares ONE per-turn undo batch handle, and ``fetch_batch_undoable`` anchors the
window to batch COMPLETION (the latest op) rather than per-row. Every item then
stays undoable for the full window after the run finishes. The sanctioned bulk
tool ``archive_message_batch`` (issue #1270 — 20+ in one call) is unchanged and
NOT gated; it was already one undoable batch and now also gets the
completion-anchored window.

Exercised against the real ``FakeGmailBackend`` and a controllable clock (the
``action_store`` module's ``time`` is swapped for a settable fake), so no
wall-clock timing and no live mailbox are involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# parents[0]=tests/ [1]=python/ [2]=email/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.organize_tools import (  # noqa: E402
    undo_archive_batch_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

EMBEDDING_DIM = 768


class _MinimalCalendarBackend:
    pass


class _DB(DatabaseMixin):
    pass


class _Clock:
    """Controllable clock for the action log. ``now`` is settable per phase."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def time(self) -> float:
        return self.now


def _fake_embed(_text: str) -> np.ndarray:
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _inbox_message(message_id: str, sender: str) -> dict:
    """A Gmail-API-shape INBOX message with a distinct sender."""
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1700000000000",
        "snippet": "promo",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Deals <{sender}>"},
                {"name": "Subject", "value": "50% off"},
                {"name": "Message-ID", "value": f"<{message_id}@x.com>"},
            ],
        },
    }


def _build_agent_with_fake_gmail(tmp_path: Path, messages: list[dict]):
    """EmailTriageAgent backed by a real FakeGmailBackend seeded with messages."""
    backend = FakeGmailBackend(user_email="me@example.com")
    for msg in messages:
        backend.add_message(msg)

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
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
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent, backend


def _call_tool(name: str, *args, **kwargs) -> dict:
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"tool {name!r} not registered"
    return json.loads(entry["function"](*args, **kwargs))


# ---------------------------------------------------------------------------
# Defects 1+2 on the reported repro path — a loop of SINGLE archive_message
# calls (N6: "archive all suggested promos") shares ONE per-turn undo batch, so
# the whole set is undoable as a unit for a window anchored to completion. This
# is the issue's remedy (b): "mint a single batch-level undo handle whose window
# starts when the batch completes, not per-op". No pre-threshold op is left with
# its own clock ticking mid-run.
# ---------------------------------------------------------------------------


def test_single_archive_loop_shares_one_undo_batch_surviving_the_run(
    tmp_path, monkeypatch
):
    """Five single archive_message calls in one turn share one batch_id, and
    undo_archive_batch restores ALL of them even after the earliest op's own
    per-op window would have lapsed — no mid-run expiry."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(action_store, "time", clock)

    msgs = [_inbox_message(f"m{i}", "promo@shop.com") for i in range(5)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()  # fresh turn -> fresh batch handle
        # Loop single archives, advancing the clock so the run spans 20s
        # (ops at 1000,1005,1010,1015,1020) — like a real multi-item run.
        for i, m in enumerate(msgs):
            clock.now = 1000.0 + 5.0 * i
            res = _call_tool("archive_message", m["id"])
            assert res["ok"] is True, f"single archive should succeed: {res}"
            assert "INBOX" not in backend.get_message(m["id"]).get("labelIds", [])

        # All five singles recorded ONE shared batch_id (route b).
        rows = agent.query(
            "SELECT DISTINCT batch_id FROM email_actions WHERE action_type='archive'"
        )
        batch_ids = {r["batch_id"] for r in rows}
        assert batch_ids == {
            agent._organize_batch_id
        }, f"single archives must share the per-turn batch handle, got {batch_ids}"
        batch_id = agent._organize_batch_id

        # t = 1040: past the FIRST op's own 30s window (1000+30=1030) but within
        # the window measured from completion (1020+30=1050). Undo restores ALL 5.
        clock.now = 1040.0
        undo = _call_tool("undo_archive_batch", batch_id)
        assert undo["ok"] is True, f"undo should succeed within window: {undo}"
        assert undo["data"]["restored"] == 5
        for m in msgs:
            labels = backend.get_message(m["id"]).get("labelIds", [])
            assert "INBOX" in labels, f"{m['id']} not restored: {labels}"
    finally:
        agent.close_db()


def test_batch_archive_tool_still_archives_bulk_unchanged(tmp_path):
    """#1270 regression guard: archive_message_batch remains the sanctioned bulk
    vehicle — a 6-message batch archives all six in one call (no new gate)."""
    msgs = [_inbox_message(f"m{i}", f"s{i}@x.com") for i in range(6)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()
        ids = [m["id"] for m in msgs]
        res = _call_tool("archive_message_batch", ids)

        assert res["ok"] is True, f"bulk batch must still archive: {res}"
        assert res["data"]["total"] == 6
        assert len(res["data"]["succeeded"]) == 6
        for mid in ids:
            labels = backend.get_message(mid).get("labelIds", [])
            assert "INBOX" not in labels, f"{mid} not archived: {labels}"
    finally:
        agent.close_db()


# ---------------------------------------------------------------------------
# Defect 2 (unit) — the batch undo window is anchored to completion, not per-op
# ---------------------------------------------------------------------------


def _record_batch_archive(db, backend, clock, batch_id, ids, times):
    """Archive each id on the backend and record an ``archive`` action at a
    controlled created_at, all under one batch_id."""
    for mid, t in zip(ids, times):
        clock.now = t
        prior_labels = list(backend.get_message(mid).get("labelIds", []))
        backend.archive_message(mid)
        action_store.record_action(
            db,
            action_type="archive",
            message_id=mid,
            payload={"prior_labels": prior_labels, "post_archive_id": mid},
            batch_id=batch_id,
            mailbox="google",
        )


def test_undo_window_anchored_to_batch_completion(tmp_path, monkeypatch):
    """All items of a bulk run stay undoable for the window AFTER the run
    completes — the earliest items no longer expire mid-run."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(action_store, "time", clock)

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)

    backend = FakeGmailBackend(user_email="me@example.com")
    ids = [f"m{i}" for i in range(5)]
    for mid in ids:
        backend.add_message(_inbox_message(mid, f"s{mid}@x.com"))

    batch_id = "batch2163"
    # Run spans 20s: ops at 1000,1005,1010,1015,1020 (completion = 1020).
    times = [1000.0, 1005.0, 1010.0, 1015.0, 1020.0]
    _record_batch_archive(db, backend, clock, batch_id, ids, times)

    window = 30

    # t = 1040: 40s after the FIRST op (past its own 30s window) but only 20s
    # after completion (1020). Under the old per-row logic rows at 1000 & 1005
    # would be dropped; anchored-to-completion keeps all five undoable.
    clock.now = 1040.0
    rows = action_store.fetch_batch_undoable(
        db, batch_id=batch_id, window_seconds=window
    )
    assert len(rows) == 5, f"all 5 must stay undoable at t=1040, got {len(rows)}"

    # End-to-end: undo restores every message to the inbox.
    result = undo_archive_batch_impl(
        lambda _row: backend, db, batch_id=batch_id, window_seconds=window
    )
    assert result["restored"] == 5
    for mid in ids:
        assert "INBOX" in backend.get_message(mid).get("labelIds", [])


def test_undo_window_still_expires_from_completion(tmp_path, monkeypatch):
    """The window is anchored to completion but still bounded — past
    completion + window the batch is no longer undoable (fails loud)."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(action_store, "time", clock)

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)

    backend = FakeGmailBackend(user_email="me@example.com")
    ids = [f"m{i}" for i in range(5)]
    for mid in ids:
        backend.add_message(_inbox_message(mid, f"s{mid}@x.com"))

    batch_id = "batch2163b"
    times = [1000.0, 1005.0, 1010.0, 1015.0, 1020.0]  # completion = 1020
    _record_batch_archive(db, backend, clock, batch_id, ids, times)

    window = 30
    # t = 1051: 31s after completion (1020) — the whole-batch window has elapsed.
    clock.now = 1051.0
    rows = action_store.fetch_batch_undoable(
        db, batch_id=batch_id, window_seconds=window
    )
    assert rows == [], "batch must be non-undoable past completion + window"

    with pytest.raises(RuntimeError):
        undo_archive_batch_impl(
            lambda _row: backend, db, batch_id=batch_id, window_seconds=window
        )
