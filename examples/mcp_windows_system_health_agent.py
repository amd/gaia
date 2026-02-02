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
2. LLM analyzes results and creates a plain text report
3. mcp_windows_Shell - Copy report to clipboard (Set-Clipboard)
4. mcp_windows_App - Open Notepad
5. mcp_windows_Click - Click the text area
6. mcp_windows_Type - Paste with Ctrl+V (^v) - atomic, no focus loss!

Compare with mcp_windows_system_health_demo.py:
- Demo: Scripted workflow, calls methods directly, no LLM reasoning
- Agent: LLM decides tool calls, shows reasoning, outputs to Notepad

Requirements:
- Windows 10/11
- Python 3.13+
- Windows MCP: uvx windows-mcp (auto-installed)
- Lemonade server running for LLM reasoning

Run:
    # Default: health check with Notepad output
    uv run examples/mcp_windows_system_health_agent.py

    # Custom queries
    uv run examples/mcp_windows_system_health_agent.py --query "Check memory only and type to Notepad"
    uv run examples/mcp_windows_system_health_agent.py --query "Check disk space and show in Notepad"

    # Debug mode to see more details
    uv run examples/mcp_windows_system_health_agent.py --debug
"""

import argparse
import sys

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
        # Configure agent defaults
        kwargs.setdefault("max_steps", 20)  # Allow multiple tool calls for full workflow
        kwargs.setdefault("silent_mode", False)  # Show agent output
        kwargs.setdefault("streaming", True)  # Stream LLM responses

        # Initialize Agent
        Agent.__init__(self, debug=debug, **kwargs)

        # Initialize MCPClientMixin (creates _mcp_manager)
        MCPClientMixin.__init__(self)

        # Connect to Windows MCP server
        # MCP tools are automatically registered and system prompt is rebuilt
        print("Connecting to Windows MCP server...")
        success = self.connect_mcp_server("windows", "uvx windows-mcp")
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

ALL operations MUST use the Windows MCP tools - you cannot run commands directly.

Your task is to:
1. Query system health using mcp_windows_Shell (PowerShell via MCP)
2. Analyze the data and create a plain text report
3. Output the report to Notepad using Windows MCP GUI automation tools

## Step 1: Gather Health Metrics via Windows MCP

Use mcp_windows_Shell to execute PowerShell commands through the Windows MCP server:

**Memory Usage:**
```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json
```

**Disk Space:**
```powershell
Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}} | ConvertTo-Json
```

**CPU Usage:**
```powershell
Get-WmiObject Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores | ConvertTo-Json
```

## Step 2: Analyze and Format Report

After gathering metrics, create a plain text health report with:
- Overall health assessment (Good/Fair/Needs Attention)
- Key findings (2-3 bullet points)
- Recommendations (2-3 items)

## Step 3: Output to Notepad (Using Clipboard for Reliability)

IMPORTANT: The Type tool can lose focus during long text entry. Use the clipboard approach instead:

1. **Copy report to clipboard using PowerShell:**
   Use mcp_windows_Shell tool to set the clipboard. For multi-line text, construct the command like:
   {"command": "powershell -c \\"Set-Clipboard -Value 'SYSTEM HEALTH REPORT\\n\\nOverall Health: Good\\n\\nKey Findings:\\n- Memory: XX GB free\\n- Disk: XX GB free\\n\\nRecommendations:\\n- Item 1\\""}

   Note: Use \\n for newlines in the PowerShell string.

2. **Open Notepad:**
   Use mcp_windows_App tool: {"mode": "launch", "name": "notepad"}

3. **Paste with Ctrl+V (DO NOT use Click - it can steal focus!):**
   Use mcp_windows_Shortcut tool to send Ctrl+V: {"key": "ctrl+v"}

   IMPORTANT: Do NOT use mcp_windows_Click before pasting - clicking can cause focus issues.
   The Shortcut tool sends the keyboard shortcut to the active window (Notepad).

## Guidelines

1. ALWAYS use the mcp_windows_Shell tool to get REAL data - NEVER make up metrics
2. Format the report as plain text (no markdown, no special characters)
3. Use the clipboard approach (Set-Clipboard + Shortcut Ctrl+V) - it's atomic and won't lose focus
4. Do NOT use mcp_windows_Click or mcp_windows_App with mode "switch" - they cause focus issues!
5. After pasting to Notepad, provide a brief confirmation as your final answer

**IMPORTANT:** Your final answer should confirm the report was pasted to Notepad, not repeat the entire report."""

    def _register_tools(self) -> None:
        """Register agent tools.

        MCP tools are automatically registered by MCPClientMixin
        when connect_mcp_server() is called.
        """
        pass


def main():
    """Main entry point for the agent-driven demo."""
    parser = argparse.ArgumentParser(
        description="Agent-Driven Windows System Health Check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: health check with Notepad output
  uv run examples/mcp_windows_system_health_agent.py

  # Custom queries (all output to Notepad)
  uv run examples/mcp_windows_system_health_agent.py --query "Check memory only and type to Notepad"
  uv run examples/mcp_windows_system_health_agent.py --query "Check disk space and show in Notepad"
""",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="Check my Windows system health (memory, disk, CPU) and type the report into Notepad.",
        help="Custom query for the agent",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum agent steps (default: 20)",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("GAIA Agent-Driven Windows MCP Health Check")
    print("=" * 60)
    print()
    print("This demo shows GAIA's agentic MCP client flow:")
    print("  - ALL operations go through Windows MCP server")
    print("  - mcp_windows_Shell for PowerShell commands")
    print("  - mcp_windows_App/Click/Type for Notepad output")
    print("  - LLM reasoning and planning visible")
    print()
    print(f"Query: {args.query}")
    print("-" * 60)
    print()

    try:
        # Create the agent
        agent = WindowsSystemHealthAgent(
            debug=args.debug,
            max_steps=args.max_steps,
        )

        # Process the query - agent loop handles everything
        result = agent.process_query(args.query)

        # The agent loop displays output automatically
        # But we can also access the final answer programmatically
        print()
        print("=" * 60)
        print("AGENT COMPLETE")
        print("=" * 60)

        if result.get("answer"):
            print("\nFinal Answer:")
            print("-" * 40)
            print(result["answer"])

        sys.exit(0)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Agent failed: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
