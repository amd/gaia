#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Windows MCP NASA Demo - GAIA MCP Client Showcase

Demonstrates GAIA's MCP client orchestrating Windows automation to:
1. Open a browser and navigate to NASA News
2. Click on the first article (GUI click)
3. Get the article content
4. Generate an AI summary using GAIA Chat SDK
5. Display results in Notepad

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

    # Run individual tests
    python examples/mcp_windows_nasa_demo.py --test notepad
    python examples/mcp_windows_nasa_demo.py --test browser
    python examples/mcp_windows_nasa_demo.py --test state
    python examples/mcp_windows_nasa_demo.py --test navigate
    python examples/mcp_windows_nasa_demo.py --test scrape

Windows MCP Tool Reference:
    App      - mode: launch/resize/switch, name: app name
    Click    - loc: [x, y], button: left/right/middle, clicks: int
    Type     - loc: [x, y], text: string, clear: bool, press_enter: bool
    Shortcut - shortcut: "ctrl+l", "alt+tab", etc.
    Snapshot - use_vision: bool, use_dom: bool
    Scrape   - url: string (required), use_dom: bool
    Scroll   - loc: [x,y], type: vertical/horizontal, direction: up/down/left/right
    Move     - loc: [x, y], drag: bool
    Wait     - duration: int (ms)
    Shell    - command: string, timeout: int
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
    - GUI clicking based on screen state
    - Page content scraping
    - AI-powered summarization
    - Results display in Notepad
    """

    # NASA News URL
    NASA_NEWS_URL = "https://www.nasa.gov/news/recently-published"

    # Timing configuration (seconds)
    WAIT_BROWSER_LAUNCH = 3.0
    WAIT_PAGE_LOAD = 5.0
    WAIT_NOTEPAD_LAUNCH = 2.0
    WAIT_AFTER_SHORTCUT = 0.5
    WAIT_AFTER_TYPE = 0.3

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
                for tool in tools:
                    self._log(f"    {tool.name}: {tool.input_schema}")
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

    # =========================================================================
    # Individual Test Methods - Each test should produce VISIBLE on-screen action
    # =========================================================================

    def test_open_notepad(self) -> bool:
        """Test 1: Open Notepad.

        SUCCESS: Notepad window appears on screen.
        FAIL: Nothing happens despite logs.

        Returns:
            bool: True if Notepad opened
        """
        self._log("TEST 1: Opening Notepad...", "STEP")

        # Use App tool with mode=launch and name=notepad
        result = self._call_windows_tool("App", {"mode": "launch", "name": "notepad"})

        if "error" in result:
            self._log(f"Failed to open Notepad: {result['error']}", "ERROR")
            return False

        self._wait(self.WAIT_NOTEPAD_LAUNCH, "Notepad to launch")
        self._log("Notepad opened - CHECK YOUR SCREEN!", "SUCCESS")
        return True

    def _check_tool_success(self, result: Dict[str, Any]) -> bool:
        """Check if a tool call was successful.

        Args:
            result: Tool result dict

        Returns:
            bool: True if successful, False if error or "not found"
        """
        if "error" in result:
            return False
        if result.get("isError"):
            return False
        # Check content for failure messages
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text", "").lower()
                    if "not found" in text or "failed" in text or "error" in text:
                        return False
        return True

    def test_open_browser(self) -> bool:
        """Test 2: Open Microsoft Edge browser.

        SUCCESS: Edge browser opens on screen.
        FAIL: Nothing happens despite logs.

        Returns:
            bool: True if browser opened
        """
        self._log("TEST 2: Opening Microsoft Edge browser...", "STEP")

        # Method 1: Try App tool with different names
        app_names = ["Microsoft Edge", "edge", "msedge"]
        for name in app_names:
            self._log(f"  Trying App launch with name='{name}'...")
            result = self._call_windows_tool("App", {"mode": "launch", "name": name})
            if self._check_tool_success(result):
                self._wait(self.WAIT_BROWSER_LAUNCH, "browser to launch")
                self._log("Browser opened - CHECK YOUR SCREEN!", "SUCCESS")
                return True

        # Method 2: Use Shell to launch Edge directly
        self._log("  App tool failed, trying Shell command...")
        result = self._call_windows_tool(
            "Shell", {"command": "start msedge", "timeout": 10}
        )
        if self._check_tool_success(result):
            self._wait(self.WAIT_BROWSER_LAUNCH, "browser to launch")
            self._log("Browser opened via Shell - CHECK YOUR SCREEN!", "SUCCESS")
            return True

        # Method 3: Try Chrome as fallback
        self._log("  Edge failed, trying Chrome...")
        result = self._call_windows_tool(
            "Shell", {"command": "start chrome", "timeout": 10}
        )
        if self._check_tool_success(result):
            self._wait(self.WAIT_BROWSER_LAUNCH, "browser to launch")
            self._log("Chrome opened via Shell - CHECK YOUR SCREEN!", "SUCCESS")
            return True

        self._log("Failed to open any browser", "ERROR")
        return False

    def test_get_screen_state(self) -> Dict[str, Any]:
        """Test 3: Get screen state to find UI elements.

        SUCCESS: Returns useful info about UI elements and positions.

        Returns:
            dict: Screen state including element positions
        """
        self._log("TEST 3: Getting screen state (Snapshot)...", "STEP")

        # Use Snapshot tool with use_dom for browser content
        result = self._call_windows_tool("Snapshot", {"use_dom": True})

        if "error" in result:
            self._log(f"Failed to get screen state: {result['error']}", "ERROR")
            return result

        # Log what we got
        content = result.get("content", [])
        if isinstance(content, list) and content:
            self._log(f"Got screen state with {len(content)} content items", "SUCCESS")
            if self.debug_mode:
                for item in content[:3]:  # Show first 3 items
                    self._log(f"    {item}")
        else:
            self._log(f"Got screen state: {result}", "SUCCESS")

        return result

    def test_navigate_to_url(self, url: str = None) -> bool:
        """Test 4: Focus address bar and navigate to URL.

        SUCCESS: URL appears in address bar and page loads.

        Args:
            url: URL to navigate to (default: NASA News)

        Returns:
            bool: True if navigation successful
        """
        url = url or self.NASA_NEWS_URL
        self._log(f"TEST 4: Navigating to {url}...", "STEP")

        # Step 1: Focus address bar with Ctrl+L using Shortcut tool
        self._log("  Focusing address bar (Ctrl+L)...")
        result = self._call_windows_tool("Shortcut", {"shortcut": "ctrl+l"})
        if "error" in result:
            self._log(f"Shortcut failed: {result['error']}", "ERROR")
            return False
        self._wait(self.WAIT_AFTER_SHORTCUT)

        # Step 2: Get screen state to find address bar coordinates
        self._log("  Getting screen state to find address bar...")
        state = self._call_windows_tool("Snapshot", {"use_dom": False})

        # Try to find address bar location from state, or use default position
        # Address bar is typically at top center of screen
        address_bar_loc = [500, 50]  # Default fallback position

        if "content" in state:
            content = state.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        # Look for address bar or URL input
                        text = str(item.get("text", "")).lower()
                        if any(
                            kw in text for kw in ["address", "url", "search", "edge://"]
                        ):
                            if "loc" in item:
                                address_bar_loc = item["loc"]
                                self._log(f"  Found address bar at {address_bar_loc}")
                                break

        # Step 3: Type URL using Type tool with location
        self._log(f"  Typing URL: {url} at {address_bar_loc}")
        result = self._call_windows_tool(
            "Type",
            {
                "loc": address_bar_loc,
                "text": url,
                "clear": True,
                "press_enter": True,
            },
        )
        if "error" in result:
            self._log(f"Type failed: {result['error']}", "ERROR")
            return False

        self._wait(self.WAIT_PAGE_LOAD, "page to load")
        self._log("Navigation complete - CHECK YOUR SCREEN!", "SUCCESS")
        return True

    def test_click_at_coordinates(self, x: int, y: int) -> bool:
        """Test 5: Click at specific screen coordinates.

        SUCCESS: Click action performed at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate

        Returns:
            bool: True if click successful
        """
        self._log(f"TEST 5: Clicking at coordinates ({x}, {y})...", "STEP")

        result = self._call_windows_tool("Click", {"loc": [x, y]})

        if "error" in result:
            self._log(f"Click failed: {result['error']}", "ERROR")
            return False

        self._log(f"Clicked at ({x}, {y}) - CHECK YOUR SCREEN!", "SUCCESS")
        return True

    def test_scrape_page(self, url: str = None) -> Optional[str]:
        """Test 6: Scrape web page content.

        SUCCESS: Returns page text content.

        Args:
            url: URL to scrape (required by Windows MCP)

        Returns:
            str: Page content or None if failed
        """
        url = url or self.NASA_NEWS_URL
        self._log(f"TEST 6: Scraping page content from {url}...", "STEP")

        # Scrape tool requires url parameter
        result = self._call_windows_tool("Scrape", {"url": url})

        if "error" in result:
            self._log(f"Scrape failed: {result['error']}", "ERROR")
            return None

        # Extract content from result
        content = result.get("content", [])
        if isinstance(content, list):
            text_content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        else:
            text_content = str(content)

        char_count = len(text_content)
        self._log(f"Scraped {char_count:,} characters", "SUCCESS")
        return text_content

    def test_type_in_notepad(self, text: str, loc: List[int] = None) -> bool:
        """Test 7: Type text into Notepad.

        SUCCESS: Text appears in Notepad window.

        Args:
            text: Text to type
            loc: Optional [x, y] coordinates. If None, clicks center of notepad.

        Returns:
            bool: True if typing successful
        """
        self._log("TEST 7: Typing into Notepad...", "STEP")

        # If no location provided, use a default position in notepad area
        # Typically notepad text area is in the center-left of the window
        if loc is None:
            # Get screen state to find notepad
            state = self._call_windows_tool("Snapshot", {"use_dom": False})
            loc = [400, 300]  # Default fallback

            if "content" in state:
                content = state.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            text_field = str(item.get("text", "")).lower()
                            if "notepad" in text_field or "untitled" in text_field:
                                if "loc" in item:
                                    loc = item["loc"]
                                    self._log(f"  Found notepad at {loc}")
                                    break

        # First click to focus
        self._call_windows_tool("Click", {"loc": loc})
        self._wait(0.2)

        # Type the text
        result = self._call_windows_tool("Type", {"loc": loc, "text": text})

        if "error" in result:
            self._log(f"Type failed: {result['error']}", "ERROR")
            return False

        self._log("Text typed - CHECK NOTEPAD!", "SUCCESS")
        return True

    # =========================================================================
    # Full Demo Workflow
    # =========================================================================

    def _open_browser(self) -> bool:
        """Open Microsoft Edge browser.

        Returns:
            bool: True if successful
        """
        return self.test_open_browser()

    def _navigate_to_url(self, url: str) -> bool:
        """Navigate browser to a URL.

        Args:
            url: URL to navigate to

        Returns:
            bool: True if successful
        """
        return self.test_navigate_to_url(url)

    def _click_first_article(self) -> bool:
        """Click on the first article using Snapshot to find it.

        Returns:
            bool: True if successful
        """
        self._log("Finding first article to click...", "STEP")

        # Get screen state to find clickable elements
        state = self.test_get_screen_state()

        if "error" in state:
            self._log("Could not get screen state to find article", "ERROR")
            return False

        # Look for article links in the state content
        content = state.get("content", [])
        article_coords = None

        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text", "").lower()
                    # Look for article-like text
                    if any(
                        keyword in text
                        for keyword in [
                            "article",
                            "news",
                            "nasa",
                            "mission",
                            "space",
                            "mars",
                            "moon",
                        ]
                    ):
                        if "loc" in item:
                            article_coords = item["loc"]
                            self._log(
                                f"  Found article at {article_coords}: {text[:50]}..."
                            )
                            break

        if article_coords:
            return self.test_click_at_coordinates(article_coords[0], article_coords[1])
        else:
            self._log("Could not find article coordinates in state", "ERROR")
            return False

    def _scrape_page(self) -> Optional[str]:
        """Scrape the current page content.

        Returns:
            str: Page content or None if failed
        """
        return self.test_scrape_page(self.NASA_NEWS_URL)

    def _generate_summary(self, content: str) -> str:
        """Generate an AI summary of the scraped content.

        Args:
            content: Page content to summarize

        Returns:
            str: AI-generated summary or fallback message
        """
        if self.skip_summary:
            return (
                "[AI summarization skipped - "
                "use --use-claude or start Lemonade server for summaries]"
            )

        self._log("Generating AI summary...", "STEP")

        try:
            from gaia.chat.sdk import quick_chat

            # Truncate content if too long
            max_content_length = 10000
            if len(content) > max_content_length:
                content = content[:max_content_length] + "\n...[truncated]"

            summary = quick_chat(
                message=f"""Summarize this NASA news in 3-5 bullet points.
Focus on the most important and interesting news items.

Content:
{content}

Provide a concise summary with bullet points (use * for bullets).""",
                system_prompt=(
                    "You are a helpful assistant that summarizes news concisely. "
                    "Use bullet points with * markers."
                ),
            )

            self._log("Summary generated", "SUCCESS")
            return summary

        except ConnectionError as e:
            self._log(f"LLM connection failed: {e}", "ERROR")
            return (
                "[AI summarization unavailable - Lemonade server not running. "
                "Use --skip-summary or --use-claude]"
            )
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
        if not self.test_open_notepad():
            return False

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
        return self.test_type_in_notepad(output)

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
        print("  3. Click on first article (GUI click)")
        print("  4. Scrape the article content")
        print("  5. Generate an AI summary")
        print("  6. Display results in Notepad")
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

        # Step 3: Click on first article (optional - may fail if no article found)
        self._click_first_article()

        # Step 4: Scrape page content
        scraped_content = self._scrape_page()
        if not scraped_content:
            print("\n[DEMO FAILED] Could not scrape page content")
            return False

        # Step 5: Generate AI summary
        summary = self._generate_summary(scraped_content)

        # Step 6: Display in Notepad
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


def run_individual_test(demo: WindowsNasaDemo, test_name: str) -> bool:
    """Run an individual test by name.

    Args:
        demo: WindowsNasaDemo instance
        test_name: Name of test to run

    Returns:
        bool: True if test passed
    """
    print(f"\n{'=' * 60}")
    print(f"Running individual test: {test_name}")
    print("=" * 60)
    print()
    print("IMPORTANT: Check your screen for visible changes!")
    print("Logs alone don't matter - only on-screen action counts.")
    print()

    if test_name == "notepad":
        return demo.test_open_notepad()
    elif test_name == "browser":
        return demo.test_open_browser()
    elif test_name == "state":
        result = demo.test_get_screen_state()
        return "error" not in result
    elif test_name == "navigate":
        # Must open browser first
        if not demo.test_open_browser():
            return False
        return demo.test_navigate_to_url()
    elif test_name == "scrape":
        content = demo.test_scrape_page(demo.NASA_NEWS_URL)
        return content is not None
    elif test_name == "click":
        # Test click at center of screen
        print("Clicking at center of screen (960, 540)...")
        return demo.test_click_at_coordinates(960, 540)
    elif test_name == "type":
        # Open notepad and type
        if not demo.test_open_notepad():
            return False
        return demo.test_type_in_notepad("Hello from GAIA MCP Demo!")
    else:
        print(f"Unknown test: {test_name}")
        print("Available tests: notepad, browser, state, navigate, scrape, click, type")
        return False


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
    parser.add_argument(
        "--test",
        type=str,
        choices=["notepad", "browser", "state", "navigate", "scrape", "click", "type"],
        help="Run an individual test instead of full demo",
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
