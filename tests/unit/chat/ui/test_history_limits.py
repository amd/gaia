# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Verify the history-pair and message-char limits applied in _chat_helpers.

These tests exercise the path that loads previous messages from the DB and
injects them into the agent's conversation_history.  They are deliberately
isolated from network / LLM dependencies.

Tests cover BOTH the synchronous (_get_chat_response) path and verify the
constants embedded in _stream_chat_response via a source-code grep so we
don't need to spin up a thread to catch them.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_messages(n_pairs: int, msg_len: int = 10) -> list:
    """Return a flat list of n_pairs user/assistant message dicts."""
    msgs = []
    for i in range(n_pairs):
        msgs.append({"role": "user", "content": f"Q{i}" * msg_len})
        msgs.append({"role": "assistant", "content": f"A{i}" * msg_len})
    return msgs


def _make_mock_db(messages: list, session_id: str = "sess-1") -> MagicMock:
    db = MagicMock()
    db.get_messages.return_value = messages
    db.get_session.return_value = {"session_id": session_id, "document_ids": []}
    db.list_documents.return_value = []
    return db


def _run_sync(coro):
    """Run a coroutine synchronously in a fresh event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── non-streaming path: _get_chat_response ────────────────────────────────────


class TestNonStreamingHistoryLimits:
    """Tests for _get_chat_response (synchronous / non-streaming mode)."""

    def _call_get_chat_response(self, messages, request_message="Hello"):
        """Invoke _get_chat_response with mocked dependencies.

        Returns the conversation_history that was injected into the agent.
        """
        from gaia.ui._chat_helpers import _get_chat_response
        from gaia.ui.models import ChatRequest

        captured_history = []

        class FakeAgent:
            conversation_history = []

            def process_query(self, msg):
                # Capture the history at call time
                captured_history.extend(self.conversation_history)
                return {"result": "ok"}

        request = ChatRequest(
            session_id="sess-1",
            message=request_message,
            stream=False,
        )

        db = _make_mock_db(messages)
        session = {"document_ids": [], "model": None}

        # ChatAgent/ChatAgentConfig are lazy-imported inside _do_chat(), so
        # patch them at their source module (gaia.agents.chat.agent) which
        # is the target of "from gaia.agents.chat.agent import ChatAgent, ..."
        with patch(
            "gaia.agents.chat.agent.ChatAgent", return_value=FakeAgent()
        ), patch("gaia.agents.chat.agent.ChatAgentConfig"):
            _run_sync(_get_chat_response(db, session, request))

        return captured_history

    def test_five_pairs_maximum_is_respected(self):
        """With 7 DB pairs only the most recent 5 should reach the agent."""
        messages = _make_messages(7)  # 7 pairs = 14 messages
        history = self._call_get_chat_response(messages)

        # 5 pairs = 10 injected messages
        assert len(history) == 10, f"Expected 10, got {len(history)}: {history}"

    def test_fewer_than_five_pairs_all_included(self):
        """With only 3 DB pairs all 3 should be injected (no truncation needed)."""
        messages = _make_messages(3)
        history = self._call_get_chat_response(messages)
        assert len(history) == 6, f"Expected 6, got {len(history)}"

    def test_exactly_five_pairs_all_included(self):
        """Boundary: exactly 5 pairs should all be included."""
        messages = _make_messages(5)
        history = self._call_get_chat_response(messages)
        assert len(history) == 10

    def test_message_truncated_at_2000_chars(self):
        """Messages longer than 2000 chars should be clipped to 2000."""
        long_msg = "x" * 5000
        messages = [
            {"role": "user", "content": long_msg},
            {"role": "assistant", "content": long_msg},
        ]
        history = self._call_get_chat_response(messages)

        assert len(history) == 2
        for entry in history:
            assert len(entry["content"]) <= 2000 + len(
                "... (truncated)"
            ), f"Content too long: {len(entry['content'])}"

    def test_short_messages_not_truncated(self):
        """Messages under 2000 chars should be passed through intact."""
        short_msg = "Hello world"
        messages = [
            {"role": "user", "content": short_msg},
            {"role": "assistant", "content": short_msg},
        ]
        history = self._call_get_chat_response(messages)
        assert history[0]["content"] == short_msg
        assert history[1]["content"] == short_msg

    def test_truncation_suffix_added(self):
        """A '... (truncated)' suffix should be appended to clipped assistant msgs."""
        long_msg = "y" * 3000
        messages = [
            {"role": "user", "content": long_msg},
            {"role": "assistant", "content": long_msg},
        ]
        history = self._call_get_chat_response(messages)
        assistant_entry = next(e for e in history if e["role"] == "assistant")
        assert assistant_entry["content"].endswith("... (truncated)")

    def test_most_recent_pairs_are_kept(self):
        """When truncating to 5 pairs, the NEWEST pairs should survive."""
        # Build 7 pairs with distinguishable content
        messages = []
        for i in range(7):
            messages.append({"role": "user", "content": f"USER_{i}"})
            messages.append({"role": "assistant", "content": f"ASST_{i}"})

        history = self._call_get_chat_response(messages)

        # Oldest two pairs (USER_0/ASST_0, USER_1/ASST_1) should be gone
        contents = [e["content"] for e in history]
        assert "USER_0" not in contents
        assert "USER_1" not in contents
        # Most recent pair should be present
        assert "USER_6" in contents
        assert "ASST_6" in contents

    def test_empty_history_injects_nothing(self):
        """No previous messages → empty conversation_history."""
        history = self._call_get_chat_response([])
        assert history == []


# ── source-code check: streaming path constants ───────────────────────────────


class TestStreamingPathConstants:
    """Verify the constants in _stream_chat_response by reading the source."""

    def _source(self):
        path = (
            Path(__file__).resolve().parents[4]
            / "src"
            / "gaia"
            / "ui"
            / "_chat_helpers.py"
        )
        return path.read_text(encoding="utf-8")

    def test_max_history_pairs_is_5(self):
        src = self._source()
        # Should contain "_MAX_HISTORY_PAIRS = 5" (not 2)
        assert "_MAX_HISTORY_PAIRS = 5" in src, (
            "Streaming path: _MAX_HISTORY_PAIRS should be 5. "
            "Found in source: "
            + str(re.findall(r"_MAX_HISTORY_PAIRS\s*=\s*\d+", src))
        )

    def test_max_msg_chars_is_2000(self):
        src = self._source()
        # Should contain "_MAX_MSG_CHARS = 2000" (not 500)
        assert "_MAX_MSG_CHARS = 2000" in src, (
            "Streaming path: _MAX_MSG_CHARS should be 2000. "
            "Found in source: "
            + str(re.findall(r"_MAX_MSG_CHARS\s*=\s*\d+", src))
        )

    def test_old_value_2_not_present_for_history_pairs(self):
        src = self._source()
        old_occurrences = re.findall(r"_MAX_HISTORY_PAIRS\s*=\s*2\b", src)
        assert not old_occurrences, (
            f"Stale _MAX_HISTORY_PAIRS = 2 still present: {old_occurrences}"
        )

    def test_old_value_500_not_present_for_msg_chars(self):
        src = self._source()
        old_occurrences = re.findall(r"_MAX_MSG_CHARS\s*=\s*500\b", src)
        assert not old_occurrences, (
            f"Stale _MAX_MSG_CHARS = 500 still present: {old_occurrences}"
        )

    def test_non_streaming_max_pairs_is_5(self):
        src = self._source()
        # Non-streaming uses _MAX_PAIRS (different name)
        assert "_MAX_PAIRS = 5" in src, (
            "Non-streaming path: _MAX_PAIRS should be 5. "
            "Found: " + str(re.findall(r"_MAX_PAIRS\s*=\s*\d+", src))
        )

    def test_non_streaming_max_chars_is_2000(self):
        src = self._source()
        assert "_MAX_CHARS = 2000" in src, (
            "Non-streaming path: _MAX_CHARS should be 2000. "
            "Found: " + str(re.findall(r"_MAX_CHARS\s*=\s*\d+", src))
        )
