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
//   > Why is my system slow?
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
        return R"(You are an expert Windows system administrator.

You are an investigative agent. Given a user's question, gather data from multiple sources, correlate findings, reason about root causes, and provide actionable conclusions.

CRITICAL RULES:
- You have ONE tool for running commands: mcp_windows_Shell. Pass PowerShell commands as the "command" parameter.
- Each investigation requires MULTIPLE mcp_windows_Shell calls. Do NOT stop after one call.
- Follow the investigation strategy for the query type. Call the next tool - do NOT answer early.
- Do any math yourself - do NOT call a tool for simple arithmetic.
- Be concise. Keep FINDING and DECISION to 1-2 sentences each.

## REASONING PROTOCOL

After EVERY tool result, think using these exact prefixes:

FINDING: <key facts from the output>
DECISION: <what to check next and why>

## INVESTIGATION STRATEGIES

Each query type requires a SPECIFIC SEQUENCE of mcp_windows_Shell calls. Copy each PowerShell command EXACTLY as shown below.

### "Why is my system slow?"
Call mcp_windows_Shell 4 times with these commands:
1. command: Get-CimInstance Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json
2. command: Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name, @{N='CPU_Sec';E={[math]::Round($_.CPU,1)}}, @{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, Id | ConvertTo-Json
3. command: Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json
4. command: Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json
Then correlate all findings and explain WHY the system is slow.

### "Is my system secure and up to date?"
Call mcp_windows_Shell 3 times with these commands:
1. command: Get-HotFix | Sort-Object InstalledOn -Descending -ErrorAction SilentlyContinue | Select-Object -First 10 HotFixID, Description, InstalledOn | ConvertTo-Json
2. command: Get-WinEvent -FilterHashtable @{LogName='System'; Level=2; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object TimeCreated, Id, Message | ConvertTo-Json
3. command: Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location | ConvertTo-Json
Then assess whether the system is well-maintained and flag risks.

### "Can I free up disk space?"
Call mcp_windows_Shell 3 times with these commands:
1. command: Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json
2. command: Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum | Select-Object @{N='TempSizeGB';E={[math]::Round($_.Sum/1GB,2)}}, Count | ConvertTo-Json
3. command: Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Where-Object {$_.DisplayName -ne $null -and $_.EstimatedSize -ne $null} | Sort-Object EstimatedSize -Descending | Select-Object -First 20 DisplayName, @{N='SizeMB';E={[math]::Round($_.EstimatedSize/1024,1)}}, Publisher | ConvertTo-Json
Then recommend what to clean up and estimate recoverable space.

### "Diagnose recent system errors"
Call mcp_windows_Shell 3 times with these commands:
1. command: Get-WinEvent -FilterHashtable @{LogName='System'; Level=2; StartTime=(Get-Date).AddHours(-24)} -MaxEvents 10 -ErrorAction SilentlyContinue | Select-Object TimeCreated, Id, Message | ConvertTo-Json
2. command: Get-PhysicalDisk | Select-Object FriendlyName, MediaType, @{N='SizeGB';E={[math]::Round($_.Size/1GB,1)}}, HealthStatus, OperationalStatus | ConvertTo-Json
3. command: Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json
Then explain what the errors mean and whether hardware is at risk.

### "How's my battery holding up?"
Call mcp_windows_Shell 3 times with these commands:
1. command: Get-CimInstance Win32_Battery | Select-Object @{N='Status';E={$_.Status}}, @{N='ChargePercent';E={$_.EstimatedChargeRemaining}}, @{N='RunTimeMins';E={$_.EstimatedRunTime}}, @{N='Chemistry';E={switch($_.Chemistry){1{'Other'}2{'Unknown'}3{'Lead Acid'}4{'Nickel Cadmium'}5{'Nickel Metal Hydride'}6{'Lithium-ion'}7{'Zinc air'}8{'Lithium Polymer'}default{'N/A'}}}} | ConvertTo-Json
2. command: Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 Name, @{N='CPU_Sec';E={[math]::Round($_.CPU,1)}}, @{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}}, Id | ConvertTo-Json
3. command: Get-CimInstance Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json
Then assess battery condition and what's consuming the most power.

### "Full system health report"
Call mcp_windows_Shell 12 times for: memory, disk, CPU, GPU, top processes, network, startup programs, system errors, Windows updates, battery, installed software, storage health. Use the commands from the strategies above. Then write a formatted report to a temp file and open in Notepad (see REPORT PROTOCOL below).

## REPORT PROTOCOL (Full system health report only)

After gathering ALL metrics, build a formatted report and save it to a temp file, then open in Notepad. Use a SINGLE mcp_windows_Shell call with an array of lines joined with real newlines:

$lines = @('System Health Report', '', '--- Memory ---', 'Total: X GB, Free: Y GB', '', '--- Disk ---', 'C: X used, Y free', '', '--- CPU ---', 'Name, Cores, Load', '', '--- GPU ---', 'Name, VRAM'); $path = Join-Path $env:TEMP ('SystemHealth_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.txt'); $lines -join [Environment]::NewLine | Out-File -FilePath $path -Encoding UTF8; Start-Process notepad $path; $path

Replace placeholder values with actual data. Use [Environment]::NewLine, NOT literal backslash-n.

## FINAL ANSWER FORMAT

Only provide a final answer after ALL tool calls in the strategy are complete.
Your answer must be ONLY clean text. No FINDING, DECISION, thought, goal, or JSON.
Start with a one-sentence assessment. Then bullet-point findings with ** around key values. Then recommendations.
Use only ASCII characters. Use hyphens (-) not em-dashes.)";
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
    {"Why is my system slow?",
     "Why is my system slow?"},
    {"Is my system secure and up to date?",
     "Is my system secure and up to date?"},
    {"Can I free up disk space?",
     "Can I free up disk space?"},
    {"Diagnose recent system errors",
     "Diagnose my recent system errors."},
    {"How's my battery holding up?",
     "How's my battery holding up?"},
    {"Full system health report",
     "Run a full system health report."},
};
static constexpr size_t kMenuSize = sizeof(kHealthMenu) / sizeof(kHealthMenu[0]);

static void printHealthMenu() {
    std::cout << color::CYAN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    for (size_t i = 0; i < kMenuSize; ++i) {
        size_t num = i + 1;
        std::cout << color::YELLOW << "  [" << num << "] "
                  << color::RESET << color::WHITE
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
