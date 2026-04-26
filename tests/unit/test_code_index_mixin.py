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
    from gaia.agents.code_index.tools.mixin import CodeIndexToolsMixin

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
        with patch("gaia.agents.code_index.tools.mixin._CODE_INDEX_AVAILABLE", False):
            make_harness(tmp_path)
            fn = _TOOL_REGISTRY["index_codebase"]["function"]
            result = json.loads(fn())
            assert "error" in result

    def test_search_code_index_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.code_index.tools.mixin._CODE_INDEX_AVAILABLE", False):
            make_harness(tmp_path)
            fn = _TOOL_REGISTRY["search_code_index"]["function"]
            result = json.loads(fn(query="test"))
            assert "error" in result

    def test_error_message_includes_install_hint(self, tmp_path):
        with patch("gaia.agents.code_index.tools.mixin._CODE_INDEX_AVAILABLE", False):
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
        assert module_path.endswith("code_index.tools.mixin")
