# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for CodeIndexAgent.

Tests cover: instantiation, tool registration, error paths when faiss is
unavailable, system prompt content, and _load_embedder behaviour.
All LLM and Lemonade dependencies are mocked.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY


@pytest.fixture(autouse=True)
def clean_tool_registry():
    """Clear the global tool registry before and after each test."""
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Agent availability guard
# ---------------------------------------------------------------------------

try:
    from gaia.agents.code_index.agent import CodeIndexAgent

    AGENT_AVAILABLE = True
except ImportError:
    AGENT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not AGENT_AVAILABLE, reason="CodeIndexAgent not importable"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_KWARGS = dict(skip_lemonade=True, silent_mode=True)
_EXPECTED_TOOLS = {
    "index_codebase",
    "search_code_index",
    "code_index_status",
    "clear_code_index",
    "search_git_history",
}


def make_agent(tmp_path=None, **kwargs):
    """Instantiate CodeIndexAgent with safe test defaults."""
    base = dict(_AGENT_KWARGS)
    base.update(kwargs)
    repo = str(tmp_path) if tmp_path else "."
    return CodeIndexAgent(repo_path=repo, **base)


# ---------------------------------------------------------------------------
# Tests: instantiation
# ---------------------------------------------------------------------------


class TestCodeIndexAgentInstantiation:
    def test_instantiates_without_error(self, tmp_path):
        agent = make_agent(tmp_path)
        assert agent is not None

    def test_repo_path_is_absolute(self, tmp_path):
        agent = make_agent(tmp_path)
        import os

        assert os.path.isabs(agent._repo_path)

    def test_sdk_not_initialised_at_construction(self, tmp_path):
        agent = make_agent(tmp_path)
        assert agent._code_index_sdk is None


# ---------------------------------------------------------------------------
# Tests: tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    def test_all_five_tools_registered(self, tmp_path):
        make_agent(tmp_path)
        assert _EXPECTED_TOOLS.issubset(set(_TOOL_REGISTRY.keys()))

    def test_tool_functions_are_callable(self, tmp_path):
        make_agent(tmp_path)
        for name in _EXPECTED_TOOLS:
            assert callable(_TOOL_REGISTRY[name]["function"])


# ---------------------------------------------------------------------------
# Tests: system prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_prompt_mentions_tool_names(self, tmp_path):
        agent = make_agent(tmp_path)
        prompt = agent._get_system_prompt()
        assert "index_codebase" in prompt
        assert "search_code_index" in prompt

    def test_prompt_gives_workflow_guidance(self, tmp_path):
        agent = make_agent(tmp_path)
        prompt = agent._get_system_prompt()
        assert "index" in prompt.lower()


# ---------------------------------------------------------------------------
# Tests: error paths when faiss/code_index unavailable
# ---------------------------------------------------------------------------


class TestToolsWhenCodeIndexUnavailable:
    def test_index_codebase_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.code_index.agent._CODE_INDEX_AVAILABLE", False):
            agent = make_agent(tmp_path)
            fn = _TOOL_REGISTRY["index_codebase"]["function"]
            result = json.loads(fn())
            assert "error" in result

    def test_search_code_index_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.code_index.agent._CODE_INDEX_AVAILABLE", False):
            agent = make_agent(tmp_path)
            fn = _TOOL_REGISTRY["search_code_index"]["function"]
            result = json.loads(fn(query="test"))
            assert "error" in result

    def test_search_git_history_returns_error_json_when_unavailable(self, tmp_path):
        with patch("gaia.agents.code_index.agent._CODE_INDEX_AVAILABLE", False):
            agent = make_agent(tmp_path)
            fn = _TOOL_REGISTRY["search_git_history"]["function"]
            result = json.loads(fn(query="test"))
            assert "error" in result


# ---------------------------------------------------------------------------
# Tests: _load_embedder behaviour (via SDK)
# ---------------------------------------------------------------------------


class TestLoadEmbedder:
    """Verify the updated _load_embedder logic in CodeIndexSDK:
    - Does NOT call unload_model()
    - Calls load_model() only when embedding model is not already loaded
    - Preserves llamacpp_args="--ubatch-size 2048"
    """

    def _make_sdk(self, tmp_path, already_loaded=False):
        """Create a CodeIndexSDK with a mocked LemonadeClient."""
        from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
        )
        sdk = CodeIndexSDK(config)

        mock_client = MagicMock()
        mock_status = MagicMock()
        if already_loaded:
            mock_status.loaded_models = [{"id": config.embedding_model}]
        else:
            mock_status.loaded_models = []
        mock_client.get_status.return_value = mock_status
        sdk._llm_client = mock_client

        return sdk, mock_client, config

    def test_load_model_called_when_not_loaded(self, tmp_path):
        try:
            sdk, mock_client, config = self._make_sdk(tmp_path, already_loaded=False)
        except ImportError:
            pytest.skip("faiss not installed")

        sdk._load_embedder()

        mock_client.load_model.assert_called_once_with(
            config.embedding_model,
            llamacpp_args="--ubatch-size 2048",
        )

    def test_load_model_skipped_when_already_loaded(self, tmp_path):
        try:
            sdk, mock_client, config = self._make_sdk(tmp_path, already_loaded=True)
        except ImportError:
            pytest.skip("faiss not installed")

        sdk._load_embedder()

        mock_client.load_model.assert_not_called()

    def test_unload_model_never_called(self, tmp_path):
        try:
            sdk, mock_client, config = self._make_sdk(tmp_path, already_loaded=False)
        except ImportError:
            pytest.skip("faiss not installed")

        sdk._load_embedder()

        mock_client.unload_model.assert_not_called()

    def test_embedder_set_even_on_load_failure(self, tmp_path):
        try:
            sdk, mock_client, config = self._make_sdk(tmp_path, already_loaded=False)
        except ImportError:
            pytest.skip("faiss not installed")

        mock_client.load_model.side_effect = RuntimeError("server unreachable")
        sdk._load_embedder()

        # _embedder should still be set (for graceful degradation)
        assert sdk._embedder is not None
