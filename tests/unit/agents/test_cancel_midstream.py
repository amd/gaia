# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mid-generation cancel for the Agent-UI streaming path (#2157).

The Agent UI injects its ``SSEOutputHandler`` as ``agent.console`` and sets
``console.cancelled`` (a ``threading.Event``) when the user hits Stop. Before
#2157 the flag was only read at step boundaries, so a single-shot chat/RAG
answer generated to completion and was persisted even after the cancel. These
tests assert ``process_query`` now observes the flag per streamed token and
ends the turn with empty text (``status == "cancelled"``) so it never
rehydrates as a completed answer — and that a console without a ``cancelled``
attribute (non-UI usage) is unaffected.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent — same pattern as test_loop_break_truthful."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _chunk(text: str, is_complete: bool = False):
    return SimpleNamespace(text=text, is_complete=is_complete, stats={})


class _FakeStream:
    """Iterable LLM stream that fires a cancel event mid-iteration.

    Records ``close()`` so the test can assert the upstream stream is torn
    down when the cancel is observed.
    """

    def __init__(self, chunks, cancel_event, cancel_after):
        self._chunks = chunks
        self._cancel_event = cancel_event
        self._cancel_after = cancel_after
        self._i = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._chunks):
            raise StopIteration
        chunk = self._chunks[self._i]
        self._i += 1
        # Simulate the user hitting Stop after this token is produced.
        if self._i == self._cancel_after:
            self._cancel_event.set()
        return chunk

    def close(self):
        self.closed = True


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True, streaming=True)
    # Console the UI would inject: a MagicMock (no-op prints) carrying a real
    # cancelled Event so _console_cancelled() reads a genuine flag.
    console = MagicMock()
    console.cancelled = threading.Event()
    a.console = console
    return a


def test_cancel_midstream_discards_streamed_answer(agent):
    """A cancel during token streaming ends the turn empty, not with the answer."""
    stream = _FakeStream(
        [
            _chunk("Partial answer "),
            _chunk("should not be seen"),
            _chunk("", is_complete=True),
        ],
        agent.console.cancelled,
        cancel_after=1,  # cancel right after the first token
    )
    agent.chat.send_messages_stream = MagicMock(return_value=stream)

    result = agent.process_query("hello")

    assert result["status"] == "cancelled"
    assert result["result"] == ""
    # Upstream Lemonade stream was closed on cancel.
    assert stream.closed is True


def test_cancel_during_blocking_generation_discards_full_answer(agent):
    """Tool-calling models generate non-streaming (one complete chunk).

    A cancel signalled while that blocking call is in flight must still
    discard the full answer rather than persist it.
    """
    stream = _FakeStream(
        [_chunk("The complete answer.", is_complete=True)],
        agent.console.cancelled,
        cancel_after=1,  # cancel arrives as the blocking call returns
    )
    agent.chat.send_messages_stream = MagicMock(return_value=stream)

    result = agent.process_query("hello")

    assert result["status"] == "cancelled"
    assert result["result"] == ""
    assert stream.closed is True


def test_non_ui_console_without_cancelled_attr_is_unaffected(agent):
    """A console lacking a ``cancelled`` attribute must not trip the check."""
    # AgentConsole has no ``cancelled`` attribute — the non-UI case.
    from gaia.agents.base.console import AgentConsole

    agent.console = AgentConsole()
    assert agent._console_cancelled() is False
