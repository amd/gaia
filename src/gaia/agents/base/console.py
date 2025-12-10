# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

# Import Rich library for pretty printing and syntax highlighting
try:
    from rich import print as rprint
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.syntax import Syntax
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print(
        "Rich library not found. Install with 'pip install rich' for syntax highlighting."
    )

# Display configuration constants
MAX_DISPLAY_LINE_LENGTH = 120


class OutputHandler(ABC):
    """
    Abstract base class for handling agent output.

    Defines the minimal interface that agents use to report their progress.
    Each implementation handles the output differently:
    - AgentConsole: Rich console output for CLI
    - SilentConsole: Suppressed output for testing
    - SSEOutputHandler: Server-Sent Events for API streaming

    This interface focuses on WHAT agents need to report, not HOW
    each handler chooses to display it.
    """

    # === Core Progress/State Methods (Required) ===

    @abstractmethod
    def print_processing_start(self, query: str, max_steps: int):
        """Print processing start message."""
        ...

    @abstractmethod
    def print_step_header(self, step_num: int, step_limit: int):
        """Print step header."""
        ...

    @abstractmethod
    def print_state_info(self, state_message: str):
        """Print current execution state."""
        ...

    @abstractmethod
    def print_thought(self, thought: str):
        """Print agent's reasoning/thought."""
        ...

    @abstractmethod
    def print_goal(self, goal: str):
        """Print agent's current goal."""
        ...

    @abstractmethod
    def print_plan(self, plan: List[Any], current_step: int = None):
        """Print agent's plan with optional current step highlight."""
        ...

    # === Tool Execution Methods (Required) ===

    @abstractmethod
    def print_tool_usage(self, tool_name: str):
        """Print tool being called."""
        ...

    @abstractmethod
    def print_tool_complete(self):
        """Print tool completion."""
        ...

    @abstractmethod
    def pretty_print_json(self, data: Dict[str, Any], title: str = None):
        """Print JSON data (tool args/results)."""
        ...

    # === Status Messages (Required) ===

    @abstractmethod
    def print_error(self, error_message: str):
        """Print error message."""
        ...

    @abstractmethod
    def print_warning(self, warning_message: str):
        """Print warning message."""
        ...

    @abstractmethod
    def print_info(self, message: str):
        """Print informational message."""
        ...

    # === Progress Indicators (Required) ===

    @abstractmethod
    def start_progress(self, message: str):
        """Start progress indicator."""
        ...

    @abstractmethod
    def stop_progress(self):
        """Stop progress indicator."""
        ...

    # === Completion Methods (Required) ===

    @abstractmethod
    def print_final_answer(self, answer: str):
        """Print final answer/result."""
        ...

    @abstractmethod
    def print_repeated_tool_warning(self):
        """Print warning about repeated tool calls (loop detection)."""
        ...

    @abstractmethod
    def print_completion(self, steps_taken: int, steps_limit: int):
        """Print completion summary."""
        ...

    # === Optional Methods (with default no-op implementations) ===

    def print_prompt(
        self, prompt: str, title: str = "Prompt"
    ):  # pylint: disable=unused-argument
        """Print prompt (for debugging). Optional - default no-op."""
        ...

    def print_response(
        self, response: str, title: str = "Response"
    ):  # pylint: disable=unused-argument
        """Print response (for debugging). Optional - default no-op."""
        ...

    def print_streaming_text(
        self, text_chunk: str, end_of_stream: bool = False
    ):  # pylint: disable=unused-argument
        """Print streaming text. Optional - default no-op."""
        ...

    def display_stats(self, stats: Dict[str, Any]):  # pylint: disable=unused-argument
        """Display performance statistics. Optional - default no-op."""
        ...

    def print_header(self, text: str):  # pylint: disable=unused-argument
        """Print header. Optional - default no-op."""
        ...

    def print_separator(self, length: int = 50):  # pylint: disable=unused-argument
        """Print separator. Optional - default no-op."""
        ...

    def print_tool_info(
        self, name: str, params_str: str, description: str
    ):  # pylint: disable=unused-argument
        """Print tool info. Optional - default no-op."""
        ...


class ProgressIndicator:
    """A simple progress indicator that shows a spinner or dots animation."""

    def __init__(self, message="Processing"):
        """Initialize the progress indicator.

        Args:
            message: The message to display before the animation
        """
        self.message = message
        self.is_running = False
        self.thread = None
        self.spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.dot_chars = [".", "..", "..."]
        self.spinner_idx = 0
        self.dot_idx = 0
        self.rich_spinner = None
        if RICH_AVAILABLE:
            self.rich_spinner = Spinner("dots", text=message)
            self.live = None

    def _animate(self):
        """Animation loop that runs in a separate thread."""
        while self.is_running:
            if RICH_AVAILABLE:
                # Rich handles the animation internally
                time.sleep(0.1)
            else:
                # Simple terminal-based animation
                self.dot_idx = (self.dot_idx + 1) % len(self.dot_chars)
                self.spinner_idx = (self.spinner_idx + 1) % len(self.spinner_chars)

                # Determine if we should use Unicode spinner or simple dots
                try:
                    # Try to print a Unicode character to see if the terminal supports it
                    print(self.spinner_chars[0], end="", flush=True)
                    print(
                        "\b", end="", flush=True
                    )  # Backspace to remove the test character

                    # If we got here, Unicode is supported
                    print(
                        f"\r{self.message} {self.spinner_chars[self.spinner_idx]}",
                        end="",
                        flush=True,
                    )
                except (UnicodeError, OSError):
                    # Fallback to simple dots
                    print(
                        f"\r{self.message}{self.dot_chars[self.dot_idx]}",
                        end="",
                        flush=True,
                    )

                time.sleep(0.1)

    def start(self, message=None):
        """Start the progress indicator.

        Args:
            message: Optional new message to display
        """
        if message:
            self.message = message

        if self.is_running:
            return

        self.is_running = True

        if RICH_AVAILABLE:
            if self.rich_spinner:
                self.rich_spinner.text = self.message
                # Use transient=True to auto-clear when done
                self.live = Live(
                    self.rich_spinner, refresh_per_second=10, transient=True
                )
                self.live.start()
        else:
            self.thread = threading.Thread(target=self._animate)
            self.thread.daemon = True
            self.thread.start()

    def stop(self):
        """Stop the progress indicator."""
        if not self.is_running:
            return

        self.is_running = False

        if RICH_AVAILABLE and self.live:
            self.live.stop()
        elif self.thread:
            self.thread.join(timeout=0.2)
            # Clear the animation line
            print("\r" + " " * (len(self.message) + 5) + "\r", end="", flush=True)


class AgentConsole(OutputHandler):
    """
    A class to handle all display-related functionality for the agent.
    Provides rich text formatting and progress indicators when available.
    Implements OutputHandler for CLI-based output.
    """

    def __init__(self):
        """Initialize the AgentConsole with appropriate display capabilities."""
        self.rich_available = RICH_AVAILABLE
        self.console = Console() if self.rich_available else None
        self.progress = ProgressIndicator()
        self.rprint = rprint
        self.Panel = Panel
        self.streaming_buffer = ""  # Buffer for accumulating streaming text
        self.file_preview_live: Optional[Live] = None
        self.file_preview_content = ""
        self.file_preview_filename = ""
        self.file_preview_max_lines = 15
        self._paused_preview = False  # Track if preview was paused for progress
        self._last_preview_update_time = 0  # Throttle preview updates
        self._preview_update_interval = 0.25  # Minimum seconds between updates

    # Implementation of OutputHandler abstract methods

    def pretty_print_json(self, data: Dict[str, Any], title: str = None) -> None:
        """
        Pretty print JSON data with syntax highlighting if Rich is available.
        If data contains a "command" field, shows it prominently.

        Args:
            data: Dictionary data to print
            title: Optional title for the panel
        """
        if self.rich_available:
            # Check if this is a command execution result
            if "command" in data and "stdout" in data:
                # Show command execution in a special format
                command = data.get("command", "")
                stdout = data.get("stdout", "")
                stderr = data.get("stderr", "")
                return_code = data.get("return_code", 0)

                # Build preview text
                preview = f"$ {command}\n\n"
                if stdout:
                    preview += stdout[:500]  # First 500 chars
                    if len(stdout) > 500:
                        preview += "\n... (output truncated)"
                if stderr:
                    preview += f"\n\nSTDERR:\n{stderr[:200]}"
                if return_code != 0:
                    preview += f"\n\n[Return code: {return_code}]"

                self.console.print(
                    Panel(
                        preview,
                        title=title or "Command Output",
                        border_style="blue",
                        expand=False,
                    )
                )
            else:
                # Regular JSON output
                # Convert to formatted JSON string
                json_str = json.dumps(data, indent=2)
                # Create a syntax object with JSON highlighting
                syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
                # Create a panel with a title if provided
                if title:
                    self.console.print(Panel(syntax, title=title, border_style="blue"))
                else:
                    self.console.print(syntax)
        else:
            # Fallback to standard pretty printing without highlighting
            if title:
                print(f"\n--- {title} ---")
            # Check if this is a command execution
            if "command" in data and "stdout" in data:
                print(f"\n$ {data.get('command', '')}")
                stdout = data.get("stdout", "")
                if stdout:
                    print(stdout[:500])
                    if len(stdout) > 500:
                        print("... (output truncated)")
            else:
                print(json.dumps(data, indent=2))

    def print_header(self, text: str) -> None:
        """
        Print a header with appropriate styling.

        Args:
            text: The header text to display
        """
        if self.rich_available:
            self.console.print(f"\n[bold blue]{text}[/bold blue]")
        else:
            print(f"\n{text}")

    def print_processing_start(self, query: str, max_steps: int) -> None:
        """
        Print the initial processing message with max steps info.

        Args:
            query: The user query being processed
            max_steps: Maximum number of steps allowed
        """
        if self.rich_available:
            self.console.print(f"\n[bold blue]🤖 Processing:[/bold blue] '{query}'")
            self.console.print("=" * 50)
            self.console.print(f"[dim]Max steps: {max_steps}[/dim]\n")
        else:
            print(f"\n🤖 Processing: '{query}'")
            print("=" * 50)
            print(f"Max steps: {max_steps}\n")

    def print_separator(self, length: int = 50) -> None:
        """
        Print a separator line.

        Args:
            length: Length of the separator line
        """
        if self.rich_available:
            self.console.print("=" * length, style="dim")
        else:
            print("=" * length)

    def print_step_header(self, step_num: int, step_limit: int) -> None:
        """
        Print a step header.

        Args:
            step_num: Current step number
            step_limit: Maximum number of steps (unused, kept for compatibility)
        """
        _ = step_limit  # Mark as intentionally unused
        if self.rich_available:
            self.console.print(
                f"\n[bold cyan]📝 Step {step_num}:[/bold cyan] Thinking...",
                highlight=False,
            )
        else:
            print(f"\n📝 Step {step_num}: Thinking...")

    def print_thought(self, thought: str) -> None:
        """
        Print the agent's thought with appropriate styling.

        Args:
            thought: The thought to display
        """
        if self.rich_available:
            self.console.print(f"[bold green]🧠 Thought:[/bold green] {thought}")
        else:
            print(f"🧠 Thought: {thought}")

    def print_goal(self, goal: str) -> None:
        """
        Print the agent's goal with appropriate styling.

        Args:
            goal: The goal to display
        """
        if self.rich_available:
            self.console.print(f"[bold yellow]🎯 Goal:[/bold yellow] {goal}")
        else:
            print(f"🎯 Goal: {goal}")

    def print_plan(self, plan: List[Any], current_step: int = None) -> None:
        """
        Print the agent's plan with appropriate styling.

        Args:
            plan: List of plan steps
            current_step: Optional index of the current step being executed (0-based)
        """
        if self.rich_available:
            self.console.print("\n[bold magenta]📋 Plan:[/bold magenta]")
            for i, step in enumerate(plan):
                step_text = step
                # Convert dict steps to string representation if needed
                if isinstance(step, dict):
                    if "tool" in step and "tool_args" in step:
                        args_str = json.dumps(step["tool_args"], sort_keys=True)
                        step_text = f"Use tool '{step['tool']}' with args: {args_str}"
                    else:
                        step_text = json.dumps(step)

                # Highlight the current step being executed
                if current_step is not None and i == current_step:
                    self.console.print(
                        f"  [dim]{i+1}.[/dim] [bold green]►[/bold green] [bold yellow]{step_text}[/bold yellow] [bold green]◄[/bold green] [cyan](current step)[/cyan]"
                    )
                else:
                    self.console.print(f"  [dim]{i+1}.[/dim] {step_text}")
            # Add an extra newline for better readability
            self.console.print("")
        else:
            print("\n📋 Plan:")
            for i, step in enumerate(plan):
                step_text = step
                # Convert dict steps to string representation if needed
                if isinstance(step, dict):
                    if "tool" in step and "tool_args" in step:
                        args_str = json.dumps(step["tool_args"], sort_keys=True)
                        step_text = f"Use tool '{step['tool']}' with args: {args_str}"
                    else:
                        step_text = json.dumps(step)

                # Highlight the current step being executed
                if current_step is not None and i == current_step:
                    print(f"  {i+1}. ► {step_text} ◄ (current step)")
                else:
                    print(f"  {i+1}. {step_text}")

    def print_plan_progress(
        self, current_step: int, total_steps: int, completed_steps: int = None
    ):
        """
        Print progress in plan execution

        Args:
            current_step: Current step being executed (1-based)
            total_steps: Total number of steps in the plan
            completed_steps: Optional number of already completed steps
        """
        if completed_steps is None:
            completed_steps = current_step - 1

        progress_str = f"[Step {current_step}/{total_steps}]"
        progress_bar = ""

        # Create a simple progress bar
        if total_steps > 0:
            bar_width = 20
            completed_chars = int((completed_steps / total_steps) * bar_width)
            current_char = 1 if current_step <= total_steps else 0
            remaining_chars = bar_width - completed_chars - current_char

            progress_bar = (
                "█" * completed_chars + "▶" * current_char + "░" * remaining_chars
            )

        if self.rich_available:
            self.rprint(f"[cyan]{progress_str}[/cyan] {progress_bar}")
        else:
            print(f"{progress_str} {progress_bar}")

    def print_tool_usage(self, tool_name: str) -> None:
        """
        Print tool usage information with user-friendly descriptions.

        Args:
            tool_name: Name of the tool being used
        """
        # Map tool names to user-friendly action descriptions
        tool_descriptions = {
            # RAG Tools
            "list_indexed_documents": "📚 Checking which documents are currently indexed",
            "query_documents": "🔍 Searching through indexed documents for relevant information",
            "query_specific_file": "📄 Searching within a specific document",
            "search_indexed_chunks": "🔎 Performing exact text search in indexed content",
            "index_document": "📥 Adding document to the knowledge base",
            "index_directory": "📁 Indexing all documents in a directory",
            "dump_document": "📝 Exporting document content for analysis",
            "summarize_document": "📋 Creating a summary of the document",
            "rag_status": "ℹ️ Retrieving RAG system status",
            # File System Tools
            "search_file": "🔍 Searching for files on your system",
            "search_directory": "📂 Looking for directories on your system",
            "search_file_content": "📝 Searching for content within files",
            "read_file": "📖 Reading file contents",
            "write_file": "✏️ Writing content to a file",
            "add_watch_directory": "👁️ Starting to monitor a directory for changes",
            # Shell Tools
            "run_shell_command": "💻 Executing shell command",
            # Default for unknown tools
            "default": "🔧 Executing operation",
        }

        # Get the description or use the tool name if not found
        action_desc = tool_descriptions.get(tool_name, tool_descriptions["default"])

        if self.rich_available:
            self.console.print(f"\n[bold blue]{action_desc}[/bold blue]")
            if action_desc == tool_descriptions["default"]:
                # If using default, also show the tool name
                self.console.print(f"  [dim]Tool: {tool_name}[/dim]")
        else:
            print(f"\n{action_desc}")
            if action_desc == tool_descriptions["default"]:
                print(f"  Tool: {tool_name}")

    def print_tool_complete(self) -> None:
        """Print that tool execution is complete."""
        if self.rich_available:
            self.console.print("[green]✅ Tool execution complete[/green]")
        else:
            print("✅ Tool execution complete")

    def print_error(self, error_message: str) -> None:
        """
        Print an error message with appropriate styling.

        Args:
            error_message: The error message to display
        """
        # Handle None error messages
        if error_message is None:
            error_message = "Unknown error occurred (received None)"

        if self.rich_available:
            self.console.print(
                Panel(str(error_message), title="⚠️ Error", border_style="red")
            )
        else:
            print(f"\n⚠️ ERROR: {error_message}\n")

    def print_info(self, message: str) -> None:
        """
        Print an information message.

        Args:
            message: The information message to display
        """
        if self.rich_available:
            self.console.print()  # Add newline before
            self.console.print(Panel(message, title="ℹ️  Info", border_style="blue"))
        else:
            print(f"\nℹ️ INFO: {message}\n")

    def print_success(self, message: str) -> None:
        """
        Print a success message.

        Args:
            message: The success message to display
        """
        if self.rich_available:
            self.console.print()  # Add newline before
            self.console.print(Panel(message, title="✅ Success", border_style="green"))
        else:
            print(f"\n✅ SUCCESS: {message}\n")

    def print_diff(self, diff: str, filename: str) -> None:
        """
        Print a code diff with syntax highlighting.

        Args:
            diff: The diff content to display
            filename: Name of the file being changed
        """
        if self.rich_available:
            from rich.syntax import Syntax

            self.console.print()  # Add newline before
            diff_panel = Panel(
                Syntax(diff, "diff", theme="monokai", line_numbers=True),
                title=f"🔧 Changes to {filename}",
                border_style="yellow",
            )
            self.console.print(diff_panel)
        else:
            print(f"\n🔧 DIFF for {filename}:")
            print("=" * 50)
            print(diff)
            print("=" * 50 + "\n")

    def print_repeated_tool_warning(self) -> None:
        """Print a warning about repeated tool calls."""
        message = "Detected repetitive tool call pattern. Agent execution paused to avoid an infinite loop. Try adjusting your prompt or agent configuration if this persists."

        if self.rich_available:
            self.console.print(
                Panel(
                    f"[bold yellow]{message}[/bold yellow]",
                    title="⚠️ Warning",
                    border_style="yellow",
                    padding=(1, 2),
                    highlight=True,
                )
            )
        else:
            print(f"\n⚠️ WARNING: {message}\n")

    def print_final_answer(
        self, answer: str, streaming: bool = True  # pylint: disable=unused-argument
    ) -> None:
        """
        Print the final answer with appropriate styling.

        Args:
            answer: The final answer to display
            streaming: Not used (kept for compatibility)
        """
        # Use the "🧠 gaia:" prefix for consistency
        if self.rich_available:
            self.console.print(f"\n[bold blue]🧠 gaia:[/bold blue] {answer}")
        else:
            print(f"\n🧠 gaia: {answer}")

    def print_completion(self, steps_taken: int, steps_limit: int) -> None:
        """
        Print completion information.

        Args:
            steps_taken: Number of steps taken
            steps_limit: Maximum number of steps allowed
        """
        self.print_separator()
        if self.rich_available:
            self.console.print(
                f"[bold blue]✨ Processing complete![/bold blue] Steps taken: {steps_taken}/{steps_limit}"
            )
        else:
            print(f"✨ Processing complete! Steps taken: {steps_taken}/{steps_limit}")

    def print_prompt(self, prompt: str, title: str = "Prompt") -> None:
        """
        Print a prompt with appropriate styling for debugging.

        Args:
            prompt: The prompt to display
            title: Optional title for the panel
        """
        if self.rich_available:
            from rich.syntax import Syntax

            # Use plain text instead of markdown to avoid any parsing issues
            # and ensure the full content is displayed
            syntax = Syntax(prompt, "text", theme="monokai", line_numbers=False)

            # Use expand=False to prevent Rich from trying to fit to terminal width
            # This ensures the full prompt is shown even if it's very long
            self.console.print(
                Panel(
                    syntax,
                    title=f"🔍 {title}",
                    border_style="cyan",
                    padding=(1, 2),
                    expand=False,
                )
            )
        else:
            print(f"\n🔍 {title}:\n{'-' * 80}\n{prompt}\n{'-' * 80}\n")

    def display_stats(self, stats: Dict[str, Any]) -> None:
        """
        Display LLM performance statistics or query execution stats.

        Args:
            stats: Dictionary containing performance statistics
                   Can include: duration, steps_taken, total_tokens (query stats)
                   Or: time_to_first_token, tokens_per_second, etc. (LLM stats)
        """
        if not stats:
            return

        # Check if we have query-level stats or LLM-level stats
        has_query_stats = any(
            key in stats for key in ["duration", "steps_taken", "total_tokens"]
        )
        has_llm_stats = any(
            key in stats for key in ["time_to_first_token", "tokens_per_second"]
        )

        # Skip if there's no meaningful stats
        if not has_query_stats and not has_llm_stats:
            return

        # Create a table for the stats
        title = "📊 Query Stats" if has_query_stats else "🚀 LLM Performance Stats"
        table = Table(
            title=title,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")

        # Add query-level stats (timing and steps)
        if "duration" in stats and stats["duration"] is not None:
            table.add_row("Duration", f"{stats['duration']:.2f}s")

        if "steps_taken" in stats and stats["steps_taken"] is not None:
            table.add_row("Steps", f"{stats['steps_taken']}")

        # Add LLM performance stats (timing)
        if "time_to_first_token" in stats and stats["time_to_first_token"] is not None:
            table.add_row("Time to First Token", f"{stats['time_to_first_token']:.2f}s")

        if "tokens_per_second" in stats and stats["tokens_per_second"] is not None:
            table.add_row("Tokens/Second", f"{stats['tokens_per_second']:.1f}")

        # Add token usage stats (always show in consistent format)
        if "input_tokens" in stats and stats["input_tokens"] is not None:
            table.add_row("Input Tokens", f"{stats['input_tokens']:,}")

        if "output_tokens" in stats and stats["output_tokens"] is not None:
            table.add_row("Output Tokens", f"{stats['output_tokens']:,}")

        if "total_tokens" in stats and stats["total_tokens"] is not None:
            table.add_row("Total Tokens", f"{stats['total_tokens']:,}")

        # Print the table in a panel
        self.console.print(Panel(table, border_style="blue"))

    def start_progress(self, message: str) -> None:
        """
        Start the progress indicator.

        Args:
            message: Message to display with the indicator
        """
        # If file preview is active, pause it temporarily
        self._paused_preview = False
        if self.file_preview_live is not None:
            try:
                self.file_preview_live.stop()
                self._paused_preview = True
                self.file_preview_live = None
                # Small delay to ensure clean transition
                time.sleep(0.05)
            except Exception:
                pass

        self.progress.start(message)

    def stop_progress(self) -> None:
        """Stop the progress indicator."""
        self.progress.stop()

        # Ensure clean line separation after progress stops
        if self.rich_available:
            # Longer delay to ensure the transient display is FULLY cleared
            time.sleep(0.15)
            # Explicitly move to a new line
            print()  # Use print() instead of console.print() to avoid Live display conflicts

        # NOTE: Do NOT create Live display here - let update_file_preview() handle it
        # This prevents double panels from appearing when both stop_progress and update_file_preview execute

        # Reset the paused flag
        if hasattr(self, "_paused_preview"):
            self._paused_preview = False

    def print_state_info(self, state_message: str):
        """
        Print the current execution state

        Args:
            state_message: Message describing the current state
        """
        if self.rich_available:
            self.console.print(
                self.Panel(
                    f"🔄 [bold cyan]{state_message}[/bold cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                )
            )
        else:
            print(f"🔄 STATE: {state_message}")

    def print_warning(self, warning_message: str):
        """
        Print a warning message

        Args:
            warning_message: Warning message to display
        """
        if self.rich_available:
            self.console.print()  # Add newline before
            self.console.print(
                self.Panel(
                    f"⚠️ [bold yellow] {warning_message} [/bold yellow]",
                    border_style="yellow",
                    padding=(0, 1),
                )
            )
        else:
            print(f"⚠️ WARNING: {warning_message}")

    def print_streaming_text(
        self, text_chunk: str, end_of_stream: bool = False
    ) -> None:
        """
        Print text content as it streams in, without newlines between chunks.

        Args:
            text_chunk: The chunk of text from the stream
            end_of_stream: Whether this is the last chunk
        """
        # Accumulate text in the buffer
        self.streaming_buffer += text_chunk

        # Print the chunk directly to console
        if self.rich_available:
            # Use low-level print to avoid adding newlines
            print(text_chunk, end="", flush=True)
        else:
            print(text_chunk, end="", flush=True)

        # If this is the end of the stream, add a newline
        if end_of_stream:
            print()

    def get_streaming_buffer(self) -> str:
        """
        Get the accumulated streaming text and reset buffer.

        Returns:
            The complete accumulated text from streaming
        """
        result = self.streaming_buffer
        self.streaming_buffer = ""  # Reset buffer
        return result

    def print_response(self, response: str, title: str = "Response") -> None:
        """
        Print an LLM response with appropriate styling.

        Args:
            response: The response text to display
            title: Optional title for the panel
        """
        if self.rich_available:
            from rich.syntax import Syntax

            syntax = Syntax(response, "markdown", theme="monokai", line_numbers=False)
            self.console.print(
                Panel(syntax, title=f"🤖 {title}", border_style="green", padding=(1, 2))
            )
        else:
            print(f"\n🤖 {title}:\n{'-' * 80}\n{response}\n{'-' * 80}\n")

    def print_tool_info(self, name: str, params_str: str, description: str) -> None:
        """
        Print information about a tool with appropriate styling.

        Args:
            name: Name of the tool
            params_str: Formatted string of parameters
            description: Tool description
        """
        if self.rich_available:
            self.console.print(
                f"[bold cyan]📌 {name}[/bold cyan]([italic]{params_str}[/italic])"
            )
            self.console.print(f"   [dim]{description}[/dim]")
        else:
            print(f"\n📌 {name}({params_str})")
            print(f"   {description}")

    def start_file_preview(
        self, filename: str, max_lines: int = 15, title_prefix: str = "📄"
    ) -> None:
        """
        Start a live streaming file preview window.

        Args:
            filename: Name of the file being generated
            max_lines: Maximum number of lines to show (default: 15)
            title_prefix: Emoji/prefix for the title (default: 📄)
        """
        # CRITICAL: Stop progress indicator if running to prevent overlapping Live displays
        if self.progress.is_running:
            self.stop_progress()

        # Stop any existing preview first to prevent stacking
        if self.file_preview_live is not None:
            try:
                self.file_preview_live.stop()
            except Exception:
                pass  # Ignore errors if already stopped
            finally:
                self.file_preview_live = None
                # Small delay to ensure display cleanup
                time.sleep(0.1)
                # Ensure we're on a new line after stopping the previous preview
                if self.rich_available:
                    self.console.print()

        # Reset state for new file
        self.file_preview_filename = filename
        self.file_preview_content = ""
        self.file_preview_max_lines = max_lines

        if self.rich_available:
            # DON'T start the live preview here - wait for first content
            pass
        else:
            # For non-rich mode, just print a header
            print(f"\n{title_prefix} Generating {filename}...")
            print("=" * 80)

    def update_file_preview(self, content_chunk: str) -> None:
        """
        Update the live file preview with new content.

        Args:
            content_chunk: New content to append to the preview
        """
        self.file_preview_content += content_chunk

        if self.rich_available:
            # Only process if we have a filename set (preview has been started)
            if not self.file_preview_filename:
                return

            # Check if enough time has passed for throttling
            current_time = time.time()
            time_since_last_update = current_time - self._last_preview_update_time

            # Start the live preview on first content if not already started
            if self.file_preview_live is None and self.file_preview_content:
                preview = self._generate_file_preview_panel("📄")
                self.file_preview_live = Live(
                    preview,
                    console=self.console,
                    refresh_per_second=4,
                    transient=False,  # Keep False to prevent double rendering
                )
                self.file_preview_live.start()
                self._last_preview_update_time = current_time
            elif (
                self.file_preview_live
                and time_since_last_update >= self._preview_update_interval
            ):
                try:
                    # Update existing live display with new content
                    preview = self._generate_file_preview_panel("📄")
                    # Just update, don't force refresh
                    self.file_preview_live.update(preview)
                    self._last_preview_update_time = current_time
                except Exception:
                    # If update fails, continue accumulating content
                    # (silently ignore preview update failures)
                    pass
        else:
            # For non-rich mode, print new content directly
            print(content_chunk, end="", flush=True)

    def stop_file_preview(self) -> None:
        """Stop the live file preview and show final summary."""
        if self.rich_available:
            # Only stop if it was started
            if self.file_preview_live:
                try:
                    self.file_preview_live.stop()
                except Exception:
                    pass
                finally:
                    self.file_preview_live = None

            # Show completion message only if we generated content
            if self.file_preview_content:
                total_lines = len(self.file_preview_content.splitlines())
                self.console.print(
                    f"[green]✅ Generated {self.file_preview_filename} ({total_lines} lines)[/green]\n"
                )
        else:
            print("\n" + "=" * 80)
            total_lines = len(self.file_preview_content.splitlines())
            print(f"✅ Generated {self.file_preview_filename} ({total_lines} lines)\n")

        # Reset state - IMPORTANT: Clear filename first to prevent updates
        self.file_preview_filename = ""
        self.file_preview_content = ""

    def _generate_file_preview_panel(self, title_prefix: str) -> Panel:
        """
        Generate a Rich Panel with the current file preview content.

        Args:
            title_prefix: Emoji/prefix for the title

        Returns:
            Rich Panel with syntax-highlighted content
        """
        lines = self.file_preview_content.splitlines()
        total_lines = len(lines)

        # Truncate extremely long lines to prevent display issues
        truncated_lines = []
        for line in lines:
            if len(line) > MAX_DISPLAY_LINE_LENGTH:
                truncated_lines.append(line[:MAX_DISPLAY_LINE_LENGTH] + "...")
            else:
                truncated_lines.append(line)

        # Show last N lines
        if total_lines <= self.file_preview_max_lines:
            preview_lines = truncated_lines
            line_info = f"All {total_lines} lines"
        else:
            preview_lines = truncated_lines[-self.file_preview_max_lines :]
            line_info = f"Last {self.file_preview_max_lines} of {total_lines} lines"

        # Determine syntax highlighting
        ext = (
            self.file_preview_filename.split(".")[-1]
            if "." in self.file_preview_filename
            else "txt"
        )
        syntax_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "jsx": "jsx",
            "tsx": "tsx",
            "json": "json",
            "md": "markdown",
            "yml": "yaml",
            "yaml": "yaml",
            "toml": "toml",
            "ini": "ini",
            "sh": "bash",
            "bash": "bash",
            "ps1": "powershell",
            "sql": "sql",
            "html": "html",
            "css": "css",
            "xml": "xml",
            "c": "c",
            "cpp": "cpp",
            "java": "java",
            "go": "go",
            "rs": "rust",
        }
        syntax_lang = syntax_map.get(ext.lower(), "text")

        # Create syntax-highlighted preview
        preview_content = (
            "\n".join(preview_lines) if preview_lines else "[dim]Generating...[/dim]"
        )

        if preview_lines:
            # Calculate starting line number for the preview
            if total_lines <= self.file_preview_max_lines:
                start_line = 1
            else:
                start_line = total_lines - self.file_preview_max_lines + 1

            syntax = Syntax(
                preview_content,
                syntax_lang,
                theme="monokai",
                line_numbers=True,
                start_line=start_line,
                word_wrap=False,  # Prevent line wrapping that causes display issues
            )
        else:
            syntax = preview_content

        return Panel(
            syntax,
            title=f"{title_prefix} {self.file_preview_filename} ({line_info})",
            border_style="cyan",
            padding=(1, 2),
        )


class SilentConsole(OutputHandler):
    """
    A silent console that suppresses all output for JSON-only mode.
    Provides the same interface as AgentConsole but with no-op methods.
    Implements OutputHandler for silent/suppressed output.
    """

    def __init__(self, silence_final_answer: bool = False):
        """Initialize the silent console.

        Args:
            silence_final_answer: If True, suppress even the final answer (for JSON-only mode)
        """
        self.streaming_buffer = ""  # Maintain compatibility
        self.silence_final_answer = silence_final_answer

    # Implementation of OutputHandler abstract methods - all no-ops
    def print_final_answer(
        self, answer: str, streaming: bool = True  # pylint: disable=unused-argument
    ) -> None:
        """
        Print the final answer.
        Only suppressed if silence_final_answer is True.

        Args:
            answer: The final answer to display
            streaming: Not used (kept for compatibility)
        """
        if self.silence_final_answer:
            return  # Completely silent

        # Print the final answer directly
        print(f"\n🧠 gaia: {answer}")

    def display_stats(self, stats: Dict[str, Any]) -> None:
        """
        Display stats even in silent mode (since explicitly requested).
        Uses the same Rich table format as AgentConsole.

        Args:
            stats: Dictionary containing performance statistics
        """
        if not stats:
            return

        # Check if we have query-level stats or LLM-level stats
        has_query_stats = any(
            key in stats for key in ["duration", "steps_taken", "total_tokens"]
        )
        has_llm_stats = any(
            key in stats for key in ["time_to_first_token", "tokens_per_second"]
        )

        # Skip if there's no meaningful stats
        if not has_query_stats and not has_llm_stats:
            return

        # Use Rich table format (same as AgentConsole)
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()

        title = "📊 Query Stats" if has_query_stats else "🚀 LLM Performance Stats"
        table = Table(
            title=title,
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")

        # Add query-level stats (timing and steps)
        if "duration" in stats and stats["duration"] is not None:
            table.add_row("Duration", f"{stats['duration']:.2f}s")

        if "steps_taken" in stats and stats["steps_taken"] is not None:
            table.add_row("Steps", f"{stats['steps_taken']}")

        # Add LLM performance stats (timing)
        if "time_to_first_token" in stats and stats["time_to_first_token"] is not None:
            table.add_row("Time to First Token", f"{stats['time_to_first_token']:.2f}s")

        if "tokens_per_second" in stats and stats["tokens_per_second"] is not None:
            table.add_row("Tokens/Second", f"{stats['tokens_per_second']:.1f}")

        # Add token usage stats (always show in consistent format)
        if "input_tokens" in stats and stats["input_tokens"] is not None:
            table.add_row("Input Tokens", f"{stats['input_tokens']:,}")

        if "output_tokens" in stats and stats["output_tokens"] is not None:
            table.add_row("Output Tokens", f"{stats['output_tokens']:,}")

        if "total_tokens" in stats and stats["total_tokens"] is not None:
            table.add_row("Total Tokens", f"{stats['total_tokens']:,}")

        # Print the table in a panel
        console.print(Panel(table, border_style="blue"))

    # All other abstract methods as no-ops
    def print_processing_start(self, query: str, max_steps: int):
        """No-op implementation."""

    def print_step_header(self, step_num: int, step_limit: int):
        """No-op implementation."""

    def print_state_info(self, state_message: str):
        """No-op implementation."""

    def print_thought(self, thought: str):
        """No-op implementation."""

    def print_goal(self, goal: str):
        """No-op implementation."""

    def print_plan(self, plan: List[Any], current_step: int = None):
        """No-op implementation."""

    def print_tool_usage(self, tool_name: str):
        """No-op implementation."""

    def print_tool_complete(self):
        """No-op implementation."""

    def pretty_print_json(self, data: Dict[str, Any], title: str = None):
        """No-op implementation."""

    def print_error(self, error_message: str):
        """No-op implementation."""

    def print_warning(self, warning_message: str):
        """No-op implementation."""

    def print_info(self, message: str):
        """No-op implementation."""

    def start_progress(self, message: str):
        """No-op implementation."""

    def stop_progress(self):
        """No-op implementation."""

    def print_repeated_tool_warning(self):
        """No-op implementation."""

    def print_completion(self, steps_taken: int, steps_limit: int):
        """No-op implementation."""
