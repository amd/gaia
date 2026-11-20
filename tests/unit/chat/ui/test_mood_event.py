# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The model's mood crosses every hop to the Agent UI as one ``mood`` event."""

from gaia.ui.sidecar import relay
from gaia.ui.sidecar.profiles import GAIA_PROFILE
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


def _events(handler):
    out = []
    while not handler.event_queue.empty():
        out.append(handler.event_queue.get_nowait())
    return out


def test_an_in_process_turn_emits_the_mood():
    handler = SSEOutputHandler()
    handler.print_mood("curious")
    assert _events(handler) == [{"type": "mood", "mood": "curious"}]


def test_the_sidecar_stream_carries_it_unchanged():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    assert translator.translate({"type": "mood", "mood": "sad"}) == [
        {"type": "mood", "mood": "sad"}
    ]


def test_the_relay_hands_it_to_the_agent_ui():
    handler = SSEOutputHandler()
    relay._dispatch_one(handler, {"type": "mood", "mood": "proud"}, GAIA_PROFILE)
    assert _events(handler) == [{"type": "mood", "mood": "proud"}]
