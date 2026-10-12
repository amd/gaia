# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Background processes: ``start_process`` and its companion tools.

Real subprocesses throughout. What these tools promise — the tree dies on
stop, nothing outlives the agent, output stays bounded — is a statement about
the OS, and a stubbed Popen would only prove the kill was *called*.
"""

import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION, Agent
from gaia.agents.base.tool_grants import grant_scope
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.base.turn_scope import _ALWAYS_RELATED
from gaia.agents.tools import background_process as bp
from gaia.agents.tools import process_tools, shell_tools
from gaia.agents.tools.background_process import (
    OutputLog,
    complete_utf8,
    terminal_view,
)
from gaia.agents.tools.process_tools import PROCESS_TOOL_NAMES, ProcessToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin

psutil = pytest.importorskip("psutil")

#: A bare interpreter name, the way a user's command line spells it; a full
#: path is not in the developer command set even under full access.
PY = next((name for name in ("python", "python3") if shutil.which(name)), None)
if PY is None:
    pytest.skip("no python on PATH to launch", allow_module_level=True)


class _Console:
    """A session where a person answers the confirmation prompt."""

    full_access = False
    auto_approve_gated_tools = False


class _FullAccess:
    """The TUI's /full-access: developer commands such as python run unasked."""

    full_access = True


class _Host(ShellToolsMixin, ProcessToolsMixin):
    def __init__(self, console=None, scratch_dir=None):
        super().__init__()
        self.console = console
        self.scratch_dir = scratch_dir
        self._cancel_event = threading.Event()


def _build(console, scratch_dir=None):
    host = _Host(console=console, scratch_dir=scratch_dir)
    host.register_shell_tools()
    host.register_process_tools()
    return host


@pytest.fixture(autouse=True)
def _no_blanket_approval(monkeypatch):
    monkeypatch.delenv("GAIA_AUTO_APPROVE_TOOLS", raising=False)


@pytest.fixture
def audited(monkeypatch):
    """Full-access launches, recorded here instead of the real audit log."""
    commands = []
    monkeypatch.setattr(
        ShellToolsMixin,
        "_audit_shell_execution",
        lambda self, command, cwd, segments: commands.append(command),
    )
    return commands


@pytest.fixture
def host(tmp_path, audited):
    """Full access, so test scripts can run; the policy is tested on ``gated``."""
    h = _build(_FullAccess(), str(tmp_path / "scratch"))
    yield h
    h.close_background_processes()


@pytest.fixture
def gated(tmp_path):
    """The default session: allowlist, confirmation tiers and rate limit all on."""
    h = _build(_Console(), str(tmp_path / "scratch"))
    yield h
    h.close_background_processes()


def _tool(name):
    return get_tool_metadata(name)["function"]


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _gone_within(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _wait_for_file(path, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return path.read_text().strip()
        time.sleep(0.1)
    raise AssertionError(f"{path} never appeared")


# A child that starts a grandchild, records the grandchild's pid, and waits.
_TREE = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open(sys.argv[1], "w").write(str(child.pid))
print("ready", flush=True)
time.sleep(120)
"""


# ---------------------------------------------------------------------------
# Policy parity: the background path is not a way around the shell policy
# ---------------------------------------------------------------------------


class TestPolicyParity:
    @pytest.mark.parametrize(
        "command",
        [
            "echo hi > out.txt",  # redirection
            "sleep 100 &",  # shell backgrounding
            "echo $(whoami)",  # substitution
            "gh auth token",  # escalation the policy refuses outright
            "git -c core.pager=evil.sh status",
            "powershell -EncodedCommand ZQBjAGgAbwA=",
            "python -c pass",  # granted only to a skill, or under full access
            "",
        ],
    )
    def test_refused_exactly_as_run_shell_command_refuses(self, gated, command):
        foreground = _tool("run_shell_command")(command)
        background = _tool("start_process")(command)

        assert foreground["status"] == background["status"] == "error"
        assert background["error"] == foreground["error"]
        assert background.get("tier") == foreground.get("tier")
        assert background["executed"] is False
        assert not gated._process_table().all()

    @pytest.mark.parametrize(
        "command", ["echo hi > out.txt", "gh auth token", "git commit -m x"]
    )
    def test_pre_prompt_refusal_matches(self, gated, command):
        args = {"command": command}
        assert gated.policy_refusal_for_call(
            "start_process", args
        ) == gated.policy_refusal_for_call("run_shell_command", args)

    @pytest.mark.parametrize(
        "console",
        [
            None,  # nothing could have shown the prompt (#2210)
            SimpleNamespace(full_access=False, auto_approve_gated_tools=True),
        ],
    )
    def test_nothing_starts_without_a_person_or_full_access(self, console):
        # A blanket pre-approval admits the read-only list, which in the
        # foreground still ends at its timeout; in the background nothing would.
        h = _build(console)
        try:
            for command in ("git --version", "git commit -m x"):
                result = _tool("start_process")(command)
                assert result["executed"] is False
                assert "Background processes run only" in result["error"]
            assert not h._process_table().all()
        finally:
            h.close_background_processes()

    def test_a_blanket_approval_is_refused_before_the_prompt(self):
        h = _build(SimpleNamespace(full_access=False, auto_approve_gated_tools=True))
        try:
            refusal = h.policy_refusal_for_call(
                "start_process", {"command": "git --version"}
            )
            assert "Background processes run only" in refusal["error"]
            assert (
                h.policy_refusal_for_call(
                    "run_shell_command", {"command": "git --version"}
                )
                is None
            )
        finally:
            h.close_background_processes()

    def test_paths_are_checked_like_the_foreground(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        (tmp_path / "secret.txt").write_text("secret")

        class _Validator:
            def is_path_allowed(self, path):
                return os.path.realpath(path).startswith(os.path.realpath(allowed))

            def scratch_hint(self, path):
                return ""

        h = _build(_Console())
        h.path_validator = _Validator()
        try:
            command = "cat ../secret.txt"
            foreground = _tool("run_shell_command")(
                command, working_directory=str(allowed)
            )
            background = _tool("start_process")(command, working_directory=str(allowed))
            assert "Access denied" in foreground["error"]
            assert background["error"] == foreground["error"]

            outside = _tool("start_process")(
                "git --version", working_directory=str(tmp_path)
            )
            assert "not in allowed paths" in outside["error"]
            assert outside["executed"] is False
        finally:
            h.close_background_processes()

    def test_start_process_always_asks(self, gated, monkeypatch):
        assert "start_process" in TOOLS_REQUIRING_CONFIRMATION
        # A read-only gh grant covers run_shell_command reads, never a
        # background run of the same command.
        monkeypatch.setattr(
            shell_tools, "skill_granted_binaries", lambda host: frozenset({"gh"})
        )
        args = {"command": "gh issue list"}
        assert gated.skill_grant_covers_call("run_shell_command", args) is True
        assert gated.skill_grant_covers_call("start_process", args) is False

    def test_always_allow_is_scoped_to_the_command(self):
        scope = grant_scope("start_process", {"command": "npm run dev"})
        assert scope is not None
        assert scope.key.startswith("start_process:")
        pipeline = {"command": "a | b"}
        foreground = grant_scope("run_shell_command", pipeline)
        background = grant_scope("start_process", pipeline)
        assert background.label == foreground.label
        assert all(key.startswith("start_process:") for key in background.keys)
        assert grant_scope("start_process", {"command": "rm -rf build"}) is None

    def test_rate_limit_is_shared_with_run_shell_command(self, gated):
        gated.max_rate_limit_wait_seconds = 0
        for _ in range(gated.max_commands_per_10_seconds):
            assert _tool("run_shell_command")("git --version")["status"] == "success"
        refused = _tool("start_process")("git --version")
        assert refused.get("rate_limited") is True
        assert refused["executed"] is False

    def test_full_access_launch_is_audited(self, host, audited):
        command = f"{PY} -c pass"
        assert _tool("start_process")(command)["status"] == "success"
        assert audited == [command]

    def test_process_state_tools_are_the_agents_own_state(self):
        assert set(PROCESS_TOOL_NAMES) - {"start_process"} <= _ALWAYS_RELATED
        assert "start_process" not in _ALWAYS_RELATED


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_returns_while_running_then_reports_exit_and_output(self, host, tmp_path):
        script = _script(
            tmp_path,
            "count.py",
            """
            import time
            for i in range(4):
                print("line", i, flush=True)
                time.sleep(0.8)
            raise SystemExit(3)
            """,
        )
        started = time.monotonic()
        result = _tool("start_process")(f'{PY} "{script}"', name="counter")
        assert time.monotonic() - started < process_tools.STARTUP_WATCH_SECONDS + 2
        assert result["status"] == "success"
        assert result["state"] == "running"
        assert result["name"] == "counter"
        assert result["check_result"] is None
        pid = result["process_id"]

        done = _tool("wait_for_process")(pid, timeout=30)
        assert done["state"] == "exited"
        assert done["exit_code"] == 3
        assert done["has_errors"] is True
        assert "line 3" in done["new_output"]
        # Shown output is not shown again.
        again = _tool("process_status")(pid)
        assert again["new_output"] == ""
        assert "line 3" in again["last_lines"]

    def test_a_command_that_fails_at_once_says_so(self, host, tmp_path):
        script = _script(tmp_path, "boom.py", "raise SystemExit('broken')\n")
        result = _tool("start_process")(f'{PY} "{script}"')
        assert result["state"] == "exited"
        assert result["exit_code"] == 1
        assert "broken" in result["new_output"]

    def test_connectors_run_like_the_foreground(self, host, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        marker = tmp_path / "never.txt"
        _script(sub, "where.py", "import os; print(os.getcwd())\n")
        _script(tmp_path, "fail.py", "raise SystemExit(2)\n")
        _script(tmp_path, "never.py", f"open({str(marker)!r}, 'w').write('ran')\n")

        ok = _tool("start_process")(f'cd "{sub}" && {PY} where.py')
        _tool("wait_for_process")(ok["process_id"], timeout=30)
        printed = _tool("read_process_output")(ok["process_id"])["content"]
        assert os.path.realpath(printed.strip()) == os.path.realpath(str(sub))

        failing = _tool("start_process")(
            f'{PY} "{tmp_path / "fail.py"}" && {PY} "{tmp_path / "never.py"}"'
        )
        done = _tool("wait_for_process")(failing["process_id"], timeout=30)
        assert done["exit_code"] == 2
        assert [s["return_code"] for s in done["steps"]] == [2]
        assert not marker.exists()

    def test_starting_does_not_move_the_shell_session(self, host, tmp_path):
        before = host.shell_session.cwd
        started = _tool("start_process")(f'cd "{tmp_path}" && {PY} -c pass')
        _tool("wait_for_process")(started["process_id"], timeout=30)
        assert host.shell_session.cwd == before

    def test_list_shows_every_process(self, host, tmp_path):
        script = _script(tmp_path, "nap.py", "import time; time.sleep(30)\n")
        a = _tool("start_process")(f'{PY} "{script}"')["process_id"]
        b = _tool("start_process")(f"{PY} -c pass")["process_id"]
        listing = _tool("process_status")()
        ids = {p["process_id"]: p for p in listing["processes"]}
        assert set(ids) == {a, b}
        assert ids[a]["state"] == "running"
        assert listing["running"] >= 1

    def test_unknown_id_names_the_known_ones(self, host):
        known = _tool("start_process")(f"{PY} -c pass")["process_id"]
        result = _tool("process_status")("p999")
        assert result["status"] == "error"
        assert known in result["error"]

    def test_running_limit_is_enforced(self, host, tmp_path, monkeypatch):
        monkeypatch.setattr(process_tools, "MAX_RUNNING", 1)
        monkeypatch.setattr(bp, "MAX_RUNNING", 1)
        script = _script(tmp_path, "nap.py", "import time; time.sleep(30)\n")
        first = _tool("start_process")(f'{PY} "{script}"')
        assert first["state"] == "running"
        second = _tool("start_process")(f'{PY} "{script}"')
        assert second["status"] == "error"
        assert second["executed"] is False
        assert first["process_id"] in str(second["running"])

    def test_finished_processes_are_evicted_with_their_logs(self, host, monkeypatch):
        monkeypatch.setattr(bp, "MAX_RETAINED", 2)
        first = _tool("start_process")(f"{PY} -c pass")
        _tool("wait_for_process")(first["process_id"], timeout=30)
        log = first["log_file"]
        for _ in range(2):
            started = _tool("start_process")(f"{PY} -c pass")
            _tool("wait_for_process")(started["process_id"], timeout=30)
        assert host._process_table().get(first["process_id"]) is None
        assert not os.path.exists(log)

    def test_eviction_disposes_outside_the_table_lock(self, host, monkeypatch):
        monkeypatch.setattr(bp, "MAX_RETAINED", 2)
        table = host._process_table()
        held = []
        real_dispose = bp.BackgroundProcess.dispose

        def dispose(self, *args, **kwargs):
            held.append(table._lock.locked())
            return real_dispose(self, *args, **kwargs)

        monkeypatch.setattr(bp.BackgroundProcess, "dispose", dispose)
        for _ in range(3):
            started = _tool("start_process")(f"{PY} -c pass")
            _tool("wait_for_process")(started["process_id"], timeout=30)
        # A slow stop must not block process_status or start_process meanwhile.
        assert held == [False]

    def test_a_finished_test_run_is_a_check(self, host, tmp_path):
        _script(tmp_path, "test_ok.py", "def test_ok():\n    assert True\n")
        # Rooted at tmp_path: otherwise the child's collection walks the
        # shared temp dir, where other runs' folders vanish mid-scan.
        ini = _script(tmp_path, "pytest.ini", "[pytest]\n")
        started = _tool("start_process")(
            f'{PY} -m pytest -q -p no:cacheprovider -c "{ini}" '
            f'"{tmp_path / "test_ok.py"}"'
        )
        done = _tool("wait_for_process")(started["process_id"], timeout=120)
        if "No module named pytest" in done["new_output"]:
            pytest.skip(f"{PY} on PATH has no pytest")
        assert done["exit_code"] == 0, done["new_output"]
        assert done["check_result"]["label"] == "pytest"
        assert done["check_result"]["passed"] is True
        # Grouped with the same command run in the foreground.
        foreground = _tool("run_shell_command")(started["command"])
        assert done["check_result"]["target"] == foreground["check_result"]["target"]


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------


class TestWait:
    def test_pattern_returns_as_soon_as_the_line_appears(self, host, tmp_path):
        script = _script(
            tmp_path,
            "server.py",
            """
            import time
            time.sleep(2.5)
            print("Listening on http://127.0.0.1:8123", flush=True)
            time.sleep(60)
            """,
        )
        started = _tool("start_process")(f'{PY} "{script}"')
        assert started["state"] == "running"
        result = _tool("wait_for_process")(
            started["process_id"], timeout=30, pattern=r"Listening on \S+"
        )
        assert result["matched"] == "Listening on http://127.0.0.1:8123"
        assert result["state"] == "running"
        assert result["waited_seconds"] < 20

    def test_matched_is_exactly_the_matching_line(self, host, tmp_path):
        # Several lines first: \r\n endings once shifted the reported line.
        script = _script(
            tmp_path,
            "noisy.py",
            """
            for word in ("aaaa", "bbbb", "cccc"):
                print(word, flush=True)
            print("Listening on 3000", flush=True)
            print("dddd", flush=True)
            import time; time.sleep(60)
            """,
        )
        started = _tool("start_process")(f'{PY} "{script}"')
        result = _tool("wait_for_process")(
            started["process_id"], timeout=10, pattern=r"Listening on \d+"
        )
        assert result["matched"] == "Listening on 3000"

    def test_a_pattern_printed_before_the_wait_still_matches(self, host, tmp_path):
        script = _script(
            tmp_path,
            "early.py",
            "print('ready', flush=True)\nimport time; time.sleep(60)\n",
        )
        started = _tool("start_process")(f'{PY} "{script}"')
        result = _tool("wait_for_process")(
            started["process_id"], timeout=10, pattern="ready"
        )
        assert result["matched"] == "ready"

    def test_deadline_reports_still_running_not_an_error(self, host, tmp_path):
        script = _script(tmp_path, "nap.py", "import time; time.sleep(60)\n")
        started = _tool("start_process")(f'{PY} "{script}"')
        result = _tool("wait_for_process")(started["process_id"], timeout=1)
        assert result["status"] == "success"
        assert result["still_running"] is True

    def test_stop_ends_the_wait_but_not_the_process(self, host, tmp_path):
        script = _script(tmp_path, "nap.py", "import time; time.sleep(60)\n")
        started = _tool("start_process")(f'{PY} "{script}"')
        threading.Timer(0.5, host._cancel_event.set).start()
        result = _tool("wait_for_process")(started["process_id"], timeout=60)
        assert result["cancelled"] is True
        assert result["waited_seconds"] < 10
        assert host._process_table().get(started["process_id"]).state == "running"

    @pytest.mark.parametrize(
        "kwargs, needle",
        [
            ({"timeout": 0}, "between 1 and"),
            ({"timeout": 601}, "between 1 and"),
            ({"pattern": "("}, "not a valid regex"),
            ({"pattern": "(a+)+$"}, "cannot be stopped"),
            ({"pattern": r"(\w+\s?)+:"}, "cannot be stopped"),
            ({"pattern": "x" * 201}, "at most 200"),
        ],
    )
    def test_bad_arguments_are_refused(self, host, kwargs, needle):
        started = _tool("start_process")(f"{PY} -c pass")
        result = _tool("wait_for_process")(started["process_id"], **kwargs)
        assert result["status"] == "error"
        assert needle in result["error"]


# ---------------------------------------------------------------------------
# Stopping and orphan cleanup
# ---------------------------------------------------------------------------


class TestTreeKill:
    def test_stop_kills_the_grandchild(self, host, tmp_path):
        script = _script(tmp_path, "tree.py", _TREE)
        pid_file = tmp_path / "grandchild.pid"
        started = _tool("start_process")(f'{PY} "{script}" "{pid_file}"')
        grandchild = int(_wait_for_file(pid_file))
        assert _alive(grandchild)

        stopped = _tool("stop_process")(started["process_id"])

        assert stopped["state"] == "stopped"
        assert stopped["stopped"] is True
        assert stopped["exit_code"] is None
        assert _gone_within(grandchild, 10)

    def test_closing_the_agent_kills_everything_and_removes_logs(
        self, tmp_path, audited
    ):
        h = _build(_FullAccess(), str(tmp_path / "scratch"))
        script = _script(tmp_path, "tree.py", _TREE)
        pid_file = tmp_path / "grandchild.pid"
        _tool("start_process")(f'{PY} "{script}" "{pid_file}"')
        grandchild = int(_wait_for_file(pid_file))
        log_dir = h._process_table().log_dir

        h.close_background_processes()

        assert _gone_within(grandchild, 10)
        assert not log_dir.exists()

    def test_hard_killed_agent_takes_its_processes_with_it(self, tmp_path):
        """SIGKILL / TerminateProcess skips every Python cleanup path.

        What remains is the OS: the kill-on-close job object on Windows, the
        guardian's pipe closing on POSIX.
        """
        script = _script(tmp_path, "tree.py", _TREE)
        pid_file = tmp_path / "grandchild.pid"
        command = f'{PY} "{script}" "{pid_file}"'
        agent = _script(
            tmp_path,
            "agent.py",
            f"""
            import sys, time
            sys.path.insert(0, {os.path.abspath("src")!r})
            from gaia.agents.base.tools import get_tool_metadata
            from gaia.agents.tools.shell_tools import ShellToolsMixin
            from gaia.agents.tools.process_tools import ProcessToolsMixin

            class Host(ShellToolsMixin, ProcessToolsMixin):
                console = type("FullAccess", (), {{"full_access": True}})()

                def _audit_shell_execution(self, *args):
                    pass

            h = Host()
            h.register_shell_tools()
            h.register_process_tools()
            r = get_tool_metadata("start_process")["function"]({command!r})
            assert r["status"] == "success", r
            time.sleep(120)
            """,
        )
        env = {**os.environ, "GAIA_ALLOW_EXTERNAL_IMPORTS": "1"}
        parent = subprocess.Popen([sys.executable, str(agent)], env=env)
        try:
            grandchild = int(_wait_for_file(pid_file, seconds=60))
            assert _alive(grandchild)
            parent.kill()
            parent.wait(10)
            assert _gone_within(grandchild, 15)
        finally:
            if parent.poll() is None:
                parent.kill()


# ---------------------------------------------------------------------------
# Output paging and bounds
# ---------------------------------------------------------------------------


class TestOutputLog:
    def test_offsets_survive_rotation_and_the_cap_holds(self, tmp_path):
        log = OutputLog(tmp_path / "p.log", cap_bytes=100)
        for i in range(30):
            log.write(f"{i:02d}-------\n".encode())  # 10 bytes each
        assert log.total_bytes == 300
        kept = (tmp_path / "p.log").stat().st_size + (
            tmp_path / "p.log.1"
        ).stat().st_size
        assert kept <= 100
        earliest = log.earliest_offset
        assert earliest > 0
        assert log.read(earliest, 10) == f"{earliest // 10:02d}-------\n".encode()
        assert log.read(290, 100) == b"29-------\n"
        # Before the kept window is clamped to it.
        assert log.read(0, 10) == log.read(earliest, 10)
        log.close(delete=True)
        assert not list(tmp_path.iterdir())

    def test_a_read_spans_both_files(self, tmp_path):
        log = OutputLog(tmp_path / "p.log", cap_bytes=40)
        log.write(b"a" * 25)  # rotates into .1
        log.write(b"b" * 5)
        assert log.read(log.earliest_offset, 100) == b"a" * 25 + b"b" * 5
        log.close()

    def test_a_log_that_cannot_rotate_keeps_writing_and_keeps_the_cap(
        self, tmp_path, monkeypatch
    ):
        # Windows refuses the rename while anything else has the file open.
        def locked(src, dst):
            raise PermissionError(32, "The process cannot access the file")

        monkeypatch.setattr(bp.os, "replace", locked)
        log = OutputLog(tmp_path / "p.log", cap_bytes=40)
        for _ in range(10):
            log.write(b"x" * 10)

        assert (tmp_path / "p.log").stat().st_size <= 40
        assert log.discarded_bytes == 100 - 40
        assert "could not rotate" in log.problem
        monkeypatch.undo()
        log.write(b"y" * 5)  # the rename works again
        assert log.problem == ""
        assert log.read(log.total_bytes - 5, 5) == b"yyyyy"
        log.close()

    def test_partial_utf8_is_not_split(self):
        text = "naïve ✓".encode()
        assert complete_utf8(text[:-1]) == "naïve ".encode()
        assert complete_utf8(text) == text

    def test_progress_bars_keep_their_last_frame(self):
        assert terminal_view("10%\r50%\r100%\ndone\r\n") == "100%\ndone\n"

    def test_paging_through_a_process(self, host, tmp_path):
        script = _script(
            tmp_path, "big.py", "for i in range(3000):\n    print(f'row {i:05d}')\n"
        )
        started = _tool("start_process")(f'{PY} "{script}"')
        done = _tool("wait_for_process")(started["process_id"], timeout=30)
        # start_process already showed (and so consumed) the newest lines.
        assert done["state"] == "exited"
        status = _tool("process_status")(started["process_id"])
        assert status["new_output"] == ""
        assert status["last_lines"].strip().endswith("row 02999")

        read = _tool("read_process_output")
        pages, offset = [], 0
        while offset is not None:
            page = read(started["process_id"], offset=offset, limit=8000)
            pages.append(page["content"])
            offset = page["next_offset"]
        rows = "".join(pages).split()
        assert rows[:2] == ["row", "00000"]
        assert rows[-1] == "02999"
        assert len(rows) == 6000

        tail = read(started["process_id"], offset=-100)
        assert tail["content"].splitlines()[-1] == "row 02999"
        assert tail["next_offset"] is None
        assert read(started["process_id"], offset=10**9)["status"] == "error"
        assert read(started["process_id"], limit=0)["status"] == "error"

    def test_truncated_new_output_says_where_to_read_from(self, host, tmp_path):
        script = _script(
            tmp_path,
            "burst.py",
            """
            import time
            time.sleep(2.5)
            for i in range(500):
                print(f"row {i:05d}", flush=True)
            time.sleep(60)
            """,
        )
        started = _tool("start_process")(f'{PY} "{script}"')
        assert started["state"] == "running"
        _tool("wait_for_process")(
            started["process_id"], timeout=30, pattern="row 00499"
        )
        # The wait consumed it; the next burst is what a status reports.
        process = host._process_table().get(started["process_id"])
        process.seen_offset = 0
        status = _tool("process_status")(started["process_id"], tail_lines=5)
        assert status["new_output"].splitlines()[-1] == "row 00499"
        assert len(status["new_output"].splitlines()) == 5
        assert status["new_output_truncated"]["unseen_from_offset"] == 0


# ---------------------------------------------------------------------------
# Across turns: the agent loop is told when a process finishes
# ---------------------------------------------------------------------------


class TestNotices:
    def test_a_finish_is_reported_once(self, host, tmp_path):
        script = _script(tmp_path, "nap.py", "import time; time.sleep(3)\n")
        started = _tool("start_process")(f'{PY} "{script}"')
        assert host.take_background_process_notices() == []
        host._process_table().get(started["process_id"]).wait(30)

        notices = host.take_background_process_notices()
        assert len(notices) == 1
        assert started["process_id"] in notices[0]
        assert "exited with code 0" in notices[0]
        assert host.take_background_process_notices() == []

    def test_no_notice_for_an_end_the_model_already_saw(self, host):
        started = _tool("start_process")(f"{PY} -c pass")
        _tool("wait_for_process")(started["process_id"], timeout=30)
        assert host.take_background_process_notices() == []

    def test_the_loop_folds_notices_in_as_a_framed_message(self):
        printed = []
        fake = SimpleNamespace(
            take_background_process_notices=lambda: ["Background process p1 exited."],
            console=SimpleNamespace(print_info=printed.append),
            BACKGROUND_NOTICE_PREAMBLE=Agent.BACKGROUND_NOTICE_PREAMBLE,
        )
        messages, conversation = [], []
        taken = Agent._drain_background_notices(fake, messages, conversation)
        assert taken == 1
        assert messages == conversation
        assert messages[0]["role"] == "user"
        assert messages[0]["content"].startswith("Automatic notice, not from the user")
        assert "- Background process p1 exited." in messages[0]["content"]
        assert printed == ["Background process p1 exited."]

    def test_a_host_without_processes_adds_nothing(self):
        messages = []
        assert Agent._drain_background_notices(SimpleNamespace(), messages, []) == 0
        assert messages == []

    def test_a_process_outlives_the_tool_call_that_started_it(self, host, tmp_path):
        # Each tool call is its own entry into the mixin — the state a later
        # turn sees is the table on the agent, not anything on the call stack.
        script = _script(
            tmp_path, "nap.py", "import time; time.sleep(3); print('done')\n"
        )
        process_id = _tool("start_process")(f'{PY} "{script}"')["process_id"]
        assert _tool("process_status")(process_id)["state"] == "running"
        result = _tool("wait_for_process")(process_id, timeout=30)
        assert result["state"] == "exited"
        assert "done" in result["new_output"]


# ---------------------------------------------------------------------------
# run_shell_command points at the background tools when the agent has them
# ---------------------------------------------------------------------------


class TestPointsAtStartProcess:
    def test_a_trailing_ampersand_is_pointed_at_start_process(self, gated):
        gated._tools_registry = {"start_process": {}}
        for refusal in (
            _tool("run_shell_command")("npm run dev &"),
            gated.policy_refusal_for_call(
                "run_shell_command", {"command": "npm run dev &"}
            ),
        ):
            assert refusal["executed"] is False
            assert "start_process" in refusal["hint"]

    def test_no_pointer_to_a_tool_the_agent_lacks(self, gated):
        gated._tools_registry = {}
        refusal = _tool("run_shell_command")("npm run dev &")
        assert "start_process" not in str(refusal)
        ceiling = _tool("run_shell_command")("git --version", timeout=99999)
        assert "start_process" not in str(ceiling)
        assert "wait_for_condition" not in ceiling["error"]

    def test_the_timeout_ceiling_is_pointed_at_start_process(self, gated):
        gated._tools_registry = {"start_process": {}}
        refusal = _tool("run_shell_command")("git --version", timeout=99999)
        assert "ceiling" in refusal["error"]
        assert "start_process" in refusal["hint"]

    def test_a_chained_line_is_not_mistaken_for_backgrounding(self, gated):
        gated._tools_registry = {"start_process": {}}
        refusal = gated.policy_refusal_for_call(
            "run_shell_command", {"command": "git --version && echo x > out"}
        )
        assert "trailing '&'" not in refusal.get("hint", "")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")
def test_a_forked_child_neither_holds_the_guardian_nor_stops_the_parents(host):
    _tool("start_process")(f'{PY} -c "import time; time.sleep(30)"')
    assert bp._GUARDIAN._proc is not None
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child
        ok = bp._GUARDIAN._proc is None and os.getpid() != bp._OWNER_PID
        os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    assert host._process_table().running()


class _TrackingGuardian:
    def __init__(self, groups):
        self.groups = set(groups)

    def registered(self, pgid):
        return pgid in self.groups

    def unregister(self, pgid):
        self.groups.discard(pgid)


def test_a_group_the_guardian_dropped_is_never_signalled(tmp_path, monkeypatch):
    """A pgid seen empty may be reused by an unrelated group by the time we stop."""
    tracked, dropped = 4242, 4343
    monkeypatch.setattr(bp, "_GUARDIAN", _TrackingGuardian({tracked}))
    # Both look alive: the dropped one because its id now belongs to someone else.
    monkeypatch.setattr(bp, "_group_alive", lambda pgid: True)
    monkeypatch.setattr(bp, "STOP_GRACE_SECONDS", 0.2)
    signalled = []
    monkeypatch.setattr(bp, "_signal_group", lambda pgid, sig: signalled.append(pgid))
    plan = SimpleNamespace(steps=[], step_cwds=[], granted=frozenset(), bypass=False)
    process = bp.BackgroundProcess(
        "p1", "", "noop", plan, OutputLog(tmp_path / "p1.log")
    )
    if process._job is not None:
        process._job.close()
        process._job = None
    process._groups = [tracked, dropped]
    process.start()
    assert process.wait(10)

    process.stop()
    process.log.close()

    assert signalled and set(signalled) == {tracked}


def test_a_host_without_shell_tools_is_refused_by_name():
    class Bare(ProcessToolsMixin):
        pass

    with pytest.raises(TypeError, match="ShellToolsMixin"):
        Bare()._start_process("echo hi", None, "")
