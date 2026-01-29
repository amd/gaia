#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Windows MCP NASA Demo - GAIA MCP Client Showcase

Demonstrates GAIA's MCP client orchestrating Windows automation to:
1. Open a browser and navigate to NASA News
2. Scrape the page content
3. Generate an AI summary using GAIA Chat SDK
4. Display results in Notepad with visible step-by-step logging

This showcases the MCP client implementation interacting with multiple
MCP tool calls in sequence for real-world Windows automation.

Requirements:
- Windows 10/11
- Python 3.13+
- Windows MCP: uvx windows-mcp (auto-installed)
- LLM (optional): Lemonade server for AI summary, or use --use-claude

Run:
    # With local LLM (Lemonade server must be running)
    python examples/mcp_windows_nasa_demo.py

    # With Claude API (no local LLM needed)
    ANTHROPIC_API_KEY=your_key python examples/mcp_windows_nasa_demo.py --use-claude

    # Skip AI summarization (useful for testing MCP tools only)
    python examples/mcp_windows_nasa_demo.py --skip-summary
"""

import argparse
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.mcp import MCPClientMixin


class WindowsNasaDemo(Agent, MCPClientMixin):
    """Demo agent orchestrating Windows MCP tools for NASA news scraping.

    This agent demonstrates:
    - Connecting to Windows MCP server
    - Browser automation (opening, navigating)
    - Page content scraping
    - AI-powered summarization
    - Results display in Notepad
    """

    # NASA News URL
    NASA_NEWS_URL = "https://www.nasa.gov/news/recently-published"

    # Timing configuration (seconds)
    WAIT_BROWSER_LAUNCH = 2.0
    WAIT_PAGE_LOAD = 4.0
    WAIT_NOTEPAD_LAUNCH = 1.0
    WAIT_AFTER_SHORTCUT = 0.5

    def __init__(
        self,
        use_claude: bool = False,
        skip_summary: bool = False,
        debug: bool = False,
        **kwargs,
    ):
        """Initialize the Windows NASA Demo agent.

        Args:
            use_claude: Use Claude API for summarization instead of local LLM
            skip_summary: Skip AI summarization (useful for testing MCP tools)
            debug: Enable debug output
            **kwargs: Additional arguments passed to Agent
        """
        # Skip Lemonade initialization if skipping summary or using Claude
        kwargs.setdefault("skip_lemonade", skip_summary or use_claude)
        kwargs.setdefault("max_steps", 20)
        kwargs.setdefault("silent_mode", True)

        # Initialize Agent
        Agent.__init__(self, use_claude=use_claude, debug=debug, **kwargs)

        # Initialize MCPClientMixin
        MCPClientMixin.__init__(self)

        self.use_claude = use_claude
        self.skip_summary = skip_summary
        self.debug_mode = debug

        # Execution log for display
        self._execution_log: List[str] = []

        # Connect to Windows MCP server
        self._connect_windows_mcp()

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return """You are a helpful AI assistant that can automate Windows tasks
using the Windows MCP server. You can open applications, type text, use keyboard
shortcuts, and scrape web content."""

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
            level: Log level (INFO, STEP, SUCCESS, ERROR)
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

        self._log(f"  Calling tool: {tool_name} with args: {args}")

        try:
            result = client.call_tool(tool_name, args)
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

    def _open_browser(self) -> bool:
        """Open Microsoft Edge browser.

        Returns:
            bool: True if successful
        """
        self._log("Opening Microsoft Edge browser...", "STEP")

        result = self._call_windows_tool("app", {"action": "open", "app_name": "edge"})

        if "error" in result:
            # Try Chrome as fallback
            self._log("  Edge failed, trying Chrome as fallback...")
            result = self._call_windows_tool(
                "app", {"action": "open", "app_name": "chrome"}
            )

            if "error" in result:
                self._log("Failed to open any browser", "ERROR")
                return False

        self._wait(self.WAIT_BROWSER_LAUNCH, "browser to launch")
        self._log("Browser opened", "SUCCESS")
        return True

    def _navigate_to_url(self, url: str) -> bool:
        """Navigate browser to a URL.

        Args:
            url: URL to navigate to

        Returns:
            bool: True if successful
        """
        self._log(f"Navigating to {url}...", "STEP")

        # Focus address bar with Ctrl+L
        self._log("  Focusing address bar (Ctrl+L)...")
        result = self._call_windows_tool("shortcut", {"keys": "ctrl+l"})
        if "error" in result:
            return False
        self._wait(self.WAIT_AFTER_SHORTCUT)

        # Type URL
        self._log(f"  Typing URL: {url}")
        result = self._call_windows_tool("type", {"text": url, "clear_before": True})
        if "error" in result:
            return False
        self._wait(self.WAIT_AFTER_SHORTCUT)

        # Press Enter to navigate
        self._log("  Pressing Enter to navigate...")
        result = self._call_windows_tool("shortcut", {"keys": "enter"})
        if "error" in result:
            return False

        self._wait(self.WAIT_PAGE_LOAD, "page to load")
        self._log("Navigation complete", "SUCCESS")
        return True

    def _scrape_page(self) -> Optional[str]:
        """Scrape the current page content.

        Returns:
            str: Page content or None if failed
        """
        self._log("Scraping page content...", "STEP")

        result = self._call_windows_tool("scrape", {})

        if "error" in result:
            # Try snapshot as fallback
            self._log("  Scrape failed, trying snapshot with DOM...")
            result = self._call_windows_tool("snapshot", {"use_dom": True})

            if "error" in result:
                self._log("Failed to scrape page content", "ERROR")
                return None

        # Extract content from result
        content = result.get("content", [])
        if isinstance(content, list):
            # Join content items
            text_content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        else:
            text_content = str(content)

        char_count = len(text_content)
        self._log(f"Scraped {char_count:,} characters", "SUCCESS")
        return text_content

    def _generate_summary(self, content: str) -> str:
        """Generate an AI summary of the scraped content.

        Args:
            content: Page content to summarize

        Returns:
            str: AI-generated summary or fallback message
        """
        if self.skip_summary:
            return "[AI summarization skipped - use --use-claude or start Lemonade server for summaries]"

        self._log("Generating AI summary...", "STEP")

        try:
            from gaia.chat.sdk import quick_chat

            # Truncate content if too long
            max_content_length = 10000
            if len(content) > max_content_length:
                content = content[:max_content_length] + "\n...[truncated]"

            summary = quick_chat(
                message=f"""Summarize this NASA news in 3-5 bullet points. Focus on the most important and interesting news items.

Content:
{content}

Provide a concise summary with bullet points (use * for bullets).""",
                system_prompt="You are a helpful assistant that summarizes news concisely. Use bullet points with * markers.",
            )

            self._log("Summary generated", "SUCCESS")
            return summary

        except ConnectionError as e:
            self._log(f"LLM connection failed: {e}", "ERROR")
            return "[AI summarization unavailable - Lemonade server not running. Use --skip-summary or --use-claude]"
        except Exception as e:
            self._log(f"Summary generation failed: {e}", "ERROR")
            return f"[AI summarization failed: {e}]"

    def _open_notepad_with_results(self, summary: str, scraped_content: str) -> bool:
        """Open Notepad and display results.

        Args:
            summary: AI-generated summary
            scraped_content: Raw scraped content (truncated preview)

        Returns:
            bool: True if successful
        """
        self._log("Opening Notepad with results...", "STEP")

        # Open Notepad
        result = self._call_windows_tool(
            "app", {"action": "open", "app_name": "notepad"}
        )

        if "error" in result:
            self._log("Failed to open Notepad", "ERROR")
            return False

        self._wait(self.WAIT_NOTEPAD_LAUNCH, "Notepad to launch")

        # Format output
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execution_log = "\n".join(self._execution_log)

        # Truncate scraped content for preview
        content_preview = scraped_content[:2000] if scraped_content else "[No content]"
        if scraped_content and len(scraped_content) > 2000:
            content_preview += "\n...[truncated for display]"

        output = f"""============================================================
GAIA Windows MCP Demo - NASA News Summary
============================================================

Timestamp: {timestamp}
URL: {self.NASA_NEWS_URL}

------------------------------------------------------------
EXECUTION LOG:
------------------------------------------------------------
{execution_log}

------------------------------------------------------------
AI SUMMARY:
------------------------------------------------------------
{summary}

------------------------------------------------------------
CONTENT PREVIEW (first 2000 chars):
------------------------------------------------------------
{content_preview}

============================================================
"""

        # Type the output into Notepad
        result = self._call_windows_tool("type", {"text": output})

        if "error" in result:
            self._log("Failed to type results into Notepad", "ERROR")
            return False

        self._log("Results displayed in Notepad", "SUCCESS")
        return True

    def run(self) -> bool:
        """Execute the full demo workflow.

        Returns:
            bool: True if demo completed successfully
        """
        print("=" * 60)
        print("GAIA Windows MCP Demo - NASA News Summary")
        print("=" * 60)
        print()
        print("This demo will:")
        print("  1. Open Microsoft Edge browser")
        print("  2. Navigate to NASA News")
        print("  3. Scrape the page content")
        print("  4. Generate an AI summary")
        print("  5. Display results in Notepad")
        print()
        print("-" * 60)
        print()

        # Step 1: Open browser
        if not self._open_browser():
            print("\n[DEMO FAILED] Could not open browser")
            return False

        # Step 2: Navigate to NASA News
        if not self._navigate_to_url(self.NASA_NEWS_URL):
            print("\n[DEMO FAILED] Could not navigate to NASA News")
            return False

        # Step 3: Scrape page content
        scraped_content = self._scrape_page()
        if not scraped_content:
            print("\n[DEMO FAILED] Could not scrape page content")
            return False

        # Step 4: Generate AI summary
        summary = self._generate_summary(scraped_content)

        # Step 5: Display in Notepad
        if not self._open_notepad_with_results(summary, scraped_content):
            # Fallback: print to console
            print("\n[WARNING] Notepad display failed, showing results in console:")
            print(summary)
            return False

        print()
        print("-" * 60)
        print("[DEMO COMPLETE] Results displayed in Notepad")
        print("=" * 60)
        return True


def main():
    """Main entry point for the demo."""
    parser = argparse.ArgumentParser(
        description="GAIA Windows MCP Demo - NASA News Summary"
    )
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude API for summarization (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--skip-summary",
        action="store_true",
        help="Skip AI summarization (useful for testing MCP tools only)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )

    args = parser.parse_args()

    # Check for Claude API key if using Claude
    if args.use_claude:
        import os

        if not os.getenv("ANTHROPIC_API_KEY"):
            print(
                "Error: ANTHROPIC_API_KEY environment variable required for --use-claude"
            )
            sys.exit(1)

    try:
        demo = WindowsNasaDemo(
            use_claude=args.use_claude,
            skip_summary=args.skip_summary,
            debug=args.debug,
        )
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
