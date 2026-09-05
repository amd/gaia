# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A shell command gets a timeout that suits what it is, and waits are one step.

Two behaviours are under test:

* ``run_shell_command`` picks its default timeout from the command's class —
  test runners, builds/installs, VCS/network calls, everything else — instead of
  killing every command at a flat 30s.
* ``wait_for_condition`` polls a predicate against a deadline inside a single
  call, so waiting for a server or a build costs one agent step, not one per
  check.

Three behaviours that already worked are pinned here as well, because the
timeout rework runs straight through them: the applied timeout comes back in the
result, a timed-out command is flagged, and partial output survives the kill.
"""

import subprocess

import pytest

from gaia.agents.tools.command_timeouts import (
    MAX_COMMAND_TIMEOUT,
    TIMEOUT_CLASSES,
    classify_command,
    resolve_timeout,
)
from gaia.agents.tools.shell_tools import (
    WAIT_MAX_POLL_INTERVAL,
    WAIT_MAX_TIMEOUT,
    WAIT_MIN_POLL_INTERVAL,
    ShellToolsMixin,
)


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _shell_tools():
    """The registered tool callables, by name."""
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
    return host, captured


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def unrestricted(monkeypatch):
    """Run with the read-only allowlist stood down.

    The allowlist is not what these tests are about, and it refuses `pytest` and
    `pip install` outright today — so with it in place only the ``default`` class
    would ever be reachable. Guardrail coverage lives in
    ``test_shell_guardrails.py``.
    """
    monkeypatch.setattr(
        ShellToolsMixin, "_validate_command", staticmethod(lambda *a, **k: None)
    )


# ---------------------------------------------------------------------------
# The class table
# ---------------------------------------------------------------------------


class TestTimeoutClasses:
    """Every class in the table is named, enumerated, and has a stated default."""

    def test_the_four_classes_and_their_defaults(self):
        assert {name: cls.seconds for name, cls in TIMEOUT_CLASSES.items()} == {
            "test": 900,
            "build": 1800,
            "network": 300,
            "default": 30,
        }

    @pytest.mark.parametrize(
        "command",
        [
            "pytest tests/unit -q",
            "python -m pytest tests/",
            "uv run pytest -x",
            "npx jest --coverage",
            "npm test",
            "npm run test:e2e",
            "cargo test --all",
            "go test ./...",
            "mvn test",
            "tox -e py311",
            "gaia eval agent --category rag_quality",
            "pytest -q | tail -20",
        ],
    )
    def test_test_runners(self, command):
        assert classify_command(command).name == "test"

    @pytest.mark.parametrize(
        "command",
        [
            "pip install -e .",
            "python -m pip install requests",
            "uv pip install -e .[dev]",
            "npm install",
            "npm ci",
            "npm run build",
            "poetry install",
            "make -j8",
            "cmake --build build",
            "cargo build --release",
            "docker build -t gaia .",
            "apt-get install -y ffmpeg",
            "tsc -p tsconfig.json",
        ],
    )
    def test_builds_and_installs(self, command):
        assert classify_command(command).name == "build"

    @pytest.mark.parametrize(
        "command",
        [
            "git clone https://github.com/amd/gaia",
            "git fetch origin",
            "git push origin main",
            "gh issue list --repo amd/gaia",
            "curl -sf http://localhost:8000/health",
            "wget https://example.com/model.gguf",
            "docker pull ubuntu:24.04",
            "huggingface-cli download amd/model",
        ],
    )
    def test_vcs_and_network(self, command):
        assert classify_command(command).name == "network"

    @pytest.mark.parametrize(
        "command",
        [
            "ls -la",
            "cat README.md",
            "grep -r foo src/",
            "git status",
            "git log --oneline -10",
            "systeminfo",
            "",
        ],
    )
    def test_everything_else(self, command):
        assert classify_command(command).name == "default"

    def test_a_pipeline_takes_its_longest_segment(self):
        # The shell waits for the whole pipeline, so `grep` does not make a
        # 15-minute test run a 30-second command.
        assert classify_command("pytest -q | grep FAILED").seconds == 900


class TestResolveTimeout:
    def test_class_default_fills_the_gap(self):
        assert resolve_timeout("pytest tests/", None) == (900, "test")
        assert resolve_timeout("ls", None) == (30, "default")

    def test_an_explicit_timeout_wins(self):
        assert resolve_timeout("pytest tests/", 45) == (45, "test")

    @pytest.mark.parametrize("bad", [0, -1, MAX_COMMAND_TIMEOUT + 1, "soon"])
    def test_an_impossible_timeout_is_refused_not_clamped(self, bad):
        # Clamping would kill a command at a limit its caller never chose, with
        # nothing in the result to say why.
        with pytest.raises(ValueError):
            resolve_timeout("ls", bad)


# ---------------------------------------------------------------------------
# The class default reaches subprocess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected_timeout,expected_class",
    [
        ("pytest tests/unit", 900, "test"),
        ("pip install -e .", 1800, "build"),
        ("git clone https://github.com/amd/gaia", 300, "network"),
        ("ls -la", 30, "default"),
    ],
)
def test_each_class_reaches_subprocess_with_its_default(
    monkeypatch, unrestricted, command, expected_timeout, expected_class
):
    _, tools = _shell_tools()
    seen = {}

    def fake_run(*_args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _Completed(stdout="ok")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools["run_shell_command"](command)

    assert seen["timeout"] == expected_timeout
    assert result["timeout"] == expected_timeout
    assert result["timeout_class"] == expected_class


def test_an_explicit_timeout_still_overrides_the_class(monkeypatch, unrestricted):
    _, tools = _shell_tools()
    seen = {}

    def fake_run(*_args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools["run_shell_command"]("pytest tests/", timeout=60)

    assert seen["timeout"] == 60
    assert result["timeout"] == 60


def test_an_out_of_range_timeout_is_refused_with_an_actionable_error(monkeypatch):
    _, tools = _shell_tools()

    def fake_run(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("the command should never have run")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = tools["run_shell_command"]("ls", timeout=MAX_COMMAND_TIMEOUT * 2)

    assert result["status"] == "error"
    assert str(MAX_COMMAND_TIMEOUT) in result["error"]
    assert "wait_for_condition" in result["error"]


# ---------------------------------------------------------------------------
# Already true — must not regress
# ---------------------------------------------------------------------------


def _timing_out(monkeypatch, stdout="partial out", stderr="partial err"):
    def fake_run(*_args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd="ls", timeout=kwargs["timeout"], output=stdout, stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", fake_run)


class TestNoRegression:
    def test_the_applied_timeout_comes_back_in_the_result(self, monkeypatch):
        _, tools = _shell_tools()
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed())

        assert tools["run_shell_command"]("ls", timeout=17)["timeout"] == 17

    def test_a_timed_out_command_is_flagged(self, monkeypatch):
        _, tools = _shell_tools()
        _timing_out(monkeypatch)

        result = tools["run_shell_command"]("ls", timeout=5)

        assert result["timed_out"] is True
        assert result["status"] == "error"
        assert result["timeout"] == 5

    def test_partial_output_survives_the_kill(self, monkeypatch):
        _, tools = _shell_tools()
        _timing_out(monkeypatch, stdout="ran 3 tests", stderr="still going")

        result = tools["run_shell_command"]("ls", timeout=5)

        assert result["stdout"] == "ran 3 tests"
        assert result["stderr"] == "still going"

    def test_the_timeout_error_says_what_to_do(self, monkeypatch):
        _, tools = _shell_tools()
        _timing_out(monkeypatch)

        error = tools["run_shell_command"]("ls", timeout=5)

        assert "timed out after 5 seconds" in error["error"]
        assert "default" in error["error"]  # names the class it came from
        assert str(MAX_COMMAND_TIMEOUT) in error["hint"]


# ---------------------------------------------------------------------------
# wait_for_condition
# ---------------------------------------------------------------------------


class TestWaitForCondition:
    def test_it_returns_as_soon_as_the_predicate_succeeds(self, monkeypatch):
        _, tools = _shell_tools()
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(stdout="up"))

        result = tools["wait_for_condition"]("ls build/output.bin", timeout=30)

        assert result["condition_met"] is True
        assert result["status"] == "success"
        assert result["polls"] == 1
        assert result["stdout"] == "up"

    def test_the_deadline_expires_loudly(self, monkeypatch):
        _, tools = _shell_tools()
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _Completed(returncode=1, stderr="no")
        )

        result = tools["wait_for_condition"]("ls build/output.bin", timeout=1)

        assert result["condition_met"] is False
        assert result["timed_out"] is True
        assert result["status"] == "error"
        assert result["has_errors"] is True
        assert result["polls"] >= 1
        assert result["last_return_code"] == 1
        # Actionable: names the predicate, the deadline, and the last check.
        assert "ls build/output.bin" in result["error"]
        assert "deadline 1s" in result["error"]
        assert result["stderr"] == "no"

    def test_a_predicate_that_cannot_run_stops_the_wait_immediately(self, monkeypatch):
        _, tools = _shell_tools()

        def fake_run(*_args, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("a refused predicate should never execute")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = tools["wait_for_condition"]("rm -rf /", timeout=600)

        assert result["condition_met"] is False
        assert result["status"] == "error"
        assert result["polls"] == 1

    def test_a_cancel_signal_ends_the_wait(self, monkeypatch):
        import threading

        host, tools = _shell_tools()
        host._cancel_event = threading.Event()
        host._cancel_event.set()
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: _Completed(returncode=1)
        )

        result = tools["wait_for_condition"]("ls nope", timeout=WAIT_MAX_TIMEOUT)

        assert result["cancelled"] is True
        assert result["condition_met"] is False

    @pytest.mark.parametrize("timeout", [0, -5, WAIT_MAX_TIMEOUT + 1])
    def test_an_unbounded_wait_is_refused(self, timeout):
        _, tools = _shell_tools()

        result = tools["wait_for_condition"]("ls", timeout=timeout)

        assert result["status"] == "error"
        assert str(WAIT_MAX_TIMEOUT) in result["error"]

    @pytest.mark.parametrize(
        "poll_interval", [0, WAIT_MIN_POLL_INTERVAL - 1, WAIT_MAX_POLL_INTERVAL + 1]
    )
    def test_the_poll_interval_is_bounded(self, poll_interval):
        _, tools = _shell_tools()

        result = tools["wait_for_condition"]("ls", poll_interval=poll_interval)

        assert result["status"] == "error"
        assert str(WAIT_MIN_POLL_INTERVAL) in result["error"]

    def test_probes_are_not_metered_as_separate_commands(self):
        """A wait is charged once, not once per poll.

        Without the exemption the second probe trips the 3-per-10-seconds burst
        limit and the primitive returns a rate-limit error instead of waiting.
        """
        host = _Host()
        for _ in range(host.max_commands_per_minute):
            host._record_command_execution()
        assert host._check_rate_limit()[0] is False

        host._shell_polling = True
        try:
            assert host._check_rate_limit()[0] is True
            before = len(host.shell_command_times)
            host._record_command_execution()
            assert len(host.shell_command_times) == before
        finally:
            host._shell_polling = False

    def test_the_wait_tool_is_gated_and_grant_scoped(self):
        from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
        from gaia.agents.base.tool_grants import grant_scope

        assert "wait_for_condition" in TOOLS_REQUIRING_CONFIRMATION
        # "Always allow" is scoped to the command, never to the tool.
        scope = grant_scope("wait_for_condition", {"command": "ls build"})
        assert scope is not None and scope.label == "ls build"

    def test_a_refused_predicate_is_refused_before_the_prompt(self):
        host = _Host()

        refusal = host.policy_refusal_for_call(
            "wait_for_condition", {"command": "rm -rf /"}
        )

        assert refusal is not None and refusal["status"] == "error"


class TestWhatTheModelIsTold:
    """The table is worthless if the model never sees it.

    The registry takes a tool's description from its ``__doc__`` — the
    ``description=``/``parameters=`` kwargs on ``@tool`` are swallowed and
    ignored — and the non-native prompt path renders only the FIRST LINE of it.
    So the class defaults have to be in that first line, and stay there.
    """

    def _first_line(self, tool_name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        _Host().register_shell_tools()
        return _TOOL_REGISTRY[tool_name]["description"].splitlines()[0]

    def test_the_docstring_states_every_class(self):
        first_line = self._first_line("run_shell_command")

        for command_class in TIMEOUT_CLASSES.values():
            assert f"{command_class.seconds}s" in first_line, (
                f"the {command_class.name} default is not in the one line of "
                f"run_shell_command the model actually sees"
            )

    def test_timeout_is_still_declared_as_an_integer(self):
        """An unreadable annotation is rendered as a string to the model."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        _Host().register_shell_tools()

        for tool_name in ("run_shell_command", "wait_for_condition"):
            params = _TOOL_REGISTRY[tool_name]["parameters"]
            assert params["timeout"]["type"] == "integer"
            assert params["timeout"]["required"] is False

    def test_the_wait_tool_states_its_bounds(self):
        first_line = self._first_line("wait_for_condition")

        assert str(WAIT_MAX_TIMEOUT) in first_line
        assert str(WAIT_MIN_POLL_INTERVAL) in first_line


def test_the_tool_guard_outlasts_the_longest_command(monkeypatch):
    """The agent-level tool timeout must not fire before the command's own.

    A 30-minute build under a 180s tool guard is abandoned by the loop while the
    subprocess is still inside its (correct) window — the feature would be inert.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    _Host().register_shell_tools()

    assert _TOOL_REGISTRY["run_shell_command"]["timeout"] > MAX_COMMAND_TIMEOUT
    assert _TOOL_REGISTRY["wait_for_condition"]["timeout"] > WAIT_MAX_TIMEOUT
