#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent-Driven Windows MCP System Health Check with Notepad Output

This demonstrates GAIA's agentic MCP client flow:
- ALL operations go through the Windows MCP server (not direct commands)
- LLM reasons about what data to collect using mcp_windows_Shell
- Outputs the health report to Notepad using MCP GUI automation tools
- Shows thoughts, goals, and plans as the agent works

Workflow:
1. mcp_windows_Shell - Execute PowerShell to get system metrics
2. LLM analyzes results and creates a nicely formatted plain text report
3. mcp_windows_Shell - Copy report to clipboard (Set-Clipboard)
4. mcp_windows_Shell - Open Notepad (Start-Process notepad)
5. mcp_windows_Wait - Wait for Notepad to open
6. mcp_windows_Shortcut - Paste with Ctrl+V

Compare with mcp_windows_system_health_demo.py:
- Demo: Scripted workflow, calls methods directly, no LLM reasoning
- Agent: LLM decides tool calls, shows reasoning, outputs to Notepad

Requirements:
- Windows 11
- Python 3.12+
- Windows MCP: uvx windows-mcp (auto-installed)
- Lemonade server running for LLM reasoning

Run:
    uv run examples/mcp_windows_system_health_agent.py
"""

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin


class WindowsSystemHealthAgent(Agent, MCPClientMixin):
    """Agent-driven Windows system health checker using MCP.

    This agent demonstrates GAIA's MCP client architecture where ALL
    operations go through the Windows MCP server:
    - Uses mcp_windows_Shell for PowerShell command execution
    - Uses mcp_windows_App/Click/Type for GUI automation (Notepad output)
    - LLM decides what data to collect and how to present it
    - Shows thoughts, plans, and reasoning as it works
    """

    def __init__(self, debug: bool = False, **kwargs):
        """Initialize the Windows System Health Agent.

        Args:
            debug: Enable debug output
            **kwargs: Additional arguments passed to Agent
        """
        # Initialize Agent
        Agent.__init__(self, max_steps=55)

        # Initialize MCPClientMixin (creates _mcp_manager)
        MCPClientMixin.__init__(self, auto_load_config=False)

        # Connect to Windows MCP server
        # MCP tools are automatically registered and system prompt is rebuilt
        print("Connecting to Windows MCP server...")
        success = self.connect_mcp_server(
            "windows", {"command": "uvx", "args": ["windows-mcp"]}
        )
        if success:
            print("  Connected to Windows MCP server")
            if debug:
                client = self.get_mcp_client("windows")
                tools = client.list_tools()
                print(f"  Available MCP tools: {[t.name for t in tools]}")
        else:
            print("  [ERROR] Failed to connect to Windows MCP server")
            print("  Ensure 'uvx' is installed: pip install uv")

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are an expert Windows system administrator using the Windows MCP server.

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

After gathering all metrics, create a formatted report and copy it to clipboard:

mcp_windows_Shell with command:
Set-Clipboard -Value "========================================`n     WINDOWS SYSTEM HEALTH REPORT`n========================================`n`nMEMORY`n  Total: XX GB`n  Free: XX GB`n`nDISK (C:)`n  Used: XX GB`n  Free: XX GB`n`nCPU`n  Model: XX`n  Load: XX%`n`n----------------------------------------`nASSESSMENT: Good/Fair/Needs Attention`n========================================"

Use `n for newlines in the PowerShell string. Replace XX with actual values.

For the ASSESSMENT section, include LLM capability analysis based on available RAM:
- Under 16GB: Can run small models (1-3B parameters)
- 16-32GB: Can run medium models (7-14B parameters)
- 32-64GB: Can run large models (30B+ parameters)
- 64GB+: Can run very large models with fast inference

Add 2-3 bullet points recommending what size models the system can handle for GAIA/Lemonade.

## Step 5: Open Notepad

mcp_windows_Shell with command: Start-Process notepad

Then use mcp_windows_Wait with duration: 2

## Step 6: Paste the Report

mcp_windows_Shortcut with shortcut: ctrl+v

---

IMPORTANT: Only provide your final answer AFTER you have executed the ctrl+v shortcut.
The task is complete when the report is visible in Notepad."""

    def _register_tools(self) -> None:
        """Register agent tools.

        MCP tools are automatically registered by MCPClientMixin
        when connect_mcp_server() is called.
        """
        pass


if __name__ == "__main__":
    agent = WindowsSystemHealthAgent()

    print("Windows System Health Agent ready! Type 'quit' to exit.")
    print("Try: 'Run a full system health check and paste the report into Notepad'")
    print("  or 'How much RAM and disk space do I have?'")
    print("  or 'What LLM models can my system run?'\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input:
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")
