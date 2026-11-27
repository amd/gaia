# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess
import sys
import unittest

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
        """Check Lemonade is up AND return the name of a loaded chat model.

        Two failure modes the bare /health status code doesn't catch:

        1. Lemonade up with no model loaded → ``status: ok`` but
           ``all_models_loaded == []`` and ``/v1/chat/completions`` 500s.
        2. Lemonade up with a model loaded but ``gaia llm`` defaults to a
           DIFFERENT model (post-PR-#865 the default flipped to
           Gemma 4 E4B) — Lemonade then 500s on the unloaded one and the
           OpenAI client retries until our 60 s test timeout. CI runners
           rarely preload Gemma; locally most devs preload Qwen.

        Returns the first chat-capable ``model_name`` (so the test can
        pass it via ``--model`` and avoid case 2), or ``None`` to skip.
        """
        try:
            response = requests.get("http://localhost:13305/api/v1/health", timeout=5)
            if response.status_code != 200:
                print(
                    f"ERROR: Lemonade server health check failed with status: {response.status_code}"
                )
                return None
            data = response.json()
            chat_models = [
                m
                for m in data.get("all_models_loaded", [])
                if m.get("type") in ("llm", "vlm")
            ]
            if not chat_models:
                print(
                    "ERROR: Lemonade is up but no chat-capable model is loaded "
                    f"(all_models_loaded={data.get('all_models_loaded')}). "
                    "Cannot exercise the LLM CLI without a model — skipping."
                )
                return None
            chosen = chat_models[0].get("model_name")
            print(
                "OK: Lemonade server health check passed; chat models loaded: "
                f"{[m.get('model_name') for m in chat_models]}; using {chosen}"
            )
            return chosen
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Lemonade server health check failed with exception: {e}")
            return None

    def test_llm_command_with_server(self):
        """Test LLM command with running server."""

        # Check command availability first
        if not self._check_command_availability():
            self.skipTest("gaia command is not available")

        # Check if server is accessible AND has a chat model loaded.
        # Returns the loaded model name so we can pass it via --model and
        # avoid the "default model not loaded" 500-retry hang.
        loaded_model = self._check_lemonade_server_health()
        if loaded_model is None:
            self.skipTest("Lemonade server is not running or no chat model is loaded")

        try:
            # Test with explicit --base-url (without /api/v1 to test
            # normalization) and --model targeting the actually-loaded
            # checkpoint (so we don't trip Lemonade's auto-load on a
            # different default which otherwise 500-retries to timeout).
            cmd = [
                "gaia",
                "llm",
                "What is 1+1?",
                "--max-tokens",
                "20",
                "--base-url",
                "http://localhost:13305",
                "--model",
                loaded_model,
            ]
            print(f"Executing command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
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
        except OSError as e:
            # WinError 4551: Application Control policy blocks subprocess
            # This can happen on CI runners with strict security policies
            print(f"OS error running gaia command (may be blocked by policy): {e}")
            self.skipTest(f"OS blocked subprocess execution: {e}")
        except Exception as e:
            print(f"UNEXPECTED EXCEPTION: {type(e).__name__}: {e}")
            print(f"Exception details: {repr(e)}")
            import traceback

            print("Full traceback:")
            traceback.print_exc()
            self.fail(
                f"Unexpected exception running LLM command: {type(e).__name__}: {e}"
            )


if __name__ == "__main__":
    unittest.main()
