// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Integration tests for MCP (Model Context Protocol) connectivity.
// Tests real MCP server connection, tool discovery, and tool execution
// via the windows-mcp server (uvx windows-mcp).
//
// Requires:
//   - Windows (windows-mcp is a Windows MCP server)
//   - uvx installed (pip install uv)
//
// Env vars:
//   GAIA_CPP_TEST_MODEL  — model ID (default: Qwen3-4B-Instruct-2507-GGUF)
//   GAIA_CPP_BASE_URL    — LLM endpoint (default: http://localhost:8000/api/v1)

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/types.h>

#include <cstdlib>
#include <string>

#ifndef _WIN32
TEST(IntegrationMCP, SkipNonWindows) {
    GTEST_SKIP() << "MCP integration tests require Windows (windows-mcp)";
}
#else

// ---------------------------------------------------------------------------
// Env var helpers
// ---------------------------------------------------------------------------

static std::string testModel() {
#ifdef _MSC_VER
    char* env = nullptr;
    size_t len = 0;
    _dupenv_s(&env, &len, "GAIA_CPP_TEST_MODEL");
    std::string result = env ? std::string(env) : "Qwen3-4B-Instruct-2507-GGUF";
    free(env);
    return result;
#else
    const char* env = std::getenv("GAIA_CPP_TEST_MODEL");
    return env ? std::string(env) : "Qwen3-4B-Instruct-2507-GGUF";
#endif
}

static std::string testBaseUrl() {
#ifdef _MSC_VER
    char* env = nullptr;
    size_t len = 0;
    _dupenv_s(&env, &len, "GAIA_CPP_BASE_URL");
    std::string result = env ? std::string(env) : "http://localhost:8000/api/v1";
    free(env);
    return result;
#else
    const char* env = std::getenv("GAIA_CPP_BASE_URL");
    return env ? std::string(env) : "http://localhost:8000/api/v1";
#endif
}

// ---------------------------------------------------------------------------
// Minimal agent for MCP testing — no custom tools, just MCP connection
// ---------------------------------------------------------------------------

class McpTestAgent : public gaia::Agent {
public:
    explicit McpTestAgent() : Agent(makeConfig()) { init(); }
    ~McpTestAgent() override { disconnectAllMcp(); }

protected:
    std::string getSystemPrompt() const override { return "Test agent for MCP."; }

private:
    static gaia::AgentConfig makeConfig() {
        gaia::AgentConfig cfg;
        cfg.baseUrl    = testBaseUrl();
        cfg.modelId    = testModel();
        cfg.maxSteps   = 1;
        cfg.silentMode = true;
        return cfg;
    }
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

TEST(IntegrationMCP, ConnectsToWindowsMcp) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected) << "Failed to connect to Windows MCP server (uvx windows-mcp)";
}

TEST(IntegrationMCP, DiscoversShellTool) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected);

    // windows-mcp should expose at least Shell (case may vary by version)
    EXPECT_FALSE(agent.tools().resolveName("mcp_windows_Shell").empty())
        << "Expected mcp_windows_Shell (or shell) tool to be discovered";
}

TEST(IntegrationMCP, DiscoversMultipleTools) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected);

    // windows-mcp typically exposes Shell, Wait, Shortcut, etc.
    size_t toolCount = agent.tools().size();
    EXPECT_GE(toolCount, 2)
        << "Expected at least 2 MCP tools, got: " << toolCount;
}

TEST(IntegrationMCP, DisconnectAndReconnect) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected);
    ASSERT_FALSE(agent.tools().resolveName("mcp_windows_Shell").empty());

    // Disconnect
    agent.disconnectMcpServer("windows");

    // Reconnect
    bool reconnected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(reconnected) << "Failed to reconnect to Windows MCP server";
    EXPECT_FALSE(agent.tools().resolveName("mcp_windows_Shell").empty())
        << "Expected mcp_windows_Shell (or shell) after reconnect";
}

TEST(IntegrationMCP, SystemPromptIncludesMcpTools) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected);

    // After MCP connect, rebuildSystemPrompt should include MCP tool descriptions.
    // Resolve the actual registered name (case may vary by windows-mcp version).
    std::string prompt = agent.systemPrompt();
    std::string resolvedShell = agent.tools().resolveName("mcp_windows_Shell");
    ASSERT_FALSE(resolvedShell.empty()) << "Expected mcp_windows_Shell (or shell) to be registered";
    EXPECT_NE(prompt.find(resolvedShell), std::string::npos)
        << "Expected system prompt to contain " << resolvedShell << " tool description";
}

#endif // _WIN32
