# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA MCP Init Command

Initializes MCP configuration for GAIA:
1. Creates ~/.gaia/ directory if it doesn't exist
2. Creates ~/.gaia/mcp_servers.json with empty config if it doesn't exist
3. Provides guidance on adding MCP servers
"""

import json
import logging
from pathlib import Path

# Rich imports for better CLI formatting
try:
    from rich.console import Console
    from rich.panel import Panel

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

log = logging.getLogger(__name__)


class MCPInitCommand:
    """
    Handler for `gaia init --profile mcp` command.

    Creates the MCP configuration directory and file structure.
    """

    def __init__(self, yes: bool = False, verbose: bool = False):
        """
        Initialize the MCP init command.

        Args:
            yes: Skip confirmation prompts
            verbose: Enable verbose output
        """
        self.yes = yes
        self.verbose = verbose
        self.console = Console() if RICH_AVAILABLE else None

    def _print(self, message: str, end: str = "\n"):
        """Print message to stdout."""
        if RICH_AVAILABLE and self.console:
            if end == "":
                self.console.print(message, end="")
            else:
                self.console.print(message)
        else:
            print(message, end=end, flush=True)

    def _print_header(self):
        """Print initialization header."""
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    "[bold cyan]GAIA MCP Configuration[/bold cyan]",
                    border_style="cyan",
                    padding=(0, 2),
                )
            )
            self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print("  GAIA MCP Configuration")
            self._print("=" * 60)
            self._print("")

    def _print_success(self, message: str):
        """Print success message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [green]✓[/green] {message}")
        else:
            self._print(f"   ✓ {message}")

    def _print_info(self, message: str):
        """Print info message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [dim]{message}[/dim]")
        else:
            self._print(f"   {message}")

    def run(self) -> int:
        """
        Execute the MCP initialization workflow.

        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        self._print_header()

        try:
            # Step 1: Create ~/.gaia/ directory if it doesn't exist
            gaia_dir = Path.home() / ".gaia"
            if not gaia_dir.exists():
                gaia_dir.mkdir(parents=True, exist_ok=True)
                self._print_success(f"Created directory: {gaia_dir}")
            else:
                self._print_success(f"Directory exists: {gaia_dir}")

            # Step 2: Create mcp_servers.json if it doesn't exist
            config_path = gaia_dir / "mcp_servers.json"
            if not config_path.exists():
                config_data = {"mcpServers": {}}
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=2)
                self._print_success(f"Created config: {config_path}")
            else:
                self._print_success(f"Config exists: {config_path}")
                self._print_info("(Existing config preserved)")

            # Step 3: Print guidance
            self._print_completion(config_path)
            return 0

        except PermissionError as e:
            self._print(f"   [red]❌[/red] Permission denied: {e}")
            return 1
        except Exception as e:
            self._print(f"   [red]❌[/red] Error: {e}")
            if self.verbose:
                import traceback

                traceback.print_exc()
            return 1

    def _print_completion(self, config_path: Path):
        """Print completion message with next steps."""
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    "[bold green]MCP configuration initialized![/bold green]",
                    border_style="green",
                    padding=(0, 2),
                )
            )
            self.console.print()
            self.console.print("  [bold]Next steps:[/bold]")
            self.console.print()
            self.console.print("  1. Add MCP servers to your config:")
            self.console.print(
                '     [cyan]gaia mcp add time "uvx mcp-server-time"[/cyan]'
            )
            self.console.print()
            self.console.print("  2. Or edit the config file directly:")
            self.console.print(f"     [cyan]{config_path}[/cyan]")
            self.console.print()
            self.console.print("  3. Browse community MCP servers:")
            self.console.print(
                "     [cyan]https://github.com/punkpeye/awesome-mcp-servers[/cyan]"
            )
            self.console.print()
            self.console.print("  [bold]Learn more:[/bold]")
            self.console.print(
                "     [cyan]https://amd-gaia.ai/guides/mcp-client[/cyan]"
            )
            self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print("  MCP configuration initialized!")
            self._print("=" * 60)
            self._print("")
            self._print("  Next steps:")
            self._print("")
            self._print("  1. Add MCP servers to your config:")
            self._print('     gaia mcp add time "uvx mcp-server-time"')
            self._print("")
            self._print("  2. Or edit the config file directly:")
            self._print(f"     {config_path}")
            self._print("")
            self._print("  3. Browse community MCP servers:")
            self._print("     https://github.com/punkpeye/awesome-mcp-servers")
            self._print("")
            self._print("  Learn more:")
            self._print("     https://amd-gaia.ai/guides/mcp-client")
            self._print("")


def run_mcp_init(yes: bool = False, verbose: bool = False) -> int:
    """
    Entry point for `gaia init --profile mcp` command.

    Args:
        yes: Skip confirmation prompts
        verbose: Enable verbose output

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    cmd = MCPInitCommand(yes=yes, verbose=verbose)
    return cmd.run()
