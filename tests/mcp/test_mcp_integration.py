#!/usr/bin/env python
#
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Test MCP Bridge Integration for Jira Agent."""

import json
import sys
import urllib.error
import urllib.request

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def test_mcp_bridge():
    """Test MCP bridge connectivity and Jira integration."""

    base_url = "http://localhost:8765"

    print("=" * 60)
    print("Testing MCP Bridge Integration")
    print("=" * 60)

    # Test 1: Check MCP bridge is running
    print("\n1. Testing MCP bridge connectivity at http://localhost:8765...")
    try:
        with urllib.request.urlopen(f"{base_url}/health") as response:
            health_data = json.loads(response.read().decode("utf-8"))
            if health_data.get("status") == "healthy":
                print("✓ Connected to MCP bridge")
                print(
                    f"   Agents: {health_data.get('agents', 0)}, Tools: {health_data.get('tools', 0)}"
                )
            else:
                print("✗ MCP bridge unhealthy")
                return None

    except Exception as e:
        print(f"✗ Failed to connect: {e}")
        return None

    # Test 2: Send an MCP-style request
    print("\n2. Testing MCP message via JSON-RPC...")
    # Use a simple tool that doesn't require auth
    mcp_request = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "tools/list",
        "params": {},
    }

    try:
        req_data = json.dumps(mcp_request).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read().decode("utf-8"))
            print("✓ Sent MCP request")
            print("✓ Received MCP response")

            # Check response structure
            if "result" in response_data:
                print("✓ Response has 'result' field")
                result = response_data["result"]
                if isinstance(result, dict) and "tools" in result:
                    print("✓ Response has proper MCP structure")
                    print(f"   Found {len(result['tools'])} tools")
            elif "error" in response_data:
                print(f"✗ Error in response: {response_data['error']}")

            return response_data

    except Exception as e:
        print(f"✗ Failed to send request: {e}")
        return None


def main():
    result = test_mcp_bridge()

    print("\n" + "=" * 60)
    if result:
        print("MCP Bridge Test Result:")
        print(
            json.dumps(result, indent=2)[:500] + "..."
            if len(json.dumps(result)) > 500
            else json.dumps(result, indent=2)
        )
        print("=" * 60)
        return True
    else:
        print("MCP Bridge Test Failed - Make sure 'gaia mcp start' is running")
        print("=" * 60)
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
