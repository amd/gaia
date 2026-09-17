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
