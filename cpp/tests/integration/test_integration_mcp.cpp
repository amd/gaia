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

#include <algorithm>
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
// Helpers
// ---------------------------------------------------------------------------

// Build a comma-separated list of all registered tool names (for diagnostics).
static std::string listToolNames(const gaia::ToolRegistry& tools) {
    std::string out;
    for (const auto& [name, _] : tools.allTools()) {
        if (!out.empty()) out += ", ";
        out += name;
    }
    return out.empty() ? "(none)" : out;
}

// Find a command/shell execution tool from windows-mcp.
// Tool names vary by version (Shell, shell, PowerShell, cmd, execute, run, …).
// Returns the first matching tool name, or empty string if none found.
static std::string findShellTool(const gaia::ToolRegistry& tools) {
    // Try exact-case-insensitive match for the canonical name first
    std::string resolved = tools.resolveName("mcp_windows_Shell");
    if (!resolved.empty()) return resolved;

    // Fallback: scan all tools for shell/command-related substrings
    for (const auto& [name, _] : tools.allTools()) {
        std::string lower = name;
        std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);
        if (lower.find("shell")   != std::string::npos ||
            lower.find("cmd")     != std::string::npos ||
            lower.find("command") != std::string::npos ||
            lower.find("power")   != std::string::npos ||
            lower.find("execute") != std::string::npos ||
            lower.find("run")     != std::string::npos) {
            return name;
        }
    }
    return "";
}

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

    // windows-mcp should expose a command/shell execution tool.
    // Tool name varies by package version — use flexible search.
    std::string shellTool = findShellTool(agent.tools());
    EXPECT_FALSE(shellTool.empty())
        << "Expected a shell/command tool from windows-mcp. "
        << "Available tools: [" << listToolNames(agent.tools()) << "]";
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

    // Pick the first discovered tool to verify persistence across reconnect.
    ASSERT_FALSE(agent.tools().allTools().empty())
        << "Expected at least one tool after connect";
    std::string testTool = agent.tools().allTools().begin()->second.name;
    ASSERT_TRUE(agent.tools().hasTool(testTool));

    // Disconnect
    agent.disconnectMcpServer("windows");

    // Reconnect
    bool reconnected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(reconnected) << "Failed to reconnect to Windows MCP server";
    EXPECT_TRUE(agent.tools().hasTool(testTool))
        << "Expected '" << testTool << "' to remain registered after reconnect";
}

TEST(IntegrationMCP, SystemPromptIncludesMcpTools) {
    McpTestAgent agent;
    bool connected = agent.connectMcpServer("windows", {
        {"command", "uvx"},
        {"args", {"windows-mcp"}}
    });
    ASSERT_TRUE(connected);

    // Pick the first discovered tool and verify it appears in the system prompt.
    ASSERT_FALSE(agent.tools().allTools().empty())
        << "Expected at least one tool after connect";
    std::string firstTool = agent.tools().allTools().begin()->second.name;

    std::string prompt = agent.systemPrompt();
    EXPECT_NE(prompt.find(firstTool), std::string::npos)
        << "Expected system prompt to contain '" << firstTool << "' tool description. "
        << "Available tools: [" << listToolNames(agent.tools()) << "]";
}

#endif // _WIN32
