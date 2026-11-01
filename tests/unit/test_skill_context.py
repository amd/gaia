# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia.skills.context: what a skill's own tools.py may do, checked per agent.

Before this, a skill tool had no way to see the agent that called it, so it
read whatever path the model passed, and a tool that needed a program or a
scratch file had to call subprocess / tempfile directly — which the audit
rightly flags and the loader cannot let through.
"""

from __future__ import annotations

import contextvars
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.security import PathValidator
from gaia.skills import context as skill_context
from gaia.skills.audit import audit_skill
from gaia.skills.errors import SkillValidationError
from gaia.skills.manager import SkillManager
from gaia.skills.permissions import Permission
from gaia.skills.tiers import enforce_tier_ceiling


class _HostAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _write_skill(root: Path, name: str, permissions: list, body: str) -> Path:
    """A one-tool skill: ``probe(file_path: str) -> dict`` with *body*."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    perms = "".join(f"\n      - {p}" for p in permissions) or " []"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Probe the skill context.\n"
        "metadata:\n  gaia:\n    security_tier: community\n"
        f"    permissions:{perms}\n"
        "    tools:\n      - name: probe\n        description: Probe.\n"
        "        parameters:\n          file_path:\n            type: string\n"
        "            required: true\n---\n\nUse probe.\n",
        encoding="utf-8",
    )
    (skill_dir / "tools.py").write_text(
        "from gaia.agents.base.tools import tool\n"
        "from gaia.skills import context as ctx\n\n\n"
        "CALLS = []\n\n\n"
        "@tool\n"
        "def probe(file_path: str) -> dict:\n"
        '    """Probe.\n\n    Args:\n        file_path: A path.\n    """\n'
        "    CALLS.append(file_path)\n"
        + textwrap.indent(textwrap.dedent(body), "    "),
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    return tmp_path / "gaia-home"


@pytest.fixture
def allowed(tmp_path):
    folder = tmp_path / "allowed"
    folder.mkdir()
    (folder / "in.txt").write_text("inside", encoding="utf-8")
    return folder


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    folder.mkdir()
    (folder / "secret.txt").write_text("outside", encoding="utf-8")
    return folder / "secret.txt"


@pytest.fixture
def agent(allowed, home):
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _HostAgent(silent_mode=True, skip_lemonade=True)
    validator = PathValidator(allowed_paths=[str(allowed)])
    validator.set_access_prompt(lambda path: False)  # the user says no
    built.path_validator = validator
    built._instance_tools = {}
    yield built
    for name in list(built.loaded_skills):
        built.unload_skill(name)


def _load(agent, tmp_path, name, permissions, body, *, bundled=True):
    root = tmp_path / ("bundled" if bundled else "user-skills")
    _write_skill(root, name, permissions, body)
    manager = SkillManager(
        agent_skill_dirs=[root] if bundled else [],
        user_skills_root=root if not bundled else tmp_path / "no-user-skills",
        include_claude_roots=False,
    )
    return agent.load_skill(name, manager=manager)


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def test_a_path_argument_outside_the_scope_never_reaches_the_tool(
    agent, tmp_path, outside
):
    _load(agent, tmp_path, "path-probe", [], "return {'status': 'success'}\n")
    module = sys.modules["gaia_skill_path_probe_tools"]

    result = agent._execute_tool("path-probe/probe", {"file_path": str(outside)})

    assert result["status"] == "error"
    assert module.CALLS == [], "the tool body ran on an out-of-scope path"


def test_a_path_argument_inside_the_scope_runs(agent, tmp_path, allowed):
    body = (
        "return {'status': 'success', 'text': ctx.check_read(file_path).read_text()}\n"
    )
    _load(agent, tmp_path, "read-probe", [], body)

    result = agent._execute_tool(
        "read-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result == {"status": "success", "text": "inside"}


def test_check_write_outside_the_scope_is_refused(agent, tmp_path, allowed, outside):
    body = (
        "ctx.check_write(str(__import__('pathlib').Path(file_path).parent.parent"
        " / 'outside' / 'new.txt'))\nreturn {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "write-probe", [], body)

    result = agent._execute_tool(
        "write-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "new.txt" in result["error"] or "outside" in result["error"]


# --------------------------------------------------------------------------
# Files: open_read / open_write / list_readable
# --------------------------------------------------------------------------


def _link_folder(link: Path, target: Path) -> None:
    """A folder link: a junction on Windows (no privilege needed), else a symlink."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_open_read_reads_text_and_bytes_inside_the_scope(agent, tmp_path, allowed):
    (allowed / "utf8.txt").write_bytes("caf\u00e9".encode("utf-8"))
    body = (
        "folder = __import__('pathlib').Path(file_path)\n"
        "with ctx.open_read(folder / 'utf8.txt') as f:\n"
        "    text = f.read()\n"
        "with ctx.open_read(folder / 'in.txt', 'rb') as f:\n"
        "    raw = f.read()\n"
        "return {'status': 'success', 'text': text, 'raw': raw.decode()}\n"
    )
    _load(agent, tmp_path, "open-probe", [], body)

    result = agent._execute_tool("open-probe/probe", {"file_path": str(allowed)})

    assert result == {"status": "success", "text": "caf\u00e9", "raw": "inside"}


def test_open_read_refuses_outside_the_scope(agent, tmp_path, allowed, outside):
    body = f"ctx.open_read({str(outside)!r}).close()\nreturn {{'status': 'success'}}\n"
    _load(agent, tmp_path, "open-out-probe", [], body)

    result = agent._execute_tool(
        "open-out-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "secret.txt" in result["error"]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("ctx.open_read(file_path, 'a')", "To write, use open_write"),
        ("ctx.open_read(file_path, 'r+')", "To write, use open_write"),
        ("ctx.open_read(file_path, 'rb', encoding='utf-8')", "is binary"),
        ("ctx.open_write(file_path, 'r')", "To read, use open_read"),
    ],
)
def test_open_modes_are_checked(agent, tmp_path, allowed, call, message):
    _load(agent, tmp_path, "mode-probe", [], f"{call}.close()\nreturn {{}}\n")

    result = agent._execute_tool(
        "mode-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error" and message in str(result)
    assert (allowed / "in.txt").read_text(encoding="utf-8") == "inside"


def test_open_write_creates_parents_inside_and_refuses_outside(
    agent, tmp_path, allowed, outside
):
    body = (
        "with ctx.open_write(file_path, newline='') as f:\n"
        "    f.write('caf\\u00e9\\n')\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "write-file-probe", [], body)
    target = allowed / "sub" / "out.txt"
    escaped = outside.parent / "new.txt"

    inside = agent._execute_tool("write-file-probe/probe", {"file_path": str(target)})
    refused = agent._execute_tool("write-file-probe/probe", {"file_path": str(escaped)})

    assert inside["status"] == "success", inside
    assert target.read_bytes() == "caf\u00e9\n".encode("utf-8")
    assert refused["status"] == "error" and not escaped.exists()


def test_list_readable_skips_hidden_secrets_and_linked_folders(
    agent, tmp_path, allowed, outside
):
    (allowed / ".hidden").write_text("x", encoding="utf-8")
    (allowed / ".env").write_text("TOKEN=x", encoding="utf-8")
    (allowed / "logs").mkdir()
    (allowed / "logs" / "app.log").write_text("ok", encoding="utf-8")
    try:
        _link_folder(allowed / "escape", outside.parent)
    except OSError as e:
        pytest.skip(f"cannot create a folder link here: {e}")
    body = (
        "deep = [p.name for p in ctx.list_readable(file_path)]\n"
        "flat = [p.name for p in ctx.list_readable(file_path, recursive=False)]\n"
        "hidden = [p.name for p in ctx.list_readable(file_path, include_hidden=True)]\n"
        "return {'status': 'success', 'deep': deep, 'flat': flat, 'hidden': hidden}\n"
    )
    _load(agent, tmp_path, "list-probe", [], body)

    result = agent._execute_tool("list-probe/probe", {"file_path": str(allowed)})

    assert result["status"] == "success", result
    assert result["flat"] == ["in.txt"]
    assert result["deep"] == ["in.txt", "app.log"]
    assert ".hidden" in result["hidden"] and ".env" not in result["hidden"]
    assert "secret.txt" not in result["deep"] + result["hidden"]


def test_list_readable_refuses_a_folder_outside_the_scope(
    agent, tmp_path, allowed, outside
):
    body = (
        f"ctx.list_readable({str(outside.parent)!r})\n" "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "list-out-probe", [], body)

    result = agent._execute_tool(
        "list-out-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"


def test_context_functions_refuse_outside_a_skill_tool():
    with pytest.raises(skill_context.SkillContextError):
        skill_context.check_read(".")


@pytest.mark.parametrize(
    ("name", "is_path"),
    [
        ("file_path", True),
        ("path", True),
        ("output_dir", True),
        ("source_file", True),
        ("url", False),
        ("max_entries", False),
    ],
)
def test_which_arguments_are_paths(name, is_path):
    assert skill_context.is_path_argument(name) is is_path


# --------------------------------------------------------------------------
# Programs and temporary files
# --------------------------------------------------------------------------


@pytest.fixture
def python_as_program(monkeypatch):
    """Resolve the declared program 'python' to this interpreter."""
    monkeypatch.setattr(
        skill_context,
        "find_program",
        lambda name: sys.executable if name == "python" else None,
    )


def test_run_program_runs_a_declared_program_with_a_scrubbed_env(
    agent, tmp_path, allowed, python_as_program, monkeypatch
):
    monkeypatch.setenv("GAIA_TEST_API_KEY", "do-not-leak")
    body = (
        "proc = ctx.run_program('python', ['-c', "
        "'import os; print(os.environ.get(\"GAIA_TEST_API_KEY\"))'], timeout=60)\n"
        "return {'status': 'success', 'out': proc.stdout.strip(), "
        "'code': proc.returncode}\n"
    )
    _load(agent, tmp_path, "run-probe", ["program:run:python"], body)

    result = agent._execute_tool(
        "run-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result == {"status": "success", "out": "None", "code": 0}


def test_run_program_refuses_an_undeclared_program(
    agent, tmp_path, allowed, python_as_program
):
    body = (
        "ctx.run_program('python', ['-V'], timeout=10)\nreturn {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "norun-probe", [], body)

    result = agent._execute_tool(
        "norun-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "program:run:python" in result["error"]


def test_temp_files_live_for_one_call(agent, tmp_path, allowed):
    body = (
        "copy = ctx.copy_to_temp(file_path)\n"
        "note = ctx.write_temp_file('out/note.txt', 'hi')\n"
        "return {'status': 'success', 'copy': str(copy), 'note': str(note), "
        "'text': copy.read_text()}\n"
    )
    _load(agent, tmp_path, "temp-probe", ["scratch:write"], body)

    result = agent._execute_tool(
        "temp-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "success" and result["text"] == "inside"
    assert not Path(result["copy"]).exists(), "temp folder outlived the call"
    assert (allowed / "in.txt").read_text() == "inside"


def test_temp_files_need_scratch_write(agent, tmp_path, allowed):
    body = "ctx.temp_dir()\nreturn {'status': 'success'}\n"
    _load(agent, tmp_path, "notemp-probe", [], body)

    result = agent._execute_tool(
        "notemp-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert "scratch:write" in result["error"]


def test_temp_names_cannot_leave_the_folder(agent, tmp_path, allowed):
    body = "ctx.write_temp_file('../escape.txt', 'x')\nreturn {'status': 'success'}\n"
    _load(agent, tmp_path, "escape-probe", ["scratch:write"], body)

    result = agent._execute_tool(
        "escape-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "leaves" in result["error"]


def _file_link(link: Path, target: Path) -> None:
    """A file symlink, or skip where this account cannot create one."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a file symlink here: {e}")


def _wait_until(predicate, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_path_argument_linking_outside_the_scope_never_reaches_the_tool(
    agent, tmp_path, allowed, outside
):
    _file_link(allowed / "looks-inside.txt", outside)
    _load(agent, tmp_path, "link-probe", [], "return {'status': 'success'}\n")
    module = sys.modules["gaia_skill_link_probe_tools"]

    result = agent._execute_tool(
        "link-probe/probe", {"file_path": str(allowed / "looks-inside.txt")}
    )

    assert result["status"] == "error"
    assert module.CALLS == []


def test_a_path_argument_through_a_linked_folder_never_reaches_the_tool(
    agent, tmp_path, allowed, outside
):
    try:
        _link_folder(allowed / "shortcut", outside.parent)
    except OSError as e:
        pytest.skip(f"cannot create a folder link here: {e}")
    _load(agent, tmp_path, "junction-probe", [], "return {'status': 'success'}\n")
    module = sys.modules["gaia_skill_junction_probe_tools"]

    result = agent._execute_tool(
        "junction-probe/probe", {"file_path": str(allowed / "shortcut" / "secret.txt")}
    )

    assert result["status"] == "error"
    assert module.CALLS == []


@pytest.mark.parametrize(
    "escape",
    ["{allowed}/../outside/secret.txt", "{allowed}/sub/../../outside/secret.txt"],
)
def test_a_traversing_path_argument_never_reaches_the_tool(
    agent, tmp_path, allowed, outside, escape
):
    _load(agent, tmp_path, "dots-probe", [], "return {'status': 'success'}\n")
    module = sys.modules["gaia_skill_dots_probe_tools"]

    result = agent._execute_tool(
        "dots-probe/probe", {"file_path": escape.format(allowed=allowed)}
    )

    assert result["status"] == "error"
    assert module.CALLS == []


def test_open_read_refuses_a_link_found_inside_the_scope(
    agent, tmp_path, allowed, outside
):
    _file_link(allowed / "alias.txt", outside)
    body = (
        "name = __import__('pathlib').Path(file_path).parent / 'alias.txt'\n"
        "ctx.open_read(name).close()\nreturn {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "alias-probe", [], body)

    result = agent._execute_tool(
        "alias-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"


def test_open_write_refuses_a_dangling_link_out_of_the_scope(
    agent, tmp_path, allowed, outside
):
    planted = outside.parent / "planted.txt"
    _file_link(allowed / "report.txt", planted)
    body = (
        "name = __import__('pathlib').Path(file_path).parent / 'report.txt'\n"
        "with ctx.open_write(name) as f:\n    f.write('x')\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "dangle-probe", [], body)

    result = agent._execute_tool(
        "dangle-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert not planted.exists()


@pytest.mark.parametrize("name", ["{outside}", "sub/../../escape.txt", "."])
def test_temp_names_are_held_to_the_folder(agent, tmp_path, allowed, outside, name):
    body = (
        f"ctx.write_temp_file({name.format(outside=outside)!r}, 'x')\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "hold-probe", ["scratch:write"], body)

    result = agent._execute_tool(
        "hold-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_a_link_planted_in_the_temp_folder_cannot_be_written_through(
    agent, tmp_path, allowed, outside
):
    body = (
        "root = ctx.temp_dir()\n"
        f"LINK(root / 'out', __import__('pathlib').Path({str(outside.parent)!r}))\n"
        "ctx.write_temp_file('out/secret.txt', 'overwritten')\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "plant-probe", ["scratch:write"], body)
    sys.modules["gaia_skill_plant_probe_tools"].LINK = _link_folder

    result = agent._execute_tool(
        "plant-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "leaves" in result["error"]
    assert outside.read_text(encoding="utf-8") == "outside"


def test_temp_files_work_when_gaia_home_is_a_link(
    agent, tmp_path, allowed, monkeypatch
):
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    try:
        _link_folder(tmp_path / "linked-home", real_home)
    except OSError as e:
        pytest.skip(f"cannot create a folder link here: {e}")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "linked-home"))
    body = (
        "note = ctx.write_temp_file('out/note.txt', 'hi')\n"
        "return {'status': 'success', 'text': note.read_text()}\n"
    )
    _load(agent, tmp_path, "linkhome-probe", ["scratch:write"], body)

    result = agent._execute_tool(
        "linkhome-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result == {"status": "success", "text": "hi"}


def test_a_timed_out_call_deletes_its_temp_folder_once_the_worker_ends(
    agent, tmp_path, allowed, monkeypatch
):
    body = (
        "import threading, time\n"
        "folder = ctx.temp_dir()\n"
        "(folder / 'part.txt').write_text('x')\n"
        "STATE['folder'] = folder\n"
        "STATE['go'].wait(30)\n"
        "(folder / 'late.txt').write_text('still mine')\n"
        "STATE['wrote_late'] = True\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "slow-probe", ["scratch:write"], body)
    module = sys.modules["gaia_skill_slow_probe_tools"]
    module.STATE = {"go": threading.Event()}
    monkeypatch.setattr(agent, "_bounded_tool_timeout", lambda *a: 0.3)

    result = agent._execute_tool(
        "slow-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result.get("timeout") is True, result
    folder = module.STATE["folder"]
    assert folder.is_dir(), "deleted under a worker that is still running"
    module.STATE["go"].set()
    assert _wait_until(lambda: not folder.exists()), "temp folder leaked"
    assert module.STATE.get("wrote_late") is True


def test_a_finished_call_leaves_no_temp_folder_behind(agent, tmp_path, allowed, home):
    body = "ctx.temp_dir('a/b')\nreturn {'status': 'success'}\n"
    _load(agent, tmp_path, "clean-probe", ["scratch:write"], body)

    for _ in range(3):
        result = agent._execute_tool(
            "clean-probe/probe", {"file_path": str(allowed / "in.txt")}
        )
        assert result["status"] == "success"

    assert list((home / "skill-temp" / "clean-probe").iterdir()) == []


def test_a_failing_call_still_deletes_its_temp_folder(agent, tmp_path, allowed, home):
    body = "ctx.write_temp_file('x.txt', 'x')\nraise RuntimeError('boom')\n"
    _load(agent, tmp_path, "boom-probe", ["scratch:write"], body)

    result = agent._execute_tool(
        "boom-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert list((home / "skill-temp" / "boom-probe").iterdir()) == []


@pytest.mark.parametrize("worker_first", [True, False])
def test_the_temp_folder_is_deleted_by_whichever_finishes_last(
    home, tmp_path, worker_first
):
    skill = SimpleNamespace(
        name="race", parsed_permissions=lambda: [Permission.parse("scratch:write")]
    )
    gate = threading.Event()
    seen = {}

    def body():
        seen["folder"] = skill_context.temp_dir()
        gate.wait(10)

    with skill_context.bind(object(), skill) as call:
        worker = threading.Thread(
            target=contextvars.copy_context().run, args=(call.wrap(body),)
        )
        worker.start()
        assert _wait_until(lambda: "folder" in seen)
        if worker_first:
            gate.set()
            worker.join(10)
    folder = seen["folder"]
    if not worker_first:
        assert folder.is_dir()
        gate.set()
        worker.join(10)
    assert not folder.exists()


def test_a_cancelled_call_kills_its_program(
    agent, tmp_path, allowed, python_as_program, monkeypatch
):
    body = (
        "import time\n"
        "STATE['started'] = time.monotonic()\n"
        "try:\n"
        "    ctx.run_program('python', ['-c', 'import time; time.sleep(60)'], "
        "timeout=120)\n"
        "except BaseException as e:\n"
        "    STATE['raised'] = type(e).__name__\n"
        "    STATE['ended'] = time.monotonic()\n"
        "    raise\n"
        "return {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "hang-probe", ["program:run:python"], body)
    module = sys.modules["gaia_skill_hang_probe_tools"]
    module.STATE = {}
    monkeypatch.setattr(agent, "_bounded_tool_timeout", lambda *a: 0.5)

    result = agent._execute_tool(
        "hang-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result.get("timeout") is True, result
    assert _wait_until(lambda: "ended" in module.STATE, 20)
    assert module.STATE["raised"] == "ToolCancelled"
    assert module.STATE["ended"] - module.STATE["started"] < 20


def test_run_program_still_times_out_on_its_own(
    agent, tmp_path, allowed, python_as_program
):
    body = (
        "ctx.run_program('python', ['-c', 'import time; time.sleep(60)'], "
        "timeout=0.5)\nreturn {'status': 'success'}\n"
    )
    _load(agent, tmp_path, "own-timeout-probe", ["program:run:python"], body)

    started = time.monotonic()
    result = agent._execute_tool(
        "own-timeout-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result["status"] == "error"
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 20


def test_run_program_returns_output_and_exit_code(
    agent, tmp_path, allowed, python_as_program
):
    body = (
        "proc = ctx.run_program('python', ['-c', "
        '\'import sys; print("out"); print("err", file=sys.stderr); sys.exit(3)\'], '
        "timeout=60)\n"
        "return {'status': 'success', 'out': proc.stdout.strip(), "
        "'err': proc.stderr.strip(), 'code': proc.returncode}\n"
    )
    _load(agent, tmp_path, "code-probe", ["program:run:python"], body)

    result = agent._execute_tool(
        "code-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result == {"status": "success", "out": "out", "err": "err", "code": 3}


@pytest.mark.parametrize(("bundled", "asks"), [(True, False), (False, True)])
def test_only_a_non_bundled_program_skill_asks_per_call(agent, tmp_path, bundled, asks):
    _load(
        agent,
        tmp_path,
        "ask-probe",
        ["program:run:python"],
        "return {'status': 'success'}\n",
        bundled=bundled,
    )
    assert agent._tool_requires_confirmation("ask-probe/probe", {}) is asks


# --------------------------------------------------------------------------
# Grammar, tiers, audit
# --------------------------------------------------------------------------


def test_program_run_must_name_the_program():
    with pytest.raises(SkillValidationError, match="naming the program"):
        Permission.parse("program:run")


def test_scratch_takes_no_scope():
    with pytest.raises(SkillValidationError, match="no scope"):
        Permission.parse("scratch:write:/tmp")


@pytest.mark.parametrize(
    ("tier", "permission", "allowed"),
    [
        ("experimental", "program:run:soffice", False),
        ("community", "program:run:soffice", True),
        ("experimental", "scratch:write", True),
    ],
)
def test_tier_ceilings_cover_the_new_permissions(tier, permission, allowed):
    from gaia.skills.errors import SkillPermissionError

    permissions = [Permission.parse(permission)]
    if allowed:
        enforce_tier_ceiling(permissions, tier=tier, skill_name="probe")
    else:
        with pytest.raises(SkillPermissionError):
            enforce_tier_ceiling(permissions, tier=tier, skill_name="probe")


_RUN_SOFFICE = "proc = ctx.run_program('soffice', ['--version'], timeout=60)\n"


@pytest.mark.parametrize(
    ("permissions", "body", "rule"),
    [
        (["program:run:soffice"], _RUN_SOFFICE, None),
        ([], _RUN_SOFFICE, "permission.undeclared.program"),
        (["program:run:other"], _RUN_SOFFICE, "permission.scope_violation"),
        (["scratch:write"], "ctx.write_temp_file('a.txt', 'x')\n", None),
        ([], "ctx.write_temp_file('a.txt', 'x')\n", "permission.undeclared.scratch"),
        ([], "ctx.open_read(file_path).close()\n", None),
        ([], "ctx.open_write(file_path + '.out').close()\n", None),
        ([], "ctx.list_readable(file_path)\n", None),
        ([], "open(file_path).close()\n", "permission.undeclared.filesystem"),
    ],
)
def test_the_audit_matches_context_calls_to_declarations(
    tmp_path, permissions, body, rule
):
    skill_dir = _write_skill(tmp_path, "audit-probe", permissions, body + "return {}\n")
    report = audit_skill(skill_dir)
    rules = [f.rule_id for f in report.findings]
    if rule is None:
        assert report.verdict == "ALLOW", rules
    else:
        assert report.verdict != "ALLOW" and rule in rules, rules


def test_the_audit_still_flags_a_raw_subprocess(tmp_path):
    body = "import subprocess\nsubprocess.run(['soffice'])\nreturn {}\n"
    skill_dir = _write_skill(tmp_path, "raw-probe", ["program:run:soffice"], body)
    assert audit_skill(skill_dir).verdict != "ALLOW"
