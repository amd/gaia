# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Offline checks for the VLM client and Lemonade config helpers."""

import os
import unittest
from io import StringIO
from unittest.mock import patch

from gaia.version import LEMONADE_VERSION


class TestVLMMimeTypeDetection(unittest.TestCase):
    """Test MIME type detection for image formats in VLM client."""

    def test_detect_jpeg_from_bytes(self):
        """Test JPEG detection from magic bytes."""
        from gaia.llm.vlm_client import detect_image_mime_type

        # JPEG magic bytes: FF D8 FF
        jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(jpeg_bytes), "image/jpeg")

    def test_detect_png_from_bytes(self):
        """Test PNG detection from magic bytes."""
        from gaia.llm.vlm_client import detect_image_mime_type

        # PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(png_bytes), "image/png")

    def test_detect_gif87a_from_bytes(self):
        """Test GIF87a detection from magic bytes."""
        from gaia.llm.vlm_client import detect_image_mime_type

        gif_bytes = b"GIF87a" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(gif_bytes), "image/gif")

    def test_detect_gif89a_from_bytes(self):
        """Test GIF89a detection from magic bytes."""
        from gaia.llm.vlm_client import detect_image_mime_type

        gif_bytes = b"GIF89a" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(gif_bytes), "image/gif")

    def test_detect_bmp_from_bytes(self):
        """Test BMP detection from magic bytes."""
        from gaia.llm.vlm_client import detect_image_mime_type

        bmp_bytes = b"BM" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(bmp_bytes), "image/bmp")

    def test_detect_webp_from_bytes(self):
        """Test WebP detection from magic bytes (RIFF...WEBP)."""
        from gaia.llm.vlm_client import detect_image_mime_type

        # WebP: RIFF [4 bytes size] WEBP
        webp_bytes = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(webp_bytes), "image/webp")

    def test_unknown_format_defaults_to_png(self):
        """Test that unknown format defaults to PNG."""
        from gaia.llm.vlm_client import detect_image_mime_type

        unknown_bytes = b"\x00\x01\x02\x03\x04\x05" + b"\x00" * 100
        self.assertEqual(detect_image_mime_type(unknown_bytes), "image/png")

    def test_empty_bytes_defaults_to_png(self):
        """Test that empty bytes default to PNG."""
        from gaia.llm.vlm_client import detect_image_mime_type

        self.assertEqual(detect_image_mime_type(b""), "image/png")

    def test_riff_without_webp_marker(self):
        """Test that RIFF without WEBP marker is not detected as WebP."""
        from gaia.llm.vlm_client import detect_image_mime_type

        # RIFF file that's not WebP (e.g., WAV audio)
        riff_not_webp = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 100
        # Should default to PNG since it's not a recognized image format
        self.assertEqual(detect_image_mime_type(riff_not_webp), "image/png")


class TestVLMClientApiKeyForwarding(unittest.TestCase):
    """Issue #1139: VLMClient must forward api_key to its inner LemonadeClient.

    VLMClient does NOT call ``resolve_lemonade_api_key`` itself — it forwards
    the raw kwarg so the canonical resolution happens once inside
    LemonadeClient. These tests prove that contract.
    """

    def test_vlm_client_forwards_explicit_api_key_to_lemonade_client(self):
        from unittest.mock import patch

        # VLMClient does a deferred ``from gaia.llm.lemonade_client import LemonadeClient``
        # inside ``__init__``; patch the symbol where it lives.
        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_lc:
            from gaia.llm.vlm_client import VLMClient

            VLMClient(api_key="abc-1139", auto_load=False)
            self.assertEqual(mock_lc.call_args.kwargs.get("api_key"), "abc-1139")

    def test_vlm_client_default_api_key_is_none_so_lemonade_client_resolves_env(self):
        """VLMClient must NOT pre-resolve the env var; LemonadeClient does that."""
        from unittest.mock import patch

        with patch("gaia.llm.lemonade_client.LemonadeClient") as mock_lc:
            from gaia.llm.vlm_client import VLMClient

            VLMClient(auto_load=False)
            # ``api_key`` is forwarded as None (not a resolved value); the
            # inner LemonadeClient is responsible for the env-var fallback.
            self.assertIsNone(mock_lc.call_args.kwargs.get("api_key"))


class TestLemonadeManagerContextMessage(unittest.TestCase):
    """Test cases for LemonadeManager context message formatting."""

    def setUp(self):
        """Reset LemonadeManager state before each test."""
        from gaia.llm.lemonade_manager import LemonadeManager

        LemonadeManager.reset()

    def tearDown(self):
        """Reset LemonadeManager state after each test."""
        from gaia.llm.lemonade_manager import LemonadeManager

        LemonadeManager.reset()

    def test_print_context_message_warning(self):
        """Test print_context_message outputs warning format."""
        from gaia.llm.lemonade_manager import LemonadeManager, MessageType

        stderr_capture = StringIO()
        with patch("sys.stderr", stderr_capture):
            LemonadeManager.print_context_message(4096, 32768, MessageType.WARNING)

        output = stderr_capture.getvalue()
        self.assertIn("⚠️", output)
        self.assertIn("Context size below recommended", output)
        self.assertIn("4096", output)
        self.assertIn("32768", output)
        self.assertNotIn("❌", output)

    def test_print_context_message_error(self):
        """Test print_context_message outputs error format."""
        from gaia.llm.lemonade_manager import LemonadeManager, MessageType

        stderr_capture = StringIO()
        with patch("sys.stderr", stderr_capture):
            LemonadeManager.print_context_message(4096, 32768, MessageType.ERROR)

        output = stderr_capture.getvalue()
        self.assertIn("❌", output)
        self.assertIn("Insufficient context size", output)
        self.assertIn("4096", output)
        self.assertIn("32768", output)
        self.assertNotIn("⚠️", output)

    def test_ensure_ready_returns_true_on_insufficient_context_initial(self):
        """Test ensure_ready returns True with insufficient context."""
        from unittest.mock import MagicMock

        from gaia.llm.lemonade_manager import (
            LemonadeManager,
            MessageType,
        )

        mock_client = MagicMock()
        mock_status = MagicMock()
        mock_status.running = True
        mock_status.version = LEMONADE_VERSION
        mock_status.busy = False
        mock_status.context_size = 4096
        mock_status.loaded_models = [
            {"id": "test-model", "labels": [], "recipe_options": {"ctx_size": 4096}}
        ]  # LLM model loaded
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:13305/api/v1"
        # Ceiling unknown, and the server keeps reporting 4096 after the reload.
        mock_client.get_model_max_context_window.return_value = None

        with patch(
            "gaia.llm.lemonade_manager.LemonadeClient",
            return_value=mock_client,
        ):
            with patch.object(
                LemonadeManager, "print_context_message"
            ) as mock_print_context:
                result = LemonadeManager.ensure_ready(
                    min_context_size=32768, quiet=False
                )

                self.assertTrue(result)
                # The unverified reload leaves the window the server reported.
                mock_print_context.assert_called_once_with(
                    4096, 32768, MessageType.WARNING
                )

    def test_ensure_ready_insufficient_context_already_initialized(self):
        """Test ensure_ready returns True on subsequent calls."""
        from unittest.mock import MagicMock

        from gaia.llm.lemonade_manager import (
            LemonadeManager,
            MessageType,
        )

        mock_client = MagicMock()
        mock_status = MagicMock()
        mock_status.running = True
        mock_status.version = LEMONADE_VERSION
        mock_status.busy = False
        mock_status.context_size = 4096
        mock_status.loaded_models = [
            {"id": "test-model", "labels": [], "recipe_options": {"ctx_size": 4096}}
        ]  # LLM model loaded
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:13305/api/v1"
        # Ceiling unknown, and the server keeps reporting 4096 after the reload.
        mock_client.get_model_max_context_window.return_value = None

        with patch(
            "gaia.llm.lemonade_manager.LemonadeClient",
            return_value=mock_client,
        ):
            first_result = LemonadeManager.ensure_ready(
                min_context_size=4096, quiet=True
            )
            self.assertTrue(first_result)

            with patch.object(
                LemonadeManager, "print_context_message"
            ) as mock_print_context:
                second_result = LemonadeManager.ensure_ready(
                    min_context_size=32768, quiet=False
                )

                self.assertTrue(second_result)
                # The unverified reload leaves the window the server reported.
                mock_print_context.assert_called_once_with(
                    4096, 32768, MessageType.WARNING
                )

    def test_ensure_ready_quiet_mode_suppresses_context_warning(self):
        """Test ensure_ready does not print warning when quiet=True."""
        from unittest.mock import MagicMock

        from gaia.llm.lemonade_manager import LemonadeManager

        mock_client = MagicMock()
        mock_status = MagicMock()
        mock_status.running = True
        mock_status.version = LEMONADE_VERSION
        mock_status.busy = False
        mock_status.context_size = 4096
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:13305/api/v1"
        # Ceiling unknown, and the server keeps reporting 4096 after the reload.
        mock_client.get_model_max_context_window.return_value = None

        with patch(
            "gaia.llm.lemonade_manager.LemonadeClient",
            return_value=mock_client,
        ):
            with patch.object(
                LemonadeManager, "print_context_message"
            ) as mock_print_context:
                result = LemonadeManager.ensure_ready(
                    min_context_size=32768, quiet=True
                )

                self.assertTrue(result)
                mock_print_context.assert_not_called()


class TestGetLemonadeConfigNormalization(unittest.TestCase):
    """Verify _get_lemonade_config normalizes LEMONADE_BASE_URL to include /api/v1."""

    def test_env_url_without_api_suffix_is_normalized(self):
        """LEMONADE_BASE_URL without /api/v1 should be normalized (issue #1158)."""
        from gaia.llm.lemonade_client import _get_lemonade_config

        with patch.dict(os.environ, {"LEMONADE_BASE_URL": "http://remote:13305"}):
            _host, _port, base_url = _get_lemonade_config()
            self.assertEqual(base_url, "http://remote:13305/api/v1")

    def test_env_url_with_api_suffix_unchanged(self):
        """LEMONADE_BASE_URL already containing /api/v1 should not be doubled."""
        from gaia.llm.lemonade_client import _get_lemonade_config

        with patch.dict(
            os.environ, {"LEMONADE_BASE_URL": "http://remote:13305/api/v1"}
        ):
            _host, _port, base_url = _get_lemonade_config()
            self.assertEqual(base_url, "http://remote:13305/api/v1")

    def test_env_url_with_trailing_slash_normalized(self):
        """Trailing slash should be stripped before appending /api/v1."""
        from gaia.llm.lemonade_client import _get_lemonade_config

        with patch.dict(os.environ, {"LEMONADE_BASE_URL": "http://remote:13305/"}):
            _host, _port, base_url = _get_lemonade_config()
            self.assertEqual(base_url, "http://remote:13305/api/v1")

    def test_env_url_host_and_port_extracted(self):
        """Host and port should be correctly parsed from the normalized URL."""
        from gaia.llm.lemonade_client import _get_lemonade_config

        with patch.dict(os.environ, {"LEMONADE_BASE_URL": "http://myhost:9000"}):
            host, port, base_url = _get_lemonade_config()
            self.assertEqual(host, "myhost")
            self.assertEqual(port, 9000)
            self.assertEqual(base_url, "http://myhost:9000/api/v1")
