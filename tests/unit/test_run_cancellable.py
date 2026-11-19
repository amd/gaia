# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A blocking call a tool can walk away from when the user presses Stop."""

import threading
import time

import pytest

from gaia.tool_cancellation import (
    ToolCancelled,
    run_cancellable,
    set_tool_cancel_event,
)


@pytest.fixture
def cancel():
    event = threading.Event()
    set_tool_cancel_event(event)
    yield event
    set_tool_cancel_event(None)


def test_with_no_cancellation_it_is_a_plain_call():
    assert (
        run_cancellable(lambda: threading.current_thread())
        is threading.current_thread()
    )


def test_the_result_comes_back(cancel):
    assert run_cancellable(lambda: 42) == 42


def test_an_error_comes_back_as_itself(cancel):
    def fail():
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        run_cancellable(fail)


def test_stop_abandons_the_call_at_once(cancel):
    release = threading.Event()

    def blocked():
        cancel.set()
        release.wait(30)

    started = time.monotonic()
    try:
        with pytest.raises(ToolCancelled):
            run_cancellable(blocked)
    finally:
        release.set()

    assert time.monotonic() - started < 5
