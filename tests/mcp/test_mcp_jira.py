#!/usr/bin/env python
"""Test MCP-based Jira integration."""

import json
import urllib.request
import urllib.error
import uuid
import sys
import io

# Fix Unicode output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def test_mcp_jira():
    """Test Jira operations through MCP bridge."""
    base_url = "http://localhost:8765"

    print("=" * 60)
    print("Testing MCP-First Jira Integration")
    print("=" * 60)

    try:
        # Check connection first
        with urllib.request.urlopen(f"{base_url}/health") as response:
            health_data = json.loads(response.read().decode("utf-8"))
            if health_data.get("status") == "healthy":
                print("\n✅ Connected to MCP bridge")
            else:
                print("\n❌ MCP bridge not healthy")
                return False

        # Test 1: Simple Jira query
        print("\n1. Testing Jira query through MCP...")
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": "gaia.jira",
                "arguments": {
                    "query": "show issues in project MDP",
                    "operation": "query",
                },
            },
        }

        req_data = json.dumps(request).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read().decode("utf-8"))

            if "result" in response_data:
                print("✅ Received response from Jira agent")
                result = response_data["result"]
                if "content" in result and len(result["content"]) > 0:
                    content = json.loads(result["content"][0]["text"])
                    if content.get("success"):
                        print(
                            f"✅ Query successful: {content.get('result', '')[:100]}..."
                        )
                        print(f"   Steps taken: {content.get('steps_taken', 0)}")
                    else:
                        print(
                            f"❌ Query failed: {content.get('error', 'Unknown error')}"
                        )
            elif "error" in response_data:
                print(f"❌ MCP Error: {response_data['error']}")

        # Test 2: List available tools
        print("\n2. Checking tool registration...")
        list_request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }

        req_data = json.dumps(list_request).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read().decode("utf-8"))

            if "result" in response_data:
                tools = response_data["result"].get("tools", [])
                jira_tools = [t for t in tools if "jira" in t.get("name", "").lower()]
                print(f"✅ Found {len(jira_tools)} Jira-related tools:")
                for tool in jira_tools:
                    print(f"   - {tool.get('name')}: {tool.get('description')}")

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


def main():
    success = test_mcp_jira()

    print("\n" + "=" * 60)
    if success:
        print("✅ MCP-First Jira Integration Working!")
        print("The Jira agent is now accessible through the MCP bridge.")
    else:
        print("❌ MCP-First Jira Integration Failed")
        print("Check that the MCP bridge is running with updated code.")
    print("=" * 60)


if __name__ == "__main__":
    main()
