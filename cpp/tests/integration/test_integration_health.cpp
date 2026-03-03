// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Integration tests for System Health Agent use-cases.
// Tests the full stack: LLM reasoning + MCP transport + windows-mcp Shell tool.
// Mirrors the real health_agent architecture: connectMcpServer("windows", ...)
// auto-discovers mcp_windows_Shell and the LLM calls it to run PowerShell.
//
// Requires:
//   - Windows (PowerShell + windows-mcp)
//   - uvx installed (pip install uv)
//   - lemonade-server running with the test model loaded
//
// Env vars:
//   GAIA_CPP_TEST_MODEL  — model ID (default: Qwen3-4B-Instruct-2507-GGUF)
//   GAIA_CPP_BASE_URL    — LLM endpoint (default: http://localhost:8000/api/v1)

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/types.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <string>

#ifndef _WIN32
TEST(IntegrationHealth, SkipNonWindows) {
    GTEST_SKIP() << "Health integration tests require Windows";
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

static std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

static gaia::AgentConfig healthTestConfig(int maxSteps = 3) {
    gaia::AgentConfig cfg;
    cfg.baseUrl    = testBaseUrl();
    cfg.modelId    = testModel();
    cfg.maxSteps   = maxSteps;
    cfg.silentMode = true;
    return cfg;
}

// ---------------------------------------------------------------------------
// Health Test Agent — uses MCP (windows-mcp) just like the real health_agent
// connectMcpServer auto-discovers mcp_windows_Shell, mcp_windows_Wait, etc.
// The LLM calls mcp_windows_Shell with PowerShell commands.
// ---------------------------------------------------------------------------

class HealthTestAgent : public gaia::Agent {
public:
    explicit HealthTestAgent(int maxSteps = 3)
        : Agent(healthTestConfig(maxSteps)) {
        init();

        // Connect to Windows MCP server — same as health_agent.cpp
        bool ok = connectMcpServer("windows", {
            {"command", "uvx"},
            {"args", {"windows-mcp"}}
        });
        if (!ok) {
            throw std::runtime_error("Failed to connect to Windows MCP server (uvx windows-mcp)");
        }
    }

    ~HealthTestAgent() override {
        disconnectAllMcp();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are a system health diagnostic assistant using the Windows MCP server.
Use the mcp_windows_Shell tool to execute PowerShell commands and gather system information.
IMPORTANT: You MUST call mcp_windows_Shell to get real data. Do not guess or make up system information.

Available PowerShell commands:
- Memory: Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json
- CPU: Get-CimInstance Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json
- Disk: Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json
- GPU: Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion, VideoProcessor | ConvertTo-Json

When asked to check something, call mcp_windows_Shell with the appropriate command, then summarize the result concisely.)";
    }
};

// ---------------------------------------------------------------------------
// Test fixture — skips all tests if MCP connection fails
// ---------------------------------------------------------------------------

class IntegrationHealthTest : public ::testing::Test {
protected:
    std::unique_ptr<HealthTestAgent> agent;

    void SetUp() override {
        agent = std::make_unique<HealthTestAgent>();
    }

    void TearDown() override {
        agent.reset();
    }
};

// ---------------------------------------------------------------------------
// Tests — LLM + MCP + real PowerShell
// ---------------------------------------------------------------------------

TEST_F(IntegrationHealthTest, CheckMemory) {
    auto result = agent->processQuery(
        "Check the system memory using mcp_windows_Shell. "
        "How much total RAM do I have and how much is free?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    std::string lower = toLower(answer);
    bool hasMemInfo = lower.find("gb") != std::string::npos
                   || lower.find("memory") != std::string::npos
                   || lower.find("ram") != std::string::npos;
    EXPECT_TRUE(hasMemInfo) << "Expected memory info in answer, got: " << answer;
}

TEST_F(IntegrationHealthTest, CheckCpu) {
    auto result = agent->processQuery(
        "Check the CPU information using mcp_windows_Shell. "
        "What processor do I have and how many cores?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    std::string lower = toLower(answer);
    bool hasCpuInfo = lower.find("core") != std::string::npos
                   || lower.find("processor") != std::string::npos
                   || lower.find("cpu") != std::string::npos
                   || lower.find("amd") != std::string::npos
                   || lower.find("intel") != std::string::npos
                   || lower.find("ryzen") != std::string::npos;
    EXPECT_TRUE(hasCpuInfo) << "Expected CPU info in answer, got: " << answer;
}

TEST_F(IntegrationHealthTest, CheckDisk) {
    auto result = agent->processQuery(
        "Check the disk space using mcp_windows_Shell. "
        "How much free space is on each drive?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    std::string lower = toLower(answer);
    bool hasDiskInfo = lower.find("gb") != std::string::npos
                    || lower.find("drive") != std::string::npos
                    || lower.find("disk") != std::string::npos
                    || lower.find("c:") != std::string::npos
                    || lower.find("free") != std::string::npos;
    EXPECT_TRUE(hasDiskInfo) << "Expected disk info in answer, got: " << answer;
}

#endif // _WIN32
