# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for LemonadeClient model loading functionality."""

import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    LemonadeStatus,
    _prompt_user_for_delete,
)


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

    @patch.object(LemonadeClient, "list_models")
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_status_probe_error_is_best_effort_and_load_still_runs(
        self, mock_load, mock_status, mock_list
    ):
        """A failed status PROBE is best-effort (#2053): it's logged and the
        load still proceeds — it must NOT abort the load, since that would let
        a transient /health hiccup silently skip loading the model. Only the
        load itself is the loud failure point.
        """
        # Setup
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.side_effect = Exception("Connection failed")
        mock_list.return_value = {"data": []}

        # Execute - the probe error is swallowed; the load still runs.
        client._ensure_model_loaded("model-a", auto_download=True)

        # Verify - load_model IS called (probe failure doesn't block the load).
        mock_load.assert_called_once()


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


# A message that ``_is_corrupt_download_error`` classifies as corrupt
# (see ``_CORRUPT_DOWNLOAD_PHRASES`` in lemonade_client.py).
_CORRUPT_ERROR_MESSAGE = "download validation failed: files are incomplete"


class TestPromptUserForDeleteNonInteractive:
    """``_prompt_user_for_delete`` must not call ``input()`` without a TTY.

    Its siblings ``_prompt_user_for_download`` and ``_prompt_user_for_repair``
    already guard on ``sys.stdin.isatty()/sys.stdout.isatty()`` and return the
    proceed-default in a non-interactive environment. ``_prompt_user_for_delete``
    lacks that guard, so on the FastAPI lifespan threadpool (no TTY) it calls
    ``input()`` and raises ``EOFError`` — dead-ending first boot (#1293).
    """

    def test_delete_prompt_returns_proceed_default_without_tty(self):
        """No TTY -> auto-proceed (return True), never call input()."""
        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(sys.stdout, "isatty", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            result = _prompt_user_for_delete("Qwen3-0.6B-GGUF")

        assert result is True
        mock_input.assert_not_called()

    def test_delete_prompt_does_not_raise_eoferror_without_tty(self):
        """Reproduce the #1293 dead-end: an idle/closed stdin makes ``input()``
        raise ``EOFError``. With the isatty guard in place we must never reach
        ``input()``, so no ``EOFError`` escapes.
        """
        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch.object(sys.stdout, "isatty", return_value=False),
            patch("builtins.input", side_effect=EOFError),
        ):
            # Must NOT raise — the guard short-circuits before input().
            result = _prompt_user_for_delete("Qwen3-0.6B-GGUF")

        assert result is True


def _make_client():
    return LemonadeClient(host="localhost", port=13305)


class TestLoadModelCorruptNonInteractive:
    """``load_model(prompt=False)`` must auto-heal a corrupt model without any
    interactive prompt, bounded to a single delete+redownload (#1293).
    """

    @patch("gaia.llm.lemonade_client._prompt_user_for_delete")
    @patch("gaia.llm.lemonade_client._prompt_user_for_repair")
    @patch.object(LemonadeClient, "pull_model_stream")
    @patch.object(LemonadeClient, "_send_request")
    def test_prompt_false_never_calls_repair_or_delete_prompt(
        self, mock_send, mock_pull, mock_repair, mock_delete
    ):
        """With ``prompt=False`` neither prompt helper may be invoked, even
        though the load failure is corrupt-classified. Resume succeeds here.
        """
        # First load fails corrupt; resume download completes; reload OK.
        mock_send.side_effect = [
            Exception(_CORRUPT_ERROR_MESSAGE),
            {"status": "loaded"},
        ]
        mock_pull.return_value = iter([{"event": "complete"}])

        client = _make_client()
        result = client.load_model("Qwen3-0.6B-GGUF", prompt=False)

        assert result == {"status": "loaded"}
        mock_repair.assert_not_called()
        mock_delete.assert_not_called()

    @patch("gaia.llm.lemonade_client._prompt_user_for_delete")
    @patch("gaia.llm.lemonade_client._prompt_user_for_repair")
    @patch.object(LemonadeClient, "delete_model")
    @patch.object(LemonadeClient, "pull_model_stream")
    @patch.object(LemonadeClient, "_send_request")
    def test_resume_failure_triggers_single_delete_and_redownload(
        self, mock_send, mock_pull, mock_delete_model, mock_repair, mock_delete_prompt
    ):
        """Resume fails -> exactly ONE delete + ONE fresh re-download, then a
        successful reload. No prompts, bounded recovery.
        """
        # send_request: initial load fails corrupt, then final reload succeeds.
        mock_send.side_effect = [
            Exception(_CORRUPT_ERROR_MESSAGE),
            {"status": "loaded"},
        ]
        # pull_model_stream: first call (resume) errors, second (fresh) completes.
        mock_pull.side_effect = [
            iter([{"event": "error", "error": "resume broke"}]),
            iter([{"event": "progress", "percent": 50}, {"event": "complete"}]),
        ]

        client = _make_client()
        result = client.load_model("Qwen3-0.6B-GGUF", prompt=False)

        assert result == {"status": "loaded"}
        mock_repair.assert_not_called()
        mock_delete_prompt.assert_not_called()
        # Bounded: exactly one delete, exactly two pull attempts (resume + fresh).
        assert mock_delete_model.call_count == 1
        assert mock_pull.call_count == 2

    @patch("gaia.llm.lemonade_client._prompt_user_for_delete")
    @patch("gaia.llm.lemonade_client._prompt_user_for_repair")
    @patch.object(LemonadeClient, "delete_model")
    @patch.object(LemonadeClient, "pull_model_stream")
    @patch.object(LemonadeClient, "_send_request")
    def test_unrecoverable_raises_actionable_error_no_eoferror(
        self, mock_send, mock_pull, mock_delete_model, mock_repair, mock_delete_prompt
    ):
        """Both resume and the single fresh re-download fail -> a single loud,
        actionable ``LemonadeClientError`` naming the recovery action and the
        Lemonade server log. No ``EOFError``, no hang, no silent swallow.
        """
        # Initial load fails corrupt; no successful reload ever happens.
        mock_send.side_effect = Exception(_CORRUPT_ERROR_MESSAGE)
        # Both pull attempts error out.
        mock_pull.side_effect = [
            iter([{"event": "error", "error": "resume broke"}]),
            iter([{"event": "error", "error": "fresh broke"}]),
        ]

        client = _make_client()
        with pytest.raises(LemonadeClientError) as exc_info:
            client.load_model("Qwen3-0.6B-GGUF", prompt=False)

        message = str(exc_info.value)
        # Actionable: names a recovery affordance and where to look.
        assert "Qwen3-0.6B-GGUF" in message
        assert "redownload" in message.lower() or "re-download" in message.lower()
        assert "server.log" in message.lower() or "server log" in message.lower()
        # No prompts; bounded recovery (single delete, two pulls).
        mock_repair.assert_not_called()
        mock_delete_prompt.assert_not_called()
        assert mock_delete_model.call_count == 1
        assert mock_pull.call_count == 2

    @patch("gaia.llm.lemonade_client._prompt_user_for_delete")
    @patch("gaia.llm.lemonade_client._prompt_user_for_repair")
    @patch.object(LemonadeClient, "pull_model_stream")
    @patch.object(LemonadeClient, "_send_request")
    def test_recovery_logs_progress_at_info(
        self, mock_send, mock_pull, mock_repair, mock_delete, caplog
    ):
        """Auto-heal must emit INFO progress from the pull stream so the boot
        log (tailed by the UI) shows movement and doesn't look frozen.
        """
        mock_send.side_effect = [
            Exception(_CORRUPT_ERROR_MESSAGE),
            {"status": "loaded"},
        ]
        mock_pull.return_value = iter(
            [
                {
                    "event": "progress",
                    "percent": 40,
                    "bytes_downloaded": 4 * 1024**3,
                    "bytes_total": 10 * 1024**3,
                },
                {"event": "complete"},
            ]
        )

        client = _make_client()
        import logging

        with caplog.at_level(logging.INFO, logger=client.log.name):
            client.load_model("Qwen3-0.6B-GGUF", prompt=False)

        info_text = " ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.INFO
        )
        assert "40" in info_text


class TestLoadModelCorruptInteractive:
    """A real TTY (``prompt=True``) must still prompt as before (#1293 must not
    weaken the interactive path).
    """

    @patch("gaia.llm.lemonade_client._prompt_user_for_repair", return_value=True)
    @patch.object(LemonadeClient, "pull_model_stream")
    @patch.object(LemonadeClient, "_send_request")
    def test_prompt_true_with_tty_reaches_repair_prompt(
        self, mock_send, mock_pull, mock_repair
    ):
        """prompt=True + simulated TTY -> the repair prompt is reached. The
        helper is patched so the test never blocks on real stdin.
        """
        mock_send.side_effect = [
            Exception(_CORRUPT_ERROR_MESSAGE),
            {"status": "loaded"},
        ]
        mock_pull.return_value = iter([{"event": "complete"}])

        client = _make_client()
        with (
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(sys.stdout, "isatty", return_value=True),
        ):
            result = client.load_model("Qwen3-0.6B-GGUF", prompt=True)

        assert result == {"status": "loaded"}
        mock_repair.assert_called_once()
