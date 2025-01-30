# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import pytest
import asyncio
import json
import time
from pathlib import Path
import requests
import subprocess
import shlex


@pytest.mark.asyncio
class TestGaiaCLI:
    # Add class variable to control output printing
    print_server_output = True

    @pytest.fixture(scope="class", autouse=True)
    async def setup_and_teardown_class(cls):
        print("\n=== Starting Server ===")
        cmd = "gaia-cli start"
        print(f"Running command: {cmd}")

        try:
            # Use asyncio subprocess instead of subprocess.Popen
            cls.process = await asyncio.create_subprocess_exec(
                "gaia-cli",
                "start",
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

            # Start output monitoring in background
            asyncio.create_task(print_output())

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
            await asyncio.create_subprocess_exec("gaia-cli", "stop")
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


# Main function to run all tests
if __name__ == "__main__":
    pytest.main(
        [
            __file__,
            "-vv",
            "-s",
            "--asyncio-mode=auto",
            "--capture=no",
            "--log-cli-level=INFO",
        ]
    )
