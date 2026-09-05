# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""run_shell_command must not lose output it cannot decode in the OS locale.

The bug these guard: the executor used bare ``text=True``, which decodes with
the locale codec — cp1252 on a default Windows box. subprocess performs that
decode inside its pipe reader THREAD, so an undecodable byte raises
UnicodeDecodeError there, the thread dies, and ``subprocess.run`` returns
returncode 0 with EMPTY stdout. The command succeeded and its output was
silently discarded, with nothing in the result to say so.

It was not hypothetical: ``gh issue list --repo amd/gaia`` returns a title
containing "⚠️", so GitHub triage received an empty backlog and the model
reported on issues it had never read. Any tool emitting UTF-8 (git, gh, npm,
docker) reaches this path.
"""

import subprocess
import sys

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _shell_tool():
    """Return the registered run_shell_command callable."""
    captured = {}
    import gaia.agents.base.tools as tools_module

    original = tools_module.tool

    def spy(**kwargs):
        def decorate(fn):
            captured[kwargs.get("name")] = fn
            return original(**kwargs)(fn)

        return decorate

    tools_module.tool = spy
    try:
        host = _Host()
        host.register_shell_tools()
    finally:
        tools_module.tool = original
    return captured["run_shell_command"]


NON_CP1252 = "⚠️ café ✓ 日本語"


@pytest.mark.skipif(
    sys.platform != "win32", reason="locale decode trap is Windows cp1252"
)
def test_output_with_non_locale_bytes_survives(tmp_path):
    """The exact shape of the bug: rc=0 with the output silently dropped.

    Read through ``cat``, which is on the read-only whitelist, so the test
    exercises the executor rather than tripping the command guardrails.
    """
    payload = tmp_path / "utf8.txt"
    payload.write_text(NON_CP1252, encoding="utf-8")

    result = _shell_tool()(
        command=f"cat {payload.name}", working_directory=str(tmp_path)
    )

    assert result["status"] == "success", result
    # The regression returned "" here while still reporting success.
    assert result["stdout"].strip(), "stdout was silently discarded"
    assert "café" in result["stdout"]


def test_executor_decodes_utf8_not_the_locale(monkeypatch):
    """Pin the call itself, so a future edit cannot quietly restore text=True.

    Asserted on the kwargs rather than only on behaviour because the failure is
    invisible in the result: the regression looks like a command that returned
    nothing, on every platform whose locale codec happens to be strict.
    """
    seen = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    _shell_tool()(command="echo hello")

    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"
    assert "text" not in seen, "text=True re-decodes with the locale codec"
    assert seen.get("stdin") is subprocess.DEVNULL, "stdin must never be inherited"


def test_child_never_inherits_the_agent_transport_stdin(monkeypatch):
    """A shell command must not inherit this process's stdin.

    capture_output redirects stdout/stderr but leaves stdin alone, and the
    agent's stdin is the TUI's pipe — open, never written, with no human behind
    it. A child that reads it blocks forever, and a timeout that reaches only
    the launched process does not rescue it: the surviving grandchild still
    holds the pipes (which is why the executor kills the whole tree).

    Observed exactly that way: `gh` invoked from the agent never returned and
    left an orphaned gh.exe behind on every attempt, while the same command ran
    in 0.07s from a shell. The tool reported a 180s timeout and GitHub triage
    was unusable.
    """
    seen = {}
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spy)
    _shell_tool()(command="echo hello")

    assert seen["stdin"] is subprocess.DEVNULL


@pytest.mark.skipif(sys.platform != "win32", reason="uses a Windows shell builtin")
def test_plain_ascii_output_is_unchanged():
    """The fix must not disturb the ordinary path."""
    result = _shell_tool()(command="echo hello")

    assert result["status"] == "success"
    assert "hello" in result["stdout"]
