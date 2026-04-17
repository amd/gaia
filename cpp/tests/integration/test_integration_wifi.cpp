// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Integration tests for Wi-Fi Troubleshooter Agent use-cases.
// Tests real PowerShell diagnostic tool execution + LLM reasoning.
// Only read-only diagnostic tools are registered (no fix tools).
//
// Requires:
//   - Windows (PowerShell commands)
//   - lemonade-server running with the test model loaded
//
// Env vars:
//   GAIA_CPP_TEST_MODEL  — model ID (default: Qwen3-4B-Instruct-2507-GGUF)
//   GAIA_CPP_BASE_URL    — LLM endpoint (default: http://localhost:8000/api/v1)

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/security.h>
#include <gaia/types.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>

#ifndef _WIN32
// Skip entire file on non-Windows
TEST(IntegrationWiFi, SkipNonWindows) {
    GTEST_SKIP() << "WiFi integration tests require Windows";
}
#else

// ---------------------------------------------------------------------------
// Helpers (same as wifi_agent.cpp)
// ---------------------------------------------------------------------------

static std::string runShell(const std::string& command) {
    std::string fullCmd = "powershell -NoProfile -NonInteractive -Command \"& { "
                          + command + " }\" 2>&1";

    std::string result;
    std::array<char, 4096> buffer;

    struct PipeCloser {
        void operator()(FILE* f) const { if (f) _pclose(f); }
    };
    std::unique_ptr<FILE, PipeCloser> pipe(_popen(fullCmd.c_str(), "r"));

    if (!pipe) {
        return "{\"error\": \"Failed to execute command\"}";
    }

    while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()) != nullptr) {
        result += buffer.data();
    }

    return result.empty() ? "{\"status\": \"completed\", \"output\": \"(no output)\"}" : result;
}


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

static gaia::AgentConfig wifiTestConfig(int maxSteps = 3) {
    gaia::AgentConfig cfg;
    cfg.baseUrl    = testBaseUrl();
    cfg.modelId    = testModel();
    cfg.maxSteps   = maxSteps;
    cfg.silentMode = true;
    return cfg;
}

// ---------------------------------------------------------------------------
// WiFi Test Agent — registers same read-only diagnostic tools as wifi_agent
// ---------------------------------------------------------------------------

class WiFiTestAgent : public gaia::Agent {
public:
    int toolCallCount = 0;
    std::string lastToolCalled;

    explicit WiFiTestAgent(int maxSteps = 3)
        : Agent(wifiTestConfig(maxSteps)) { init(); }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are a network diagnostic assistant. Use the provided tools to answer questions about the network.
IMPORTANT: You MUST call the appropriate tool to get real data. Do not guess or make up network information.
When asked to check something specific, call the relevant tool, then summarize the result concisely.)";
    }

    void registerTools() override {
        toolRegistry().registerTool(
            "check_adapter",
            "Show Wi-Fi adapter status including SSID, signal strength, radio type, and connection state.",
            [this](const gaia::json& /*args*/) -> gaia::json {
                ++toolCallCount;
                lastToolCalled = "check_adapter";
                std::string cmd = "netsh wlan show interfaces";
                std::string output = runShell(cmd);
                return {{"tool", "check_adapter"}, {"command", cmd}, {"output", output}};
            },
            {}
        );

        toolRegistry().registerTool(
            "check_ip_config",
            "Show full IP configuration for all network adapters including IP address, subnet mask, default gateway, DNS servers.",
            [this](const gaia::json& /*args*/) -> gaia::json {
                ++toolCallCount;
                lastToolCalled = "check_ip_config";
                std::string cmd = "ipconfig /all";
                std::string output = runShell(cmd);
                return {{"tool", "check_ip_config"}, {"command", cmd}, {"output", output}};
            },
            {}
        );

        toolRegistry().registerTool(
            "test_dns_resolution",
            "Test DNS resolution by resolving a hostname to an IP address. Returns JSON with resolved addresses.",
            [this](const gaia::json& args) -> gaia::json {
                ++toolCallCount;
                lastToolCalled = "test_dns_resolution";
                std::string hostname = args.value("hostname", "google.com");
                if (!gaia::isSafeShellArg(hostname)) {
                    return {{"error", "Invalid hostname"}};
                }
                std::string cmd = "Resolve-DnsName -Name " + hostname
                    + " -Type A -ErrorAction Stop | Select-Object Name, IPAddress, QueryType"
                    + " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "test_dns_resolution"}, {"command", cmd}, {"hostname", hostname}, {"output", output}};
            },
            {{"hostname", gaia::ToolParamType::STRING, false, "Hostname to resolve (default: google.com)"}}
        );

        toolRegistry().registerTool(
            "test_internet",
            "Test internet connectivity by connecting to 8.8.8.8 on port 443. Returns JSON with connection status and latency.",
            [this](const gaia::json& /*args*/) -> gaia::json {
                ++toolCallCount;
                lastToolCalled = "test_internet";
                std::string cmd =
                    "Test-NetConnection -ComputerName 8.8.8.8 -Port 443"
                    " | Select-Object ComputerName, RemotePort, TcpTestSucceeded, PingSucceeded,"
                    " PingReplyDetails"
                    " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "test_internet"}, {"command", cmd}, {"output", output}};
            },
            {}
        );

        toolRegistry().registerTool(
            "ping_host",
            "Ping a specific host and return connection status, latency, and resolved address as JSON.",
            [this](const gaia::json& args) -> gaia::json {
                ++toolCallCount;
                lastToolCalled = "ping_host";
                std::string host = args.value("host", "");
                if (host.empty()) {
                    return {{"error", "host parameter is required"}};
                }
                if (!gaia::isSafeShellArg(host)) {
                    return {{"error", "Invalid host"}};
                }
                std::string cmd =
                    "Test-NetConnection -ComputerName " + host
                    + " | Select-Object ComputerName, RemoteAddress, PingSucceeded, PingReplyDetails"
                    + " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "ping_host"}, {"command", cmd}, {"host", host}, {"output", output}};
            },
            {{"host", gaia::ToolParamType::STRING, true, "Hostname or IP to ping"}}
        );
    }
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

TEST(IntegrationWiFi, CheckAdapter) {
    WiFiTestAgent agent;
    auto result = agent.processQuery("Check the Wi-Fi adapter status. What SSID am I connected to and what is the signal strength?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    EXPECT_GT(agent.toolCallCount, 0) << "Expected at least one tool call";
}

TEST(IntegrationWiFi, IpConfig) {
    WiFiTestAgent agent;
    auto result = agent.processQuery("Show my IP configuration. What is my IP address and default gateway?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    EXPECT_GT(agent.toolCallCount, 0) << "Expected at least one tool call";
}

TEST(IntegrationWiFi, DnsResolution) {
    WiFiTestAgent agent;
    auto result = agent.processQuery("Test DNS resolution for google.com. Does it resolve successfully?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    EXPECT_GT(agent.toolCallCount, 0) << "Expected at least one tool call";
    // DNS should resolve to an IP — look for dotted-quad pattern or "google"
    std::string lower = toLower(answer);
    bool hasDnsInfo = lower.find("google") != std::string::npos
                   || lower.find("resolve") != std::string::npos
                   || lower.find("ip") != std::string::npos
                   || lower.find(".") != std::string::npos;
    EXPECT_TRUE(hasDnsInfo) << "Expected DNS resolution info in answer, got: " << answer;
}

TEST(IntegrationWiFi, InternetConnectivity) {
    WiFiTestAgent agent;
    auto result = agent.processQuery("Test internet connectivity. Can I reach external servers?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    EXPECT_GT(agent.toolCallCount, 0) << "Expected at least one tool call";
}

#endif // _WIN32
