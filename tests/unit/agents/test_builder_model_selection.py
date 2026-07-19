# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing tests (TDD red phase) for issue #2243 — BuilderAgent model selection.

BuilderAgent currently hardcodes ``model_id = "Qwen3.5-35B-A3B-GGUF"`` and fails
outright on any machine that hasn't installed that specific 35B model. These
tests describe the target contract:

  - ``_select_builder_model(base_url)`` (new, module-level in
    ``gaia.agents.builder.agent``) picks the first entry of
    ``BUILDER_PREFERRED_MODELS`` that is actually installed on the user's
    Lemonade server (per ``get_lemonade_models``), or raises a typed,
    actionable ``LemonadeError``.
  - ``BuilderAgent.__init__`` only calls ``_select_builder_model`` when the
    caller did not pin an explicit ``model_id`` — an explicit model_id is
    never second-guessed by a live network check.

This file is intentionally written against the *interface contract* in the
issue plan, not against the current implementation. Every test here is
expected to fail red until the fix lands (missing names -> ImportError /
AttributeError, not assertion failures).
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.builder.agent import (  # noqa: F401  (import itself is part of the red-phase check)
    BuilderAgent,
    BuilderAgentConfig,
    _select_builder_model,
)
from gaia.agents.registry import BUILDER_PREFERRED_MODELS
from gaia.llm.providers.lemonade import LemonadeError, LemonadeNetworkError

_BASE_URL = "http://localhost:13305/api/v1"

# The patch target is the NAME AS IMPORTED into builder/agent.py, following
# this repo's "patch where it's used" convention for non-deferred imports
# (builder/agent.py is a plain module, unlike agents/base/agent.py's
# deliberately-deferred Lemonade imports).
_GET_MODELS_PATCH_TARGET = "gaia.agents.builder.agent.get_lemonade_models"


def _make_agent(config: BuilderAgentConfig, tmp_path) -> BuilderAgent:
    with patch("os.path.expanduser", return_value=str(tmp_path)):
        return BuilderAgent(config)


class TestSelectBuilderModelFunction:
    """Direct unit tests of ``_select_builder_model`` in isolation."""

    def test_returns_first_available_preferred_model(self):
        with patch(
            _GET_MODELS_PATCH_TARGET,
            return_value=["gemma4-it-e2b-FLM"],
        ) as mock_get:
            result = _select_builder_model(_BASE_URL)
        assert result == "gemma4-it-e2b-FLM"
        mock_get.assert_called_once_with(_BASE_URL)

    def test_prefers_gemma_even_when_the_35b_is_installed(self):
        """Gemma wins so the builder lands on the same model as every other
        agent. Preferring the 35B here would evict and cold-reload the resident
        model on exactly the machines that still have it installed."""
        with patch(
            _GET_MODELS_PATCH_TARGET,
            return_value=["Qwen3.5-35B-A3B-GGUF", "Gemma-4-E4B-it-GGUF"],
        ):
            result = _select_builder_model(_BASE_URL)
        assert result == "Gemma-4-E4B-it-GGUF"

    def test_falls_back_to_the_35b_when_it_is_the_only_option(self):
        """The 35B stays last rather than removed, so an existing install that
        has nothing else still works."""
        with patch(
            _GET_MODELS_PATCH_TARGET,
            return_value=["Qwen3.5-35B-A3B-GGUF"],
        ):
            result = _select_builder_model(_BASE_URL)
        assert result == "Qwen3.5-35B-A3B-GGUF"

    def test_never_selects_a_model_absent_from_the_server_list(self):
        """Only ever return an id that was actually reported as installed."""
        available = ["gemma4-it-e2b-FLM"]
        with patch(_GET_MODELS_PATCH_TARGET, return_value=available):
            result = _select_builder_model(_BASE_URL)
        assert result in available
        assert result != "Qwen3.5-35B-A3B-GGUF"

    def test_unreachable_lemonade_raises_network_error(self):
        """``get_lemonade_models`` returning None means unreachable, not empty."""
        with patch(_GET_MODELS_PATCH_TARGET, return_value=None):
            with pytest.raises(LemonadeNetworkError):
                _select_builder_model(_BASE_URL)

    def test_no_usable_model_installed_raises_actionable_error(self):
        """Reachable server, zero usable models -> distinct, actionable error."""
        with patch(_GET_MODELS_PATCH_TARGET, return_value=[]):
            with pytest.raises(LemonadeError) as excinfo:
                _select_builder_model(_BASE_URL)
        message = excinfo.value.user_message
        # Must name a concrete candidate model so the user knows what to install.
        assert any(
            model in message for model in BUILDER_PREFERRED_MODELS
        ), f"expected a concrete candidate model name in the error, got: {message!r}"
        # Must give a remediation command.
        assert (
            "gaia download" in message or "gaia init" in message
        ), f"expected a remediation command in the error, got: {message!r}"

    def test_unreachable_and_no_models_errors_are_distinct(self):
        """The 'can't tell' case and the 'nothing installed' case must not share text."""
        with patch(_GET_MODELS_PATCH_TARGET, return_value=None):
            with pytest.raises(LemonadeNetworkError) as unreachable_exc:
                _select_builder_model(_BASE_URL)

        with patch(_GET_MODELS_PATCH_TARGET, return_value=[]):
            with pytest.raises(LemonadeError) as empty_exc:
                _select_builder_model(_BASE_URL)

        assert unreachable_exc.value.user_message != empty_exc.value.user_message

    def test_unreachable_error_does_not_claim_not_installed(self):
        """The unreachable-server message must be connectivity-flavored, not
        imply the model is missing (that's a different, misleading claim)."""
        with patch(_GET_MODELS_PATCH_TARGET, return_value=None):
            with pytest.raises(LemonadeNetworkError) as excinfo:
                _select_builder_model(_BASE_URL)
        assert "not installed" not in excinfo.value.user_message.lower()

    def test_error_never_claims_quality_is_reduced(self):
        """No 'quality may be reduced' FUD — rejected in the accepted plan."""
        with patch(_GET_MODELS_PATCH_TARGET, return_value=[]):
            with pytest.raises(LemonadeError) as excinfo:
                _select_builder_model(_BASE_URL)
        message = excinfo.value.user_message.lower()
        assert "quality" not in message


class TestBuilderAgentModelSelectionOnConstruction:
    """End-to-end: constructing BuilderAgent must reflect selection outcomes."""

    def test_construction_resolves_to_installed_model_when_35b_absent(self, tmp_path):
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id=None)
        with patch(
            _GET_MODELS_PATCH_TARGET,
            return_value=["gemma4-it-e2b-FLM"],
        ):
            agent = _make_agent(config, tmp_path)
        assert agent.model_id == "gemma4-it-e2b-FLM"
        assert agent.model_id != "Qwen3.5-35B-A3B-GGUF"

    def test_construction_prefers_gemma_when_available(self, tmp_path):
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id=None)
        with patch(
            _GET_MODELS_PATCH_TARGET,
            return_value=["Qwen3.5-35B-A3B-GGUF", "Gemma-4-E4B-it-GGUF"],
        ):
            agent = _make_agent(config, tmp_path)
        assert agent.model_id == "Gemma-4-E4B-it-GGUF"

    def test_construction_raises_when_nothing_usable_installed(self, tmp_path):
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id=None)
        with patch(_GET_MODELS_PATCH_TARGET, return_value=[]):
            with pytest.raises(LemonadeError) as excinfo:
                _make_agent(config, tmp_path)
        message = excinfo.value.user_message
        assert message, "error must carry an actionable, non-empty message"
        assert any(model in message for model in BUILDER_PREFERRED_MODELS)

    def test_construction_raises_network_error_when_unreachable(self, tmp_path):
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id=None)
        with patch(_GET_MODELS_PATCH_TARGET, return_value=None):
            with pytest.raises(LemonadeNetworkError):
                _make_agent(config, tmp_path)

    def test_explicit_model_id_bypasses_live_model_check_entirely(self, tmp_path):
        """An explicit model_id must never trigger the live Lemonade lookup."""
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id="My-Pinned-Model")
        with patch(_GET_MODELS_PATCH_TARGET) as mock_get:
            agent = _make_agent(config, tmp_path)
        mock_get.assert_not_called()
        assert agent.model_id == "My-Pinned-Model"

    def test_explicit_model_id_bypass_even_when_server_unreachable(self, tmp_path):
        """Pinning a model_id must succeed even if Lemonade can't be reached —
        the explicit choice is never second-guessed by a live network check."""
        config = BuilderAgentConfig(base_url=_BASE_URL, model_id="My-Pinned-Model")
        with patch(_GET_MODELS_PATCH_TARGET, return_value=None) as mock_get:
            agent = _make_agent(config, tmp_path)
        mock_get.assert_not_called()
        assert agent.model_id == "My-Pinned-Model"


class TestGetLemonadeModelsRequestShape:
    """Assert the SHAPE of the outgoing HTTP call, not merely that it happened.

    Per the repo's mocking rule: a stub proving invocation is not enough —
    at least one test must assert on the actual URL/args passed to the
    underlying HTTP call.
    """

    def test_get_lemonade_models_requests_the_models_endpoint(self):
        from gaia.agents.registry import get_lemonade_models

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"id": "gemma4-it-e2b-FLM"}, {"id": "Qwen3.5-35B-A3B-GGUF"}]
        }

        with patch("requests.get", return_value=mock_response) as mock_requests_get:
            result = get_lemonade_models(_BASE_URL)

        mock_requests_get.assert_called_once()
        called_url = mock_requests_get.call_args[0][0]
        assert (
            called_url == f"{_BASE_URL}/models"
        ), f"expected the /models endpoint, got: {called_url!r}"
        assert result == ["gemma4-it-e2b-FLM", "Qwen3.5-35B-A3B-GGUF"]

    def test_get_lemonade_models_returns_none_on_connection_error(self):
        import requests

        from gaia.agents.registry import get_lemonade_models

        with patch("requests.get", side_effect=requests.exceptions.ConnectionError()):
            result = get_lemonade_models(_BASE_URL)
        assert result is None

    def test_get_lemonade_models_returns_none_on_non_2xx(self):
        from gaia.agents.registry import get_lemonade_models

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"data": []}

        with patch("requests.get", return_value=mock_response):
            result = get_lemonade_models(_BASE_URL)
        assert result is None

    def test_get_lemonade_models_empty_list_is_not_none(self):
        """Reachable server, zero models installed -> [] not None. These are
        semantically different and callers must not conflate them."""
        from gaia.agents.registry import get_lemonade_models

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        with patch("requests.get", return_value=mock_response):
            result = get_lemonade_models(_BASE_URL)
        assert result == []
        assert result is not None
