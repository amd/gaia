# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's stream timeout counts only silence: time with no agent
events, no tool running, and no question or prompt waiting on the user.

A long task that keeps making progress must run to the end (#5177).
"""

import threading
import time

from gaia.ui._chat_helpers import _SilenceClock, _stream_timeout_message
from gaia.ui.sse_handler import SSEOutputHandler


def test_a_pending_question_is_reported_until_it_is_answered():
    handler = SSEOutputHandler()
    answers = []
    asker = threading.Thread(
        target=lambda: answers.append(
            handler.request_user_input_blocking(
                message="Which repo?", timeout_seconds=30
            )
        ),
        daemon=True,
    )

    assert handler.awaiting_user_input() is False
    asker.start()
    deadline = time.monotonic() + 5
    request = None
    while request is None and time.monotonic() < deadline:
        event = handler.event_queue.get(timeout=5)
        if event and event.get("type") == "user_input_request":
            request = event
    assert request is not None
    assert handler.awaiting_user_input() is True

    assert handler.resolve_user_input(request["request_id"], "gaia")
    asker.join(timeout=5)

    assert answers == ["gaia"]
    assert handler.awaiting_user_input() is False


def test_the_clock_counts_only_silence():
    clock = _SilenceClock(100.0)

    assert clock.silent_for(False, 160.0) == 60.0
    clock.progressed(170.0)
    assert clock.silent_for(False, 200.0) == 30.0  # an event restarts it
    assert clock.silent_for(True, 900.0) == 0.0  # a tool run or user wait
    assert clock.silent_for(False, 950.0) == 50.0  # restarts once it ends


def test_a_running_tool_counts_as_expected_quiet():
    handler = SSEOutputHandler()
    assert handler.tool_running() is False
    handler.print_tool_usage("run_python")
    assert handler.tool_running() is True
    handler.pretty_print_json({"status": "success"}, "Result")
    assert handler.tool_running() is False


def test_the_timeout_message_says_why_in_plain_words():
    message = _stream_timeout_message(600)
    assert "no progress for 10 minutes" in message
    assert "stopped before it finished" not in message
    assert "made no progress for 45 seconds" in _stream_timeout_message(45)


def test_a_pending_tool_confirmation_counts_as_waiting_on_the_user():
    """A prompt can wait as long as the turn's budget; that wait is the user's."""
    handler = SSEOutputHandler()
    answers = []
    asker = threading.Thread(
        target=lambda: answers.append(
            handler.confirm_tool_execution("write_file", {"file_path": "a.txt"})
        ),
        daemon=True,
    )

    asker.start()
    deadline = time.monotonic() + 5
    while handler._confirm_id is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handler.awaiting_user_input() is True

    handler.resolve_tool_confirmation(approved=False)
    asker.join(timeout=5)

    assert answers == [False]
    assert handler.awaiting_user_input() is False
