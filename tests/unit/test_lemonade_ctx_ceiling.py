# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the context-ceiling clamp (issue #2992).

GAIA picked its requested ctx_size from a flat per-device constant
(``profile_ctx_size``) with no knowledge of the model actually being loaded,
then reported the requested value back as if it were a measurement. Lemonade
exposes the model's real trained-context ceiling as ``max_context_window`` in
both ``/api/v1/models`` and ``/api/v1/health``'s ``all_models_loaded`` entries
— this suite covers clamping the requested ctx to that ceiling and reporting
the value actually in force.
"""

from unittest.mock import patch

from gaia.llm.lemonade_client import (
    NPU_CTX_SIZE,
    LemonadeClient,
    LemonadeClientError,
    LemonadeStatus,
    resolve_effective_ctx_size,
)

# ---------------------------------------------------------------------------
# resolve_effective_ctx_size — pure clamp function
# ---------------------------------------------------------------------------


def test_clamp_applied_when_ceiling_below_requested():
    """65536 requested, 40960 is the model's real ceiling -> clamp to 40960."""
    assert resolve_effective_ctx_size(65536, 40960) == 40960


def test_no_clamp_when_ceiling_at_or_above_requested():
    """Ceiling >= requested: pass the requested value through unchanged."""
    assert resolve_effective_ctx_size(65536, 131072) == 65536
    assert resolve_effective_ctx_size(65536, 65536) == 65536


def test_no_clamp_when_ceiling_unknown():
    """None (or falsy) ceiling means Lemonade hasn't resolved the model's
    metadata yet — proceed with the requested value, never guess a smaller
    number than what was actually asked for."""
    assert resolve_effective_ctx_size(65536, None) == 65536
    assert resolve_effective_ctx_size(65536, 0) == 65536


def test_clamp_applies_under_npu_profile_too():
    """The clamp is profile-agnostic — a ceiling below NPU_CTX_SIZE (32768)
    is the same #2992 bug as the GPU/CPU 65536 case, and a fix that only
    special-cases GPU_CTX_SIZE would miss it entirely."""
    assert NPU_CTX_SIZE == 32768
    assert resolve_effective_ctx_size(NPU_CTX_SIZE, 16384) == 16384
    assert resolve_effective_ctx_size(NPU_CTX_SIZE, 40960) == NPU_CTX_SIZE


# ---------------------------------------------------------------------------
# LemonadeClient.get_model_max_context_window
# ---------------------------------------------------------------------------


class TestGetModelMaxContextWindow:
    def test_reads_from_already_loaded_status_without_extra_http_call(self):
        """When the model is already in `status.loaded_models`, the ceiling
        must come from there — no catalog HTTP call needed."""
        client = LemonadeClient(host="localhost", port=13305)
        status = LemonadeStatus(
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "max_context_window": 40960,
                }
            ],
        )
        with patch.object(client, "list_models") as mock_list:
            ceiling = client.get_model_max_context_window(
                "Qwen3-0.6B-GGUF", status=status
            )
        assert ceiling == 40960
        mock_list.assert_not_called()

    def test_falls_back_to_catalog_when_not_in_status(self):
        client = LemonadeClient(host="localhost", port=13305)
        status = LemonadeStatus(running=True, loaded_models=[])
        with patch.object(
            client,
            "list_models",
            return_value={
                "data": [
                    {"id": "Gemma-4-E4B-it-GGUF", "max_context_window": 131072},
                ]
            },
        ) as mock_list:
            ceiling = client.get_model_max_context_window(
                "Gemma-4-E4B-it-GGUF", status=status
            )
        assert ceiling == 131072
        mock_list.assert_called_once()

    def test_catalog_lookup_disabled_returns_none_without_http_call(self):
        """`allow_catalog_lookup=False` must never make an HTTP call — this is
        the opt-out used by `_ensure_model_loaded_locked`'s best-effort
        conflict check so it doesn't add a network round trip to every
        model load (or break unit tests that don't mock `list_models`)."""
        client = LemonadeClient(host="localhost", port=13305)
        status = LemonadeStatus(running=True, loaded_models=[])
        with patch.object(client, "list_models") as mock_list:
            ceiling = client.get_model_max_context_window(
                "Gemma-4-E4B-it-GGUF", status=status, allow_catalog_lookup=False
            )
        assert ceiling is None
        mock_list.assert_not_called()

    def test_missing_max_context_window_returns_none(self):
        """A model with no `max_context_window` reported (common for an
        undownloaded model) is 'unknown', not 'unbounded' — must return None,
        not 0 or some sentinel that looks like a real ceiling."""
        client = LemonadeClient(host="localhost", port=13305)
        with patch.object(
            client,
            "list_models",
            return_value={"data": [{"id": "Some-Model-GGUF"}]},
        ):
            ceiling = client.get_model_max_context_window("Some-Model-GGUF")
        assert ceiling is None

    def test_catalog_query_failure_returns_none_not_raise(self):
        client = LemonadeClient(host="localhost", port=13305)
        with patch.object(
            client, "list_models", side_effect=LemonadeClientError("boom")
        ):
            ceiling = client.get_model_max_context_window("Some-Model-GGUF")
        assert ceiling is None


# ---------------------------------------------------------------------------
# _ensure_model_loaded_locked — floor (MODELS registry) vs ceiling conflict
# ---------------------------------------------------------------------------


class TestFloorCeilingConflict:
    @patch.object(LemonadeClient, "load_model")
    @patch.object(LemonadeClient, "get_status")
    def test_registry_floor_exceeding_known_ceiling_clamps_and_warns(
        self, mock_status, mock_load, caplog
    ):
        """If MODELS[...].min_ctx_size (the floor GAIA requires for a model)
        exceeds a *known* max_context_window ceiling, GAIA must clamp to the
        ceiling and load anyway — matching
        ``LemonadeManager._report_capped_at_ceiling``, which treats the
        identical situation as "proceed capped", not fatal. Raising here
        would leave the client and the manager disagreeing about the exact
        same (floor, ceiling) conflict, and the manager's own reload path
        already reaches this state successfully."""
        client = LemonadeClient(host="localhost", port=13305)
        # Qwen3-0.6B-GGUF's registry floor is 4096; report a ceiling below it,
        # with the model resident under that ceiling so a reload is expected.
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "max_context_window": 2048,
                    "recipe_options": {"ctx_size": 1024},
                }
            ],
        )

        with caplog.at_level("WARNING", logger="gaia.llm.lemonade_client"):
            client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        warning = "\n".join(
            rec.message for rec in caplog.records if rec.levelname == "WARNING"
        )
        assert "4096" in warning
        assert "2048" in warning
        assert "Qwen3-0.6B-GGUF" in warning
        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=2048
        )

    @patch.object(LemonadeClient, "load_model")
    @patch.object(LemonadeClient, "get_status")
    def test_resident_at_clamped_ceiling_skips_reload(self, mock_status, mock_load):
        """A model already loaded exactly at its (clamped) ceiling must NOT
        reload on every call. The clamp has to apply BEFORE the already-loaded
        comparison, or the comparison runs against the unclamped registry
        floor, never matches, and every chat completion pays a wasted /load
        that changes nothing."""
        client = LemonadeClient(host="localhost", port=13305)
        # Qwen3-0.6B-GGUF's registry floor is 4096; its ceiling here is 2048,
        # and it's already resident at that ceiling.
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "max_context_window": 2048,
                    "recipe_options": {"ctx_size": 2048},
                }
            ],
        )

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)
        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "load_model")
    @patch.object(LemonadeClient, "get_status")
    def test_unrelated_model_in_status_does_not_trigger_conflict_or_http(
        self, mock_status, mock_load
    ):
        """Regression guard: when the requested model is NOT the one already
        loaded, the best-effort ceiling check must not make a *catalog*
        HTTP call (``show_all=True``) — mirrors
        `test_known_model_uses_registry_ctx_size` in
        test_lemonade_model_loading.py. The pre-existing ``is_downloaded``
        probe still calls ``list_models()`` (no show_all) further down; that
        is unrelated to the ceiling check and untouched by this fix."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "some-other-model"}],
        )
        with patch.object(
            client, "list_models", return_value={"data": []}
        ) as mock_list:
            client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)
        for call in mock_list.call_args_list:
            assert call.kwargs.get("show_all") is not True
        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )

    @patch.object(LemonadeClient, "load_model")
    @patch.object(LemonadeClient, "get_status")
    def test_ceiling_at_or_above_floor_does_not_raise(self, mock_status, mock_load):
        """Loaded under the floor (2048 < registry's 4096) with a ceiling
        comfortably above the floor (40960): must reload to the floor value,
        not raise."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "max_context_window": 40960,
                    "recipe_options": {"ctx_size": 2048},
                }
            ],
        )
        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)
        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )

    @patch.object(LemonadeClient, "load_model")
    @patch.object(LemonadeClient, "get_status")
    def test_unknown_ceiling_logs_debug_and_does_not_raise(
        self, mock_status, mock_load, caplog
    ):
        """Missing max_context_window (older Lemonade, unusual recipe, or a
        response that omits it) must not be silent: named at debug level so
        the "unknown, proceeding anyway" choice is traceable, and the load
        must still proceed at the registry floor (#2992)."""
        client = LemonadeClient(host="localhost", port=13305)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[{"id": "some-other-model"}],
        )
        with caplog.at_level("DEBUG", logger="gaia.llm.lemonade_client"):
            client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)
        mock_load.assert_called_once_with(
            "Qwen3-0.6B-GGUF", auto_download=True, prompt=False, ctx_size=4096
        )
        assert any(
            "max_context_window" in rec.message for rec in caplog.records
        ), "Missing ceiling must be logged, not silently ignored (#2992)."


class TestCtxSizeOverrideBypassesClamp:
    """The eval-only exact-pin path (`ctx_size_override` / `_ensure_pinned_load`)
    must be completely untouched by the #2992 clamp — a pinned eval run
    deliberately requesting an exact ctx_size (even one above a model's
    ceiling) must get exactly that value, never a silently substituted one."""

    @patch.object(LemonadeClient, "_ensure_pinned_load")
    def test_ctx_size_override_short_circuits_before_ceiling_check(
        self, mock_pinned_load
    ):
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=999999)
        with patch.object(client, "get_model_max_context_window") as mock_ceiling:
            client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)
        mock_pinned_load.assert_called_once_with("Qwen3-0.6B-GGUF")
        mock_ceiling.assert_not_called()
