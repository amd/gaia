// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Wi-Fi Troubleshooter Agent — pure registered-tool approach.
// Runs a full diagnostic chain (adapter -> IP/DHCP -> DNS -> gateway -> internet)
// and auto-applies fixes using PowerShell. No Python, no MCP dependency.
//
// Usage:
//   ./wifi_agent
//   > Run a full network diagnostic.
//
// Requirements:
//   - Windows (PowerShell commands for network diagnostics)
//   - LLM server running at http://localhost:8000/api/v1

#include <array>
#include <cstdio>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

#include <gaia/agent.h>
#include <gaia/console.h>
#include <gaia/types.h>

// ---------------------------------------------------------------------------
// Clean console — nicely formatted progress without redundancy
// ---------------------------------------------------------------------------
class CleanConsole : public gaia::OutputHandler {
public:
    static constexpr const char* RESET   = "\033[0m";
    static constexpr const char* BOLD    = "\033[1m";
    static constexpr const char* DIM     = "\033[90m";
    static constexpr const char* RED     = "\033[91m";
    static constexpr const char* GREEN   = "\033[92m";
    static constexpr const char* YELLOW  = "\033[93m";
    static constexpr const char* BLUE    = "\033[94m";
    static constexpr const char* MAGENTA = "\033[95m";
    static constexpr const char* CYAN    = "\033[96m";

    void printProcessingStart(const std::string& /*query*/, int /*maxSteps*/,
                              const std::string& /*modelId*/) override {
        std::cout << std::endl;
    }

    void printStepHeader(int stepNum, int stepLimit) override {
        stepNum_ = stepNum;
        stepLimit_ = stepLimit;
    }

    void printStateInfo(const std::string& /*message*/) override {}

    void printThought(const std::string& thought) override {
        if (!thought.empty()) {
            std::cout << MAGENTA << "  Thinking: " << RESET << DIM
                      << truncate(thought, 120) << RESET << std::endl;
        }
    }

    void printGoal(const std::string& /*goal*/) override {}

    void printPlan(const gaia::json& plan, int /*currentStep*/) override {
        if (planShown_ || !plan.is_array()) return;
        planShown_ = true;
        std::cout << BOLD << "  Plan: " << RESET;
        for (size_t i = 0; i < plan.size(); ++i) {
            if (i > 0) std::cout << DIM << " -> " << RESET;
            if (plan[i].is_object() && plan[i].contains("tool")) {
                std::cout << CYAN << plan[i]["tool"].get<std::string>() << RESET;
            }
        }
        std::cout << std::endl;
    }

    void printToolUsage(const std::string& toolName) override {
        std::cout << YELLOW << "  [" << stepNum_ << "/" << stepLimit_ << "] "
                  << BOLD << toolName << RESET
                  << DIM << " ..." << RESET << std::flush;
    }

    void printToolComplete() override {
        std::cout << GREEN << " done" << RESET << std::endl;
    }

    void prettyPrintJson(const gaia::json& /*data*/,
                         const std::string& /*title*/) override {
        // Suppress raw JSON dumps — the LLM reads them, the user doesn't need to
    }

    void printError(const std::string& message) override {
        std::cout << RED << "  ERROR: " << RESET << message << std::endl;
    }

    void printWarning(const std::string& message) override {
        std::cout << YELLOW << "  WARNING: " << RESET << message << std::endl;
    }

    void printInfo(const std::string& /*message*/) override {}

    void startProgress(const std::string& message) override {
        std::cout << DIM << "  " << message << "..." << RESET << std::flush;
    }

    void stopProgress() override {
        std::cout << std::endl;
    }

    void printFinalAnswer(const std::string& /*answer*/) override {
        // Suppress — main() prints the final answer with "Agent:" prefix
    }

    void printCompletion(int stepsTaken, int /*stepsLimit*/) override {
        std::cout << DIM << "  (" << stepsTaken << " steps)" << RESET
                  << std::endl;
    }

private:
    static std::string truncate(const std::string& s, size_t maxLen) {
        if (s.size() <= maxLen) return s;
        return s.substr(0, maxLen) + "...";
    }

    int stepNum_ = 0;
    int stepLimit_ = 0;
    bool planShown_ = false;
};

// ---------------------------------------------------------------------------
// Shell helper — runs a command and captures stdout+stderr
// ---------------------------------------------------------------------------
static std::string runShell(const std::string& command) {
    std::string fullCmd;
#ifdef _WIN32
    // Wrap in PowerShell with & { } so cmd.exe doesn't parse internal pipes
    fullCmd = "powershell -NoProfile -NonInteractive -Command \"& { "
              + command + " }\" 2>&1";
#else
    // Linux/macOS fallback (for CI compilation — tools won't return real data)
    fullCmd = command + " 2>&1";
#endif

    std::string result;
    std::array<char, 4096> buffer;

#ifdef _WIN32
    std::unique_ptr<FILE, decltype(&_pclose)> pipe(_popen(fullCmd.c_str(), "r"), _pclose);
#else
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(fullCmd.c_str(), "r"), pclose);
#endif

    if (!pipe) {
        return "{\"error\": \"Failed to execute command\"}";
    }

    while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()) != nullptr) {
        result += buffer.data();
    }

    return result.empty() ? "{\"status\": \"completed\", \"output\": \"(no output)\"}" : result;
}

// ---------------------------------------------------------------------------
// Wi-Fi Troubleshooter Agent
// ---------------------------------------------------------------------------
class WiFiTroubleshooterAgent : public gaia::Agent {
public:
    explicit WiFiTroubleshooterAgent(const std::string& modelId)
        : Agent(makeConfig(modelId)) {
        setOutputHandler(std::make_unique<CleanConsole>());
        init();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are an expert Windows network troubleshooter. You diagnose and fix Wi-Fi connectivity issues using PowerShell commands via your registered tools.

## DIAGNOSTIC PROTOCOL (follow this order)

1. **Adapter Check** — call `check_adapter` to verify the Wi-Fi adapter is present and connected
2. **IP Configuration** — call `check_ip_config` to verify IP address, subnet mask, gateway, and DHCP status
3. **Gateway Ping** — call `ping_host` with the default gateway IP from step 2
4. **DNS Resolution** — call `test_dns_resolution` to verify DNS is working
5. **Internet Connectivity** — call `test_internet` to verify end-to-end internet access

## FIX PROTOCOL (apply only if diagnostics reveal issues)

- **No IP address / DHCP failure** → call `renew_dhcp_lease`
- **DNS resolution failure** → call `flush_dns_cache`, then re-test; if still failing, call `set_dns_servers` with Google DNS (8.8.8.8 / 8.8.4.4)
- **Adapter disconnected / hardware issue** → call `restart_wifi_adapter` with the adapter name from step 1
- After applying any fix, re-run the relevant diagnostic to confirm the fix worked

## OUTPUT FORMAT

End every response with one of:
- **RESOLVED** — all diagnostics pass or issue was fixed
- **PARTIALLY RESOLVED** — some issues fixed but others remain
- **NEEDS MANUAL ACTION** — issue requires user intervention (driver update, hardware problem, router issue, etc.)

Always explain what you found, what you did, and what the user should do next (if anything).)";
    }

    void registerTools() override {
        // -----------------------------------------------------------------
        // Diagnostic tools (read-only)
        // -----------------------------------------------------------------

        toolRegistry().registerTool(
            "check_adapter",
            "Show Wi-Fi adapter status including SSID, signal strength, radio type, and connection state. Returns the output of 'netsh wlan show interfaces'.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string output = runShell("netsh wlan show interfaces");
                return {{"tool", "check_adapter"}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "check_ip_config",
            "Show full IP configuration for all network adapters including IP address, subnet mask, default gateway, DNS servers, and DHCP status. Returns the output of 'ipconfig /all'.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string output = runShell("ipconfig /all");
                return {{"tool", "check_ip_config"}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "test_dns_resolution",
            "Test DNS resolution by resolving a hostname to an IP address. Returns JSON with resolved addresses and response time.",
            [](const gaia::json& args) -> gaia::json {
                std::string hostname = args.value("hostname", "google.com");
                std::string cmd = "Resolve-DnsName -Name " + hostname
                    + " -Type A -ErrorAction Stop | Select-Object Name, IPAddress, QueryType"
                    + " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "test_dns_resolution"}, {"hostname", hostname}, {"output", output}};
            },
            {
                {"hostname", gaia::ToolParamType::STRING, /*required=*/false,
                 "The hostname to resolve (default: google.com)"}
            }
        );

        toolRegistry().registerTool(
            "test_internet",
            "Test internet connectivity by connecting to a reliable external host on port 443. Returns JSON with connection status, latency, and remote address.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string cmd =
                    "Test-NetConnection -ComputerName 8.8.8.8 -Port 443"
                    " | Select-Object ComputerName, RemotePort, TcpTestSucceeded, PingSucceeded,"
                    " PingReplyDetails"
                    " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "test_internet"}, {"output", output}};
            },
            {}  // no parameters
        );

        // -----------------------------------------------------------------
        // Diagnostic tool with parameter
        // -----------------------------------------------------------------

        toolRegistry().registerTool(
            "ping_host",
            "Ping a specific host and return connection status, latency, and resolved address as JSON.",
            [](const gaia::json& args) -> gaia::json {
                std::string host = args.value("host", "");
                if (host.empty()) {
                    return {{"error", "host parameter is required"}};
                }
                std::string cmd =
                    "Test-NetConnection -ComputerName " + host
                    + " | Select-Object ComputerName, RemoteAddress, PingSucceeded, PingReplyDetails"
                    + " | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "ping_host"}, {"host", host}, {"output", output}};
            },
            {
                {"host", gaia::ToolParamType::STRING, /*required=*/true,
                 "The hostname or IP address to ping (e.g. '192.168.1.1' or 'google.com')"}
            }
        );

        // -----------------------------------------------------------------
        // Fix tools
        // -----------------------------------------------------------------

        toolRegistry().registerTool(
            "flush_dns_cache",
            "Clear the local DNS resolver cache. Use this when DNS resolution fails to remove stale or corrupted cache entries.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string output = runShell("Clear-DnsClientCache");
                return {{"tool", "flush_dns_cache"}, {"status", "completed"}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "set_dns_servers",
            "Set custom DNS server addresses for a network adapter. Use this when the default DNS servers are not resolving correctly.",
            [](const gaia::json& args) -> gaia::json {
                std::string adapter = args.value("adapter_name", "");
                std::string primary = args.value("primary_dns", "");
                std::string secondary = args.value("secondary_dns", "");

                if (adapter.empty() || primary.empty()) {
                    return {{"error", "adapter_name and primary_dns are required"}};
                }

                std::string cmd = "Set-DnsClientServerAddress -InterfaceAlias '"
                    + adapter + "' -ServerAddresses ";
                if (secondary.empty()) {
                    cmd += "'" + primary + "'";
                } else {
                    cmd += "('" + primary + "','" + secondary + "')";
                }

                std::string output = runShell(cmd);
                return {
                    {"tool", "set_dns_servers"},
                    {"adapter_name", adapter},
                    {"primary_dns", primary},
                    {"secondary_dns", secondary},
                    {"status", "completed"},
                    {"output", output}
                };
            },
            {
                {"adapter_name", gaia::ToolParamType::STRING, /*required=*/true,
                 "The network adapter name (e.g. 'Wi-Fi')"},
                {"primary_dns", gaia::ToolParamType::STRING, /*required=*/true,
                 "Primary DNS server IP address (e.g. '8.8.8.8')"},
                {"secondary_dns", gaia::ToolParamType::STRING, /*required=*/false,
                 "Secondary DNS server IP address (e.g. '8.8.4.4')"}
            }
        );

        toolRegistry().registerTool(
            "renew_dhcp_lease",
            "Release and renew the DHCP lease for all network adapters. Use this when the adapter has no IP address or an APIPA (169.254.x.x) address.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string output = runShell(
                    "ipconfig /release; Start-Sleep -Seconds 1; ipconfig /renew"
                );
                return {{"tool", "renew_dhcp_lease"}, {"status", "completed"}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "restart_wifi_adapter",
            "Disable and re-enable a network adapter to reset its connection. Use this as a last resort when the adapter is in a bad state.",
            [](const gaia::json& args) -> gaia::json {
                std::string adapter = args.value("adapter_name", "");
                if (adapter.empty()) {
                    return {{"error", "adapter_name is required"}};
                }

                std::string cmd =
                    "Disable-NetAdapter -Name '" + adapter + "' -Confirm:$false; "
                    "Start-Sleep -Seconds 3; "
                    "Enable-NetAdapter -Name '" + adapter + "' -Confirm:$false";

                std::string output = runShell(cmd);
                return {
                    {"tool", "restart_wifi_adapter"},
                    {"adapter_name", adapter},
                    {"status", "completed"},
                    {"output", output}
                };
            },
            {
                {"adapter_name", gaia::ToolParamType::STRING, /*required=*/true,
                 "The network adapter name to restart (e.g. 'Wi-Fi')"}
            }
        );
    }

private:
    static gaia::AgentConfig makeConfig(const std::string& modelId) {
        gaia::AgentConfig config;
        config.maxSteps = 20;
        config.modelId = modelId;
        return config;
    }
};

// ---------------------------------------------------------------------------
// Diagnostic menu — maps numbered selections to pre-written prompts
// ---------------------------------------------------------------------------
static const std::pair<std::string, std::string> kDiagnosticMenu[] = {
    {"Full network diagnostic",
     "Run a full network diagnostic following the complete diagnostic protocol."},
    {"Check Wi-Fi adapter",
     "Check the Wi-Fi adapter status and report the connection state, signal strength, and SSID."},
    {"Check IP configuration",
     "Check the IP configuration and report IP addresses, default gateway, DNS servers, and DHCP status."},
    {"Test DNS resolution",
     "Test DNS resolution and report whether name resolution is working correctly."},
    {"Test internet connectivity",
     "Test internet connectivity and report whether the internet is reachable."},
    {"Flush DNS cache",
     "Flush the DNS cache to clear any stale or corrupted entries, then verify DNS is working."},
    {"Renew DHCP lease",
     "Renew the DHCP lease to get a fresh IP address, then verify the new configuration."},
};
static constexpr size_t kMenuSize = sizeof(kDiagnosticMenu) / sizeof(kDiagnosticMenu[0]);

static void printDiagnosticMenu() {
    std::cout << "--------------------------------------------------" << std::endl;
    for (size_t i = 0; i < kMenuSize; ++i) {
        std::cout << "  [" << (i + 1) << "] " << kDiagnosticMenu[i].first << std::endl;
    }
    std::cout << "--------------------------------------------------" << std::endl;
    std::cout << "  Or type your own question. Type 'quit' to exit." << std::endl;
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// main — model selection + interactive loop with diagnostic menu
// ---------------------------------------------------------------------------
int main() {
    try {
        std::cout << "\n=== Wi-Fi Troubleshooter | GAIA C++ | Local inference ===" << std::endl;

        // --- Model selection ---
        std::cout << "\nSelect inference backend:" << std::endl;
        std::cout << "  [1] GPU  - Qwen3-4B-Instruct-2507-GGUF" << std::endl;
        std::cout << "  [2] NPU  - Qwen3-4B-Instruct-2507-FLM" << std::endl;
        std::cout << std::endl;
        std::cout << "> " << std::flush;

        std::string modelChoice;
        std::getline(std::cin, modelChoice);

        std::string modelId;
        if (modelChoice == "2") {
            modelId = "Qwen3-4B-Instruct-2507-FLM";
            std::cout << "Using NPU backend: " << modelId << std::endl;
        } else {
            modelId = "Qwen3-4B-Instruct-2507-GGUF";
            std::cout << "Using GPU backend: " << modelId << std::endl;
        }

        WiFiTroubleshooterAgent agent(modelId);

        std::cout << "\nReady!\n" << std::endl;

        // --- Interactive loop with diagnostic menu ---
        std::string userInput;
        while (true) {
            printDiagnosticMenu();
            std::cout << "> " << std::flush;
            std::getline(std::cin, userInput);

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q") break;

            // Map numbered selection to pre-written prompt
            std::string query;
            if (userInput.size() == 1 && userInput[0] >= '1' && userInput[0] <= '0' + static_cast<char>(kMenuSize)) {
                size_t idx = static_cast<size_t>(userInput[0] - '1');
                query = kDiagnosticMenu[idx].second;
                std::cout << "\n> " << kDiagnosticMenu[idx].first << std::endl;
            } else {
                query = userInput;
            }

            auto result = agent.processQuery(query);
            if (result.contains("result") && !result["result"].get<std::string>().empty()) {
                std::cout << "\nAgent: " << result["result"].get<std::string>() << "\n" << std::endl;
            }
        }

    } catch (const std::exception& e) {
        std::cerr << "Fatal error: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
