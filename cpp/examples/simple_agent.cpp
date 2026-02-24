// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Simple demo agent that connects to a Windows MCP server
// and performs system health checks.
//
// Ported from Python: examples/mcp_windows_system_health_agent.py
//
// Usage:
//   ./simple_agent
//   > Run a full system health analysis.
//
// Requirements:
//   - Windows MCP server: uvx windows-mcp
//   - LLM server running at http://localhost:8000/api/v1

#include <iostream>
#include <string>

#include <gaia/agent.h>
#include <gaia/types.h>

/// Windows System Health Agent.
/// Connects to the Windows MCP server for PowerShell, GUI automation, etc.
class WindowsSystemHealthAgent : public gaia::Agent {
public:
    WindowsSystemHealthAgent() : Agent(makeConfig()) {
        init();  // Register tools and compose system prompt

        // Connect to Windows MCP server
        std::cout << "Connecting to Windows MCP server..." << std::endl;
        bool success = connectMcpServer("windows", {
            {"command", "uvx"},
            {"args", {"windows-mcp"}}
        });

        if (success) {
            std::cout << "  Connected to Windows MCP server" << std::endl;
        } else {
            std::cout << "  [ERROR] Failed to connect to Windows MCP server" << std::endl;
            std::cout << "  Ensure 'uvx' is installed: pip install uv" << std::endl;
        }
    }

    ~WindowsSystemHealthAgent() override {
        disconnectAllMcp();
    }

protected:
    std::string getSystemPrompt() const override {
        return R"(You are an expert Windows system administrator using the Windows MCP server.

CRITICAL: Your task is NOT complete until you have pasted the report into Notepad.
DO NOT give a final answer until you have completed ALL of these steps:

## MANDATORY STEPS (must complete all 6):

[ ] Step 1: Get memory info with mcp_windows_Shell
[ ] Step 2: Get disk info with mcp_windows_Shell
[ ] Step 3: Get CPU info with mcp_windows_Shell
[ ] Step 4: Copy formatted report to clipboard with mcp_windows_Shell (Set-Clipboard)
[ ] Step 5: Open Notepad with mcp_windows_Shell (Start-Process notepad)
[ ] Step 6: Paste with mcp_windows_Shortcut (ctrl+v)

---

## Step 1-3: Gather Health Metrics

Use mcp_windows_Shell to execute these PowerShell commands:

Memory: Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json

Disk: Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json

CPU: Get-WmiObject Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json

## Step 4: Copy Report to Clipboard

Create a formatted report and copy to clipboard using Set-Clipboard.

## Step 5: Open Notepad

mcp_windows_Shell with command: Start-Process notepad
Then use mcp_windows_Wait with duration: 2

## Step 6: Paste the Report

mcp_windows_Shortcut with shortcut: ctrl+v

IMPORTANT: Only provide your final answer AFTER you have executed the ctrl+v shortcut.)";
    }

private:
    static gaia::AgentConfig makeConfig() {
        gaia::AgentConfig config;
        config.maxSteps = 55;
        return config;
    }
};

int main() {
    try {
        WindowsSystemHealthAgent agent;

        std::cout << "\nWindows System Health Agent ready! Type 'quit' to exit." << std::endl;
        std::cout << "Try: 'Run a full system health analysis.'" << std::endl;
        std::cout << "  or 'How much RAM and disk space do I have?'" << std::endl;
        std::cout << "  or 'What LLM models can my system run?'\n" << std::endl;

        std::string userInput;
        while (true) {
            std::cout << "You: " << std::flush;
            std::getline(std::cin, userInput);

            if (userInput.empty()) continue;
            if (userInput == "quit" || userInput == "exit" || userInput == "q") break;

            auto result = agent.processQuery(userInput);
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
