# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""request_user_input waits for the user, and the tool watchdog must outlast it.

Its default wait (300 s) was longer than the default tool cap (180 s), so the
agent abandoned every unanswered question as "may be hung" and an answer
typed after three minutes went to a worker nobody was waiting on.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY

gaia_agent_chat = pytest.importorskip("gaia_agent_chat")


class _Console:
    def __init__(self):
        self.asked = []

    def request_user_input_blocking(self, **kwargs):
        self.asked.append(kwargs)
        return "__NO_RESPONSE__"


@pytest.fixture
def ask():
    from gaia_agent_chat.agent import ChatAgent

    stub = object.__new__(ChatAgent)
    stub.console = _Console()
    stub.observers = []
    saved = dict(_TOOL_REGISTRY)
    try:
        ChatAgent._register_loop_control_tools(stub)
        entry = _TOOL_REGISTRY["request_user_input"]
        yield entry, stub.console
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_watchdog_outlasts_the_longest_wait(ask):
    from gaia_agent_chat.agent import USER_INPUT_MAX_WAIT_S

    from gaia.agents.base.agent import tool_execution_timeout

    entry, _console = ask

    assert entry["timeout"] > USER_INPUT_MAX_WAIT_S
    assert entry["timeout"] > tool_execution_timeout()


@pytest.mark.parametrize("asked,waited", [(30, 30), (300, 300), (86400, 600)])
def test_a_wait_is_capped_below_the_watchdog(ask, asked, waited):
    entry, console = ask

    assert entry["function"](message="Which repo?", timeout_seconds=asked) == (
        "__NO_RESPONSE__"
    )

    assert console.asked[-1]["timeout_seconds"] == waited
