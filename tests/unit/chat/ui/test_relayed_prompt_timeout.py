# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed permission prompt shows its time limit and times out on it.

The Agent UI relays the flagship to its sidecar, whose own bound is a 15-minute
backstop. The UI used to drop the limit from the prompt, so the card showed no
countdown and the question waited past the 10 minutes an in-process turn gives.
"""

from __future__ import annotations

import threading
import time

from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.sse_handler import SSEOutputHandler


class _Proxy:
    def __init__(self) -> None:
        self.decisions: list = []
        self.sent = threading.Event()

    def tool_decision(self, run_id: str, decision: str, confirm_id: str) -> dict:
        self.decisions.append((run_id, decision, confirm_id))
        self.sent.set()
        return {}


def _relayed_handler(timeout: float) -> "tuple[SSEOutputHandler, _Proxy]":
    handler = SSEOutputHandler()
    handler.confirm_timeout_seconds = timeout
    proxy = _Proxy()
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = "run-1"
    return handler, proxy


def _relay_prompt(handler: SSEOutputHandler, confirm_id: str = "c-1") -> dict:
    relay._dispatch_one(
        handler,
        {
            "type": "needs_confirmation",
            "action": "run_shell_command",
            "summary": "ls",
            "confirm_id": confirm_id,
            "args": {"command": "ls"},
        },
        GAIA_PROFILE,
        answers_confirmations=True,
    )
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    (prompt,) = [e for e in events if e["type"] == "permission_request"]
    return prompt


def test_a_relayed_prompt_carries_the_ui_time_limit_and_its_deadline():
    handler, _ = _relayed_handler(600)
    before = time.time()

    prompt = _relay_prompt(handler)

    assert prompt["timeout_seconds"] == 600
    assert before + 600 <= prompt["expires_at"] <= time.time() + 600
    handler.set_relay_confirmation(None)


def test_an_unanswered_relayed_prompt_tells_the_sidecar_it_timed_out():
    handler, proxy = _relayed_handler(0.05)

    _relay_prompt(handler, "c-7")

    assert proxy.sent.wait(5)
    assert proxy.decisions == [("run-1", "timeout", "c-7")]
    assert not handler.relay_confirmation_pending()


def test_an_answered_relayed_prompt_never_times_out():
    handler, proxy = _relayed_handler(0.05)
    _relay_prompt(handler, "c-8")

    assert handler.resolve_relay_confirmation(True, "c-8")
    time.sleep(0.2)

    assert proxy.decisions == [("run-1", "allow", "c-8")]


def test_an_in_process_prompt_carries_its_deadline():
    handler = SSEOutputHandler()
    handler.confirm_timeout_seconds = 0.05
    before = time.time()

    assert (
        handler.confirm_tool_execution("run_shell_command", {"command": "ls"}) is False
    )

    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    (prompt,) = [e for e in events if e["type"] == "permission_request"]
    assert prompt["timeout_seconds"] == 0.05
    assert before < prompt["expires_at"] <= time.time() + 0.05
