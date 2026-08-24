# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""External MCP services must name what is missing, not leak an errno.

Node ships ``npx`` on Windows as ``npx.cmd``. Spawning the literal string
"npx" without a shell fails with "[WinError 2] The system cannot find the file
specified" — a message naming neither npx nor Node.

Observed cost: `search_web` returned that errno, and the agent told the user it
had "a consistent environmental problem preventing external network requests",
then abandoned the task. The tool was one PATHEXT lookup away from working.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from gaia.mcp.external_services import ExternalMCPService, PerplexityService


def test_argv0_is_resolved_through_path():
    """shutil.which applies PATHEXT, which is what finds npx.cmd on Windows."""
    service = ExternalMCPService(command=[sys.executable, "-V"])

    assert service._resolved_command()[0] == sys.executable


def test_unresolvable_argv0_is_left_alone():
    """So the spawn raises FileNotFoundError and the handler can name it."""
    service = ExternalMCPService(command=["definitely-not-a-real-program-xyz"])

    assert service._resolved_command() == ["definitely-not-a-real-program-xyz"]


def test_missing_executable_is_reported_by_name(monkeypatch):
    """The failure a user sees must say 'npx', not '[WinError 2]'."""
    service = ExternalMCPService(command=["npx", "-y", "some-mcp-server"])

    def boom(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "run", boom)
    result = service.call_tool("anything", {})

    assert "npx" in result["error"]
    assert "Node.js" in result["error"]
    assert "WinError" not in result["error"]


def test_mcp_output_is_decoded_as_utf8(monkeypatch):
    """JSON-RPC is UTF-8 by spec — never the Windows locale codec."""
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, '{"result": {}}', "")

    monkeypatch.setattr(subprocess, "run", spy)
    ExternalMCPService(command=[sys.executable]).call_tool("t", {})

    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"
    assert "text" not in seen


def test_perplexity_without_a_key_says_so(monkeypatch):
    """A missing key must not become a mystery failure inside npx.

    The model relays this string to the user, so it decides whether they read
    "set PERPLEXITY_API_KEY" or a fabricated network-outage story.
    """
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    service = PerplexityService()

    def fail(*args, **kwargs):
        pytest.fail("no subprocess should be spawned without a key")

    monkeypatch.setattr(subprocess, "run", fail)
    result = service.search_web("anything")

    assert result["success"] is False
    assert "PERPLEXITY_API_KEY" in result["error"]
