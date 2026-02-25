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

#ifdef _WIN32
#include <windows.h>
#endif

#include <array>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>

#include <gaia/agent.h>
#include <gaia/console.h>
#include <gaia/types.h>

// ---------------------------------------------------------------------------
// ANSI color constants (shared by CleanConsole and TUI helpers)
// ---------------------------------------------------------------------------
namespace color {
    constexpr const char* RESET   = "\033[0m";
    constexpr const char* BOLD    = "\033[1m";
    constexpr const char* DIM     = "\033[2m";
    constexpr const char* ITALIC  = "\033[3m";
    constexpr const char* UNDERLN = "\033[4m";
    constexpr const char* GRAY    = "\033[90m";
    constexpr const char* RED     = "\033[91m";
    constexpr const char* GREEN   = "\033[92m";
    constexpr const char* YELLOW  = "\033[93m";
    constexpr const char* BLUE    = "\033[94m";
    constexpr const char* MAGENTA = "\033[95m";
    constexpr const char* CYAN    = "\033[96m";
    constexpr const char* WHITE   = "\033[97m";
    // Background
    constexpr const char* BG_BLUE = "\033[44m";
}

// ---------------------------------------------------------------------------
// Clean console — nicely formatted progress with tool output summaries
// ---------------------------------------------------------------------------
class CleanConsole : public gaia::OutputHandler {
public:
    void printProcessingStart(const std::string& /*query*/, int /*maxSteps*/,
                              const std::string& /*modelId*/) override {
        std::cout << std::endl;
        planShown_ = false;
        toolsRun_ = 0;
        lastGoal_.clear();
    }

    void printStepHeader(int stepNum, int stepLimit) override {
        stepNum_ = stepNum;
        stepLimit_ = stepLimit;
    }

    void printStateInfo(const std::string& /*message*/) override {}

    void printThought(const std::string& thought) override {
        if (thought.empty()) return;

        // Look for structured FINDING:/DECISION: reasoning format
        auto findingPos = thought.find("FINDING:");
        if (findingPos == std::string::npos) findingPos = thought.find("Finding:");
        auto decisionPos = thought.find("DECISION:");
        if (decisionPos == std::string::npos) decisionPos = thought.find("Decision:");

        if (findingPos != std::string::npos || decisionPos != std::string::npos) {
            // --- Structured reasoning: parse and color-code ---
            if (findingPos != std::string::npos) {
                size_t start = findingPos + 8; // skip "FINDING:"
                size_t end = (decisionPos != std::string::npos) ? decisionPos : thought.size();
                std::string text = thought.substr(start, end - start);
                // Trim whitespace
                size_t f = text.find_first_not_of(" \t\n\r");
                size_t l = text.find_last_not_of(" \t\n\r");
                if (f != std::string::npos) text = text.substr(f, l - f + 1);

                std::cout << color::GREEN << color::BOLD << "  Finding: "
                          << color::RESET;
                printWrapped(text, 79, 11);
            }
            if (decisionPos != std::string::npos) {
                size_t start = decisionPos + 9; // skip "DECISION:"
                std::string text = thought.substr(start);
                size_t f = text.find_first_not_of(" \t\n\r");
                size_t l = text.find_last_not_of(" \t\n\r");
                if (f != std::string::npos) text = text.substr(f, l - f + 1);

                std::cout << color::YELLOW << color::BOLD << "  Decision: "
                          << color::RESET;
                printWrapped(text, 78, 12);
            }
        } else {
            // --- Fallback: existing Analysis/Thinking display ---
            if (toolsRun_ > 0) {
                std::cout << color::BLUE << color::BOLD << "  Analysis: "
                          << color::RESET;
            } else {
                std::cout << color::MAGENTA << "  Thinking: " << color::RESET;
            }
            printWrapped(thought, 78, 12);
        }
    }

    void printGoal(const std::string& goal) override {
        if (goal.empty() || goal == lastGoal_) return;
        lastGoal_ = goal;
        std::cout << std::endl;
        std::cout << color::CYAN << color::ITALIC
                  << "  Goal: " << color::RESET;
        printWrapped(goal, 82, 8);
    }

    void printPlan(const gaia::json& plan, int /*currentStep*/) override {
        if (planShown_ || !plan.is_array()) return;
        planShown_ = true;
        std::cout << color::BOLD << color::CYAN << "  Plan: " << color::RESET;
        for (size_t i = 0; i < plan.size(); ++i) {
            if (i > 0) std::cout << color::GRAY << " -> " << color::RESET;
            if (plan[i].is_object() && plan[i].contains("tool")) {
                std::cout << color::CYAN
                          << plan[i]["tool"].get<std::string>()
                          << color::RESET;
            }
        }
        std::cout << std::endl;
    }

    void printToolUsage(const std::string& toolName) override {
        lastToolName_ = toolName;
        std::cout << std::endl;
        std::cout << color::YELLOW << color::BOLD
                  << "  [" << stepNum_ << "/" << stepLimit_ << "] "
                  << toolName << color::RESET << std::endl;
    }

    void printToolComplete() override {
        ++toolsRun_;
    }

    void prettyPrintJson(const gaia::json& data,
                         const std::string& title) override {
        // Show tool arguments (the command being sent)
        if (title == "Tool Args" && data.is_object() && !data.empty()) {
            std::string argsStr;
            bool first = true;
            for (auto& [key, val] : data.items()) {
                if (!first) argsStr += ", ";
                argsStr += key + "=";
                if (val.is_string()) argsStr += val.get<std::string>();
                else argsStr += val.dump();
                first = false;
            }
            std::cout << color::GRAY << "      Args: ";
            printWrapped(argsStr, 78, 12);
            std::cout << color::RESET;
            return;
        }

        if (title != "Tool Result" || !data.is_object()) return;

        // Show the command that was executed
        if (data.contains("command")) {
            std::string cmd = data["command"].get<std::string>();
            std::cout << color::CYAN << "      Cmd: " << color::RESET
                      << color::GRAY;
            printWrapped(cmd, 79, 11);
            std::cout << color::RESET;
        }

        // Show error if present
        if (data.contains("error")) {
            std::cout << color::RED << color::BOLD << "      Error: "
                      << color::RESET << color::RED
                      << data["error"].get<std::string>()
                      << color::RESET << std::endl;
            return;
        }

        // Show tool output preview
        if (data.contains("output")) {
            std::string output = data["output"].get<std::string>();
            if (output.empty() || output.find("(no output)") != std::string::npos) {
                std::cout << color::GREEN << "      Result: "
                          << color::RESET << color::GRAY << "(no output)"
                          << color::RESET << std::endl;
                return;
            }
            std::cout << color::GREEN << "      Output:" << color::RESET
                      << std::endl;
            printOutputPreview(output);
        }

        // Show status for fix tools
        if (data.contains("status")) {
            auto status = data["status"].get<std::string>();
            const char* statusColor = (status == "completed")
                ? color::GREEN : color::YELLOW;
            std::cout << statusColor << "      Status: " << status
                      << color::RESET << std::endl;
        }
    }

    void printError(const std::string& message) override {
        std::cout << color::RED << color::BOLD << "  ERROR: " << color::RESET
                  << color::RED;
        printWrapped(message, 81, 9);
        std::cout << color::RESET;
    }

    void printWarning(const std::string& message) override {
        std::cout << color::YELLOW << "  WARNING: " << color::RESET
                  << message << std::endl;
    }

    void printInfo(const std::string& /*message*/) override {}

    void startProgress(const std::string& /*message*/) override {}

    void stopProgress() override {}

    void printFinalAnswer(const std::string& answer) override {
        if (answer.empty()) return;

        // Extract clean text — the LLM sometimes returns raw JSON instead
        // of plain text. Try to extract "answer" or "thought" fields.
        std::string cleanAnswer = answer;
        if (!answer.empty() && answer.front() == '{') {
            try {
                auto j = gaia::json::parse(answer);
                if (j.is_object()) {
                    if (j.contains("answer") && j["answer"].is_string()) {
                        cleanAnswer = j["answer"].get<std::string>();
                    } else if (j.contains("thought") && j["thought"].is_string()) {
                        cleanAnswer = j["thought"].get<std::string>();
                    }
                }
            } catch (...) {
                // Not valid JSON — use as-is
            }
        }

        std::cout << std::endl;
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::GREEN << color::BOLD
                  << "  Conclusion" << color::RESET << std::endl;
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        // Print each line of the answer word-wrapped
        std::string line;
        std::istringstream stream(cleanAnswer);
        while (std::getline(stream, line)) {
            if (line.empty()) {
                std::cout << std::endl;
            } else {
                std::cout << "  ";
                printWrapped(line, 88, 2);
            }
        }
        std::cout << color::GREEN
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
    }

    void printCompletion(int stepsTaken, int /*stepsLimit*/) override {
        std::cout << color::GRAY << "  Completed in " << stepsTaken
                  << " steps" << color::RESET << std::endl;
    }

private:
    // Print text with word-wrapping at the given width, indented by indent spaces
    static void printWrapped(const std::string& text, size_t width, size_t indent) {
        std::string indentStr(indent, ' ');
        std::istringstream words(text);
        std::string word;
        size_t col = 0;
        bool firstWord = true;
        while (words >> word) {
            if (!firstWord && col + 1 + word.size() > width) {
                std::cout << std::endl << indentStr;
                col = 0;
            } else if (!firstWord) {
                std::cout << ' ';
                ++col;
            }
            std::cout << word;
            col += word.size();
            firstWord = false;
        }
        std::cout << color::RESET << std::endl;
    }

    // Print a compact preview of command output (up to kMaxPreviewLines lines)
    void printOutputPreview(const std::string& output) {
        constexpr int kMaxPreviewLines = 10;
        std::istringstream stream(output);
        std::string line;
        int lineCount = 0;
        int totalLines = 0;

        // Count total non-empty lines
        {
            std::istringstream counter(output);
            std::string tmp;
            while (std::getline(counter, tmp)) {
                if (!tmp.empty() && tmp.find_first_not_of(" \t\r\n") != std::string::npos)
                    ++totalLines;
            }
        }

        std::cout << color::GRAY << "      .------------------------------------------------------------------------------------"
                  << color::RESET << std::endl;
        while (std::getline(stream, line) && lineCount < kMaxPreviewLines) {
            // Skip empty lines
            if (line.empty() || line.find_first_not_of(" \t\r\n") == std::string::npos)
                continue;
            // Trim trailing \r
            if (!line.empty() && line.back() == '\r') line.pop_back();
            // Truncate long lines
            if (line.size() > 82) line = line.substr(0, 79) + "...";
            std::cout << color::GRAY << "      | " << line << color::RESET
                      << std::endl;
            ++lineCount;
        }
        if (totalLines > kMaxPreviewLines) {
            std::cout << color::GRAY << "      | ... ("
                      << (totalLines - kMaxPreviewLines)
                      << " more lines)" << color::RESET << std::endl;
        }
        std::cout << color::GRAY << "      '------------------------------------------------------------------------------------"
                  << color::RESET << std::endl;
    }

    int stepNum_ = 0;
    int stepLimit_ = 0;
    int toolsRun_ = 0;
    bool planShown_ = false;
    std::string lastToolName_;
    std::string lastGoal_;
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
        return R"(You are an expert Windows network troubleshooter running locally on AMD hardware via the GAIA framework. You diagnose and fix Wi-Fi connectivity issues using PowerShell commands via your registered tools.

You are an intelligent agent. Given a user's question, decide which tools are relevant, run them one at a time, reason about each result, adapt your approach based on what you find, and continue until the question is answered or the issue is resolved.

IMPORTANT: Be concise. Keep FINDING and DECISION to 1-2 sentences each. No filler words.

CRITICAL: Do NOT provide a final "answer" until you have finished ALL relevant tool calls. If you still have tools to run or fixes to apply, you MUST call the next tool — do NOT stop early with an answer. Only provide an "answer" when your investigation is truly complete.

## REASONING PROTOCOL

After EVERY tool result, structure your thought using these exact prefixes:

FINDING: <1-2 sentences: key facts and values from the output>
DECISION: <1 sentence: what to do next and WHY>

The user sees FINDING and DECISION highlighted in the UI. Use them to make your reasoning visible.

## HOW TO APPROACH A QUERY

1. Read the user's question and decide which tools are relevant
2. Create a plan showing the tools you intend to run (include it in your first response)
3. Execute the first tool
4. After each result: analyze it (FINDING), decide what to do next (DECISION), then CALL THE NEXT TOOL
5. Update your plan as needed — skip steps that are no longer relevant, add fix/verify steps
6. Only when ALL tools are done, provide your final answer

Your approach should be entirely driven by the query:
- "Run a full diagnostic" → run ALL diagnostic tools, summarize everything at the end
- "Check my DNS" → just run DNS test, report result, stop
- "Why can't I connect?" → start with adapter check, follow the evidence
- "Fix my internet" → diagnose first, apply fixes, verify fixes worked

## AVAILABLE DIAGNOSTIC SEQUENCE

For a full network diagnostic, the typical sequence is:
1. `check_adapter` — adapter present and connected?
2. `check_ip_config` — valid IP, gateway, DNS servers?
3. `ping_host` — gateway reachable?
4. `test_dns_resolution` — name resolution working?
5. `test_internet` — end-to-end connectivity?
6. `test_bandwidth` — download speed acceptable?

Adapt based on what you find. If the adapter is disconnected, try to enable it first, then continue. If everything passes early, you can stop early for targeted queries (but NOT for a full diagnostic).

## FIXING ISSUES

When you find a problem, fix it and verify:
1. Apply the fix
2. Re-run the diagnostic that failed to verify the fix worked
3. Report the before/after in your FINDING
4. If the fix failed, try the next option

IMPORTANT — Wi-Fi radio vs adapter:
- If radio status shows "Software Off": use `toggle_wifi_radio` (turns on the Windows Wi-Fi radio toggle)
- If adapter is administratively disabled: use `enable_wifi_adapter` (enables the network interface)
- `enable_wifi_adapter` does NOT turn on the radio. You need `toggle_wifi_radio` for that.
- After toggling the radio on, wait a moment then re-check with `check_adapter` to verify it connected.

Available fix tools: `toggle_wifi_radio`, `flush_dns_cache`, `set_dns_servers`, `renew_dhcp_lease`, `enable_wifi_adapter`, `restart_wifi_adapter`

## FINAL ANSWER

Only provide an "answer" after ALL tool calls are complete. Format your summary as:

1. A results table showing each check and its status:
   - Adapter: OK / FAIL (details)
   - IP Config: OK / FAIL (details)
   - DNS: OK / FAIL (details)
   - Internet: OK / FAIL (details)
   - Speed: X Mbps (if tested)

2. Any fixes applied and whether they worked
3. Overall status: RESOLVED, PARTIALLY RESOLVED, or NEEDS MANUAL ACTION
4. One sentence summary of the situation

## GOAL TRACKING

Always set a short `goal` field (3-6 words) describing your current objective.)";
    }

    void registerTools() override {
        // -----------------------------------------------------------------
        // Diagnostic tools (read-only)
        // -----------------------------------------------------------------

        toolRegistry().registerTool(
            "check_adapter",
            "Show Wi-Fi adapter status including SSID, signal strength, radio type, and connection state. Returns the output of 'netsh wlan show interfaces'.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string cmd = "netsh wlan show interfaces";
                std::string output = runShell(cmd);
                return {{"tool", "check_adapter"}, {"command", cmd}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "check_wifi_drivers",
            "Show Wi-Fi driver information including driver name, version, vendor, supported radio types, and whether hosted network is supported. Returns the output of 'netsh wlan show drivers'.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string cmd = "netsh wlan show drivers";
                std::string output = runShell(cmd);
                return {{"tool", "check_wifi_drivers"}, {"command", cmd}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "check_ip_config",
            "Show full IP configuration for all network adapters including IP address, subnet mask, default gateway, DNS servers, and DHCP status. Returns the output of 'ipconfig /all'.",
            [](const gaia::json& /*args*/) -> gaia::json {
                std::string cmd = "ipconfig /all";
                std::string output = runShell(cmd);
                return {{"tool", "check_ip_config"}, {"command", cmd}, {"output", output}};
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
                return {{"tool", "test_dns_resolution"}, {"command", cmd}, {"hostname", hostname}, {"output", output}};
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
                return {{"tool", "test_internet"}, {"command", cmd}, {"output", output}};
            },
            {}  // no parameters
        );

        toolRegistry().registerTool(
            "test_bandwidth",
            "Run a quick download speed test by fetching a small file from a CDN and measuring throughput. Returns download speed in Mbps. This is a rough estimate, not a full speed test.",
            [](const gaia::json& /*args*/) -> gaia::json {
                // Download a ~1MB file from Cloudflare's speed test endpoint and measure time
                std::string cmd =
                    "$url = 'https://speed.cloudflare.com/__down?bytes=1000000'; "
                    "$sw = [System.Diagnostics.Stopwatch]::StartNew(); "
                    "$response = Invoke-WebRequest -Uri $url -UseBasicParsing; "
                    "$sw.Stop(); "
                    "$bytes = $response.Content.Length; "
                    "$seconds = $sw.Elapsed.TotalSeconds; "
                    "$mbps = [math]::Round(($bytes * 8) / ($seconds * 1000000), 2); "
                    "@{ "
                    "  download_mbps = $mbps; "
                    "  bytes = $bytes; "
                    "  seconds = [math]::Round($seconds, 2); "
                    "  source = 'speed.cloudflare.com' "
                    "} | ConvertTo-Json";
                std::string output = runShell(cmd);
                return {{"tool", "test_bandwidth"}, {"command", "Download speed test (1MB from Cloudflare CDN)"}, {"output", output}};
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
                return {{"tool", "ping_host"}, {"command", cmd}, {"host", host}, {"output", output}};
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
                std::string cmd = "Clear-DnsClientCache";
                std::string output = runShell(cmd);
                return {{"tool", "flush_dns_cache"}, {"command", cmd}, {"status", "completed"}, {"output", output}};
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
                    {"command", cmd},
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
                std::string cmd = "ipconfig /release; Start-Sleep -Seconds 1; ipconfig /renew";
                std::string output = runShell(cmd);
                return {{"tool", "renew_dhcp_lease"}, {"command", cmd}, {"status", "completed"}, {"output", output}};
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
                    {"command", cmd},
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

        toolRegistry().registerTool(
            "enable_wifi_adapter",
            "Enable a disabled Wi-Fi adapter without a full restart cycle. Use when the adapter is administratively disabled but hardware radio is on.",
            [](const gaia::json& args) -> gaia::json {
                std::string adapter = args.value("adapter_name", "");
                if (adapter.empty()) {
                    return {{"error", "adapter_name is required"}};
                }
                std::string cmd = "Enable-NetAdapter -Name '" + adapter + "' -Confirm:$false";
                std::string output = runShell(cmd);
                return {
                    {"tool", "enable_wifi_adapter"},
                    {"command", cmd},
                    {"adapter_name", adapter},
                    {"status", "completed"},
                    {"output", output}
                };
            },
            {
                {"adapter_name", gaia::ToolParamType::STRING, /*required=*/true,
                 "The adapter name to enable (e.g. 'Wi-Fi')"}
            }
        );

        toolRegistry().registerTool(
            "toggle_wifi_radio",
            "Turn the Wi-Fi radio ON or OFF using the Windows Radio Management API. Use this when the adapter shows 'Software Off' in radio status — Enable-NetAdapter alone does NOT turn on the radio. This is the equivalent of the Wi-Fi toggle in Windows Settings.",
            [](const gaia::json& args) -> gaia::json {
                std::string state = args.value("state", "on");
                // Use WinRT Radio API via PowerShell to toggle the Wi-Fi radio.
                // This requires .NET reflection to resolve the generic AsTask() method,
                // so we write a temp .ps1 script and execute via -File to avoid
                // escaping issues with powershell -Command "...".
                std::string radioState = (state == "off") ? "Off" : "On";

                // Build script content
                std::string script =
                    "Add-Type -AssemblyName System.Runtime.WindowsRuntime\n"
                    "[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null\n"
                    "$at = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {\n"
                    "    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and\n"
                    "    $_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation')\n"
                    "})[0]\n"
                    "Function Await($o, $r) {\n"
                    "    $t = $at.MakeGenericMethod($r).Invoke($null, @($o))\n"
                    "    $t.Wait() | Out-Null\n"
                    "    $t.Result\n"
                    "}\n"
                    "$rs = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) "
                    "([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])\n"
                    "$w = $rs | Where-Object { $_.Kind -eq 'WiFi' }\n"
                    "if ($w) {\n"
                    "    Await ($w.SetStateAsync([Windows.Devices.Radios.RadioState]::" + radioState + ")) "
                    "([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null\n"
                    "    Write-Output 'Wi-Fi radio set to " + radioState + "'\n"
                    "    $w | Select-Object Name,Kind,State | ConvertTo-Json\n"
                    "} else {\n"
                    "    Write-Output 'ERROR: No Wi-Fi radio found'\n"
                    "}\n";

                // Write to temp file and execute
                std::string tempPath;
#ifdef _WIN32
                char* tmp = std::getenv("TEMP");
                tempPath = (tmp ? std::string(tmp) : "C:\\Temp") + "\\gaia_radio.ps1";
#else
                tempPath = "/tmp/gaia_radio.ps1";
#endif
                {
                    std::ofstream f(tempPath);
                    f << script;
                }

                std::string execCmd = "powershell -NoProfile -ExecutionPolicy Bypass -File \""
                                      + tempPath + "\"";
                std::string output;
                std::array<char, 4096> buffer;
#ifdef _WIN32
                std::unique_ptr<FILE, decltype(&_pclose)> pipe(
                    _popen((execCmd + " 2>&1").c_str(), "r"), _pclose);
#else
                std::unique_ptr<FILE, decltype(&pclose)> pipe(
                    popen((execCmd + " 2>&1").c_str(), "r"), pclose);
#endif
                if (pipe) {
                    while (fgets(buffer.data(), static_cast<int>(buffer.size()), pipe.get()))
                        output += buffer.data();
                }

                // Cleanup temp file
                std::remove(tempPath.c_str());

                return {
                    {"tool", "toggle_wifi_radio"},
                    {"command", "Windows Radio API: Set Wi-Fi radio to " + radioState},
                    {"requested_state", radioState},
                    {"status", "completed"},
                    {"output", output}
                };
            },
            {
                {"state", gaia::ToolParamType::STRING, /*required=*/false,
                 "The desired radio state: 'on' or 'off' (default: 'on')"}
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
    {"Check Wi-Fi drivers",
     "Check the Wi-Fi driver information including driver name, version, vendor, and supported radio types."},
    {"Check IP configuration",
     "Check the IP configuration and report IP addresses, default gateway, DNS servers, and DHCP status."},
    {"Test DNS resolution",
     "Test DNS resolution and report whether name resolution is working correctly."},
    {"Test internet connectivity",
     "Test internet connectivity and report whether the internet is reachable."},
    {"Test bandwidth",
     "Run a quick download speed test and report the approximate Wi-Fi speed in Mbps."},
    {"Flush DNS cache",
     "Flush the DNS cache to clear any stale or corrupted entries, then verify DNS is working."},
    {"Renew DHCP lease",
     "Renew the DHCP lease to get a fresh IP address, then verify the new configuration."},
};
static constexpr size_t kMenuSize = sizeof(kDiagnosticMenu) / sizeof(kDiagnosticMenu[0]);

static void printDiagnosticMenu() {
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    for (size_t i = 0; i < kMenuSize; ++i) {
        std::cout << color::YELLOW << "  [" << (i + 1) << "] "
                  << color::RESET << color::WHITE
                  << kDiagnosticMenu[i].first
                  << color::RESET << std::endl;
    }
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GRAY
              << "  Or type your own question. Type 'quit' to exit."
              << color::RESET << std::endl;
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// main — model selection + interactive loop with diagnostic menu
// ---------------------------------------------------------------------------
int main() {
    try {
        // --- Admin check ---
#ifdef _WIN32
        {
            bool isAdmin = false;
            HANDLE token = nullptr;
            if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
                TOKEN_ELEVATION elevation{};
                DWORD size = sizeof(elevation);
                if (GetTokenInformation(token, TokenElevation, &elevation, sizeof(elevation), &size)) {
                    isAdmin = elevation.TokenIsElevated != 0;
                }
                CloseHandle(token);
            }
            if (!isAdmin) {
                std::cout << std::endl;
                std::cout << color::YELLOW << color::BOLD
                          << "  WARNING: " << color::RESET
                          << color::YELLOW
                          << "Not running as admin."
                          << color::RESET << std::endl;
                std::cout << color::GRAY
                          << "  Fix tools (restart adapter,"
                          << std::endl
                          << "  flush DNS, etc.) need elevated"
                          << std::endl
                          << "  privileges. Right-click your"
                          << std::endl
                          << "  terminal -> Run as administrator."
                          << color::RESET << std::endl;
            }
        }
#endif

        // --- Banner ---
        std::cout << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "   Wi-Fi Troubleshooter  |  GAIA C++ Agent Framework  |  Local Inference"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;

        // --- Model selection ---
        std::cout << std::endl;
        std::cout << color::BOLD << "  Select inference backend:"
                  << color::RESET << std::endl;
        std::cout << color::YELLOW << "  [1] " << color::RESET
                  << color::GREEN << "GPU" << color::RESET
                  << color::GRAY << "  - Qwen3-4B-Instruct-2507-GGUF"
                  << color::RESET << std::endl;
        std::cout << color::YELLOW << "  [2] " << color::RESET
                  << color::MAGENTA << "NPU" << color::RESET
                  << color::GRAY << "  - Qwen3-4B-Instruct-2507-FLM"
                  << color::RESET << std::endl;
        std::cout << std::endl;
        std::cout << color::BOLD << "  > " << color::RESET << std::flush;

        std::string modelChoice;
        std::getline(std::cin, modelChoice);

        std::string modelId;
        if (modelChoice == "2") {
            modelId = "Qwen3-4B-Instruct-2507-FLM";
            std::cout << color::MAGENTA << "  Using NPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        } else {
            modelId = "Qwen3-4B-Instruct-2507-GGUF";
            std::cout << color::GREEN << "  Using GPU backend: "
                      << color::BOLD << modelId << color::RESET << std::endl;
        }

        WiFiTroubleshooterAgent agent(modelId);

        std::cout << std::endl;
        std::cout << color::GREEN << color::BOLD << "  Ready!"
                  << color::RESET << std::endl;
        std::cout << std::endl;

        // --- Interactive loop with diagnostic menu ---
        std::string userInput;
        while (true) {
            printDiagnosticMenu();
            std::cout << color::BOLD << "  > " << color::RESET << std::flush;
            std::getline(std::cin, userInput);

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q") break;

            // Map numbered selection to pre-written prompt
            std::string query;
            if (userInput.size() == 1 && userInput[0] >= '1' && userInput[0] <= '0' + static_cast<char>(kMenuSize)) {
                size_t idx = static_cast<size_t>(userInput[0] - '1');
                query = kDiagnosticMenu[idx].second;
                std::cout << color::CYAN << "  > "
                          << kDiagnosticMenu[idx].first
                          << color::RESET << std::endl;
            } else {
                query = userInput;
            }

            auto result = agent.processQuery(query);
            // Final answer is now printed by CleanConsole::printFinalAnswer()
            // Only print here if printFinalAnswer was somehow skipped
            (void)result;
        }

        std::cout << std::endl;
        std::cout << color::GRAY << "  Goodbye!" << color::RESET << std::endl;

    } catch (const std::exception& e) {
        std::cerr << color::RED << color::BOLD << "Fatal error: "
                  << color::RESET << color::RED << e.what()
                  << color::RESET << std::endl;
        return 1;
    }

    return 0;
}
