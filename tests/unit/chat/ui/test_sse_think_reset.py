# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply cut off inside its reasoning must not colour the next reply."""

from gaia.ui.sse_handler import SSEOutputHandler


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def test_a_reply_cut_off_mid_reasoning_does_not_make_the_next_one_reasoning():
    handler = SSEOutputHandler()

    handler.print_streaming_text("<think>weighing the options", end_of_stream=True)
    assert handler._in_thinking is False
    _drain(handler)

    handler.print_streaming_text("The answer is four.", end_of_stream=True)
    events = [e for e in _drain(handler) if e]

    assert any(
        e.get("type") == "chunk" and "four" in (e.get("content") or "") for e in events
    ), events
    assert not any(e.get("type") == "thinking" for e in events), events
