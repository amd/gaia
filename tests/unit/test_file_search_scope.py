# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""File search must look where the user's work is, and say where it looked.

Ask the flagship how many Go files are under `tui/internal` and it answered
"Zero." There are 203. Two things compounded (#3576):

* the search was rooted at ``Path.cwd()``, which for a daemon-spawned sidecar is
  its own package directory — the user's project was not in the search set;
* nothing downstream could tell that apart from a genuine zero, because the
  result was ``status: "success"`` with no record of where it looked.

These tests build a fake project, point the agent's sandbox at it, and assert
the search reaches it — from a working directory that is somewhere else
entirely, which is the condition the bug needed.

No LLM or external service required.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin


class _Sandbox:
    """Stand-in for PathValidator: the paths the operator declared.

    Implements the read gate the tools actually call — ``validate_read`` as
    well as ``is_path_allowed`` — so this double cannot pass while the real
    validator's interface moves underneath it.
    """

    def __init__(self, *paths):
        self.allowed_paths = {Path(p).resolve() for p in paths}

    def is_path_allowed(self, path, prompt_user=True):
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.allowed_paths
        )

    def validate_read(self, path, prompt_user=True):
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return False, f"Access denied: '{path}' is not in allowed paths"
        return True, ""


@pytest.fixture
def project(tmp_path):
    """A project with Go files, and a separate directory to run the agent from.

    ``elsewhere`` stands in for the sidecar's own package directory — the cwd
    the daemon actually spawns it in.
    """
    root = tmp_path / "myproject"
    (root / "tui" / "internal").mkdir(parents=True)
    for name in ("model.go", "view.go", "controller.go"):
        (root / "tui" / "internal" / name).write_text("package internal\n")
    (root / "README.md").write_text("# project\n")

    elsewhere = tmp_path / "sidecar-package-dir"
    elsewhere.mkdir()

    prev = Path.cwd()
    os.chdir(elsewhere)
    try:
        yield root, elsewhere
    finally:
        os.chdir(prev)


# ============================================================================
# 1. file_tools.search_file
# ============================================================================


@pytest.fixture
def search_file(project):
    root, _ = project
    mixin = FileSearchToolsMixin()
    mixin.path_validator = _Sandbox(root)

    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        entry = _TOOL_REGISTRY.get("search_file")
        assert entry is not None, "search_file was not registered"
        fn = entry["function"]
        fn.mixin = mixin
        yield fn
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestSearchFileLooksAtTheWorkspace:
    def test_it_finds_project_files_from_an_unrelated_cwd(self, search_file, project):
        """The condition the bug needed: cwd is not the project."""
        result = search_file("*.go")
        assert result["status"] == "success", result
        assert result["count"] == 3, result

    def test_a_named_directory_scopes_the_search(self, search_file, project):
        root, _ = project
        result = search_file("*.go", directory=str(root / "tui" / "internal"))
        assert result["count"] == 3
        # Parts, not a substring: "tui/internal" is not how Windows spells it.
        assert all(Path(f).parts[-3:-1] == ("tui", "internal") for f in result["files"])

    def test_a_relative_directory_resolves_against_the_workspace(
        self, search_file, project
    ):
        """The user says "in tui/internal"; they do not mean the process cwd."""
        result = search_file("*.go", directory="tui/internal")
        assert result["count"] == 3

    def test_a_directory_that_does_not_exist_is_an_error_not_an_empty_result(
        self, search_file, project
    ):
        root, _ = project
        result = search_file("*.go", directory=str(root / "no" / "such" / "folder"))
        assert result["status"] == "error"
        assert "not an empty result" in result["error"]

    def test_a_typod_relative_directory_reads_as_a_typo(self, search_file):
        """It matches no workspace root — say that, and list the roots.

        Resolving it against the process cwd and reporting "access denied"
        named an absolute path the user never typed. Being specific is safe
        here: no absolute path was supplied, so this is not an existence
        oracle.
        """
        result = search_file("*.go", directory="no/such/folder")

        assert result["status"] == "error"
        assert "not under any workspace root" in result["error"]
        assert "not an empty result" in result["error"]
        assert result["workspace_roots"]

    def test_a_typod_relative_directory_does_not_name_a_path_the_user_never_typed(
        self, search_file
    ):
        result = search_file("*.go", directory="no/such/folder")
        assert "sidecar-package-dir" not in result["error"]

    def test_a_directory_outside_the_sandbox_is_refused(self, search_file, tmp_path):
        outside = tmp_path / "not-my-project"
        outside.mkdir()
        result = search_file("*.go", directory=str(outside))
        assert result["status"] == "error"
        assert "not in allowed paths" in result["error"]

    def test_a_genuine_zero_says_where_it_looked(self, search_file, project):
        root, _ = project
        result = search_file("*.rs", directory=str(root))
        assert result["count"] == 0
        assert result["searched_paths"], result
        assert str(root) in result["display_message"]

    def test_a_genuine_zero_tells_the_model_not_to_generalise_it(
        self, search_file, project
    ):
        root, _ = project
        result = search_file("*.rs", directory=str(root))
        assert "not zero on the machine" in result["suggestion"]


# ============================================================================
# 2. filesystem_tools.find_files — the flagship's search tool
# ============================================================================


def _filesystem_agent(root):
    class MockAgent(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = _Sandbox(root)
            self._fs_index = None
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        agent = MockAgent()
        agent.register_filesystem_tools()
    return agent, registered


class TestFindFilesLooksAtTheWorkspace:
    def test_smart_scope_reaches_the_project_not_the_cwd(self, project):
        root, elsewhere = project
        agent, tools = _filesystem_agent(root)

        out = tools["find_files"]("*.go")

        assert "model.go" in out, out
        assert str(elsewhere) not in out

    def test_cwd_scope_means_the_workspace_not_the_process_directory(self, project):
        root, _ = project
        agent, tools = _filesystem_agent(root)

        assert "model.go" in tools["find_files"]("*.go", scope="cwd")

    def test_workspace_roots_prefers_the_sandbox_over_the_process_cwd(self, project):
        root, elsewhere = project
        agent, _ = _filesystem_agent(root)

        roots = agent.workspace_roots()

        assert roots == [str(root.resolve())]
        assert str(elsewhere.resolve()) not in roots

    def test_workspace_roots_falls_back_to_cwd_without_a_sandbox(self, project):
        _, elsewhere = project

        class Bare(FileSystemToolsMixin):
            pass

        # resolve(): on macOS tmp_path is /var/... while cwd reports /private/var.
        assert Bare().workspace_roots() == [str(elsewhere.resolve())]


# ============================================================================
# 3. A named directory stays the whole scope — deep search included
# ============================================================================


class TestDeepSearchCannotEscapeANamedDirectory:
    """The model's natural next move after a scoped miss is `deep_search=True`.

    That used to fall through to a drive-wide sweep, so "no .rs files in
    tui/internal" could come back with a match from somewhere else entirely —
    the same wrong answer this fix exists to remove, one step softer.
    """

    def test_a_scoped_miss_does_not_sweep_the_drive(self, search_file, project):
        root, _ = project
        (root / "elsewhere.rs").write_text("fn main() {}\n")

        result = search_file(
            "*.rs", directory=str(root / "tui" / "internal"), deep_search=True
        )

        assert result["count"] == 0, result
        assert not result["files"]

    def test_a_scoped_miss_does_not_offer_deep_search_either(
        self, search_file, project
    ):
        root, _ = project
        result = search_file("*.rs", directory=str(root), deep_search=False)
        assert result["deep_search_available"] is False

    def test_an_unscoped_miss_still_offers_deep_search(self, search_file):
        result = search_file("*.rs")
        assert result["deep_search_available"] is True


# ============================================================================
# 4. Roots: most specific first, and only the project walked exhaustively
# ============================================================================


class TestRootOrderAndDepth:
    def test_roots_are_ordered_most_specific_first(self, tmp_path):
        from gaia.agents.tools.search_scope import search_roots

        shallow = tmp_path / "a"
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)

        class Host:
            path_validator = _Sandbox(shallow, deep)

        assert search_roots(Host()) == [deep.resolve(), shallow.resolve()]

    def test_a_relative_directory_resolves_against_the_deepest_match(
        self, tmp_path, monkeypatch
    ):
        """Both roots contain `pkg/`; the user meant the one they are in."""
        outer = tmp_path / "outer"
        inner = tmp_path / "outer" / "inner"
        (outer / "pkg").mkdir(parents=True)
        (inner / "pkg").mkdir(parents=True)
        (outer / "pkg" / "outer.go").write_text("package pkg\n")
        (inner / "pkg" / "inner.go").write_text("package pkg\n")

        mixin = FileSearchToolsMixin()
        mixin.path_validator = _Sandbox(outer, inner)
        saved = dict(_TOOL_REGISTRY)
        try:
            mixin.register_file_search_tools()
            fn = _TOOL_REGISTRY["search_file"]["function"]
            result = fn("*.go", directory="pkg")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

        assert [Path(f).name for f in result["files"]] == ["inner.go"]

    def test_only_the_primary_root_is_walked_exhaustively(self, tmp_path):
        """An accumulated approval must not be traversed in full on every miss."""
        from gaia.agents.tools.search_scope import (
            DEEP_ROOT_DEPTH,
            SHALLOW_ROOT_DEPTH,
            root_depth,
        )

        project = tmp_path / "project" / "nested"
        documents = tmp_path / "Documents"
        project.mkdir(parents=True)
        documents.mkdir()
        roots = [project.resolve(), documents.resolve()]

        assert root_depth(roots[0], roots) == DEEP_ROOT_DEPTH
        assert root_depth(roots[1], roots) == SHALLOW_ROOT_DEPTH

    def test_a_named_directory_is_always_walked_exhaustively(
        self, search_file, project
    ):
        """Scope came from the user, so depth is not a guess."""
        root, _ = project
        deep = root / "tui" / "internal" / "a" / "b" / "c" / "d" / "e" / "f"
        deep.mkdir(parents=True)
        (deep / "buried.go").write_text("package f\n")

        result = search_file("buried", directory=str(root / "tui"))

        assert [Path(f).name for f in result["files"]] == ["buried.go"]


# ============================================================================
# 5. The index path must agree with the walk path
# ============================================================================


class TestTheIndexScopeUsesTheSameRoots:
    """#3671 added an index-hit scope filter that resolved `cwd` to Path.cwd().

    Left that way, a hit the walk correctly found under the workspace would be
    filtered back out because it is not under the directory the sidecar was
    spawned in — the two halves of one search disagreeing.
    """

    def test_cwd_scope_resolves_to_the_workspace_not_the_process_directory(
        self, project
    ):
        from gaia.agents.tools.filesystem_tools import _scope_roots

        root, elsewhere = project
        agent, _ = _filesystem_agent(root)

        roots = _scope_roots("cwd", agent)

        assert roots == [root.resolve()]
        assert elsewhere.resolve() not in roots

    def test_a_workspace_file_passes_the_index_scope_filter(self, project):
        from gaia.agents.tools.filesystem_tools import (
            _path_in_roots,
            _scope_roots,
        )

        root, _ = project
        agent, _ = _filesystem_agent(root)

        hit = str(root / "tui" / "internal" / "model.go")
        assert _path_in_roots(hit, _scope_roots("cwd", agent)) is True

    def test_an_outside_file_still_fails_it(self, project):
        from gaia.agents.tools.filesystem_tools import (
            _path_in_roots,
            _scope_roots,
        )

        root, elsewhere = project
        agent, _ = _filesystem_agent(root)

        outside = str(elsewhere / "stray.go")
        assert _path_in_roots(outside, _scope_roots("cwd", agent)) is False

    def test_unbounded_scopes_are_still_unbounded(self, project):
        from gaia.agents.tools.filesystem_tools import _scope_roots

        root, _ = project
        agent, _ = _filesystem_agent(root)

        assert _scope_roots("smart", agent) == []
        assert _scope_roots("everywhere", agent) == []

    def test_an_explicit_path_scope_is_unchanged(self, project):
        from gaia.agents.tools.filesystem_tools import _scope_roots

        root, _ = project
        agent, _ = _filesystem_agent(root)
        named = root / "tui"

        assert _scope_roots(str(named), agent) == [named.resolve()]


# ============================================================================
# 6. Full access: the permission boundary is not the search scope
# ============================================================================


_FS_ROOT = Path("/").resolve()  # "C:\\" on Windows


def _search_file_tool(validator):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = validator
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        fn = _TOOL_REGISTRY["search_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
    return fn


@pytest.fixture
def full_access_project(tmp_path, monkeypatch):
    """A small project as the cwd, with ``/`` as the only allowed path.

    What the TUI's full-access mode and the benchmark harness set up.
    """
    root = (tmp_path / "proj").resolve()
    (root / "src").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text("# changes\n")
    (root / "src" / "load_config.py").write_text("def load_config(): ...\n")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def listed_dirs(tmp_path, monkeypatch):
    """Record every directory listed, and keep the walk off the real disk.

    Directories outside ``tmp_path`` list as empty, so a regression shows up as
    a wrong depth or a missing result — not as a multi-minute crawl of ``/``.
    """
    top = tmp_path.resolve()
    real_iterdir = Path.iterdir
    listed = []

    def bounded_iterdir(self):
        listed.append(self)
        resolved = self.resolve()
        if resolved == top or top in resolved.parents:
            return real_iterdir(self)
        return iter(())

    monkeypatch.setattr(Path, "iterdir", bounded_iterdir)
    return listed


@pytest.fixture
def root_depths(monkeypatch):
    """The depth ``search_file`` gave each workspace root, in walk order."""
    import gaia.agents.tools.file_tools as file_tools_module

    depths = {}
    real_depth = file_tools_module.root_depth

    def recording_depth(root, roots):
        depths[Path(root)] = real_depth(root, roots)
        return depths[Path(root)]

    monkeypatch.setattr(file_tools_module, "root_depth", recording_depth)
    return depths


class TestFullAccessSearchesTheWorkspace:
    def test_an_undirected_search_finds_the_project_file(
        self, full_access_project, listed_dirs
    ):
        search_file = _search_file_tool(_Sandbox(_FS_ROOT))

        result = search_file("CHANGELOG.md")

        assert result["status"] == "success", result
        assert result["files"][0] == str(full_access_project / "CHANGELOG.md")

    def test_only_the_project_is_walked_deeply(
        self, full_access_project, listed_dirs, root_depths
    ):
        from gaia.agents.tools.search_scope import DEEP_ROOT_DEPTH, SHALLOW_ROOT_DEPTH

        search_file = _search_file_tool(_Sandbox(_FS_ROOT))

        result = search_file("load_config")

        assert [Path(f).name for f in result["files"]] == ["load_config.py"]
        assert list(root_depths) == [full_access_project, _FS_ROOT]
        assert root_depths[full_access_project] == DEEP_ROOT_DEPTH
        assert root_depths[_FS_ROOT] == SHALLOW_ROOT_DEPTH

    def test_the_filesystem_root_alone_is_never_walked_exhaustively(
        self, tmp_path, monkeypatch, listed_dirs, root_depths
    ):
        """No workspace to prefer (cwd is home): ``/`` is still capped."""
        from gaia.agents.tools.search_scope import SHALLOW_ROOT_DEPTH

        home = (tmp_path / "home").resolve()
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.chdir(home)
        search_file = _search_file_tool(_Sandbox(_FS_ROOT))

        search_file("anything")

        assert root_depths == {_FS_ROOT: SHALLOW_ROOT_DEPTH}

    def test_deep_search_still_sweeps_the_drive_when_asked(
        self, full_access_project, listed_dirs, monkeypatch
    ):
        """``deep_search`` is the explicit request for a whole-disk walk."""
        import gaia.agents.tools.file_tools as file_tools_module

        monkeypatch.setattr(file_tools_module.platform, "system", lambda: "Linux")
        search_file = _search_file_tool(_Sandbox(_FS_ROOT))

        search_file("no-such-file-anywhere", deep_search=True)

        assert listed_dirs[0] == full_access_project
        # The capped workspace pass, then the explicit sweep.
        assert [p.resolve() for p in listed_dirs].count(_FS_ROOT) == 2


class TestAHomeSandboxIsCappedToo:
    """The flagship's default sandbox is ``~`` — also not a project."""

    @pytest.fixture
    def home_with_project(self, tmp_path, monkeypatch):
        home = (tmp_path / "home").resolve()
        project = home / "work" / "proj"
        deep_in_project = project / "a" / "b" / "c" / "d" / "e" / "f"
        deep_in_project.mkdir(parents=True)
        (deep_in_project / "target_notes.md").write_text("x\n")
        deep_elsewhere = home / "g" / "h" / "i" / "j" / "k" / "l" / "m" / "n"
        deep_elsewhere.mkdir(parents=True)
        (deep_elsewhere / "target_other.md").write_text("x\n")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.chdir(project)
        return home, project

    def test_the_workspace_goes_first_and_home_is_shallow(self, home_with_project):
        from gaia.agents.tools.search_scope import (
            DEEP_ROOT_DEPTH,
            SHALLOW_ROOT_DEPTH,
            root_depth,
            search_roots,
        )

        home, project = home_with_project

        class Host:
            path_validator = _Sandbox(home)

        roots = search_roots(Host())

        assert roots == [project, home]
        assert root_depth(project, roots) == DEEP_ROOT_DEPTH
        assert root_depth(home, roots) == SHALLOW_ROOT_DEPTH

    def test_search_file_finds_deep_project_files_but_does_not_crawl_home(
        self, home_with_project
    ):
        home, _ = home_with_project
        search_file = _search_file_tool(_Sandbox(home))

        result = search_file("target")

        assert [Path(f).name for f in result["files"]] == ["target_notes.md"]

    def test_find_files_does_not_crawl_home_either(self, home_with_project):
        home, _ = home_with_project
        _, tools = _filesystem_agent(home)

        for scope in ("cwd", "smart"):
            out = tools["find_files"]("target_*", scope=scope)
            assert "target_notes.md" in out, (scope, out)
            assert "target_other.md" not in out, (scope, out)

    def test_an_explicit_home_scope_is_not_capped(self, home_with_project):
        """The user asked for home by name, so the depth is not a guess."""
        home, _ = home_with_project
        _, tools = _filesystem_agent(home)

        assert "target_other.md" in tools["find_files"]("target_*", scope="home")


class TestBothToolsAgreeOnTheProject:
    """The two search tools must not answer the same question differently.

    ``search_scope`` exists so the depth rule has one home. A copy of it inside
    ``find_files`` capped the project at ten levels while ``search_file`` walked
    it to ``DEEP_ROOT_DEPTH``, so whether a file was findable depended on which
    tool the model happened to pick.
    """

    @pytest.fixture
    def deep_project(self, tmp_path, monkeypatch):
        root = (tmp_path / "proj").resolve()
        buried = root.joinpath(*(f"lvl{i}" for i in range(12)))
        buried.mkdir(parents=True)
        (buried / "buried_config.py").write_text("BURIED = 1\n")
        monkeypatch.chdir(root)
        return root

    def test_find_files_reaches_a_file_below_the_old_ten_level_cap(self, deep_project):
        _, tools = _filesystem_agent(deep_project)

        for scope in ("cwd", "smart"):
            assert "buried_config.py" in tools["find_files"](
                "buried_*", scope=scope
            ), scope

    def test_search_file_reaches_it_too(self, deep_project):
        mixin = FileSearchToolsMixin()
        mixin.path_validator = _Sandbox(deep_project)
        saved = dict(_TOOL_REGISTRY)
        try:
            mixin.register_file_search_tools()
            fn = _TOOL_REGISTRY["search_file"]["function"]
            fn.mixin = mixin
            result = fn("buried_config.py")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

        assert [Path(f).name for f in result["files"]] == ["buried_config.py"]

    def test_a_broad_root_is_still_capped_on_the_content_path(self, tmp_path):
        """Deferring to the shared policy must not lift the full-access cap.

        Content search has its own ceiling of 8; the shallow cap is lower, and
        taking the larger of the two would crawl ``/`` two levels further than
        before this module existed.
        """
        from gaia.agents.tools.search_scope import SHALLOW_ROOT_DEPTH, root_depth

        assert root_depth(_FS_ROOT, [_FS_ROOT]) == SHALLOW_ROOT_DEPTH
        assert min(8, root_depth(_FS_ROOT, [_FS_ROOT])) == SHALLOW_ROOT_DEPTH


class TestProjectSandboxesAreUnchanged:
    def test_a_cwd_inside_a_project_root_does_not_reorder_roots(
        self, tmp_path, monkeypatch
    ):
        from gaia.agents.tools.search_scope import search_roots

        outer = (tmp_path / "outer").resolve()
        inner = outer / "inner"
        (inner / "sub").mkdir(parents=True)
        monkeypatch.chdir(inner / "sub")

        class Host:
            path_validator = _Sandbox(outer, inner)

        assert search_roots(Host()) == [inner, outer]

    def test_a_cwd_outside_the_sandbox_is_not_added(self, tmp_path, monkeypatch):
        from gaia.agents.tools.search_scope import search_roots

        allowed = (tmp_path / "allowed").resolve()
        allowed.mkdir()
        outside = (tmp_path / "outside").resolve()
        outside.mkdir()
        monkeypatch.chdir(outside)

        class Host:
            path_validator = _Sandbox(allowed)

        assert search_roots(Host()) == [allowed]

    def test_a_recorded_project_root_wins_over_the_cwd(self, tmp_path, monkeypatch):
        """A sidecar's cwd is its package dir; the agent records the real project."""
        from gaia.agents.tools.search_scope import search_roots

        project = (tmp_path / "proj").resolve()
        project.mkdir()
        elsewhere = (tmp_path / "pkg").resolve()
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        class Host:
            path_validator = _Sandbox(_FS_ROOT)

            def _project_map_root(self):
                return str(project)

        assert search_roots(Host()) == [project, _FS_ROOT]


# ============================================================================
# 7. A walk is bounded, so a huge root cannot hang the call (#3889)
# ============================================================================


def _register_search_file(sandbox):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = sandbox
    mixin.register_file_search_tools()
    return _TOOL_REGISTRY["search_file"]["function"]


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def big_home(tmp_path):
    """A home-sized sandbox: many folders, and a cwd elsewhere."""
    home = tmp_path / "home"
    for d in range(40):
        folder = home / f"folder{d}" / "sub"
        folder.mkdir(parents=True)
        for f in range(25):
            (folder / f"notes{f}.txt").write_text("x\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    prev = Path.cwd()
    os.chdir(elsewhere)
    try:
        yield home
    finally:
        os.chdir(prev)


class TestTheWalkIsBounded:
    def test_an_exhausted_entry_budget_returns_what_it_found_marked_truncated(
        self, big_home, registry, monkeypatch
    ):
        import time

        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_ENTRY_BUDGET", 30)
        search_file = _register_search_file(_Sandbox(big_home))

        started = time.monotonic()
        result = search_file("notes")
        elapsed = time.monotonic() - started

        assert elapsed < 5, elapsed
        assert result["status"] == "success"
        assert result["truncated"] is True
        assert result["count"] > 0, result
        assert "pass `directory`" in result["hint"]

    def test_a_truncated_miss_does_not_read_as_an_honest_zero(
        self, big_home, registry, monkeypatch
    ):
        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_ENTRY_BUDGET", 30)
        search_file = _register_search_file(_Sandbox(big_home))

        result = search_file("pyproject.toml")

        assert result["count"] == 0
        assert result["truncated"] is True
        assert "NOT a complete zero" in result["suggestion"]
        assert "No files matching" not in result["display_message"]

    def test_an_exhausted_time_budget_stops_the_walk(
        self, big_home, registry, monkeypatch
    ):
        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_TIME_BUDGET_S", 0.0)
        search_file = _register_search_file(_Sandbox(big_home))

        result = search_file("notes")

        assert result["truncated"] is True
        assert result["count"] == 0

    def test_a_named_directory_is_bounded_too(self, big_home, registry, monkeypatch):
        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_ENTRY_BUDGET", 30)
        search_file = _register_search_file(_Sandbox(big_home))

        result = search_file("pyproject.toml", directory=str(big_home))

        assert result["truncated"] is True
        assert "narrower `directory`" in result["hint"]

    def test_a_search_within_budget_is_not_marked_truncated(self, search_file, project):
        result = search_file("*.go")
        assert "truncated" not in result


class TestANarrowSandboxKeepsItsDepth:
    """The #3889 demotion must not cost coverage in a project-sized sandbox.

    Gating it on ``$HOME`` was the fix: with ``allowed_paths=[project]`` and a
    cwd in one of its subdirectories, demoting the project to
    :data:`SHALLOW_ROOT_DEPTH` put its own deeply-nested files out of reach —
    and the miss came back as a plain zero, so nothing signalled the loss.
    """

    @pytest.fixture
    def project_with_subdir(self, tmp_path, monkeypatch):
        home = (tmp_path / "home").resolve()
        project = home / "work" / "gaia"
        buried = project / "a" / "b" / "c" / "d" / "e" / "f" / "g"
        buried.mkdir(parents=True)
        (buried / "buried_in_project.py").write_text("x = 1\n")
        subdir = project / "tui"
        subdir.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.chdir(subdir)
        return project, subdir

    def test_a_deeply_nested_file_is_still_found(self, project_with_subdir, registry):
        project, _ = project_with_subdir
        search_file = _register_search_file(_Sandbox(project))

        result = search_file("buried_in_project")

        assert [Path(f).name for f in result["files"]] == ["buried_in_project.py"]


class TestTruncationNamesTheBudgetThatTripped:
    def test_an_entry_budget_says_so(self, big_home, registry, monkeypatch):
        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_ENTRY_BUDGET", 30)
        search_file = _register_search_file(_Sandbox(big_home))

        result = search_file("pyproject.toml")

        assert "files and folders" in result["hint"], result["hint"]
        assert " s \u2014" not in result["hint"]

    def test_a_time_budget_says_so(self, big_home, registry, monkeypatch):
        from gaia.agents.tools import search_scope

        monkeypatch.setattr(search_scope, "SEARCH_TIME_BUDGET_S", 0.0)
        search_file = _register_search_file(_Sandbox(big_home))

        result = search_file("pyproject.toml")

        assert "files and folders" not in result["hint"], result["hint"]
        assert " s \u2014" in result["hint"]


class TestCommonFoldersAreMatchedByPathNotPrefix:
    def test_a_root_whose_name_prefixes_a_common_folder_does_not_hide_it(
        self, tmp_path, monkeypatch, registry
    ):
        home = (tmp_path / "home").resolve()
        (home / "Doc").mkdir(parents=True)
        (home / "Documents").mkdir()
        (home / "Documents" / "quarterly_report.txt").write_text("x\n")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.chdir(tmp_path)
        search_file = _register_search_file(_Sandbox(home / "Doc"))

        result = search_file("quarterly_report")

        assert [Path(f).name for f in result["files"]] == ["quarterly_report.txt"]
