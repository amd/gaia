#!/usr/bin/env python
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Simple test to validate the MCP HTTP bridge is working.
Run this after starting the bridge with: gaia mcp start
"""

import io
import json
import sys
import urllib.error
import urllib.request

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def test_mcp_bridge():
    """Simple validation test for MCP bridge."""
    base_url = "http://localhost:8765"

    print("🔍 Testing MCP HTTP Bridge...")
    print("-" * 40)

    # Test 1: Health Check
    print("1. Health Check... ", end="")
    try:
        with urllib.request.urlopen(f"{base_url}/health") as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "healthy":
                print(
                    f"✅ PASSED ({data.get('agents', 0)} agents, {data.get('tools', 0)} tools)"
                )
            else:
                print("❌ FAILED - Unhealthy status")
                return False
    except Exception as e:
        print(f"❌ FAILED - {e}")
        return False

    # Test 2: List Tools
    print("2. List Tools... ", end="")
    try:
        with urllib.request.urlopen(f"{base_url}/tools") as response:
            data = json.loads(response.read().decode("utf-8"))
            tools = data.get("tools", [])
            if len(tools) > 0:
                print(f"✅ PASSED ({len(tools)} tools found)")
            else:
                print("❌ FAILED - No tools found")
                return False
    except Exception as e:
        print(f"❌ FAILED - {e}")
        return False

    # Test 3: Jira Endpoint
    print("3. Jira Endpoint... ", end="")
    try:
        req_data = json.dumps({"query": "show 1 issue"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/jira",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("success") or "result" in data:
                print("✅ PASSED")
            else:
                print(f"❌ FAILED - {data.get('error', 'Unknown error')}")
                return False
    except urllib.error.HTTPError as e:
        # In CI without Jira credentials, endpoint may return 500
        import os

        if (
            os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        ) and e.code == 500:
            print("⚠️  EXPECTED (No Jira credentials in CI)")
        else:
            print(f"❌ FAILED - HTTP Error {e.code}: {e.reason}")
            return False
    except Exception as e:
        print(f"❌ FAILED - {e}")
        return False

    # Test 4: LLM Endpoint
    print("4. LLM Endpoint... ", end="")
    try:
        req_data = json.dumps({"query": "What is 2+2?"}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/llm",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            if "result" in data or "response" in data:
                print("✅ PASSED")
            else:
                print(f"❌ FAILED - {data.get('error', 'No response')}")
                return False
    except urllib.error.HTTPError as e:
        # In CI without Lemonade, LLM endpoint returns 500
        import os

        if (
            os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
        ) and e.code == 500:
            print("⚠️  EXPECTED (No Lemonade in CI)")
        else:
            print(f"❌ FAILED - HTTP Error {e.code}: {e.reason}")
            return False
    except Exception as e:
        print(f"❌ FAILED - {e}")
        return False

    print("-" * 40)
    print("✨ All tests passed! MCP bridge is working correctly.")
    return True


if __name__ == "__main__":
    # Check if bridge is running
    try:
        with urllib.request.urlopen("http://localhost:8765/health") as response:
            pass
    except:
        print("❌ MCP bridge not running at http://localhost:8765")
        print("Start it with: gaia mcp start")
        sys.exit(1)

    # Run tests
    success = test_mcp_bridge()
    sys.exit(0 if success else 1)
