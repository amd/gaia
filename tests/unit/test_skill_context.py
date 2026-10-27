# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia.skills.context: what a skill's own tools.py may do, checked per agent.

Before this, a skill tool had no way to see the agent that called it, so it
read whatever path the model passed, and a tool that needed a program or a
scratch file had to call subprocess / tempfile directly — which the audit
rightly flags and the loader cannot let through.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
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


def test_a_skill_tool_works_in_a_session_that_never_loaded_the_skill(
    agent, tmp_path, allowed, home
):
    """A later chat sees the tool another chat registered; calling it must work."""
    body = (
        "return {'status': 'success', 'text': ctx.check_read(file_path).read_text()}\n"
    )
    _load(agent, tmp_path, "shared-probe", [], body)
    with patch("gaia.agents.base.agent.AgentSDK"):
        later = _HostAgent(silent_mode=True, skip_lemonade=True)
    later.path_validator = PathValidator(allowed_paths=[str(allowed)])
    later._skill_manager = SkillManager(
        agent_skill_dirs=[tmp_path / "bundled"],
        user_skills_root=tmp_path / "no-user-skills",
        include_claude_roots=False,
    )
    later._instance_tools = dict(agent._instance_tools)
    assert "shared-probe" not in later.loaded_skills

    result = later._execute_tool(
        "shared-probe/probe", {"file_path": str(allowed / "in.txt")}
    )

    assert result == {"status": "success", "text": "inside"}
    assert "shared-probe" in later.loaded_skills


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
