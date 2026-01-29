# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SDToolsMixin - Stable Diffusion image generation tools."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from gaia.agents.sd import SDToolsMixin


@pytest.fixture
def mock_lemonade_client():
    """Create a mock LemonadeClient for testing."""
    with patch("gaia.agents.sd.mixin.LemonadeClient") as MockClient:
        mock_client = MagicMock()
        MockClient.return_value = mock_client
        mock_client.base_url = "http://localhost:8000/api/v1"
        yield mock_client


class TestSDToolsMixinInit:
    """Test SDToolsMixin initialization."""

    def test_init_sd_creates_output_directory(self, tmp_path, mock_lemonade_client):
        """Test that init_sd creates the output directory."""
        mixin = SDToolsMixin()
        output_dir = tmp_path / "sd_images"

        mixin.init_sd(output_dir=str(output_dir))

        assert output_dir.exists()
        assert mixin.sd_output_dir == output_dir

    def test_init_sd_sets_defaults(self, mock_lemonade_client):
        """Test that init_sd sets default values correctly."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        assert mixin.sd_default_model == "SDXL-Base-1.0"
        assert mixin.sd_default_size is None  # Auto-selected per model
        assert mixin.sd_default_steps is None  # Auto-selected per model

    def test_init_sd_custom_config(self, tmp_path, mock_lemonade_client):
        """Test init_sd with custom configuration."""
        mixin = SDToolsMixin()
        mixin.init_sd(
            base_url="http://custom:9000",
            output_dir=str(tmp_path),
            default_model="SD-Turbo",
            default_size="512x512",
            default_steps=8,
        )

        assert mixin.sd_default_model == "SD-Turbo"
        assert mixin.sd_default_size == "512x512"
        assert mixin.sd_default_steps == 8

    def test_generations_list_is_instance_level(self, mock_lemonade_client):
        """Test that sd_generations is instance-level, not shared."""
        mixin1 = SDToolsMixin()
        mixin2 = SDToolsMixin()

        mixin1.init_sd()
        mixin2.init_sd()

        # Add to one instance
        mixin1.sd_generations.append({"test": "data"})

        # Other instance should be empty
        assert len(mixin1.sd_generations) == 1
        assert len(mixin2.sd_generations) == 0


class TestSDToolsMixinValidation:
    """Test SDToolsMixin parameter validation."""

    def test_validate_invalid_model(self, mock_lemonade_client):
        """Test that invalid model returns error."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt", model="InvalidModel")

        assert result["status"] == "error"
        assert "Invalid model" in result["error"]

    def test_validate_invalid_size(self, mock_lemonade_client):
        """Test that invalid size returns error."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt", size="999x999")

        assert result["status"] == "error"
        assert "Invalid size" in result["error"]

    def test_valid_models(self):
        """Test that valid models are accepted."""
        assert "SD-Turbo" in SDToolsMixin.SD_MODELS
        assert "SDXL-Turbo" in SDToolsMixin.SD_MODELS

    def test_valid_sizes(self):
        """Test that valid sizes are defined."""
        assert "512x512" in SDToolsMixin.SD_SIZES
        assert "768x768" in SDToolsMixin.SD_SIZES
        assert "1024x1024" in SDToolsMixin.SD_SIZES


class TestSDToolsMixinGeneration:
    """Test SDToolsMixin image generation."""

    def test_generate_image_success(self, tmp_path, mock_lemonade_client):
        """Test successful image generation."""
        # Mock response with base64 PNG
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_lemonade_client.generate_image.return_value = {
            "data": [{"b64_json": fake_png}]
        }
        mock_lemonade_client.load_model.return_value = {"status": "success"}

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        result = mixin._generate_image("a test image")

        assert result["status"] == "success"
        assert Path(result["image_path"]).exists()
        assert result["model"] == "SDXL-Base-1.0"  # Default model
        assert result["size"] == "1024x1024"  # Auto-selected for SDXL
        assert "generation_time_ms" in result

    def test_generate_image_tracks_history(self, tmp_path, mock_lemonade_client):
        """Test that successful generation is tracked in history."""
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_lemonade_client.generate_image.return_value = {
            "data": [{"b64_json": fake_png}]
        }
        mock_lemonade_client.load_model.return_value = {"status": "success"}

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        assert len(mixin.sd_generations) == 0

        mixin._generate_image("first image")
        assert len(mixin.sd_generations) == 1

        mixin._generate_image("second image")
        assert len(mixin.sd_generations) == 2

    def test_generate_image_connection_error(self, mock_lemonade_client):
        """Test handling of connection errors."""
        from gaia.llm.lemonade_client import LemonadeClientError

        mock_lemonade_client.load_model.side_effect = LemonadeClientError(
            "Connection refused"
        )

        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt")

        assert result["status"] == "error"
        assert "Connection" in result["error"] or "connect" in result["error"].lower()

    def test_generate_image_with_seed(self, tmp_path, mock_lemonade_client):
        """Test generation with seed parameter."""
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_lemonade_client.generate_image.return_value = {
            "data": [{"b64_json": fake_png}]
        }
        mock_lemonade_client.load_model.return_value = {"status": "success"}

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        result = mixin._generate_image("test", seed=42)

        assert result["status"] == "success"
        assert result["seed"] == 42

        # Verify seed was passed in request
        call_kwargs = mock_lemonade_client.generate_image.call_args[1]
        assert call_kwargs["seed"] == 42

    def test_load_model_called_before_generation(self, tmp_path, mock_lemonade_client):
        """Test that load_model is called before image generation."""
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_lemonade_client.generate_image.return_value = {
            "data": [{"b64_json": fake_png}]
        }
        mock_lemonade_client.load_model.return_value = {"status": "success"}

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        mixin._generate_image("test prompt")

        # Verify load_model was called with the model name
        mock_lemonade_client.load_model.assert_called()
        call_args = mock_lemonade_client.load_model.call_args
        assert call_args[0][0] == "SDXL-Base-1.0"  # Default model


class TestSDToolsMixinHealthCheck:
    """Test SDToolsMixin health check."""

    def test_health_check_healthy(self, mock_lemonade_client):
        """Test health check when server is healthy."""
        mock_lemonade_client.list_sd_models.return_value = [
            {"id": "SD-Turbo", "labels": ["image"]},
            {"id": "SDXL-Turbo", "labels": ["image"]},
        ]

        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "healthy"
        assert "SD-Turbo" in health["models"]
        assert "SDXL-Turbo" in health["models"]

    def test_health_check_unavailable(self, mock_lemonade_client):
        """Test health check when server is unavailable."""
        from gaia.llm.lemonade_client import LemonadeClientError

        mock_lemonade_client.list_sd_models.side_effect = LemonadeClientError(
            "Connection refused"
        )

        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "unavailable"
        assert "error" in health

    def test_health_check_no_models(self, mock_lemonade_client):
        """Test health check when no SD models available."""
        mock_lemonade_client.list_sd_models.return_value = []

        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "unavailable"
        assert "No SD models" in health["error"]


class TestSDToolsMixinSaveImage:
    """Test SDToolsMixin image saving."""

    def test_save_image_creates_file(self, tmp_path, mock_lemonade_client):
        """Test that _save_image creates a file."""
        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = mixin._save_image("test prompt", image_bytes, "SD-Turbo")

        assert path.exists()
        assert path.suffix == ".png"
        assert "test_prompt" in path.stem
        assert "SD-Turbo" in path.stem

    def test_save_image_sanitizes_filename(self, tmp_path, mock_lemonade_client):
        """Test that special characters are removed from filename."""
        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        image_bytes = b"\x89PNG\r\n\x1a\n"
        path = mixin._save_image(
            "test/prompt:with<special>chars", image_bytes, "SD-Turbo"
        )

        assert path.exists()
        # Should not contain special characters
        assert "/" not in path.stem
        assert ":" not in path.stem
        assert "<" not in path.stem
