# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for console-level confirmation of gated tools (#2210).

Before this, ``OutputHandler.confirm_tool_execution`` returned ``True``
unconditionally and ``AgentConsole`` never overrode it, so every tool in
``Agent.confirmation_required_tools()`` executed with no prompt whenever an
agent was driven from a CLI or a subprocess. These tests pin the safe
behaviour: prompt on a TTY (defaulting to "no"), deny when there is nobody to
ask, and approve only on an explicit yes or an explicit unattended opt-in.
"""

import io
import json
import logging
import os
from pathlib import Path

import pytest

import gaia.agents.base.console as console_module
from gaia.agents.base.agent import Agent
from gaia.agents.base.console import (
    AUTO_APPROVE_ENV_VAR,
    AgentConsole,
    OutputHandler,
    SilentConsole,
    format_confirmation_args,
    terminal_is_interactive,
)
from gaia.agents.base.tools import tool

REPO_SRC = Path(__file__).resolve().parents[3] / "src"


class _FakeStream(io.StringIO):
    """stdin/stdout stand-in whose ``isatty()`` is controllable."""

    def __init__(self, interactive: bool):
        super().__init__("")
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


@pytest.fixture(autouse=True)
def _no_env_opt_in(monkeypatch):
    """Every test starts from the user's real state: no unattended opt-in set."""
    import gaia

    monkeypatch.delenv(AUTO_APPROVE_ENV_VAR, raising=False)
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, AUTO_APPROVE_ENV_VAR, raising=False)


def _set_env_opt_in(monkeypatch, value: str) -> None:
    """Opt in the way an operator does: in the real process environment.

    ``os.environ`` alone is deliberately NOT enough — see
    ``TestDotenvCannotGrantApproval``.
    """
    import gaia

    monkeypatch.setenv(AUTO_APPROVE_ENV_VAR, value)
    monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, AUTO_APPROVE_ENV_VAR, value)


@pytest.fixture
def tty(monkeypatch):
    """Report an interactive terminal without displacing pytest's capture.

    ``terminal_is_interactive`` itself is tested against real streams in
    ``TestTerminalDetection``, and end-to-end behind a real pty in
    ``tests/integration/test_cli_tool_confirmation.py``.
    """
    monkeypatch.setattr(console_module, "terminal_is_interactive", lambda: True)


@pytest.fixture
def no_tty(monkeypatch):
    """Report no terminal (CI, subprocess host, `cmd < file`, `cmd > file`)."""
    monkeypatch.setattr(console_module, "terminal_is_interactive", lambda: False)


def _answer(monkeypatch, *answers):
    """Feed answers to the console's ``input()`` prompt, in order."""
    queue = list(answers)
    captured = []

    def _fake_input(prompt=""):
        captured.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", _fake_input)
    return captured


class _MinimalHandler(OutputHandler):
    """Concrete OutputHandler that implements only the abstract methods, so the
    inherited confirmation default is what gets exercised."""

    def print_processing_start(self, query, max_steps, model_id=None):
        pass

    def print_step_header(self, step_num, step_limit):
        pass

    def print_state_info(self, state_message):
        pass

    def print_thought(self, thought):
        pass

    def print_goal(self, goal):
        pass

    def print_plan(self, plan, current_step=None):
        pass

    def print_tool_usage(self, tool_name):
        pass

    def print_tool_complete(self):
        pass

    def pretty_print_json(self, data, title=None):
        pass

    def print_error(self, error_message):
        pass

    def print_warning(self, warning_message):
        pass

    def print_info(self, message):
        pass

    def start_progress(self, message):
        pass

    def stop_progress(self):
        pass

    def pause_progress(self):
        pass

    def resume_progress(self):
        pass

    def print_final_answer(self, answer):
        pass

    def print_repeated_tool_warning(self):
        pass

    def print_completion(self, steps_taken, steps_limit):
        pass

    def print_step_paused(self, description):
        pass

    def print_command_executing(self, command):
        pass

    def print_agent_selected(self, agent_name, language, project_type):
        pass


class TestBaseHandlerDefault:
    """The root cause: a handler that cannot ask a human must not answer."""

    def test_default_denies(self):
        assert (
            _MinimalHandler().confirm_tool_execution("send_now", {"to": "a@b.c"})
            is False
        )

    def test_denial_reason_is_actionable(self):
        handler = _MinimalHandler()
        handler.confirm_tool_execution("send_now", {})
        reason = handler.confirmation_denied_reason("send_now")
        assert "send_now" in reason
        assert "cannot run" in reason
        # Names both ways out: an interactive terminal, or the explicit opt-in.
        assert "interactive terminal" in reason
        assert AUTO_APPROVE_ENV_VAR in reason

    def test_denial_is_logged(self, caplog):
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.console"):
            _MinimalHandler().confirm_tool_execution("permanent_delete", {})
        assert any("permanent_delete" in r.getMessage() for r in caplog.records)

    def test_silent_console_denies(self):
        """JSON-only / MCP-wrapper hosts have no prompt channel either."""
        assert SilentConsole().confirm_tool_execution("write_file", {}) is False

    def test_host_opt_in_approves_and_logs(self, caplog):
        handler = SilentConsole(auto_approve_gated_tools=True)
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.console"):
            assert handler.confirm_tool_execution("write_file", {}) is True
        assert any("Auto-approved" in r.getMessage() for r in caplog.records)

    def test_env_opt_in_approves(self, monkeypatch):
        _set_env_opt_in(monkeypatch, "1")
        assert _MinimalHandler().confirm_tool_execution("send_now", {}) is True
        assert AgentConsole().confirm_tool_execution("send_now", {}) is True

    def test_env_opt_in_ignores_junk_values(self, monkeypatch):
        _set_env_opt_in(monkeypatch, "0")
        assert _MinimalHandler().confirm_tool_execution("send_now", {}) is False
        _set_env_opt_in(monkeypatch, "maybe")
        assert _MinimalHandler().confirm_tool_execution("send_now", {}) is False

    def test_os_environ_alone_does_not_opt_in(self, monkeypatch):
        """Only the startup environment counts — see the .env tests below."""
        monkeypatch.setenv(AUTO_APPROVE_ENV_VAR, "1")
        assert _MinimalHandler().confirm_tool_execution("send_now", {}) is False


class TestAgentConsoleNonInteractive:
    """The CLI console driven from a pipe / CI / subprocess host."""

    def test_denies_without_tty(self, no_tty):
        assert AgentConsole().confirm_tool_execution("send_draft", {}) is False

    def test_denial_message_is_shown_to_the_user(self, no_tty, capsys):
        console = AgentConsole()
        console.confirm_tool_execution("send_draft", {"to": "a@b.c"})
        out = capsys.readouterr().out
        assert "send_draft" in out
        assert AUTO_APPROVE_ENV_VAR in out

    def test_never_prompts_without_tty(self, no_tty, monkeypatch):
        """No blocking read on a closed/piped stdin — that would hang a daemon."""

        def _explode(prompt=""):
            raise AssertionError("input() must not be called without a TTY")

        monkeypatch.setattr("builtins.input", _explode)
        assert AgentConsole().confirm_tool_execution("send_now", {}) is False

    def test_denial_notice_is_silent_on_a_json_only_console(self, no_tty, capsys):
        """JSON-only output must stay parseable — the reason goes to the log."""
        SilentConsole().confirm_tool_execution("send_draft", {})
        assert capsys.readouterr().out == ""


class TestTerminalDetection:
    """``terminal_is_interactive`` against real stream objects."""

    def test_both_ends_a_tty(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", _FakeStream(interactive=True))
        monkeypatch.setattr("sys.stdout", _FakeStream(interactive=True))
        assert terminal_is_interactive() is True

    def test_piped_stdin(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", _FakeStream(interactive=False))
        monkeypatch.setattr("sys.stdout", _FakeStream(interactive=True))
        assert terminal_is_interactive() is False

    def test_redirected_stdout(self, monkeypatch):
        """`gaia … > log.txt`: prompting would block on a question nobody sees."""
        monkeypatch.setattr("sys.stdin", _FakeStream(interactive=True))
        monkeypatch.setattr("sys.stdout", _FakeStream(interactive=False))
        assert terminal_is_interactive() is False

    def test_closed_stream(self, monkeypatch):
        class _Closed:
            def isatty(self):
                raise ValueError("I/O operation on closed file")

        monkeypatch.setattr("sys.stdin", _Closed())
        assert terminal_is_interactive() is False

    def test_missing_stream(self, monkeypatch):
        """A pythonw / frozen build can have stdin set to None."""
        monkeypatch.setattr("sys.stdin", None)
        assert terminal_is_interactive() is False

    def test_gated_tool_is_denied_when_no_terminal(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", _FakeStream(interactive=False))
        monkeypatch.setattr("sys.stdout", _FakeStream(interactive=False))
        assert AgentConsole().confirm_tool_execution("send_now", {}) is False


class TestAgentConsoleInteractive:
    """The CLI console attached to a real terminal."""

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "  yes  "])
    def test_yes_approves(self, tty, monkeypatch, answer):
        _answer(monkeypatch, answer)
        assert AgentConsole().confirm_tool_execution("send_now", {}) is True

    @pytest.mark.parametrize("answer", ["n", "no", "", "   ", "whatever"])
    def test_no_and_empty_deny(self, tty, monkeypatch, answer):
        _answer(monkeypatch, answer)
        console = AgentConsole()
        assert console.confirm_tool_execution("send_now", {}) is False
        assert "denied by the user" in console.confirmation_denied_reason("send_now")

    def test_eof_denies(self, tty, monkeypatch):
        def _eof(prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _eof)
        assert AgentConsole().confirm_tool_execution("send_now", {}) is False

    def test_ctrl_c_denies(self, tty, monkeypatch):
        def _interrupt(prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _interrupt)
        assert AgentConsole().confirm_tool_execution("send_now", {}) is False

    def test_prompt_shows_tool_and_arguments(self, tty, monkeypatch, capsys):
        _answer(monkeypatch, "n")
        AgentConsole().confirm_tool_execution(
            "send_now", {"to": "boss@example.com", "subject": "resignation"}
        )
        out = capsys.readouterr().out
        assert "send_now" in out
        assert "boss@example.com" in out
        assert "resignation" in out

    def test_prompt_defaults_to_no(self, tty, monkeypatch):
        prompts = _answer(monkeypatch, "")
        AgentConsole().confirm_tool_execution("send_now", {})
        # Capitalised N marks the default; y/a are the explicit approvals.
        assert "[N]o" in prompts[0]

    def test_always_approves_that_tool_for_the_session_only(self, tty, monkeypatch):
        _answer(monkeypatch, "a", "n")
        console = AgentConsole()
        assert console.confirm_tool_execution("write_file", {"path": "a.py"}) is True
        # Second call to the same tool does not re-prompt (no answers consumed).
        assert console.confirm_tool_execution("write_file", {"path": "b.py"}) is True
        # A different tool still asks — and the queued "n" denies it.
        assert console.confirm_tool_execution("send_now", {}) is False

    def test_always_does_not_leak_across_consoles(self, tty, monkeypatch):
        _answer(monkeypatch, "a", "n")
        assert AgentConsole().confirm_tool_execution("write_file", {}) is True
        assert AgentConsole().confirm_tool_execution("write_file", {}) is False


class TestArgumentRendering:
    def test_no_arguments_is_labelled(self):
        assert format_confirmation_args({}) == "(no arguments)"

    def test_long_values_are_shortened(self):
        rendered = format_confirmation_args({"body": "x" * 10_000})
        assert "10000 chars" in rendered
        assert len(rendered) < 2_000

    def test_every_argument_name_survives_shortening(self):
        """A hidden key is a hidden side effect — names are never dropped."""
        rendered = format_confirmation_args(
            {"command": "rm -rf " + "a" * 5_000 + "/important", "cwd": "/tmp"}
        )
        assert "command" in rendered and "cwd" in rendered
        # Head AND tail of the shortened value stay visible.
        assert "rm -rf" in rendered
        assert "/important" in rendered


# === The agent-loop contract ===


class _GatedAgent(Agent):
    """Agent with one gated tool that records whether its body ran."""

    CONFIRMATION_REQUIRED_TOOLS = frozenset({"send_now"})

    def __init__(self, console, **kwargs):
        self.sent = []
        self._console_override = console
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "gated"

    def _create_console(self):
        return self._console_override

    def _register_tools(self) -> None:
        sent = self.sent

        @tool
        def send_now(to: str) -> str:
            """Send an email immediately. Gated."""
            sent.append(to)
            return "SENT"


def _make_agent(console):
    from unittest.mock import patch

    with patch("gaia.agents.base.agent.AgentSDK"):
        return _GatedAgent(console=console, silent_mode=True, skip_lemonade=True)


class TestExecuteToolContract:
    def test_non_tty_cli_denies_and_body_never_runs(self, no_tty):
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert result["status"] == "denied"
        assert agent.sent == []

    def test_denied_result_carries_the_actionable_reason(self, no_tty):
        agent = _make_agent(AgentConsole())
        error = agent._execute_tool("send_now", {"to": "a@b.c"})["error"]
        assert "send_now" in error
        assert AUTO_APPROVE_ENV_VAR in error

    def test_interactive_no_reports_a_user_denial(self, tty, monkeypatch):
        _answer(monkeypatch, "n")
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert result["status"] == "denied"
        assert result["error"] == "Tool 'send_now' was denied by the user."
        assert agent.sent == []

    def test_interactive_yes_executes(self, tty, monkeypatch):
        _answer(monkeypatch, "y")
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert result == "SENT"
        assert agent.sent == ["a@b.c"]

    def test_ungated_tools_are_unaffected(self, no_tty):
        """The gate only touches the confirmation set — normal tools still run."""
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("unknown_tool_name", {})
        # Unknown-tool error, not a confirmation denial.
        assert result["status"] == "error"

    def test_console_without_the_reason_hook_still_gets_a_message(self, no_tty):
        """Duck-typed hook: a custom console that only implements the bool API."""

        class _BareConsole(AgentConsole):
            def confirm_tool_execution(self, tool_name, tool_args):
                return False

            confirmation_denied_reason = None  # not callable

        agent = _make_agent(_BareConsole())
        result = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert result["status"] == "denied"
        assert result["error"] == "Tool 'send_now' was denied by the user."


class TestSilentModeStillAsks:
    """`gaia chat` builds a SilentConsole for a real user at a real terminal
    (cli.py sets ``silent_mode=True`` for everything but ``--debug``). Silence
    must suppress narration, not consent — otherwise the flagship CLI would go
    from "runs gated tools without asking" to "refuses them without asking".
    """

    def test_silent_console_prompts_on_a_terminal(self, tty, monkeypatch):
        _answer(monkeypatch, "y")
        assert SilentConsole().confirm_tool_execution("write_file", {}) is True

    def test_silent_console_no_denies(self, tty, monkeypatch):
        _answer(monkeypatch, "n")
        assert SilentConsole().confirm_tool_execution("write_file", {}) is False

    def test_both_cli_consoles_share_the_prompt_implementation(self):
        from gaia.agents.base.console import TerminalConfirmationMixin

        assert issubclass(AgentConsole, TerminalConfirmationMixin)
        assert issubclass(SilentConsole, TerminalConfirmationMixin)

    def test_chat_agents_console_can_be_asked(self, tty, monkeypatch):
        """Pins the real console the real entry point builds."""
        pytest.importorskip("gaia_agent_chat")
        from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

        stub = _StubChatAgent(ChatAgentConfig(silent_mode=True))
        console = ChatAgent._create_console(stub)
        _answer(monkeypatch, "n")
        assert console.confirm_tool_execution("write_file", {}) is False
        _answer(monkeypatch, "y")
        assert console.confirm_tool_execution("write_file", {}) is True


class _StubChatAgent:
    """Just enough of ChatAgent for ``_create_console`` — no LLM, no RAG."""

    def __init__(self, config):
        self.silent_mode = config.silent_mode


class TestAlwaysAllowScope:
    """ "Always" must not become a blank cheque for arbitrary execution."""

    @pytest.mark.parametrize("tool_name", ["run_shell_command", "run_cli_command"])
    def test_shell_tools_never_offer_always(self, tty, monkeypatch, tool_name):
        prompts = _answer(monkeypatch, "a", "n")
        console = AgentConsole()
        # "a" is not an approval for these — the tool name says nothing about
        # what the next command will be.
        assert console.confirm_tool_execution(tool_name, {"command": "ls"}) is False
        assert "always" not in prompts[0]
        # And nothing was remembered: the next call asks again.
        assert (
            console.confirm_tool_execution(tool_name, {"command": "rm -rf /"}) is False
        )

    def test_reset_clears_remembered_approvals(self, tty, monkeypatch):
        _answer(monkeypatch, "a", "n")
        console = AgentConsole()
        assert console.confirm_tool_execution("write_file", {}) is True
        console.reset_tool_approvals()
        # Asks again after the reset — and the queued "n" denies.
        assert console.confirm_tool_execution("write_file", {}) is False


class TestDenialReasonBinding:
    """The reason must belong to the tool it is reported for."""

    def test_reason_is_not_reused_for_a_different_tool(self, no_tty):
        console = AgentConsole()
        console.confirm_tool_execution("send_now", {})
        assert "send_now" in console.confirmation_denied_reason("send_now")
        # A console that denies without recording a reason (a third-party
        # handler, a monkeypatched test seam) must not inherit this one.
        assert console.confirmation_denied_reason("permanent_delete") == (
            "Tool 'permanent_delete' was denied by the user."
        )

    def test_approval_clears_a_stale_reason(self, tty, monkeypatch):
        _answer(monkeypatch, "n", "y")
        console = AgentConsole()
        console.confirm_tool_execution("send_now", {})
        assert console.confirm_tool_execution("send_now", {}) is True
        assert console._last_denial is None


class TestDotenvCannotGrantApproval:
    """A ``.env`` file travels with a directory; it is not an operator decision.

    ``gaia/__init__.py`` runs ``load_dotenv()`` at import, which would otherwise
    let a project-local file switch off every confirmation prompt.
    """

    def test_env_var_name_matches_the_scrubbed_one(self):
        import gaia

        assert gaia._AUTO_APPROVE_TOOLS_VAR == AUTO_APPROVE_ENV_VAR

    def test_dotenv_injected_value_is_dropped(self, tmp_path):
        """Import GAIA in a child process whose CWD holds a hostile .env."""
        import subprocess
        import sys

        (tmp_path / ".env").write_text("GAIA_AUTO_APPROVE_TOOLS=1\n")
        script = (
            "import os, sys, json\n"
            "sys.path.insert(0, os.environ['GAIA_SRC'])\n"
            "import gaia\n"
            "from gaia.agents.base.console import auto_approve_env_enabled\n"
            "print(json.dumps({'enabled': auto_approve_env_enabled(),\n"
            "                  'env': os.environ.get('GAIA_AUTO_APPROVE_TOOLS')}))\n"
        )
        env = dict(os.environ)
        env.pop("GAIA_AUTO_APPROVE_TOOLS", None)
        env["GAIA_SRC"] = str(REPO_SRC)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["enabled"] is False, "a .env file enabled unattended approval"
        # dotenv did inject it into os.environ — the point is that the
        # confirmation gate does not read from there.
        assert payload["env"] == "1"
        assert "Ignoring GAIA_AUTO_APPROVE_TOOLS from a .env file" in proc.stderr

    def test_real_process_environment_still_works(self, tmp_path):
        """The operator's own export must keep working — only dotenv is dropped."""
        import subprocess
        import sys

        script = (
            "import os, sys, json\n"
            "sys.path.insert(0, os.environ['GAIA_SRC'])\n"
            "import gaia\n"
            "from gaia.agents.base.console import auto_approve_env_enabled\n"
            "print(json.dumps({'enabled': auto_approve_env_enabled()}))\n"
        )
        env = dict(os.environ)
        env["GAIA_AUTO_APPROVE_TOOLS"] = "1"
        env["GAIA_SRC"] = str(REPO_SRC)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip().splitlines()[-1])["enabled"] is True


class TestApiServerHandlerDenies:
    """The OpenAI-compatible API stream is one-way: no channel to approve on."""

    def test_api_sse_handler_denies_gated_tools(self):
        from gaia.api.sse_handler import SSEOutputHandler as ApiSSEOutputHandler

        handler = ApiSSEOutputHandler()
        assert handler.confirm_tool_execution("write_file", {"path": "x"}) is False
        assert AUTO_APPROVE_ENV_VAR in handler.confirmation_denied_reason("write_file")


class TestDenialNoticeThrottling:
    """A retrying model must not bury the transcript in repeated notices."""

    def test_notice_is_shown_once_per_tool(self, no_tty, capsys):
        console = AgentConsole()
        for _ in range(3):
            assert console.confirm_tool_execution("send_now", {}) is False
        out = capsys.readouterr().out
        assert out.count("requires explicit user approval") == 1

        # A different tool gets its own notice.
        assert console.confirm_tool_execution("write_file", {}) is False
        assert "write_file" in capsys.readouterr().out

    def test_every_denial_is_still_logged(self, no_tty, caplog):
        console = AgentConsole()
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.console"):
            for _ in range(3):
                console.confirm_tool_execution("send_now", {})
        denials = [
            r for r in caplog.records if "Denied confirmation-gated" in r.getMessage()
        ]
        assert len(denials) == 3


class TestGateNeverCrashes:
    """A broken terminal must DENY, not raise.

    Before the gate existed the tool simply ran, so an exception escaping
    ``confirm_tool_execution`` would be a brand-new way to kill a working
    session — and it propagates: ``Agent._execute_tool``'s callers do not wrap
    it. Every one of these inputs used to raise out of the gate.
    """

    def test_malformed_tool_args_still_prompt(self, tty, monkeypatch):
        """A model can emit ``tool_args`` as a list or a bare string."""
        _answer(monkeypatch, "n", "n", "n")
        console = AgentConsole()
        for bad_args in (["boss@example.com"], "boss@example.com", 42):
            assert console.confirm_tool_execution("send_now", bad_args) is False

    def test_malformed_tool_args_are_shown_not_hidden(self, tty, monkeypatch, capsys):
        _answer(monkeypatch, "y")
        AgentConsole().confirm_tool_execution("send_now", ["boss@example.com"])
        assert "boss@example.com" in capsys.readouterr().out

    def test_unreadable_terminal_denies(self, tty, monkeypatch):
        """`input()` raises OSError on a detached / broken tty."""

        def _broken(prompt=""):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr("builtins.input", _broken)
        console = AgentConsole()
        assert console.confirm_tool_execution("send_now", {"to": "a@b.c"}) is False
        # Nobody said no — the reason must not blame the user.
        reason = console.confirmation_denied_reason("send_now")
        assert "could not present the request or read an answer" in reason
        assert "denied by the user" not in reason

    def test_undisplayable_request_denies_without_prompting(self, tty, monkeypatch):
        """If the request cannot be rendered, do not ask the user to approve
        something they never saw."""

        def _explode(*_a, **_kw):
            raise RuntimeError("terminal exploded")

        def _must_not_prompt(prompt=""):
            raise AssertionError("prompted for a request the user never saw")

        monkeypatch.setattr("builtins.input", _must_not_prompt)
        console = AgentConsole()
        monkeypatch.setattr(console, "_render_confirmation_request", _explode)
        assert console.confirm_tool_execution("send_now", {"to": "a@b.c"}) is False
        assert "could not present" in console.confirmation_denied_reason("send_now")

    def test_broken_stdout_on_the_deny_path_still_denies(self, no_tty, monkeypatch):
        """A daemon-hosted agent can have a closed stdout."""

        class _Closed:
            def write(self, *_a):
                raise ValueError("I/O operation on closed file")

            def flush(self):
                pass

            def isatty(self):
                return False

        monkeypatch.setattr("sys.stdout", _Closed())
        assert AgentConsole().confirm_tool_execution("send_now", {}) is False

    def test_broken_progress_hooks_still_reach_a_decision(self, tty, monkeypatch):
        _answer(monkeypatch, "y")
        console = AgentConsole()
        monkeypatch.setattr(
            console, "pause_progress", lambda: (_ for _ in ()).throw(OSError("nope"))
        )
        monkeypatch.setattr(
            console, "resume_progress", lambda: (_ for _ in ()).throw(OSError("nope"))
        )
        assert console.confirm_tool_execution("send_now", {"to": "a@b.c"}) is True

    def test_display_failures_are_logged(self, no_tty, monkeypatch, caplog):
        console = AgentConsole()
        monkeypatch.setattr(
            console,
            "_show_denial_notice",
            lambda _r: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.console"):
            assert console.confirm_tool_execution("send_now", {}) is False
        assert any("failed on this terminal" in r.getMessage() for r in caplog.records)


class TestSideEffectNeverHappensOnTheBrokenPaths:
    """The point of the gate: the tool body must not run. Asserted on the side
    effect itself, not on whether a prompt was requested."""

    def test_unreadable_terminal_does_not_execute_the_tool(self, tty, monkeypatch):
        def _broken(prompt=""):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr("builtins.input", _broken)
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("send_now", {"to": "a@b.c"})
        assert result["status"] == "denied"
        assert agent.sent == [], "the send ran on a terminal that could not ask"

    def test_malformed_args_do_not_execute_the_tool(self, tty, monkeypatch):
        _answer(monkeypatch, "n")
        agent = _make_agent(AgentConsole())
        result = agent._execute_tool("send_now", ["a@b.c"])
        assert result["status"] == "denied"
        assert agent.sent == []

    def test_no_exception_escapes_the_gate_into_the_agent_loop(self, tty, monkeypatch):
        """``_execute_tool``'s callers don't catch — a raise here kills the run."""

        def _broken(prompt=""):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr("builtins.input", _broken)
        agent = _make_agent(AgentConsole())
        # Would raise AttributeError/OSError before the fix.
        assert agent._execute_tool("send_now", {"to": "a@b.c"})["status"] == "denied"


class TestPromptDoesNotAlterWhatRuns:
    """Shortening is for the display only. If it leaked into the arguments, an
    approved write would silently truncate the user's file."""

    def test_arguments_are_not_mutated_by_rendering(self, tty, monkeypatch):
        _answer(monkeypatch, "y")
        args = {"path": "/tmp/x.txt", "content": "y" * 5_000}
        AgentConsole().confirm_tool_execution("write_file", args)
        assert len(args["content"]) == 5_000

    def test_approved_tool_receives_the_full_arguments(self, tty, monkeypatch):
        _answer(monkeypatch, "y")
        received = {}

        class _Probe(Agent):
            AGENT_ID = "confirmation-arg-fidelity"
            CONFIRMATION_REQUIRED_TOOLS = frozenset({"write_file"})

            def _create_console(self):
                return AgentConsole()

            def _get_system_prompt(self):
                return "p"

            def _register_tools(self):
                @tool
                def write_file(path: str, content: str) -> str:
                    """Write a file. Gated."""
                    received["len"] = len(content)
                    return "WROTE"

        from unittest.mock import patch

        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _Probe(silent_mode=True, skip_lemonade=True)

        agent._execute_tool(
            "write_file", {"path": "/tmp/x.txt", "content": "y" * 5_000}
        )
        assert received["len"] == 5_000
