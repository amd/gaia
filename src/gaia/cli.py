# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from gaia.llm.lemonade_client import (
    DEFAULT_HOST,
    DEFAULT_LEMONADE_URL,
    DEFAULT_MODEL_NAME,
    DEFAULT_PORT,
    LemonadeClient,
    LemonadeClientError,
    _get_lemonade_config,
)
from gaia.llm.llm_client import LLMClient
from gaia.logger import get_logger
from gaia.version import version

# Optional imports
try:
    from gaia.agents.blender.agent import BlenderAgent
    from gaia.mcp.blender_mcp_client import MCPClient

    BLENDER_AVAILABLE = True
except ImportError:
    BlenderAgent = None
    MCPClient = None
    BLENDER_AVAILABLE = False

try:
    from gaia.agents.code.agent import CodeAgent

    CODE_AVAILABLE = True
except ImportError:
    CodeAgent = None
    CODE_AVAILABLE = False

# Load environment variables from .env file
load_dotenv()

# Set debug level for the logger
logging.getLogger("gaia").setLevel(logging.INFO)

# Add the parent directory to sys.path to import gaia modules
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent.parent.parent
sys.path.append(str(parent_dir))

# Default directories for evaluation workflow
DEFAULT_WORKSPACE = Path.cwd()
DEFAULT_TEST_DATA_DIR = "output/test_data"
DEFAULT_GROUNDTRUTH_DIR = "output/groundtruth"
DEFAULT_EXPERIMENTS_DIR = "output/experiments"
DEFAULT_EVALUATIONS_DIR = "output/evaluations"


# Helper functions for download progress display
def _format_bytes(b: int) -> str:
    """Format bytes to human readable string."""
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.1f} GB"
    elif b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    elif b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def _make_progress_bar(percent: int, width: int = 20) -> str:
    """Create a progress bar string."""
    filled = int(width * percent / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def check_lemonade_health(host=None, port=None):
    """Check if Lemonade server is running and healthy using LemonadeClient."""
    log = get_logger(__name__)

    # Use provided host/port, or get from env var, or use defaults
    env_host, env_port, _ = _get_lemonade_config()
    host = host if host is not None else env_host
    port = port if port is not None else env_port

    try:
        # Create a LemonadeClient instance for health checking
        client = LemonadeClient(host=host, port=port, verbose=False, keep_alive=True)

        # Perform health check
        health_result = client.health_check()

        # Check if the response indicates the server is healthy
        if health_result.get("status") == "ok":
            log.debug(f"Lemonade server is healthy at {host}:{port}")
            return True
        else:
            log.debug(f"Lemonade server health check returned: {health_result}")
            return False

    except LemonadeClientError as e:
        log.debug(f"Lemonade health check failed: {str(e)}")
        return False
    except Exception as e:
        log.debug(f"Unexpected error during Lemonade health check: {str(e)}")
        return False


def is_lemonade_installed() -> bool:
    """Check if Lemonade server is installed by attempting to get its version.

    Uses LemonadeClient.get_lemonade_version() which runs 'lemonade-server --version'.
    Returns True if the version can be retrieved, False otherwise.
    """
    # Create a minimal client instance just to check version
    client = LemonadeClient(verbose=False)
    return client.get_lemonade_version() is not None


def print_lemonade_error(for_code_agent=False):
    """Print informative error message when Lemonade is not running.

    Args:
        for_code_agent: If True, includes instructions for --ctx-size parameter
    """
    print(
        "❌ Error: Lemonade server is not running or not accessible.", file=sys.stderr
    )
    print("", file=sys.stderr)

    # Check if lemonade-server is installed
    if not is_lemonade_installed():
        print("📥 Lemonade server is not installed on your system.", file=sys.stderr)
        print("", file=sys.stderr)
        print("To install Lemonade server:", file=sys.stderr)
        print("  1. Visit: https://lemonade-server.ai", file=sys.stderr)
        print("  2. Download the installer for your platform", file=sys.stderr)
        print("  3. Run the installer and follow prompts", file=sys.stderr)
        print("", file=sys.stderr)
        print("After installation, try your command again.", file=sys.stderr)
    else:
        print("Lemonade server is installed but not running.", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "GAIA will automatically start Lemonade Server if installed.",
            file=sys.stderr,
        )
        print("If auto-start fails, you can start it manually by:", file=sys.stderr)
        print("  • Double-clicking the desktop shortcut, or", file=sys.stderr)
        if for_code_agent:
            print(
                "  • Running: lemonade-server serve --ctx-size 32768",
                file=sys.stderr,
            )
        else:
            print("  • Running: lemonade-server serve", file=sys.stderr)
        print("", file=sys.stderr)
        if for_code_agent:
            print(
                "Note: GAIA requires larger context size (32768 tokens)",
                file=sys.stderr,
            )
            print("", file=sys.stderr)
        base_url = os.getenv("LEMONADE_BASE_URL", f"{DEFAULT_LEMONADE_URL}/api/v1")
        print(
            f"The server should be accessible at {base_url}/health",
            file=sys.stderr,
        )
        print("Then try your command again.", file=sys.stderr)


def initialize_lemonade_for_agent(
    agent: str,
    auto_start: bool = True,
    quiet: bool = False,
    skip_if_external: bool = False,
    use_claude: bool = False,
    use_chatgpt: bool = False,
    host: str = None,
    port: int = None,
):
    """
    Initialize Lemonade Server for a specific GAIA agent.

    This function uses agent-specific profiles to ensure the correct context size
    and model requirements are met. It provides consistent initialization across
    all GAIA CLI commands.

    Args:
        agent: Agent name (chat, code, talk, rag, blender, jira, docker, vlm, minimal, mcp)
        auto_start: Automatically start server if not running
        quiet: Suppress output (only errors)
        skip_if_external: If True, skip initialization when using Claude/ChatGPT
        use_claude: Whether Claude API is being used
        use_chatgpt: Whether ChatGPT API is being used
        host: Host address of the Lemonade server (defaults to LEMONADE_BASE_URL env var)
        port: Port number of the Lemonade server (defaults to LEMONADE_BASE_URL env var)

    Returns:
        Tuple of (success: bool, base_url: str, version: str)

    Note:
        Host and port can be configured via LEMONADE_BASE_URL environment variable.

    Example:
        success, base_url, version = initialize_lemonade_for_agent("chat")
        if not success:
            sys.exit(1)
    """
    log = get_logger(__name__)

    # Use provided host/port, or get from env var, or use defaults
    env_host, env_port, env_base_url = _get_lemonade_config()
    host = host if host is not None else env_host
    port = port if port is not None else env_port

    # Skip initialization if using external API
    if skip_if_external and (use_claude or use_chatgpt):
        return True, env_base_url, None

    try:
        client = LemonadeClient(host=host, port=port, keep_alive=True, verbose=False)
        status = client.initialize(
            agent=agent,
            auto_start=auto_start,
            quiet=quiet,
        )

        if not status.running:
            if not quiet:
                print_lemonade_error(for_code_agent=(agent in ["code", "chat"]))
            return False, None, None

        # For chat and code agents, check if context size is sufficient
        # These agents require 32768 tokens and will fail mid-execution with
        # cryptic errors if context is insufficient
        required_ctx = 32768
        if agent in ["code", "chat"] and status.context_size < required_ctx:
            if not quiet:
                print("")
                print(
                    f"❌ Insufficient context size for {agent} agent.", file=sys.stderr
                )
                print(
                    f"   Current: {status.context_size} tokens, "
                    f"Required: {required_ctx} tokens",
                    file=sys.stderr,
                )
                print("", file=sys.stderr)
                print("   To fix this issue:", file=sys.stderr)
                print(
                    "   1. Stop the Lemonade server (if running)",
                    file=sys.stderr,
                )
                print(
                    f"   2. Restart with: lemonade-server serve --ctx-size {required_ctx}",
                    file=sys.stderr,
                )
                print("", file=sys.stderr)
            return False, None, None

        # Get server version for logging/debugging
        server_version = client.get_lemonade_version()

        # Use the client's base_url which is configured for the correct API version
        base_url = client.base_url

        return True, base_url, server_version

    except Exception as e:
        log.error(f"Lemonade initialization failed: {e}")
        if not quiet:
            print(f"❌ Lemonade initialization failed: {e}", file=sys.stderr)
        return False, None, None


def ensure_agent_models(
    agent: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    quiet: bool = False,
    timeout: int = 1800,
) -> bool:
    """
    Ensure all models required for an agent are downloaded.

    This function checks if models are available and downloads them with
    streaming progress if needed. Called before starting agents to provide
    user feedback during model downloads.

    Args:
        agent: Agent name (chat, code, rag, talk, blender, jira, docker, vlm, minimal, mcp)
        host: Lemonade server host
        port: Lemonade server port
        quiet: Suppress output (only errors)
        timeout: Timeout per model in seconds

    Returns:
        bool: True if all models are available, False on error
    """
    log = get_logger(__name__)

    try:
        client = LemonadeClient(host=host, port=port, verbose=False)

        # Get required models for this agent
        model_ids = client.get_required_models(agent)

        if not model_ids:
            return True

        # Check which models need downloading
        models_to_download = []
        for model_id in model_ids:
            if not client.check_model_available(model_id):
                models_to_download.append(model_id)

        if not models_to_download:
            log.debug(f"All models for {agent} agent already available")
            return True

        if not quiet:
            print(
                f"📥 Downloading {len(models_to_download)} model(s) for {agent} agent..."
            )
            print()

        # Progress tracking
        last_percent = [-1]
        last_file_index = [0]

        def progress_callback(event_type: str, data: dict) -> None:
            """Display download progress in CLI."""
            if quiet:
                return

            if event_type == "progress":
                percent = data.get("percent", 0)
                file_name = data.get("file", "unknown")
                file_index = data.get("file_index", 1)
                total_files = data.get("total_files", 1)

                # Print newline when moving to a new file
                if file_index != last_file_index[0] and last_file_index[0] > 0:
                    print()  # Newline for previous file
                last_file_index[0] = file_index

                # Update every 2% for smooth progress
                if percent >= last_percent[0] + 2 or percent == 0 or percent == 100:
                    bytes_downloaded = data.get("bytes_downloaded", 0)
                    bytes_total = data.get("bytes_total", 0)

                    # Create progress bar
                    bar = _make_progress_bar(percent)
                    progress_line = (
                        f"   {bar} {percent:3d}% "
                        f"[{file_index}/{total_files}] {file_name}: "
                        f"{_format_bytes(bytes_downloaded)}/{_format_bytes(bytes_total)}"
                    )
                    print(f"\r{progress_line:<100}", end="", flush=True)
                    last_percent[0] = percent

            elif event_type == "complete":
                print()  # Newline after progress
                print("   ✅ Download complete")
                last_percent[0] = -1
                last_file_index[0] = 0

            elif event_type == "error":
                print()  # Newline after progress
                error_msg = data.get("error", "Unknown error")
                print(f"   ❌ Error: {error_msg}")

        # Download each model
        for model_id in models_to_download:
            last_percent[0] = -1

            if not quiet:
                print(f"📥 {model_id}")

            try:
                for event in client.pull_model_stream(
                    model_name=model_id,
                    timeout=timeout,
                    progress_callback=progress_callback,
                ):
                    if event.get("event") == "error":
                        log.error(f"Failed to download {model_id}")
                        return False
            except LemonadeClientError as e:
                log.error(f"Failed to download {model_id}: {e}")
                if not quiet:
                    print(f"   ❌ Failed: {e}")
                return False

            if not quiet:
                print()

        if not quiet:
            print(f"✅ All models ready for {agent} agent")
            print()

        return True

    except Exception as e:
        log.error(f"Failed to ensure models for {agent}: {e}")
        if not quiet:
            print(f"❌ Error checking/downloading models: {e}", file=sys.stderr)
        return False


def check_mcp_health(host="localhost", port=9876):
    """Check if Blender MCP server is running and accessible."""
    log = get_logger(__name__)

    try:
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            log.debug("Blender MCP server is accessible")
            return True
        else:
            log.debug(f"Failed to connect to Blender MCP server on {host}:{port}")
            return False
    except Exception as e:
        log.debug(f"Error checking MCP server: {str(e)}")
        return False


def print_mcp_error():
    """Print informative error message when Blender MCP server is not running."""
    print(
        "❌ Error: Blender MCP server is not running or not accessible.",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    print("To set up the Blender MCP server:", file=sys.stderr)
    print("", file=sys.stderr)
    print("1. Open Blender (version 4.3 or newer recommended)", file=sys.stderr)
    print("2. Go to Edit > Preferences > Add-ons", file=sys.stderr)
    print("3. Click the down arrow button, then 'Install...'", file=sys.stderr)
    print(
        "4. Navigate to: <GAIA_REPO>/src/gaia/mcp/blender_mcp_server.py",
        file=sys.stderr,
    )
    print("5. Install and enable the 'Simple Blender MCP' add-on", file=sys.stderr)
    print(
        "6. Open the 3D viewport sidebar (press 'N' key if not visible)",
        file=sys.stderr,
    )
    print("7. Find the 'Blender MCP' panel in the sidebar", file=sys.stderr)
    print("8. Set port to 9876 and click 'Start Server'", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "For detailed setup instructions, see: workshop/blender.ipynb", file=sys.stderr
    )
    print("", file=sys.stderr)
    print("Then try your Blender command again.", file=sys.stderr)


class GaiaCliClient:
    log = get_logger(__name__)

    def __init__(
        self,
        model=DEFAULT_MODEL_NAME,
        max_tokens=512,
        show_stats=False,
        logging_level="INFO",
    ):
        self.log = self.__class__.log  # Use the class-level logger for instances
        # Set the logging level for this instance's logger
        self.log.setLevel(getattr(logging, logging_level))

        self.model = model
        self.max_tokens = max_tokens
        self.cli_mode = True  # Set this to True for CLI mode
        self.show_stats = show_stats

        # Initialize LLM client for local inference
        self.llm_client = LLMClient()

        self.log.debug("Gaia CLI client initialized.")
        self.log.debug(f"model: {self.model}\n max_tokens: {self.max_tokens}")

    async def send_message(self, message):
        try:
            # Use LLMClient.generate with streaming
            response_generator = self.llm_client.generate(
                prompt=message,
                model=self.model,
                stream=True,
                max_tokens=self.max_tokens,
            )

            for chunk in response_generator:
                print(chunk, end="", flush=True)
                yield chunk

        except Exception as e:
            error_message = f"❌ Error: {str(e)}"
            self.log.error(error_message)
            print(error_message)
            yield error_message

    def get_stats(self):
        try:
            stats = self.llm_client.get_performance_stats()
            self.log.debug(f"Stats received: {stats}")
            return stats
        except Exception as e:
            self.log.error(f"Error while fetching stats: {str(e)}")
            return None

    async def prompt(self, message):
        async for chunk in self.send_message(message):
            yield chunk

    def chat(
        self,
        message=None,
        model=None,
        max_tokens=512,
        system_prompt=None,
        assistant_name=None,
        stats=False,
    ):
        """Chat interface using the new ChatApp - interactive if no message, single message if message provided"""
        try:
            from gaia.chat.sdk import ChatConfig, ChatSDK

            # Interactive mode if no message provided, single message mode if message provided
            use_interactive = message is None

            # Build config dict, only include model if specified
            config_kwargs = {
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "assistant_name": assistant_name or "assistant",
                "show_stats": stats,
            }
            if model:
                config_kwargs["model"] = model

            if use_interactive:
                # Interactive mode using ChatSDK
                config = ChatConfig(**config_kwargs)
                chat = ChatSDK(config)
                asyncio.run(chat.start_interactive_session())
            else:
                # Single message mode with streaming
                config = ChatConfig(**config_kwargs)
                chat = ChatSDK(config)
                full_response = ""
                for chunk in chat.send_stream(message):
                    if not chunk.is_complete:
                        print(chunk.text, end="", flush=True)
                        full_response += chunk.text
                    else:
                        # Show stats if configured and available
                        if stats and chunk.stats:
                            print()  # Add newline before stats
                            chat.display_stats(chunk.stats)
                print()  # Add final newline
                return full_response

        except Exception as e:
            # Check if it's a connection error and provide helpful message
            self.log.error(f"Error in chat: {str(e)}")
            print(f"❌ Error: {str(e)}")
            sys.exit(1)


async def async_main(action, **kwargs):
    log = get_logger(__name__)

    # Map actions to agent profiles for Lemonade initialization
    # Each agent has specific model and context size requirements
    # Note: code, blender, jira, docker are handled by their own handler functions
    action_to_agent = {
        "prompt": "minimal",  # Basic prompts use minimal profile
        "chat": "chat",
        "talk": "talk",
        "stats": "minimal",
    }

    # Initialize Lemonade with agent-specific profile
    lemonade_base_url = kwargs.get("base_url")  # May be None if not specified
    if action in action_to_agent:
        agent_profile = action_to_agent[action]
        use_claude = kwargs.get("use_claude", False)
        use_chatgpt = kwargs.get("use_chatgpt", False)

        success, detected_base_url, _server_version = initialize_lemonade_for_agent(
            agent=agent_profile,
            auto_start=True,
            skip_if_external=True,
            use_claude=use_claude,
            use_chatgpt=use_chatgpt,
        )
        if not success:
            sys.exit(1)

        # Use detected base_url if not explicitly provided
        if lemonade_base_url is None:
            lemonade_base_url = detected_base_url
            kwargs["base_url"] = detected_base_url

    # Create client for actions that use GaiaCliClient (not chat - it uses ChatAgent)
    client = None
    if action in ["prompt", "stats"]:
        # Filter out audio-related parameters that are no longer part of GaiaCliClient
        audio_params = {
            "whisper_model_size",
            "audio_device_index",
            "silence_threshold",
            "no_tts",
        }
        excluded_params = {"message", "stats", "assistant_name"} | audio_params
        client_params = {k: v for k, v in kwargs.items() if k not in excluded_params}
        client = GaiaCliClient(**client_params)

    if action == "prompt":
        if not kwargs.get("message"):
            log.error("Message is required for prompt action.")
            print("❌ Error: Message is required for prompt action.")
            sys.exit(1)
        response = ""
        async for chunk in client.prompt(kwargs["message"]):
            response += chunk
        if kwargs.get("show_stats", False):
            stats = client.get_stats()
            if stats:
                return {"response": response, "stats": stats}
        return {"response": response}
    elif action == "chat":
        # Use Chat Agent with RAG, file search, and shell execution
        from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
        from gaia.agents.chat.app import interactive_mode

        try:
            # Use silent mode when debug is off to hide intermediate processing
            # SilentConsole will still stream the final answer
            query = kwargs.get("query")
            debug_mode = kwargs.get("debug", False)
            use_silent_mode = not debug_mode  # Hide processing steps unless debugging

            # Create configuration with CLI values
            config = ChatAgentConfig(
                use_claude=kwargs.get("use_claude", False),
                use_chatgpt=kwargs.get("use_chatgpt", False),
                claude_model=kwargs.get("claude_model", "claude-sonnet-4-20250514"),
                base_url=kwargs.get(
                    "base_url",
                    os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL),
                ),
                model_id=kwargs.get("model", None),
                max_steps=kwargs.get("max_steps", 100),
                streaming=kwargs.get("stream", False),
                show_prompts=kwargs.get("show_prompts", False),
                show_stats=kwargs.get("show_stats", False),
                silent_mode=use_silent_mode,
                debug=debug_mode,
                rag_documents=kwargs.get("index", []),
                watch_directories=kwargs.get("watch", []),
                chunk_size=kwargs.get("chunk_size", 500),
                max_chunks=kwargs.get("max_chunks", 3),
                allowed_paths=kwargs.get("allowed_paths", None),
            )

            # Create Chat Agent with configuration
            agent = ChatAgent(config)

            # Create initial session if not loading one
            if not agent.current_session:
                agent.current_session = agent.session_manager.create_session()
                log.debug(f"Created new session: {agent.current_session.session_id}")

            # List tools if requested
            if kwargs.get("list_tools", False):
                agent.list_tools(verbose=True)
                return

            # Single query mode
            query = kwargs.get("query")
            if query:
                result = agent.process_query(query, trace=kwargs.get("trace", False))
                # The console (either AgentConsole or SilentConsole) already handles printing

                if kwargs.get("show_stats", False) and result.get("duration"):
                    agent.console.display_stats(result)

                return 0 if result["status"] == "success" else 1

            # Interactive mode
            interactive_mode(agent)
            return

        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            return
        except Exception as e:
            log.error(f"Error in chat: {e}", exc_info=True)
            print(f"❌ Error: {e}")
            return
        finally:
            # Cleanup
            try:
                if "agent" in locals():
                    agent.stop_watching()
            except Exception:  # pylint: disable=broad-except
                pass
    elif action == "talk":
        # Use TalkSDK for voice functionality
        from gaia.talk.sdk import TalkConfig, TalkSDK

        # Create SDK configuration from CLI arguments
        index_file = kwargs.get("index")
        rag_documents = [index_file] if index_file else None

        config = TalkConfig(
            whisper_model_size=kwargs.get("whisper_model_size", "base"),
            audio_device_index=kwargs.get(
                "audio_device_index", None
            ),  # Use default device if not specified
            silence_threshold=kwargs.get("silence_threshold", 0.5),
            enable_tts=not kwargs.get("no_tts", False),
            system_prompt=None,  # Could add this as a parameter later
            show_stats=kwargs.get("stats", False),
            logging_level=kwargs.get(
                "logging_level", "INFO"
            ),  # Back to INFO now that issues are fixed
            # RAG configuration
            rag_documents=rag_documents,
        )

        # Create SDK instance
        talk_sdk = TalkSDK(config)

        # Start voice chat session
        print("Starting voice chat...")
        print("Say 'stop' to quit or press Ctrl+C")

        await talk_sdk.start_voice_session()
        log.info("Voice chat session ended.")
        return
    elif action == "stats":
        stats = client.get_stats()
        if stats:
            return {"stats": stats}
        log.error("No stats available.")
        print("❌ Error: No stats available.")
        sys.exit(1)
    else:
        log.error(f"Unknown action specified: {action}")
        print(f"❌ Error: Unknown action specified: {action}")
        sys.exit(1)


def run_cli(action, **kwargs):
    return asyncio.run(async_main(action, **kwargs))


def main():
    import argparse

    # Create the main parser
    parser = argparse.ArgumentParser(
        description=f"Gaia CLI - Interact with Gaia AI agents. \n{version}",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Get logger instance
    log = get_logger(__name__)

    # Add version argument
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"{version}",
        help="Show program's version number and exit",
    )

    # Create a parent parser for common arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--logging-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )

    # Generic LLM backend options (available to all agents)
    parent_parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Use Claude API instead of local Lemonade server",
    )
    parent_parser.add_argument(
        "--use-chatgpt",
        action="store_true",
        help="Use ChatGPT/OpenAI API instead of local Lemonade server",
    )
    parent_parser.add_argument(
        "--claude-model",
        default="claude-sonnet-4-20250514",
        help="Claude model to use when --use-claude is specified (default: claude-sonnet-4-20250514)",
    )
    parent_parser.add_argument(
        "--base-url",
        default=None,
        help=f"Lemonade LLM server base URL (default: from LEMONADE_BASE_URL env or {DEFAULT_LEMONADE_URL})",
    )
    parent_parser.add_argument(
        "--model",
        default=None,
        help="Model ID to use (default: auto-selected by each agent)",
    )
    parent_parser.add_argument(
        "--trace",
        action="store_true",
        help="Save detailed JSON trace of agent execution (default: disabled)",
    )
    parent_parser.add_argument(
        "--max-steps",
        type=int,
        default=100,
        help="Maximum conversation steps (default: 100)",
    )
    parent_parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List available tools and exit",
    )
    parent_parser.add_argument(
        "--stats",
        "--show-stats",
        action="store_true",
        dest="show_stats",
        help="Show performance statistics",
    )
    parent_parser.add_argument(
        "--stream",
        action="store_true",
        help="Enable real-time streaming of LLM responses (shows raw JSON)",
    )
    parent_parser.add_argument(
        "--no-lemonade-check",
        action="store_true",
        help="Skip Lemonade server check (for CI/testing without Lemonade)",
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Core Gaia CLI commands - add parent_parser to each subcommand
    # Note: start and stop commands removed since CLI assumes Lemonade is running

    # Add prompt-specific options
    prompt_parser = subparsers.add_parser(
        "prompt", help="Send a single prompt to Gaia", parents=[parent_parser]
    )
    prompt_parser.add_argument(
        "message",
        help="Message to send to Gaia",
    )
    prompt_parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum number of tokens to generate (default: 512)",
    )

    chat_parser = subparsers.add_parser(
        "chat",
        help="Interactive chat with RAG, file search, and shell execution",
        parents=[parent_parser],
    )
    chat_parser.add_argument(
        "--query",
        "-q",
        type=str,
        help="Single query to execute (defaults to interactive mode if not provided)",
    )

    # Agent configuration
    chat_parser.add_argument(
        "--show-prompts", action="store_true", help="Display prompts sent to LLM"
    )
    chat_parser.add_argument("--debug", action="store_true", help="Enable debug output")

    # RAG configuration
    chat_parser.add_argument(
        "--index",
        "-i",
        nargs="+",
        metavar="FILE",
        help="PDF document(s) to index for RAG (space-separated)",
    )
    chat_parser.add_argument(
        "--watch", "-w", nargs="+", help="Directories to monitor for new documents"
    )
    chat_parser.add_argument(
        "--chunk-size", type=int, default=500, help="Document chunk size (default: 500)"
    )
    chat_parser.add_argument(
        "--max-chunks",
        type=int,
        default=3,
        help="Maximum chunks to retrieve (default: 3)",
    )
    chat_parser.add_argument(
        "--allowed-paths",
        nargs="+",
        help="Allowed directory paths for file operations (default: current directory)",
    )

    talk_parser = subparsers.add_parser(
        "talk", help="Start voice conversation with Gaia", parents=[parent_parser]
    )
    talk_parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum number of tokens to generate (default: 512)",
    )
    talk_parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable text-to-speech in voice chat mode",
    )
    talk_parser.add_argument(
        "--audio-device-index",
        type=int,
        default=None,
        help="Index of the audio input device to use (default: auto-detect)",
    )
    talk_parser.add_argument(
        "--whisper-model-size",
        type=str,
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Size of the Whisper model to use (default: base)",
    )
    talk_parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.5,
        help="Silence threshold in seconds (default: 0.5)",
    )

    # RAG configuration for talk (document Q&A with voice)
    talk_parser.add_argument(
        "--index", "-i", type=str, help="Index a PDF document for voice Q&A"
    )
    talk_parser.set_defaults(action="talk")

    # Add summarize command
    summarize_parser = subparsers.add_parser(
        "summarize",
        help="Summarize meeting transcripts and emails",
        parents=[parent_parser],
    )
    summarize_parser.add_argument(
        "-i",
        "--input",
        help="Input file or directory path (required unless using --list-configs)",
    )
    summarize_parser.add_argument(
        "-o",
        "--output",
        help="Output file/directory path (auto-adjusted based on format)",
    )
    summarize_parser.add_argument(
        "-t",
        "--type",
        choices=["transcript", "email", "auto"],
        default="auto",
        help="Input type (default: auto-detect)",
    )
    summarize_parser.add_argument(
        "-f",
        "--format",
        choices=["json", "pdf", "email", "both"],
        default="json",
        help="Output format (default: json). 'both' generates json and pdf",
    )
    summarize_parser.add_argument(
        "--styles",
        nargs="+",
        choices=[
            "brief",
            "detailed",
            "bullets",
            "executive",
            "participants",
            "action_items",
            "all",
        ],
        default=["executive", "participants", "action_items"],
        help="Summary style(s) to generate (default: executive participants action_items)",
    )
    summarize_parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Maximum tokens for summary (default: 1024)",
    )
    summarize_parser.add_argument(
        "--email-to", help="Email recipients (comma-separated) for email output format"
    )
    summarize_parser.add_argument(
        "--email-subject", help="Email subject line (default: auto-generated)"
    )
    summarize_parser.add_argument("--email-cc", help="CC recipients (comma-separated)")
    summarize_parser.add_argument(
        "--config", help="Use predefined configuration file from configs/ directory"
    )
    summarize_parser.add_argument(
        "--list-configs",
        action="store_true",
        help="List all available configuration templates",
    )
    summarize_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal output, suppress progress indicators",
    )
    summarize_parser.add_argument(
        "--verbose", action="store_true", help="Detailed output with debug information"
    )
    summarize_parser.add_argument(
        "--combined-prompt",
        action="store_true",
        help="Combine multiple styles into single LLM call (experimental - may reduce quality)",
    )
    summarize_parser.add_argument(
        "--no-viewer",
        action="store_true",
        help="Don't automatically open HTML viewer for JSON output",
    )

    # Add Blender agent command
    blender_parser = subparsers.add_parser(
        "blender",
        help="Blender 3D scene creation and modification",
        parents=[parent_parser],
    )
    blender_parser.add_argument(
        "--example",
        type=int,
        choices=range(1, 7),
        help="Run a specific example (1-6), if not specified run interactive mode",
    )
    blender_parser.add_argument(
        "--steps", type=int, default=5, help="Maximum number of steps per query"
    )
    blender_parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Directory to save output files",
    )
    blender_parser.add_argument(
        "--query", type=str, help="Custom query to run instead of examples"
    )
    blender_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enable interactive mode to continuously input queries",
    )
    blender_parser.add_argument(
        "--debug-prompts",
        action="store_true",
        default=False,
        help="Enable debug prompts",
    )
    blender_parser.add_argument(
        "--print-result",
        action="store_true",
        default=False,
        help="Print results to console",
    )
    blender_parser.add_argument(
        "--mcp-port",
        type=int,
        default=9876,
        help="Port for the Blender MCP server (default: 9876)",
    )

    # Add Jira app command
    jira_parser = subparsers.add_parser(
        "jira",
        help="Natural language interface for Atlassian tools (Jira, Confluence, Compass)",
        parents=[parent_parser],
    )
    jira_parser.add_argument(
        "command",
        nargs="?",
        help="Natural language command to execute (e.g., 'Create a bug report for login issue')",
    )
    jira_parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run in interactive mode for continuous commands",
    )
    jira_parser.add_argument(
        "--mcp-host",
        default="localhost",
        help="MCP bridge host (default: localhost)",
    )
    jira_parser.add_argument(
        "--mcp-port",
        type=int,
        default=8765,
        help="MCP bridge port (default: 8765)",
    )
    jira_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    jira_parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    # Add Code agent command
    code_parser = subparsers.add_parser(
        "code",
        help="Python code assistant with analysis, generation, and linting",
        parents=[parent_parser],
    )
    code_parser.add_argument(
        "query",
        nargs="?",
        help="Code operation query (e.g., 'Generate a function to sort a list')",
    )
    code_parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode for multiple queries",
    )
    code_parser.add_argument(
        "--silent",
        "-s",
        action="store_true",
        help="Silent mode - suppress console output, return JSON only",
    )
    code_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    code_parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Display prompts sent to LLM",
    )
    # Note: --use-claude, --use-chatgpt, --max-steps, --list-tools, --stream, --stats inherited from parent_parser
    code_parser.add_argument(
        "--step-through",
        action="store_true",
        help="Enable step-through debugging mode (pause at each agent step)",
    )
    code_parser.add_argument(
        "--path",
        "-p",
        type=str,
        default=None,
        help="Project directory path. Creates directory if it doesn't exist. All operations will use this as working directory.",
    )
    code_parser.set_defaults(action="code")

    # Add Docker app command
    docker_parser = subparsers.add_parser(
        "docker",
        help="Natural language interface for Docker containerization",
        parents=[parent_parser],
    )
    docker_parser.add_argument(
        "command",
        help="Natural language command to execute (e.g., 'Create a Dockerfile for my Flask app')",
    )
    docker_parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help="Directory to analyze/containerize (default: current directory)",
    )
    docker_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    docker_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    docker_parser.set_defaults(action="docker")

    # Add API server command
    api_parser = subparsers.add_parser(
        "api",
        help="Start OpenAI-compatible API server for VSCode integration",
        parents=[parent_parser],
    )
    api_parser.add_argument(
        "subcommand",
        choices=["start", "stop", "status"],
        help="API server command (start, stop, or status)",
    )
    api_parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind API server (default: localhost)",
    )
    api_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for API server (default: 8080)",
    )
    api_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    api_parser.add_argument(
        "--show-prompts",
        action="store_true",
        help="Display prompts sent to LLM",
    )
    api_parser.add_argument(
        "--streaming",
        action="store_true",
        help="Enable real-time streaming of LLM responses",
    )
    api_parser.add_argument(
        "--step-through",
        action="store_true",
        help="Enable step-through debugging mode (pause at each agent step)",
    )
    api_parser.set_defaults(action="api")

    # Add model pull command
    pull_parser = subparsers.add_parser(
        "pull",
        help="Download/install a model from the Lemonade Server registry",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Pull a registered model
  gaia pull Qwen3-0.6B-GGUF

  # Pull and register a custom model from HuggingFace
  gaia pull user.Custom-Model-GGUF --checkpoint unsloth/Custom-Model-GGUF:Q4_K_M --recipe llamacpp

  # Pull a reasoning model
  gaia pull user.DeepSeek-GGUF --checkpoint unsloth/DeepSeek-R1-GGUF --recipe llamacpp --reasoning

  # Pull a vision model with mmproj
  gaia pull user.Vision-Model --checkpoint model/vision:Q4 --recipe llamacpp --vision --mmproj mmproj.gguf
        """,
    )
    pull_parser.add_argument(
        "model_name",
        help="Name of the model to pull (use 'user.' prefix for custom models)",
    )
    pull_parser.add_argument(
        "--checkpoint",
        help="HuggingFace checkpoint for custom models (e.g., unsloth/Model-GGUF:Q4_K_M)",
    )
    pull_parser.add_argument(
        "--recipe",
        help="Lemonade recipe for custom models (e.g., llamacpp, oga-cpu)",
    )
    pull_parser.add_argument(
        "--reasoning",
        action="store_true",
        help="Mark model as a reasoning model (like DeepSeek)",
    )
    pull_parser.add_argument(
        "--vision",
        action="store_true",
        help="Mark model as having vision capabilities",
    )
    pull_parser.add_argument(
        "--embedding",
        action="store_true",
        help="Mark model as an embedding model",
    )
    pull_parser.add_argument(
        "--reranking",
        action="store_true",
        help="Mark model as a reranking model",
    )
    pull_parser.add_argument(
        "--mmproj",
        help="Multimodal projector file for vision models",
    )
    pull_parser.add_argument(
        "--timeout",
        type=int,
        default=1200,
        help="Timeout in seconds for model download (default: 1200)",
    )
    pull_parser.add_argument(
        "--host",
        default="localhost",
        help="Lemonade server host (default: localhost)",
    )
    pull_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Lemonade server port (default: 8000)",
    )
    pull_parser.set_defaults(action="pull")

    # Add model download command
    download_parser = subparsers.add_parser(
        "download",
        help="Download all models required for GAIA agents",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download all models for all agents
  gaia download

  # Download models for chat agent only
  gaia download --agent chat

  # Download models for code agent
  gaia download --agent code

  # List available agents and their required models
  gaia download --list

  # Delete all downloaded GAIA models (free up disk space)
  gaia download --clear-cache

Available agents: chat, code, talk, rag, blender, jira, docker, vlm, minimal, mcp
        """,
    )
    download_parser.add_argument(
        "--agent",
        default="all",
        help="Agent to download models for (default: all)",
    )
    download_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_models",
        help="List required models without downloading",
    )
    download_parser.add_argument(
        "--clear-cache",
        action="store_true",
        dest="clear_cache",
        help="Delete all downloaded GAIA models to free up disk space",
    )
    download_parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per model in seconds (default: 1800)",
    )
    download_parser.add_argument(
        "--host",
        default="localhost",
        help="Lemonade server host (default: localhost)",
    )
    download_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Lemonade server port (default: 8000)",
    )
    download_parser.set_defaults(action="download")

    subparsers.add_parser(
        "stats",
        help="Show Gaia statistics from the most recent run.",
        parents=[parent_parser],
    )

    # Add utility commands to main parser instead of creating a separate parser
    test_parser = subparsers.add_parser(
        "test", help="Run various tests", parents=[parent_parser]
    )
    test_parser.add_argument(
        "--test-type",
        required=True,
        choices=[
            "tts-preprocessing",
            "tts-streaming",
            "tts-audio-file",
            "asr-file-transcription",
            "asr-microphone",
            "asr-list-audio-devices",
        ],
        help="Type of test to run",
    )
    test_parser.add_argument(
        "--test-text",
        help="Text to use for TTS tests",
    )
    test_parser.add_argument(
        "--input-audio-file",
        help="Input audio file path for ASR file transcription test",
    )
    test_parser.add_argument(
        "--output-audio-file",
        default="output.wav",
        help="Output file path for TTS audio file test (default: output.wav)",
    )
    test_parser.add_argument(
        "--recording-duration",
        type=int,
        default=10,
        help="Recording duration in seconds for ASR microphone test (default: 10)",
    )
    test_parser.add_argument(
        "--whisper-model-size",
        type=str,
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Size of the Whisper model to use (default: base)",
    )
    test_parser.add_argument(
        "--audio-device-index",
        type=int,
        default=1,
        help="Index of audio input device (optional)",
    )

    # Add YouTube-specific options
    yt_parser = subparsers.add_parser(
        "youtube", help="YouTube utilities", parents=[parent_parser]
    )
    yt_parser.add_argument(
        "--download-transcript",
        metavar="URL",
        help="Download transcript from a YouTube URL",
    )
    yt_parser.add_argument(
        "--output-path",
        help="Output file path for transcript (optional, default: transcript_<video_id>.txt)",
    )

    # Add new subparser for kill command
    kill_parser = subparsers.add_parser(
        "kill", help="Kill process running on specific port", parents=[parent_parser]
    )
    kill_parser.add_argument(
        "--port", type=int, required=True, help="Port number to kill process on"
    )

    # Add LLM app command
    llm_parser = subparsers.add_parser(
        "llm",
        help="Run simple LLM queries using LLMClient wrapper",
        parents=[parent_parser],
    )
    llm_parser.add_argument("query", help="The query/prompt to send to the LLM")
    llm_parser.add_argument(
        "--max-tokens",
        type=int,
        default=512,
        help="Maximum tokens to generate (default: 512)",
    )
    llm_parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming the response (streaming is enabled by default)",
    )

    # Add groundtruth generation subparser
    gt_parser = subparsers.add_parser(
        "groundtruth",
        help="Generate ground truth data for various evaluation use cases",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process a single file for RAG evaluation (default)
  gaia groundtruth -f ./data/html/blender/introduction.html

  # Process a transcript for summary generation
  gaia groundtruth -f ./data/transcripts/meeting.txt --use-case summarization

  # Process a transcript for Q&A generation
  gaia groundtruth -f ./data/transcripts/meeting.txt --use-case qa

  # Process an email for business email analysis
  gaia groundtruth -f ./data/emails/project_update.txt --use-case email

  # Process all HTML files in a directory for RAG
  gaia groundtruth -d ./data/html/blender

  # Process transcript files for summarization
  gaia groundtruth -d ./data/transcripts -p "*.txt" --use-case summarization

  # Process transcript files for Q&A generation
  gaia groundtruth -d ./data/transcripts -p "*.txt" --use-case qa

  # Process email files for email processing evaluation
  gaia groundtruth -d ./data/emails -p "*.txt" --use-case email

  # Process with custom output directory
  gaia groundtruth -f ./data/html/intro.html -o ./output/gt

  # Use custom Claude model
  gaia groundtruth -f ./data/doc.html --claude-model claude-3-opus-20240229

  # Generate 10 Q&A pairs per document (RAG only)
  gaia groundtruth -d ./data/html/blender --num-samples 10

  # Force regeneration of all ground truth files
  gaia groundtruth -d ./data/html/blender --force
        """,
    )

    # Input source (mutually exclusive)
    gt_input_group = gt_parser.add_mutually_exclusive_group(required=False)
    gt_input_group.add_argument(
        "-f", "--file", type=str, help="Path to a single document file to process"
    )
    gt_input_group.add_argument(
        "-d",
        "--directory",
        type=str,
        default=f"./{DEFAULT_TEST_DATA_DIR}",
        help=f"Directory containing documents to process (results will be consolidated into a single JSON file) (default: ./{DEFAULT_TEST_DATA_DIR})",
    )

    # Optional arguments for groundtruth
    gt_parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=f"./{DEFAULT_GROUNDTRUTH_DIR}",
        help=f"Output directory for generated ground truth files (default: ./{DEFAULT_GROUNDTRUTH_DIR})",
    )
    gt_parser.add_argument(
        "-p",
        "--pattern",
        type=str,
        default="*",
        help="File pattern to match when processing directory (default: *)",
    )
    gt_parser.add_argument(
        "-u",
        "--use-case",
        type=str,
        choices=["rag", "summarization", "qa", "email"],
        default="summarization",
        help="Use case for ground truth generation: 'rag' for document Q&A pairs, 'summarization' for transcript summaries, 'qa' for transcript Q&A pairs, 'email' for email processing analysis (default: summarization)",
    )
    gt_parser.add_argument(
        "--max-tokens",
        type=int,
        default=4096,
        help="Maximum tokens for Claude responses (default: 4096)",
    )
    gt_parser.add_argument(
        "--no-save-text",
        action="store_true",
        help="Don't save extracted text for HTML files",
    )
    gt_parser.add_argument(
        "--custom-prompt",
        type=str,
        help="Path to a file containing a custom prompt for Claude",
    )
    gt_parser.add_argument(
        "--num-samples",
        type=int,
        default=5,
        help="Number of Q&A pairs to generate per document (RAG use case only, default: 5)",
    )
    gt_parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of all ground truth files, even if they already exist (default: skip existing)",
    )

    # Add new subparser for creating evaluation templates
    template_parser = subparsers.add_parser(
        "create-template",
        help="Create a template results file from ground truth data for manual RAG evaluation",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create template from ground truth file
  gaia create-template -f ./output/groundtruth/introduction.groundtruth.json

  # Create template with custom output path
  gaia create-template -f ./output/groundtruth/doc.groundtruth.json -o ./templates/

  # Create template with custom similarity threshold
  gaia create-template -f ./output/groundtruth/doc.groundtruth.json --threshold 0.8
        """,
    )

    template_parser.add_argument(
        "-f",
        "--groundtruth-file",
        type=str,
        required=True,
        help="Path to the ground truth JSON file",
    )
    template_parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="./output/templates",
        help="Output directory for template file (default: ./output/templates)",
    )
    template_parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Similarity threshold for evaluation (default: 0.7)",
    )

    # Add new subparser for evaluation
    eval_parser = subparsers.add_parser(
        "eval",
        help="Evaluate experiment results (summarization, Q&A, RAG, etc.)",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Evaluate single experiment file
  gaia eval -f ./output/experiments/emails/customer_support_email.Claude-Sonnet-Basic-Summary.experiment.json

  # Evaluate all experiment files in a directory (hierarchical structure supported)
  gaia eval -d ./output/experiments -o ./output/evaluations

  # Evaluate with custom output directory
  gaia eval -f ./output/experiments/my_experiment.experiment.json -o ./output/evaluations

  # Evaluate summarization results with separate groundtruth file
  gaia eval -f ./output/experiments/meetings/design_review_meeting.Claude-Sonnet-Basic-Summary.experiment.json -g ./output/groundtruth/meetings/design_review_meeting.summarization.groundtruth.json

  # Evaluate directory with specific Claude model
  gaia eval -d ./output/experiments --claude-model claude-3-opus-20240229

  # Evaluate and display summary only (no detailed report file)
  gaia eval -d ./output/experiments --summary-only
        """,
    )

    # Create mutually exclusive group for file vs directory input
    file_group = eval_parser.add_mutually_exclusive_group(required=False)
    file_group.add_argument(
        "-f",
        "--results-file",
        type=str,
        help="Path to the RAG results JSON file (template or results)",
    )
    file_group.add_argument(
        "-d",
        "--directory",
        type=str,
        default=f"./{DEFAULT_EXPERIMENTS_DIR}",
        help=f"Path to directory containing JSON experiment files to process (default: ./{DEFAULT_EXPERIMENTS_DIR})",
    )
    eval_parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=f"./{DEFAULT_EVALUATIONS_DIR}",
        help=f"Output directory for evaluation report (default: ./{DEFAULT_EVALUATIONS_DIR})",
    )
    eval_parser.add_argument(
        "-g",
        "--groundtruth",
        type=str,
        help="Path to ground truth file for comparison (especially useful for summarization evaluation)",
    )
    eval_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only display summary, don't save detailed evaluation report",
    )
    eval_parser.add_argument(
        "--report-only",
        action="store_true",
        help="Generate consolidated report from existing evaluation data without re-running evaluations",
    )
    eval_parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of all evaluations, even if they already exist (default: skip existing)",
    )
    eval_parser.add_argument(
        "--regenerate-report",
        action="store_true",
        help="Regenerate consolidated report only (without re-running evaluations)",
    )
    eval_parser.add_argument(
        "--incremental-update",
        action="store_true",
        help="Update consolidated report incrementally with new evaluations only",
    )

    # Add new subparser for generating summary reports from evaluation directories
    report_parser = subparsers.add_parser(
        "report",
        help="Generate summary report from evaluation results directory",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate report from evaluation directory
  gaia report -d ./output/eval

  # Generate report with custom output filename
  gaia report -d ./output/eval -o Model_Comparison_Report.md

  # Generate report and display summary only
  gaia report -d ./output/eval --summary-only
        """,
    )

    report_parser.add_argument(
        "-d",
        "--eval-dir",
        type=str,
        default=f"./{DEFAULT_EVALUATIONS_DIR}",
        help=f"Directory containing .eval.json files to analyze (default: ./{DEFAULT_EVALUATIONS_DIR})",
    )
    report_parser.add_argument(
        "-o",
        "--output-file",
        type=str,
        default="LLM_Evaluation_Report.md",
        help="Output filename for the markdown report (default: LLM_Evaluation_Report.md)",
    )
    report_parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only display summary to console, don't save report file",
    )

    # Add new subparser for launching the evaluation results visualizer
    visualize_parser = subparsers.add_parser(
        "visualize",
        help="Launch web-based evaluation results visualizer",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Launch visualizer with default settings
  gaia visualize

  # Launch with custom data directories
  gaia visualize --experiments-dir ./output/my_experiments --evaluations-dir ./output/my_evaluations

  # Launch on custom port without opening browser
  gaia visualize --port 8080 --no-browser

  # Launch with specific workspace directory
  gaia visualize --workspace ./output/evaluations_workspace
  
  # Visualize agent output files
  gaia visualize --agent-outputs-dir ./agent_outputs
  
  # Visualize a single agent output file
  gaia visualize --agent-output-file ./agent_output_20250827_135905.json
  
  # Use GAIA_WORKSPACE environment variable for workspace root
  export GAIA_WORKSPACE=./my_workspace
  gaia visualize  # Will use ./my_workspace as base for all directories
        """,
    )

    visualize_parser.add_argument(
        "--port",
        type=int,
        default=3000,
        help="Port to run the visualizer server on (default: 3000)",
    )
    visualize_parser.add_argument(
        "--experiments-dir",
        type=str,
        help=f"Directory containing experiment JSON files (default: ./{DEFAULT_EXPERIMENTS_DIR})",
    )
    visualize_parser.add_argument(
        "--evaluations-dir",
        type=str,
        help=f"Directory containing evaluation JSON files (default: ./{DEFAULT_EVALUATIONS_DIR})",
    )
    visualize_parser.add_argument(
        "--workspace",
        type=str,
        help="Base workspace directory (default: current directory)",
    )
    visualize_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't automatically open browser after starting server",
    )
    visualize_parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Host address for the visualizer server (default: localhost)",
    )
    visualize_parser.add_argument(
        "--test-data-dir",
        type=str,
        help=f"Directory containing test data files (default: ./{DEFAULT_TEST_DATA_DIR})",
    )
    visualize_parser.add_argument(
        "--groundtruth-dir",
        type=str,
        help=f"Directory containing groundtruth files (default: ./{DEFAULT_GROUNDTRUTH_DIR})",
    )
    visualize_parser.add_argument(
        "--agent-outputs-dir",
        type=str,
        help="Directory containing agent output JSON files (default: current directory)",
    )
    visualize_parser.add_argument(
        "--agent-output-file",
        type=str,
        help="Single agent output JSON file to visualize",
    )

    # Add new subparser for generating synthetic test data
    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate synthetic test data for evaluation (meeting transcripts or business emails)",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate meeting transcripts
  gaia generate --meeting-transcript -o ./output/meetings
  gaia generate --meeting-transcript -o ./output/meetings --target-tokens 3000 --count-per-type 3
  gaia generate --meeting-transcript -o ./output/meetings --meeting-types standup planning

  # Generate business emails
  gaia generate --email -o ./output/emails
  gaia generate --email -o ./output/emails --target-tokens 1500 --count-per-type 3
  gaia generate --email -o ./output/emails --email-types project_update sales_outreach
        """,
    )

    # Add new subparser for batch experiment runner
    batch_exp_parser = subparsers.add_parser(
        "batch-experiment",
        help="Run batch experiments with different LLM configurations on transcript data",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create sample configuration file
  gaia batch-experiment --create-sample-config experiment_config.json

  # Create configuration from groundtruth metadata
  gaia batch-experiment --create-config-from-groundtruth ./output/groundtruth/meeting.qa.groundtruth.json

  # Run batch experiments on transcript directory
  gaia batch-experiment -c experiment_config.json -i ./transcripts -o ./output/experiments

  # Run batch experiments on transcript directory with custom queries from groundtruth
  gaia batch-experiment -c experiment_config.json -i ./transcripts -q ./output/groundtruth/meeting.qa.groundtruth.json -o ./output/experiments

  # Run batch experiments on single transcript file
  gaia batch-experiment -c experiment_config.json -i ./meeting_transcript.txt -o ./output/experiments

  # Run batch experiments on groundtruth file
  gaia batch-experiment -c experiment_config.json -i ./output/groundtruth/transcript.qa.groundtruth.json -o ./output/experiments

  # Run batch experiments on consolidated groundtruth file
  gaia batch-experiment -c experiment_config.json -i ./output/groundtruth/consolidated_summarization_groundtruth.json -o ./output/experiments

  # Run with custom delay between requests to avoid rate limiting
  gaia batch-experiment -c experiment_config.json -i ./transcripts -o ./output/experiments --delay 2.0

  # Process multiple experiment results
  gaia eval -f ./output/experiments/Claude-Sonnet-Standard.experiment.json
  gaia report -d ./output/experiments
        """,
    )

    # Add mutually exclusive group for generation type
    generate_type_group = generate_parser.add_mutually_exclusive_group(required=True)
    generate_type_group.add_argument(
        "--meeting-transcript",
        action="store_true",
        help="Generate meeting transcripts for testing transcript summarization",
    )
    generate_type_group.add_argument(
        "--email",
        action="store_true",
        help="Generate business emails for testing email processing and summarization",
    )

    # Add common arguments for generate command
    generate_parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=None,  # Will be set to ./output/test_data
        help=f"Output directory for generated files (default: ./{DEFAULT_TEST_DATA_DIR})",
    )
    generate_parser.add_argument(
        "--target-tokens",
        type=int,
        help="Target token count per generated item (default: 1000 for transcripts, 800 for emails)",
    )
    generate_parser.add_argument(
        "--count-per-type",
        type=int,
        default=1,
        help="Number items to generate per type (default: 1)",
    )
    # Note: --claude-model inherited from parent_parser

    # Add type-specific arguments
    generate_parser.add_argument(
        "--meeting-types",
        nargs="+",
        choices=[
            "standup",
            "planning",
            "client_call",
            "design_review",
            "performance_review",
            "all_hands",
            "budget_planning",
            "product_roadmap",
        ],
        help="Specific meeting types to generate (only used with --meeting-transcript, default: all types)",
    )
    generate_parser.add_argument(
        "--email-types",
        nargs="+",
        choices=[
            "project_update",
            "meeting_request",
            "customer_support",
            "sales_outreach",
            "internal_announcement",
            "technical_discussion",
            "vendor_communication",
            "performance_feedback",
        ],
        help="Specific email types to generate (only used with --email, default: all types)",
    )

    # Add arguments for batch experiment command
    batch_exp_parser.add_argument(
        "-c", "--config", type=str, help="Path to experiment configuration JSON file"
    )
    batch_exp_parser.add_argument(
        "-i",
        "--input",
        type=str,
        default=f"./{DEFAULT_TEST_DATA_DIR}",
        help=f"Path to input data: transcript file, directory of transcripts, or groundtruth JSON file (default: ./{DEFAULT_TEST_DATA_DIR})",
    )
    batch_exp_parser.add_argument(
        "-q",
        "--queries-source",
        type=str,
        help="Path to groundtruth JSON file to extract queries from (for QA experiments on raw transcripts)",
    )
    batch_exp_parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default=f"./{DEFAULT_EXPERIMENTS_DIR}",
        help=f"Output directory for experiment results (default: ./{DEFAULT_EXPERIMENTS_DIR})",
    )
    batch_exp_parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay in seconds between requests to avoid rate limiting (default: 1.0)",
    )
    batch_exp_parser.add_argument(
        "--create-sample-config",
        type=str,
        help="Create a sample configuration file at the specified path",
    )
    batch_exp_parser.add_argument(
        "--create-config-from-groundtruth",
        type=str,
        help="Create configuration from groundtruth file metadata (provide groundtruth file path)",
    )
    batch_exp_parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of all experiments, even if they already exist (default: skip existing)",
    )

    # Add MCP (Model Context Protocol) command
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Start or manage MCP (Model Context Protocol) bridge server",
        parents=[parent_parser],
    )
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="mcp_action", help="MCP action to perform"
    )

    # MCP start command
    mcp_start_parser = mcp_subparsers.add_parser(
        "start", help="Start the MCP bridge server", parents=[parent_parser]
    )
    mcp_start_parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind the server to (default: localhost)",
    )
    mcp_start_parser.add_argument(
        "--port", type=int, default=8765, help="Port to listen on (default: 8765)"
    )
    # Note: --base-url is inherited from parent_parser
    mcp_start_parser.add_argument(
        "--auth-token", help="Optional authentication token for secure connections"
    )
    mcp_start_parser.add_argument(
        "--no-streaming", action="store_true", help="Disable streaming responses"
    )
    mcp_start_parser.add_argument(
        "--background", action="store_true", help="Run MCP bridge in background mode"
    )
    mcp_start_parser.add_argument(
        "--log-file",
        default="gaia.mcp.log",
        help="Log file path for background mode (default: gaia.mcp.log)",
    )
    mcp_start_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging for all HTTP requests",
    )
    mcp_start_parser.add_argument(
        "--ctx-size",
        type=int,
        default=32768,
        help="Context size for Lemonade Server (default: 32768 for coding)",
    )

    # MCP status command
    mcp_status_parser = mcp_subparsers.add_parser(
        "status", help="Check MCP server status"
    )
    mcp_status_parser.add_argument(
        "--host", default="localhost", help="Host to check (default: localhost)"
    )
    mcp_status_parser.add_argument(
        "--port", type=int, default=8765, help="Port to check (default: 8765)"
    )

    # MCP stop command
    _ = mcp_subparsers.add_parser("stop", help="Stop background MCP bridge server")

    # MCP test command
    mcp_test_parser = mcp_subparsers.add_parser(
        "test", help="Test MCP bridge functionality"
    )
    mcp_test_parser.add_argument(
        "--host", default="localhost", help="Host to connect to (default: localhost)"
    )
    mcp_test_parser.add_argument(
        "--port", type=int, default=8765, help="Port to connect to (default: 8765)"
    )
    mcp_test_parser.add_argument(
        "--query", default="Hello, GAIA!", help="Test query to send"
    )
    mcp_test_parser.add_argument(
        "--tool", default="gaia.chat", help="Tool to test (default: gaia.chat)"
    )

    # MCP agent command
    mcp_agent_parser = mcp_subparsers.add_parser(
        "agent", help="Test MCP orchestrator agent functionality"
    )
    mcp_agent_parser.add_argument(
        "--host", default="localhost", help="Host to connect to (default: localhost)"
    )
    mcp_agent_parser.add_argument(
        "--port", type=int, default=8765, help="Port to connect to (default: 8765)"
    )
    mcp_agent_parser.add_argument(
        "request", help="Natural language request for the orchestrator agent"
    )
    mcp_agent_parser.add_argument(
        "--domain",
        default="all",
        help="Tool domain to focus on (e.g., 'atlassian', 'gaia', 'all')",
    )
    mcp_agent_parser.add_argument(
        "--context", help="Optional additional context about the request"
    )

    # MCP Docker command (per-agent MCP server)
    mcp_docker_parser = mcp_subparsers.add_parser(
        "docker", help="Start Docker MCP server (per-agent architecture)"
    )
    mcp_docker_parser.add_argument(
        "--host", default="localhost", help="Host to bind to (default: localhost)"
    )
    mcp_docker_parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    mcp_docker_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )

    # Cache command (for Context7 cache management)
    cache_parser = subparsers.add_parser(
        "cache", help="Manage Context7 API cache and rate limiting"
    )
    cache_subparsers = cache_parser.add_subparsers(
        dest="cache_action", help="Cache action to perform"
    )

    # Cache status command
    _ = cache_subparsers.add_parser("status", help="Show cache and rate limiter status")

    # Cache clear command
    cache_clear_parser = cache_subparsers.add_parser("clear", help="Clear cached data")
    cache_clear_parser.add_argument(
        "--context7", action="store_true", help="Clear Context7 cache"
    )
    cache_clear_parser.add_argument(
        "--all", action="store_true", help="Clear all caches"
    )

    args = parser.parse_args()

    # Check if action is specified
    if not args.action:
        log.warning("No action specified. Displaying help message.")
        parser.print_help()
        return

    # Set logging level using the GaiaLogger manager
    from gaia.logger import log_manager

    log_manager.set_level("gaia", getattr(logging, args.logging_level))

    # Handle core Gaia CLI commands
    if args.action in ["prompt", "chat", "talk", "stats"]:
        kwargs = {
            k: v for k, v in vars(args).items() if v is not None and k != "action"
        }
        log.debug(f"Executing {args.action} with parameters: {kwargs}")
        try:
            result = run_cli(args.action, **kwargs)
            if result:
                print(result)
        except Exception as e:
            log.error(f"Error executing {args.action}: {e}")
            print(f"❌ Error: {e}")
            sys.exit(1)
        return

    # Handle report generation command
    if args.action == "report":
        log.info("Generating summary report from evaluation directory")
        try:
            from gaia.eval.eval import Evaluator
        except ImportError as e:
            log.error(f"Failed to import evaluator: {e}")
            print("❌ Error: Failed to import eval module.")
            print("The evaluation dependencies are not installed.")
            print("")
            print("To fix this, install the evaluation dependencies:")
            print("  pip install -e .[eval]")
            print("")
            print("This will install required packages including:")
            print("  - anthropic (for Claude AI)")
            print("  - beautifulsoup4 (for HTML processing)")
            print("  - python-dotenv (for environment variables)")
            return

        try:
            evaluator = Evaluator()

            # If summary_only is True, don't save the report file
            output_path = None if args.summary_only else args.output_file

            # Create output directory if it doesn't exist
            if output_path and output_path != "temp_report.md":
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)

            result = evaluator.generate_summary_report(
                eval_dir=args.eval_dir, output_path=output_path or "temp_report.md"
            )

            print(
                f"✅ Successfully analyzed {result['models_analyzed']} evaluation files"
            )

            if not args.summary_only:
                print(f"  Report saved to: {result['report_path']}")

            # Display key metrics summary
            models_data = result["summary_data"]
            evaluation_type = result.get("evaluation_type", "unknown")

            if models_data:
                best_model = models_data[0]

                if evaluation_type == "summarization":
                    # Show quality score for summarization evaluations
                    print(
                        f"  Best performing model: {best_model['name']} ({best_model['quality_score']:.1f}% quality score)"
                    )
                    print(
                        f"  Overall quality range: {models_data[-1]['quality_score']:.1f}% - {best_model['quality_score']:.1f}%"
                    )

                    # Check if any model meets production standards for summarization
                    production_ready = any(
                        m["quality_score"] >= 70.0 for m in models_data
                    )
                    if production_ready:
                        print("  Status: Some models approaching production readiness")
                    else:
                        print(
                            "  Status: No models meet production standards (70%+ quality score)"
                        )
                else:
                    # Show pass rate for RAG evaluations
                    print(
                        f"  Best performing model: {best_model['name']} ({best_model['pass_rate']:.0%} pass rate)"
                    )
                    print(
                        f"  Overall performance range: {models_data[-1]['pass_rate']:.0%} - {best_model['pass_rate']:.0%}"
                    )

                    # Check if any model meets production standards for RAG
                    production_ready = any(
                        m["pass_rate"] >= 0.7 and m["mean_similarity"] >= 0.7
                        for m in models_data
                    )
                    if production_ready:
                        print("  Status: Some models approaching production readiness")
                    else:
                        print(
                            "  Status: No models meet production standards (70% pass rate + 0.7 similarity)"
                        )

            # Clean up temp file if using summary_only
            if args.summary_only and output_path is None:
                try:
                    os.remove("temp_report.md")
                except OSError as e:
                    log.debug(f"Could not remove temp file: {e}")

        except Exception as e:
            log.error(f"Error generating report: {e}")
            print(f"Error generating report: {e}")
            return

        return

    # Handle summarize command
    if args.action == "summarize":
        import json

        from gaia.apps.summarize.app import SummarizerApp, SummaryConfig
        from gaia.apps.summarize.html_viewer import HTMLViewer

        # Handle list-configs option
        if args.list_configs:
            import gaia.apps.summarize.app

            config_dir = Path(gaia.apps.summarize.app.__file__).parent / "configs"
            if config_dir.exists():
                print("\nAvailable summarization configurations:\n")
                for config_file in sorted(config_dir.glob("*.json")):
                    try:
                        with open(config_file, encoding="utf-8") as f:
                            config_data = json.load(f)
                        name = config_file.stem
                        desc = config_data.get("description", "No description")
                        print(f"{name:<20} - {desc}")
                    except (json.JSONDecodeError, OSError) as e:
                        log.debug(f"Failed to read config file {config_file}: {e}")
                print("\nUse: gaia summarize --config <config_name>")
            else:
                print("No configuration templates found.")
            return

        # Validate required arguments (input not required for --list-configs)
        if not args.list_configs and not args.input:
            # Show help instead of just an error
            print("\nUsage: gaia summarize -i INPUT [options]\n")
            print("Summarize meeting transcripts and emails\n")
            print("Required arguments:")
            print("  -i, --input INPUT    Input file or directory path\n")
            print("Common options:")
            print(
                "  -o, --output OUTPUT  Output file/directory path (auto-adjusted based on format)"
            )
            print(
                "  -f, --format FORMAT  Output format: json, pdf, email, both (default: json)"
            )
            print(
                "  --styles STYLES      Summary style(s): brief, detailed, bullets, executive,"
            )
            print("                       participants, action_items, all")
            print(
                "                       (default: executive participants action_items)"
            )
            print(
                "  --config CONFIG      Use predefined configuration from configs/ directory"
            )
            print("  --list-configs       List all available configuration templates\n")
            print("Examples:")
            print("  gaia summarize -i meeting.txt -o summary.json")
            print("  gaia summarize -i meeting.txt --styles executive action_items")
            print("  gaia summarize -i ./transcripts/ -o ./summaries/")
            print("  gaia summarize --list-configs\n")
            print("For full help: gaia summarize --help")
            sys.exit(1)

        # Handle "all" style
        if "all" in args.styles:
            args.styles = [
                "brief",
                "detailed",
                "bullets",
                "executive",
                "participants",
                "action_items",
            ]

        # Validate email format requirements
        if args.format == "email":
            if Path(args.input).is_dir():
                print(
                    "❌ Error: Email format only supports single file input, not directories"
                )
                sys.exit(1)
            if not args.email_to:
                print("❌ Error: --email-to is required for email output format")
                sys.exit(1)

            # Validate email addresses
            from gaia.apps.summarize.app import validate_email_list

            try:
                validate_email_list(args.email_to)
                if args.email_cc:
                    validate_email_list(args.email_cc)
            except ValueError as e:
                print(f"❌ Error: {e}")
                sys.exit(1)

        # Load configuration if specified
        if args.config:
            import gaia.apps.summarize

            config_path = (
                Path(gaia.apps.summarize.__file__).parent
                / "configs"
                / f"{args.config}.json"
            )
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                # Apply config values
                if "styles" in config_data:
                    args.styles = config_data["styles"]
                if "format" in config_data:
                    args.format = config_data["format"]
                if "max_tokens" in config_data:
                    args.max_tokens = config_data["max_tokens"]
                if "combined_prompt" in config_data:
                    args.combined_prompt = config_data["combined_prompt"]
                log.info(f"Loaded configuration from {args.config}")
            else:
                print(f"❌ Error: Configuration file '{args.config}' not found")
                sys.exit(1)

        # Set logging level
        if args.verbose:
            log_manager.set_level("gaia.apps.summarize", logging.DEBUG)
        elif args.quiet:
            log_manager.set_level("gaia.apps.summarize", logging.WARNING)

        # Create summarizer config
        config = SummaryConfig(
            model=args.model,
            max_tokens=args.max_tokens,
            input_type=args.type,
            styles=args.styles,
            combined_prompt=args.combined_prompt,
        )

        # Create summarizer app
        app = SummarizerApp(config)

        try:
            input_path = Path(args.input)

            if input_path.is_file():
                # Single file processing
                if not args.quiet:
                    print(f"Summarizing file: {input_path}")

                result = app.summarize_file(input_path)

                # Handle output
                if args.format == "json":
                    output_path = args.output or input_path.with_suffix(".summary.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=2)
                    print(f"✅ Summary saved to: {output_path}")

                    # Create and open HTML viewer unless disabled
                    if not args.no_viewer:
                        html_path = HTMLViewer.create_and_open(
                            result, output_path, auto_open=True
                        )
                        print(f"🌐 HTML viewer created: {html_path}")
                        print(
                            "   (Use --no-viewer to disable automatic HTML generation)"
                        )

                elif args.format == "email":
                    # Email output - show preview and open email client
                    print("\n📧 Email Preview:")
                    print(f"To: {args.email_to}")
                    if args.email_cc:
                        print(f"CC: {args.email_cc}")
                    subject = args.email_subject or f"Summary - {input_path.stem}"
                    print(f"Subject: {subject}")

                    # Build email body
                    email_body = f"Summary of: {input_path.name}\n"
                    email_body += "=" * 50 + "\n\n"

                    # Add summaries based on result structure
                    if "summary" in result:
                        # Single style output
                        email_body += result["summary"]["text"] + "\n\n"
                        if "items" in result["summary"]:
                            email_body += "Action Items:\n"
                            for item in result["summary"]["items"]:
                                email_body += f"  • {item}\n"
                            email_body += "\n"
                    else:
                        # Multiple styles output
                        for style, summary_data in result["summaries"].items():
                            email_body += f"{style.upper().replace('_', ' ')}:\n"
                            email_body += "-" * 30 + "\n"
                            if "text" in summary_data:
                                email_body += summary_data["text"] + "\n"
                            if "items" in summary_data:
                                for item in summary_data["items"]:
                                    email_body += f"  • {item}\n"
                            if "participants" in summary_data:
                                for participant in summary_data["participants"]:
                                    email_body += f"  • {participant}\n"
                            email_body += "\n"

                    # Show preview of email body
                    print("\nEmail Body Preview (first 500 chars):")
                    print("-" * 50)
                    print(email_body[:500] + ("..." if len(email_body) > 500 else ""))
                    print("-" * 50)

                    print("\nPress Enter to open email client, or Ctrl+C to cancel...")
                    try:
                        input()

                        # Create mailto URL
                        import platform
                        import urllib.parse

                        mailto_params = {
                            "subject": subject,
                            "body": email_body[
                                :2000
                            ],  # Limit body to avoid URL length issues
                        }
                        if args.email_cc:
                            mailto_params["cc"] = args.email_cc

                        # Build mailto URL
                        params_str = urllib.parse.urlencode(
                            mailto_params, quote_via=urllib.parse.quote
                        )
                        mailto_url = f"mailto:{args.email_to}?{params_str}"

                        # Open email client
                        system = platform.system()
                        try:
                            if system == "Windows":
                                subprocess.run(
                                    ["start", "", mailto_url], shell=True, check=True
                                )
                            elif system == "Darwin":  # macOS
                                subprocess.run(["open", mailto_url], check=True)
                            else:  # Linux/Unix
                                subprocess.run(["xdg-open", mailto_url], check=True)
                            print("✅ Email client opened successfully")
                        except subprocess.CalledProcessError:
                            print(
                                "❌ Failed to open email client. Please check your default email client settings."
                            )
                        except Exception as e:
                            print(f"❌ Error opening email client: {e}")

                    except KeyboardInterrupt:
                        print("\nCancelled.")

                elif args.format in ["pdf", "both"]:
                    # Generate PDF output
                    try:
                        from gaia.apps.summarize.pdf_formatter import (
                            HAS_REPORTLAB,
                            PDFFormatter,
                        )

                        if not HAS_REPORTLAB:
                            print(
                                "❌ Error: PDF output requires reportlab. Install with: pip install reportlab"
                            )
                            if args.format == "both":
                                print(
                                    "ℹ️  JSON output was still generated successfully."
                                )
                            sys.exit(1)

                        formatter = PDFFormatter()
                        pdf_path = Path(
                            args.output or input_path.with_suffix(".summary.pdf")
                        )

                        # Generate PDF
                        formatter.format_summary_as_pdf(result, pdf_path)
                        print(f"✅ PDF summary saved to: {pdf_path}")

                        # Also save JSON if format is "both"
                        if args.format == "both":
                            json_path = pdf_path.with_suffix(".json")
                            with open(json_path, "w", encoding="utf-8") as f:
                                json.dump(result, f, indent=2)
                            print(f"✅ JSON summary saved to: {json_path}")

                            # Create HTML viewer for JSON
                            if not args.no_viewer:
                                html_path = HTMLViewer.create_and_open(
                                    result, json_path, auto_open=True
                                )
                                print(f"🌐 HTML viewer created: {html_path}")

                    except ImportError as e:
                        print(f"❌ Error: {e}")
                        if args.format == "both":
                            # Fall back to JSON only
                            json_path = Path(
                                args.output or input_path.with_suffix(".summary.json")
                            )
                            with open(json_path, "w", encoding="utf-8") as f:
                                json.dump(result, f, indent=2)
                            print(f"✅ JSON summary saved to: {json_path}")
                            print(
                                "ℹ️  PDF generation skipped due to missing dependencies."
                            )
                        else:
                            sys.exit(1)
                    except Exception as e:
                        print(f"❌ Error generating PDF: {e}")
                        sys.exit(1)

            elif input_path.is_dir():
                # Directory batch processing
                if not args.quiet:
                    print(f"Summarizing directory: {input_path}")

                results = app.summarize_directory(input_path)

                if not results:
                    print("❌ No files found to summarize")
                    sys.exit(1)

                # Save results
                output_dir = Path(args.output or "./summaries")
                output_dir.mkdir(exist_ok=True)

                # Check if we need PDF formatter
                pdf_formatter = None
                if args.format in ["pdf", "both"]:
                    try:
                        from gaia.apps.summarize.pdf_formatter import (
                            HAS_REPORTLAB,
                            PDFFormatter,
                        )

                        if HAS_REPORTLAB:
                            pdf_formatter = PDFFormatter()
                        else:
                            print(
                                "⚠️  Warning: PDF output requires reportlab. Install with: pip install reportlab"
                            )
                            if args.format == "pdf":
                                print("❌ Cannot generate PDF files without reportlab.")
                                sys.exit(1)
                    except ImportError:
                        print("⚠️  Warning: PDF formatter not available")
                        if args.format == "pdf":
                            sys.exit(1)

                for i, result in enumerate(results):
                    input_file = result["metadata"]["input_file"]
                    base_name = Path(input_file).stem

                    files_created = []

                    # Save JSON if needed
                    if args.format in ["json", "both"]:
                        json_path = output_dir / f"{base_name}.summary.json"
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(result, f, indent=2)
                        files_created.append(json_path.name)

                        # Create HTML viewer for JSON (don't auto-open for batch)
                        if not args.no_viewer:
                            html_path = HTMLViewer.create_and_open(
                                result,
                                json_path,
                                auto_open=False,  # Don't open browser for each file in batch
                            )
                            files_created.append(html_path.name)

                    # Save PDF if needed
                    if args.format in ["pdf", "both"] and pdf_formatter:
                        pdf_path = output_dir / f"{base_name}.summary.pdf"
                        try:
                            pdf_formatter.format_summary_as_pdf(result, pdf_path)
                            files_created.append(pdf_path.name)
                        except Exception as e:
                            print(
                                f"⚠️  Warning: Failed to generate PDF for {base_name}: {e}"
                            )

                    if not args.quiet and files_created:
                        print(
                            f"✅ [{i+1}/{len(results)}] {Path(input_file).name} → {', '.join(files_created)}"
                        )

                print(
                    f"\n✅ Processed {len(results)} files. Summaries saved to: {output_dir}"
                )
                if not args.no_viewer and args.format in ["json", "both"]:
                    print("   📂 HTML viewers created for each JSON file")
                    print("   💡 Open any .html file to view the formatted summary")

            else:
                print(f"❌ Error: Input path does not exist: {input_path}")
                sys.exit(1)

        except Exception as e:
            log.error(f"Error during summarization: {e}")
            print(f"❌ Error: {e}")
            sys.exit(1)

        return

    # Handle utility commands
    if args.action == "test":
        log.info(f"Running test type: {args.test_type}")
        if args.test_type.startswith("tts"):
            try:
                from gaia.audio.kokoro_tts import KokoroTTS

                tts = KokoroTTS()
                log.debug("TTS initialized successfully")
            except Exception as e:
                log.error(f"Failed to initialize TTS: {e}")
                print(f"❌ Error: Failed to initialize TTS: {e}")
                return

            test_text = (
                args.test_text
                or """
Let's play a game of trivia. I'll ask you a series of questions on a particular topic,
and you try to answer them to the best of your ability.

Here's your first question:

**Question 1:** Which American author wrote the classic novel "To Kill a Mockingbird"?

A) F. Scott Fitzgerald
B) Harper Lee
C) Jane Austen
D) J. K. Rowling
E) Edgar Allan Poe

Let me know your answer!
"""
            )

            if args.test_type == "tts-preprocessing":
                tts.test_preprocessing(test_text)
            elif args.test_type == "tts-streaming":
                tts.test_streaming_playback(test_text)
            elif args.test_type == "tts-audio-file":
                tts.test_generate_audio_file(test_text, args.output_audio_file)

        elif args.test_type.startswith("asr"):
            try:
                from gaia.audio.whisper_asr import WhisperAsr

                asr = WhisperAsr(
                    model_size=args.whisper_model_size,
                    device_index=args.audio_device_index,
                )
                log.debug("ASR initialized successfully")
            except ImportError:
                log.error(
                    "WhisperAsr not found. Please install voice support with: pip install -e .[talk]"
                )
                raise
            except Exception as e:
                log.error(f"Failed to initialize ASR: {e}")
                print(f"❌ Error: Failed to initialize ASR: {e}")
                return

            if args.test_type == "asr-file-transcription":
                if not args.input_audio_file:
                    print(
                        "❌ Error: --input-audio-file is required for asr-file-transcription test"
                    )
                    return
                try:
                    text = asr.transcribe_file(args.input_audio_file)
                    print("\nTranscription result:")
                    print("-" * 40)
                    print(text)
                    print("-" * 40)
                except Exception as e:
                    print(f"❌ Error transcribing file: {e}")

            elif args.test_type == "asr-microphone":
                print(f"\nRecording for {args.recording_duration} seconds...")
                print("Speak into your microphone...")

                # Setup transcription queue and start recording
                import queue

                transcription_queue = queue.Queue()
                asr.transcription_queue = transcription_queue
                asr.start_recording()

                try:
                    start_time = time.time()
                    while time.time() - start_time < args.recording_duration:
                        try:
                            text = transcription_queue.get_nowait()
                            print(f"\nTranscribed: {text}")
                        except queue.Empty:
                            time.sleep(0.1)
                            remaining = args.recording_duration - int(
                                time.time() - start_time
                            )
                            print(f"\rRecording... {remaining}s remaining", end="")
                finally:
                    asr.stop_recording()
                    print("\nRecording stopped.")

            elif args.test_type == "asr-list-audio-devices":
                from gaia.audio.audio_recorder import AudioRecorder

                recorder = AudioRecorder()
                devices = recorder.list_audio_devices()
                print("\nAvailable Audio Input Devices:")
                for device in devices:
                    print(f"Index {device['index']}: {device['name']}")
                    print(f"    Max Input Channels: {device['maxInputChannels']}")
                    print(f"    Default Sample Rate: {device['defaultSampleRate']}")
                    print()
                return

        return

    # Handle utility functions
    if args.action == "youtube":
        if args.download_transcript:
            log.info(f"Downloading transcript from {args.download_transcript}")
            try:
                from llama_index.readers.youtube_transcript import (
                    YoutubeTranscriptReader,
                )

                doc = YoutubeTranscriptReader().load_data(
                    ytlinks=[args.download_transcript]
                )
                output_path = args.output_path or "transcript.txt"
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(doc[0].text)
                print(f"✅ Transcript downloaded to: {output_path}")
            except ImportError as e:
                print(
                    "❌ Error: YouTube transcript functionality requires additional dependencies."
                )
                print(
                    "Please install: pip install llama-index-readers-youtube-transcript"
                )
                print(f"Import error: {e}")
                sys.exit(1)
            return

    # Handle kill command
    if args.action == "kill":
        log.info(f"Attempting to kill process on port {args.port}")
        result = kill_process_by_port(args.port)
        if result["success"]:
            print(f"✅ {result['message']}")
        else:
            print(f"❌ {result['message']}")
        return

    # Handle model pull command
    if args.action == "pull":
        log.info(f"Pulling model: {args.model_name}")
        verbose = getattr(args, "verbose", False)
        try:
            client = LemonadeClient(host=args.host, port=args.port, verbose=verbose)

            # Check if Lemonade server is running
            if not check_lemonade_health(args.host, args.port):
                print_lemonade_error()
                return

            print(f"📥 Pulling model: {args.model_name}")

            # Define a CLI progress callback for real-time updates
            last_percent = [-1]  # Use list to allow mutation in closure
            last_file_index = [0]

            def cli_progress_callback(event_type: str, data: dict) -> None:
                """Display download progress in CLI."""
                if event_type == "progress":
                    percent = data.get("percent", 0)
                    file_name = data.get("file", "unknown")
                    file_index = data.get("file_index", 1)
                    total_files = data.get("total_files", 1)

                    # Print newline when moving to a new file
                    if file_index != last_file_index[0] and last_file_index[0] > 0:
                        print()  # Newline for previous file
                    last_file_index[0] = file_index

                    # Update every 2% for smooth progress
                    if percent >= last_percent[0] + 2 or percent == 0 or percent == 100:
                        bytes_downloaded = data.get("bytes_downloaded", 0)
                        bytes_total = data.get("bytes_total", 0)

                        # Create progress bar
                        bar = _make_progress_bar(percent)
                        progress_line = (
                            f"   {bar} {percent:3d}% "
                            f"[{file_index}/{total_files}] {file_name}: "
                            f"{_format_bytes(bytes_downloaded)}/{_format_bytes(bytes_total)}"
                        )
                        print(f"\r{progress_line:<100}", end="", flush=True)
                        last_percent[0] = percent

                elif event_type == "complete":
                    print()  # Newline after progress
                    print(f"✅ Model downloaded successfully: {args.model_name}")

                elif event_type == "error":
                    print()  # Newline after progress
                    error_msg = data.get("error", "Unknown error")
                    print(f"❌ Download failed: {error_msg}")

            # Use streaming pull with progress callback
            completed = False
            for event in client.pull_model_stream(
                model_name=args.model_name,
                checkpoint=getattr(args, "checkpoint", None),
                recipe=getattr(args, "recipe", None),
                reasoning=getattr(args, "reasoning", False) or None,
                vision=getattr(args, "vision", False) or None,
                embedding=getattr(args, "embedding", False) or None,
                reranking=getattr(args, "reranking", False) or None,
                mmproj=getattr(args, "mmproj", None),
                timeout=args.timeout,
                progress_callback=cli_progress_callback,
            ):
                if event.get("event") == "complete":
                    completed = True
                elif event.get("event") == "error":
                    sys.exit(1)

            if not completed:
                print("⚠️  Model pull completed without explicit complete event")

        except LemonadeClientError as e:
            print(f"❌ Error pulling model: {e}")
            sys.exit(1)
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "refused" in error_msg:
                print_lemonade_error()
            else:
                print(f"❌ Error: {e}")
            sys.exit(1)
        return

    # Handle model download command
    if args.action == "download":
        from gaia.llm.lemonade_client import AGENT_PROFILES, MODELS

        log.info(f"Download models command - agent: {args.agent}")
        verbose = getattr(args, "verbose", False)
        try:
            client = LemonadeClient(host=args.host, port=args.port, verbose=verbose)

            # Clear cache mode: delete all GAIA models (including partial downloads)
            if args.clear_cache:
                # Check if Lemonade server is running
                if not check_lemonade_health(args.host, args.port):
                    print_lemonade_error()
                    return

                model_ids = client.get_required_models("all")
                if not model_ids:
                    print("📦 No GAIA models defined")
                    return

                print(f"🗑️  Clearing cache for {len(model_ids)} GAIA model(s)...")
                print("   (This removes both complete and partial downloads)")
                print()

                success_count = 0
                skip_count = 0
                fail_count = 0

                in_use_models = []
                for model_id in model_ids:
                    print(f"   Deleting {model_id}...", end=" ", flush=True)
                    try:
                        client.delete_model(model_id)
                        print("✅")
                        success_count += 1
                    except LemonadeClientError as e:
                        error_str = str(e).lower()
                        # Model not found is OK - means it wasn't downloaded
                        if "not found" in error_str or "does not exist" in error_str:
                            print("⏭️  (not downloaded)")
                            skip_count += 1
                        elif "being used by another process" in error_str:
                            print("🔒 (model is loaded)")
                            in_use_models.append(model_id)
                            fail_count += 1
                        else:
                            print(f"❌ {e}")
                            fail_count += 1

                print()
                print("=" * 50)
                print("🗑️  Cache Clear Summary:")
                print(f"   ✅ Deleted: {success_count}")
                print(f"   ⏭️  Skipped (not downloaded): {skip_count}")
                if fail_count > 0:
                    print(f"   ❌ Failed: {fail_count}")
                print("=" * 50)

                # Show helpful tips if models are in use
                if in_use_models:
                    print()
                    print(
                        "💡 Some models could not be deleted because they are currently loaded."
                    )
                    print("   To delete them, restart Lemonade Server and try again:")
                    print()
                    print(
                        "   1. Close any running GAIA commands (gaia chat, gaia code, etc.)"
                    )
                    print(
                        "   2. Restart Lemonade Server (close window and run: lemonade-server serve)"
                    )
                    print("   3. Run: gaia download --clear-cache")
                    print()
                    print("   Or manually delete the model cache folders:")
                    print("   - Lemonade cache: %LOCALAPPDATA%\\lemonade\\")
                    print(
                        "   - HuggingFace cache: %USERPROFILE%\\.cache\\huggingface\\hub\\"
                    )

                return

            # List mode: show required models without downloading
            if args.list_models:
                agent_name = args.agent.lower()
                if agent_name == "all":
                    print("📦 Models required for all GAIA agents:\n")
                    all_models = set()
                    for profile in AGENT_PROFILES.values():
                        print(f"  {profile.display_name} ({profile.name}):")
                        for model_key in profile.models:
                            if model_key in MODELS:
                                model = MODELS[model_key]
                                all_models.add(model.model_id)
                                # Check if available
                                available = client.check_model_available(model.model_id)
                                status = "✅" if available else "⬜"
                                print(f"    {status} {model.model_id}")
                        print()
                    print(f"  Total unique models: {len(all_models)}")
                else:
                    profile = client.get_agent_profile(agent_name)
                    if not profile:
                        print(f"❌ Unknown agent: {agent_name}")
                        print(f"   Available: {', '.join(client.list_agents())}")
                        sys.exit(1)
                    print(f"📦 Models required for {profile.display_name}:\n")
                    for model_key in profile.models:
                        if model_key in MODELS:
                            model = MODELS[model_key]
                            available = client.check_model_available(model.model_id)
                            status = "✅" if available else "⬜"
                            print(f"  {status} {model.model_id}")
                return

            # Check if Lemonade server is running
            if not check_lemonade_health(args.host, args.port):
                print_lemonade_error()
                return

            agent_name = args.agent.lower()
            model_ids = client.get_required_models(agent_name)

            if not model_ids:
                if agent_name != "all":
                    profile = client.get_agent_profile(agent_name)
                    if not profile:
                        print(f"❌ Unknown agent: {agent_name}")
                        print(f"   Available: {', '.join(client.list_agents())}")
                        sys.exit(1)
                print(f"📦 No models to download for '{agent_name}'")
                return

            print(f"📥 Downloading {len(model_ids)} model(s) for '{agent_name}'...")
            print()

            # Track progress per model
            current_model = [None]
            last_percent = [-1]
            last_file_index = [0]

            def download_progress_callback(event_type: str, data: dict) -> None:
                """Display download progress in CLI."""
                if event_type == "progress":
                    percent = data.get("percent", 0)
                    file_name = data.get("file", "unknown")
                    file_index = data.get("file_index", 1)
                    total_files = data.get("total_files", 1)

                    # Print newline when moving to a new file
                    if file_index != last_file_index[0] and last_file_index[0] > 0:
                        print()  # Newline for previous file
                    last_file_index[0] = file_index

                    # Update every 2% for smooth progress
                    if percent >= last_percent[0] + 2 or percent == 0 or percent == 100:
                        bytes_downloaded = data.get("bytes_downloaded", 0)
                        bytes_total = data.get("bytes_total", 0)

                        # Create progress bar
                        bar = _make_progress_bar(percent)
                        progress_line = (
                            f"   {bar} {percent:3d}% "
                            f"[{file_index}/{total_files}] {file_name}: "
                            f"{_format_bytes(bytes_downloaded)}/{_format_bytes(bytes_total)}"
                        )
                        print(f"\r{progress_line:<100}", end="", flush=True)
                        last_percent[0] = percent

                elif event_type == "complete":
                    print()  # Newline after progress
                    print("   ✅ Download complete")
                    last_percent[0] = -1  # Reset for next model
                    last_file_index[0] = 0

                elif event_type == "error":
                    print()  # Newline after progress
                    error_msg = data.get("error", "Unknown error")
                    print(f"   ❌ Error: {error_msg}")

            # Download each model
            success_count = 0
            skip_count = 0
            fail_count = 0

            for model_id in model_ids:
                current_model[0] = model_id
                last_percent[0] = -1

                # Check if already available
                if client.check_model_available(model_id):
                    print(f"✅ {model_id} (already downloaded)")
                    skip_count += 1
                    continue

                print(f"📥 {model_id}")

                try:
                    completed = False
                    for event in client.pull_model_stream(
                        model_name=model_id,
                        timeout=args.timeout,
                        progress_callback=download_progress_callback,
                    ):
                        if event.get("event") == "complete":
                            completed = True
                        elif event.get("event") == "error":
                            fail_count += 1
                            break

                    if completed:
                        success_count += 1
                except LemonadeClientError as e:
                    print(f"   ❌ Failed: {e}")
                    fail_count += 1

                print()

            # Summary
            print("=" * 50)
            print("📊 Download Summary:")
            print(f"   ✅ Downloaded: {success_count}")
            print(f"   ⏭️  Skipped (already available): {skip_count}")
            if fail_count > 0:
                print(f"   ❌ Failed: {fail_count}")
            print("=" * 50)

            if fail_count > 0:
                sys.exit(1)

        except LemonadeClientError as e:
            print(f"❌ Error: {e}")
            sys.exit(1)
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "refused" in error_msg:
                print_lemonade_error()
            else:
                print(f"❌ Error: {e}")
            sys.exit(1)
        return

    # Handle LLM command
    if args.action == "llm":
        # Initialize Lemonade with minimal profile for direct LLM queries
        success, _, _ = initialize_lemonade_for_agent(
            agent="minimal",
            auto_start=False,
            quiet=False,
        )
        if not success:
            return

        try:
            from gaia.apps.llm.app import main as llm

            response = llm(
                query=args.query,
                model=args.model,
                max_tokens=args.max_tokens,
                stream=not getattr(args, "no_stream", False),
                base_url=getattr(args, "base_url", None),
            )

            # Only print if streaming is disabled (response wasn't already printed during streaming)
            if getattr(args, "no_stream", False):
                print("\n" + "=" * 50)
                print("LLM Response:")
                print("=" * 50)
                print(response)
                print("=" * 50)
            return
        except Exception as e:
            # Check if it's a connection error and provide helpful message
            error_msg = str(e).lower()
            if (
                "connection" in error_msg
                or "refused" in error_msg
                or "timeout" in error_msg
            ):
                print_lemonade_error()
            else:
                print(f"❌ Error: {str(e)}")
            return

    # Handle groundtruth generation
    if args.action == "groundtruth":
        log.info("Starting ground truth generation")
        try:
            from gaia.eval.groundtruth import GroundTruthGenerator, UseCase
        except ImportError as e:
            log.error(f"Failed to import GroundTruthGenerator: {e}")
            print("❌ Error: Failed to import groundtruth module.")
            print("The evaluation dependencies are not installed.")
            print("")
            print("To fix this, install the evaluation dependencies:")
            print("  pip install -e .[eval]")
            print("")
            print("This will install required packages including:")
            print("  - anthropic (for Claude AI)")
            print("  - beautifulsoup4 (for HTML processing)")
            print("  - python-dotenv (for environment variables)")
            return

        # Initialize generator
        try:
            generator = GroundTruthGenerator(
                model=args.claude_model, max_tokens=args.max_tokens
            )
        except Exception as e:
            log.error(f"Error initializing generator: {e}")
            print(f"Error initializing generator: {e}")
            return

        # Load custom prompt if provided
        custom_prompt = None
        if args.custom_prompt:
            try:
                with open(args.custom_prompt, "r", encoding="utf-8") as f:
                    custom_prompt = f.read().strip()
                print(f"Using custom prompt from: {args.custom_prompt}")
            except Exception as e:
                log.error(f"Error loading custom prompt: {e}")
                print(f"Error loading custom prompt: {e}")
                return

        save_text = not args.no_save_text

        # Convert use case string to enum
        use_case = UseCase(args.use_case)

        try:
            if args.file:
                # Process single file
                print(
                    f"Processing single file: {args.file} (use case: {use_case.value})"
                )
                result = generator.generate(
                    file_path=args.file,
                    use_case=use_case,
                    prompt=custom_prompt,
                    save_text=save_text,
                    output_dir=args.output_dir,
                    num_samples=args.num_samples,
                )
                print("✅ Successfully generated ground truth data")
                print(f"  Output: {args.output_dir}")
                usage = result["metadata"]["usage"]
                cost = result["metadata"]["cost"]
                print(
                    f"  Token usage: {usage['input_tokens']:,} input + {usage['output_tokens']:,} output = {usage['total_tokens']:,} total"
                )
                print(
                    f"  Cost: ${cost['input_cost']:.4f} input + ${cost['output_cost']:.4f} output = ${cost['total_cost']:.4f} total"
                )

                # Different output based on use case
                if use_case == UseCase.RAG:
                    qa_pairs_count = len(result["analysis"]["qa_pairs"])
                    print(
                        f"  Q&A pairs: {qa_pairs_count} (${cost['total_cost']/qa_pairs_count:.4f} per pair)"
                    )
                elif use_case == UseCase.SUMMARIZATION:
                    summary_count = len(result["analysis"]["summaries"])
                    print(
                        f"  Summary types generated: {summary_count} different formats"
                    )
                    print(
                        f"  Evaluation criteria: {len(result['analysis']['evaluation_criteria'])} categories"
                    )
                elif use_case == UseCase.QA:
                    qa_count = len(result["analysis"]["qa_pairs"])
                    print(f"  Q&A pairs generated: {qa_count} questions")
                    print(
                        f"  Evaluation criteria: {len(result['analysis']['evaluation_criteria'])} categories"
                    )

            elif args.directory:
                # Process directory
                print(
                    f"Processing directory: {args.directory} (use case: {use_case.value})"
                )
                print(f"File pattern: {args.pattern}")
                consolidated_data = generator.generate_batch(
                    input_dir=args.directory,
                    file_pattern=args.pattern,
                    use_case=use_case,
                    force=args.force,
                    prompt=custom_prompt,
                    save_text=save_text,
                    output_dir=args.output_dir,
                    num_samples=args.num_samples,
                )

                if consolidated_data:
                    # Extract totals from consolidated metadata
                    total_usage = consolidated_data["metadata"]["total_usage"]
                    total_cost = consolidated_data["metadata"]["total_cost"]
                    files_processed = consolidated_data["metadata"]["consolidated_from"]

                    print(f"✅ Successfully processed {files_processed} files")
                    print(
                        f"  Output: {args.output_dir}/consolidated_{use_case.value}_groundtruth.json"
                    )
                    print(
                        f"  Total token usage: {total_usage['input_tokens']:,} input + {total_usage['output_tokens']:,} output = {total_usage['total_tokens']:,} total"
                    )
                    print(
                        f"  Total cost: ${total_cost['input_cost']:.4f} input + ${total_cost['output_cost']:.4f} output = ${total_cost['total_cost']:.4f} total"
                    )
                    print(
                        f"  Average cost per file: ${total_cost['total_cost']/files_processed:.4f}"
                    )

                    # Different summary stats based on use case
                    analysis = consolidated_data["analysis"]
                    if use_case == UseCase.RAG:
                        # For RAG, count total Q&A pairs across all documents
                        total_pairs = 0
                        if "qa_pairs" in analysis:
                            total_pairs = len(analysis["qa_pairs"])
                        print(f"  Total Q&A pairs: {total_pairs}")
                        if total_pairs > 0:
                            print(
                                f"  Average cost per Q&A pair: ${total_cost['total_cost']/total_pairs:.4f}"
                            )
                    elif use_case == UseCase.SUMMARIZATION:
                        summaries_count = len(analysis.get("summaries", {}))
                        print(
                            f"  Generated {files_processed} comprehensive transcript summaries"
                        )
                        print(
                            f"  Consolidated into single file with {summaries_count} transcript summaries"
                        )
                    elif use_case == UseCase.QA:
                        # For QA, count total Q&A pairs across all transcripts
                        total_qa_pairs = 0
                        if "qa_pairs" in analysis:
                            total_qa_pairs = len(analysis["qa_pairs"])
                        print(
                            f"  Generated {files_processed} transcript Q&A evaluations"
                        )
                        print(f"  Total Q&A pairs: {total_qa_pairs}")
                        if total_qa_pairs > 0:
                            print(
                                f"  Average cost per Q&A pair: ${total_cost['total_cost']/total_qa_pairs:.4f}"
                            )
                else:
                    print("No files were processed successfully")
                    return

        except Exception as e:
            log.error(f"Error during groundtruth processing: {e}")
            print(f"❌ Error during processing: {e}")
            return

        return

    # Handle template creation
    if args.action == "create-template":
        log.info("Creating template results file")
        try:
            from gaia.eval.eval import Evaluator
        except ImportError as e:
            log.error(f"Failed to import evaluator: {e}")
            print("❌ Error: Failed to import eval module.")
            print("The evaluation dependencies are not installed.")
            print("")
            print("To fix this, install the evaluation dependencies:")
            print("  pip install -e .[eval]")
            print("")
            print("This will install required packages including:")
            print("  - anthropic (for Claude AI)")
            print("  - beautifulsoup4 (for HTML processing)")
            print("  - python-dotenv (for environment variables)")
            return

        try:
            evaluator = Evaluator()
            template_path = evaluator.create_template(
                groundtruth_file=args.groundtruth_file,
                output_dir=args.output_dir,
                similarity_threshold=args.threshold,
            )
            print("✅ Successfully created template file")
            print(f"  Template: {template_path}")
            print(
                "  Instructions: Fill in the 'response' fields with your model outputs"
            )
            print("  Then run: gaia eval <template_file> to evaluate performance")

        except Exception as e:
            log.error(f"Error creating template: {e}")
            print(f"❌ Error creating template: {e}")
            return

        return

    # Handle evaluation
    if args.action == "eval":
        log.info("Evaluating experiment results")
        try:
            from gaia.eval.eval import Evaluator
        except ImportError as e:
            log.error(f"Failed to import evaluator: {e}")
            print("❌ Error: Failed to import eval module.")
            print("The evaluation dependencies are not installed.")
            print("")
            print("To fix this, install the evaluation dependencies:")
            print("  pip install -e .[eval]")
            print("")
            print("This will install required packages including:")
            print("  - anthropic (for Claude AI)")
            print("  - beautifulsoup4 (for HTML processing)")
            print("  - python-dotenv (for environment variables)")
            return

        try:
            evaluator = Evaluator(model=args.claude_model)

            # If summary_only is True, don't save to output_dir (None)
            output_dir = None if args.summary_only else args.output_dir

            # Handle directory processing
            if args.directory:
                # Find all experiment JSON files in the directory (recursively)
                experiment_dir = Path(args.directory)

                # If report-only mode, look for existing evaluation files
                if args.report_only:
                    eval_dir = Path(args.output_dir)
                    if not eval_dir.exists():
                        print(f"❌ Evaluation directory not found: {args.output_dir}")
                        print(
                            "Please run evaluation first or specify correct output directory."
                        )
                        sys.exit(1)

                    # Find all evaluation files
                    eval_files = list(eval_dir.rglob("*.eval.json"))
                    if not eval_files:
                        print(
                            f"❌ No .eval.json files found in directory: {args.output_dir}"
                        )
                        sys.exit(1)

                    print(f"Found {len(eval_files)} evaluation files")

                    # Generate consolidated report
                    evaluation_files = [f.name for f in eval_files]
                    consolidated_report_path = (
                        evaluator.create_consolidated_evaluation_report(
                            evaluation_files, args.output_dir, str(experiment_dir)
                        )
                    )
                    print(f"✅ Consolidated Report: {consolidated_report_path}")
                    return

                json_files = list(experiment_dir.rglob("*.experiment.json"))

                if not json_files:
                    print(
                        f"❌ No .experiment.json files found in directory: {args.directory}"
                    )
                    sys.exit(1)

                # Convert to strings for compatibility
                json_files = [str(f) for f in json_files]

                print(f"Found {len(json_files)} JSON files to process")

                # Filter out existing evaluations if skip-existing is enabled
                files_to_process = []
                skipped_count = 0

                for json_file in json_files:
                    # By default skip existing evaluations, unless --force is specified
                    if not args.force and evaluator.check_evaluation_exists(
                        json_file, args.output_dir
                    ):
                        skipped_count += 1
                        continue
                    files_to_process.append(json_file)

                if skipped_count > 0:
                    print(f"Skipping {skipped_count} existing evaluations")
                    print(f"Processing {len(files_to_process)} new evaluations")

                if not files_to_process:
                    print(
                        "✅ All evaluations already exist. Use --force to re-run evaluations, or --regenerate-report to rebuild the consolidated report."
                    )
                    if args.regenerate_report or args.incremental_update:
                        # Generate report from existing evaluations
                        eval_dir = Path(args.output_dir)
                        if eval_dir.exists():
                            evaluation_files = [
                                f.name for f in eval_dir.rglob("*.eval.json")
                            ]
                            if evaluation_files:
                                consolidated_report_path = (
                                    evaluator.create_consolidated_evaluation_report(
                                        evaluation_files,
                                        args.output_dir,
                                        str(experiment_dir),
                                    )
                                )
                                print(
                                    f"✅ Updated Consolidated Report: {consolidated_report_path}"
                                )
                    return

                total_files_processed = 0
                total_usage = {"total_tokens": 0}
                total_cost = {"total_cost": 0.0}
                evaluation_files = []  # Track evaluation files for consolidated report

                for json_file in sorted(files_to_process):
                    print(f"\n📄 Processing: {os.path.basename(json_file)}")

                    try:
                        evaluation_data = evaluator.generate_enhanced_report(
                            results_path=json_file,
                            output_dir=output_dir,
                            groundtruth_path=getattr(args, "groundtruth", None),
                            base_experiment_dir=str(experiment_dir),
                        )

                        if output_dir and evaluation_data:
                            # Track evaluation files for consolidated report
                            eval_file_path = Path(json_file).stem + ".eval.json"
                            evaluation_files.append(eval_file_path)

                        total_files_processed += 1

                        # Display summary for this file
                        overall_rating = evaluation_data.get("overall_rating", {})
                        print(
                            f"  Overall Rating: {overall_rating.get('rating', 'N/A')}"
                        )

                        metrics = overall_rating.get("metrics", {})
                        if metrics:
                            # Display metrics based on evaluation type
                            if "num_questions" in metrics:
                                # Q&A evaluation metrics
                                print(
                                    f"  Questions: {metrics.get('num_questions', 'N/A')}"
                                )

                                pass_rate = metrics.get("pass_rate", "N/A")
                                if isinstance(pass_rate, (int, float)):
                                    print(f"  Pass Rate: {pass_rate:.1%}")
                                else:
                                    print(f"  Pass Rate: {pass_rate}")

                                mean_similarity = metrics.get("mean_similarity", "N/A")
                                if isinstance(mean_similarity, (int, float)):
                                    print(f"  Mean Similarity: {mean_similarity:.3f}")
                                else:
                                    print(f"  Mean Similarity: {mean_similarity}")
                            elif "total_summaries" in metrics:
                                # Summarization evaluation metrics
                                print(
                                    f"  Summaries: {metrics.get('total_summaries', 'N/A')}"
                                )

                                quality_score = metrics.get("quality_score", "N/A")
                                if isinstance(quality_score, (int, float)):
                                    print(f"  Quality Score: {quality_score:.1f}")
                                else:
                                    print(f"  Quality Score: {quality_score}")

                                print(
                                    f"  Excellent: {metrics.get('excellent_count', 0)}, Good: {metrics.get('good_count', 0)}, Fair: {metrics.get('fair_count', 0)}, Poor: {metrics.get('poor_count', 0)}"
                                )

                        # Accumulate usage and cost
                        if evaluation_data.get("total_usage"):
                            file_usage = evaluation_data["total_usage"]
                            total_usage["total_tokens"] += file_usage.get(
                                "total_tokens", 0
                            )

                        if evaluation_data.get("total_cost"):
                            file_cost = evaluation_data["total_cost"]
                            total_cost["total_cost"] += file_cost.get("total_cost", 0.0)

                    except Exception as e:
                        log.error(f"Error processing {json_file}: {e}")
                        print(f"  ❌ Error: {e}")
                        continue

                print(
                    f"\n✅ Successfully processed {total_files_processed}/{len(json_files)} files"
                )

                if not args.summary_only and total_files_processed > 0:
                    print(f"  Detailed Reports: {args.output_dir}")

                    # Create or update consolidated evaluation report
                    if len(evaluation_files) > 0:
                        if args.regenerate_report:
                            consolidated_report_path = (
                                evaluator.create_consolidated_evaluation_report(
                                    evaluation_files,
                                    args.output_dir,
                                    str(experiment_dir),
                                )
                            )
                            print(
                                f"  Regenerated Consolidated Report: {consolidated_report_path}"
                            )
                        elif args.incremental_update and len(evaluation_files) > 0:
                            consolidated_report_path = (
                                evaluator.update_consolidated_evaluation_report(
                                    output_dir=args.output_dir,
                                    new_eval_files=None,  # Auto-detect
                                    regenerate=False,
                                    base_experiment_dir=str(experiment_dir),
                                )
                            )
                            print(
                                f"  Updated Consolidated Report: {consolidated_report_path}"
                            )
                        elif (
                            len(evaluation_files) > 1
                            or not Path(args.output_dir)
                            .joinpath("consolidated_evaluations_report.json")
                            .exists()
                        ):
                            # Create new consolidated report for multiple files or if it doesn't exist
                            consolidated_report_path = (
                                evaluator.create_consolidated_evaluation_report(
                                    evaluation_files,
                                    args.output_dir,
                                    str(experiment_dir),
                                )
                            )
                            print(f"  Consolidated Report: {consolidated_report_path}")
                        else:
                            # Single new file - use incremental update
                            consolidated_report_path = (
                                evaluator.update_consolidated_evaluation_report(
                                    output_dir=args.output_dir,
                                    new_eval_files=None,  # Auto-detect
                                    regenerate=False,
                                    base_experiment_dir=str(experiment_dir),
                                )
                            )
                            print(
                                f"  Updated Consolidated Report: {consolidated_report_path}"
                            )

                # Print total cost information
                if total_usage["total_tokens"] > 0:
                    print(f"  Total Token Usage: {total_usage['total_tokens']:,}")
                if total_cost["total_cost"] > 0:
                    print(f"  Total Cost: ${total_cost['total_cost']:.4f}")

                # Suggest next steps
                print("\nNext steps:")
                print(
                    f"  1. View results interactively: gaia visualize --experiments-dir {args.directory} --evaluations-dir {args.output_dir}"
                )
                print(
                    f"  2. Generate markdown report: gaia report -d {args.output_dir}"
                )

            else:
                # Handle single file processing (existing logic)
                if args.report_only:
                    print(
                        "❌ Error: --report-only flag can only be used with directory input (-d)"
                    )
                    print("For single file evaluation, run without --report-only flag.")
                    sys.exit(1)

                evaluation_data = evaluator.generate_enhanced_report(
                    results_path=args.results_file,
                    output_dir=output_dir,
                    groundtruth_path=getattr(args, "groundtruth", None),
                )

                print("✅ Successfully evaluated experiment")

                # Display summary information
                overall_rating = evaluation_data.get("overall_rating", {})
                print(f"  Overall Rating: {overall_rating.get('rating', 'N/A')}")

                metrics = overall_rating.get("metrics", {})
                if metrics:
                    # Display metrics based on evaluation type
                    if "num_questions" in metrics:
                        # Q&A evaluation metrics
                        print(f"  Questions: {metrics.get('num_questions', 'N/A')}")

                        pass_rate = metrics.get("pass_rate", "N/A")
                        if isinstance(pass_rate, (int, float)):
                            print(f"  Pass Rate: {pass_rate:.1%}")
                        else:
                            print(f"  Pass Rate: {pass_rate}")

                        mean_similarity = metrics.get("mean_similarity", "N/A")
                        if isinstance(mean_similarity, (int, float)):
                            print(f"  Mean Similarity: {mean_similarity:.3f}")
                        else:
                            print(f"  Mean Similarity: {mean_similarity}")
                    elif "total_summaries" in metrics:
                        # Summarization evaluation metrics
                        print(f"  Summaries: {metrics.get('total_summaries', 'N/A')}")

                        quality_score = metrics.get("quality_score", "N/A")
                        if isinstance(quality_score, (int, float)):
                            print(f"  Quality Score: {quality_score:.1f}")
                        else:
                            print(f"  Quality Score: {quality_score}")

                        print(
                            f"  Excellent: {metrics.get('excellent_count', 0)}, Good: {metrics.get('good_count', 0)}, Fair: {metrics.get('fair_count', 0)}, Poor: {metrics.get('poor_count', 0)}"
                        )

                if not args.summary_only:
                    print(f"  Detailed Report: {args.output_dir}")

                    # Auto-update consolidated report after single file evaluation
                    try:
                        consolidated_report_path = (
                            evaluator.update_consolidated_evaluation_report(
                                output_dir=output_dir,
                                new_eval_files=None,  # Auto-detect
                                regenerate=False,
                            )
                        )
                        print(
                            f"  Updated Consolidated Report: {consolidated_report_path}"
                        )
                    except Exception as e:
                        print(f"  ⚠️  Could not update consolidated report: {e}")

                # Print cost information if available
                if evaluation_data.get("total_usage") and evaluation_data.get(
                    "total_cost"
                ):
                    total_usage = evaluation_data["total_usage"]
                    total_cost = evaluation_data["total_cost"]
                    print(f"  Token Usage: {total_usage['total_tokens']:,} total")
                    print(f"  Cost: ${total_cost['total_cost']:.4f}")

        except Exception as e:
            log.error(f"Error evaluating RAG system: {e}")
            print(f"❌ Error evaluating RAG system: {e}")
            return

        return

    # Handle generate command
    if args.action == "generate":
        # Set default output directory to test_data root if not specified
        # The generator will create subdirectories based on content type
        if args.output_dir is None:
            args.output_dir = f"./{DEFAULT_TEST_DATA_DIR}"

        if args.meeting_transcript:
            log.info("Generating example meeting transcripts")
            try:
                from gaia.eval.transcript_generator import TranscriptGenerator
            except ImportError as e:
                log.error(f"Failed to import TranscriptGenerator: {e}")
                print("❌ Error: Failed to import transcript generator module.")
                print("The evaluation dependencies are not installed.")
                print("")
                print("To fix this, install the evaluation dependencies:")
                print("  pip install -e .[eval]")
                print("")
                print("This will install required packages including:")
                print("  - anthropic (for Claude AI)")
                print("  - beautifulsoup4 (for HTML processing)")
                print("  - python-dotenv (for environment variables)")
                return

            try:
                generator = TranscriptGenerator(claude_model=args.claude_model)

                # Filter meeting types if specified
                original_templates = None
                if args.meeting_types:
                    # Temporarily filter the templates
                    original_templates = generator.meeting_templates.copy()
                    generator.meeting_templates = {
                        k: v
                        for k, v in generator.meeting_templates.items()
                        if k in args.meeting_types
                    }

                # Set default target tokens for transcripts if not specified
                target_tokens = args.target_tokens if args.target_tokens else 1000

                result = generator.generate_transcript_set(
                    output_dir=args.output_dir,
                    target_tokens=target_tokens,
                    count_per_type=args.count_per_type,
                )

                print("✅ Successfully generated meeting transcripts")
                print(f"  Output directory: {result['output_directory']}")
                print(f"  Generated files: {len(result['generated_files'])}")
                print(f"  Metadata file: {result['metadata_file']}")

                # Show summary stats
                summary = result["summary"]
                generation_info = summary["generation_info"]
                total_tokens = generation_info["total_claude_usage"]["total_tokens"]
                total_cost = generation_info["total_claude_cost"]["total_cost"]
                avg_tokens = (
                    total_tokens / len(summary["transcripts"])
                    if summary["transcripts"]
                    else 0
                )

                print(f"  Total tokens used: {total_tokens:,}")
                print(f"  Total cost: ${total_cost:.4f}")
                print(f"  Average tokens per file: {avg_tokens:.0f}")
                print(
                    f"  Average cost per file: ${total_cost/len(summary['transcripts']):.4f}"
                )
                print(f"  Meeting types: {', '.join(generation_info['meeting_types'])}")
                print(f"  Claude model: {generation_info['claude_model']}")

                # Restore original templates if they were filtered
                if args.meeting_types and original_templates is not None:
                    generator.meeting_templates = original_templates

            except Exception as e:
                log.error(f"Error generating transcripts: {e}")
                print(f"❌ Error generating transcripts: {e}")
                return

        elif args.email:
            log.info("Generating example business emails")
            try:
                from gaia.eval.email_generator import EmailGenerator
            except ImportError as e:
                log.error(f"Failed to import EmailGenerator: {e}")
                print("❌ Error: Failed to import email generator module.")
                print("The evaluation dependencies are not installed.")
                print("")
                print("To fix this, install the evaluation dependencies:")
                print("  pip install -e .[eval]")
                print("")
                print("This will install required packages including:")
                print("  - anthropic (for Claude AI)")
                print("  - beautifulsoup4 (for HTML processing)")
                print("  - python-dotenv (for environment variables)")
                return

            try:
                generator = EmailGenerator(claude_model=args.claude_model)

                # Filter email types if specified
                original_templates = None
                if args.email_types:
                    # Temporarily filter the templates
                    original_templates = generator.email_templates.copy()
                    generator.email_templates = {
                        k: v
                        for k, v in generator.email_templates.items()
                        if k in args.email_types
                    }

                # Set default target tokens for emails if not specified
                target_tokens = args.target_tokens if args.target_tokens else 800

                result = generator.generate_email_set(
                    output_dir=args.output_dir,
                    target_tokens=target_tokens,
                    count_per_type=args.count_per_type,
                )

                print("✅ Successfully generated business emails")
                print(f"  Output directory: {result['output_directory']}")
                print(f"  Generated files: {len(result['generated_files'])}")
                print(f"  Metadata file: {result['metadata_file']}")

                # Show summary stats
                summary = result["summary"]
                generation_info = summary["generation_info"]
                total_tokens = generation_info["total_claude_usage"]["total_tokens"]
                total_cost = generation_info["total_claude_cost"]["total_cost"]
                avg_tokens = (
                    total_tokens / len(summary["emails"]) if summary["emails"] else 0
                )

                print(f"  Total tokens used: {total_tokens:,}")
                print(f"  Total cost: ${total_cost:.4f}")
                print(f"  Average tokens per file: {avg_tokens:.0f}")
                print(
                    f"  Average cost per file: ${total_cost/len(summary['emails']):.4f}"
                )
                print(f"  Email types: {', '.join(generation_info['email_types'])}")
                print(f"  Claude model: {generation_info['claude_model']}")

                # Restore original templates if they were filtered
                if args.email_types and original_templates is not None:
                    generator.email_templates = original_templates

            except Exception as e:
                log.error(f"Error generating emails: {e}")
                print(f"❌ Error generating emails: {e}")
                return

        return

    # Handle batch-experiment command
    if args.action == "batch-experiment":
        log.info("Running batch experiments")
        try:
            from gaia.eval.batch_experiment import BatchExperimentRunner
        except ImportError as e:
            log.error(f"Failed to import BatchExperimentRunner: {e}")
            print("❌ Error: Failed to import batch experiment module.")
            print("The evaluation dependencies are not installed.")
            print("")
            print("To fix this, install the evaluation dependencies:")
            print("  pip install -e .[eval]")
            print("")
            print("This will install required packages including:")
            print("  - anthropic (for Claude AI)")
            print("  - beautifulsoup4 (for HTML processing)")
            print("  - python-dotenv (for environment variables)")
            return

        # Create sample config if requested
        if args.create_sample_config:
            runner = BatchExperimentRunner.__new__(BatchExperimentRunner)
            runner.log = get_logger(__name__)
            runner.create_sample_config(args.create_sample_config)
            print(f"✅ Sample configuration created: {args.create_sample_config}")
            print("Edit this file to define your experiments, then run:")
            print(
                f"  gaia batch-experiment -c {args.create_sample_config} -i <input_path> -o <output_dir>"
            )
            return

        # Create config from groundtruth if requested
        if args.create_config_from_groundtruth:
            # Determine output filename if not provided in the argument
            groundtruth_path = Path(args.create_config_from_groundtruth)
            default_output = f"{groundtruth_path.stem}.config.json"

            runner = BatchExperimentRunner.__new__(BatchExperimentRunner)
            runner.log = get_logger(__name__)
            try:
                config_path = runner.create_config_from_groundtruth(
                    args.create_config_from_groundtruth, default_output
                )
                print(
                    f"✅ Configuration created from groundtruth metadata: {config_path}"
                )
                print("Review and edit the configuration, then run:")
                print(
                    f"  gaia batch-experiment -c {config_path} -i <input_path> -o <output_dir>"
                )
            except Exception as e:
                print(f"❌ Error creating config from groundtruth: {e}")
                return
            return

        # Validate required arguments
        if not args.config or not args.input:
            print(
                "❌ Error: Both --config and --input are required (unless using --create-sample-config or --create-config-from-groundtruth)"
            )
            return

        try:
            # Run batch experiments
            runner = BatchExperimentRunner(args.config)
            # By default skip existing experiments, unless --force is specified
            result_files, skipped_count = runner.run_all_experiments(
                input_path=args.input,
                output_dir=args.output_dir,
                delay_seconds=args.delay,
                queries_source=args.queries_source,
                skip_existing=not args.force,
            )

            # Report results with skip information
            if skipped_count > 0:
                new_count = len(result_files) - skipped_count
                print(
                    f"✅ Completed {len(result_files)} experiments ({new_count} new, {skipped_count} skipped)"
                )
            else:
                print(f"✅ Completed {len(result_files)} experiments")

            print(f"  Results saved to: {args.output_dir}")
            print("  Generated files:")
            for result_file in result_files:
                print(f"    - {Path(result_file).name}")

            print("\nNext steps:")
            print(f"  1. Evaluate results using: gaia eval -d {args.output_dir}")
            print(f"  2. Generate comparative report: gaia report -d {args.output_dir}")

        except Exception as e:
            log.error(f"Error running batch experiments: {e}")
            print(f"❌ Error running batch experiments: {e}")
            return

        return

    # Handle MCP command
    if args.action == "mcp":
        handle_mcp_command(args)
        return

    # Handle Cache command
    if args.action == "cache":
        handle_cache_command(args)
        return

    # Handle Blender command
    if args.action == "blender":
        handle_blender_command(args)
        return

    # Handle Code command
    if args.action == "code":
        handle_code_command(args)
        return

    # Handle Jira command
    if args.action == "jira":
        handle_jira_command(args)
        return

    # Handle Docker command
    if args.action == "docker":
        handle_docker_command(args)
        return

    # Handle API server command
    if args.action == "api":
        handle_api_command(args)
        return

    # Handle visualize command
    if args.action == "visualize":
        handle_visualize_command(args)
        return

    # Log error for unknown action
    log.error(f"Unknown action specified: {args.action}")
    parser.print_help()
    return


def kill_process_by_port(port):
    """Find and kill a process running on a specific port."""
    try:
        if sys.platform.startswith("win"):
            # Windows implementation
            cmd = f"netstat -ano | findstr :{port}"
            output = subprocess.check_output(cmd, shell=True).decode()
            if output:
                # Split output into lines and process each line
                for line in output.strip().split("\n"):
                    # Only process lines that contain the specific port
                    if f":{port}" in line:
                        parts = line.strip().split()
                        # Get the last part which should be the PID
                        try:
                            pid = int(parts[-1])
                            if pid > 0:  # Ensure we don't try to kill PID 0
                                subprocess.run(
                                    f"taskkill /PID {pid} /F", shell=True, check=True
                                )
                                return {
                                    "success": True,
                                    "message": f"Killed process {pid} running on port {port}",
                                }
                        except (IndexError, ValueError):
                            continue
                return {
                    "success": False,
                    "message": f"Could not find valid PID for port {port}",
                }
        else:
            # Linux/Unix implementation
            try:
                # Use lsof to find process using the port
                cmd = f"lsof -ti:{port}"
                output = subprocess.check_output(cmd, shell=True).decode().strip()
                if output:
                    pids = output.split("\n")
                    killed_pids = []
                    for pid_str in pids:
                        try:
                            pid = int(pid_str.strip())
                            if pid > 0:
                                subprocess.run(f"kill -9 {pid}", shell=True, check=True)
                                killed_pids.append(str(pid))
                        except (ValueError, subprocess.CalledProcessError):
                            continue
                    if killed_pids:
                        return {
                            "success": True,
                            "message": f"Killed process(es) {', '.join(killed_pids)} running on port {port}",
                        }
                return {
                    "success": False,
                    "message": f"Could not find valid PID for port {port}",
                }
            except subprocess.CalledProcessError:
                # If lsof is not available, try netstat + ps approach
                try:
                    # Use netstat to find the port, then extract PID
                    cmd = f"netstat -tulpn | grep :{port}"
                    output = subprocess.check_output(cmd, shell=True).decode()
                    if output:
                        for line in output.strip().split("\n"):
                            if f":{port}" in line:
                                parts = line.strip().split()
                                # Look for PID/process_name pattern in the last column
                                for part in parts:
                                    if "/" in part:
                                        try:
                                            pid = int(part.split("/")[0])
                                            if pid > 0:
                                                subprocess.run(
                                                    f"kill -9 {pid}",
                                                    shell=True,
                                                    check=True,
                                                )
                                                return {
                                                    "success": True,
                                                    "message": f"Killed process {pid} running on port {port}",
                                                }
                                        except (
                                            ValueError,
                                            subprocess.CalledProcessError,
                                        ):
                                            continue
                    return {
                        "success": False,
                        "message": f"Could not find valid PID for port {port}",
                    }
                except subprocess.CalledProcessError:
                    return {
                        "success": False,
                        "message": f"No process found running on port {port} (lsof and netstat methods failed)",
                    }

        return {"success": False, "message": f"No process found running on port {port}"}
    except subprocess.CalledProcessError:
        return {"success": False, "message": f"No process found running on port {port}"}
    except Exception as e:
        return {
            "success": False,
            "message": f"Error killing process on port {port}: {str(e)}",
        }


def wait_for_user():
    """Wait for user to press Enter before continuing."""
    input("Press Enter to continue to the next example...")


def run_blender_examples(agent, selected_example=None, print_result=True):
    """
    Run the Blender agent example demonstrations.

    Args:
        agent: The BlenderAgent instance
        selected_example: Optional example number to run specifically
        print_result: Whether to print the result
    """
    console = agent.console

    examples = {
        1: {
            "name": "Clearing the scene",
            "description": "This example demonstrates how to clear all objects from a scene.",
            "query": "Clear the scene to start fresh",
        },
        2: {
            "name": "Creating a basic cube",
            "description": "This example creates a red cube at the center of the scene.",
            "query": "Create a red cube at the center of the scene and make sure it has a red material",
        },
        3: {
            "name": "Creating a sphere with specific properties",
            "description": "This example creates a blue sphere with specific parameters.",
            "query": "Create a blue sphere at position (3, 0, 0) and set its scale to (2, 2, 2)",
        },
        4: {
            "name": "Creating multiple objects",
            "description": "This example creates multiple objects with specific arrangements.",
            "query": "Create a green cube at (0, 0, 0) and a red sphere 3 units above it",
        },
        5: {
            "name": "Creating and modifying objects",
            "description": "This example creates objects and then modifies them.",
            "query": "Create a blue cylinder, then make it taller and move it up 2 units",
        },
    }

    # If a specific example is requested, run only that one
    if selected_example and selected_example in examples:
        example = examples[selected_example]
        console.print_header(f"=== Example {selected_example}: {example['name']} ===")
        console.print_header(example["description"])
        agent.process_query(example["query"])
        agent.display_result(print_result=print_result)
        return

    # Run all examples in sequence
    for idx, example in examples.items():
        console.print_header(f"=== Example {idx}: {example['name']} ===")
        console.print_header(example["description"])
        agent.process_query(example["query"], trace=True)
        agent.display_result(print_result=print_result)

        # Wait for user input between examples, except the last one
        if idx < len(examples):
            wait_for_user()


def run_blender_interactive_mode(agent, print_result=True):
    """
    Run the Blender Agent in interactive mode where the user can continuously input queries.

    Args:
        agent: The BlenderAgent instance
        print_result: Whether to print the result
    """
    console = agent.console
    console.print_header("=== Blender Interactive Mode ===")
    console.print_header(
        "Enter your 3D scene queries. Type 'exit', 'quit', or 'q' to exit."
    )

    while True:
        try:
            query = input("\nEnter Blender query: ")
            if query.lower() in ["exit", "quit", "q"]:
                console.print_header("Exiting Blender interactive mode.")
                break

            if query.strip():  # Process only non-empty queries
                agent.process_query(query)
                agent.display_result(print_result=print_result)

        except KeyboardInterrupt:
            console.print_header("\nBlender interactive mode interrupted. Exiting.")
            break
        except Exception as e:
            console.print_error(f"Error processing Blender query: {e}")


def handle_code_command(args):
    """
    Handle the Code agent command.

    Args:
        args: Parsed command line arguments for the code command
    """
    log = get_logger(__name__)

    # Set logging level to DEBUG if --debug flag is used
    if getattr(args, "debug", False):
        from gaia.logger import log_manager

        # Set root logger level first to ensure all handlers process DEBUG messages
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Update all existing loggers that start with "gaia"
        for logger_name in list(log_manager.loggers.keys()):
            if logger_name.startswith("gaia"):
                log_manager.loggers[logger_name].setLevel(logging.DEBUG)

        # Set default level for future loggers
        log_manager.set_level("gaia", logging.DEBUG)

        # Also ensure all handlers have DEBUG level
        for handler in root_logger.handlers:
            handler.setLevel(logging.DEBUG)

    if not CODE_AVAILABLE:
        log.error("Code agent is not available. Please check your installation.")
        return

    # Get base_url from args or environment
    base_url = getattr(args, "base_url", None)
    if base_url is None:
        base_url = os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)

    # Initialize Lemonade with code agent profile (32768 context)
    # Skip for remote servers (e.g., devtunnel URLs) or external APIs
    is_local = "localhost" in base_url or "127.0.0.1" in base_url
    if is_local:
        success, _, _ = initialize_lemonade_for_agent(
            agent="code",
            auto_start=True,
            skip_if_external=True,
            use_claude=getattr(args, "use_claude", False),
            use_chatgpt=getattr(args, "use_chatgpt", False),
        )
        if not success:
            sys.exit(1)

    try:
        # Import RoutingAgent for intelligent language detection
        from gaia.agents.routing.agent import RoutingAgent

        # Handle --path argument
        project_path = getattr(args, "path", None)
        if project_path:
            project_path = Path(project_path).expanduser().resolve()
            # Create directory if it doesn't exist
            project_path.mkdir(parents=True, exist_ok=True)
            project_path = str(project_path)
            log.debug(f"Using project path: {project_path}")

        # Get the query to analyze
        query = args.query if hasattr(args, "query") and args.query else None

        # Use RoutingAgent to determine language and project type
        if query:
            # Prepare agent configuration from CLI args
            agent_config = {
                "silent_mode": getattr(args, "silent", False),
                "debug": getattr(args, "debug", False),
                "show_prompts": getattr(args, "show_prompts", False),
                "max_steps": getattr(args, "max_steps", 100),
                "use_claude": getattr(args, "use_claude", False),
                "use_chatgpt": getattr(args, "use_chatgpt", False),
                "streaming": getattr(args, "streaming", False),
                "base_url": getattr(args, "base_url", None),
            }

            # Single query mode - use routing with configuration
            router = RoutingAgent(**agent_config)
            agent = router.process_query(query)
        else:
            # Interactive mode - start with default Python agent
            # User can still benefit from routing per query
            agent = CodeAgent(
                silent_mode=getattr(args, "silent", False),
                debug=getattr(args, "debug", False),
                show_prompts=getattr(args, "show_prompts", False),
                max_steps=getattr(args, "max_steps", 100),
                use_claude=getattr(args, "use_claude", False),
                use_chatgpt=getattr(args, "use_chatgpt", False),
                streaming=getattr(args, "streaming", False),
                base_url=getattr(args, "base_url", None),
            )

        # Handle list tools option
        if getattr(args, "list_tools", False):
            agent.list_tools(verbose=True)
            return

        # Handle interactive mode
        if getattr(args, "interactive", False):
            log.info("🤖 Code Agent Interactive Mode")
            log.info("Type 'exit' or 'quit' to end the session")
            log.info("Type 'help' for available commands\n")

            while True:
                try:
                    query = input("\ncode> ").strip()

                    if query.lower() in ["exit", "quit"]:
                        log.info("Goodbye!")
                        break

                    if query.lower() == "help":
                        print("\nAvailable commands:")
                        print("  Generate functions, classes, or tests")
                        print("  Analyze Python files")
                        print("  Validate Python syntax")
                        print("  Lint and format code")
                        print("  Edit files with diffs")
                        print("  Search for code patterns")
                        print("  Type 'exit' or 'quit' to end")
                        continue

                    if not query:
                        continue

                    # Process the query
                    result = agent.process_query(
                        query,
                        workspace_root=project_path,
                        max_steps=getattr(args, "max_steps", 100),
                        trace=args.trace,
                    )

                    # Display result
                    if not args.silent:
                        if result.get("status") == "success":
                            log.info(f"\n✅ {result.get('result', 'Task completed')}")
                        else:
                            log.error(f"\n❌ {result.get('result', 'Task failed')}")

                except KeyboardInterrupt:
                    print("\n\nInterrupted. Type 'exit' to quit.")
                    continue
                except Exception as e:
                    log.error(f"Error processing query: {e}")
                    if args.debug:
                        import traceback

                        traceback.print_exc()

        # Single query mode
        elif hasattr(args, "query") and args.query:
            result = agent.process_query(
                args.query,
                workspace_root=project_path,
                max_steps=args.max_steps,
                trace=args.trace,
                step_through=getattr(args, "step_through", False),
            )

            # Output result
            if args.silent:
                # In silent mode, output only JSON
                import json

                print(json.dumps(result, indent=2))
            else:
                # Display formatted result
                agent.display_result("Code Operation Result", result)

        else:
            # Default to interactive mode when no query provided
            log.info("Starting Code Agent interactive mode (type 'help' for commands)")

            while True:
                try:
                    query = input("\ncode> ").strip()

                    if query.lower() in ["exit", "quit"]:
                        log.info("Goodbye!")
                        break

                    if query.lower() == "help":
                        print("\nAvailable commands:")
                        print("  Generate functions, classes, or tests")
                        print("  Analyze Python files")
                        print("  Validate Python syntax")
                        print("  Lint and format code")
                        print("  Edit files with diffs")
                        print("  Search for code patterns")
                        print("  Type 'exit' or 'quit' to end")
                        continue

                    if not query:
                        continue

                    # Process the query
                    result = agent.process_query(
                        query,
                        workspace_root=project_path,
                        max_steps=getattr(args, "max_steps", 100),
                        trace=args.trace,
                    )

                    # Display result
                    if not args.silent:
                        if result.get("status") == "success":
                            log.info(f"\n✅ {result.get('result', 'Task completed')}")
                        else:
                            log.error(f"\n❌ {result.get('result', 'Task failed')}")

                except KeyboardInterrupt:
                    print("\n\nInterrupted. Type 'exit' to quit.")
                    continue
                except Exception as e:
                    log.error(f"Error processing query: {e}")
                    if getattr(args, "debug", False):
                        import traceback

                        traceback.print_exc()
            return

    except Exception as e:
        log.error(f"Error initializing Code agent: {e}")
        if getattr(args, "debug", False):
            import traceback

            traceback.print_exc()


def handle_jira_command(args):
    """
    Handle the Jira app command.

    Args:
        args: Parsed command line arguments for the jira command
    """
    log = get_logger(__name__)

    # Initialize Lemonade with jira agent profile (32768 context)
    success, _, _ = initialize_lemonade_for_agent(
        agent="jira",
        auto_start=True,
        skip_if_external=True,
        use_claude=getattr(args, "use_claude", False),
        use_chatgpt=getattr(args, "use_chatgpt", False),
    )
    if not success:
        sys.exit(1)

    try:
        # Import and use JiraApp directly (no MCP needed)
        from gaia.apps.jira.app import main as jira_main

        # Pass the arguments directly to the Jira app
        # The app expects certain arguments, so we need to ensure they're set
        if not hasattr(args, "verbose"):
            args.verbose = False
        if not hasattr(args, "debug"):
            args.debug = False
        if not hasattr(args, "model"):
            args.model = None

        # Run the Jira app's main function
        result = asyncio.run(jira_main(args))
        sys.exit(result)

    except ImportError as e:
        log.error(f"Failed to import Jira app: {e}")
        print("❌ Error: Jira app components are not available")
        print("Make sure GAIA is installed properly: pip install -e .")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error running Jira app: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def handle_docker_command(args):
    """
    Handle the Docker app command.

    Args:
        args: Parsed command line arguments for the docker command
    """
    log = get_logger(__name__)

    # Initialize Lemonade with docker agent profile (32768 context)
    success, _, _ = initialize_lemonade_for_agent(
        agent="docker",
        auto_start=True,
        skip_if_external=True,
        use_claude=getattr(args, "use_claude", False),
        use_chatgpt=getattr(args, "use_chatgpt", False),
    )
    if not success:
        sys.exit(1)

    try:
        # Import and use DockerApp directly
        from gaia.apps.docker.app import main as docker_main

        # Pass the arguments directly to the Docker app
        # The app expects certain arguments, so we need to ensure they're set
        if not hasattr(args, "verbose"):
            args.verbose = False
        if not hasattr(args, "debug"):
            args.debug = False
        if not hasattr(args, "model"):
            args.model = None
        if not hasattr(args, "directory"):
            args.directory = "."

        # Run the Docker app's main function
        result = asyncio.run(docker_main(args))
        sys.exit(result)

    except ImportError as e:
        log.error(f"Failed to import Docker app: {e}")
        print("❌ Error: Docker app components are not available")
        print("Make sure GAIA is installed properly: pip install -e .")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error running Docker app: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def handle_api_command(args):
    """
    Handle the API server command.

    Args:
        args: Parsed command line arguments for the api command
    """
    log = get_logger(__name__)

    if args.subcommand == "start":
        # Initialize Lemonade with mcp profile (unless --no-lemonade-check)
        if not getattr(args, "no_lemonade_check", False):
            success, _, _ = initialize_lemonade_for_agent(
                agent="mcp",
                auto_start=False,
                quiet=False,
            )
            if not success:
                return

        try:
            import uvicorn

            # Set environment variables BEFORE importing the app
            # This allows agent_registry.py to read them at import time
            if getattr(args, "debug", False):
                os.environ["GAIA_API_DEBUG"] = "1"
            if getattr(args, "show_prompts", False):
                os.environ["GAIA_API_SHOW_PROMPTS"] = "1"
            if getattr(args, "streaming", False):
                os.environ["GAIA_API_STREAMING"] = "1"
            if getattr(args, "step_through", False):
                os.environ["GAIA_API_STEP_THROUGH"] = "1"

            # Now import the app (agent_registry will see the env vars)
            from gaia.api.openai_server import app

            print("🚀 Starting GAIA OpenAI-compatible API server...")
            print(f"   Host: {args.host}")
            print(f"   Port: {args.port}")

            # Show debug features if enabled
            if (
                getattr(args, "debug", False)
                or getattr(args, "show_prompts", False)
                or getattr(args, "streaming", False)
                or getattr(args, "step_through", False)
            ):
                print("\n🐛 Debug features enabled:")
                if getattr(args, "debug", False):
                    print("   • Debug logging")
                if getattr(args, "show_prompts", False):
                    print("   • Show prompts")
                if getattr(args, "streaming", False):
                    print("   • LLM streaming")
                if getattr(args, "step_through", False):
                    print("   • Step-through mode")

            print("\n📍 API Endpoint:")
            print(f"   http://{args.host}:{args.port}/v1/chat/completions")
            print("\n💡 Configure VSCode GAIA extension to use:")
            print(f"   http://{args.host}:{args.port}")
            print("\nPress Ctrl+C to stop the server\n")

            # Set uvicorn log level based on debug flag
            log_level = "debug" if getattr(args, "debug", False) else "info"
            uvicorn.run(app, host=args.host, port=args.port, log_level=log_level)

        except ImportError as e:
            log.error(f"Failed to import API server: {e}")
            print("❌ Error: API server components are not available")
            print("Make sure uvicorn is installed: pip install uvicorn")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n✅ API server stopped")
            sys.exit(0)
        except Exception as e:
            log.error(f"Error running API server: {e}")
            print(f"❌ Error: {e}")
            sys.exit(1)

    elif args.subcommand == "status":
        # Check if server is running
        import socket

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((args.host, args.port))
            sock.close()

            if result == 0:
                print(f"✅ API server is running at http://{args.host}:{args.port}")
            else:
                print(f"❌ API server is not running at http://{args.host}:{args.port}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Error checking server status: {e}")
            sys.exit(1)

    elif args.subcommand == "stop":
        print(f"🛑 Stopping API server on port {args.port}...")
        try:
            kill_process_by_port(args.port)
            print("✅ API server stopped")
        except Exception as e:
            print(f"❌ Error stopping server: {e}")
            sys.exit(1)


def handle_visualize_command(args):
    """
    Handle the evaluation results visualizer command.

    Args:
        args: Parsed command line arguments for the visualize command
    """
    log = get_logger(__name__)

    try:
        import socket
        import webbrowser
    except ImportError as e:
        log.error(f"Failed to import required modules: {e}")
        print("❌ Error: Failed to import required modules for visualizer")
        return

    # Determine workspace and data directories
    # Check for GAIA_WORKSPACE environment variable
    workspace_env = os.getenv("GAIA_WORKSPACE")
    if args.workspace:
        workspace_dir = Path(args.workspace)
    elif workspace_env:
        workspace_dir = Path(workspace_env)
    else:
        workspace_dir = Path.cwd()
    experiments_dir = (
        Path(args.experiments_dir)
        if args.experiments_dir
        else workspace_dir / DEFAULT_EXPERIMENTS_DIR
    )
    evaluations_dir = (
        Path(args.evaluations_dir)
        if args.evaluations_dir
        else workspace_dir / DEFAULT_EVALUATIONS_DIR
    )
    test_data_dir = (
        Path(args.test_data_dir)
        if args.test_data_dir
        else workspace_dir / DEFAULT_TEST_DATA_DIR
    )
    groundtruth_dir = (
        Path(args.groundtruth_dir)
        if args.groundtruth_dir
        else workspace_dir / DEFAULT_GROUNDTRUTH_DIR
    )
    agent_outputs_dir = (
        Path(args.agent_outputs_dir) if args.agent_outputs_dir else workspace_dir
    )

    # Handle single agent output file
    single_agent_file = None
    if args.agent_output_file:
        single_agent_file = Path(args.agent_output_file)
        if not single_agent_file.exists():
            print(f"❌ Error: Agent output file not found: {single_agent_file}")
            return

    # Get the webapp directory
    webapp_dir = Path(__file__).parent / "eval" / "webapp"

    if not webapp_dir.exists():
        print("❌ Error: Evaluation webapp not found")
        print(f"Expected location: {webapp_dir}")
        return

    # Check if Node.js is available
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Error: Node.js is not installed or not in PATH")
        print("The evaluation visualizer requires Node.js to run.")
        print("Please install Node.js from https://nodejs.org/")
        return

    # Check if dependencies are installed
    node_modules_dir = webapp_dir / "node_modules"
    if not node_modules_dir.exists():
        print("📦 Installing webapp dependencies...")
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=webapp_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            print("✅ Dependencies installed successfully")
        except subprocess.CalledProcessError as e:
            print("❌ Error: Failed to install webapp dependencies")
            print(f"Error: {e.stderr}")
            return

    # Check if port is available
    def is_port_available(host, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            return result != 0

    if not is_port_available(args.host, args.port):
        print(f"❌ Error: Port {args.port} is already in use")
        print("Try using a different port with --port <port_number>")
        return

    # Set up environment variables for the webapp
    env = os.environ.copy()
    env["PORT"] = str(args.port)
    env["EXPERIMENTS_PATH"] = str(experiments_dir.absolute())
    env["EVALUATIONS_PATH"] = str(evaluations_dir.absolute())
    env["TEST_DATA_PATH"] = str(test_data_dir.absolute())
    env["GROUNDTRUTH_PATH"] = str(groundtruth_dir.absolute())
    env["AGENT_OUTPUTS_PATH"] = str(agent_outputs_dir.absolute())
    if single_agent_file:
        env["SINGLE_AGENT_FILE"] = str(single_agent_file.absolute())

    print("🚀 Starting evaluation results visualizer...")
    print(f"   Workspace: {workspace_dir.absolute()}")
    print(f"   Experiments: {experiments_dir.absolute()}")
    print(f"   Evaluations: {evaluations_dir.absolute()}")
    print(f"   Test Data: {test_data_dir.absolute()}")
    print(f"   Groundtruth: {groundtruth_dir.absolute()}")
    print(f"   Agent Outputs: {agent_outputs_dir.absolute()}")
    if single_agent_file:
        print(f"   Single Agent File: {single_agent_file.absolute()}")
    print(f"   Server: http://{args.host}:{args.port}")

    # Start the Node.js server
    try:
        server_process = subprocess.Popen(
            ["node", "server.js"],
            cwd=webapp_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        # Wait a moment for server to start
        time.sleep(2)

        # Check if server started successfully
        if server_process.poll() is not None:
            output, _ = server_process.communicate()
            print("❌ Error: Failed to start webapp server")
            print(f"Server output: {output}")
            return

        # Open browser if requested
        if not args.no_browser:
            url = f"http://{args.host}:{args.port}"
            try:
                webbrowser.open(url)
                # Browser opened automatically
            except Exception as e:
                print(f"⚠️  Could not open browser automatically: {e}")
                print(f"   Please open {url} manually")

        print("\n📊 Evaluation Results Visualizer is running!")
        print(f"   Access at: http://{args.host}:{args.port}")
        print("   Press Ctrl+C to stop the server")

        # Stream server output
        try:
            for line in iter(server_process.stdout.readline, ""):
                if line.strip():
                    print(f"[SERVER] {line.rstrip()}")
        except KeyboardInterrupt:
            log.debug("User interrupted server output streaming")
            raise

    except KeyboardInterrupt:
        print("\n⏹️  Stopping evaluation visualizer...")
    except Exception as e:
        log.error(f"Error running visualizer: {e}")
        print(f"❌ Error: {e}")
    finally:
        # Clean up server process
        try:
            if "server_process" in locals():
                server_process.terminate()
                server_process.wait(timeout=5)
                print("✅ Server stopped successfully")
        except subprocess.TimeoutExpired:
            server_process.kill()
            print("⚠️  Server force-killed")
        except Exception as e:
            print(f"⚠️  Error stopping server: {e}")


def handle_blender_command(args):
    """
    Handle the Blender agent command.

    Args:
        args: Parsed command line arguments for the blender command
    """
    log = get_logger(__name__)

    # Check if Blender components are available
    if not BLENDER_AVAILABLE:
        print("❌ Error: Blender agent components are not available")
        print("Install blender dependencies with: pip install -e .[blender]")
        sys.exit(1)

    # Initialize Lemonade with blender agent profile (32768 context)
    log.info("Initializing Lemonade for Blender agent...")
    success, _, _ = initialize_lemonade_for_agent(
        agent="blender",
        auto_start=True,
        skip_if_external=True,
        use_claude=getattr(args, "use_claude", False),
        use_chatgpt=getattr(args, "use_chatgpt", False),
    )
    if not success:
        sys.exit(1)

    # Check if Blender MCP server is running
    mcp_port = getattr(args, "mcp_port", 9876)
    log.info(f"Checking Blender MCP server connectivity on port {mcp_port}...")
    if not check_mcp_health(port=mcp_port):
        print_mcp_error()
        print(f"Note: Checking for MCP server on port {mcp_port}", file=sys.stderr)
        sys.exit(1)
    log.info("✅ Blender MCP server is accessible")

    # Create output directory if specified
    output_dir = args.output_dir
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        # Create MCP client with custom port if specified
        mcp_client = MCPClient(host="localhost", port=mcp_port)

        # Get base_url from args or environment
        base_url = getattr(args, "base_url", None)

        # Create the BlenderAgent
        agent = BlenderAgent(
            mcp=mcp_client,
            model_id=args.model,
            base_url=base_url,
            max_steps=args.steps,
            output_dir=output_dir,
            streaming=args.stream,
            show_stats=args.stats,
            debug_prompts=args.debug_prompts,
        )

        # Run in interactive mode if specified
        if args.interactive:
            run_blender_interactive_mode(agent, print_result=args.print_result)
        # Process a custom query if provided
        elif args.query:
            agent.console.print_header(f"Processing Blender query: '{args.query}'")
            agent.process_query(args.query)
            agent.display_result(print_result=args.print_result)
        # Run specific example if provided, otherwise run all examples
        else:
            run_blender_examples(
                agent, selected_example=args.example, print_result=args.print_result
            )

    except Exception as e:
        blender_log = get_logger(__name__)
        blender_log.error(f"Error running Blender agent: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def handle_cache_command(args):
    """Handle the cache management command.

    Args:
        args: Parsed command-line arguments
    """
    if not hasattr(args, "cache_action") or args.cache_action is None:
        print("❌ Error: No cache action specified")
        print("Available actions: status, clear")
        print("Run 'gaia cache --help' for more information")
        return

    from gaia.mcp.context7_cache import Context7Cache, Context7RateLimiter
    from gaia.mcp.external_services import Context7Service

    try:
        if args.cache_action == "status":
            # Check Context7 availability first
            is_available = Context7Service.check_availability()

            print("\n=== Context7 Service Status ===")
            if is_available:
                print("✓ Context7 is AVAILABLE (npx found, service working)")
            else:
                print("✗ Context7 is UNAVAILABLE (npx not found or service failed)")
                print("  The Code Agent will use embedded knowledge instead.")

            # Show cache and rate limiter status
            cache = Context7Cache()
            rate_limiter = Context7RateLimiter()

            status = rate_limiter.get_status()

            print("\n=== Context7 Cache Status ===")
            print(f"Cache directory: {cache.cache_dir}")
            # Use glob to count library ID files since _load_json is protected
            library_id_count = (
                len(list(cache.cache_dir.glob("library_*.json")))
                if hasattr(cache, "cache_dir")
                else 0
            )
            print(f"Library IDs cached: {library_id_count}")

            # Count documentation files
            doc_count = len(list(cache.docs_dir.glob("*.json")))
            print(f"Documentation entries: {doc_count}")

            print("\n=== Rate Limiter Status ===")
            print(
                f"Tokens available: {status['tokens_available']}/{status['max_tokens']}"
            )
            print(f"Circuit breaker: {'OPEN' if status['circuit_open'] else 'CLOSED'}")
            print(f"Consecutive failures: {status['consecutive_failures']}")

            if status["tokens_available"] < 5:
                print("\n⚠️  Warning: Low token count. Rate limiting may occur soon.")

            if not is_available:
                print("\n💡 To enable Context7:")
                print("   1. Install Node.js and npm")
                print("   2. Ensure 'npx' is in your PATH")
                print(
                    "   3. Optionally add CONTEXT7_API_KEY to .env for higher rate limits"
                )

        elif args.cache_action == "clear":
            # Clear caches
            if args.all or args.context7:
                cache = Context7Cache()
                cache.clear()
                print("✓ Context7 cache cleared")
            else:
                print("Specify --context7 or --all to clear caches")
                print("Run 'gaia cache clear --help' for more information")

    except Exception as e:
        cache_log = get_logger(__name__)
        cache_log.error(f"Error managing cache: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def handle_mcp_command(args):
    """
    Handle the MCP (Model Context Protocol) command.

    Args:
        args: Parsed command line arguments for the MCP command
    """
    log = get_logger(__name__)

    if not hasattr(args, "mcp_action") or args.mcp_action is None:
        print(
            "❌ No MCP action specified. Use 'gaia mcp --help' to see available actions."
        )
        return

    if args.mcp_action == "start":
        handle_mcp_start(args)
    elif args.mcp_action == "status":
        handle_mcp_status(args)
    elif args.mcp_action == "stop":
        handle_mcp_stop(args)
    elif args.mcp_action == "test":
        handle_mcp_test(args)
    elif args.mcp_action == "agent":
        handle_mcp_agent(args)
    elif args.mcp_action == "docker":
        handle_mcp_docker(args)
    else:
        log.error(f"Unknown MCP action: {args.mcp_action}")
        print(f"❌ Unknown MCP action: {args.mcp_action}")


def handle_mcp_start(args):
    """Start the MCP bridge server (HTTP-native implementation)."""
    log = get_logger(__name__)

    try:
        # Check if MCP dependencies are available (HTTP-native, no websockets needed)
        try:
            import aiohttp  # noqa: F401  # pylint: disable=unused-import
        except ImportError as e:
            log.error(f"MCP dependencies not installed: {e}")
            print("❌ Error: MCP dependencies not installed.")
            print("")
            print("To fix this, install the MCP dependencies:")
            print("  pip install -e .[mcp]")
            return

        # Import and start the HTTP-native MCP bridge
        from gaia.mcp.mcp_bridge import start_server as start_mcp_http

        # Initialize Lemonade with mcp agent profile (unless --no-lemonade-check)
        if not getattr(args, "no_lemonade_check", False):
            success, _, _ = initialize_lemonade_for_agent(
                agent="mcp",
                auto_start=False,  # MCP doesn't auto-start, just check
                quiet=False,
            )
            if not success:
                return
            print("")  # Add blank line before MCP output

        # Handle background mode
        if args.background:
            # Run in background mode
            log_file_path = os.path.abspath(args.log_file)

            # Check if MCP bridge is already running by checking port
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((args.host, args.port))
            sock.close()

            if result == 0:
                print(f"❌ MCP bridge is already running on {args.host}:{args.port}")
                print(f"📄 Check log file: {log_file_path}")
                print("📋 Use 'gaia mcp status' to verify or 'gaia mcp stop' to stop")
                return

            # Start background process - use gaia.cli module to ensure it works everywhere
            # This avoids PATH issues on Linux where 'gaia' command might not be available
            cmd_args = [
                sys.executable,
                "-m",
                "gaia.cli",
                "mcp",
                "start",
                "--host",
                args.host,
                "--port",
                str(args.port),
            ]

            # Add optional arguments if provided
            if args.base_url:
                cmd_args.extend(["--base-url", args.base_url])
            if args.auth_token:
                cmd_args.extend(["--auth-token", args.auth_token])
            if args.no_streaming:
                cmd_args.append("--no-streaming")
            if getattr(args, "verbose", False):
                cmd_args.append("--verbose")
            if getattr(args, "no_lemonade_check", False):
                cmd_args.append("--no-lemonade-check")

            print("🚀 Starting GAIA MCP Bridge in background")
            print(f"📍 Host: {args.host}:{args.port}")
            print(f"📄 Log file: {log_file_path}")

            # Write initial banner BEFORE starting subprocess (prevents truncation issues)
            import datetime

            ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            with open(log_file_path, "w", encoding="utf-8") as init_log:
                init_log.write(
                    f"{ts} | INFO | GAIA MCP Bridge started in background mode\n"
                )
                init_log.write(f"{ts} | INFO | Host: {args.host}:{args.port}\n")
                init_log.write(f"{ts} | INFO | Base URL: {args.base_url}\n")
                streaming = "disabled" if args.no_streaming else "enabled"
                init_log.write(f"{ts} | INFO | Streaming: {streaming}\n")
                init_log.write(f"{ts} | INFO | " + "=" * 60 + "\n")

            # Open for append - subprocess will add its output after banner
            log_handle = open(log_file_path, "a", encoding="utf-8")

            # Start the process
            if sys.platform.startswith("win"):
                # Windows
                process = subprocess.Popen(
                    cmd_args,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    cwd=os.getcwd(),
                    text=True,
                )
            else:
                # Unix-like systems
                process = subprocess.Popen(
                    cmd_args,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=os.getcwd(),
                    text=True,
                )

            # Write PID to dedicated PID file
            pid_file_path = os.path.abspath("gaia.mcp.pid")
            with open(pid_file_path, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(process.pid))

            # Append PID to log (banner was written before subprocess started)
            ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            log_handle.write(f"{ts} | INFO | Process ID: {process.pid}\n")
            log_handle.flush()

            print("✅ MCP bridge started in background")
            print(f"📍 Listening on: {args.host}:{args.port}")
            print(f"📄 Log file: {log_file_path}")
            print(f"🔢 Process ID: {process.pid}")
            print("📋 Use 'gaia mcp status' to check status")
            return

        # Run in foreground mode
        log.info("Starting GAIA MCP Bridge on %s:%s", args.host, args.port)
        print(f"🚀 Starting GAIA MCP Bridge on {args.host}:{args.port}")

        if args.auth_token:
            print("🔒 Authentication enabled")

        print(f"🔗 GAIA LLM server: {args.base_url}")
        print(f"📡 Streaming: {'disabled' if args.no_streaming else 'enabled'}")
        if getattr(args, "verbose", False):
            print("🔍 Verbose logging: enabled")
        print("")
        print("Press Ctrl+C to stop the server")

        # Start HTTP-native MCP bridge
        verbose = getattr(args, "verbose", False)
        start_mcp_http(
            host=args.host, port=args.port, base_url=args.base_url, verbose=verbose
        )

    except KeyboardInterrupt:
        log.info("MCP bridge stopped by user")
        print("\n✅ MCP bridge stopped")
    except Exception as e:
        log.error(f"Error starting MCP bridge: {e}")
        print(f"❌ Error starting MCP bridge: {e}")


def handle_mcp_stop(_args):
    """Stop the background MCP bridge server."""
    log = get_logger(__name__)

    try:
        # Note: os, sys, subprocess already imported at module level

        # Get PID from gaia.mcp.pid file
        pid_file_path = os.path.abspath("gaia.mcp.pid")

        if not os.path.exists(pid_file_path):
            print("❌ No MCP bridge PID file found")
            print("📋 Use 'gaia mcp status' to check if server is running")
            return

        try:
            with open(pid_file_path, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
        except (ValueError, IOError) as e:
            print(f"❌ Error reading PID file: {e}")
            return

        # Stop the process
        try:
            if sys.platform.startswith("win"):
                # Windows - check if process exists first
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if str(pid) in result.stdout:
                    print(f"🔄 Stopping MCP bridge process (PID: {pid})")
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True)
                    print(f"✅ Stopped MCP bridge (PID: {pid})")
                else:
                    print(f"⚠️  Process {pid} was not running")
            else:
                # Unix-like systems
                try:
                    print(f"🔄 Stopping MCP bridge process (PID: {pid})")
                    os.kill(pid, 15)  # SIGTERM
                    print(f"✅ Stopped MCP bridge (PID: {pid})")
                except OSError:
                    print(f"⚠️  Process {pid} was not running")

        except subprocess.CalledProcessError:
            print(f"❌ Failed to stop process {pid}")
        except Exception as e:
            print(f"❌ Error stopping process {pid}: {e}")

        # Clean up PID file
        try:
            os.remove(pid_file_path)
            print("🧹 Cleaned up PID file")
        except OSError as e:
            log.debug(f"Could not remove PID file: {e}")

    except Exception as e:
        log.error(f"Error stopping MCP bridge: {e}")
        print(f"❌ Error stopping MCP bridge: {e}")


def handle_mcp_status(args):
    """Check MCP server status (HTTP-native)."""
    log = get_logger(__name__)

    try:
        import socket

        print(f"🔍 Checking MCP server status at {args.host}:{args.port}")

        # Test connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((args.host, args.port))
        sock.close()

        if result == 0:
            print(f"✅ MCP server is running and accessible at {args.host}:{args.port}")

            # Try the new /status endpoint for comprehensive details
            try:
                import json
                import urllib.request

                # First try the new /status endpoint
                status_url = f"http://{args.host}:{args.port}/status"
                try:
                    with urllib.request.urlopen(status_url, timeout=3) as response:
                        data = json.loads(response.read().decode())

                        if data.get("status") == "healthy":
                            print("✅ MCP server is fully operational (HTTP)")
                            print(
                                f"   Service: {data.get('service', 'GAIA MCP Bridge')}"
                            )
                            print(f"   Version: {data.get('version', 'Unknown')}")
                            print(
                                f"   LLM Backend: {data.get('llm_backend', 'Unknown')}"
                            )

                            # Display agents
                            agents = data.get("agents", {})
                            print(f"\n📦 Agents ({len(agents)}):")
                            for name, info in agents.items():
                                print(
                                    f"   • {name}: {info.get('description', 'No description')}"
                                )
                                capabilities = info.get("capabilities", [])
                                if capabilities:
                                    print(
                                        f"     Capabilities: {', '.join(capabilities)}"
                                    )

                            # Display tools
                            tools = data.get("tools", {})
                            print(f"\n🔧 Tools ({len(tools)}):")
                            for name, info in tools.items():
                                print(
                                    f"   • {name}: {info.get('description', 'No description')}"
                                )

                            # Display endpoints
                            endpoints = data.get("endpoints", {})
                            if endpoints:
                                print("\n📍 Available Endpoints:")
                                for _, desc in endpoints.items():
                                    print(f"   • {desc}")
                        else:
                            print("⚠️  Server is running but may not be healthy")
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        # Fall back to /health for older versions
                        health_url = f"http://{args.host}:{args.port}/health"
                        with urllib.request.urlopen(health_url, timeout=3) as response:
                            data = json.loads(response.read().decode())
                            if data.get("status") == "healthy":
                                print("✅ MCP server is fully operational (HTTP)")
                                print(
                                    f"   Service: {data.get('service', 'GAIA MCP Bridge')}"
                                )
                                print(f"   Agents: {data.get('agents', 0)}")
                                print(f"   Tools: {data.get('tools', 0)}")
                                print(
                                    "\n💡 Note: Update the MCP bridge for detailed status information"
                                )
                            else:
                                print("⚠️  Server is running but may not be healthy")
                    else:
                        raise
                except urllib.error.URLError:
                    print("⚠️  Server is running but status endpoint not accessible")
                    print("   Server may be starting up or using an older version")
            except Exception as e:
                log.debug(f"Cannot perform detailed status check: {e}")
                print("⚠️  Cannot perform detailed status check")
        else:
            print(f"❌ MCP server is not accessible at {args.host}:{args.port}")
            print("   Make sure the server is running with: gaia mcp start")

    except Exception as e:
        log.error(f"Error checking MCP status: {e}")
        print(f"❌ Error checking MCP status: {e}")


def handle_mcp_test(args):
    """Test MCP bridge functionality (HTTP-native)."""
    log = get_logger(__name__)

    try:
        import json
        import urllib.parse
        import urllib.request

        print(f"🧪 Testing MCP bridge at {args.host}:{args.port}")
        print(f"📝 Test query: {args.query}")
        print(f"🔧 Tool: {args.tool}")

        # First check if server is running
        health_url = f"http://{args.host}:{args.port}/health"
        try:
            with urllib.request.urlopen(health_url, timeout=3) as response:
                health_data = json.loads(response.read().decode())
                if health_data.get("status") == "healthy":
                    print("✅ MCP server is healthy")
                else:
                    print("⚠️  Server may not be fully operational")
        except urllib.error.URLError:
            print(f"❌ Cannot connect to MCP server at {args.host}:{args.port}")
            print("   Make sure the server is running with: gaia mcp start")
            return

        # Test the actual tool call via HTTP POST
        try:
            # Prepare the JSON-RPC request
            rpc_request = {
                "jsonrpc": "2.0",
                "id": "test-1",
                "method": "tools/call",
                "params": {"name": args.tool, "arguments": {"query": args.query}},
            }

            # Send POST request
            url = f"http://{args.host}:{args.port}/"
            data = json.dumps(rpc_request).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode())

                if "result" in result:
                    print("✅ Query executed successfully")
                    content = result["result"].get("content", [])
                    if content and len(content) > 0:
                        if isinstance(content[0], dict) and "text" in content[0]:
                            response_text = content[0]["text"]
                            try:
                                response_data = json.loads(response_text)
                                print(
                                    f"💬 Response: {response_data.get('response', response_text)}"
                                )
                            except json.JSONDecodeError:
                                print(f"💬 Response: {response_text}")
                        else:
                            print(f"💬 Response: {content}")
                    else:
                        print("⚠️  No response content received")
                elif "error" in result:
                    print(f"❌ Query failed: {result['error']}")
                else:
                    print("❌ Unexpected response format")

        except urllib.error.HTTPError as e:
            print(f"❌ HTTP Error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            print(f"❌ Connection error: {e.reason}")
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON response: {e}")
        except Exception as e:
            print(f"❌ Test failed: {e}")

    except Exception as e:
        log.error(f"Error running MCP test: {e}")
        print(f"❌ Error running MCP test: {e}")


def handle_mcp_agent(args):
    """Test MCP orchestrator agent functionality (HTTP-native)."""
    log = get_logger(__name__)

    try:
        import json
        import urllib.parse
        import urllib.request

        print(f"🤖 Testing MCP orchestrator agent at {args.host}:{args.port}")
        print(f"📝 Agent request: {args.request}")
        print(f"🎯 Target domain: {args.domain}")
        if args.context:
            print(f"💭 Context: {args.context}")

        # First check if server is running
        health_url = f"http://{args.host}:{args.port}/health"
        try:
            with urllib.request.urlopen(health_url, timeout=3) as response:
                health_data = json.loads(response.read().decode())
                if health_data.get("status") == "healthy":
                    print("✅ MCP server is healthy")
                else:
                    print("⚠️  Server may not be fully operational")
        except urllib.error.URLError:
            print(f"❌ Cannot connect to MCP server at {args.host}:{args.port}")
            print("   Make sure the server is running with: gaia mcp start")
            return

        # Test agent call via HTTP POST
        try:
            # Prepare agent arguments
            agent_arguments = {"request": args.request, "domain": args.domain}
            if args.context:
                agent_arguments["context"] = args.context

            # Prepare the JSON-RPC request
            rpc_request = {
                "jsonrpc": "2.0",
                "id": "agent-test-1",
                "method": "tools/call",
                "params": {"name": "gaia.agent", "arguments": agent_arguments},
            }

            # Send POST request
            url = f"http://{args.host}:{args.port}/"
            data = json.dumps(rpc_request).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )

            print("🔄 Agent is analyzing request and orchestrating tools...")

            with urllib.request.urlopen(req, timeout=60) as response:
                agent_data = json.loads(response.read().decode())

                if "result" in agent_data:
                    print("✅ Agent executed successfully")
                    content = agent_data["result"].get("content", [])
                    if content and len(content) > 0:
                        if isinstance(content[0], dict) and "text" in content[0]:
                            try:
                                result = json.loads(content[0]["text"])

                                print("\n🎯 Agent Results:")
                                print(f"  Domain: {result.get('domain', 'unknown')}")
                                print(
                                    f"  Workflow Steps: {result.get('workflow_steps', 0)}"
                                )
                                print(f"  Success: {result.get('success', False)}")

                                # Display agent workflow results
                                agent_results = result.get("agent_results", {})
                                workflow_results = agent_results.get(
                                    "workflow_results", {}
                                )

                                if workflow_results:
                                    print("\n📋 Workflow Execution Details:")
                                    for (
                                        step_key,
                                        step_result,
                                    ) in workflow_results.items():
                                        print(
                                            f"  {step_key}: {step_result.get('description', 'Unknown step')}"
                                        )
                                        if "error" in step_result:
                                            print(
                                                f"    ❌ ERROR: {step_result['error']}"
                                            )
                                        else:
                                            print(
                                                f"    ✅ SUCCESS: {step_result.get('tool', 'unknown')} completed"
                                            )
                            except json.JSONDecodeError:
                                print(f"💬 Response: {content[0]['text']}")
                        else:
                            print(f"💬 Response: {content}")
                    else:
                        print("⚠️  No response content received")
                elif "error" in agent_data:
                    print(f"❌ Agent execution failed: {agent_data['error']}")
                else:
                    print("❌ Unexpected response format")

        except urllib.error.HTTPError as e:
            print(f"❌ HTTP Error: {e.code} {e.reason}")
        except urllib.error.URLError as e:
            print(f"❌ Connection error: {e.reason}")
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON response: {e}")
        except Exception as e:
            print(f"❌ Agent test failed: {e}")

    except Exception as e:
        log.error(f"Error running MCP agent test: {e}")
        print(f"❌ Error running MCP agent test: {e}")


def handle_mcp_docker(args):
    """Start the Docker MCP server (per-agent architecture)."""
    log = get_logger(__name__)

    try:
        from gaia.mcp.servers.docker_mcp import start_docker_mcp

        print("=" * 60)
        print("🐳 GAIA Docker MCP Server")
        print("=" * 60)
        print(f"Starting on {args.host}:{args.port}")
        if args.verbose:
            print("🔍 Verbose mode: ENABLED")
        print("\nPress Ctrl+C to stop")
        print("=" * 60)

        # Start the Docker MCP server
        start_docker_mcp(
            port=args.port,
            host=args.host,
            verbose=args.verbose,
        )

    except KeyboardInterrupt:
        print("\n✅ Docker MCP server stopped")
    except ImportError as e:
        log.error(f"Failed to import Docker MCP server: {e}")
        print("❌ Error: Could not load Docker MCP server")
        print(f"   {e}")
    except Exception as e:
        log.error(f"Error starting Docker MCP server: {e}")
        print(f"❌ Error starting Docker MCP server: {e}")


if __name__ == "__main__":
    main()
