# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess
import sys
import unittest
from io import StringIO
from unittest.mock import patch

import requests

# from gaia.logger import get_logger


class TestLlmCli(unittest.TestCase):
    def setUp(self):
        # self.log = get_logger(__name__)
        pass

    def _check_command_availability(self):
        """Check if gaia command is available."""
        gaia_path = shutil.which("gaia")

        print("Command availability check:")
        print(f"  gaia path: {gaia_path}")
        print(f"  Current PATH: {os.environ.get('PATH', 'NOT_SET')}")
        print(f"  Current Python: {sys.executable}")

        if not gaia_path:
            print("ERROR: gaia command not found in PATH")
            return False

        print("OK: gaia command found")
        return True

    def _check_lemonade_server_health(self):
        """Check if lemonade server is running and accessible."""
        try:
            response = requests.get("http://localhost:8000/api/v1/health", timeout=5)
            if response.status_code == 200:
                print("OK: Lemonade server health check passed")
                return True
            else:
                print(
                    f"ERROR: Lemonade server health check failed with status: {response.status_code}"
                )
                return False
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Lemonade server health check failed with exception: {e}")
            return False

    def test_llm_command_with_server(self):
        """Test LLM command with running server."""

        # Check command availability first
        if not self._check_command_availability():
            self.skipTest("gaia command is not available")

        # Check if server is accessible
        if not self._check_lemonade_server_health():
            self.skipTest("Lemonade server is not running")

        try:
            # Test with explicit --base-url (without /api/v1 to test normalization)
            print(
                "Executing command: gaia llm 'What is 1+1?' --max-tokens 20 --base-url http://localhost:8000"
            )

            # Test the LLM command with explicit --base-url
            # This validates both the CLI arg and the base_url normalization
            result = subprocess.run(
                [
                    "gaia",
                    "llm",
                    "What is 1+1?",
                    "--max-tokens",
                    "20",
                    "--base-url",
                    "http://localhost:8000",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            # Log the output for debugging (avoid logging log messages to prevent conflicts)
            print(f"LLM command exit code: {result.returncode}")
            if result.stdout:
                print(f"LLM command stdout length: {len(result.stdout)} characters")
                print(f"LLM command stdout content: {repr(result.stdout)}")
            if result.stderr:
                print(f"LLM command stderr length: {len(result.stderr)} characters")
                print(f"LLM command stderr content: {repr(result.stderr)}")

            # Command should not crash (exit code 0 or reasonable error)
            # Allow non-zero exit codes since server might not have models loaded
            if result.returncode == 0:
                print("OK: LLM command executed successfully")
                stdout_content = result.stdout.strip()
                stderr_content = result.stderr.strip()
                print(f"Checking stdout content: {repr(stdout_content)}")

                # Check for lemonade server connection issues
                combined_output = (stdout_content + stderr_content).lower()
                server_error_indicators = [
                    "lemonade server is not running",
                    "not accessible",
                    "connection error",
                    "error generating response from local llm: connection error",
                ]

                found_server_errors = [
                    indicator
                    for indicator in server_error_indicators
                    if indicator in combined_output
                ]
                if found_server_errors:
                    self.fail(
                        f"LLM command failed due to lemonade server connection issues. "
                        f"Found indicators: {found_server_errors}. "
                        f"Full output - stdout: '{result.stdout}', stderr: '{result.stderr}'"
                    )

                # Should have some output
                self.assertTrue(
                    len(stdout_content) > 0,
                    f"Expected some output from LLM command, but got: stdout='{result.stdout}', stderr='{result.stderr}'",
                )
                print("OK: LLM command produced expected output")
            else:
                # If it failed, it should be a reasonable error message
                print(
                    f"LLM command returned error (may be expected if no models loaded). Exit code: {result.returncode}"
                )
                print(f"Error output - stdout: {repr(result.stdout)}")
                print(f"Error output - stderr: {repr(result.stderr)}")

                # Check for lemonade server connection issues even on non-zero exit
                combined_output = (result.stdout + result.stderr).lower()
                server_error_indicators = [
                    "lemonade server is not running",
                    "not accessible",
                    "connection error",
                ]

                found_server_errors = [
                    indicator
                    for indicator in server_error_indicators
                    if indicator in combined_output
                ]
                if found_server_errors:
                    self.fail(
                        f"LLM command failed due to lemonade server connection issues. "
                        f"Found indicators: {found_server_errors}. "
                        f"Full output - stdout: '{result.stdout}', stderr: '{result.stderr}'"
                    )

                # Should not be a crash or connection error
                error_text = (result.stdout + result.stderr).lower()
                crash_keywords = ["traceback", "exception", "crash"]
                found_crashes = [kw for kw in crash_keywords if kw in error_text]
                self.assertFalse(
                    len(found_crashes) > 0,
                    f"LLM command appears to have crashed with keywords {found_crashes}. Full error: stdout='{result.stdout}', stderr='{result.stderr}'",
                )

        except subprocess.TimeoutExpired as e:
            print(f"TIMEOUT DETAILS: {e}")
            print(f"Command: {e.cmd}")
            print(f"Timeout: {e.timeout}")
            if hasattr(e, "stdout") and e.stdout:
                print(f"Partial stdout: {e.stdout}")
            if hasattr(e, "stderr") and e.stderr:
                print(f"Partial stderr: {e.stderr}")
            self.fail(f"LLM command with server timed out after 60 seconds: {e}")
        except Exception as e:
            print(f"UNEXPECTED EXCEPTION: {type(e).__name__}: {e}")
            print(f"Exception details: {repr(e)}")
            import traceback

            print("Full traceback:")
            traceback.print_exc()
            self.fail(
                f"Unexpected exception running LLM command: {type(e).__name__}: {e}"
            )


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
        mock_status.context_size = 4096
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:8000/api/v1"

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
        mock_status.context_size = 4096
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:8000/api/v1"

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
        mock_status.context_size = 4096
        mock_client.get_status.return_value = mock_status
        mock_client.base_url = "http://localhost:8000/api/v1"

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


if __name__ == "__main__":
    unittest.main()
