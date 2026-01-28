# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SDToolsMixin - Stable Diffusion image generation tools."""

import base64
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gaia.agents.sd import SDToolsMixin


class TestSDToolsMixinInit:
    """Test SDToolsMixin initialization."""

    def test_init_sd_creates_output_directory(self, tmp_path):
        """Test that init_sd creates the output directory."""
        mixin = SDToolsMixin()
        output_dir = tmp_path / "sd_images"

        mixin.init_sd(output_dir=str(output_dir))

        assert output_dir.exists()
        assert mixin.sd_output_dir == output_dir

    def test_init_sd_sets_defaults(self):
        """Test that init_sd sets default values correctly."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        assert mixin.sd_default_model == "SD-Turbo"
        assert mixin.sd_default_size == "512x512"
        assert mixin.sd_default_steps == 4
        assert mixin.sd_endpoint == "http://localhost:8000/api/v1/images/generations"

    def test_init_sd_custom_config(self, tmp_path):
        """Test init_sd with custom configuration."""
        mixin = SDToolsMixin()
        mixin.init_sd(
            base_url="http://custom:9000",
            output_dir=str(tmp_path),
            default_model="SDXL-Turbo",
            default_size="1024x1024",
            default_steps=8,
        )

        assert mixin.sd_endpoint == "http://custom:9000/api/v1/images/generations"
        assert mixin.sd_default_model == "SDXL-Turbo"
        assert mixin.sd_default_size == "1024x1024"
        assert mixin.sd_default_steps == 8

    def test_generations_list_is_instance_level(self):
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

    def test_validate_invalid_model(self):
        """Test that invalid model returns error."""
        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt", model="InvalidModel")

        assert result["status"] == "error"
        assert "Invalid model" in result["error"]

    def test_validate_invalid_size(self):
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

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_success(self, mock_post, tmp_path):
        """Test successful image generation."""
        # Mock response with base64 PNG (minimal valid PNG header)
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {"data": [{"b64_json": fake_png}]},
        )
        mock_post.return_value.raise_for_status = Mock()

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        result = mixin._generate_image("a test image")

        assert result["status"] == "success"
        assert Path(result["image_path"]).exists()
        assert result["model"] == "SD-Turbo"
        assert result["size"] == "512x512"
        assert "generation_time_ms" in result

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_tracks_history(self, mock_post, tmp_path):
        """Test that successful generation is tracked in history."""
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {"data": [{"b64_json": fake_png}]},
        )
        mock_post.return_value.raise_for_status = Mock()

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        assert len(mixin.sd_generations) == 0

        mixin._generate_image("first image")
        assert len(mixin.sd_generations) == 1

        mixin._generate_image("second image")
        assert len(mixin.sd_generations) == 2

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_connection_error(self, mock_post):
        """Test handling of connection errors."""
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError()

        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt")

        assert result["status"] == "error"
        assert "Cannot connect" in result["error"]

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_timeout(self, mock_post):
        """Test handling of timeout errors."""
        import requests

        mock_post.side_effect = requests.exceptions.Timeout()

        mixin = SDToolsMixin()
        mixin.init_sd()

        result = mixin._generate_image("test prompt")

        assert result["status"] == "error"
        assert "timed out" in result["error"]

    @patch("gaia.agents.sd.mixin.requests.post")
    def test_generate_image_with_seed(self, mock_post, tmp_path):
        """Test generation with seed parameter."""
        fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {"data": [{"b64_json": fake_png}]},
        )
        mock_post.return_value.raise_for_status = Mock()

        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        result = mixin._generate_image("test", seed=42)

        assert result["status"] == "success"
        assert result["seed"] == 42

        # Verify seed was passed in request
        call_args = mock_post.call_args
        assert call_args[1]["json"]["seed"] == 42


class TestSDToolsMixinHealthCheck:
    """Test SDToolsMixin health check."""

    @patch("gaia.agents.sd.mixin.requests.get")
    def test_health_check_healthy(self, mock_get):
        """Test health check when server is healthy."""
        mock_get.return_value = Mock(ok=True)

        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "healthy"
        assert "SD-Turbo" in health["models"]

    @patch("gaia.agents.sd.mixin.requests.get")
    def test_health_check_unavailable(self, mock_get):
        """Test health check when server is unavailable."""
        import requests

        mock_get.side_effect = requests.exceptions.ConnectionError()

        mixin = SDToolsMixin()
        mixin.init_sd()

        health = mixin.sd_health_check()

        assert health["status"] == "unavailable"
        assert "error" in health


class TestSDToolsMixinSaveImage:
    """Test SDToolsMixin image saving."""

    def test_save_image_creates_file(self, tmp_path):
        """Test that _save_image creates a file."""
        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = mixin._save_image("test prompt", image_bytes, "SD-Turbo")

        assert path.exists()
        assert path.suffix == ".png"
        assert "test_prompt" in path.stem
        assert "SD-Turbo" in path.stem

    def test_save_image_sanitizes_filename(self, tmp_path):
        """Test that special characters are removed from filename."""
        mixin = SDToolsMixin()
        mixin.init_sd(output_dir=str(tmp_path))

        image_bytes = b"\x89PNG\r\n\x1a\n"
        path = mixin._save_image("test/prompt:with<special>chars", image_bytes, "SD-Turbo")

        assert path.exists()
        # Should not contain special characters
        assert "/" not in path.stem
        assert ":" not in path.stem
        assert "<" not in path.stem
