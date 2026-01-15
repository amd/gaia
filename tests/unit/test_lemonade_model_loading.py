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
        """Verify load_model is called when model not in loaded_models list."""
        # Setup
        client = LemonadeClient(host="localhost", port=8000)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:8000",
            running=True,
            loaded_models=[{"id": "model-a"}],
        )

        # Execute
        client._ensure_model_loaded("model-b", auto_download=True)

        # Verify - should call with prompt=False to skip user confirmation
        mock_load.assert_called_once_with("model-b", auto_download=True, prompt=False)

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_skips_load_when_model_already_loaded(self, mock_load, mock_status):
        """Verify no load_model call when model already in loaded_models list."""
        # Setup
        client = LemonadeClient(host="localhost", port=8000)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:8000",
            running=True,
            loaded_models=[{"id": "model-a"}],
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
        client = LemonadeClient(host="localhost", port=8000)

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
        client = LemonadeClient(host="localhost", port=8000)
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
        client = LemonadeClient(host="localhost", port=8000)
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
        client = LemonadeClient(host="localhost", port=8000)
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
        client = LemonadeClient(host="localhost", port=8000)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:8000",
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
        client = LemonadeClient(host="localhost", port=8000)

        # Mock status to show model NOT loaded
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:8000",
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

        # Verify load_model was called to download/load the model WITHOUT prompting
        mock_load.assert_called_once_with("new-model", auto_download=True, prompt=False)

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    @patch("gaia.llm.lemonade_client.OpenAI")
    def test_model_not_loaded_when_already_present(
        self, mock_openai_class, mock_load, mock_status
    ):
        """Integration test: no load when model already in loaded_models list."""
        # Setup
        client = LemonadeClient(host="localhost", port=8000)

        # Mock status to show model IS loaded
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:8000",
            running=True,
            loaded_models=[{"id": "existing-model"}],
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
