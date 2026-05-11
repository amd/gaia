# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for LemonadeClient model loading functionality."""

from unittest.mock import MagicMock, Mock, patch

from gaia.llm.lemonade_client import LemonadeClient, LemonadeStatus


class TestEnsureModelLoaded:
    """Test _ensure_model_loaded helper method."""

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_calls_load_when_model_not_loaded(self, mock_load, mock_status):
        """Verify load_model is called when model not in loaded_models list.

        Unknown models (not in MODELS registry) default to ctx_size=32768
        so ChatAgent's >7K-token system prompt isn't truncated by Lemonade's
        default 4096 ctx — the silent-empty-stream regression that blocked
        gaia-lite. See _chat_helpers._maybe_load_expected_model + the
        matching log line in lemonade_client._ensure_model_loaded.
        """
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "model-a"}],
        )

        # Execute
        client._ensure_model_loaded("model-b", auto_download=True)

        # Verify: model is not in the built-in MODELS registry, so the
        # 32K fallback kicks in (ChatAgent's prompt is >7K tokens; loading
        # at Lemonade's 4K default would silently truncate it).
        mock_load.assert_called_once_with(
            "model-b", auto_download=True, prompt=False, ctx_size=32768
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_known_model_uses_registry_ctx_size(self, mock_load, mock_status):
        """Verify a model in the built-in MODELS registry loads at the
        registry's ``min_ctx_size``, NOT the 32K fallback.

        The fallback only fires for models *not* in MODELS — see
        ``lemonade_client.py:_ensure_model_loaded`` "Model not in MODELS
        registry" branch. This test prevents a silent regression where a
        future refactor breaks the registry-lookup loop and every model
        ends up at 32K (wasting memory on small models like Qwen3 0.6B).
        """
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "some-other-model"}],
        )

        # Qwen3-0.6B-GGUF is in MODELS with min_ctx_size=4096 — the
        # smallest of the registered models, so a regression that always
        # picks 32K is detectable here.
        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_skips_load_when_model_already_loaded(self, mock_load, mock_status):
        """Verify no load_model call when model already in loaded_models list.

        After #1030 ``_ensure_model_loaded`` skips the reload only when the
        loaded entry's ``recipe_options.ctx_size`` is at or above the GAIA
        expected window. Unknown models (not in MODELS) default to 32K, so
        the mock must report at least that to take the no-op branch — see
        ``lemonade_client.py:_ensure_model_loaded`` "Loaded but under-sized"
        comment for the reload path.
        """
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "model-a", "recipe_options": {"ctx_size": 32768}}],
        )

        # Execute
        client._ensure_model_loaded("model-a", auto_download=True)

        # Verify - should NOT call load_model
        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_skips_check_when_auto_download_disabled(self, mock_load, mock_status):
        """Verify method returns early when auto_download=False."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)

        # Execute
        client._ensure_model_loaded("model-a", auto_download=False)

        # Verify - should NOT call get_status or load_model
        mock_status.assert_not_called()
        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_handles_status_check_error_gracefully(self, mock_load, mock_status):
        """Verify errors during status check are logged but don't fail."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.side_effect = Exception("Connection failed")

        # Execute - should not raise
        client._ensure_model_loaded("model-a", auto_download=True)

        # Verify - load_model should not be called due to error
        mock_load.assert_not_called()


class TestStreamCompletionsModelLoading:
    """Test that _stream_completions_with_openai calls _ensure_model_loaded."""

    @patch.object(LemonadeClient, "_ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_calls_ensure_model_loaded_before_request(
        self, mock_openai_class, mock_ensure
    ):
        """Verify _ensure_model_loaded is called before making the API request."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance

        # Mock the streaming response
        mock_chunk = Mock()
        mock_chunk.model_dump.return_value = {
            "id": "test",
            "object": "text_completion",
            "created": 12345,
            "model": "test-model",
            "choices": [{"index": 0, "text": "Hello", "finish_reason": None}],
        }
        mock_openai_instance.completions.create.return_value = iter([mock_chunk])

        # Execute - consume the generator
        list(
            client._stream_completions_with_openai(
                model="test-model",
                prompt="test prompt",
                auto_download=True,
            )
        )

        # Verify _ensure_model_loaded was called with correct arguments
        mock_ensure.assert_called_once_with("test-model", True)

        # Verify it was called BEFORE the API request
        assert mock_ensure.call_count == 1
        assert mock_openai_instance.completions.create.called


class TestStreamChatCompletionsModelLoading:
    """Test that _stream_chat_completions_with_openai calls _ensure_model_loaded."""

    @patch.object(LemonadeClient, "_ensure_model_loaded")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_calls_ensure_model_loaded_before_request(
        self, mock_openai_class, mock_ensure
    ):
        """Verify _ensure_model_loaded is called before making the API request."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance

        # Mock the streaming response
        mock_chunk = Mock()
        mock_chunk.id = "test-id"
        mock_chunk.object = "chat.completion.chunk"
        mock_chunk.created = 12345
        mock_chunk.model = "test-model"

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.delta = Mock()
        mock_choice.delta.role = "assistant"
        mock_choice.delta.content = "Hello"
        mock_choice.finish_reason = None
        mock_chunk.choices = [mock_choice]

        mock_openai_instance.chat.completions.create.return_value = iter([mock_chunk])

        # Execute - consume the generator
        list(
            client._stream_chat_completions_with_openai(
                model="test-model",
                messages=[{"role": "user", "content": "test"}],
                auto_download=True,
            )
        )

        # Verify _ensure_model_loaded was called with correct arguments
        mock_ensure.assert_called_once_with("test-model", True)

        # Verify it was called BEFORE the API request
        assert mock_ensure.call_count == 1
        assert mock_openai_instance.chat.completions.create.called


class TestNoPromptBehavior:
    """Test that model downloads happen without prompting."""

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_ensure_model_loaded_passes_prompt_false(self, mock_load, mock_status):
        """Verify _ensure_model_loaded passes prompt=False to avoid user prompts."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[],  # No models loaded
        )

        # Execute
        client._ensure_model_loaded("new-model", auto_download=True)

        # Verify prompt=False is passed to skip user confirmation
        assert mock_load.called
        call_kwargs = mock_load.call_args.kwargs
        assert "prompt" in call_kwargs
        assert call_kwargs["prompt"] is False


class TestModelLoadingIntegration:
    """Integration-style tests for model loading behavior."""

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_model_loaded_when_not_present(
        self, mock_openai_class, mock_load, mock_status
    ):
        """Integration test: model is loaded when not in loaded_models list."""
        # Setup
        client = LemonadeClient(host="localhost", port=13305)

        # Mock status to show model NOT loaded
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "different-model"}],
        )

        # Mock OpenAI client
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance
        mock_chunk = Mock()
        mock_chunk.model_dump.return_value = {
            "id": "test",
            "object": "text_completion",
            "created": 12345,
            "model": "new-model",
            "choices": [{"index": 0, "text": "Response", "finish_reason": None}],
        }
        mock_openai_instance.completions.create.return_value = iter([mock_chunk])

        # Execute - consume the generator
        list(
            client._stream_completions_with_openai(
                model="new-model",
                prompt="test",
                auto_download=True,
            )
        )

        # Verify load_model was called to download/load the model WITHOUT prompting.
        # Same 32K fallback as above: "new-model" isn't in MODELS, so the
        # default ctx is bumped from Lemonade's 4K up to 32K.
        mock_load.assert_called_once_with(
            "new-model", auto_download=True, prompt=False, ctx_size=32768
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_model_not_loaded_when_already_present(
        self, mock_openai_class, mock_load, mock_status
    ):
        """Integration test: no load when model already in loaded_models list.

        Same shape as ``test_skips_load_when_model_already_loaded``: after
        #1030 the loaded entry must carry ``recipe_options.ctx_size`` at or
        above the GAIA-expected window (32K for unknown models), otherwise
        the under-sized-reload branch fires.
        """
        # Setup
        client = LemonadeClient(host="localhost", port=13305)

        # Mock status to show model IS loaded
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {"id": "existing-model", "recipe_options": {"ctx_size": 32768}}
            ],
        )

        # Mock OpenAI client
        mock_openai_instance = MagicMock()
        mock_openai_class.return_value = mock_openai_instance
        mock_chunk = Mock()
        mock_chunk.model_dump.return_value = {
            "id": "test",
            "object": "text_completion",
            "created": 12345,
            "model": "existing-model",
            "choices": [{"index": 0, "text": "Response", "finish_reason": None}],
        }
        mock_openai_instance.completions.create.return_value = iter([mock_chunk])

        # Execute - consume the generator
        list(
            client._stream_completions_with_openai(
                model="existing-model",
                prompt="test",
                auto_download=True,
            )
        )

        # Verify load_model was NOT called (model already loaded)
        mock_load.assert_not_called()
