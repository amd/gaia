# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Closing an agent stops the MCP servers it started.

Each MCP server is a process tree (``uvx`` → ``uv`` → the server). The Agent UI
builds agents it never caches — the new-chat warm-up, scheduled runs, goal
ticks — and closes them; their servers kept running, about ten processes per
new chat.

``close()`` stops them and waits. The garbage collector's ``__del__`` may run on
the server's event loop, so there the stop runs on its own thread instead of
blocking it.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaiapy")

from gaiapy.agent import GaiaAgent  # noqa: E402
from gaiapy.chat_agent import ChatAgent  # noqa: E402


def _manager(stop=None):
    manager = MagicMock()
    manager._clients = {"git": object()}

    def _disconnect_all():
        if stop is not None:
            stop()
        manager._clients = {}

    manager.disconnect_all.side_effect = _disconnect_all
    return manager


def _agent(cls, manager):
    agent = cls.__new__(cls)
    agent.observers = []
    agent._mcp_manager = manager
    return agent


def test_close_stops_every_mcp_server_before_it_returns():
    manager = _manager()
    agent = _agent(GaiaAgent, manager)
    agent.close()
    manager.disconnect_all.assert_called_once_with()
    assert manager._clients == {}


def test_the_finalizer_stops_servers_without_blocking_its_thread():
    released = threading.Event()
    stopped = threading.Event()

    def _slow_stop():
        released.wait(5)  # a server slow to exit
        stopped.set()

    manager = _manager(stop=_slow_stop)
    agent = _agent(ChatAgent, manager)
    started = time.monotonic()
    ChatAgent.__del__(agent)
    assert time.monotonic() - started < 1  # the event loop is not held
    released.set()
    assert stopped.wait(5)  # but the servers are still stopped


def test_close_without_waiting_returns_before_slow_servers_exit():
    released = threading.Event()
    stopped = threading.Event()

    def _slow_stop():
        released.wait(5)
        stopped.set()

    manager = _manager(stop=_slow_stop)
    agent = _agent(GaiaAgent, manager)
    started = time.monotonic()
    agent.close(wait_for_mcp=False)
    assert time.monotonic() - started < 1
    released.set()
    assert stopped.wait(5)
    manager.disconnect_all.assert_called_once_with()


def test_after_close_the_finalizer_has_nothing_left_to_stop():
    manager = _manager()
    agent = _agent(GaiaAgent, manager)
    agent.close()
    ChatAgent.__del__(agent)
    manager.disconnect_all.assert_called_once_with()


def test_an_agent_without_mcp_closes_cleanly():
    ChatAgent.__del__(_agent(ChatAgent, None))


def test_a_failed_disconnect_does_not_stop_the_rest_of_close():
    manager = MagicMock()
    manager.disconnect_all.side_effect = RuntimeError("server already gone")
    agent = _agent(GaiaAgent, manager)
    agent._fs_index = MagicMock()
    agent.close()
    agent._fs_index.close_db.assert_called_once_with()
