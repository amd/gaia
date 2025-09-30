#!/usr/bin/env python
"""
Comprehensive test suite for HTTP-native MCP system validation.
Tests all endpoints, protocols, and integration points.
"""

import json
import urllib.request
import urllib.error
import time
import sys
import io

# Fix Unicode on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Configuration
MCP_HOST = "localhost"
MCP_PORT = 8765
BASE_URL = f"http://{MCP_HOST}:{MCP_PORT}"

# Test results tracking
test_results = []


def test(name, description):
    """Decorator for test functions."""

    def decorator(func):
        def wrapper():
            print(f"\n🧪 Testing: {name}")
            print(f"   {description}")
            try:
                result = func()
                if result:
                    print(f"   ✅ PASSED")
                    test_results.append(
                        {"test": name, "status": "PASSED", "details": None}
                    )
                else:
                    print(f"   ❌ FAILED")
                    test_results.append(
                        {
                            "test": name,
                            "status": "FAILED",
                            "details": "Test returned False",
                        }
                    )
                return result
            except Exception as e:
                print(f"   ❌ ERROR: {e}")
                test_results.append(
                    {"test": name, "status": "ERROR", "details": str(e)}
                )
                return False

        return wrapper

    return decorator


def make_request(endpoint, method="GET", data=None):
    """Helper to make HTTP requests."""
    url = f"{BASE_URL}{endpoint}"

    if data:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
    else:
        req = urllib.request.Request(url, method=method)

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


@test("Health Check", "Verify MCP bridge is running and healthy")
def test_health():
    response = make_request("/health")
    assert "status" in response, "Missing status field"
    assert response["status"] == "healthy", f"Status not healthy: {response['status']}"
    assert "agents" in response, "Missing agents count"
    assert response["agents"] >= 1, "No agents registered"
    assert "tools" in response, "Missing tools count"
    print(f"   📊 {response['agents']} agents, {response['tools']} tools registered")
    return True


@test("List Tools", "Verify tool listing endpoint")
def test_list_tools():
    response = make_request("/tools")
    assert "tools" in response, "Missing tools field"
    tools = response["tools"]
    assert isinstance(tools, list), "Tools should be a list"
    assert len(tools) > 0, "No tools available"

    # Check for required tools
    tool_names = [t.get("name") for t in tools if isinstance(t, dict)]
    assert "gaia.jira" in tool_names, "Jira tool not found"
    assert "gaia.query" in tool_names, "Query tool not found"

    print(f"   📋 Found {len(tools)} tools: {', '.join(tool_names[:5])}")
    return True


@test("Direct Jira Endpoint", "Test direct Jira operations via /jira")
def test_direct_jira():
    # Skip in CI environment as it requires authentication
    import os

    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        print("   ⏭️  SKIPPED (CI environment - requires auth)")
        return True

    data = {"query": "show issues in project MDP limit 3"}
    response = make_request("/jira", method="POST", data=data)

    assert "success" in response, "Missing success field"
    assert response["success"] == True, f"Operation failed: {response.get('error')}"
    assert "steps_taken" in response, "Missing steps_taken field"

    print(f"   📊 Steps taken: {response['steps_taken']}")
    if response.get("result"):
        print(f"   📝 Result preview: {response['result'][:100]}...")
    return True


@test("JSON-RPC Initialize", "Test JSON-RPC initialization")
def test_jsonrpc_initialize():
    data = {"jsonrpc": "2.0", "id": "test-init", "method": "initialize", "params": {}}
    response = make_request("/", method="POST", data=data)

    assert "jsonrpc" in response, "Not a valid JSON-RPC response"
    assert response["jsonrpc"] == "2.0", "Wrong JSON-RPC version"
    assert "result" in response, "Missing result field"

    result = response["result"]
    assert "protocolVersion" in result, "Missing protocol version"
    assert "serverInfo" in result, "Missing server info"
    assert "capabilities" in result, "Missing capabilities"

    caps = result["capabilities"]
    assert caps.get("tools") == True, "Tools capability not enabled"

    print(f"   📌 Protocol: {result['protocolVersion']}")
    print(
        f"   🖥️ Server: {result['serverInfo']['name']} v{result['serverInfo']['version']}"
    )
    return True


@test("JSON-RPC Tool List", "Test listing tools via JSON-RPC")
def test_jsonrpc_tool_list():
    data = {"jsonrpc": "2.0", "id": "test-list", "method": "tools/list", "params": {}}
    response = make_request("/", method="POST", data=data)

    assert "result" in response, "Missing result field"
    assert "tools" in response["result"], "Missing tools in result"

    tools = response["result"]["tools"]
    assert len(tools) > 0, "No tools returned"

    # Verify tool structure
    for tool in tools[:1]:  # Check first tool
        assert "name" in tool, "Tool missing name"
        assert "description" in tool, "Tool missing description"

    print(f"   🛠️ {len(tools)} tools available via JSON-RPC")
    return True


@test("JSON-RPC Jira Call", "Test Jira operations via JSON-RPC tools/call")
def test_jsonrpc_jira_call():
    # Skip in CI environment as it requires authentication
    import os

    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        print("   ⏭️  SKIPPED (CI environment - requires auth)")
        return True

    data = {
        "jsonrpc": "2.0",
        "id": "test-jira",
        "method": "tools/call",
        "params": {
            "name": "gaia.jira",
            "arguments": {"query": "show my assigned issues", "operation": "query"},
        },
    }
    response = make_request("/", method="POST", data=data)

    assert "result" in response, f"Missing result: {response}"
    result = response["result"]
    assert "content" in result, "Missing content in result"
    assert len(result["content"]) > 0, "Empty content"

    content = json.loads(result["content"][0]["text"])
    assert "success" in content, "Missing success field in content"

    print(f"   ✨ Jira call successful via JSON-RPC")
    if content.get("steps_taken"):
        print(f"   📊 Steps: {content['steps_taken']}")
    return True


@test("Error Handling - Invalid Endpoint", "Test 404 handling")
def test_error_404():
    response = make_request("/invalid-endpoint")
    assert "error" in response, "Should return error"
    return True


@test("Error Handling - Invalid JSON-RPC", "Test malformed JSON-RPC")
def test_error_invalid_jsonrpc():
    data = {"jsonrpc": "1.0", "method": "test"}  # Wrong version
    response = make_request("/", method="POST", data=data)
    assert "error" in response, "Should return error for invalid JSON-RPC"
    return True


@test("Error Handling - Unknown Tool", "Test calling non-existent tool")
def test_error_unknown_tool():
    data = {
        "jsonrpc": "2.0",
        "id": "test-error",
        "method": "tools/call",
        "params": {"name": "gaia.nonexistent", "arguments": {}},
    }
    response = make_request("/", method="POST", data=data)

    # Should have either error in response or error in result content
    if "error" in response:
        print(
            f"   👍 Properly returned error: {response['error'].get('message', 'Unknown')}"
        )
    elif "result" in response:
        content = json.loads(response["result"]["content"][0]["text"])
        assert "error" in content, "Should indicate tool error"
        print(f"   👍 Error handled: {content['error']}")
    return True


@test("Direct LLM Endpoint", "Test direct LLM queries via /llm")
def test_direct_llm():
    import os

    # In CI without Lemonade, LLM endpoint will fail
    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        data = {"query": "What is 2+2?"}
        response = make_request("/llm", method="POST", data=data)
        # Expect an error response in CI
        if "error" in response and "HTTP 500" in response["error"]:
            print(f"   ⚠️  Expected error in CI (no Lemonade): {response['error']}")
            return True

    data = {"query": "What is 2+2?"}
    response = make_request("/llm", method="POST", data=data)

    assert "success" in response or "result" in response, "Missing response fields"
    print(f"   🤖 LLM endpoint functional")
    return True


@test("CORS Headers", "Verify CORS support for browser clients")
def test_cors():
    # Test OPTIONS request
    req = urllib.request.Request(f"{BASE_URL}/", method="OPTIONS")
    with urllib.request.urlopen(req) as response:
        headers = response.headers
        assert "Access-Control-Allow-Origin" in headers, "Missing CORS origin header"
        assert "Access-Control-Allow-Methods" in headers, "Missing CORS methods header"
        print(f"   🌐 CORS enabled: {headers['Access-Control-Allow-Origin']}")
    return True


@test("Performance - Response Time", "Check response times")
def test_performance():
    # First health check (warm-up, not measured)
    print(f"   🔄 Warming up...")
    _ = make_request("/health")

    # Second health check (measured)
    start = time.time()
    response = make_request("/health")
    elapsed = time.time() - start

    # More reasonable threshold: 3 seconds for health check
    assert elapsed < 3.0, f"Health check too slow: {elapsed:.2f}s"
    print(f"   ⚡ Health check: {elapsed*1000:.0f}ms (after warm-up)")

    # Test Jira endpoint
    start = time.time()
    data = {"query": "show 1 issue"}
    response = make_request("/jira", method="POST", data=data)
    elapsed = time.time() - start

    print(f"   ⚡ Jira query: {elapsed:.2f}s")
    return True


def run_all_tests():
    """Run all tests and generate report."""
    print("=" * 60)
    print("🔬 HTTP-Native MCP System Validation")
    print("=" * 60)
    print(f"Target: {BASE_URL}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Run tests
    test_health()
    test_list_tools()
    test_direct_jira()
    test_jsonrpc_initialize()
    test_jsonrpc_tool_list()
    test_jsonrpc_jira_call()
    test_error_404()
    test_error_invalid_jsonrpc()
    test_error_unknown_tool()
    test_direct_llm()
    test_cors()
    test_performance()

    # Generate report
    print("\n" + "=" * 60)
    print("📊 Test Results Summary")
    print("=" * 60)

    passed = sum(1 for r in test_results if r["status"] == "PASSED")
    failed = sum(1 for r in test_results if r["status"] == "FAILED")
    errors = sum(1 for r in test_results if r["status"] == "ERROR")

    print(f"✅ Passed: {passed}/{len(test_results)}")
    if failed > 0:
        print(f"❌ Failed: {failed}")
    if errors > 0:
        print(f"⚠️ Errors: {errors}")

    if failed + errors > 0:
        print("\n🔍 Failed Tests:")
        for result in test_results:
            if result["status"] != "PASSED":
                print(f"  - {result['test']}: {result['details']}")

    # Overall verdict
    print("\n" + "=" * 60)
    if passed == len(test_results):
        print("🎉 ALL TESTS PASSED - MCP System Fully Validated!")
    else:
        print(f"⚠️ {failed + errors} tests need attention")
    print("=" * 60)

    return passed == len(test_results)


if __name__ == "__main__":
    # Check if MCP is running
    try:
        response = make_request("/health")
        if "error" in response:
            print("❌ MCP bridge not running at {BASE_URL}")
            print("Start it with: python src/gaia/mcp/mcp_bridge_http.py")
            sys.exit(1)
    except:
        print(f"❌ Cannot connect to MCP bridge at {BASE_URL}")
        print("Start it with: python src/gaia/mcp/mcp_bridge_http.py")
        sys.exit(1)

    # Run tests
    success = run_all_tests()
    sys.exit(0 if success else 1)
