# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Task-start project map (#3379).

Pins the four things the feature is only useful if it guarantees: the
"code repository" predicate, the token budget on the 32K NPU profile, cache
invalidation on change, and the once-per-session ``index_codebase`` trigger.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import threading

import pytest

from gaia.agents.base.project_map import (
    PROJECT_MANIFESTS,
    PROJECT_MAP_TOKEN_BUDGET,
    PROJECT_ROOT_ENV,
    PlatformQuirks,
    ProjectMapMixin,
    build_project_map,
    clear_project_map_cache,
    detect_platform_quirks,
    is_agent_own_source,
    is_code_repository,
    render_project_map,
    resolve_project_root,
)
from gaia.agents.base.system_context import (
    CLI_TOOL_PROBES,
    DEV_TOOL_PROBES,
    probe_binaries,
)
from gaia.agents.base.turn_metrics import count_tokens
from gaia.llm.lemonade_client import NPU_CTX_SIZE


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_project_map_cache()
    yield
    clear_project_map_cache()


@pytest.fixture
def repo(tmp_path):
    """A minimal but realistic project: VCS dir, manifest, source tree."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo").mkdir()
    (tmp_path / "src" / "cli.py").write_text("print('hi')\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    return tmp_path


# ── the predicate ─────────────────────────────────────────────────────────


def test_predicate_true_for_vcs_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    assert is_code_repository(tmp_path)


@pytest.mark.parametrize("manifest", PROJECT_MANIFESTS)
def test_predicate_true_for_every_declared_manifest(tmp_path, manifest):
    (tmp_path / manifest).write_text("")
    assert is_code_repository(tmp_path)


def test_predicate_false_without_vcs_or_manifest(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "photos").mkdir()
    assert not is_code_repository(tmp_path)


def test_predicate_is_not_recursive(tmp_path):
    """A directory of repositories is not itself one."""
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    assert not is_code_repository(tmp_path)


def test_predicate_false_for_a_file(tmp_path):
    f = tmp_path / "pyproject.toml"
    f.write_text("")
    assert not is_code_repository(f)


# ── platform quirks: exactly three, all populated ─────────────────────────


def test_platform_quirks_are_a_closed_set_of_three():
    fields = PlatformQuirks.__dataclass_fields__
    assert set(fields) == {"path_separator", "path_quoting", "shell_dialect"}


def test_platform_quirks_are_all_populated():
    q = detect_platform_quirks()
    assert q.path_separator == os.sep
    assert q.path_quoting and q.shell_dialect


def test_rendered_map_names_all_three_quirks(repo):
    text = render_project_map(build_project_map(repo))
    for label in ("Path separator", "Paths with spaces", "Shell dialect"):
        assert label in text


# ── shape and entry points ────────────────────────────────────────────────


def test_map_records_directory_shape_and_skips_noise(repo):
    pm = build_project_map(repo)
    assert "src" in pm.top_level_dirs
    assert "tests" in pm.top_level_dirs
    assert "node_modules" not in pm.top_level_dirs
    assert ".git" not in pm.top_level_dirs
    assert pm.subdirs["src"] == ["demo"]


def test_map_records_entry_points_from_the_closed_list(repo):
    pm = build_project_map(repo)
    assert "src/cli.py" in pm.entry_points
    assert "Makefile" in pm.entry_points


def test_npm_scripts_become_entry_points(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "vite build", "dev": "vite"}})
    )
    pm = build_project_map(tmp_path)
    assert "npm run build" in pm.entry_points
    assert "npm run dev" in pm.entry_points


def test_malformed_package_json_is_reported_not_swallowed(tmp_path, caplog):
    (tmp_path / "package.json").write_text("{ not json")
    with caplog.at_level("WARNING"):
        pm = build_project_map(tmp_path)
    assert pm.entry_points == []
    assert any("not readable JSON" in r.message for r in caplog.records)


# ── binaries: one probe, extended ─────────────────────────────────────────


def test_dev_probe_extends_rather_than_duplicates_the_app_probe():
    """The extension is a superset in kind, not a second mechanism."""
    assert set(CLI_TOOL_PROBES) - set(DEV_TOOL_PROBES) <= {"code", "cursor", "brew"}
    assert len(DEV_TOOL_PROBES) > len(CLI_TOOL_PROBES)


def test_probe_binaries_agrees_with_which():
    import shutil

    probed = probe_binaries(["python", "definitely-not-a-real-binary-xyz"])
    assert probed["python"] == (shutil.which("python") is not None)
    assert probed["definitely-not-a-real-binary-xyz"] is False


def test_map_names_absent_commands_so_the_agent_does_not_try_them(repo, monkeypatch):
    monkeypatch.setenv("PATH", "")
    clear_project_map_cache()
    pm = build_project_map(repo)
    assert pm.tools_present == []
    assert pm.shell_commands == []
    assert set(pm.tools_absent) == set(DEV_TOOL_PROBES)
    assert "NOT installed" in render_project_map(pm)


def test_shell_allowlist_is_not_conflated_with_what_is_installed(repo):
    """Claiming run_shell_command accepts ``uv`` causes the very refusal
    this map exists to prevent."""
    from gaia.agents.tools.shell_tools import ALLOWED_COMMANDS

    pm = build_project_map(repo)
    assert set(pm.shell_commands) <= ALLOWED_COMMANDS
    off_limits = set(pm.tools_present) - ALLOWED_COMMANDS
    accepts = next(
        (ln for ln in render_project_map(pm).splitlines() if "accepts:" in ln), ""
    )
    named = set(re.findall(r"[\w.-]+", accepts.partition(":")[2]))
    assert not (named & off_limits)


# ── the budget, on the 32K profile ────────────────────────────────────────


def test_budget_is_a_small_fraction_of_the_npu_window():
    assert PROJECT_MAP_TOKEN_BUDGET == 600
    assert PROJECT_MAP_TOKEN_BUDGET < NPU_CTX_SIZE * 0.02


def test_render_stays_within_budget_on_a_realistic_repo(repo):
    assert count_tokens(render_project_map(build_project_map(repo))) <= (
        PROJECT_MAP_TOKEN_BUDGET
    )


def test_render_stays_within_budget_on_a_pathological_repo(tmp_path):
    """200 top-level directories, each with 20 children, must still fit."""
    (tmp_path / ".git").mkdir()
    for i in range(200):
        d = tmp_path / f"package_with_a_long_name_{i:03d}"
        d.mkdir()
        for j in range(20):
            (d / f"submodule_{j:02d}").mkdir()
    pm = build_project_map(tmp_path)
    assert count_tokens(render_project_map(pm)) <= PROJECT_MAP_TOKEN_BUDGET


def test_high_priority_sections_survive_truncation(tmp_path):
    """Quirks outrank the directory listing when the budget bites."""
    (tmp_path / ".git").mkdir()
    for i in range(200):
        (tmp_path / f"dir_{i:03d}").mkdir()
    text = render_project_map(build_project_map(tmp_path))
    assert "Shell dialect" in text
    assert "PROJECT MAP" in text


def test_budget_is_honoured_when_the_caller_lowers_it(repo):
    text = render_project_map(build_project_map(repo), token_budget=40)
    assert count_tokens(text) <= 40
    assert "PROJECT MAP" in text


# ── caching ───────────────────────────────────────────────────────────────


def test_map_is_cached_between_calls(repo):
    assert build_project_map(repo) is build_project_map(repo)


def test_cache_invalidates_when_a_directory_appears(repo):
    first = build_project_map(repo)
    (repo / "docs").mkdir()
    second = build_project_map(repo)
    assert second is not first
    assert "docs" in second.top_level_dirs


def test_cache_invalidates_when_a_manifest_changes(repo):
    first = build_project_map(repo)
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\nversion='2'\n")
    assert build_project_map(repo) is not first


def test_cache_invalidates_when_path_changes(repo, monkeypatch):
    first = build_project_map(repo)
    monkeypatch.setenv("PATH", "/some/other/place")
    assert build_project_map(repo) is not first


# ── root resolution ───────────────────────────────────────────────────────


def test_explicit_root_wins(repo, monkeypatch):
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(repo.parent))
    assert resolve_project_root(str(repo)) == str(repo.resolve())


def test_env_root_is_used_when_no_explicit_root(repo, monkeypatch):
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(repo))
    assert resolve_project_root() == str(repo.resolve())


def test_a_nonexistent_root_fails_loudly(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(ValueError, match="not a directory"):
        resolve_project_root(str(missing))


def test_cwd_resolves_when_it_is_a_repository(repo, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.chdir(repo)
    assert resolve_project_root() == str(repo.resolve())


def test_a_subdirectory_resolves_to_the_repository_above_it(repo, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.chdir(repo / "src" / "demo")
    assert resolve_project_root() == str(repo.resolve())


def test_no_root_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    plain = tmp_path / "just" / "files"
    plain.mkdir(parents=True)
    monkeypatch.chdir(plain)
    assert resolve_project_root() is None


# ── the mixin: prompt injection and the index trigger ─────────────────────


class _FakeSDK:
    def __init__(self, indexed: bool):
        self._indexed = indexed
        self.status_calls = 0

    def is_indexed(self):
        return self._indexed

    def get_status(self):
        self.status_calls += 1
        return {"indexed": self._indexed}


class _Base:
    """Stands in for ``Agent``: a no-op ``_on_task_start`` that ends the chain."""

    def _on_task_start(self, user_input: str) -> None:
        pass


class _FakeAgent(ProjectMapMixin, _Base):
    def __init__(self, root, indexed=False, auto_index=True):
        self.config = type(
            "C", (), {"project_root": str(root), "auto_index": auto_index}
        )()
        self.sdk = _FakeSDK(indexed)
        self.index_calls = []
        self._tools_registry = {
            "index_codebase": {"function": lambda **kw: self.index_calls.append(kw)},
            "run_shell_command": {"function": lambda **kw: None},
        }

    def _get_code_index_sdk(self):
        return self.sdk


def _join(agent):
    """Run the background index thread to completion."""
    for t in threading.enumerate():
        if t.name == "gaia-project-map-index":
            t.join(timeout=10)
    return agent.index_calls


def test_prompt_fragment_is_the_rendered_map(repo):
    text = _FakeAgent(repo).get_project_map_system_prompt()
    assert text.startswith("==== PROJECT MAP ====")
    assert str(repo.resolve()) in text
    assert count_tokens(text) <= PROJECT_MAP_TOKEN_BUDGET


def test_prompt_fragment_is_empty_outside_a_project(tmp_path, monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    plain = tmp_path / "docs-only"
    plain.mkdir()
    monkeypatch.chdir(plain)
    agent = _FakeAgent(plain)
    agent.config.project_root = None
    assert agent.get_project_map_system_prompt() == ""


def test_index_trigger_fires_for_an_unindexed_repository(repo):
    agent = _FakeAgent(repo, indexed=False)
    agent._on_task_start("do a thing")
    assert _join(agent) == [{}]


def test_prompt_says_building_while_the_index_is_running(repo):
    gate = threading.Event()
    agent = _FakeAgent(repo, indexed=False)
    agent._tools_registry["index_codebase"]["function"] = lambda **kw: gate.wait(10)
    agent._on_task_start("do a thing")
    try:
        text = agent.get_project_map_system_prompt()
    finally:
        gate.set()
        _join(agent)
    assert "building now in the background" in text


def test_prompt_reports_a_failed_index_instead_of_waiting_forever(repo):
    """A dead background index must not read as "still building" all session."""

    def _boom(**_kw):
        raise RuntimeError("faiss exploded")

    agent = _FakeAgent(repo, indexed=False)
    agent._tools_registry["index_codebase"]["function"] = _boom
    agent._on_task_start("do a thing")
    _join(agent)
    assert "build FAILED" in agent.get_project_map_system_prompt()


def test_a_json_error_from_the_tool_counts_as_a_failure(repo, caplog):
    """``index_codebase`` reports refusals as JSON, not by raising."""
    agent = _FakeAgent(repo, indexed=False)
    agent._tools_registry["index_codebase"]["function"] = lambda **kw: json.dumps(
        {"error": "refused: home directory"}
    )
    with caplog.at_level("ERROR"):
        agent._on_task_start("do a thing")
        _join(agent)
    assert "build FAILED" in agent.get_project_map_system_prompt()
    assert any("refused: home directory" in r.getMessage() for r in caplog.records)


def test_index_status_is_a_presence_check_not_a_metadata_parse(repo):
    """``get_status`` parses every indexed chunk; the prompt renders every turn."""
    agent = _FakeAgent(repo, indexed=True)
    for _ in range(3):
        agent.get_project_map_system_prompt()
    assert agent.sdk.status_calls == 0


def test_index_trigger_is_skipped_when_already_indexed(repo):
    agent = _FakeAgent(repo, indexed=True)
    agent._on_task_start("do a thing")
    assert _join(agent) == []
    assert "search_code_index" in agent.get_project_map_system_prompt()


def test_index_trigger_fires_at_most_once(repo):
    agent = _FakeAgent(repo, indexed=False)
    for _ in range(3):
        agent._on_task_start("again")
    assert len(_join(agent)) == 1


def test_index_trigger_respects_the_config_off_switch(repo):
    agent = _FakeAgent(repo, indexed=False, auto_index=False)
    agent._on_task_start("do a thing")
    assert _join(agent) == []


def test_index_trigger_respects_the_env_off_switch(repo, monkeypatch):
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    agent = _FakeAgent(repo, indexed=False)
    agent._on_task_start("do a thing")
    assert _join(agent) == []


def test_index_trigger_skipped_for_a_non_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    agent = _FakeAgent(plain, indexed=False)
    agent._on_task_start("do a thing")
    assert _join(agent) == []


def test_background_index_failure_is_logged_not_swallowed(repo, caplog):
    def _boom(**_kw):
        raise RuntimeError("faiss exploded")

    agent = _FakeAgent(repo, indexed=False)
    agent._tools_registry["index_codebase"]["function"] = _boom
    with caplog.at_level("ERROR"):
        agent._on_task_start("do a thing")
        _join(agent)
    assert any("faiss exploded" in r.getMessage() for r in caplog.records)


# ── wiring, checked against the source ────────────────────────────────────
#
# ``gaia_agent`` resolves through an editable install that can point at a
# different checkout, so importing ``GaiaAgent`` here would assert against the
# wrong tree. These parse this repository's files instead.

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _bases_of(path: pathlib.Path, class_name: str) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [b.id for b in node.bases if isinstance(b, ast.Name)]
    raise AssertionError(f"class {class_name!r} not found in {path}")


def test_project_map_mixin_precedes_the_base_agent_in_gaia_agent():
    """Listed after ChatAgent, ``Agent``'s no-op hook would shadow the override."""
    bases = _bases_of(
        _REPO / "hub" / "agents" / "gaia" / "python" / "gaia_agent" / "agent.py",
        "GaiaAgent",
    )
    assert bases[0] == "ProjectMapMixin"
    assert "ChatAgent" in bases[1:]


def test_base_agent_calls_the_task_start_hook_before_composing_the_prompt():
    src = (_REPO / "src" / "gaia" / "agents" / "base" / "agent.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    impl = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_process_query_impl"
    )
    called = [
        n.func.attr
        for n in ast.walk(impl)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert "_on_task_start" in called
    assert called.index("_on_task_start") < called.index("_refresh_active_tool_filter")


def test_base_agent_hook_is_a_no_op_so_agents_without_the_mixin_are_unaffected():
    from gaia.agents.base.agent import Agent

    assert Agent._on_task_start(object(), "anything") is None


def test_the_agents_own_source_tree_is_never_the_project(monkeypatch):
    """The dev-mode sidecar's cwd is the GAIA checkout — not the user's work."""
    import gaia

    gaia_repo = pathlib.Path(gaia.__file__).resolve().parents[2]
    assert is_agent_own_source(gaia_repo)
    assert is_code_repository(gaia_repo), "precondition: it looks like a project"

    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.chdir(gaia_repo)
    assert resolve_project_root() is None


def test_an_explicit_root_may_point_at_gaia_itself(monkeypatch):
    """Working on GAIA is legitimate — it just has to be asked for."""
    import gaia

    gaia_repo = pathlib.Path(gaia.__file__).resolve().parents[2]
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    assert resolve_project_root(str(gaia_repo)) == str(gaia_repo)


def test_shell_commands_are_omitted_for_an_agent_without_the_shell_tool(repo):
    text = render_project_map(build_project_map(repo), has_shell_tool=False)
    assert "run_shell_command" not in text
    assert "NOT installed" in text


def test_a_wrong_base_order_fails_at_class_definition():
    """Silent otherwise: the prompt fragment renders either way."""
    from gaia.agents.base.agent import Agent

    with pytest.raises(TypeError, match="ProjectMapMixin after Agent"):

        class _Wrong(Agent, ProjectMapMixin):
            pass
