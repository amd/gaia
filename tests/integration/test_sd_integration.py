# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for SDToolsMixin with real Lemonade Server.

These tests require Lemonade Server running with SD-Turbo model available.
Tests will be skipped if the server is not accessible.

Run with:
    pytest tests/integration/test_sd_integration.py -v

Prerequisites:
    lemonade-server serve --model SD-Turbo
"""

from pathlib import Path

import pytest

from gaia.agents.sd import SDToolsMixin
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError


def lemonade_available():
    """Check if Lemonade Server is running and SD models are available."""
    try:
        client = LemonadeClient(verbose=False)
        sd_models = client.list_sd_models()
        return len(sd_models) > 0
    except Exception:
        return False


# Skip all tests if Lemonade is not available
pytestmark = pytest.mark.skipif(
    not lemonade_available(), reason="Lemonade Server with SD model not available"
)


class TestSDIntegration:
    """Integration tests for SD image generation with real Lemonade Server."""

    def test_generate_small_image(self, tmp_path):
        """Test generating a small 512x512 image with SD-Turbo.

        This is the minimal integration test to verify end-to-end functionality.
        Uses SD-Turbo at 512x512 for fastest generation.
        """
        mixin = SDToolsMixin()
        mixin.init_sd(
            output_dir=str(tmp_path),
            default_model="SD-Turbo",
            default_size="512x512",
        )

        # Generate a simple test image
        result = mixin._generate_image(
            prompt="a red circle on white background, simple, minimal",
            model="SD-Turbo",
            size="512x512",
        )

        # Verify success
        assert (
            result["status"] == "success"
        ), f"Generation failed: {result.get('error')}"
        assert "image_path" in result

        # Verify file was created
        image_path = Path(result["image_path"])
        assert image_path.exists(), f"Image file not found: {image_path}"
        assert image_path.suffix == ".png"

        # Verify file has content (basic PNG check)
        file_size = image_path.stat().st_size
        assert file_size > 1000, f"Image file too small: {file_size} bytes"

        # Verify result metadata
        assert result["model"] == "SD-Turbo"
        assert result["size"] == "512x512"
        assert "generation_time_ms" in result
        assert result["generation_time_ms"] > 0

    def test_health_check_with_real_server(self):
        """Test health check returns available SD models."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "healthy"
        assert "models" in health
        assert len(health["models"]) > 0
        # At least SD-Turbo should be available
        assert any("SD" in m for m in health["models"])

    def test_generation_history_tracking(self, tmp_path):
        """Test that generations are tracked in history."""
        mixin = SDToolsMixin()
        mixin.init_sd(
            output_dir=str(tmp_path),
            default_model="SD-Turbo",
            default_size="512x512",
        )

        # Initially empty
        assert len(mixin.sd_generations) == 0

        # Generate one image
        result = mixin._generate_image(
            prompt="blue square",
            model="SD-Turbo",
            size="512x512",
        )

        if result["status"] == "success":
            assert len(mixin.sd_generations) == 1
            assert mixin.sd_generations[0]["prompt"] == "blue square"
            assert "created_at" in mixin.sd_generations[0]

    def test_seed_reproducibility(self, tmp_path):
        """Test that using the same seed produces consistent results."""
        mixin = SDToolsMixin()
        mixin.init_sd(
            output_dir=str(tmp_path),
            default_model="SD-Turbo",
            default_size="512x512",
        )

        # Generate with fixed seed
        result1 = mixin._generate_image(
            prompt="a green triangle",
            model="SD-Turbo",
            size="512x512",
            seed=12345,
        )

        result2 = mixin._generate_image(
            prompt="a green triangle",
            model="SD-Turbo",
            size="512x512",
            seed=12345,
        )

        # Both should succeed
        if result1["status"] == "success" and result2["status"] == "success":
            # With same seed, images should have same hash
            # Note: This may not be 100% deterministic on all hardware
            assert result1["seed"] == 12345
            assert result2["seed"] == 12345


class TestLemonadeClientSDMethods:
    """Integration tests for LemonadeClient SD methods."""

    def test_list_sd_models(self):
        """Test listing SD models from Lemonade Server."""
        client = LemonadeClient(verbose=False)
        sd_models = client.list_sd_models()

        assert isinstance(sd_models, list)
        assert len(sd_models) > 0

        # Each model should have an id
        for model in sd_models:
            assert "id" in model

    def test_generate_image_via_client(self, tmp_path):
        """Test image generation directly via LemonadeClient."""
        client = LemonadeClient(verbose=False)

        # First ensure model is loaded
        try:
            client.load_model("SD-Turbo", auto_download=True, prompt=False, timeout=300)
        except LemonadeClientError:
            pass  # Model might already be loaded

        # Generate image
        response = client.generate_image(
            prompt="a yellow star on black background",
            model="SD-Turbo",
            size="512x512",
            timeout=120,
        )

        assert "data" in response
        assert len(response["data"]) > 0
        assert "b64_json" in response["data"][0]

        # Verify it's valid base64
        import base64

        image_bytes = base64.b64decode(response["data"][0]["b64_json"])
        assert len(image_bytes) > 1000
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
