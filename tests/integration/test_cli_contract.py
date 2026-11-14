# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What every `gaia` command owes the person who runs it.

The commands are read from the CLI's own parser, so one added tomorrow is
covered without a new case. Run by .github/workflows/test_cli_contract.yml.
Each is run for real, as a subprocess, against a model server that records
its requests, and then with no model server at all:

- It never ends in a traceback.
- With the model server down, a command that needs it exits non-zero and says
  why (rc18: `gaia download --list` exited 0 and listed every model as missing).
- It prints the model's answer once (rc18: `gaia prompt` printed it twice).
- With `--base-url`, every request goes to that server and none to the default
  one (rc18: `gaia talk --index` and `gaia api start` ignored the flag).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from gaia.cli import build_parser
from tests.fixtures.fake_lemonade import ANSWER, FakeLemonade

REPO = Path(__file__).resolve().parents[2]
#: Longest a command may run. One still up by then is a server or a prompt.
TIMEOUT_S = 25
#: Never listening, so a connection to it is refused at once.
DEAD_URL = "http://127.0.0.1:1/api/v1"

#: Commands this table lists but never starts, each with why.
NEVER_STARTED = {
    "kill": "ends GAIA and Lemonade processes for the whole machine",
    "uninstall": "removes GAIA's files",
    "init": "installs Lemonade and downloads models",
    "daemon": "starts a machine-wide process; tests/integration/test_daemon.py",
    "lemonade embedded": "downloads and starts a model server",
    "eval": "runs an eval against whichever backend answers on the default port",
    "test": "runs model smoke tests that need a loaded model",
    "telegram": "starts a background adapter; tests/unit/cli/test_cli_telegram.py",
    "slack": "starts a background adapter",
}

#: Invocations the parser walk cannot reach: an optional flag changes what runs.
EXTRA_INVOCATIONS = [
    ["download", "--list"],
    ["prompt", "Reply with one word."],
    ["llm", "Reply with one word."],
    ["-p", "Reply with one word."],
]

#: Commands whose job is to report whether the model server is up: saying
#: "down" with exit 0 is their correct answer.
REPORTS_SERVER_STATE: set = set()

#: Commands that open a listening port; each gets a free one, not its default.
BINDS_A_PORT = {"mcp start", "api start"}

#: Rules broken today, each naming its issue. Strict: a fix has to delete its
#: line. Keyed by (rule, command as typed); the rule is "traceback" or
#: "model server".
KNOWN_BUGS = {
    ("model server", "chat"): "amd/gaia#7345",
    ("model server", "-p Reply with one word."): "amd/gaia#7345",
}
#: Commands whose --base-url is not the model server's address.
OTHER_BASE_URL = {
    "gateway install": "its --base-url is the gateway's address",
}


@dataclass
class Command:
    args: list
    base_url: bool

    @property
    def name(self) -> str:
        return " ".join(self.args)


def _needs(parser) -> list:
    one_of = [
        group._group_actions[0]
        for group in parser._mutually_exclusive_groups
        if group.required
    ]
    values = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        positional = not action.option_strings
        if positional and action.nargs in ("?", "*", argparse.REMAINDER):
            continue
        if not positional and not action.required and action not in one_of:
            continue
        if action.choices:
            value = str(next(iter(action.choices)))
        elif action.type in (int, float):
            value = "1"
        else:
            value = "contract-walk"
        if not positional:
            values.append(action.option_strings[0])
        values.extend([value] * (action.nargs if isinstance(action.nargs, int) else 1))
    return values


def _takes_base_url(parser) -> bool:
    return any("--base-url" in action.option_strings for action in parser._actions)


def _walk(parser, path: list, found: list) -> None:
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if path and not any(group.required for group in groups):
        found.append(Command([*path, *_needs(parser)], _takes_base_url(parser)))
    for group in groups:
        seen = set()
        for name, child in group.choices.items():
            if id(child) not in seen:
                seen.add(id(child))
                _walk(child, [*path, name], found)


def never_started(args: list) -> str | None:
    for depth in range(len(args), 0, -1):
        reason = NEVER_STARTED.get(" ".join(args[:depth]))
        if reason:
            return reason
    return None


def commands() -> list:
    parser = build_parser()
    found: list = []
    _walk(parser, [], found)
    for args in EXTRA_INVOCATIONS:
        namespace_parser = parser
        base_url = False
        for word in args:
            groups = [
                a
                for a in namespace_parser._actions
                if isinstance(a, argparse._SubParsersAction)
            ]
            child = next((g.choices[word] for g in groups if word in g.choices), None)
            if child is None:
                break
            namespace_parser = child
        base_url = _takes_base_url(namespace_parser)
        found.append(Command(list(args), base_url))
    return [c for c in found if never_started(c.args) is None]


COMMANDS = commands()
#: Commands held to the model-server rules: those that take --base-url, and
#: the extra invocations above. Each costs two or three real runs.
MODEL_COMMANDS = [
    c
    for c in COMMANDS
    if (c.base_url or c.args in EXTRA_INVOCATIONS) and c.name not in OTHER_BASE_URL
]


def _params(commands_: list, rule: str):
    for command in commands_:
        marks = []
        reason = KNOWN_BUGS.get((rule, command.name))
        if reason:
            marks.append(
                pytest.mark.xfail(reason=reason, strict=True, raises=AssertionError)
            )
        yield pytest.param(command, id=command.name, marks=marks)


@dataclass
class Run:
    code: int | None  # None: still running at the time limit, and stopped
    output: str
    stdout: str


def _env(home: Path, base_url: str) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GAIA_", "LEMONADE_", "HF_", "ANTHROPIC_", "FIREWORKS_"))
    }
    hub = REPO / "hub" / "agents"
    paths = [str(REPO / "src"), *[str(p) for p in sorted(hub.glob("*/python"))]]
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "AppData" / "Roaming"),
        LOCALAPPDATA=str(home / "AppData" / "Local"),
        GAIA_HOME=str(home / ".gaia"),
        GAIA_DAEMON_HOME=str(home / ".gaia" / "host"),
        HF_HOME=str(home / "hf"),
        LEMONADE_BASE_URL=base_url,
        PYTHONPATH=os.pathsep.join(paths),
        PYTHONIOENCODING="utf-8",
        NO_PROXY="127.0.0.1,localhost",
        NO_COLOR="1",
    )
    # A daemon a command starts dies with this test process at the latest.
    env["GAIA_DAEMON_EXIT_WITH_PID"] = str(os.getpid())
    return env


def _end_tree(pid: int) -> None:
    import psutil

    try:
        parent = psutil.Process(pid)
        tree = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return
    for proc in tree:
        # One that already exited must not spare the rest of the tree.
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue


def _run(args: list, home: Path, base_url: str) -> Run:
    home.mkdir(parents=True, exist_ok=True)
    out_path, err_path = home / "stdout.txt", home / "stderr.txt"
    with out_path.open("wb") as out, err_path.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", "gaia.cli", *args],
            env=_env(home, base_url),
            cwd=str(home),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        )
        try:
            code = proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _end_tree(proc.pid)
            proc.wait(timeout=30)
            code = None
    _stop_daemon(home)
    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    return Run(code, stdout + stderr, stdout)


def _stop_daemon(home: Path) -> None:
    """Stop the daemon a command started in *home*; several start one."""
    host = home / ".gaia" / "host"
    if not (host / "instance.json").exists():
        return
    from gaia.daemon.strays import find_daemons, same_home, stop_daemon

    for daemon in find_daemons():
        if daemon.home is not None and same_home(daemon.home, host):
            stop_daemon(daemon)


def _server() -> FakeLemonade:
    return FakeLemonade(
        downloaded=(
            "Gemma-4-E4B-it-GGUF",
            "user.embeddinggemma-300m-GGUF",
            "fireworks.deepseek-v4p1-flash",
        )
    )


def _free_port() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


def _args(command: Command) -> list:
    """The command line, with a free port for a command that would bind one."""
    args = list(command.args)
    if command.name in BINDS_A_PORT:
        args += ["--port", _free_port()]
    return args


def _not_timed_out_on_known_bug(rule: str, command: Command, run: Run) -> None:
    """A known bug a command never reached must not read as fixed."""
    issue = KNOWN_BUGS.get((rule, command.name))
    if issue and run.code is None:
        pytest.fail(
            f"`gaia {command.name}` was stopped at the {TIMEOUT_S} s limit "
            f"before reaching {issue}"
        )


def _no_hang_when_down(command: Command, up: Run, down: Run) -> None:
    """A command that finishes with the server up must not outlast the limit with it down.

    Retrying a dead server past the limit is how `gaia download --list` exits 0
    on a down server and still slips past the exit-code check.
    """
    assert up.code is None or down.code is not None, (
        f"`gaia {command.name}` finished with the model server up, but with it "
        f"down it was still running at the {TIMEOUT_S} s limit:\n"
        f"{down.output[-2000:]}"
    )


def _no_traceback(command: Command, run: Run, when: str) -> None:
    assert (
        "Traceback (most recent call last)" not in run.output
    ), f"`gaia {command.name}` ended in a traceback {when}:\n{run.output[-2000:]}"


@pytest.mark.parametrize("command", list(_params(COMMANDS, "traceback")))
def test_no_command_ends_in_a_traceback(command, tmp_path):
    """Whatever a command cannot do, it says so in words."""
    args = _args(command)
    if command.base_url:
        args += ["--base-url", DEAD_URL]
    run = _run(args, tmp_path / "down", DEAD_URL)
    _not_timed_out_on_known_bug("traceback", command, run)
    _no_traceback(command, run, "with the model server down")


@pytest.mark.parametrize("command", list(_params(MODEL_COMMANDS, "model server")))
def test_model_server_contract(command, tmp_path):
    with _server() as named, _server() as default:
        args = _args(command)
        if command.base_url:
            # The flag names one server; the environment names another.
            args += ["--base-url", named.base_url]
        up = _run(args, tmp_path / "up", default.base_url)
        asked = bool(named.requests or default.requests)
        stray = [f"{r.method} {r.path}" for r in default.requests]
    _not_timed_out_on_known_bug("model server", command, up)
    _no_traceback(command, up, "with the model server up")
    if command.base_url:
        assert not stray, (
            f"`gaia {command.name} --base-url X` sent these to the default "
            f"server instead of X: {stray}"
        )
    printed = up.stdout.count(ANSWER)
    assert printed <= 1, (
        f"`gaia {command.name}` printed the model's answer {printed} times:\n"
        f"{up.stdout[-2000:]}"
    )
    if not asked:
        return

    args = _args(command)
    if command.base_url:
        args += ["--base-url", DEAD_URL]
    down = _run(args, tmp_path / "down", DEAD_URL)
    _not_timed_out_on_known_bug("model server", command, down)
    _no_hang_when_down(command, up, down)
    if down.code is None or command.name in REPORTS_SERVER_STATE:
        return
    assert down.code != 0 and down.output.strip(), (
        f"`gaia {command.name}` uses the model server, but with it down it "
        f"exited {down.code}:\n{down.output[-2000:]}"
    )


def test_the_tables_name_real_commands():
    """A stale entry reads as coverage, or as a known bug, that is not there."""
    every: list = []
    _walk(build_parser(), [], every)
    paths = {" ".join(c.args[:depth]) for c in every for depth in (1, 2, 3)}
    assert set(NEVER_STARTED) <= paths
    names = {c.name for c in COMMANDS}
    assert {name for _rule, name in KNOWN_BUGS} <= names
    assert {rule for rule, _name in KNOWN_BUGS} <= {"traceback", "model server"}
    assert REPORTS_SERVER_STATE <= names
    assert BINDS_A_PORT <= names
    assert set(OTHER_BASE_URL) <= names


def test_most_commands_are_started():
    every: list = []
    _walk(build_parser(), [], every)
    assert len(COMMANDS) > 0.7 * len(every)


@pytest.mark.parametrize("code", [None, 0, 1])
def test_a_timeout_never_reads_as_a_fixed_bug(code):
    """A stopped run proves nothing, so a known-bug cell fails instead of passing."""
    (rule, name), issue = next(iter(KNOWN_BUGS.items()))
    run = Run(code, "", "")
    if code is None:
        with pytest.raises(pytest.fail.Exception, match=issue):
            _not_timed_out_on_known_bug(rule, Command(name.split(), False), run)
    else:
        _not_timed_out_on_known_bug(rule, Command(name.split(), False), run)
    # A long-running command with no known bug is still allowed to time out.
    _not_timed_out_on_known_bug(
        rule, Command(["no-such-command"], False), Run(None, "", "")
    )
    # Same command, other rule: its timeout is not this bug's business.
    other = "model server" if rule == "traceback" else "traceback"
    if (other, name) not in KNOWN_BUGS:
        _not_timed_out_on_known_bug(
            other, Command(name.split(), False), Run(None, "", "")
        )


def test_a_timeout_fails_a_known_bug_cell_instead_of_xpassing():
    """pytest.fail is not an AssertionError, so raises= keeps it from counting as the bug."""
    marks = [
        mark
        for rule in ("traceback", "model server")
        for param in _params(COMMANDS, rule)
        for mark in param.marks
        if mark.name == "xfail"
    ]
    assert marks, "no known-bug cell to check"
    for mark in marks:
        assert mark.kwargs["strict"] is True
        assert mark.kwargs["raises"] is AssertionError
    assert not issubclass(pytest.fail.Exception, AssertionError)


@pytest.mark.parametrize(
    "up_code, down_code, hangs",
    [(0, None, True), (1, None, True), (0, 1, False), (None, None, False)],
)
def test_a_hang_on_a_down_server_is_not_a_pass(up_code, down_code, hangs):
    """Only a command that also outlasts the limit with the server up may do so down."""
    command = Command(["download", "--list"], False)
    up, down = Run(up_code, "", ""), Run(down_code, "", "")
    if hangs:
        with pytest.raises(AssertionError, match="still running"):
            _no_hang_when_down(command, up, down)
    else:
        _no_hang_when_down(command, up, down)
