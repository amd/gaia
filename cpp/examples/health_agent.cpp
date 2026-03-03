// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// System Health Agent — connects to a Windows MCP server and performs
// system health checks with a polished terminal UI.
//
// Ported from Python: examples/mcp_windows_system_health_agent.py
//
// Usage:
//   ./health_agent
//   > Run a full system health analysis.
//
// Requirements:
//   - Windows MCP server: uvx windows-mcp
//   - LLM server running at http://localhost:8000/api/v1

#include <algorithm>
#include <cctype>
#include <iostream>
#include <string>
#include <utility>

#include <gaia/agent.h>
#include <gaia/clean_console.h>
#include <gaia/types.h>

// Alias for convenience — matches wifi_agent pattern
namespace color = gaia::color;

// ---------------------------------------------------------------------------
// Windows System Health Agent
// ---------------------------------------------------------------------------
/// Connects to the Windows MCP server for PowerShell, GUI automation, etc.
class WindowsSystemHealthAgent : public gaia::Agent {
public:
    explicit WindowsSystemHealthAgent(const std::string& modelId)
        : Agent(makeConfig(modelId)) {
        setOutputHandler(std::make_unique<gaia::CleanConsole>());
        init();  // Register tools and compose system prompt

        // Connect to Windows MCP server
        std::cout << color::GRAY << "  Connecting to Windows MCP server..."
                  << color::RESET << std::endl;
        bool success = connectMcpServer("windows", {
            {"command", "uvx"},
            {"args", {"windows-mcp"}}
        });

        if (success) {
            std::cout << color::GREEN << "  Connected to Windows MCP server"
                      << color::RESET << std::endl;
        } else {
            std::cout << color::RED << color::BOLD
                      << "  [ERROR] " << color::RESET << color::RED
                      << "Failed to connect to Windows MCP server"
                      << color::RESET << std::endl;
            std::cout << color::GRAY
                      << "  Ensure 'uvx' is installed: pip install uv"
                      << color::RESET << std::endl;
        }
    }

    ~WindowsSystemHealthAgent() override {
        disconnectAllMcp();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are an expert Windows system administrator using the Windows MCP server.

You are an intelligent agent. Given a user's question, decide which tools are relevant, run them one at a time, reason about each result, adapt your approach based on what you find, and continue until the question is answered.

IMPORTANT: Be concise. Keep FINDING and DECISION to 1-2 sentences each. No filler words.

CRITICAL: Do NOT provide a final "answer" until you have finished ALL relevant tool calls. If you still have tools to run, you MUST call the next tool - do NOT stop early with an answer. Only provide an "answer" when your investigation is truly complete.

## REASONING PROTOCOL

After EVERY tool result, structure your thought using these exact prefixes:

FINDING: <1-2 sentences: key facts and values from the output>
DECISION: <1 sentence: what to do next and WHY>

The user sees FINDING and DECISION highlighted in the UI. Use them to make your reasoning visible.

## AVAILABLE POWERSHELL COMMANDS

Use mcp_windows_Shell to execute these PowerShell commands:

Memory: Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json

Disk: Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json

CPU: Get-CimInstance Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json

GPU: Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion, VideoProcessor | ConvertTo-Json

Top Processes: Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name, @{N='CPU_Sec';E={[math]::Round($_.CPU,1)}}, @{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, Id | ConvertTo-Json

Network Config: Get-NetIPConfiguration | Select-Object InterfaceAlias, @{N='IPv4';E={($_.IPv4Address).IPAddress}}, @{N='Gateway';E={($_.IPv4DefaultGateway).NextHop}}, @{N='DNS';E={($_.DNSServer).ServerAddresses -join ', '}} | ConvertTo-Json

Startup Programs: Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location | ConvertTo-Json

Recent System Errors: Get-WinEvent -FilterHashtable @{LogName='System'; Level=2; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object TimeCreated, Id, Message | ConvertTo-Json

Windows Update Status: Get-HotFix | Sort-Object InstalledOn -Descending -ErrorAction SilentlyContinue | Select-Object -First 10 HotFixID, Description, InstalledOn | ConvertTo-Json

Battery Health: Get-CimInstance Win32_Battery | Select-Object @{N='Status';E={$_.Status}}, @{N='ChargePercent';E={$_.EstimatedChargeRemaining}}, @{N='RunTimeMins';E={$_.EstimatedRunTime}}, @{N='Chemistry';E={switch($_.Chemistry){1{'Other'}2{'Unknown'}3{'Lead Acid'}4{'Nickel Cadmium'}5{'Nickel Metal Hydride'}6{'Lithium-ion'}7{'Zinc air'}8{'Lithium Polymer'}default{'N/A'}}}} | ConvertTo-Json

Installed Software: Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where-Object {$_.DisplayName -ne $null} | Sort-Object InstallDate -Descending -ErrorAction SilentlyContinue | Select-Object -First 20 DisplayName, DisplayVersion, Publisher, InstallDate | ConvertTo-Json

Storage Health (SMART): Get-PhysicalDisk | Select-Object FriendlyName, MediaType, @{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}, HealthStatus, OperationalStatus | ConvertTo-Json

## HTML REPORT GENERATION

When asked to generate a report, use this PowerShell pattern to create a styled HTML file:

1. Gather all requested data into PowerShell variables
2. Build an HTML string with inline CSS styling (professional look: font-family Segoe UI, tables with borders, color-coded status, a header with date/hostname)
3. Write the HTML to a temp file: $htmlPath = "$env:TEMP\SystemHealthReport_$(Get-Date -Format 'yyyyMMdd_HHmmss').html"
4. Set-Content -Path $htmlPath -Value $htmlContent
5. Open the report in the default browser: Start-Process $htmlPath
6. Copy the file path to clipboard: Set-Clipboard -Value $htmlPath

Tell the user the report file path so they can find it. The HTML report is viewable in any browser and can be printed to PDF via the browser's print dialog (Ctrl+P).

## HOW TO APPROACH A QUERY

Your approach should be entirely driven by the query:
- "Full system health analysis" -> gather ALL core metrics (memory, disk, CPU, GPU), write report to temp file, open in Notepad
- "Full diagnostics + report" -> gather ALL metrics (memory, disk, CPU, GPU, processes, network, startup, errors, updates, battery, software, storage), generate a styled HTML report
- "Check memory" -> just run the memory command, report the result, stop
- "Check disk space" -> just run the disk command, report, stop
- "Check CPU info" -> just run the CPU command, report, stop
- "Check GPU info" -> just run the GPU command, report, stop
- "Top processes" -> just run the top processes command, report, stop
- "Network config" -> just run the network command, report, stop
- "Startup programs" -> just run the startup command, report, stop
- "Recent errors" -> just run the event log command, report, stop
- "Windows updates" -> just run the hotfix command, report, stop
- "Battery health" -> just run the battery command, report, stop
- "Installed software" -> just run the software command, report, stop
- "Storage health" -> just run the SMART command, report, stop
- "What LLM models can I run?" -> gather RAM, disk, CPU, GPU specs, then recommend models using the reference table below

For targeted queries (single metric), just run the relevant command and give a direct answer. Do NOT open Notepad or generate a report for simple queries.

## LLM MODEL RECOMMENDATIONS BY HARDWARE

Use this reference when recommending models. RAM = system RAM, VRAM = GPU dedicated memory.

- 8GB RAM, no dedicated GPU: Qwen3-0.6B-GGUF, Phi-4-mini-3.8B-GGUF
- 16GB RAM, any GPU: Qwen3-4B-GGUF, Llama-3.2-3B-GGUF, Phi-4-mini-3.8B-GGUF
- 16GB RAM + AMD NPU (Ryzen AI): Qwen3-4B-FLM (NPU-accelerated via Lemonade)
- 32GB RAM, 8GB+ VRAM: Qwen3-8B-GGUF, Llama-3.1-8B-GGUF, Qwen3-Coder-30B-A3B-GGUF (MoE, only 3B active)
- 64GB+ RAM, 16GB+ VRAM: Llama-3.3-70B-GGUF (Q4), Qwen3-32B-GGUF, DeepSeek-R1-Distill-Qwen-32B-GGUF

Recommend Lemonade Server (lemonade-server) as the inference backend for AMD hardware. GGUF models run on GPU, FLM models run on NPU.

## FULL ANALYSIS PROTOCOL (only for "full analysis" requests)

For a full analysis, complete ALL of these steps:
1. Get memory info with mcp_windows_Shell
2. Get disk info with mcp_windows_Shell
3. Get CPU info with mcp_windows_Shell
4. Get GPU info with mcp_windows_Shell
5. Build a formatted report and save it to a temp file, then open in Notepad. Use a SINGLE mcp_windows_Shell call. Build the report using an array of lines joined with real newlines. Example pattern:

$lines = @('System Health Report', '', '--- Memory ---', 'Total: X GB, Free: Y GB', '', '--- Disk ---', 'C: X used, Y free', '', '--- CPU ---', 'Name, Cores, Load', '', '--- GPU ---', 'Name, VRAM'); $path = Join-Path $env:TEMP ('SystemHealth_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.txt'); $lines -join [Environment]::NewLine | Out-File -FilePath $path -Encoding UTF8; Start-Process notepad $path; $path

Replace placeholder values with actual data from steps 1-4. IMPORTANT: Use an array of strings joined with [Environment]::NewLine. Do NOT use literal backslash-n characters.

Do NOT give a final answer until ALL steps above are completed.

## COMPREHENSIVE DIAGNOSTICS + REPORT PROTOCOL

For a comprehensive diagnostics report, gather ALL of these in order:
1. Memory info
2. Disk info
3. CPU info
4. GPU info
5. Top 10 processes by CPU
6. Network configuration
7. Startup programs
8. Recent system errors (last 24h)
9. Windows Update history
10. Battery health (if laptop)
11. Installed software (top 20)
12. Storage health (SMART)
13. Build a styled HTML report with all results and save it
14. Open the HTML report in the default browser

Do NOT give a final answer until ALL data is gathered and the report is generated.

## FINAL ANSWER

Only provide an "answer" after ALL tool calls are complete.
IMPORTANT: Use only ASCII characters. Do NOT use em-dashes, en-dashes, or unicode symbols. Use a hyphen (-) or colon (:) instead.
Use ** around key values (RAM amounts, disk sizes, CPU names, percentages, GPU names) to highlight them.
Do NOT use markdown tables. Use bullet points and hyphens only.

## GOAL TRACKING

Always set a short `goal` field (3-6 words) describing your current objective.)";
    }

private:
    static gaia::AgentConfig makeConfig(const std::string& modelId) {
        gaia::AgentConfig config;
        config.maxSteps = 75;
        config.modelId = modelId;
        config.contextSize = 32768; // 32K needed for "Run ALL diagnostics" (12+ tool calls)
        return config;
    }
};

// ---------------------------------------------------------------------------
// Health-check menu — maps numbered selections to pre-written prompts
// ---------------------------------------------------------------------------
static const std::pair<std::string, std::string> kHealthMenu[] = {
    {"Full system health analysis",
     "Run a full system health analysis. Check memory, disk space, CPU, and GPU info. Write a formatted report to a temp file and open it in Notepad."},
    {"Check memory usage",
     "Check the system memory usage. Report total RAM, free RAM, and usage percentage."},
    {"Check disk space",
     "Check disk space on all drives. Report used and free space for each drive."},
    {"Check CPU info",
     "Check the CPU information. Report the processor name, number of cores, and current load percentage."},
    {"Check GPU info",
     "Check the GPU information. Report the GPU name, VRAM, driver version, and video processor."},
    {"Top processes by CPU usage",
     "Show the top 10 processes by CPU usage. Report process name, CPU time, memory usage, and PID."},
    {"Network configuration",
     "Check the network configuration. Report interface names, IPv4 addresses, gateways, and DNS servers."},
    {"Startup programs",
     "List programs that run at startup. Report name, command, and location (registry key or startup folder)."},
    {"Recent system errors (last 24h)",
     "Check the Windows Event Log for system errors in the last 24 hours. Report time, event ID, and message for the 10 most recent errors."},
    {"Windows Update status",
     "Check the Windows Update history. Report the 10 most recent hotfixes with ID, description, and install date."},
    {"Battery health",
     "Check the battery health status. Report charge percentage, estimated run time, battery chemistry, and overall status."},
    {"Installed software (top 20)",
     "List the 20 most recently installed programs. Report name, version, publisher, and install date."},
    {"Storage health (SMART)",
     "Check storage device health using SMART data. Report disk name, media type, size, health status, and operational status."},
    {"What LLM models can my system run?",
     "Analyze the system specs (RAM, disk, CPU, GPU) and recommend which LLM models this machine can run locally. Consider models like Qwen3, Llama, and Phi."},
    {"Run ALL diagnostics + generate report",
     "Run a comprehensive system diagnostic. Gather ALL system information: memory, disk, CPU, GPU, top processes, network config, startup programs, recent system errors, Windows Update status, battery health, installed software, and storage health. Then generate a detailed styled HTML report and open it in the browser."},
};
static constexpr size_t kMenuSize = sizeof(kHealthMenu) / sizeof(kHealthMenu[0]);

static void printHealthMenu() {
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    for (size_t i = 0; i < kMenuSize; ++i) {
        size_t num = i + 1;
        // Right-align numbers for clean columns (e.g. " [1]" vs "[15]")
        if (num < 10)
            std::cout << color::YELLOW << "   [" << num << "] ";
        else
            std::cout << color::YELLOW << "  [" << num << "] ";
        std::cout << color::RESET << color::WHITE
                  << kHealthMenu[i].first
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
// main — model selection + interactive loop with health-check menu
// ---------------------------------------------------------------------------
int main() {
    try {
        // --- Banner ---
        std::cout << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "  ========================================================================================"
                  << color::RESET << std::endl;
        std::cout << color::CYAN << color::BOLD
                  << "   System Health Agent  |  GAIA C++ Agent Framework  |  Local Inference"
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
        if (!std::getline(std::cin, modelChoice)) return 1;

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

        WindowsSystemHealthAgent agent(modelId);

        std::cout << std::endl;
        std::cout << color::GREEN << color::BOLD << "  Ready!"
                  << color::RESET << std::endl;
        std::cout << std::endl;

        // --- Interactive loop with health-check menu ---
        std::string userInput;
        while (true) {
            printHealthMenu();
            std::cout << color::BOLD << "  > " << color::RESET << std::flush;
            if (!std::getline(std::cin, userInput)) break;

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q") break;

            // Map numbered selection to pre-written prompt
            std::string query;
            bool isNumber = !userInput.empty() &&
                std::all_of(userInput.begin(), userInput.end(),
                            [](unsigned char c) { return std::isdigit(c); });
            if (isNumber) {
                int choice = 0;
                try { choice = std::stoi(userInput); }
                catch (const std::out_of_range&) { choice = -1; }
                if (choice >= 1 && choice <= static_cast<int>(kMenuSize)) {
                    size_t idx = static_cast<size_t>(choice - 1);
                    query = kHealthMenu[idx].second;
                    std::cout << color::CYAN << "  > "
                              << kHealthMenu[idx].first
                              << color::RESET << std::endl;
                } else {
                    std::cout << color::RED << "  Invalid selection. Enter 1-"
                              << kMenuSize << " or type a question."
                              << color::RESET << std::endl;
                    continue;
                }
            } else {
                query = userInput;
            }

            auto result = agent.processQuery(query);
            // Final answer is printed by CleanConsole::printFinalAnswer()
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
