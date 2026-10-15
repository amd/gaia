# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill's reference files: the documented examples run, and the agent can read them.

Runs the shipped example and the SDK page's code blocks so the docs cannot
drift, then checks the read-only folder grant ``Agent.load_skill`` gives the
agent's file tools — from an Agent-UI-shaped scope that does not cover the
skill folder, which is where reads used to be refused with no way to approve.
"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator
from gaia.skills import SkillManager, SkillValidationError

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "skill_references_example.py"
SDK_PAGE = REPO / "docs" / "sdk" / "sdks" / "skills.mdx"
GUIDE = REPO / "docs" / "guides" / "starter-skills.mdx"

SKILL_MD = """\
---
name: query-helper
description: Write search queries using our query syntax. Use when the user asks for a search query.
---

# Query helper

Before writing a query, read `references/query-syntax.md` for the operators
we support, then build the query from them.
"""
SYNTAX = "# Query syntax\n\n- `AND`, `OR`, `NOT` combine terms.\n"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keep ``~/.gaia`` and ``~/.claude`` under tmp_path; restore the tool registry."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(home / ".gaia"))
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _make_skill(root: Path, name: str = "query-helper") -> Path:
    skill_dir = root / name
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "SKILL.md").write_text(
        SKILL_MD.replace("query-helper", name), encoding="utf-8"
    )
    (skill_dir / "references" / "query-syntax.md").write_text(SYNTAX, encoding="utf-8")
    (skill_dir / "scripts" / "build_query.py").write_text("print(1)\n", "utf-8")
    return skill_dir


def _user_root() -> Path:
    return SkillManager().user_root


class _StubAgent(FileIOToolsMixin):
    """Real ``Agent`` skill methods plus the real file tools, no LLM stack."""

    from gaia.agents.base.agent import Agent

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    SKILL_MANIFEST = None
    _BUNDLED_SKILLS_DIRNAME = Agent._BUNDLED_SKILLS_DIRNAME
    _SKILL_MANIFEST_FILENAME = Agent._SKILL_MANIFEST_FILENAME
    _instance_tools = None
    _skill_manager = None
    _loaded_skills = None
    _active_skill_filter = None

    skill_manager = Agent.skill_manager
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    _format_tools_for_prompt = Agent._format_tools_for_prompt
    _bundled_skill_dirs = Agent._bundled_skill_dirs
    _resolve_skill_manifest = Agent._resolve_skill_manifest
    _note_skill_active = Agent._note_skill_active
    _pin_skill_body = Agent._pin_skill_body
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    get_skills_system_prompt = Agent.get_skills_system_prompt

    def __init__(self, scope: Path):
        # The Agent UI's shape: an explicit allowlist that is NOT the skill root.
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self._truncation_budget = lambda: (3000, 2000)
        self.register_file_io_tools()
        self.read_file = _TOOL_REGISTRY["read_file"]["function"]
        self.write_file = _TOOL_REGISTRY["write_file"]["function"]

    def rebuild_system_prompt(self):
        pass


@pytest.fixture
def agent(tmp_path):
    scope = tmp_path / "workspace"
    scope.mkdir()
    return _StubAgent(scope)


def _code_after(marker: str, page: Path) -> str:
    text = page.read_text(encoding="utf-8")
    match = re.search(
        re.escape("{/* " + marker + " */}") + r"\s*```python\n(.*?)```", text, re.S
    )
    assert match, f"{page.name} lost its '{marker}' code block"
    return match.group(1)


# ----------------------------------------------------------------------
# The documented examples
# ----------------------------------------------------------------------


def test_shipped_example_runs_and_reads_the_reference_file(capsys):
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    out = capsys.readouterr().out
    assert "Skill: query-helper" in out
    assert "read `references/query-syntax.md`" in out
    assert "`site:example.com` limits results to one site." in out
    assert "Bundled files: ['references/query-syntax.md']" in out


def test_sdk_page_excerpt_is_verbatim_from_the_example():
    excerpt = _code_after("source: examples/skill_references_example.py", SDK_PAGE)
    source = {line.strip() for line in EXAMPLE.read_text(encoding="utf-8").splitlines()}
    drifted = [
        line
        for line in excerpt.splitlines()
        if line.strip() and line.strip() not in source
    ]
    assert not drifted, f"skills.mdx excerpt no longer matches the example: {drifted}"


def test_skill_md_shown_in_the_docs_is_the_one_the_example_builds():
    example = runpy.run_path(str(EXAMPLE))
    for page in (SDK_PAGE, GUIDE):
        assert example["SKILL_MD"].strip() in page.read_text(encoding="utf-8")


def test_documented_agent_loads_the_skill_and_reads_its_reference(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _make_skill(tmp_path / "skills")
    namespace: dict = {}
    exec(_code_after("doc-test: agent", SDK_PAGE), namespace)  # noqa: S102

    agent = namespace["QueryAgent"](skip_lemonade=True, silent_mode=True)

    assert "query-helper" in agent.loaded_skills
    assert "references/query-syntax.md" in agent.get_skills_system_prompt()
    ref = tmp_path / "skills" / "query-helper" / "references" / "query-syntax.md"
    result = _TOOL_REGISTRY["read_file"]["function"](str(ref))
    assert result.get("status") != "error", result
    assert "combine terms" in str(result)


# ----------------------------------------------------------------------
# The agent's file tools and a loaded skill's folder
# ----------------------------------------------------------------------


def test_reference_is_unreadable_before_load_and_readable_after(agent):
    skill_dir = _make_skill(_user_root())
    ref = skill_dir / "references" / "query-syntax.md"

    before = agent.read_file(str(ref))
    assert before["status"] == "error" and "Access denied" in before["error"]

    skill = agent.load_skill("query-helper")
    assert skill.directory == skill_dir
    after = agent.read_file(str(ref))
    assert after.get("status") != "error", after
    assert "combine terms" in str(after)
    script = agent.read_file(str(skill_dir / "scripts" / "build_query.py"))
    assert script.get("status") != "error", script


def test_skill_folder_grant_is_read_only(agent):
    skill_dir = _make_skill(_user_root())
    agent.load_skill("query-helper")

    target = skill_dir / "references" / "query-syntax.md"
    result = agent.write_file(str(target), "overwritten")
    assert result["status"] == "error"
    assert target.read_text(encoding="utf-8") == SYNTAX
    assert not agent.path_validator.is_path_allowed(str(target), prompt_user=False)


def test_dotdot_escape_from_the_skill_folder_is_refused(agent):
    skill_dir = _make_skill(_user_root())
    outside = _user_root().parent / "outside.md"
    outside.write_text("not part of the skill", encoding="utf-8")
    agent.load_skill("query-helper")

    escaped = skill_dir / "references" / ".." / ".." / ".." / "outside.md"
    result = agent.read_file(str(escaped))
    assert result["status"] == "error" and "Access denied" in result["error"]
    with pytest.raises(SkillValidationError, match="escapes skill"):
        agent.skill_manager.resource_path("query-helper", "../../outside.md")


def test_secret_files_inside_the_skill_folder_stay_blocked(agent):
    skill_dir = _make_skill(_user_root())
    (skill_dir / ".env").write_text("TOKEN=abc", encoding="utf-8")
    agent.load_skill("query-helper")

    result = agent.read_file(str(skill_dir / ".env"))
    assert result["status"] == "error" and "Read blocked" in result["error"]


def test_unload_withdraws_the_read_grant(agent):
    skill_dir = _make_skill(_user_root())
    ref = skill_dir / "references" / "query-syntax.md"
    agent.load_skill("query-helper")
    assert agent.unload_skill("query-helper")

    result = agent.read_file(str(ref))
    assert result["status"] == "error" and "Access denied" in result["error"]


def test_huge_reference_file_is_not_loaded_into_the_prompt(agent):
    skill_dir = _make_skill(_user_root())
    marker = "HUGE-REFERENCE-SENTINEL"
    (skill_dir / "references" / "big.md").write_text(
        (marker + " " + "x" * 1000 + "\n") * 2000, encoding="utf-8"
    )
    agent.load_skill("query-helper")

    prompt = agent.get_skills_system_prompt()
    assert marker not in prompt
    assert "combine terms" not in prompt
    assert len(prompt) < 2000


def test_missing_reference_file_gives_a_clear_error(agent):
    skill_dir = _make_skill(_user_root())
    agent.load_skill("query-helper")

    with pytest.raises(SkillValidationError, match="has no resource 'references/nope"):
        agent.skill_manager.resource_path("query-helper", "references/nope.md")
    result = agent.read_file(str(skill_dir / "references" / "nope.md"))
    assert result["status"] == "error" and "File not found" in result["error"]


def test_non_ascii_reference_file_names(agent):
    skill_dir = _make_skill(_user_root())
    name = "syntaxe-requêtes-検索.md"
    (skill_dir / "references" / name).write_text("Opérateurs: ET, OU", "utf-8")
    agent.load_skill("query-helper")

    path = agent.skill_manager.resource_path("query-helper", f"references/{name}")
    assert path.read_text(encoding="utf-8") == "Opérateurs: ET, OU"
    result = agent.read_file(str(path))
    assert result.get("status") != "error", result
    assert "Opérateurs" in str(result)


def test_any_folder_name_works_including_reference_and_references(agent):
    skill_dir = _make_skill(_user_root())
    (skill_dir / "reference").mkdir()
    (skill_dir / "reference" / "old-style.md").write_text("singular", "utf-8")
    agent.load_skill("query-helper")

    for relative, expected in (
        ("reference/old-style.md", "singular"),
        ("references/query-syntax.md", "combine terms"),
    ):
        path = agent.skill_manager.resource_path("query-helper", relative)
        result = agent.read_file(str(path))
        assert result.get("status") != "error", result
        assert expected in str(result)


def test_read_only_grant_refuses_home_and_drive_root(agent):
    validator = agent.path_validator
    home = Path.home()
    for too_broad in (home, Path(home.anchor), home / "skills" / ".." / ".."):
        with pytest.raises(ValueError, match="covers the drive or home folder"):
            validator.add_read_only_path(str(too_broad))
    assert not validator.read_only_paths
