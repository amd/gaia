import json
import time
import threading
from typing import Dict, Any, List

# Import Rich library for pretty printing and syntax highlighting
try:
    from rich.console import Console
    from rich.syntax import Syntax
    from rich.panel import Panel
    from rich import print as rprint
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print(
        "Rich library not found. Install with 'pip install rich' for syntax highlighting."
    )


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
                self.live = Live(self.rich_spinner, refresh_per_second=10)
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


class AgentConsole:
    """
    A class to handle all display-related functionality for the agent.
    Provides rich text formatting and progress indicators when available.
    """

    def __init__(self):
        """Initialize the AgentConsole with appropriate display capabilities."""
        self.rich_available = RICH_AVAILABLE
        self.console = Console() if self.rich_available else None
        self.progress = ProgressIndicator()
        self.rprint = rprint
        self.Panel = Panel
        self.streaming_buffer = ""  # Buffer for accumulating streaming text

    def pretty_print_json(self, data: Dict[str, Any], title: str = None) -> None:
        """
        Pretty print JSON data with syntax highlighting if Rich is available.

        Args:
            data: Dictionary data to print
            title: Optional title for the panel
        """
        if self.rich_available:
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
            step_limit: Maximum number of steps
        """
        if self.rich_available:
            self.console.print(
                f"\n[bold cyan]📝 Step {step_num}/{step_limit}:[/bold cyan] Thinking...",
                highlight=False,
            )
        else:
            print(f"\n📝 Step {step_num}/{step_limit}: Thinking...")

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
        Print tool usage information.

        Args:
            tool_name: Name of the tool being used
        """
        if self.rich_available:
            self.console.print(f"\n[bold blue]🔧 Using tool:[/bold blue] {tool_name}")
        else:
            print(f"\n🔧 Using tool: {tool_name}")

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
            self.console.print(Panel(message, title="ℹ️  Info", border_style="blue"))
        else:
            print(f"\nℹ️ INFO: {message}\n")

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

    def print_final_answer(self, answer: str) -> None:
        """
        Print the final answer with appropriate styling.

        Args:
            answer: The final answer to display
        """
        if self.rich_available:
            self.console.print(f"\n[bold green]✅ Final answer:[/bold green] {answer}")
        else:
            print(f"\n✅ Final answer: {answer}")

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

            syntax = Syntax(prompt, "markdown", theme="monokai", line_numbers=False)
            self.console.print(
                Panel(syntax, title=f"🔍 {title}", border_style="cyan", padding=(1, 2))
            )
        else:
            print(f"\n🔍 {title}:\n{'-' * 80}\n{prompt}\n{'-' * 80}\n")

    def display_stats(self, stats: Dict[str, Any]) -> None:
        """
        Display LLM performance statistics.

        Args:
            stats: Dictionary containing performance statistics
        """
        if not stats:
            return

        # Skip if there's no meaningful stats
        if not stats.get("time_to_first_token") and not stats.get("tokens_per_second"):
            return

        # Create a nice display of the stats
        if self.rich_available:
            # Create a table for the stats
            table = Table(
                title="🚀 LLM Performance Stats",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Metric", style="dim")
            table.add_column("Value", justify="right")

            # Add stats to the table
            if (
                "time_to_first_token" in stats
                and stats["time_to_first_token"] is not None
            ):
                table.add_row(
                    "Time to First Token", f"{stats['time_to_first_token']:.2f} sec"
                )

            if "tokens_per_second" in stats and stats["tokens_per_second"] is not None:
                table.add_row("Tokens per Second", f"{stats['tokens_per_second']:.2f}")

            if "input_tokens" in stats and stats["input_tokens"] is not None:
                table.add_row("Input Tokens", f"{stats['input_tokens']}")

            if "output_tokens" in stats and stats["output_tokens"] is not None:
                table.add_row("Output Tokens", f"{stats['output_tokens']}")

            # Print the table in a panel
            self.console.print(Panel(table, border_style="blue"))
        else:
            # Plain text fallback
            print("\n--- LLM Performance Stats ---")
            if (
                "time_to_first_token" in stats
                and stats["time_to_first_token"] is not None
            ):
                print(f"Time to First Token: {stats['time_to_first_token']:.2f} sec")
            if "tokens_per_second" in stats and stats["tokens_per_second"] is not None:
                print(f"Tokens per Second: {stats['tokens_per_second']:.2f}")
            if "input_tokens" in stats and stats["input_tokens"] is not None:
                print(f"Input Tokens: {stats['input_tokens']}")
            if "output_tokens" in stats and stats["output_tokens"] is not None:
                print(f"Output Tokens: {stats['output_tokens']}")
            print("-----------------------------")

    def start_progress(self, message: str) -> None:
        """
        Start the progress indicator.

        Args:
            message: Message to display with the indicator
        """
        self.progress.start(message)

    def stop_progress(self) -> None:
        """Stop the progress indicator."""
        self.progress.stop()

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
