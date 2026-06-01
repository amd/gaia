# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for llama.cpp backend integration paths.

The LemonadeClient wraps a local Lemonade Server that in turn manages a
llama-server child process. Several code paths deal specifically with
llama.cpp parameters (``llamacpp_args``, ``ctx_size``), model-load HTTP
request construction, context-size validation, and llama.cpp-specific
error shapes. These tests cover those paths with mocked HTTP calls --
no running Lemonade Server required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from gaia.llm.lemonade_client import (
    DEFAULT_CONTEXT_SIZE,
    MODELS,
    LemonadeClient,
    LemonadeStatus,
    ModelRequirement,
    ModelType,
)
from gaia.llm.providers.lemonade import (
    LemonadeContextOverflowError,
    LemonadeError,
    LemonadeModelNotLoadedError,
    LemonadeNetworkError,
    LemonadeProvider,
    LemonadeUpstreamTimeoutError,
    _classify_lemonade_response,
)

# ── load_model: llamacpp_args & ctx_size request construction ─────────


class TestLoadModelRequestConstruction:
    """Verify that load_model builds the correct HTTP payload for /load."""

    @patch.object(LemonadeClient, "_send_request")
    def test_basic_load_sends_model_name_only(self, mock_send):
        """Minimal load_model call sends only model_name in the payload."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model("Gemma-4-E4B-it-GGUF")

        mock_send.assert_called_once()
        _method, _url, payload = mock_send.call_args[0][:3]
        assert payload["model_name"] == "Gemma-4-E4B-it-GGUF"
        assert "llamacpp_args" not in payload
        assert "ctx_size" not in payload
        assert "save_options" not in payload

    @patch.object(LemonadeClient, "_send_request")
    def test_llamacpp_args_included_in_payload(self, mock_send):
        """llamacpp_args string is forwarded verbatim to the /load request."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model(
            "nomic-embed-text-v2-moe-GGUF",
            llamacpp_args="--ubatch-size 2048 --split-mode none",
        )

        payload = mock_send.call_args[0][2]
        assert payload["llamacpp_args"] == "--ubatch-size 2048 --split-mode none"

    @patch.object(LemonadeClient, "_send_request")
    def test_ctx_size_included_in_payload(self, mock_send):
        """Explicit ctx_size is forwarded as an integer in the payload."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model("Gemma-4-E4B-it-GGUF", ctx_size=65536)

        payload = mock_send.call_args[0][2]
        assert payload["ctx_size"] == 65536

    @patch.object(LemonadeClient, "_send_request")
    def test_ctx_size_zero_is_included(self, mock_send):
        """ctx_size=0 is explicitly included (not treated as falsy/missing)."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model("Qwen3-0.6B-GGUF", ctx_size=0)

        payload = mock_send.call_args[0][2]
        assert "ctx_size" in payload
        assert payload["ctx_size"] == 0

    @patch.object(LemonadeClient, "_send_request")
    def test_save_options_included_when_true(self, mock_send):
        """save_options=True persists ctx_size and llamacpp_args to config."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model(
            "Gemma-4-E4B-it-GGUF",
            ctx_size=65536,
            llamacpp_args="--ubatch-size 2048",
            save_options=True,
        )

        payload = mock_send.call_args[0][2]
        assert payload["save_options"] is True
        assert payload["ctx_size"] == 65536
        assert payload["llamacpp_args"] == "--ubatch-size 2048"

    @patch.object(LemonadeClient, "_send_request")
    def test_full_payload_with_all_params(self, mock_send):
        """All optional params present in a single load_model call."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model(
            "nomic-embed-text-v2-moe-GGUF",
            llamacpp_args="--ubatch-size 2048",
            ctx_size=2048,
            save_options=True,
        )

        payload = mock_send.call_args[0][2]
        assert payload == {
            "model_name": "nomic-embed-text-v2-moe-GGUF",
            "llamacpp_args": "--ubatch-size 2048",
            "ctx_size": 2048,
            "save_options": True,
        }

    @patch.object(LemonadeClient, "_send_request")
    def test_load_endpoint_url(self, mock_send):
        """load_model posts to {base_url}/load."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)

        client.load_model("Gemma-4-E4B-it-GGUF")

        url = mock_send.call_args[0][1]
        assert url.endswith("/load")
        assert "13305" in url

    @patch.object(LemonadeClient, "_send_request")
    def test_load_updates_client_model_attribute(self, mock_send):
        """Successful load_model updates client.model to the loaded model."""
        mock_send.return_value = {"status": "ok"}
        client = LemonadeClient(host="localhost", port=13305)
        assert client.model != "new-model"

        client.load_model("new-model")

        assert client.model == "new-model"


# ── _ensure_model_loaded: ctx_size resolution from MODELS registry ────


class TestEnsureModelLoadedCtxResolution:
    """Verify _ensure_model_loaded resolves ctx_size correctly from the
    MODELS registry and falls back to DEFAULT_CONTEXT_SIZE for unknowns.
    """

    @pytest.fixture(autouse=True)
    def _stub_list_models(self):
        with patch.object(LemonadeClient, "list_models", return_value={"data": []}):
            yield

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_gemma_4_e4b_loads_at_65536(self, mock_load, mock_status):
        """Gemma 4 E4B is registered with min_ctx_size=65536 for doc-Q&A."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True, loaded_models=[{"id": "other-model"}]
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "Gemma-4-E4B-it-GGUF", auto_download=True, prompt=False, ctx_size=65536
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_qwen3_0_6b_loads_at_4096(self, mock_load, mock_status):
        """Qwen3 0.6B is the smallest model -- 4096 ctx is enough."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(running=True, loaded_models=[])

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_unknown_model_falls_back_to_default_ctx(self, mock_load, mock_status):
        """Models not in MODELS registry get DEFAULT_CONTEXT_SIZE (32K)."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(running=True, loaded_models=[])

        client._ensure_model_loaded("my-custom-model-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "my-custom-model-GGUF",
            auto_download=True,
            prompt=False,
            ctx_size=DEFAULT_CONTEXT_SIZE,
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_reload_when_loaded_at_undersized_ctx(self, mock_load, mock_status):
        """Model loaded at 4096 when GAIA expects 65536 triggers a reload.

        This is the #1030 follow-up regression: Lemonade auto-loads Gemma
        at its default 4K ctx after embedder warm-up unloads the chat model,
        bypassing MODELS[...].min_ctx_size.
        """
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 4096},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        # Must reload at the registry ctx_size (65536), not skip.
        mock_load.assert_called_once_with(
            "Gemma-4-E4B-it-GGUF", auto_download=True, prompt=False, ctx_size=65536
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_reload_when_loaded_at_sufficient_ctx(self, mock_load, mock_status):
        """Model loaded at exactly the expected ctx_size is not reloaded."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 65536},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_reload_when_loaded_above_expected_ctx(self, mock_load, mock_status):
        """Model loaded at 128K when only 65K is expected is fine (over-provisioned)."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 131072},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_model_name_match_via_model_name_field(self, mock_load, mock_status):
        """Loaded models can be matched via 'model_name' field (not just 'id')."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True,
            loaded_models=[
                {
                    "model_name": "Qwen3-0.6B-GGUF",
                    "recipe_options": {"ctx_size": 4096},
                }
            ],
        )

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_missing_recipe_options_triggers_reload(self, mock_load, mock_status):
        """Loaded entry without recipe_options has ctx=0 which is under-sized."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            running=True,
            loaded_models=[{"id": "Qwen3-0.6B-GGUF"}],  # no recipe_options
        )

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        # ctx=0 < 4096 expected, so reload fires.
        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )


# ── launch_server: ctx_size CLI flag ──────────────────────────────────


class TestLaunchServerCtxSize:
    """Verify launch_server passes --ctx-size to the lemonade-server command."""

    @patch("builtins.open", MagicMock())
    @patch("gaia.llm.lemonade_client.kill_process_on_port")
    @patch("subprocess.Popen")
    @patch("socket.create_connection")
    @patch("time.sleep")
    def test_ctx_size_appended_to_command(
        self, _sleep, mock_conn, mock_popen, mock_kill
    ):
        mock_conn.return_value = MagicMock()
        mock_popen.return_value = MagicMock(pid=12345)
        client = LemonadeClient(host="localhost", port=13305)

        client.launch_server(ctx_size=65536, background="silent")

        cmd = mock_popen.call_args[0][0]
        assert "--ctx-size" in cmd
        assert "65536" in cmd

    @patch("builtins.open", MagicMock())
    @patch("gaia.llm.lemonade_client.kill_process_on_port")
    @patch("subprocess.Popen")
    @patch("socket.create_connection")
    @patch("time.sleep")
    def test_no_ctx_size_flag_when_none(self, _sleep, mock_conn, mock_popen, mock_kill):
        mock_conn.return_value = MagicMock()
        mock_popen.return_value = MagicMock(pid=12345)
        client = LemonadeClient(host="localhost", port=13305)

        client.launch_server(background="silent")

        cmd = mock_popen.call_args[0][0]
        assert "--ctx-size" not in cmd


# ── OpenAI-standard vs llama.cpp-native param separation ──────────────


class TestParamSeparation:
    """Verify that _stream_chat_completions_with_openai splits OpenAI-
    standard params from llama.cpp-native ones (extra_body).
    """

    @patch.object(LemonadeClient, "_ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_repeat_penalty_goes_to_extra_body(self, mock_openai_cls, mock_ensure):
        """llama.cpp-native repeat_penalty must not be a top-level kwarg."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        mock_chunk = Mock()
        mock_chunk.id = "c1"
        mock_chunk.created = 1
        mock_chunk.model = "m"
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.delta = Mock(role="assistant", content="hi", reasoning_content=None)
        mock_choice.finish_reason = None
        mock_chunk.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = iter([mock_chunk])

        list(
            client._stream_chat_completions_with_openai(
                model="test",
                messages=[{"role": "user", "content": "x"}],
                repeat_penalty=1.2,
                repeat_last_n=256,
                frequency_penalty=0.3,
            )
        )

        call_kwargs = mock_instance.chat.completions.create.call_args[1]
        # frequency_penalty is OpenAI-standard -> top-level
        assert call_kwargs.get("frequency_penalty") == 0.3
        # repeat_penalty/repeat_last_n are llama.cpp-native -> extra_body
        extra = call_kwargs.get("extra_body", {})
        assert extra.get("repeat_penalty") == 1.2
        assert extra.get("repeat_last_n") == 256

    @patch.object(LemonadeClient, "_ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_openai_standard_params_stay_top_level(self, mock_openai_cls, mock_ensure):
        """OpenAI-standard params (presence_penalty, top_p, seed) stay top-level."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        mock_chunk = Mock()
        mock_chunk.id = "c1"
        mock_chunk.created = 1
        mock_chunk.model = "m"
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.delta = Mock(role="assistant", content="ok", reasoning_content=None)
        mock_choice.finish_reason = None
        mock_chunk.choices = [mock_choice]
        mock_instance.chat.completions.create.return_value = iter([mock_chunk])

        list(
            client._stream_chat_completions_with_openai(
                model="test",
                messages=[{"role": "user", "content": "x"}],
                presence_penalty=0.5,
                top_p=0.9,
                seed=42,
            )
        )

        call_kwargs = mock_instance.chat.completions.create.call_args[1]
        assert call_kwargs.get("presence_penalty") == 0.5
        assert call_kwargs.get("top_p") == 0.9
        assert call_kwargs.get("seed") == 42
        # These must NOT be in extra_body
        extra = call_kwargs.get("extra_body", {})
        assert "presence_penalty" not in extra
        assert "top_p" not in extra
        assert "seed" not in extra


# ── Error classification: llama.cpp-specific failures ─────────────────


class TestContextOverflowClassification:
    """Verify _classify_lemonade_response handles ctx-exceeded errors
    from llama-server, including the dynamic retryable logic.
    """

    def test_exceed_context_size_type(self):
        """Top-level type=exceed_context_size is recognised."""
        payload = {
            "error": {
                "type": "exceed_context_size",
                "message": "request (42000 tokens) exceeds the available context size (4096 tokens)",
                "n_ctx": 4096,
            }
        }
        err, is_err = _classify_lemonade_response(payload)
        assert is_err is True
        assert isinstance(err, LemonadeContextOverflowError)

    def test_context_overflow_retryable_when_n_ctx_small(self):
        """When n_ctx < 65536, model was loaded at wrong size -- retryable."""
        payload = {
            "error": {
                "type": "exceed_context_size",
                "message": "exceeds the available context size",
                "n_ctx": 4096,
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeContextOverflowError)
        assert err.retryable is True

    def test_context_overflow_not_retryable_at_full_ctx(self):
        """When n_ctx >= 65536, conversation is genuinely too big -- not retryable."""
        payload = {
            "error": {
                "type": "exceed_context_size",
                "message": "exceeds the available context size",
                "n_ctx": 65536,
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeContextOverflowError)
        assert err.retryable is False

    def test_context_overflow_not_retryable_when_n_ctx_zero(self):
        """n_ctx=0 (not reported) defaults to non-retryable."""
        payload = {
            "error": {
                "type": "exceed_context_size",
                "message": "exceeds the available context size",
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeContextOverflowError)
        assert err.retryable is False

    def test_nested_context_overflow_from_llama_server(self):
        """Nested error under details.response.error is also classified."""
        payload = {
            "error": {
                "type": "backend_error",
                "message": "upstream model returned an error",
                "details": {
                    "response": {
                        "error": {
                            "type": "exceed_context_size",
                            "message": "request exceeds the available context size",
                            "n_ctx": 8192,
                        }
                    }
                },
            }
        }
        err, is_err = _classify_lemonade_response(payload)
        assert is_err is True
        assert isinstance(err, LemonadeContextOverflowError)
        # 8192 < 65536 -> retryable
        assert err.retryable is True

    def test_nested_n_ctx_used_when_top_level_missing(self):
        """n_ctx from the nested llama-server error is picked up."""
        payload = {
            "error": {
                "type": "backend_error",
                "message": "",
                "details": {
                    "response": {
                        "error": {
                            "type": "exceed_context_size",
                            "message": "exceeds context",
                            "n_ctx": 32768,
                        }
                    }
                },
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeContextOverflowError)
        # 32768 < 65536 -> retryable
        assert err.retryable is True

    def test_context_overflow_by_message_string(self):
        """Detection works via message substring even without type field."""
        payload = {
            "error": {
                "type": "unknown_error",
                "message": "The request (40000 tokens) exceeds the available context size (4096 tokens)",
                "n_ctx": 4096,
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeContextOverflowError)
        assert err.retryable is True


class TestModelNotLoadedClassification:
    """Verify model_not_loaded detection from llama-server."""

    def test_model_not_loaded_type(self):
        payload = {
            "error": {
                "type": "model_not_loaded",
                "message": "No model is currently loaded",
            }
        }
        err, is_err = _classify_lemonade_response(payload)
        assert is_err is True
        assert isinstance(err, LemonadeModelNotLoadedError)
        assert err.retryable is True

    def test_no_model_loaded_by_message(self):
        """Detection by message substring: 'no model loaded'."""
        payload = {
            "error": {
                "type": "server_error",
                "message": "no model loaded, please load a model first",
            }
        }
        err, _ = _classify_lemonade_response(payload)
        assert isinstance(err, LemonadeModelNotLoadedError)


class TestLlamaServerCorruptDownload:
    """Verify _is_corrupt_download_error catches llama-server startup failures."""

    def test_llama_server_failed_to_start(self):
        """'llama-server failed to start' indicates corrupt model files."""
        client = LemonadeClient(host="localhost", port=13305)
        error = Exception(
            "model_load_error: llama-server failed to start after loading model"
        )
        assert client._is_corrupt_download_error(error) is True

    def test_incomplete_download(self):
        client = LemonadeClient(host="localhost", port=13305)
        error = Exception("download validation failed: files are incomplete")
        assert client._is_corrupt_download_error(error) is True

    def test_unrelated_error_not_corrupt(self):
        client = LemonadeClient(host="localhost", port=13305)
        error = Exception("connection timeout after 30s")
        assert client._is_corrupt_download_error(error) is False


class TestUnrecognisedErrorFallback:
    """Errors with an envelope but no specific match fall to generic LemonadeError."""

    def test_unknown_error_type_returns_generic(self):
        payload = {
            "error": {
                "type": "some_exotic_failure",
                "message": "something completely unexpected happened",
            }
        }
        err, is_err = _classify_lemonade_response(payload)
        assert is_err is True
        assert isinstance(err, LemonadeError)
        assert not isinstance(err, LemonadeModelNotLoadedError)
        assert not isinstance(err, LemonadeContextOverflowError)
        assert not isinstance(err, LemonadeUpstreamTimeoutError)
        assert not isinstance(err, LemonadeNetworkError)

    def test_non_dict_response_returns_no_error(self):
        err, is_err = _classify_lemonade_response("not a dict")
        assert is_err is False
        assert err is None

    def test_no_error_key_returns_no_error(self):
        err, is_err = _classify_lemonade_response({"choices": [{"text": "hi"}]})
        assert is_err is False
        assert err is None

    def test_error_key_not_dict_returns_no_error(self):
        err, is_err = _classify_lemonade_response({"error": "just a string"})
        assert is_err is False
        assert err is None


# ── MODELS registry: structural invariants ────────────────────────────


class TestModelsRegistry:
    """Verify invariants the rest of the code relies on."""

    def test_all_entries_have_min_ctx_size(self):
        """Every model in MODELS must declare a min_ctx_size > 0."""
        for key, req in MODELS.items():
            assert isinstance(req, ModelRequirement), f"{key} is not a ModelRequirement"
            assert req.min_ctx_size > 0, f"{key} has min_ctx_size={req.min_ctx_size}"

    def test_gemma_4_e4b_ctx_is_65536(self):
        """Gemma 4 E4B's 64K ctx is a load-bearing constant for RAG/doc-Q&A."""
        req = MODELS["gemma-4-e4b"]
        assert req.min_ctx_size == 65536
        assert req.model_id == "Gemma-4-E4B-it-GGUF"

    def test_qwen3_0_6b_is_smallest(self):
        """Qwen3 0.6B at 4096 is the smallest registered model."""
        req = MODELS["qwen3-0.6b"]
        assert req.min_ctx_size == 4096
        for key, other in MODELS.items():
            if other.model_type == ModelType.LLM:
                assert (
                    other.min_ctx_size >= req.min_ctx_size
                ), f"{key} has smaller ctx than qwen3-0.6b"

    def test_default_context_size_value(self):
        """DEFAULT_CONTEXT_SIZE is 32768 -- the fallback for unknown models."""
        assert DEFAULT_CONTEXT_SIZE == 32768

    def test_embedding_model_has_tool_calling_false(self):
        """Embedding models must not claim tool_calling capability."""
        for key, req in MODELS.items():
            if req.model_type == ModelType.EMBEDDING:
                assert (
                    req.tool_calling is False
                ), f"Embedding model {key} has tool_calling=True"


# ── LemonadeProvider: repetition-penalty defaults via chat() ──────────


class TestLemonadeProviderRepetitionDefaults:
    """Verify the LemonadeProvider.chat() method sets llama.cpp repetition
    penalty defaults so they reach the backend.
    """

    def test_chat_sets_repeat_penalty_defaults(self):
        """chat() should inject repeat_penalty and repeat_last_n defaults."""
        provider = LemonadeProvider.__new__(LemonadeProvider)
        provider._backend = MagicMock(spec=LemonadeClient)
        provider._model = "Gemma-4-E4B-it-GGUF"
        provider._system_prompt = None

        # Return a valid non-streaming response
        provider._backend.chat_completions.return_value = {
            "choices": [
                {
                    "message": {"content": "Hello!"},
                    "finish_reason": "stop",
                }
            ]
        }

        provider.chat(
            [{"role": "user", "content": "hi"}],
            stream=False,
        )

        call_kwargs = provider._backend.chat_completions.call_args[1]
        assert call_kwargs["repeat_penalty"] == 1.1
        assert call_kwargs["repeat_last_n"] == 256
        assert call_kwargs["frequency_penalty"] == 0.3
        assert call_kwargs["presence_penalty"] == 0.1

    def test_chat_allows_override_of_repeat_penalty(self):
        """Caller-specified repeat_penalty overrides the default."""
        provider = LemonadeProvider.__new__(LemonadeProvider)
        provider._backend = MagicMock(spec=LemonadeClient)
        provider._model = "Gemma-4-E4B-it-GGUF"
        provider._system_prompt = None

        provider._backend.chat_completions.return_value = {
            "choices": [
                {
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }

        provider.chat(
            [{"role": "user", "content": "hi"}],
            stream=False,
            repeat_penalty=1.5,
        )

        call_kwargs = provider._backend.chat_completions.call_args[1]
        assert call_kwargs["repeat_penalty"] == 1.5


# ── LemonadeProvider: error classification integration ────────────────


class TestLemonadeProviderErrorRaising:
    """Verify LemonadeProvider.chat() raises typed errors for llama.cpp failures."""

    def test_context_overflow_raises_typed_error(self):
        """Non-streaming chat that returns ctx-overflow raises LemonadeContextOverflowError."""
        provider = LemonadeProvider.__new__(LemonadeProvider)
        provider._backend = MagicMock(spec=LemonadeClient)
        provider._model = "Gemma-4-E4B-it-GGUF"
        provider._system_prompt = None

        provider._backend.chat_completions.return_value = {
            "error": {
                "type": "exceed_context_size",
                "message": "request (50000) exceeds the available context size (4096)",
                "n_ctx": 4096,
            }
        }

        with pytest.raises(LemonadeContextOverflowError) as exc_info:
            provider.chat(
                [{"role": "user", "content": "hi"}],
                stream=False,
            )

        assert exc_info.value.retryable is True

    def test_model_not_loaded_raises_typed_error(self):
        provider = LemonadeProvider.__new__(LemonadeProvider)
        provider._backend = MagicMock(spec=LemonadeClient)
        provider._model = "Gemma-4-E4B-it-GGUF"
        provider._system_prompt = None

        provider._backend.chat_completions.return_value = {
            "error": {
                "type": "model_not_loaded",
                "message": "No model is currently loaded",
            }
        }

        with pytest.raises(LemonadeModelNotLoadedError):
            provider.chat(
                [{"role": "user", "content": "hi"}],
                stream=False,
            )

    def test_unexpected_response_shape_raises_generic(self):
        """A response with no 'choices' and no 'error' raises LemonadeError."""
        provider = LemonadeProvider.__new__(LemonadeProvider)
        provider._backend = MagicMock(spec=LemonadeClient)
        provider._model = "test"
        provider._system_prompt = None

        provider._backend.chat_completions.return_value = {"garbage": True}

        with pytest.raises(LemonadeError):
            provider.chat(
                [{"role": "user", "content": "hi"}],
                stream=False,
            )
