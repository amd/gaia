#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent-Driven Windows MCP System Health Check

This demonstrates GAIA's agent-driven approach to system health monitoring:
- LLM reasons about what data to collect
- Shows thoughts, goals, and plans as the agent works
- Adapts based on findings (unlike scripted workflow)
- Provides AI-powered analysis and recommendations

Compare with mcp_windows_system_health_demo.py:
- Demo: Scripted workflow, calls methods directly, no LLM reasoning visible
- Agent: LLM decides tool calls, shows reasoning, adapts to queries

Requirements:
- Windows 10/11
- Python 3.13+
- Windows MCP: uvx windows-mcp (auto-installed)
- Lemonade server running for LLM reasoning

Run:
    # Default health check
    uv run examples/mcp_windows_system_health_agent.py

    # Custom queries
    uv run examples/mcp_windows_system_health_agent.py --query "Check if disk is running low"
    uv run examples/mcp_windows_system_health_agent.py --query "What processes are using the most memory?"
    uv run examples/mcp_windows_system_health_agent.py --query "Is my battery healthy?"

    # Debug mode to see more details
    uv run examples/mcp_windows_system_health_agent.py --debug
"""

import argparse
import sys

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin


class WindowsSystemHealthAgent(Agent, MCPClientMixin):
    """Agent-driven Windows system health checker using MCP.

    Unlike the scripted demo, this agent:
    - Uses LLM to decide what data to collect
    - Shows thoughts, plans, and reasoning
    - Adapts queries based on context
    - Can handle varied user requests
    """

    def __init__(self, debug: bool = False, **kwargs):
        """Initialize the Windows System Health Agent.

        Args:
            debug: Enable debug output
            **kwargs: Additional arguments passed to Agent
        """
        # Configure agent defaults
        kwargs.setdefault("max_steps", 15)  # Allow multiple tool calls
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
        return """You are an expert Windows system administrator.

Your task is to analyze Windows system health by gathering real metrics and providing recommendations.

## Available Data Collection

Use the Shell tool to execute PowerShell commands for gathering system metrics:

**Memory Usage:**
```powershell
Get-CimInstance Win32_OperatingSystem | Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, @{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | ConvertTo-Json
```

**Top Memory Processes:**
```powershell
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 Name, @{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,2)}} | ConvertTo-Json
```

**Disk Space:**
```powershell
Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Used -ne $null} | Select-Object Name, @{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, @{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}}, @{N='TotalGB';E={[math]::Round(($_.Used+$_.Free)/1GB,2)}} | ConvertTo-Json
```

**CPU Usage:**
```powershell
Get-WmiObject Win32_Processor | Select-Object Name, LoadPercentage, NumberOfCores, NumberOfLogicalProcessors | ConvertTo-Json
```

**Battery Status:**
```powershell
Get-WmiObject Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus | ConvertTo-Json
```
Battery status codes: 1=Discharging, 2=AC Connected, 3=Fully Charged, 4=Low, 5=Critical, 6=Charging

## Guidelines

1. ALWAYS use the Shell tool to get REAL data - NEVER make up metrics
2. Gather all relevant metrics before providing analysis
3. After gathering data, provide:
   - Overall health assessment (Good/Fair/Needs Attention)
   - Key findings (2-3 bullet points)
   - Actionable recommendations (2-3 items)
4. Be concise but thorough
5. If a metric is unavailable (e.g., no battery on desktop), note it and continue"""

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
  # Default comprehensive health check
  uv run examples/mcp_windows_system_health_agent.py

  # Check specific aspects
  uv run examples/mcp_windows_system_health_agent.py --query "Is my disk running low?"
  uv run examples/mcp_windows_system_health_agent.py --query "What's using all my memory?"
  uv run examples/mcp_windows_system_health_agent.py --query "Check CPU usage"
""",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="Check my Windows system health comprehensively: memory, disk, CPU, and battery. Provide a detailed report with your analysis and recommendations.",
        help="Custom query for the agent (default: comprehensive health check)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=15,
        help="Maximum agent steps (default: 15)",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("GAIA Agent-Driven Windows System Health Check")
    print("=" * 60)
    print()
    print("This agent-driven demo shows:")
    print("  - LLM reasoning and planning")
    print("  - Tool calls with arguments")
    print("  - Adaptive responses based on query")
    print("  - AI-powered analysis")
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
