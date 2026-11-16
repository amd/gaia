# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A wait on the user, or a running tool, is not a turn going without progress.

The Agent UI stops a turn only after a window with no progress (#5177). A
question, a tool confirmation, or a long tool run is quiet by design and has its
own deadline, so the handler reports each one for the stream loop to discount.
"""

import threading
import time

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


def test_a_running_tool_is_reported_until_its_result():
    handler = SSEOutputHandler()
    assert handler.tool_running() is False
    handler.print_tool_usage("run_python")
    assert handler.tool_running() is True
    handler.pretty_print_json({"code": "print(1)"}, "Arguments")
    assert handler.tool_running() is True  # arguments are not the result
    handler.pretty_print_json({"status": "success"}, "Tool Result")
    assert handler.tool_running() is False


def test_a_tool_that_skips_its_result_still_ends_on_complete():
    handler = SSEOutputHandler()
    handler.print_tool_usage("run_python")
    handler.print_tool_complete()
    assert handler.tool_running() is False


def test_a_pending_tool_confirmation_counts_as_waiting_on_the_user():
    """A prompt waits on the user, so it never counts as no progress."""
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
