# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for #2456 — the #2163 bulk-undo feature was unreachable in a
normal two-turn flow, for two independent reasons:

1. The default undo window (30 s) was shorter than a single Email-agent turn on
   Gemma-class hardware (40-140 s), so the batch had always expired by the time
   the user's NEXT turn reached ``undo_archive_batch``. This half is fixed by
   #2447 (default raised to 120 s, overridable via
   ``GAIA_EMAIL_UNDO_WINDOW_SECONDS``) and covered by
   ``test_undo_window_config_2447.py`` — not retested here.

2. A conversational "undo that" in the following turn asked the user for the
   internal batch uuid instead of recalling it. The agent now tracks the last
   archive ``batch_id`` per session and ``undo_archive_batch`` uses it when no id
   is supplied. This is the regression covered below.

Exercised against the real ``FakeGmailBackend`` and a controllable clock, so no
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
# Defect 2 — "undo that" with no explicit id restores the most recent archive
# batch of the session, and the id survives across a turn boundary.
# (Defect 1 — the undo window itself — is covered by
# ``test_undo_window_config_2447.py``.)
# ---------------------------------------------------------------------------


def test_undo_with_no_id_restores_last_batch_archive(tmp_path, monkeypatch):
    """archive_message_batch, then a next-turn undo_archive_batch() with NO id
    restores every message — the user never quotes the internal uuid."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(action_store, "time", clock)

    msgs = [_inbox_message(f"m{i}", f"s{i}@x.com") for i in range(3)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()
        ids = [m["id"] for m in msgs]
        arch = _call_tool("archive_message_batch", ids)
        assert arch["ok"] is True
        batch_id = arch["data"]["batch_id"]
        assert agent._last_archive_batch_id == batch_id

        # Simulate the user's NEXT turn: the per-turn handle is re-minted, but
        # the session's last-archive handle must survive.
        agent._reset_organize_counter()
        assert agent._organize_batch_id != batch_id
        assert agent._last_archive_batch_id == batch_id

        undo = _call_tool("undo_archive_batch")  # no id — "undo that"
        assert undo["ok"] is True, f"id-less undo should succeed: {undo}"
        assert undo["data"]["restored"] == 3
        for mid in ids:
            labels = backend.get_message(mid).get("labelIds", [])
            assert "INBOX" in labels, f"{mid} not restored: {labels}"
    finally:
        agent.close_db()


def test_undo_with_no_id_restores_single_archive_loop(tmp_path, monkeypatch):
    """A loop of single archive_message calls is also recalled by an id-less
    undo in the following turn."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(action_store, "time", clock)

    msgs = [_inbox_message(f"m{i}", "promo@shop.com") for i in range(4)]
    agent, backend = _build_agent_with_fake_gmail(tmp_path, msgs)
    try:
        agent._reset_organize_counter()
        for m in msgs:
            assert _call_tool("archive_message", m["id"])["ok"] is True
        assert agent._last_archive_batch_id == agent._organize_batch_id

        agent._reset_organize_counter()  # next turn
        undo = _call_tool("undo_archive_batch")
        assert undo["ok"] is True
        assert undo["data"]["restored"] == 4
        for m in msgs:
            assert "INBOX" in backend.get_message(m["id"]).get("labelIds", [])
    finally:
        agent.close_db()


def test_undo_with_no_id_and_no_prior_archive_errors(tmp_path):
    """With nothing archived this session, an id-less undo returns an actionable
    error rather than a silent no-op or a raw uuid demand."""
    agent, _ = _build_agent_with_fake_gmail(tmp_path, [])
    try:
        assert agent._last_archive_batch_id is None
        res = _call_tool("undo_archive_batch")
        assert res["ok"] is False
        assert "archive" in res["error"].lower()
    finally:
        agent.close_db()
