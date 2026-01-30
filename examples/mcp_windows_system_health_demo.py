#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Windows MCP System Health Check Demo - GAIA MCP Client Showcase

Demonstrates GAIA's MCP client orchestrating Windows automation to:
1. Query system memory usage via PowerShell
2. Check disk space on all drives
3. Get battery status and charge level
4. Monitor CPU usage percentage
5. Generate an AI-powered health report with recommendations (using local Lemonade LLM)
6. Display results in Notepad

This showcase uses the Shell tool to execute PowerShell commands for
querying system health metrics, demonstrating practical AI agent capability.

Requirements:
- Windows 10/11
- Python 3.13+
- Windows MCP: uvx windows-mcp (auto-installed)
- Lemonade server running for AI analysis (optional with --skip-summary)

Run:
    # With local LLM (Lemonade server must be running)
    python examples/mcp_windows_system_health_demo.py

    # Skip AI analysis (useful for testing MCP tools only)
    python examples/mcp_windows_system_health_demo.py --skip-summary

    # Run individual tests
    python examples/mcp_windows_system_health_demo.py --test memory
    python examples/mcp_windows_system_health_demo.py --test disk
    python examples/mcp_windows_system_health_demo.py --test battery
    python examples/mcp_windows_system_health_demo.py --test cpu
    python examples/mcp_windows_system_health_demo.py --test shell
    python examples/mcp_windows_system_health_demo.py --test all

Windows MCP Shell Tool:
    Shell - command: string (PowerShell command), timeout: int (optional)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin


class WindowsSystemHealthDemo(Agent, MCPClientMixin):
    """Demo agent using Windows MCP Shell tool to check system health.

    This agent demonstrates:
    - Connecting to Windows MCP server
    - Executing PowerShell commands via Shell tool
    - Parsing system health metrics
    - AI-powered health report generation
    - GUI automation for Notepad results display
    """

    # Timing configuration (seconds)
    WAIT_NOTEPAD_LAUNCH = 2.0
    WAIT_SHELL_COMMAND = 5.0

    def __init__(
        self,
        skip_summary: bool = False,
        debug: bool = False,
        **kwargs,
    ):
        """Initialize the Windows System Health Demo agent.

        Args:
            skip_summary: Skip AI analysis (useful for testing MCP tools only)
            debug: Enable debug output
            **kwargs: Additional arguments passed to Agent
        """
        # Skip Lemonade initialization if skipping summary
        kwargs.setdefault("skip_lemonade", skip_summary)
        kwargs.setdefault("max_steps", 20)
        kwargs.setdefault("silent_mode", True)

        # Initialize Agent (always use local Lemonade LLM)
        Agent.__init__(self, debug=debug, **kwargs)

        # Initialize MCPClientMixin
        MCPClientMixin.__init__(self)

        self.skip_summary = skip_summary
        self.debug_mode = debug

        # Execution log for display
        self._execution_log: List[str] = []

        # Health check results storage
        self._health_results: Dict[str, Any] = {}

        # Connect to Windows MCP server
        self._connect_windows_mcp()

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful AI assistant that monitors Windows system health.
You can execute PowerShell commands to query system metrics and provide
actionable recommendations based on the results."""

    def _register_tools(self) -> None:
        """Register agent tools (MCP tools are auto-registered)."""
        # MCP tools are automatically registered by MCPClientMixin
        pass

    def _connect_windows_mcp(self) -> bool:
        """Connect to the Windows MCP server.

        Returns:
            bool: True if connection successful
        """
        self._log("Connecting to Windows MCP server...")

        # Windows MCP is installed via uvx (uv tool)
        success = self.connect_mcp_server("windows", "uvx windows-mcp")

        if success:
            self._log("[SUCCESS] Connected to Windows MCP server", "SUCCESS")

            # List available tools for debugging
            if self.debug_mode:
                client = self.get_mcp_client("windows")
                tools = client.list_tools()
                self._log(f"  Available tools: {[t.name for t in tools]}")
        else:
            self._log("[ERROR] Failed to connect to Windows MCP server", "ERROR")

        return success

    def _log(self, message: str, level: str = "INFO") -> None:
        """Log a message with timestamp.

        Args:
            message: Message to log
            level: Log level (INFO, STEP, SUCCESS, ERROR, WARNING)
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = (
            f"[{timestamp}] [{level}] {message}"
            if level != "INFO"
            else f"[{timestamp}] {message}"
        )
        self._execution_log.append(formatted)
        print(formatted)

    def _call_windows_tool(
        self, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Call a Windows MCP tool with logging and error handling.

        Args:
            tool_name: Name of the MCP tool to call
            args: Tool arguments

        Returns:
            dict: Tool response
        """
        client = self.get_mcp_client("windows")
        if not client:
            self._log("Windows MCP client not connected", "ERROR")
            return {"error": "Not connected to Windows MCP server"}

        self._log(f"  Calling tool: {tool_name}")
        if self.debug_mode:
            self._log(f"    Args: {args}")

        try:
            result = client.call_tool(tool_name, args)
            if self.debug_mode:
                self._log(f"  Result: {result}")
            if "error" in result:
                self._log(f"  Tool error: {result['error']}", "ERROR")
            return result
        except Exception as e:
            self._log(f"  Exception calling tool: {e}", "ERROR")
            return {"error": str(e)}

    def _wait(self, seconds: float, reason: str = "") -> None:
        """Wait for a specified duration.

        Args:
            seconds: Duration to wait
            reason: Optional reason for waiting
        """
        if reason:
            self._log(f"  Waiting {seconds}s for {reason}...")
        time.sleep(seconds)

    def _run_powershell(self, command: str, timeout: int = 30) -> Optional[str]:
        """Execute a PowerShell command via Shell tool.

        Args:
            command: PowerShell command to execute
            timeout: Command timeout in seconds

        Returns:
            str: Command output or None if failed
        """
        result = self._call_windows_tool(
            "Shell", {"command": command, "timeout": timeout}
        )

        if "error" in result:
            return None

        # Extract text content from result
        content = result.get("content", [])
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    text_parts.append(item.get("text", str(item)))
                else:
                    text_parts.append(str(item))
            raw_output = "\n".join(text_parts)
        else:
            raw_output = str(content)

        # Strip "Response: " prefix and "Status Code: X" suffix from Shell output
        output = raw_output
        if output.startswith("Response: "):
            output = output[len("Response: "):]
        # Remove trailing status code line
        if "\n\nStatus Code:" in output:
            output = output.split("\n\nStatus Code:")[0]
        elif "\nStatus Code:" in output:
            output = output.split("\nStatus Code:")[0]

        return output.strip()

    # =========================================================================
    # System Health Check Methods
    # =========================================================================

    def check_memory_usage(self) -> Dict[str, Any]:
        """Get top memory-consuming processes via PowerShell.

        Returns:
            dict: Memory usage information with top processes
        """
        self._log("Checking memory usage...", "STEP")

        # Get top 5 processes by memory usage
        ps_command = (
            "Get-Process | Sort-Object WorkingSet64 -Descending | "
            "Select-Object -First 5 Name, "
            "@{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,2)}} | "
            "ConvertTo-Json"
        )

        output = self._run_powershell(ps_command)

        result = {"top_processes": [], "total_used_gb": 0, "available_gb": 0}

        if output:
            try:
                # Parse JSON output
                processes = json.loads(output)
                if isinstance(processes, dict):
                    processes = [processes]  # Single result case
                result["top_processes"] = processes
                self._log(f"  Found {len(processes)} top memory consumers", "SUCCESS")
            except json.JSONDecodeError:
                self._log(f"  Raw output: {output[:500]}")
                # Try to parse text output
                result["raw_output"] = output

        # Get total memory info
        mem_command = (
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object @{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}}, "
            "@{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | "
            "ConvertTo-Json"
        )

        mem_output = self._run_powershell(mem_command)
        if mem_output:
            try:
                mem_info = json.loads(mem_output)
                result["total_gb"] = mem_info.get("TotalGB", 0)
                result["free_gb"] = mem_info.get("FreeGB", 0)
                result["used_gb"] = round(
                    result["total_gb"] - result["free_gb"], 2
                )
                result["usage_percent"] = round(
                    (result["used_gb"] / result["total_gb"]) * 100, 1
                ) if result["total_gb"] > 0 else 0
                self._log(
                    f"  Memory: {result['used_gb']:.1f} GB used of "
                    f"{result['total_gb']:.1f} GB ({result['usage_percent']}%)",
                    "SUCCESS"
                )
            except json.JSONDecodeError:
                pass

        self._health_results["memory"] = result
        return result

    def check_disk_space(self) -> Dict[str, Any]:
        """Get disk space for all drives via PowerShell.

        Returns:
            dict: Disk space information for all drives
        """
        self._log("Checking disk space...", "STEP")

        ps_command = (
            "Get-PSDrive -PSProvider FileSystem | "
            "Where-Object {$_.Used -ne $null} | "
            "Select-Object Name, "
            "@{N='UsedGB';E={[math]::Round($_.Used/1GB,2)}}, "
            "@{N='FreeGB';E={[math]::Round($_.Free/1GB,2)}}, "
            "@{N='TotalGB';E={[math]::Round(($_.Used+$_.Free)/1GB,2)}} | "
            "ConvertTo-Json"
        )

        output = self._run_powershell(ps_command)

        result = {"drives": [], "warnings": []}

        if output:
            try:
                drives = json.loads(output)
                if isinstance(drives, dict):
                    drives = [drives]  # Single drive case

                for drive in drives:
                    drive_name = drive.get("Name", "?")
                    total = drive.get("TotalGB", 0)
                    free = drive.get("FreeGB", 0)
                    used = drive.get("UsedGB", 0)

                    if total > 0:
                        usage_percent = round((used / total) * 100, 1)
                        drive["UsagePercent"] = usage_percent

                        # Flag drives over 80% full
                        if usage_percent > 80:
                            result["warnings"].append(
                                f"Drive {drive_name}: is {usage_percent}% full"
                            )

                result["drives"] = drives
                self._log(f"  Found {len(drives)} drives", "SUCCESS")

                for drive in drives:
                    name = drive.get("Name", "?")
                    used = drive.get("UsedGB", 0)
                    free = drive.get("FreeGB", 0)
                    pct = drive.get("UsagePercent", 0)
                    warning = " [WARNING]" if pct > 80 else ""
                    self._log(
                        f"    {name}: {used:.1f} GB used, "
                        f"{free:.1f} GB free ({pct}%){warning}"
                    )

            except json.JSONDecodeError:
                self._log(f"  Raw output: {output[:500]}")
                result["raw_output"] = output

        self._health_results["disk"] = result
        return result

    def check_battery_status(self) -> Dict[str, Any]:
        """Get battery charge and status via WMI.

        Returns:
            dict: Battery status information
        """
        self._log("Checking battery status...", "STEP")

        ps_command = (
            "Get-WmiObject Win32_Battery | "
            "Select-Object EstimatedChargeRemaining, BatteryStatus, "
            "EstimatedRunTime, DesignCapacity, FullChargeCapacity | "
            "ConvertTo-Json"
        )

        output = self._run_powershell(ps_command)

        result = {
            "available": False,
            "charge_percent": 0,
            "status": "Unknown",
            "estimated_runtime_minutes": None,
        }

        # Battery status codes
        status_codes = {
            1: "Discharging",
            2: "AC Connected",
            3: "Fully Charged",
            4: "Low",
            5: "Critical",
            6: "Charging",
            7: "Charging High",
            8: "Charging Low",
            9: "Charging Critical",
            10: "Undefined",
            11: "Partially Charged",
        }

        if output and output.strip():
            try:
                battery = json.loads(output)
                if battery:
                    result["available"] = True
                    result["charge_percent"] = battery.get(
                        "EstimatedChargeRemaining", 0
                    )
                    status_code = battery.get("BatteryStatus", 0)
                    result["status"] = status_codes.get(status_code, f"Code {status_code}")
                    runtime = battery.get("EstimatedRunTime")
                    if runtime and runtime != 71582788:  # Special "unknown" value
                        result["estimated_runtime_minutes"] = runtime

                    self._log(
                        f"  Battery: {result['charge_percent']}% - {result['status']}",
                        "SUCCESS"
                    )
                    if result["estimated_runtime_minutes"]:
                        hours = result["estimated_runtime_minutes"] // 60
                        mins = result["estimated_runtime_minutes"] % 60
                        self._log(f"  Estimated runtime: {hours}h {mins}m")
                else:
                    self._log("  No battery detected (desktop PC?)", "INFO")
            except json.JSONDecodeError:
                self._log("  Could not parse battery info", "WARNING")
                result["raw_output"] = output
        else:
            self._log("  No battery detected (desktop PC?)", "INFO")

        self._health_results["battery"] = result
        return result

    def check_cpu_usage(self) -> Dict[str, Any]:
        """Get current CPU load percentage via WMI.

        Returns:
            dict: CPU usage information
        """
        self._log("Checking CPU usage...", "STEP")

        # Get CPU load percentage
        ps_command = (
            "Get-WmiObject Win32_Processor | "
            "Select-Object Name, LoadPercentage, NumberOfCores, "
            "NumberOfLogicalProcessors, MaxClockSpeed | "
            "ConvertTo-Json"
        )

        output = self._run_powershell(ps_command)

        result = {
            "load_percent": 0,
            "name": "Unknown",
            "cores": 0,
            "logical_processors": 0,
            "max_clock_mhz": 0,
        }

        if output:
            try:
                cpu = json.loads(output)
                if isinstance(cpu, list):
                    cpu = cpu[0]  # Multi-socket case, take first

                result["load_percent"] = cpu.get("LoadPercentage", 0) or 0
                result["name"] = cpu.get("Name", "Unknown").strip()
                result["cores"] = cpu.get("NumberOfCores", 0)
                result["logical_processors"] = cpu.get("NumberOfLogicalProcessors", 0)
                result["max_clock_mhz"] = cpu.get("MaxClockSpeed", 0)

                self._log(f"  CPU: {result['name']}", "SUCCESS")
                self._log(
                    f"  Load: {result['load_percent']}% "
                    f"({result['cores']} cores, {result['logical_processors']} threads)"
                )

            except json.JSONDecodeError:
                self._log(f"  Raw output: {output[:500]}")
                result["raw_output"] = output

        self._health_results["cpu"] = result
        return result

    def test_shell_command(self) -> bool:
        """Test basic Shell tool functionality.

        Returns:
            bool: True if Shell tool works
        """
        self._log("Testing Shell tool...", "STEP")

        # Simple command to verify Shell works
        output = self._run_powershell("Write-Output 'Shell tool working!'")

        if output and "Shell tool working!" in output:
            self._log("Shell tool is functional", "SUCCESS")
            return True
        else:
            self._log("Shell tool test failed", "ERROR")
            return False

    # =========================================================================
    # AI Summary Generation
    # =========================================================================

    def _generate_health_report(self) -> str:
        """Generate an AI-powered health report with recommendations.

        Returns:
            str: Health report with AI analysis
        """
        if self.skip_summary:
            self._log("AI analysis skipped", "INFO")
            return self._generate_basic_report()

        self._log("Generating AI health analysis...", "STEP")

        # Build data summary for LLM
        data_summary = self._build_data_summary()

        try:
            from gaia.chat.sdk import quick_chat

            analysis = quick_chat(
                message=f"""Analyze this Windows system health data and provide:
1. Overall health assessment (Good/Fair/Needs Attention)
2. Key findings (2-3 bullet points)
3. Actionable recommendations (2-3 bullet points)

System Health Data:
{data_summary}

Be concise and practical. Use * for bullet points.""",
                system_prompt=(
                    "You are a helpful system administrator assistant. "
                    "Provide clear, actionable advice based on system metrics. "
                    "Keep responses concise and practical."
                ),
            )

            self._log("AI analysis generated", "SUCCESS")
            return f"{self._generate_basic_report()}\n\nAI ANALYSIS:\n{'-' * 40}\n{analysis}"

        except ConnectionError as e:
            self._log(f"LLM connection failed: {e}", "ERROR")
            return self._generate_basic_report() + (
                "\n\n[AI analysis unavailable - Lemonade server not running. "
                "Start with: lemonade-server serve]"
            )
        except Exception as e:
            self._log(f"AI analysis failed: {e}", "ERROR")
            return self._generate_basic_report() + f"\n\n[AI analysis failed: {e}]"

    def _build_data_summary(self) -> str:
        """Build a text summary of collected health data.

        Returns:
            str: Formatted data summary
        """
        lines = []

        # Memory
        mem = self._health_results.get("memory", {})
        if mem:
            lines.append(f"MEMORY: {mem.get('used_gb', '?')} GB used of "
                        f"{mem.get('total_gb', '?')} GB ({mem.get('usage_percent', '?')}%)")
            procs = mem.get("top_processes", [])
            if procs:
                lines.append("Top memory consumers:")
                for p in procs[:3]:
                    lines.append(f"  - {p.get('Name', '?')}: {p.get('MemoryMB', '?')} MB")

        # Disk
        disk = self._health_results.get("disk", {})
        if disk:
            lines.append("\nDISK SPACE:")
            for d in disk.get("drives", []):
                lines.append(
                    f"  {d.get('Name', '?')}: "
                    f"{d.get('UsedGB', '?')} GB used, "
                    f"{d.get('FreeGB', '?')} GB free "
                    f"({d.get('UsagePercent', '?')}%)"
                )

        # Battery
        battery = self._health_results.get("battery", {})
        if battery.get("available"):
            lines.append(
                f"\nBATTERY: {battery.get('charge_percent', '?')}% - "
                f"{battery.get('status', 'Unknown')}"
            )

        # CPU
        cpu = self._health_results.get("cpu", {})
        if cpu:
            lines.append(f"\nCPU: {cpu.get('load_percent', '?')}% load")
            lines.append(f"  {cpu.get('name', 'Unknown')}")

        return "\n".join(lines)

    def _generate_basic_report(self) -> str:
        """Generate a basic report without AI analysis.

        Returns:
            str: Basic formatted report
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "SYSTEM HEALTH REPORT",
            "=" * 50,
            f"Generated: {timestamp}",
            "",
        ]

        # Memory section
        mem = self._health_results.get("memory", {})
        lines.append("MEMORY USAGE:")
        lines.append("-" * 40)
        if mem:
            lines.append(
                f"  Total: {mem.get('total_gb', '?')} GB | "
                f"Used: {mem.get('used_gb', '?')} GB | "
                f"Free: {mem.get('free_gb', '?')} GB"
            )
            lines.append(f"  Usage: {mem.get('usage_percent', '?')}%")
            procs = mem.get("top_processes", [])
            if procs:
                lines.append("\n  Top memory consumers:")
                for p in procs:
                    lines.append(f"    {p.get('Name', '?')}: {p.get('MemoryMB', '?')} MB")
        else:
            lines.append("  [No data]")
        lines.append("")

        # Disk section
        disk = self._health_results.get("disk", {})
        lines.append("DISK SPACE:")
        lines.append("-" * 40)
        if disk and disk.get("drives"):
            for d in disk["drives"]:
                warning = " [!]" if d.get("UsagePercent", 0) > 80 else ""
                lines.append(
                    f"  {d.get('Name', '?')}: "
                    f"{d.get('UsedGB', '?')} GB used, "
                    f"{d.get('FreeGB', '?')} GB free "
                    f"({d.get('UsagePercent', '?')}%){warning}"
                )
        else:
            lines.append("  [No data]")
        lines.append("")

        # Battery section
        battery = self._health_results.get("battery", {})
        lines.append("BATTERY STATUS:")
        lines.append("-" * 40)
        if battery.get("available"):
            lines.append(f"  Charge: {battery.get('charge_percent', '?')}%")
            lines.append(f"  Status: {battery.get('status', 'Unknown')}")
            if battery.get("estimated_runtime_minutes"):
                hours = battery["estimated_runtime_minutes"] // 60
                mins = battery["estimated_runtime_minutes"] % 60
                lines.append(f"  Estimated runtime: {hours}h {mins}m")
        else:
            lines.append("  No battery (desktop PC)")
        lines.append("")

        # CPU section
        cpu = self._health_results.get("cpu", {})
        lines.append("CPU STATUS:")
        lines.append("-" * 40)
        if cpu:
            lines.append(f"  Processor: {cpu.get('name', 'Unknown')}")
            lines.append(
                f"  Cores: {cpu.get('cores', '?')} physical, "
                f"{cpu.get('logical_processors', '?')} logical"
            )
            lines.append(f"  Current Load: {cpu.get('load_percent', '?')}%")
        else:
            lines.append("  [No data]")
        lines.append("")

        # Warnings
        disk_warnings = disk.get("warnings", [])
        if disk_warnings:
            lines.append("WARNINGS:")
            lines.append("-" * 40)
            for w in disk_warnings:
                lines.append(f"  [!] {w}")
            lines.append("")

        return "\n".join(lines)

    # =========================================================================
    # Notepad Display
    # =========================================================================

    def _find_notepad_text_area(self) -> List[int]:
        """Find the text area coordinates in Notepad window.

        Returns:
            List[int]: [x, y] coordinates for the text area center
        """
        # Default coordinates for Notepad text area
        return [1200, 1000]

    def display_in_notepad(self, report: str) -> bool:
        """Open Notepad and display the health report.

        Args:
            report: Health report text to display

        Returns:
            bool: True if successful
        """
        self._log("Opening Notepad to display report...", "STEP")

        # Open Notepad
        result = self._call_windows_tool("App", {"mode": "launch", "name": "notepad"})
        if "error" in result:
            self._log(f"Failed to open Notepad: {result['error']}", "ERROR")
            return False

        self._wait(self.WAIT_NOTEPAD_LAUNCH, "Notepad to launch")

        # Get text area location
        loc = self._find_notepad_text_area()

        # Switch to Notepad to ensure focus
        self._call_windows_tool("App", {"mode": "switch", "name": "notepad"})
        self._wait(0.3)

        # Click to focus text area
        self._call_windows_tool("Click", {"loc": loc})
        self._wait(0.2)

        # Type the report
        self._log(f"Typing {len(report)} characters...", "STEP")
        result = self._call_windows_tool("Type", {"loc": loc, "text": report})

        if "error" in result:
            self._log(f"Type failed: {result['error']}", "ERROR")
            return False

        self._log("Report displayed in Notepad", "SUCCESS")
        return True

    # =========================================================================
    # Main Workflow
    # =========================================================================

    def run_health_check(self) -> str:
        """Run all health checks and generate report.

        Returns:
            str: Complete health report
        """
        print("=" * 60)
        print("GAIA Windows MCP Demo - System Health Check")
        print("=" * 60)
        print()
        print("This demo will:")
        print("  1. Check memory usage (top processes)")
        print("  2. Check disk space (all drives)")
        print("  3. Check battery status")
        print("  4. Check CPU usage")
        print("  5. Generate AI-powered health analysis")
        print("  6. Display results in Notepad")
        print()
        print("-" * 60)
        print()

        # Run all health checks
        self.check_memory_usage()
        self.check_disk_space()
        self.check_battery_status()
        self.check_cpu_usage()

        # Generate report with AI analysis
        report = self._generate_health_report()

        return report

    def run(self) -> bool:
        """Execute the full demo workflow.

        Returns:
            bool: True if demo completed successfully
        """
        # Run health checks and generate report
        report = self.run_health_check()

        # Display in Notepad
        print()
        if not self.display_in_notepad(report):
            # Fallback: print to console
            print("\n[WARNING] Notepad display failed, showing results in console:")
            print()
            print(report)
            return False

        print()
        print("-" * 60)
        print("[DEMO COMPLETE] Health report displayed in Notepad")
        print("=" * 60)
        return True


def run_individual_test(demo: WindowsSystemHealthDemo, test_name: str) -> bool:
    """Run an individual test by name.

    Args:
        demo: WindowsSystemHealthDemo instance
        test_name: Name of test to run

    Returns:
        bool: True if test passed
    """
    print(f"\n{'=' * 60}")
    print(f"Running individual test: {test_name}")
    print("=" * 60)
    print()

    if test_name == "shell":
        return demo.test_shell_command()
    elif test_name == "memory":
        result = demo.check_memory_usage()
        return bool(result.get("top_processes") or result.get("total_gb"))
    elif test_name == "disk":
        result = demo.check_disk_space()
        return bool(result.get("drives"))
    elif test_name == "battery":
        result = demo.check_battery_status()
        # Battery test passes even if no battery (desktop PC)
        return True
    elif test_name == "cpu":
        result = demo.check_cpu_usage()
        return bool(result.get("name") and result.get("name") != "Unknown")
    elif test_name == "all":
        # Run all checks
        tests = ["shell", "memory", "disk", "battery", "cpu"]
        results = {}
        for t in tests:
            print(f"\n--- Running {t} test ---")
            results[t] = run_individual_test(demo, t)

        print(f"\n{'=' * 60}")
        print("TEST RESULTS SUMMARY:")
        print("=" * 60)
        all_passed = True
        for t, passed in results.items():
            status = "PASS" if passed else "FAIL"
            print(f"  {t}: {status}")
            if not passed:
                all_passed = False

        return all_passed
    else:
        print(f"Unknown test: {test_name}")
        print("Available tests: shell, memory, disk, battery, cpu, all")
        return False


def main():
    """Main entry point for the demo."""
    parser = argparse.ArgumentParser(
        description="GAIA Windows MCP Demo - System Health Check"
    )
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Skip AI analysis (useful for testing MCP tools only)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )
    parser.add_argument(
        "--test",
        type=str,
        choices=["shell", "memory", "disk", "battery", "cpu", "all"],
        help="Run an individual test instead of full demo",
    )

    args = parser.parse_args()

    try:
        demo = WindowsSystemHealthDemo(
            skip_summary=args.skip_summary,
            debug=args.debug,
        )

        if args.test:
            # Run individual test
            success = run_individual_test(demo, args.test)
        else:
            # Run full demo
            success = demo.run()

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Demo failed with exception: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
