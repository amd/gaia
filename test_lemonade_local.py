#!/usr/bin/env python3
"""
Local test script to verify lemonade-sdk installation and basic functionality.
Run this to validate lemonade server works before running CI tests.

Usage:
    python test_lemonade_local.py
"""

import os
import subprocess
import sys
import time
from importlib.metadata import version as get_version
from pathlib import Path

import requests


def check_installation():
    """Check if lemonade-sdk is installed and lemonade-server-dev exists."""
    print("=" * 60)
    print("CHECKING LEMONADE INSTALLATION")
    print("=" * 60)

    # Check if lemonade-sdk is installed (using importlib.metadata for uv venv compatibility)
    try:
        lemonade_version = get_version("lemonade-sdk")
        print("✅ lemonade-sdk is installed")
        print(f"   Version: {lemonade_version}")
    except Exception as e:
        print("❌ lemonade-sdk is NOT installed")
        print(f"   Error: {e}")
        print(f"   Run: uv pip install '.[lemonade]'")
        return False

    # Check if lemonade-server-dev executable exists
    venv_scripts = Path(".venv/Scripts") if os.name == "nt" else Path(".venv/bin")
    lemonade_exe = venv_scripts / ("lemonade-server-dev.exe" if os.name == "nt" else "lemonade-server-dev")

    if lemonade_exe.exists():
        print(f"✅ Found lemonade-server-dev at: {lemonade_exe}")
    else:
        print(f"❌ lemonade-server-dev NOT FOUND at: {lemonade_exe}")
        print(f"   Files in {venv_scripts}:")
        if venv_scripts.exists():
            for f in venv_scripts.glob("*lemon*"):
                print(f"     - {f.name}")
        return False

    print()
    return True


def start_server(lemonade_exe):
    """Start lemonade-server-dev in background."""
    print("=" * 60)
    print("STARTING LEMONADE SERVER")
    print("=" * 60)

    # Set environment variable
    env = os.environ.copy()
    env["GGML_VK_DISABLE_COOPMAT"] = "1"

    # Start server
    print(f"Running: {lemonade_exe} serve --port 8000 --ctx-size 4096 --no-tray")

    try:
        process = subprocess.Popen(
            [str(lemonade_exe), "serve", "--port", "8000", "--ctx-size", "4096", "--no-tray"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )
        print(f"✅ Server started with PID: {process.pid}")
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
        return None

    # Wait for server to be ready
    print("Waiting for server to start...")
    max_wait = 30
    waited = 0
    ready = False

    while waited < max_wait and not ready:
        time.sleep(2)
        waited += 2
        try:
            response = requests.get("http://localhost:8000/api/v1/health", timeout=5)
            if response.status_code == 200:
                print(f"✅ Server ready (waited {waited}s)")
                print(f"   Health: {response.json()}")
                ready = True
        except:
            print(f"   Waiting... ({waited}/{max_wait}s)")

    if not ready:
        print(f"❌ Server failed to start within {max_wait}s")
        print("\n--- Server STDOUT ---")
        if process.stdout:
            print(process.stdout.read())
        print("\n--- Server STDERR ---")
        if process.stderr:
            print(process.stderr.read())
        process.kill()
        return None

    print()
    return process


def pull_model_cli(lemonade_exe, model_name):
    """Pull model using CLI."""
    print("=" * 60)
    print(f"PULLING MODEL VIA CLI: {model_name}")
    print("=" * 60)

    print(f"Running: {lemonade_exe} pull {model_name}")
    try:
        result = subprocess.run(
            [str(lemonade_exe), "pull", model_name],
            capture_output=True,
            text=True,
            timeout=300
        )
        print(f"Exit code: {result.returncode}")
        if result.stdout:
            print("STDOUT:")
            print(result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr)

        if result.returncode != 0:
            print(f"❌ CLI pull failed with exit code {result.returncode}")
            return False

        print("✅ Model pulled successfully via CLI")
    except subprocess.TimeoutExpired:
        print("❌ Pull timed out after 5 minutes")
        return False
    except Exception as e:
        print(f"❌ Pull failed: {e}")
        return False

    print()
    return True


def pull_model_api(model_name, port=8000):
    """Pull model using API."""
    print("=" * 60)
    print(f"PULLING MODEL VIA API: {model_name}")
    print("=" * 60)

    try:
        print(f"POST http://localhost:{port}/api/v1/pull")
        response = requests.post(
            f"http://localhost:{port}/api/v1/pull",
            json={"model_name": model_name},
            timeout=600
        )

        print(f"Status code: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code != 200:
            print(f"❌ API pull failed with status {response.status_code}")
            return False

        print("✅ Model pulled successfully via API")
    except Exception as e:
        print(f"❌ API pull failed: {e}")
        return False

    print()
    return True


def test_server_alive(port=8000):
    """Quick check if server is still responsive."""
    try:
        response = requests.get(f"http://localhost:{port}/api/v1/health", timeout=5)
        if response.status_code == 200:
            print(f"✅ Server is alive: {response.json()}")
            return True
        else:
            print(f"❌ Server returned {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Server is not responding: {e}")
        return False


def load_model(model_name):
    """Load model via API."""
    print("=" * 60)
    print(f"LOADING MODEL: {model_name}")
    print("=" * 60)

    time.sleep(5)  # Wait for files to sync

    try:
        response = requests.post(
            "http://localhost:8000/api/v1/load",
            json={"model_name": model_name},
            timeout=120
        )

        print(f"Status code: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code != 200:
            print(f"❌ Load failed with status {response.status_code}")
            return False

        print("✅ Model loaded successfully")
    except Exception as e:
        print(f"❌ Load failed: {e}")
        return False

    print()
    return True


def test_completion(model_name):
    """Test a simple completion."""
    print("=" * 60)
    print("TESTING COMPLETION")
    print("=" * 60)

    time.sleep(10)  # Wait for initialization

    try:
        response = requests.post(
            "http://localhost:8000/api/v1/completions",
            json={
                "model": model_name,
                "prompt": "Say exactly: Hello World",
                "max_tokens": 10
            },
            timeout=30
        )

        print(f"Status code: {response.status_code}")

        if response.status_code != 200:
            print(f"❌ Completion failed with status {response.status_code}")
            print(f"   Response: {response.text}")
            return False

        result = response.json()
        text = result.get("choices", [{}])[0].get("text", "")
        print(f"✅ Completion successful")
        print(f"   Response: {text}")
    except Exception as e:
        print(f"❌ Completion failed: {e}")
        return False

    print()
    return True


def main():
    """Run all tests."""
    model_name = "Qwen3-0.6B-GGUF"
    venv_scripts = Path(".venv/Scripts") if os.name == "nt" else Path(".venv/bin")
    lemonade_exe = venv_scripts / ("lemonade-server-dev.exe" if os.name == "nt" else "lemonade-server-dev")

    server_process = None
    test_api_pull = input("\nTest API pull (may crash server)? [y/N]: ").strip().lower() == 'y'

    try:
        # 1. Check installation
        if not check_installation():
            print("\n❌ FAILED: Installation check failed")
            return 1

        # 2. Start server
        server_process = start_server(lemonade_exe)
        if not server_process:
            print("\n❌ FAILED: Could not start server")
            return 1

        # 3. Pull model
        if test_api_pull:
            print("\n⚠️  Testing API pull (this may crash the server)...")
            if not pull_model_api(model_name):
                print("\n❌ FAILED: API pull failed")
                return 1

            print("\n🔍 Checking if server survived API pull...")
            if not test_server_alive():
                print("\n❌ CRITICAL: Server died after API pull!")
                print("   This is the root cause of CI failures.")
                return 1
        else:
            if not pull_model_cli(lemonade_exe, model_name):
                print("\n❌ FAILED: Could not pull model via CLI")
                return 1

        # 4. Load model
        if not load_model(model_name):
            print("\n❌ FAILED: Could not load model")
            return 1

        # 5. Test completion
        if not test_completion(model_name):
            print("\n❌ FAILED: Completion test failed")
            return 1

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        if test_api_pull:
            print("\n✅ API pull works locally - CI issue is environment-specific")
        else:
            print("\nLemonade server is working correctly with CLI pull.")
            print("Run again with 'y' to test if API pull crashes the server.")
        return 0

    finally:
        # Cleanup
        if server_process:
            print("\nCleaning up server process...")
            try:
                server_process.kill()
                print("✅ Server stopped")
            except:
                pass


if __name__ == "__main__":
    sys.exit(main())
