# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The speech model's download reaches the Agent UI and stays in the turn.

The tool reported it as a status with no phase. Relayed from the sidecar, a
status without a phase never reaches the live line, so the window kept showing
"Audio ready" for the whole download.
"""

from __future__ import annotations

import pytest

from gaia.agents.tools.audio_tools import (
    SPEECH_MODEL_DOWNLOAD_PHASE,
    SPEECH_MODEL_DOWNLOADED_PHASE,
)
from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

from .test_empty_answer_tail import _run, runtime  # noqa: F401  (fixture)

_RUNNING = "Downloading the speech model, one time — 37% of 1.6 GB"
_DONE = "Downloaded the speech model (1.6 GB)"


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _over_the_wire(phase, message):
    """One phased status from the tool's console, as the Agent UI receives it."""
    source = SSEOutputHandler()
    source.report_phase(phase, message)
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    [wire] = translator.translate(_drain(source)[0])
    handler = SSEOutputHandler()
    relay._dispatch_one(handler, wire, GAIA_PROFILE)
    [event] = _drain(handler)
    return wire, event


def test_the_download_line_keeps_its_phase_through_the_sidecar_relay():
    wire, event = _over_the_wire(SPEECH_MODEL_DOWNLOAD_PHASE, _RUNNING)

    assert wire == {
        "type": "status",
        "message": _RUNNING,
        "phase": SPEECH_MODEL_DOWNLOAD_PHASE,
    }
    assert event == {
        "type": "status",
        "message": _RUNNING,
        "phase": SPEECH_MODEL_DOWNLOAD_PHASE,
    }


def test_every_percent_is_relayed_not_only_the_first():
    source = SSEOutputHandler()
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    for percent in (1, 2, 3):
        source.report_phase(
            SPEECH_MODEL_DOWNLOAD_PHASE,
            f"Downloading the speech model, one time — {percent}% of 1.6 GB",
        )
    wire = [out for event in _drain(source) for out in translator.translate(event)]
    assert [e["message"][-12:] for e in wire] == [
        "1% of 1.6 GB",
        "2% of 1.6 GB",
        "3% of 1.6 GB",
    ]


def test_the_finished_download_is_a_recorded_phase():
    assert SPEECH_MODEL_DOWNLOADED_PHASE in helpers._RECORDED_PHASES
    assert SPEECH_MODEL_DOWNLOAD_PHASE not in helpers._RECORDED_PHASES


@pytest.mark.asyncio
async def test_the_finished_download_is_saved_with_the_turns_steps(
    runtime,  # noqa: F811
):
    tool = {"type": "tool_start", "tool": "transcribe_media", "detail": "standup.wav"}
    _, running = _over_the_wire(SPEECH_MODEL_DOWNLOAD_PHASE, _RUNNING)
    _, done = _over_the_wire(SPEECH_MODEL_DOWNLOADED_PHASE, _DONE)
    result = {"type": "tool_result", "title": "Transcribed", "success": True}

    sid, _events = await _run(runtime, [tool, running, done, result])

    steps = runtime.get_messages(sid)[-1]["agent_steps"]
    assert [(s["type"], s["label"], s.get("success")) for s in steps] == [
        ("tool", "Using transcribe_media", True),
        ("status", _DONE, True),
    ]
    assert not any(s["active"] for s in steps)
