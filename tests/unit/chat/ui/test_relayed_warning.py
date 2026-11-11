# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A warning the GAIA sidecar raises reaches the Agent UI as a warning.

Over the relay it used to arrive as a plain status line, which the Agent UI
only uses for the live line, so a notice such as "these MCP servers were
dropped" never appeared in the window.
"""

from __future__ import annotations

from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

_NOTICE = "MCP servers not loaded: weatherbox. Loading them failed: bad JSON"


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def test_the_sidecar_keeps_a_warnings_level_on_the_wire():
    source = SSEOutputHandler()
    source.print_warning(_NOTICE)
    [event] = _drain(source)

    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    assert translator.translate(event) == [
        {"type": "status", "message": _NOTICE, "level": "warning"}
    ]


def test_a_progress_line_carries_no_level():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    [out] = translator.translate(
        {"type": "status", "status": "working", "message": "Reading your request"}
    )
    assert "level" not in out


def test_the_agent_ui_shows_a_relayed_warning_as_a_warning():
    handler = SSEOutputHandler()
    relay._dispatch_one(
        handler,
        {"type": "status", "message": _NOTICE, "level": "warning"},
        GAIA_PROFILE,
    )
    assert _drain(handler) == [
        {"type": "status", "status": "warning", "message": _NOTICE}
    ]


def test_a_relayed_progress_line_stays_a_plain_status():
    handler = SSEOutputHandler()
    relay._dispatch_one(
        handler, {"type": "status", "message": "Reading your request"}, GAIA_PROFILE
    )
    assert _drain(handler) == [{"type": "status", "message": "Reading your request"}]
