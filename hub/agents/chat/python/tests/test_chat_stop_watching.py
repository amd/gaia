# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent.stop_watching closes its watcher handlers, on any supported core.

The chat agent pins ``amd-gaia>=0.25.0``; a core whose ``FileChangeHandler``
has no ``close()`` reindexes inline, so stop_watching must not require it.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent  # noqa: E402

from gaia.utils.file_watcher import FileChangeHandler  # noqa: E402


def _agent(*handlers):
    observer = MagicMock()
    return SimpleNamespace(observers=[observer], file_handlers=list(handlers))


def test_handlers_without_close_from_an_older_core_are_tolerated():
    class _OldCoreHandler:
        pass

    agent = _agent(_OldCoreHandler(), _OldCoreHandler())
    ChatAgent.stop_watching(agent)
    assert agent.file_handlers == []
    assert agent.observers == []


def test_every_handler_is_closed_once_and_forgotten():
    a, b = MagicMock(spec=["close"]), MagicMock(spec=["close"])
    agent = _agent(a, b)
    ChatAgent.stop_watching(agent)
    ChatAgent.stop_watching(agent)
    a.close.assert_called_once_with()
    b.close.assert_called_once_with()
    assert agent.file_handlers == []


def test_a_close_that_fails_is_not_swallowed():
    broken = MagicMock(spec=["close"])
    broken.close.side_effect = RuntimeError("close boom")
    agent = _agent(broken)
    with pytest.raises(RuntimeError, match="close boom"):
        ChatAgent.stop_watching(agent)


def test_a_mixed_list_still_closes_the_new_handler(tmp_path):
    class _OldCoreHandler:
        pass

    seen = []
    handler = FileChangeHandler(on_created=seen.append, debounce_seconds=0.05)
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")
    handler.on_created(SimpleNamespace(src_path=str(target), is_directory=False))
    assert handler.wait_idle(timeout=10)
    dispatcher = handler._dispatcher
    assert isinstance(dispatcher, threading.Thread) and dispatcher.is_alive()

    agent = _agent(_OldCoreHandler(), handler)
    ChatAgent.stop_watching(agent)

    dispatcher.join(timeout=5)
    assert not dispatcher.is_alive()
    assert seen == [str(target)]


def test_an_agent_that_never_ran_init_stops_without_error():
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    ChatAgent.stop_watching(agent)
