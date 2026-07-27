# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end confirmation behaviour for CLI-driven agents (#2210).

The unit tests for this fix patch ``sys.stdin``, which proves the branch but not
the branch *condition*: under pytest, stdin is already a non-TTY stand-in, so a
mocked ``isatty()`` could pass while the real thing behaves differently. These
tests run a real agent in a real child process — once with a pipe on stdin (a
subprocess host, a CI job, ``gaia … < file``) and once behind a real pty (a user
at a terminal) — and check what the user actually gets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# A minimal real Agent with one confirmation-gated tool, driven through the real
# AgentConsole. Prints a single machine-readable line for the test to assert on.
PROBE_SCRIPT = '''
import json
import os
from unittest.mock import patch

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.tools import tool

# GAIA_CONSOLE=silent exercises the console `gaia chat` actually builds.
CONSOLE = SilentConsole if os.environ.get("GAIA_CONSOLE") == "silent" else AgentConsole


class ProbeAgent(Agent):
    AGENT_ID = "cli-confirmation-probe"
    CONFIRMATION_REQUIRED_TOOLS = frozenset({"send_now"})

    def __init__(self, **kwargs):
        self.executed = []
        super().__init__(**kwargs)

    def _create_console(self):
        return CONSOLE()

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        executed = self.executed

        @tool
        def send_now(to: str) -> str:
            """Send an email immediately. Gated."""
            executed.append(to)
            return "SENT"


with patch("gaia.agents.base.agent.AgentSDK"):
    agent = ProbeAgent(skip_lemonade=True)

result = agent._execute_tool("send_now", {"to": "boss@example.com"})
print("RESULT " + json.dumps({"result": result, "executed": agent.executed}))
'''


def _child_env() -> dict:
    env = os.environ.copy()
    # Real initial state: the unattended opt-in must not be inherited.
    env.pop("GAIA_AUTO_APPROVE_TOOLS", None)
    src = str(REPO_ROOT / "src")
    env["PYTHONPATH"] = (
        src + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src
    )
    # Keep the child's output plain so the assertions read the payload, not ANSI.
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    return env


def _result_line(output: str) -> dict:
    import json

    for line in output.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT ") :])
    raise AssertionError(f"probe produced no RESULT line. Output was:\n{output}")


class TestPipedStdin:
    """A subprocess host / CI job: no terminal, so nobody can approve."""

    def test_redirected_stdout_is_not_treated_as_interactive(self, tmp_path):
        """`gaia … > log.txt` from a terminal: prompting there would block on a
        question the user cannot see, so it must deny instead."""
        log = tmp_path / "out.log"
        with open(log, "w", encoding="utf-8") as sink:
            proc = subprocess.run(
                [sys.executable, "-c", PROBE_SCRIPT],
                cwd=REPO_ROOT,
                env=_child_env(),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
        assert proc.returncode == 0, proc.stderr
        payload = _result_line(log.read_text(encoding="utf-8"))
        assert payload["result"]["status"] == "denied"
        assert payload["executed"] == []

    def test_gated_tool_is_denied_and_never_executes(self):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE_SCRIPT],
            cwd=REPO_ROOT,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr

        payload = _result_line(proc.stdout)
        assert payload["result"]["status"] == "denied"
        assert payload["executed"] == []
        # The denial must tell the operator what to do about it.
        assert "GAIA_AUTO_APPROVE_TOOLS" in payload["result"]["error"]

    def test_explicit_opt_in_lets_an_unattended_host_proceed(self):
        env = _child_env()
        env["GAIA_AUTO_APPROVE_TOOLS"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c", PROBE_SCRIPT],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr

        payload = _result_line(proc.stdout)
        assert payload["result"] == "SENT"
        assert payload["executed"] == ["boss@example.com"]


@pytest.mark.skipif(
    sys.platform == "win32", reason="pty is POSIX-only; Windows uses the piped path"
)
class TestRealTerminal:
    """A user at a terminal: the prompt appears and the answer is honoured."""

    @staticmethod
    def _run_behind_pty(answer: str, console: str = "rich") -> str:
        import pty
        import select

        env = _child_env()
        env["GAIA_CONSOLE"] = console
        primary, secondary = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-c", PROBE_SCRIPT],
            cwd=REPO_ROOT,
            env=env,
            stdin=secondary,
            stdout=secondary,
            stderr=secondary,
            text=False,
        )
        os.close(secondary)

        chunks: list[bytes] = []
        answered = False
        try:
            while proc.poll() is None or select.select([primary], [], [], 0.2)[0]:
                ready = select.select([primary], [], [], 0.5)[0]
                if ready:
                    try:
                        data = os.read(primary, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    chunks.append(data)
                if not answered and b"Allow this?" in b"".join(chunks):
                    os.write(primary, answer.encode())
                    answered = True
            proc.wait(timeout=180)
        finally:
            os.close(primary)
            if proc.poll() is None:  # pragma: no cover - safety net
                proc.kill()

        assert (
            answered
        ), "the confirmation prompt never appeared on a real terminal:\n" + b"".join(
            chunks
        ).decode(
            errors="replace"
        )
        return b"".join(chunks).decode(errors="replace")

    def test_prompt_shows_the_tool_and_its_arguments(self):
        output = self._run_behind_pty("n\r")
        assert "send_now" in output
        assert "boss@example.com" in output

    def test_answering_no_denies_and_the_tool_never_runs(self):
        payload = _result_line(self._run_behind_pty("n\r"))
        assert payload["result"]["status"] == "denied"
        assert payload["result"]["error"] == "Tool 'send_now' was denied by the user."
        assert payload["executed"] == []

    def test_bare_enter_denies(self):
        """The prompt defaults to no — a stray Enter must not send mail."""
        payload = _result_line(self._run_behind_pty("\r"))
        assert payload["result"]["status"] == "denied"
        assert payload["executed"] == []

    def test_answering_yes_executes(self):
        payload = _result_line(self._run_behind_pty("y\r"))
        assert payload["result"] == "SENT"
        assert payload["executed"] == ["boss@example.com"]

    def test_silent_console_still_asks_the_user_at_a_terminal(self):
        """`gaia chat` builds a SilentConsole even for an interactive user, so
        silence must not swallow the question (#2210)."""
        output = self._run_behind_pty("y\r", console="silent")
        assert "send_now" in output
        payload = _result_line(output)
        assert payload["result"] == "SENT"

    def test_silent_console_honours_no(self):
        payload = _result_line(self._run_behind_pty("n\r", console="silent"))
        assert payload["result"]["status"] == "denied"
        assert payload["executed"] == []
