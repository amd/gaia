# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import pytest
import asyncio
import time
from pathlib import Path
import requests
import subprocess
import shlex
import os
import argparse
import sys


def pytest_addoption(parser):
    parser.addoption(
        "--hybrid",
        action="store_true",
        default=False,
        help="Use hybrid backend (pass flag to enable)",
    )


@pytest.mark.asyncio
class TestGaiaCLI:
    # Add class variable to control output printing
    print_server_output = True

    @pytest.fixture(scope="class", autouse=True)
    async def setup_and_teardown_class(cls, request):
        print("\n=== Starting Server ===")

        # Get hybrid parameter from command line, default to False
        hybrid = request.config.getoption("--hybrid", default=False)

        if hybrid:
            cmd = "gaia-cli start --backend oga --device hybrid --dtype int4 --model amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
        else:
            cmd = "gaia-cli start"
        print(f"Running command: {cmd}")

        # Add class variable to store the monitoring task
        cls.monitor_task = None

        try:
            # Use asyncio subprocess instead of subprocess.Popen
            if hybrid:
                cls.process = await asyncio.create_subprocess_exec(
                    "gaia-cli",
                    "start",
                    "--backend",
                    "oga",
                    "--device",
                    "hybrid",
                    "--dtype",
                    "int4",
                    "--model",
                    "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid",
                    "--background",
                    "none",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                cls.process = await asyncio.create_subprocess_exec(
                    "gaia-cli",
                    "start",
                    "--background",
                    "none",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            async def print_output():
                try:
                    while True:
                        stdout_line = await cls.process.stdout.readline()
                        stderr_line = await cls.process.stderr.readline()

                        if (
                            not stdout_line
                            and not stderr_line
                            and cls.process.returncode is not None
                        ):
                            break

                        if cls.print_server_output:
                            if stdout_line:
                                print(f"[STDOUT] {stdout_line.decode().strip()}")
                            if stderr_line:
                                print(
                                    f"[STDERR] {stderr_line.decode().strip()}",
                                    flush=True,
                                )

                        await asyncio.sleep(0.1)
                except Exception as e:
                    print(f"Output monitoring error: {e}")
                    cls.process.terminate()

            # Store the monitoring task in class variable
            cls.monitor_task = asyncio.create_task(print_output())

            # Wait for both agent and LLM servers
            timeout = 120
            start_time = time.time()
            print(f"Waiting for servers to be ready (timeout: {timeout}s)...")

            while time.time() - start_time < timeout:
                try:
                    # Check if process has terminated
                    if cls.process.returncode is not None:
                        raise RuntimeError(
                            f"Server process terminated with code {cls.process.returncode}"
                        )

                    # Check agent server
                    agent_health = requests.get("http://127.0.0.1:8001/health")
                    # Check LLM server
                    llm_health = requests.get("http://127.0.0.1:8000/health")

                    if (
                        agent_health.status_code == 200
                        and llm_health.status_code == 200
                    ):
                        print("Both servers are ready!")
                        await asyncio.sleep(5)
                        break
                except requests.exceptions.ConnectionError:
                    if time.time() - start_time > timeout - 10:
                        print(
                            f"Still waiting... ({int(timeout - (time.time() - start_time))}s remaining)"
                        )
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"Error during health check: {e}")
                    raise
            else:
                print("Servers failed to start!")
                cls.process.terminate()
                raise TimeoutError("Servers failed to start within timeout period")

            print("=== Server Started Successfully ===\n")
            yield

        except Exception as e:
            print(f"Setup error: {e}")
            await asyncio.create_subprocess_exec("gaia-cli", "stop")
            raise
        finally:
            print("\n=== Cleaning Up ===")
            # Cancel the monitoring task if it exists
            if hasattr(cls, "monitor_task") and cls.monitor_task:
                cls.monitor_task.cancel()
                try:
                    await cls.monitor_task
                except asyncio.CancelledError:
                    pass

            stop_process = await asyncio.create_subprocess_exec("gaia-cli", "stop")
            await stop_process.wait()
            if hasattr(cls, "process"):
                try:
                    cls.process.terminate()
                    await cls.process.wait()
                except:
                    pass
            await asyncio.sleep(1)
            print("=== Cleanup Complete ===")

    async def test_server_health(self):
        """Test if both agent and LLM servers are responding to health checks."""
        # Test Agent server health
        agent_response = requests.get("http://127.0.0.1:8001/health")
        assert agent_response.status_code == 200

        # Test LLM server health
        llm_response = requests.get("http://127.0.0.1:8000/health")
        assert llm_response.status_code == 200

    async def test_prompt(self):
        """Test basic prompt functionality with a simple question."""
        print("\n=== Starting prompt test ===")
        cmd = 'gaia-cli prompt "How many r\'s in strawberry?"'
        print(f"Running command: {cmd}")

        try:
            print("Executing prompt command...")
            process = await asyncio.create_subprocess_exec(
                "gaia-cli",
                "prompt",
                "How many r's in strawberry?",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=60
                )
                print("Command completed!")

                response = stdout.decode()
                print(f"Response: {response}")

                assert process.returncode == 0
                assert "Gaia CLI client initialized" in response
                assert "error" not in response.lower()
                assert len(response) > 0

            except asyncio.TimeoutError:
                print("Command timed out!")
                process.terminate()
                pytest.fail("Command timed out after 60 seconds")

        except Exception as e:
            print(f"Test failed with error: {e}")
            raise

    async def test_stats(self):
        """Test if server statistics are being collected and reported correctly."""
        # Get stats
        cmd = "gaia-cli stats"
        try:
            result = subprocess.run(
                shlex.split(cmd),
                capture_output=True,
                text=True,
                timeout=30,  # Add 30 second timeout
            )

            assert result.returncode == 0
            stats = result.stdout
            print(f"Stats: {stats}")

            assert "error" not in stats.lower()
            assert len(stats) > 0

        except subprocess.TimeoutExpired:
            pytest.fail("Stats command timed out after 30 seconds")

    async def test_tts_preprocessing_cli(self):
        """Test TTS preprocessing through CLI interface."""
        print("\n=== Starting TTS preprocessing CLI test ===")
        test_text = "Hello! This is a test of TTS preprocessing."

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "test",
            "--test-type",
            "tts-preprocessing",
            "--test-text",
            test_text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        response = stdout.decode()

        assert process.returncode == 0
        assert test_text in response
        assert "error" not in response.lower()

    async def test_asr_file_transcription_cli(self):
        """Test ASR file transcription through CLI interface."""
        print("\n=== Starting ASR file transcription CLI test ===")

        # Use the same test file path as in test_asr.py
        test_file = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "GAIA"
            / "data"
            / "audio"
            / "test.m4a"
        )

        if not test_file.exists():
            pytest.skip(
                f"Test file {test_file} not found - skipping transcription test"
            )

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "test",
            "--test-type",
            "asr-file-transcription",
            "--input-audio-file",
            str(test_file),
            "--whisper-model-size",
            "base",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        response = stdout.decode()

        assert process.returncode == 0
        assert "This is a test." in response
        assert "error" not in response.lower()

    async def test_asr_microphone_cli(self):
        """Test ASR microphone recording through CLI interface."""
        print("\n=== Starting ASR microphone test ===")

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "test",
            "--test-type",
            "asr-microphone",
            "--recording-duration",
            "3",  # Short duration for test
            "--whisper-model-size",
            "base",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        response = stdout.decode()
        print(f"Response: {response}")

        assert process.returncode == 0
        if "no default input device available" in response.lower():
            print(
                "\nWARNING: Skipping audio recording assertions - no input device available in this environment"
            )
            print(
                "This is expected in CI environments or systems without audio hardware.\n"
            )
            return

        assert "recording..." in response.lower()
        assert "recording stopped" in response.lower()
        assert "error" not in response.lower()

    async def test_list_audio_devices_cli(self):
        """Test listing audio devices through CLI interface."""
        print("\n=== Starting audio device listing test ===")

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "test",
            "--test-type",
            "asr-list-audio-devices",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        response = stdout.decode()

        assert process.returncode == 0
        if "no default input device available" in response.lower():
            print(
                "\nWARNING: Skipping audio device list assertions - no input devices available in this environment"
            )
            print(
                "This is expected in CI environments or systems without audio hardware.\n"
            )
            return

        assert "available audio devices:" in response.lower()
        assert "error" not in response.lower()

    async def test_tts_audio_file_cli(self, tmp_path):
        """Test TTS audio file generation through CLI interface."""
        print("\n=== Starting TTS audio file generation test ===")

        output_file = tmp_path / "test_output.wav"
        test_text = "This is a test of audio file generation."

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "test",
            "--test-type",
            "tts-audio-file",
            "--test-text",
            test_text,
            "--output-audio-file",
            str(output_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        assert process.returncode == 0
        assert output_file.exists()
        assert output_file.stat().st_size > 0

    async def test_talk_mode_start(self):
        """Test that talk mode starts successfully."""
        print("\n=== Starting talk mode initialization test ===")

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "talk",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            # Give it a moment to initialize
            await asyncio.sleep(2)

            # Check if the process is running and initialized
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                pytest.fail(
                    f"Talk mode failed to start. Return code: {process.returncode}\nOutput: {stdout.decode()}\nError: {stderr.decode()}"
                )

            # Verify expected output in stdout
            stdout_data = await process.stdout.read(1024)
            output = stdout_data.decode()

            assert "Gaia CLI client initialized" in output

        finally:
            # Clean up
            process.terminate()
            await process.communicate()

    async def test_talk_mode_with_no_tts(self):
        """Test that talk mode starts successfully."""
        print("\n=== Starting talk mode initialization test ===")

        process = await asyncio.create_subprocess_exec(
            "gaia-cli",
            "talk",
            "--no-tts",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            # Give it a moment to initialize
            await asyncio.sleep(2)

            # Check if the process is running and initialized
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                pytest.fail(
                    f"Talk mode failed to start. Return code: {process.returncode}\nOutput: {stdout.decode()}\nError: {stderr.decode()}"
                )

            # Verify expected output in stdout
            stdout_data = await process.stdout.read(1024)
            output = stdout_data.decode()

            assert "Gaia CLI client initialized" in output

        finally:
            # Clean up
            process.terminate()
            await process.communicate()


# Main function to run all tests
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hybrid",
        action="store_true",
        default=False,
        help="Use hybrid backend (pass flag to enable)",
    )
    args, remaining_args = parser.parse_known_args()

    # Create a list of pytest arguments
    pytest_args = [
        __file__,
        "-vv",
        "-s",
        "--asyncio-mode=auto",
        "--capture=no",
        "--log-cli-level=INFO",
    ]

    # Only add hybrid flag if explicitly set to True
    if args.hybrid:
        pytest_args.append("--hybrid")

    # Run pytest with the constructed arguments and exit with its return code
    exit_code = pytest.main(pytest_args)
    print(f"pytest exit code: {exit_code}")
    sys.exit(exit_code)
