# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for #2163 — two defects in bulk archive:

1. The bulk-archive confirmation GATE let pre-threshold operations execute
   UNCONFIRMED. ``archive_message_batch`` checked the per-turn organize counter
   BEFORE its own ops were counted, so a fresh ``archive_message_batch([6 ids])``
   saw count == 0, passed the gate, and archived all six with no confirmation.

2. Undo windows EXPIRED MID-RUN. ``fetch_batch_undoable`` measured the window
   from each row's own ``created_at``; in a multi-item run the earliest items
   crossed the window before the run finished, so the closing "undo within the
   window" offer was already false for them.

Both are exercised against the real ``FakeGmailBackend`` and a controllable
clock (the ``action_store`` module's ``time`` is swapped for a settable fake),
so no wall-clock timing and no live mailbox are involved.
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
        patch("gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0),
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
# Defect 1 — gate blocks pre-threshold bulk archive (AC a)
# ---------------------------------------------------------------------------


def test_bulk_archive_gate_blocks_before_any_op_executes(tmp_path):
    """archive_message_batch([6 ids]) on a fresh turn must archive NOTHING and
    return the batch-confirm sentinel — the plan-level gate fires before any op."""
    msgs = [_inbox_message(f"m{i}", f"s{i}@x.com") for i in range(6)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()  # fresh turn
        ids = [m["id"] for m in msgs]
        res = _call_tool("archive_message_batch", ids)

        # Gate tripped: error envelope carrying the batch-confirm sentinel.
        assert res["ok"] is False, f"expected gate to block, got {res}"
        assert "Batch threshold exceeded" in res["error"]

        # NOTHING archived — every message still in INBOX.
        for mid in ids:
            labels = backend.get_message(mid).get("labelIds", [])
            assert "INBOX" in labels, f"{mid} was archived unconfirmed: {labels}"
    finally:
        agent.close_db()


def test_below_threshold_batch_still_archives(tmp_path):
    """A below-threshold batch is NOT gated — no over-gating regression."""
    msgs = [_inbox_message(f"m{i}", f"s{i}@x.com") for i in range(4)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()
        ids = [m["id"] for m in msgs]
        res = _call_tool("archive_message_batch", ids)

        assert res["ok"] is True, f"below-threshold batch should run: {res}"
        assert res["data"]["total"] == 4
        for mid in ids:
            labels = backend.get_message(mid).get("labelIds", [])
            assert "INBOX" not in labels, f"{mid} not archived: {labels}"
    finally:
        agent.close_db()


# ---------------------------------------------------------------------------
# Defect 2 — undo window survives the whole run (AC b)
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
    rows = action_store.fetch_batch_undoable(db, batch_id=batch_id, window_seconds=window)
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
    rows = action_store.fetch_batch_undoable(db, batch_id=batch_id, window_seconds=window)
    assert rows == [], "batch must be non-undoable past completion + window"

    with pytest.raises(RuntimeError):
        undo_archive_batch_impl(
            lambda _row: backend, db, batch_id=batch_id, window_seconds=window
        )
