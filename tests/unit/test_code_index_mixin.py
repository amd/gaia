# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for CodeIndexToolsMixin.

Tests cover: state initialisation, tool registration via a minimal composed
Agent subclass, error paths when faiss / code_index is unavailable, and
lazy SDK construction behaviour. LLM / Lemonade dependencies are mocked.
"""

import json
import os
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear the global tool registry before and after each test."""
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Mixin availability guard
# ---------------------------------------------------------------------------

try:
    from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin

    MIXIN_AVAILABLE = True
except ImportError:
    MIXIN_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MIXIN_AVAILABLE, reason="CodeIndexToolsMixin not importable"
)


# ---------------------------------------------------------------------------
# Helpers — minimal test harness that composes only the mixin
# ---------------------------------------------------------------------------

_EXPECTED_TOOLS = {
    "index_codebase",
    "search_code_index",
    "get_index_status",
    "clear_code_index",
}


class _Harness(CodeIndexToolsMixin):
    """Minimal standalone consumer of the mixin (no Agent base).

    Avoids pulling in the full CodeAgent stack so these tests focus on the
    mixin's own behaviour.
    """

    def __init__(self, repo_path="."):
        self._init_code_index_state(repo_path=repo_path)
        self.register_code_index_tools()


def make_harness(tmp_path=None):
    repo = str(tmp_path) if tmp_path else "."
    return _Harness(repo_path=repo)


# ---------------------------------------------------------------------------
# Tests: state initialisation
# ---------------------------------------------------------------------------


class TestStateInit:
    def test_repo_path_is_absolute(self, tmp_path):
        h = make_harness(tmp_path)
        assert os.path.isabs(h._repo_path)

    def test_sdk_not_initialised_at_construction(self, tmp_path):
        h = make_harness(tmp_path)
        assert h._code_index_sdk is None

    def test_ensure_state_idempotent(self, tmp_path):
        h = make_harness(tmp_path)
        original_repo = h._repo_path
        h._ensure_code_index_state()
        assert h._repo_path == original_repo


# ---------------------------------------------------------------------------
# Tests: tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_all_tools_registered(self, tmp_path):
        make_harness(tmp_path)
        assert _EXPECTED_TOOLS.issubset(set(_TOOL_REGISTRY.keys()))

    def test_tool_functions_are_callable(self, tmp_path):
        make_harness(tmp_path)
        for name in _EXPECTED_TOOLS:
            assert callable(_TOOL_REGISTRY[name]["function"])


# ---------------------------------------------------------------------------
# Tests: error paths when faiss/code_index unavailable
# ---------------------------------------------------------------------------


class TestToolsWhenCodeIndexUnavailable:
    def test_index_codebase_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.tools.code_index_tools._CODE_INDEX_AVAILABLE", False):
            make_harness(tmp_path)
            fn = _TOOL_REGISTRY["index_codebase"]["function"]
            result = json.loads(fn())
            assert "error" in result

    def test_search_code_index_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.tools.code_index_tools._CODE_INDEX_AVAILABLE", False):
            make_harness(tmp_path)
            fn = _TOOL_REGISTRY["search_code_index"]["function"]
            result = json.loads(fn(query="test"))
            assert "error" in result

    def test_error_message_includes_install_hint(self, tmp_path):
        with patch("gaia.agents.tools.code_index_tools._CODE_INDEX_AVAILABLE", False):
            make_harness(tmp_path)
            fn = _TOOL_REGISTRY["index_codebase"]["function"]
            result = json.loads(fn())
            assert "pip install -e '.[rag]'" in result["error"]


# ---------------------------------------------------------------------------
# Tests: registry exposure
# ---------------------------------------------------------------------------


class TestRegistryEntry:
    def test_code_index_registered_in_known_tools(self):
        from gaia.agents.registry import KNOWN_TOOLS

        assert "code_index" in KNOWN_TOOLS
        module_path, class_name = KNOWN_TOOLS["code_index"]
        assert class_name == "CodeIndexToolsMixin"
        assert module_path == "gaia.agents.tools.code_index_tools"


# ---------------------------------------------------------------------------
# Tests: index_codebase's repo_path traversal guard
#
# index_codebase(repo_path=...) lets the LLM redirect the tool to any
# subdirectory it names, so the guard confining that redirect to the agent's
# original root is a real security boundary, not just input validation. It
# must survive `..`, an absolute path elsewhere, AND a symlink/junction
# planted *inside* the allowed root that resolves to somewhere outside it —
# the last one is easy to get wrong because CodeIndexSDK.__init__ calls
# Path(repo_path).resolve(), which follows the link and would silently
# re-root the whole SDK (and its own PathValidator) onto the link's target
# if the guard compared unresolved strings.
# ---------------------------------------------------------------------------


def _make_link(link_path: str, target_path: str) -> bool:
    """Best-effort symlink/junction creation. Returns False if unsupported
    in this environment (e.g. no privilege on Windows without Dev Mode) so
    the caller can skip rather than fail on an unrelated platform limitation.
    """
    try:
        os.symlink(target_path, link_path, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        import subprocess

        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", link_path, target_path],
                capture_output=True,
                check=True,
            )
            return True
        except (subprocess.CalledProcessError, OSError):
            return False
    return False


class TestTraversalGuard:
    def test_within_root_is_allowed(self, tmp_path):
        root = tmp_path / "repo"
        sub = root / "sub"
        sub.mkdir(parents=True)
        h = make_harness(root)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            result = json.loads(fn(repo_path=str(sub)))

        # Reaches the "SDK not initialised" branch — proves it passed the
        # guard rather than being rejected as a traversal attempt.
        assert result == {"error": "code_index SDK not initialised"}
        assert h._repo_path == os.path.abspath(str(sub))

    def test_dotdot_traversal_is_blocked(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        h = make_harness(root)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        escape = str(root / ".." / "outside")
        result = json.loads(fn(repo_path=escape))

        assert "error" in result
        assert "must be within" in result["error"]

    def test_absolute_path_elsewhere_is_blocked(self, tmp_path):
        root = tmp_path / "repo"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        h = make_harness(root)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        result = json.loads(fn(repo_path=str(outside)))

        assert "error" in result
        assert "must be within" in result["error"]

    def test_symlink_inside_root_escaping_outside_is_blocked(self, tmp_path):
        """A link planted *inside* the allowed root but resolving outside
        it must still be blocked — this is the case that a naive
        os.path.abspath (no symlink resolution) prefix check misses.
        """
        root = tmp_path / "repo"
        allowed_sub = root / "sub"
        allowed_sub.mkdir(parents=True)
        outside = tmp_path / "outside_secret"
        outside.mkdir()
        (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")

        link = allowed_sub / "escape_link"
        if not _make_link(str(link), str(outside)):
            pytest.skip("symlink/junction creation not permitted in this environment")

        h = make_harness(root)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]
        result = json.loads(fn(repo_path=str(link)))

        assert "error" in result
        assert "must be within" in result["error"]
        # The guard must reject *before* re-rooting mixin state onto the
        # escaped path.
        assert h._repo_path == os.path.abspath(str(root))


# ---------------------------------------------------------------------------
# One repository per session was the real limit (#3544)
# ---------------------------------------------------------------------------
#
# A successful index replaced the mixin's root with the repo just indexed, and
# the containment check validated against that. So the guard could only ever
# ratchet inward: index ~/projects/a, and ~/projects/b is then "outside the
# allowed area" for the rest of the session. `gaia chat` and the flagship hold
# a long-lived instance, so this was the normal case, not an edge one.


class TestASecondRepositoryCanStillBeIndexed:
    @staticmethod
    def _two_repos(tmp_path):
        ceiling = tmp_path / "projects"
        first = ceiling / "a"
        second = ceiling / "b"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        return ceiling, first, second

    def test_a_second_repo_under_the_ceiling_is_allowed(self, tmp_path):
        ceiling, first, second = self._two_repos(tmp_path)
        h = make_harness(ceiling)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            assert json.loads(fn(repo_path=str(first))) == {
                "error": "code_index SDK not initialised"
            }
            # Used to fail here with "repo_path must be within …/a".
            assert json.loads(fn(repo_path=str(second))) == {
                "error": "code_index SDK not initialised"
            }

        assert h._repo_path == os.path.abspath(str(second))

    def test_switching_back_to_the_first_repo_works_too(self, tmp_path):
        ceiling, first, second = self._two_repos(tmp_path)
        h = make_harness(ceiling)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            fn(repo_path=str(first))
            fn(repo_path=str(second))
            result = json.loads(fn(repo_path=str(first)))

        assert result == {"error": "code_index SDK not initialised"}
        assert h._repo_path == os.path.abspath(str(first))

    def test_the_ceiling_does_not_move_with_the_indexed_repo(self, tmp_path):
        ceiling, first, _ = self._two_repos(tmp_path)
        h = make_harness(ceiling)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            fn(repo_path=str(first))

        assert h._code_index_ceiling == os.path.abspath(str(ceiling))
        assert h._repo_path == os.path.abspath(str(first))

    def test_the_guard_still_holds_after_indexing(self, tmp_path):
        """Widening the check must not widen it past the sandbox."""
        ceiling, first, _ = self._two_repos(tmp_path)
        outside = tmp_path / "somewhere-else"
        outside.mkdir()
        h = make_harness(ceiling)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            fn(repo_path=str(first))
            result = json.loads(fn(repo_path=str(outside)))

        assert "must be within" in result["error"]
        assert str(ceiling) in result["error"]

    def test_a_sibling_of_the_indexed_repo_is_still_refused_above_the_ceiling(
        self, tmp_path
    ):
        """The ceiling is the sandbox, not the repo's parent."""
        _, first, _ = self._two_repos(tmp_path)
        sibling_of_ceiling = tmp_path / "other-tree"
        sibling_of_ceiling.mkdir()
        h = make_harness(first)  # ceiling IS the repo here
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        result = json.loads(fn(repo_path=str(sibling_of_ceiling)))

        assert "must be within" in result["error"]

    def test_switching_repos_drops_the_previous_sdk(self, tmp_path):
        """The next search must not answer from the previous repo's index."""
        ceiling, first, second = self._two_repos(tmp_path)
        h = make_harness(ceiling)
        fn = _TOOL_REGISTRY["index_codebase"]["function"]

        with patch.object(h, "_get_code_index_sdk", return_value=None):
            fn(repo_path=str(first))
            h._code_index_sdk = object()  # as a real index would leave it
            fn(repo_path=str(second))

        assert h._code_index_sdk is None
