# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A batch change the mailbox applied is never reported as failed (#6994).

When saving the undo record fails after Gmail/Outlook already applied the
change, the message is reported as applied but not undoable — not as failed.
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

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.ui.sse_handler import tool_result_failed  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _inbox_message(message_id: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1700000000000",
        "snippet": "promo",
        "payload": {
            "headers": [
                {"name": "From", "value": "Deals <deals@x.com>"},
                {"name": "Subject", "value": "50% off"},
            ],
        },
    }


@pytest.fixture
def agent(tmp_path):
    backend = FakeGmailBackend(user_email="me@example.com")
    for mid in ("m1", "m2"):
        backend.add_message(_inbox_message(mid))
    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=object(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    vec = np.ones(768, dtype=np.float32) / np.sqrt(768)
    with (
        patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()),
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch("gaia.agents.base.memory.MemoryMixin._embed_text", return_value=vec),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        built = EmailTriageAgent(config=cfg)
    built._reset_organize_counter()
    yield built, backend
    built.close_db()


def _call(name: str, *args) -> tuple[dict, str]:
    raw = _TOOL_REGISTRY[name]["function"](*args)
    return json.loads(raw), raw


_RECORD = "gaia_agent_email.tools.organize_tools.action_store.record_action"


@pytest.mark.parametrize(
    "name, extra",
    [
        ("mark_read_batch", ()),
        ("archive_message_batch", ()),
    ],
)
def test_applied_change_with_failed_undo_record_is_not_reported_failed(
    agent, name, extra
):
    built, _ = agent
    built._reset_organize_counter()
    with patch(_RECORD, side_effect=RuntimeError("database is locked")):
        body, raw = _call(name, ["m1", "m2"], *extra)

    assert body["ok"] is True, body
    assert not tool_result_failed(raw)
    data = body["data"]
    assert data["failed_count"] == 0
    assert data["succeeded_count"] == 2
    assert "partial" not in data
    assert data["not_undoable_count"] == 2
    assert all(s["action_id"] is None for s in data["succeeded"])
    assert all("database is locked" in s["undo_error"] for s in data["succeeded"])
    assert "cannot be undone" in data["summary"]


def test_archive_change_still_lands_in_the_mailbox(agent):
    built, backend = agent
    with patch(_RECORD, side_effect=RuntimeError("disk full")):
        _call("archive_message_batch", ["m1"])

    assert "INBOX" not in backend.get_message("m1").get("labelIds", [])


def test_real_failure_and_unrecorded_success_are_reported_separately(agent):
    built, _ = agent
    with patch(_RECORD, side_effect=RuntimeError("disk full")):
        body, _ = _call("mark_read_batch", ["m1", "gone"])

    data = body["data"]
    assert body["ok"] is True
    assert data["partial"] is True
    assert (data["succeeded_count"], data["failed_count"]) == (1, 1)
    assert data["failed"][0]["message_id"] == "gone"
    assert data["not_undoable_count"] == 1
    assert data["summary"].startswith("Could not mark as read 1 of 2 messages")
    assert "m1" in data["summary"]


def test_recorded_successes_carry_no_undo_warning(agent):
    body, _ = _call("mark_read_batch", ["m1", "m2"])
    data = body["data"]

    assert "not_undoable_count" not in data
    assert all(s["action_id"] for s in data["succeeded"])
