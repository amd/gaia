# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for deriving context size from the Lemonade health payload."""

import unittest

# Representative /api/v1/health payload from Lemonade 10.7.0 (no top-level
# context_size; ctx lives per-model under recipe_options).
LEMONADE_10_7_HEALTH = {
    "status": "ok",
    "version": "10.7.0",
    "model_loaded": "nomic-embed-text-v2-moe-GGUF",
    "websocket_port": 9000,
    "max_models": {"embedding": 2, "llm": 2},
    "all_models_loaded": [
        {
            "backend_url": "http://127.0.0.1:8002/v1",
            "checkpoint": "nomic-ai/nomic-embed-text-v2-moe-GGUF:Q8_0",
            "device": "gpu",
            "model_name": "nomic-embed-text-v2-moe-GGUF",
            "recipe": "llamacpp",
            "recipe_options": {"ctx_size": 32768},
            "type": "embedding",
        },
        {
            "backend_url": "http://127.0.0.1:8003/v1",
            "checkpoint": "unsloth/Qwen3-VL-4B-Instruct-GGUF:Q4_K_M",
            "device": "gpu",
            "model_name": "Qwen3-VL-4B-Instruct-GGUF",
            "recipe": "llamacpp",
            "recipe_options": {"ctx_size": 16384},
            "type": "llm",
        },
    ],
}


class TestDeriveContextSize(unittest.TestCase):
    """_derive_context_size must read per-model ctx from 10.x health payloads."""

    def _derive(self, health, model="Qwen3-VL-4B-Instruct-GGUF"):
        from gaia_agent_emr.dashboard.server import _derive_context_size

        return _derive_context_size(health, model)

    def test_prefers_the_requested_models_ctx(self):
        """The VLM's own ctx wins over other loaded models'."""
        self.assertEqual(self._derive(LEMONADE_10_7_HEALTH), 16384)

    def test_falls_back_to_max_loaded_ctx_when_model_absent(self):
        """If the VLM isn't loaded, report the largest loaded ctx."""
        self.assertEqual(self._derive(LEMONADE_10_7_HEALTH, model="not-loaded"), 32768)

    def test_returns_zero_when_nothing_loaded(self):
        health = {"status": "ok", "version": "10.7.0", "all_models_loaded": []}
        self.assertEqual(self._derive(health), 0)

    def test_legacy_top_level_context_size_still_honored(self):
        """Pre-10.x payloads with a top-level context_size keep working."""
        health = {"status": "ok", "context_size": 32768}
        self.assertEqual(self._derive(health), 32768)

    def test_missing_recipe_options_is_not_an_error(self):
        health = {
            "status": "ok",
            "all_models_loaded": [{"model_name": "m", "recipe_options": None}],
        }
        self.assertEqual(self._derive(health, model="m"), 0)

    def test_bool_values_are_rejected(self):
        """bool is an int subclass; a truthy ctx must not count as a size."""
        health = {
            "status": "ok",
            "context_size": True,
            "all_models_loaded": [
                {"model_name": "m", "recipe_options": {"ctx_size": True}}
            ],
        }
        self.assertEqual(self._derive(health, model="m"), 0)

    def test_malformed_shapes_degrade_to_zero(self):
        """Non-dict entries and non-dict recipe_options must not raise."""
        health = {
            "status": "ok",
            "all_models_loaded": [
                "not-a-dict",
                {"model_name": "m", "recipe_options": "oops"},
                {"recipe_options": {"ctx_size": 8192}},  # unnamed model still counts
            ],
        }
        self.assertEqual(self._derive(health, model="m"), 8192)

    def test_non_list_all_models_loaded_degrades_to_zero(self):
        health = {"status": "ok", "all_models_loaded": "oops"}
        self.assertEqual(self._derive(health), 0)


if __name__ == "__main__":
    unittest.main()
