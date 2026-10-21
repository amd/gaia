# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A bundled skill's code works on the flagship, in a checkout and in the binary.

Domain tools live in skills (``hub/skills/<name>/tools.py`` and ``scripts/``),
not in the core agent. That only holds if the flagship registers a bundled
skill's ``@tool`` functions when the skill loads, removes them when it unloads,
can run what the skill ships under ``scripts/``, and the frozen binary carries
the starter pack with every module its code imports.

Bundled skills are trusted by where they come from: they ship inside GAIA, so
no ``gaia skill promote`` is needed. That is why every one of them must pass the
static security audit here, at test time, instead.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from gaia_agent.agent import _bundled_skill_roots

from gaia.skills.audit import audit_skill
from gaia.skills.capture import code_is_deferred
from gaia.skills.manager import ROOT_AGENT_BUNDLED, SkillManager

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_freeze_skills", PACKAGING / "freeze.py"
)
assert _spec and _spec.loader
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)


def _bundled_skill_dirs() -> list[Path]:
    seen: dict[str, Path] = {}
    for root in _bundled_skill_roots():
        for skill_dir in sorted(Path(root).iterdir()):
            if (skill_dir / "SKILL.md").is_file():
                seen.setdefault(skill_dir.name, skill_dir)
    return list(seen.values())


def _ships_code(skill_dir: Path) -> bool:
    return (skill_dir / "tools.py").is_file() or (skill_dir / "scripts").is_dir()


CODE_SKILLS = [d for d in _bundled_skill_dirs() if _ships_code(d)]


@pytest.fixture(scope="module")
def flagship():
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True))


def _bundled_only(roots, tmp_path: Path) -> SkillManager:
    """The flagship's bundled roots, with no user or Claude Code skills mixed in."""
    return SkillManager(
        agent_skill_dirs=roots,
        user_skills_root=tmp_path / "user-skills",
        include_claude_roots=False,
    )


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_a_bundled_skill_tool_registers_on_load_and_goes_on_unload(
    flagship, tmp_path
):
    """rss-digest ships fetch_rss in its tools.py; loading it must offer the tool."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    manager = _bundled_only(flagship.SKILL_DIRS, tmp_path)
    before = set(flagship._tools_registry)
    assert "rss-digest/fetch_rss" not in before, "registered before any load"

    skill = flagship.load_skill("rss-digest", manager=manager)
    try:
        assert skill.root == ROOT_AGENT_BUNDLED
        assert not code_is_deferred(skill), "bundled code must not wait on promote"
        entry = flagship._tools_registry["rss-digest/fetch_rss"]
        assert entry["skill"] == "rss-digest"
        assert callable(entry["function"])
        # Only the skill's own tools were added: tool counts and capability
        # docs describe the agent without skills, and stay true until a load.
        assert set(flagship._tools_registry) - before == {"rss-digest/fetch_rss"}
        offered = {
            s["function"]["name"] for s in flagship._build_openai_tool_schemas()
        }
        assert "rss-digest/fetch_rss" in offered
    finally:
        assert flagship.unload_skill("rss-digest")

    assert set(flagship._tools_registry) == before
    assert "rss-digest/fetch_rss" not in _TOOL_REGISTRY


def test_a_bundled_skill_scripts_file_runs_by_path(flagship, tmp_path):
    """A skill's scripts/ file is on disk at <skill directory>/scripts/<file>.

    load_skill hands the model the skill's directory; run_python must be able
    to run a script under it by joining that path.
    """
    root = tmp_path / "bundled"
    skill_dir = root / "script-probe"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: script-probe\n"
        "description: Prove a bundled script runs by path.\n"
        "---\n\n"
        "Run scripts/probe.py.\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "probe.py").write_text(
        'print("probe-ran")\n', encoding="utf-8"
    )

    skill = flagship.load_skill("script-probe", manager=_bundled_only([root], tmp_path))
    try:
        script = Path(skill.directory) / "scripts" / "probe.py"
        assert script.is_file()
        run_python = flagship._tools_registry["run_python"]["function"]
        result = run_python(code=f"import runpy; runpy.run_path({str(script)!r})")
    finally:
        flagship.unload_skill("script-probe")

    assert result.get("return_code") == 0, result
    assert "probe-ran" in result.get("stdout", ""), result


# --------------------------------------------------------------------------
# Trust by provenance: the audit runs here instead of at promote time
# --------------------------------------------------------------------------


def test_some_bundled_skill_ships_code():
    """Guard the parametrization below — an empty list would pass vacuously."""
    assert any(d.name == "rss-digest" for d in CODE_SKILLS)


@pytest.mark.parametrize("skill_dir", CODE_SKILLS, ids=lambda d: d.name)
def test_bundled_skill_code_passes_the_security_audit(skill_dir: Path):
    """What `gaia skill audit <dir>` decides; bundled code ships only on ALLOW."""
    report = audit_skill(skill_dir)
    assert report.verdict == "ALLOW", "\n".join(
        f"{f.severity} {f.location}: {f.message}" for f in report.findings
    )


def test_the_description_lint_covers_bundled_skill_tools():
    """`util/lint.py --tool-descriptions` checks skill tools, not only core ones."""
    from gaia.eval.tool_cost import build_full_agent_skeleton

    util = Path(__file__).resolve().parents[5] / "util"
    spec = importlib.util.spec_from_file_location(
        "check_tool_descriptions", util / "check_tool_descriptions.py"
    )
    assert spec and spec.loader
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)

    agent = build_full_agent_skeleton()
    before = dict(agent._instance_tools)
    names = {
        s["function"]["name"] for s in check.bundled_skill_tool_schemas(agent)
    }

    assert "rss-digest/fetch_rss" in names
    assert agent._instance_tools == before, "the lint left skill tools behind"


# --------------------------------------------------------------------------
# The frozen binary carries the starter pack and what its code imports
# --------------------------------------------------------------------------


def test_the_freeze_bundles_every_starter_skill():
    staged = {dest: src for src, dest in freeze._resolve_add_data()}
    for skill_dir in freeze._skill_dirs(freeze.HUB_SKILLS_SRC):
        assert (
            staged.get(f"{freeze.SKILLS_DEST}/{skill_dir.name}") == skill_dir
        ), f"{skill_dir.name} would be missing from the binary"


def test_the_freeze_lands_skills_where_the_frozen_agent_looks():
    """``gaia_agent/skills`` is ``_SKILLS_DIR`` relative to the package."""
    from gaia_agent.agent import _SKILLS_DIR

    assert freeze.SKILLS_DEST == f"{_SKILLS_DIR.parent.name}/{_SKILLS_DIR.name}"


def test_the_freeze_collects_the_imports_of_skill_code():
    """gaia is not collected wholesale, so skill imports must be named."""
    hidden = set(freeze.skill_code_hidden_imports())
    assert {"gaia.web.client", "gaia.agents.base.tools"} <= hidden


def test_the_freeze_refuses_a_skill_import_that_is_not_installed(tmp_path):
    skill_dir = tmp_path / "needs-missing"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: needs-missing\n---\n")
    (skill_dir / "tools.py").write_text("import gaia_no_such_module_xyz\n")
    (skill_dir / "helper.py").write_text("")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("import helper\n")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(freeze, "REPO_ROOT", tmp_path)
        with pytest.raises(SystemExit) as excinfo:
            freeze.skill_code_hidden_imports([skill_dir])

    message = str(excinfo.value)
    assert "gaia_no_such_module_xyz" in message
    assert "helper" not in message, "a module the skill ships is not missing"


def test_the_freeze_refuses_a_skill_name_in_both_roots(tmp_path, monkeypatch):
    packaged, starter = tmp_path / "packaged", tmp_path / "starter"
    for root in (packaged, starter):
        (root / "dup").mkdir(parents=True)
        (root / "dup" / "SKILL.md").write_text("---\nname: dup\n---\n")
    monkeypatch.setattr(freeze, "SKILLS_SRC", packaged)
    monkeypatch.setattr(freeze, "HUB_SKILLS_SRC", starter)

    with pytest.raises(SystemExit, match="dup"):
        freeze._resolve_add_data()

