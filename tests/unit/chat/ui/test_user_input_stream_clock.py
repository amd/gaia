# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time spent waiting on a request_user_input answer does not count against
the Agent UI's per-turn stream timeout.

Without this, a question asked late in a long turn got only what was left of
the turn's 600 s, and running out was reported as "try a simpler query".
"""

import threading
import time

from gaia.ui._chat_helpers import _UserWaitClock
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


def test_the_clock_counts_only_time_spent_waiting_on_the_user():
    clock = _UserWaitClock()

    assert clock.update(False, 100.0) == 0.0
    assert clock.update(True, 120.0) == 0.0
    assert clock.update(True, 300.0) == 180.0  # an open wait counts as it runs
    assert clock.update(False, 420.0) == 300.0
    assert clock.update(False, 500.0) == 300.0  # the turn's own time does not
    assert clock.update(True, 510.0) == 300.0
    assert clock.update(False, 520.0) == 310.0
