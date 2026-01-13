# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent Prompt Session - Claude Code-style CLI interface.

Provides 3-pane interface:
1. Top: Tool execution window (scrollable)
2. Middle: Agent response window (scrollable)
3. Bottom: Fixed user input with autocomplete

Works with any GAIA agent type.
"""

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from .commands import CommandRegistry
from .completers import get_completer


class AgentCommandCompleter(Completer):
    """
    Autocomplete for agent commands.

    Generic completer that works with any agent's command registry.
    """

    def __init__(self, agent, command_registry: CommandRegistry):
        """
        Initialize completer.

        Args:
            agent: The agent instance (for context-aware completion)
            command_registry: Registry of commands for this agent
        """
        self.agent = agent
        self.registry = command_registry

    def get_completions(self, document: Document, _complete_event):
        """Generate completions based on current input."""
        text = document.text_before_cursor

        # If user typed "/", show all commands
        if text == "/":
            for cmd_name, cmd in self.registry.get_all().items():
                yield Completion(
                    cmd_name[1:],  # Remove leading /
                    start_position=-len(text),
                    display=cmd.usage,
                    display_meta=cmd.description,
                )

        # If user started typing a command, filter matches
        elif text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd_part = parts[0]

            # Command name completion
            for cmd_name, cmd in self.registry.get_all().items():
                if cmd_name.startswith(cmd_part):
                    yield Completion(
                        cmd_name[len(cmd_part) :],
                        start_position=0,
                        display=cmd.usage,
                        display_meta=cmd.description,
                    )

            # Argument completion if command is recognized
            if len(parts) > 1 and cmd_part in self.registry.get_all():
                cmd = self.registry.get(cmd_part)
                if cmd.arg_completer_name:
                    completer_func = get_completer(cmd.arg_completer_name)
                    if completer_func:
                        current_arg = parts[1]
                        for completion in completer_func(self.agent, current_arg):
                            yield completion


class AgentPromptSession:
    """
    Generic prompt session for any GAIA agent.

    Provides:
    - Fixed bottom input with autocomplete
    - Scrollable output windows
    - Clean visual separation
    - Command history
    """

    def __init__(
        self,
        agent,
        command_registry: CommandRegistry,
        history_file: str = ".gaia_history",
        prompt_text: str = "> ",
    ):
        """
        Initialize prompt session.

        Args:
            agent: The agent instance
            command_registry: Registry of commands
            history_file: Path to history file
            prompt_text: Prompt string (default: "> ")
        """
        self.agent = agent
        self.command_registry = command_registry

        # Create prompt session with autocomplete
        self.session = PromptSession(
            completer=AgentCommandCompleter(agent, command_registry),
            complete_while_typing=True,
            history=FileHistory(history_file),
            auto_suggest=AutoSuggestFromHistory(),
            enable_open_in_editor=True,
            multiline=False,
            style=Style.from_dict(
                {
                    "completion-menu": "bg:#008888 #ffffff",
                    "completion-menu.completion": "bg:#008888 #ffffff",
                    "completion-menu.completion.current": "bg:#00aaaa #000000",
                    "completion-menu.meta.completion": "bg:#00bbbb #ffffff",
                    "completion-menu.meta.completion.current": "bg:#00dddd #000000",
                }
            ),
        )

        self.prompt_text = prompt_text

    def print_separator(self, char="─", width=80):
        """Print a separator line."""
        print(char * width)

    def print_tool_output(self, message: str):
        """
        Print tool execution output in the top pane.

        Args:
            message: Tool output message
        """
        # Tool output goes to stdout normally
        # In future, could buffer this in a separate scrollback
        print(message)

    def print_agent_response(self, message: str):
        """
        Print agent conversational response in the middle pane.

        Args:
            message: Agent's response
        """
        # Print with formatting
        print(f"\nA: {message}")

    def get_input(self) -> str:
        """
        Get user input with autocomplete and fixed bottom position.

        Returns:
            User input string
        """
        # Print separator before input
        self.print_separator()

        # Get input with autocomplete
        try:
            user_input = self.session.prompt(self.prompt_text).strip()
            return user_input
        except (KeyboardInterrupt, EOFError):
            return None

    def show_commands_help(self):
        """Show all available commands in a nice format."""
        print("\n" + "=" * 80)
        print(f"{self.command_registry.agent_name.upper()} AGENT - AVAILABLE COMMANDS")
        print("=" * 80)

        # Group commands by category
        doc_commands = []
        session_commands = []
        debug_commands = []
        other_commands = []

        for cmd_name, cmd in self.command_registry.get_all().items():
            if cmd_name in ["/index", "/list", "/watch"]:
                doc_commands.append(cmd)
            elif cmd_name in ["/save", "/resume", "/sessions", "/reset"]:
                session_commands.append(cmd)
            elif cmd_name in [
                "/chunks",
                "/chunk",
                "/test",
                "/dump",
                "/status",
                "/search-debug",
                "/clear-cache",
            ]:
                debug_commands.append(cmd)
            else:
                other_commands.append(cmd)

        # Print categorized commands
        if doc_commands:
            print("\n📚 DOCUMENT MANAGEMENT:")
            for cmd in doc_commands:
                print(f"  {cmd.usage:<25} - {cmd.description}")

        if session_commands:
            print("\n💾 SESSION MANAGEMENT:")
            for cmd in session_commands:
                print(f"  {cmd.usage:<25} - {cmd.description}")

        if debug_commands:
            print("\n🔍 DEBUG & OBSERVABILITY:")
            for cmd in debug_commands:
                print(f"  {cmd.usage:<25} - {cmd.description}")

        if other_commands:
            print("\n❓ OTHER:")
            for cmd in other_commands:
                print(f"  {cmd.usage:<25} - {cmd.description}")

        print("\n" + "=" * 80)
        print("💡 TIP: Type / to see autocomplete suggestions")
        print("=" * 80)
