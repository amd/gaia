# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The chat agent's process-launching tools report a failed launch as an error.

``list_windows`` read an empty process list out of a ``tasklist`` that had
failed, and ``notify_desktop`` returned success the moment PowerShell was
spawned, whether or not it ever drew anything.
"""

import subprocess
import sys

import pytest

from tests.unit.test_profilespec_characterization import chat_agent_build_context


@pytest.fixture
def chat_agent():
    return pytest.importorskip(
        "gaia_agent_chat.agent", reason="gaia-agent-chat wheel not installed"
    )


@pytest.fixture
def tools(chat_agent):
    with chat_agent_build_context("full") as agent:
        agent._register_tools()
        yield agent._tools_registry


def _completed(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(["x"], code, stdout, stderr)


def test_a_failed_tasklist_is_an_error_not_an_empty_list(
    tools, chat_agent, monkeypatch
):
    monkeypatch.setattr(chat_agent.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "pywinauto", None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(1, stderr="ERROR: Access denied\n"),
    )

    result = tools["list_windows"]["function"]()

    assert result["status"] == "error"
    assert result["error"] == "'tasklist' exited with code 1: ERROR: Access denied"
    assert result["return_code"] == 1


def test_a_failed_wmctrl_names_its_exit_code(tools, chat_agent, monkeypatch):
    monkeypatch.setattr(chat_agent.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _completed(1, stderr="Cannot open display.\n"),
    )

    result = tools["list_windows"]["function"]()

    assert result["status"] == "error"
    assert result["error"] == "'wmctrl -l' exited with code 1: Cannot open display."


class _ExitedPopen:
    """A PowerShell that died at once, before drawing the message box."""

    def __init__(self, argv, **kwargs):
        kwargs["stderr"].write(b"Add-Type : Cannot add type.\r\n")

    def wait(self, timeout=None):
        return 1


class _RunningPopen:
    """A PowerShell still running: the message box is up."""

    def __init__(self, argv, **kwargs):
        pass

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("powershell", timeout)


@pytest.mark.parametrize(
    "popen, status", [(_ExitedPopen, "error"), (_RunningPopen, "success")]
)
def test_notify_desktop_reports_what_powershell_did(
    tools, chat_agent, monkeypatch, popen, status
):
    monkeypatch.setattr(chat_agent.platform, "system", lambda: "Windows")
    monkeypatch.setitem(sys.modules, "plyer", None)
    monkeypatch.setattr(subprocess, "Popen", popen)

    result = tools["notify_desktop"]["function"](title="t", message="m")

    assert result["status"] == status, result
    if status == "error":
        assert result["error"] == (
            "Notification failed: 'powershell' exited with code 1: "
            "Add-Type : Cannot add type."
        )
