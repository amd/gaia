# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#1892): LemonadeClient instance-scoped exact-pin ctx override.

``LemonadeClient`` does not yet accept a ``ctx_size_override`` constructor
kwarg / attribute. This file is the TDD pin for it:

1. ``ctx_size_override`` is INSTANCE-scoped only -- never a module/class-level
   flag, never a mutation of the shared ``MODELS`` dict.
2. When set, ``_ensure_model_loaded`` switches from FLOOR semantics
   (``loaded_ctx >= expected_ctx`` -> no-op) to EXACT-PIN semantics: reload
   whenever ``loaded_ctx != override``, even when the loaded ctx is HIGHER
   than the override (a floor check would wrongly no-op here).
3. The non-override path keeps today's floor semantics unchanged (regression
   guard).
"""

from unittest.mock import patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeStatus


class TestCtxOverrideIsInstanceScoped:
    def test_ctx_override_is_instance_scoped(self):
        """Setting the override on one instance must not leak to another
        instance constructed afterward in the same process."""
        client_a = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        client_b = LemonadeClient(host="localhost", port=13305)

        assert client_a.ctx_size_override == 4096
        assert client_b.ctx_size_override is None

    def test_default_ctx_size_override_is_none(self):
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

    def test_ctx_size_override_is_not_a_class_attribute_mutation(self):
        """Setting the override on an instance must not become visible as a
        class-level default for instances constructed with no override --
        i.e. it must not be implemented as a mutable class attribute."""
        client_a = LemonadeClient(host="localhost", port=13305, ctx_size_override=8192)
        assert client_a.ctx_size_override == 8192

        client_b = LemonadeClient(host="localhost", port=13305)
        assert client_b.ctx_size_override is None

        # Also assert the class itself carries no non-None default that a
        # future instance would inherit.
        assert getattr(LemonadeClient, "ctx_size_override", None) is None

    def test_ctx_size_override_does_not_mutate_models_registry(self):
        """The override must be a client-instance concern -- it must never
        write into the shared MODELS dict (which is process-wide and shared
        across every LemonadeClient instance)."""
        from gaia.llm.lemonade_client import MODELS

        # Snapshot the registry's min_ctx_size values before constructing an
        # overridden client.
        before = {k: v.min_ctx_size for k, v in MODELS.items()}

        LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)

        after = {k: v.min_ctx_size for k, v in MODELS.items()}
        assert before == after


class TestEnsureModelLoadedExactPin:
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_ensure_model_loaded_exact_pins_override(self, mock_load, mock_status):
        """A model loaded at ctx=16384 with override=4096 must still reload
        at exactly 4096 -- a floor check (16384 >= 4096) would wrongly no-op
        here, but exact-pin semantics require an exact match."""
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "model_name": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 16384},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "Gemma-4-E4B-it-GGUF",
            auto_download=True,
            prompt=False,
            ctx_size=4096,
        )

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_exact_pin_skips_reload_when_already_at_override(
        self, mock_load, mock_status
    ):
        """When the loaded ctx already equals the override, no reload is
        needed -- exact-pin is a ``!=`` check, not always-reload."""
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=4096)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "model_name": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 4096},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_exact_pin_reloads_when_loaded_ctx_is_lower_than_override(
        self, mock_load, mock_status
    ):
        """Sanity companion to the exact-pin test: the lower-than-override
        direction (which floor semantics also would have reloaded) must
        still reload at exactly the override value."""
        client = LemonadeClient(host="localhost", port=13305, ctx_size_override=16384)
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Gemma-4-E4B-it-GGUF",
                    "model_name": "Gemma-4-E4B-it-GGUF",
                    "recipe_options": {"ctx_size": 4096},
                }
            ],
        )

        client._ensure_model_loaded("Gemma-4-E4B-it-GGUF", auto_download=True)

        mock_load.assert_called_once_with(
            "Gemma-4-E4B-it-GGUF",
            auto_download=True,
            prompt=False,
            ctx_size=16384,
        )


class TestNonOverridePathKeepsFloorSemantics:
    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_override_and_loaded_ctx_at_or_above_expected_does_not_reload(
        self, mock_load, mock_status
    ):
        """Regression guard: a client with NO ctx_size_override set must keep
        today's floor semantics -- loaded_ctx >= expected_ctx (from the
        MODELS registry lookup) is still a no-op, not a forced exact-pin
        reload."""
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

        # Qwen3-0.6B-GGUF is in MODELS with min_ctx_size=4096 (the smallest
        # registered model) -- loaded at exactly that floor must no-op.
        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "recipe_options": {"ctx_size": 4096},
                }
            ],
        )

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()

    @patch.object(LemonadeClient, "get_status")
    @patch.object(LemonadeClient, "load_model")
    def test_no_override_and_loaded_ctx_above_expected_still_does_not_reload(
        self, mock_load, mock_status
    ):
        """Floor semantics: a HIGHER-than-expected loaded ctx must still
        no-op with no override set -- this is the case exact-pin
        deliberately changes when an override IS set (see
        test_ensure_model_loaded_exact_pins_override above)."""
        client = LemonadeClient(host="localhost", port=13305)
        assert client.ctx_size_override is None

        mock_status.return_value = LemonadeStatus(
            url="http://localhost:13305",
            running=True,
            loaded_models=[
                {
                    "id": "Qwen3-0.6B-GGUF",
                    "model_name": "Qwen3-0.6B-GGUF",
                    "recipe_options": {"ctx_size": 16384},
                }
            ],
        )

        client._ensure_model_loaded("Qwen3-0.6B-GGUF", auto_download=True)

        mock_load.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
