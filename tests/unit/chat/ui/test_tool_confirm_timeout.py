# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's permission prompt waits as long as the TUI's, and says so."""

import threading
from pathlib import Path

from gaia.ui import sse_handler
from gaia.ui.sse_handler import SSEOutputHandler

_TUI_CONFIRMATION = (
    Path(__file__).resolve().parents[4]
    / "tui"
    / "internal"
    / "ui"
    / "components"
    / "confirmation.go"
)


def test_the_wait_matches_the_tui_bound():
    source = _TUI_CONFIRMATION.read_text(encoding="utf-8")
    assert "const DeliverableConfirmationTimeout = 10 * time.Minute" in source
    assert sse_handler.TOOL_CONFIRM_TIMEOUT_SECONDS == 10 * 60


def test_the_prompt_advertises_the_wait_it_enforces():
    handler = SSEOutputHandler()
    worker = threading.Thread(
        target=handler.confirm_tool_execution,
        args=("write_file", {"file_path": "a.txt"}),
        daemon=True,
    )
    worker.start()
    request = handler.event_queue.get(timeout=5)
    handler.resolve_tool_confirmation(approved=False)
    worker.join(timeout=5)

    assert request["type"] == "permission_request"
    assert request["timeout_seconds"] == handler.confirm_timeout_seconds == 600


def test_a_failed_tool_ends_as_a_failure():
    """tool_end must not overturn the error its tool_result just reported."""
    handler = SSEOutputHandler()
    handler.pretty_print_json({"status": "error", "error": "Access denied"}, "Result")
    handler.print_tool_complete()
    handler.pretty_print_json({"status": "success"}, "Result")
    handler.print_tool_complete()

    events = [handler.event_queue.get_nowait() for _ in range(4)]
    ends = [e["success"] for e in events if e["type"] == "tool_end"]
    results = [e["success"] for e in events if e["type"] == "tool_result"]
    assert results == [False, True]
    assert ends == [False, True]
