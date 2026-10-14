# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A batch organize that changed nothing is a failure, not ``ok: true`` (#5111).

Every batch tool returned ``ok: true`` with the failures tucked into a list, so
the agent and the Agent UI read "0 of 3 archived" as done. All failed is now an
error envelope; some failed stays ``ok`` but says how many and why.
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
from gaia_agent_email.tools.organize_tools import _batch_envelope  # noqa: E402

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
    yield built
    built.close_db()


def _call(name: str, *args) -> tuple[dict, str]:
    raw = _TOOL_REGISTRY[name]["function"](*args)
    return json.loads(raw), raw


def test_every_message_failing_is_an_error(agent):
    body, raw = _call("archive_message_batch", ["gone1", "gone2", "gone3"])
    assert body["ok"] is False, body
    assert "Could not archive 3 of 3 messages" in body["error"]
    assert "gone1" in body["error"]
    assert body["data"]["failed_count"] == 3
    assert body["data"]["succeeded_count"] == 0
    assert agent._last_archive_batch_id is None, "nothing archived, nothing to undo"
    assert tool_result_failed(raw), "the Agent UI must draw this step as failed"


def test_some_messages_failing_is_a_partial_success(agent):
    body, raw = _call("archive_message_batch", ["m1", "m2", "gone"])
    assert body["ok"] is True, body
    data = body["data"]
    assert data["partial"] is True
    assert (data["succeeded_count"], data["failed_count"]) == (2, 1)
    assert data["summary"].startswith("Could not archive 1 of 3 messages: gone:")
    assert agent._last_archive_batch_id == data["batch_id"]
    assert not tool_result_failed(raw)


def test_every_message_succeeding_has_no_failure_summary(agent):
    body, _ = _call("mark_read_batch", ["m1", "m2"])
    assert body["ok"] is True
    assert body["data"]["failed_count"] == 0
    assert "summary" not in body["data"]
    assert "partial" not in body["data"]


def test_batch_envelope_caps_the_reasons_it_lists():
    failed = [{"message_id": f"x{i}", "error": "KeyError: gone"} for i in range(5)]
    body = json.loads(
        _batch_envelope("star", "b1", [f["message_id"] for f in failed], {
            "succeeded": [],
            "failed": failed,
        })
    )
    assert body["ok"] is False
    assert body["error"].endswith("(and 2 more)")
    assert len(body["data"]["failed"]) == 5
