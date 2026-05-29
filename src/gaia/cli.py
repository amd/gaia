# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from gaia.agents.base.console import AgentConsole
from gaia.llm import create_client
from gaia.llm.lemonade_client import (
    DEFAULT_HOST,
    DEFAULT_LEMONADE_URL,
    DEFAULT_MODEL_NAME,
    DEFAULT_PORT,
    LemonadeClient,
    LemonadeClientError,
    _get_lemonade_config,
)
from gaia.logger import get_logger
from gaia.perf_analysis import run_perf_visualization
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

# Load environment variables from .env file
load_dotenv()

# Set debug level for the logger
logging.getLogger("gaia").setLevel(logging.INFO)

# Add the parent directory to sys.path to import gaia modules
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent.parent.parent
sys.path.append(str(parent_dir))


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


def initialize_lemonade_for_agent(
    agent: str,
    quiet: bool = False,
    skip_if_external: bool = False,
    use_claude: bool = False,
    use_chatgpt: bool = False,
    host: str | None = None,
    port: int | None = None,
    base_url: str | None = None,
):
    """
    Initialize Lemonade Server for a specific GAIA agent.

    Uses LemonadeManager singleton shared by CLI and SDK for consistent
    initialization and error handling.

    Args:
        agent: Agent name (chat, code, talk, rag, blender, jira, docker, vlm, minimal, mcp)
        quiet: Suppress output (only errors)
        skip_if_external: If True, skip initialization when using Claude/ChatGPT
        use_claude: Whether Claude API is being used
        use_chatgpt: Whether ChatGPT API is being used
        host: Host address of the Lemonade server (defaults to LEMONADE_BASE_URL env var)
        port: Port number of the Lemonade server (defaults to LEMONADE_BASE_URL env var)
        base_url: Full base URL for the Lemonade server (e.g., https://abc.ngrok-free.app).
                  When provided, takes priority over host/port.

    Returns:
        Tuple of (success: bool, base_url: str | None)

    Note:
        Host and port can be configured via LEMONADE_BASE_URL environment variable,
        or pass a full URL via base_url for remote servers (e.g., ngrok).

    Example:
        success, base_url = initialize_lemonade_for_agent("chat")
        if not success:
            sys.exit(1)
    """
    from gaia.llm.lemonade_manager import LemonadeManager

    log = get_logger(__name__)

    # Use provided base_url, or host/port, or get from env var, or use defaults
    env_host, env_port, env_base_url = _get_lemonade_config()

    # If base_url is provided (e.g., --base-url), use it directly
    # This preserves https:// URLs (e.g., ngrok) without mangling
    if base_url is None:
        host = host if host is not None else env_host
        port = port if port is not None else env_port

    # Skip initialization if using external API
    if skip_if_external and (use_claude or use_chatgpt):
        return True, base_url or env_base_url

    # Map agent names to context size requirements.
    # `chat` and `rag` need 64K so doc-Q&A flows (system prompt + RAG
    # retrieval + tool result + history) don't crush the window —
    # `summarize_document` was hitting context overflow on 1-2 MB PDFs
    # at the previous 32K default (#1030 follow-up). Users on tight RAM
    # can override with the ``GAIA_CTX_SIZE`` env var.
    agent_context_sizes = {
        "code": 32768,
        "chat": 65536,
        "code_index": 32768,
        "jira": 32768,
        "blender": 32768,
        "docker": 32768,
        "talk": 32768,
        "rag": 65536,
        "email": 32768,  # email agent (#962) — needs room for body + thread context
        "sd": 8192,  # SD agent needs 8K for image + story workflow
        "mcp": 4096,
        "minimal": 4096,
        "vlm": 8192,
    }
    required_ctx = agent_context_sizes.get(agent.lower(), 32768)

    # Env-var override: lets users on lower-memory hardware dial back
    # (or, in advanced cases, push higher up to the model's 128K max).
    # Honors any positive integer; values lower than the requested ctx
    # still load — the user is explicitly taking the trade-off.
    _ctx_override = os.environ.get("GAIA_CTX_SIZE", "").strip()
    if _ctx_override:
        try:
            _ctx_int = int(_ctx_override)
            if _ctx_int > 0:
                log.info(
                    "GAIA_CTX_SIZE=%d overriding agent '%s' default of %d",
                    _ctx_int,
                    agent,
                    required_ctx,
                )
                required_ctx = _ctx_int
        except ValueError:
            log.warning(
                "GAIA_CTX_SIZE=%r is not a positive integer; ignoring",
                _ctx_override,
            )

    # LemonadeManager handles all validation and error printing
    # Pass base_url directly when provided to preserve full URL (https, ngrok, etc.)
    try:
        if base_url:
            success = LemonadeManager.ensure_ready(
                min_context_size=required_ctx,
                quiet=quiet,
                base_url=base_url,
            )
        else:
            success = LemonadeManager.ensure_ready(
                min_context_size=required_ctx,
                quiet=quiet,
                host=host,
                port=port,
            )
    except LemonadeClientError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return False, None

    if not success:
        return False, None

    # Get base_url from LemonadeManager
    resolved_url = LemonadeManager.get_base_url()
    if resolved_url is None:
        if base_url:
            resolved_url = base_url
        else:
            resolved_url = f"http://{host}:{port}/api/v1"
    return True, resolved_url


def ensure_agent_models(
    agent: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    quiet: bool = False,
    _timeout: int = 1800,  # Reserved for future use
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

        # Use AgentConsole for nicely formatted progress display
        console = AgentConsole()

        if not quiet:
            console.print_info(
                f"Downloading {len(models_to_download)} model(s) for {agent} agent"
            )

        # Download each model with progress display
        for model_id in models_to_download:
            if not quiet:
                console.print_download_start(model_id)

            try:
                event_count = 0
                last_bytes = 0
                last_time = time.time()

                for event in client.pull_model_stream(model_name=model_id):
                    event_count += 1
                    event_type = event.get("event")

                    if event_type == "progress":
                        # Skip first 2 spurious events from Lemonade
                        if event_count <= 2 or quiet:
                            continue

                        # Calculate download speed
                        current_bytes = event.get("bytes_downloaded", 0)
                        current_time = time.time()
                        time_delta = current_time - last_time

                        speed_mbps = 0.0
                        if time_delta > 0.1 and current_bytes > last_bytes:
                            bytes_delta = current_bytes - last_bytes
                            speed_mbps = (bytes_delta / time_delta) / (1024 * 1024)
                            last_bytes = current_bytes
                            last_time = current_time

                        console.print_download_progress(
                            percent=event.get("percent", 0),
                            bytes_downloaded=current_bytes,
                            bytes_total=event.get("bytes_total", 0),
                            speed_mbps=speed_mbps,
                        )

                    elif event_type == "complete":
                        if not quiet:
                            console.print_download_complete(model_id)

                    elif event_type == "error":
                        if not quiet:
                            console.print_download_error(
                                event.get("error", "Unknown error"), model_id
                            )
                        log.error(f"Failed to download {model_id}")
                        return False

            except LemonadeClientError as e:
                log.error(f"Failed to download {model_id}: {e}")
                if not quiet:
                    console.print_download_error(str(e), model_id)
                return False

        if not quiet:
            console.print_success(f"All models ready for {agent} agent")

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
        self.llm_client = create_client("lemonade", model=model)

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
            from gaia.chat.sdk import AgentConfig, AgentSDK

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
                # Interactive mode using AgentSDK
                config = AgentConfig(**config_kwargs)
                chat = AgentSDK(config)
                asyncio.run(chat.start_interactive_session())
            else:
                # Single message mode with streaming
                config = AgentConfig(**config_kwargs)
                chat = AgentSDK(config)
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
        "browse": "chat",
        "analyze": "chat",
        "talk": "talk",
        "stats": "minimal",
    }

    # Initialize Lemonade with agent-specific profile
    lemonade_base_url = kwargs.get("base_url")  # May be None if not specified
    if action in action_to_agent and not kwargs.get("no_lemonade_check", False):
        agent_profile = action_to_agent[action]
        use_claude = kwargs.get("use_claude", False)
        use_chatgpt = kwargs.get("use_chatgpt", False)

        success, detected_base_url = initialize_lemonade_for_agent(
            agent=agent_profile,
            skip_if_external=True,
            use_claude=use_claude,
            use_chatgpt=use_chatgpt,
            base_url=lemonade_base_url,
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
        # Filter out parameters that are not accepted by GaiaCliClient
        # GaiaCliClient only accepts: model, max_tokens, show_stats, logging_level
        audio_params = {
            "whisper_model_size",
            "audio_device_index",
            "silence_threshold",
            "no_tts",
        }
        llm_provider_params = {
            "use_claude",
            "use_chatgpt",
            "claude_model",
            "base_url",
        }
        cli_params = {
            "action",
            "message",
            "stats",
            "assistant_name",
            "stream",
            "no_lemonade_check",
            "list_tools",
        }
        excluded_params = cli_params | audio_params | llm_provider_params
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

            # Resolve device to model_id when --device is set and --model is not.
            # GPU is the default when no --device is specified.
            # Fallback policy:
            #   - Explicit --device: fail loudly if unavailable (no fallback)
            #   - Default (no --device): GPU default, fallback to CPU with warning
            explicit_model = kwargs.get("model", None)
            device = kwargs.get("device", None)
            device_was_explicit = device is not None
            effective_device = device or "gpu"

            # Check device availability when Lemonade is reachable
            if not explicit_model:
                from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

                try:
                    _dev_client = LemonadeClient(verbose=False)
                    _sysinfo = _dev_client.get_system_info()
                    _devices = _sysinfo.get("devices", {})

                    if effective_device == "npu":
                        npu_info = _devices.get("amd_npu", {})
                        if not npu_info.get("available"):
                            if device_was_explicit:
                                print(
                                    "❌ NPU not available on this system. "
                                    "Requires Ryzen AI 300/400/Max (XDNA2).",
                                    file=sys.stderr,
                                )
                                sys.exit(1)
                            effective_device = "gpu"
                    elif effective_device == "gpu":
                        has_gpu = any(
                            "gpu" in k.lower()
                            and isinstance(v, dict)
                            and v.get("available")
                            for k, v in _devices.items()
                        )
                        if not has_gpu and not device_was_explicit:
                            effective_device = "cpu"
                except Exception:
                    pass  # Lemonade not running yet; proceed with requested device

                for dc in DEFAULT_DEVICE_CONFIGS:
                    if dc.device == effective_device:
                        explicit_model = dc.model
                        break

            # Always announce which device the agent will run on.
            device_labels = {"cpu": "CPU", "gpu": "GPU", "npu": "NPU (Ryzen AI)"}
            device_label = device_labels.get(effective_device, effective_device.upper())
            print(f"🖥️  Device: {device_label}  |  Model: {explicit_model or 'auto'}")
            if effective_device == "cpu":
                print(
                    "   ⚠️  Running on CPU — expect significantly slower response "
                    "times. Use 'gaia init' to set up GPU acceleration."
                )
            if effective_device == "npu":
                print("   ℹ️  NPU mode requires: gaia init --profile npu")

            # Create configuration with CLI values
            config = ChatAgentConfig(
                use_claude=kwargs.get("use_claude", False),
                use_chatgpt=kwargs.get("use_chatgpt", False),
                claude_model=kwargs.get("claude_model", "claude-sonnet-4-20250514"),
                base_url=kwargs.get(
                    "base_url",
                    os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL),
                ),
                model_id=explicit_model,
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
                mcp_tool_limit=kwargs.get("mcp_tool_limit", 50),
            )

            # Create Chat Agent with configuration
            agent = ChatAgent(config)

            # Create initial session if not loading one
            if not agent.current_session:
                agent.current_session = agent.session_manager.create_session()
                # Reset tool loader session state on new session
                try:
                    if hasattr(agent, "tool_loader"):
                        agent.tool_loader.reset_session()
                except Exception:
                    pass
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

            # First-boot: if no profile entries exist, offer onboarding intro
            from gaia.agents.base.memory_store import MemoryStore as _BootMS

            _boot_store = _BootMS()
            try:
                _has_profile = bool(
                    _boot_store.get_by_category("profile", context="global", limit=1)
                )
            finally:
                _boot_store.close()
            if not _has_profile:
                print("\n" + "=" * 60)
                print("  First time with GAIA? Let's set up your profile.")
                print("=" * 60)
                try:
                    run_first_boot = (
                        input("  Quick intro? Takes ~1 minute. [Y/n]: ").strip().lower()
                    )
                except (EOFError, KeyboardInterrupt):
                    run_first_boot = "n"
                if run_first_boot != "n":
                    _bootstrap_chat()
                    print()

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
    elif action in ("browse", "analyze"):
        if action == "browse":
            from gaia.agents.browser.agent import BrowserAgent, BrowserAgentConfig

            agent = BrowserAgent(
                BrowserAgentConfig(
                    use_claude=kwargs.get("use_claude", False),
                    use_chatgpt=kwargs.get("use_chatgpt", False),
                    claude_model=kwargs.get("claude_model", "claude-sonnet-4-20250514"),
                    base_url=kwargs.get("base_url"),
                    model_id=kwargs.get("model", None),
                    max_steps=kwargs.get("max_steps", 100),
                    streaming=kwargs.get("stream", False),
                    show_prompts=kwargs.get("show_prompts", False),
                    show_stats=kwargs.get("show_stats", False),
                    silent_mode=not (
                        kwargs.get("debug", False) or kwargs.get("list_tools", False)
                    ),
                    debug=kwargs.get("debug", False),
                    allowed_paths=kwargs.get("allowed_paths", None),
                )
            )
        else:
            from gaia.agents.analyst.agent import AnalystAgent, AnalystAgentConfig

            agent = AnalystAgent(
                AnalystAgentConfig(
                    use_claude=kwargs.get("use_claude", False),
                    use_chatgpt=kwargs.get("use_chatgpt", False),
                    claude_model=kwargs.get("claude_model", "claude-sonnet-4-20250514"),
                    base_url=kwargs.get("base_url"),
                    model_id=kwargs.get("model", None),
                    max_steps=kwargs.get("max_steps", 100),
                    streaming=kwargs.get("stream", False),
                    show_prompts=kwargs.get("show_prompts", False),
                    show_stats=kwargs.get("show_stats", False),
                    silent_mode=not (
                        kwargs.get("debug", False) or kwargs.get("list_tools", False)
                    ),
                    debug=kwargs.get("debug", False),
                    allowed_paths=kwargs.get("allowed_paths", None),
                )
            )

        try:
            if kwargs.get("list_tools", False):
                agent.list_tools(verbose=True)
                return 0

            query = kwargs.get("query")
            if query:
                result = agent.process_query(query, trace=kwargs.get("trace", False))
                if kwargs.get("show_stats", False) and result.get("duration"):
                    agent.console.display_stats(result)
                return 0 if result["status"] == "success" else 1

            print(f"Starting {agent.__class__.__name__}. Type /quit to exit.")
            while True:
                user_input = input("\nYou: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in {"/quit", "/exit"}:
                    return 0
                agent.process_query(user_input, trace=kwargs.get("trace", False))
        finally:
            if hasattr(agent, "close"):
                agent.close()
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
            mic_threshold=kwargs.get("mic_threshold", 0.003),
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


def _ensure_webui_built(log=None):
    """Rebuild the Agent UI frontend if source files are newer than dist."""
    from gaia.ui.build import ensure_webui_built

    ensure_webui_built(
        log_fn=log.info if log else print,
        warn_fn=log.warning if log else print,
    )


def _launch_agent_ui(port=4200, base_url=None, log=None, debug=False, webui_dist=None):
    """Launch the Agent UI server (FastAPI + uvicorn).

    Reused by top-level --ui, gaia chat --ui, and the interactive menu.
    """
    if log is None:
        log = get_logger(__name__)

    _ensure_webui_built(log=log)

    try:
        from gaia.ui.server import create_app

        # Forward --base-url to the UI server via environment variable
        if base_url:
            os.environ["LEMONADE_BASE_URL"] = base_url
            log.info(f"Using remote Lemonade server: {base_url}")
            print(f"Remote Lemonade server: {base_url}")

        log.info(f"Starting GAIA Agent UI on http://localhost:{port}")
        print(f"Starting GAIA Agent UI on http://localhost:{port}")
        print(f"   Open your browser to http://localhost:{port}")
        print("   Press Ctrl+C to stop")
        print()
        if not base_url:
            print("   Prerequisites:")
            print(
                "     1. Models downloaded  : gaia init --profile chat  (first time only, ~25 GB)"
            )
            print("     2. Lemonade running   : lemonade-server serve")
            print()

        import uvicorn

        app = create_app(webui_dist=webui_dist)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            log_level="debug" if debug else "info",
            access_log=debug,
        )
    except ImportError as e:
        print(f"\nMissing dependencies for Agent UI: {e}")
        print("\n   The Agent UI requires extra dependencies that are not installed.")
        print("   Install them with:\n")
        print('     uv pip install -e ".[ui]"')
        print("\n   Or if you installed from PyPI:\n")
        print("     pip install amd-gaia[ui]")
        print()
        sys.exit(1)
    except OSError as e:
        err_str = str(e).lower()
        # Windows WSAEADDRINUSE (10048) or WSAEACCES (10013) — port already in use
        if (
            "10048" in str(e)
            or "10013" in str(e)
            or "address already in use" in err_str
        ):
            print(f"\nPort {port} is already in use.")
            print(f"   Another process is already listening on port {port}.")
            print("   Try a different port:")
            print("     gaia chat --ui --ui-port 8080")
        else:
            log.error(f"Error starting Agent UI: {e}")
            print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error starting Agent UI: {e}")
        print(f"Error: {e}")
        sys.exit(1)


def _launch_interactive_cli(log=None):
    """Launch the interactive CLI chat with default configuration.

    Reused by top-level --cli and the interactive menu.
    """
    if log is None:
        log = get_logger(__name__)

    try:
        success, base_url = initialize_lemonade_for_agent("chat")
        if not success:
            sys.exit(1)

        from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
        from gaia.agents.chat.app import interactive_mode

        config = ChatAgentConfig(
            base_url=base_url or os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL),
            silent_mode=True,
        )
        agent = ChatAgent(config)

        if not agent.current_session:
            agent.current_session = agent.session_manager.create_session()
            # Reset tool loader session state on new session
            try:
                if hasattr(agent, "tool_loader"):
                    agent.tool_loader.reset_session()
            except Exception:
                pass

        interactive_mode(agent)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        log.error(f"Error in chat: {e}", exc_info=True)
        print(f"Error: {e}")
        sys.exit(1)


def _show_interactive_menu(log=None):
    """Show an interactive menu when `gaia` is run with no arguments."""
    if log is None:
        log = get_logger(__name__)

    print()
    print("========================================")
    print(f"  GAIA {version}")
    print("  Build AI Agents That Run Locally")
    print("========================================")
    print()
    print("  [1] Agent UI  — Desktop chat interface (browser)")
    print("  [2] CLI Chat  — Interactive terminal chat")
    print("  [3] Help      — Show all commands")
    print()

    try:
        choice = input("  Select [1/2/3]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return

    if choice == "1":
        _launch_agent_ui(log=log)
    elif choice == "2":
        _launch_interactive_cli(log=log)
    elif choice == "3":
        print()
        print("  Usage: gaia [--ui | --cli | <command>]")
        print()
        print("  Quick start:")
        print("    gaia                   Launch Agent UI (default)")
        print("    gaia --ui              Launch Agent UI (explicit)")
        print("    gaia --ui-port 8080    Agent UI on custom port")
        print("    gaia --cli             Interactive CLI chat")
        print()
        print("  Commands:")
        print("    gaia chat              Interactive chat with RAG")
        print("    gaia chat --ui         Agent UI (alias for gaia --ui)")
        print('    gaia prompt "Hello"    Single prompt to LLM')
        print("    gaia talk              Voice interaction")
        print("    gaia init              Setup Lemonade + models")
        print("    gaia code              Code generation agent")
        print()
        print("  Run 'gaia --help' for the full command list.")
    else:
        print(f"  Unknown option: {choice}")
        print("  Run 'gaia --help' for all commands.")


def _print_reliability_summary(scorecards, pass_threshold=0.90):
    """Print a reliability summary table from multiple eval iteration scorecards.

    Groups scenario results across iterations and computes per-scenario pass rates.
    Prints a colorized table and a GO/NO_GO readiness signal.
    """
    # Collect per-scenario results across all iterations
    by_scenario = defaultdict(list)
    for sc in scorecards:
        if not sc:
            continue
        for result in sc.get("scenarios", []):
            sid = result.get("scenario_id", "unknown")
            by_scenario[sid].append(result.get("status", "ERRORED"))

    if not by_scenario:
        print("\n[RELIABILITY] No scenario results to aggregate.")
        return

    n_iterations = sum(1 for sc in scorecards if sc)

    # Compute pass rates
    rows = []
    all_pass = True
    for sid in sorted(by_scenario.keys()):
        statuses = by_scenario[sid]
        pass_count = sum(1 for s in statuses if s == "PASS")
        total = len(statuses)
        rate = pass_count / total if total > 0 else 0.0
        passed = rate >= pass_threshold
        if not passed:
            all_pass = False
        rows.append((sid, pass_count, total, rate, passed))

    # Print table — guard colour codes so piped output (CI, log files,
    # non-ANSI Windows shells) stays clean.
    use_color = sys.stdout.isatty()
    green = "\033[32m" if use_color else ""
    red = "\033[31m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    print(f"\n{'=' * 72}")
    print(f"  MCP RELIABILITY SUMMARY  ({n_iterations} iterations)")
    print(f"{'=' * 72}")
    print(f"  {'Scenario':<40} {'Pass Rate':>12} {'Result':>8}")
    print(f"  {'-' * 40} {'-' * 12} {'-' * 8}")

    for sid, pass_count, total, rate, passed in rows:
        rate_str = f"{pass_count}/{total} ({rate:.0%})"
        colour = green if passed else red
        label = "PASS" if passed else "FAIL"
        result_str = f"{colour}{label:>8}{reset}"
        print(f"  {sid:<40} {rate_str:>12} {result_str}")

    print(f"  {'-' * 40} {'-' * 12} {'-' * 8}")

    # Readiness signal
    if all_pass:
        print(
            f"\n  Readiness: {green}GO{reset} (all scenarios >= {pass_threshold:.0%})"
        )
    else:
        failing = sum(1 for _, _, _, _, p in rows if not p)
        print(
            f"\n  Readiness: {red}NO_GO{reset} ({failing} scenario(s) below {pass_threshold:.0%})"
        )
    print(f"{'=' * 72}\n")

    # Write reliability_report.json alongside the last run's results
    last_sc = next((sc for sc in reversed(scorecards) if sc), None)
    if last_sc:
        from gaia.eval.runner import RESULTS_DIR

        report = {
            "iterations": n_iterations,
            "pass_threshold": pass_threshold,
            "readiness": "GO" if all_pass else "NO_GO",
            "scenarios": [
                {
                    "scenario_id": sid,
                    "pass_count": pc,
                    "total": t,
                    "iteration_pass_rate": r,
                    "status": "PASS" if p else "FAIL",
                }
                for sid, pc, t, r, p in rows
            ],
        }
        report_path = RESULTS_DIR / "reliability_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[RELIABILITY] Report saved → {report_path}")


def build_parser():
    """Build and return the root argparse parser."""
    import argparse

    # Create the main parser
    parser = argparse.ArgumentParser(
        description=f"Gaia CLI - Interact with Gaia AI agents. \n{version}",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Add version argument
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"{version}",
        help="Show program's version number and exit",
    )

    # Top-level flags for launching Agent UI or CLI directly
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch the Agent UI (browser-based chat interface)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=4200,
        help="Port for the Agent UI server (default: 4200, used with --ui)",
    )
    parser.add_argument(
        "--ui-dist",
        default=None,
        help="Path to pre-built Agent UI frontend dist directory (used with --ui)",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Launch interactive CLI chat",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Remote Lemonade server base URL (e.g. https://host:13305/api/v1)."
            " Used with --ui."
        ),
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
    prompt_parser.add_argument(
        "--device",
        choices=["cpu", "gpu", "npu"],
        default=None,
        help="Inference device: cpu, gpu (default), or npu (Ryzen AI)",
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
    chat_parser.add_argument(
        "--device",
        choices=["cpu", "gpu", "npu"],
        default=None,
        help="Inference device: cpu, gpu (default), or npu (Ryzen AI). "
        "Selects the model and backend for this agent session.",
    )

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
    chat_parser.add_argument(
        "--max-indexed-files",
        type=int,
        default=100,
        help="Maximum number of files to keep indexed before LRU eviction (default: 100)",
    )
    chat_parser.add_argument(
        "--max-total-chunks",
        type=int,
        default=10000,
        help="Maximum total chunks across all indexed files (default: 10000)",
    )
    chat_parser.add_argument(
        "--mcp-tool-limit",
        type=int,
        default=50,
        help="Maximum MCP tools to register (default: 50). "
        "Larger tool sets bloat the system prompt and degrade small-model "
        "tool-calling accuracy — keep this as low as your workflow allows. "
        "Workflows with >50 tools warrant a fresh eval run on the target model.",
    )

    # Agent UI
    chat_parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch the Agent UI (browser-based chat interface)",
    )
    chat_parser.add_argument(
        "--ui-port",
        type=int,
        default=4200,
        help="Port for the Agent UI server (default: 4200)",
    )
    chat_parser.add_argument(
        "--ui-dist",
        default=None,
        help="Path to pre-built Agent UI frontend dist directory (used with --ui)",
    )
    for agent_command, agent_help in (
        ("browse", "Web research with search, page fetch, and download tools"),
        ("analyze", "Structured data analysis with scratchpad tables"),
    ):
        agent_parser = subparsers.add_parser(
            agent_command,
            help=agent_help,
            parents=[parent_parser],
        )
        agent_parser.add_argument(
            "--query",
            "-q",
            type=str,
            help="Single query to execute (defaults to interactive mode if not provided)",
        )
        agent_parser.add_argument(
            "--show-prompts", action="store_true", help="Display prompts sent to LLM"
        )
        agent_parser.add_argument(
            "--debug", action="store_true", help="Enable debug output"
        )
        agent_parser.add_argument(
            "--allowed-paths",
            nargs="+",
            help="Allowed directory paths for file operations",
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
    talk_parser.add_argument(
        "--mic-threshold",
        type=float,
        default=0.003,
        help="Microphone amplitude threshold for voice detection (default: 0.003). Lower = more sensitive",
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
        choices=["transcript", "email", "pdf", "auto"],
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

    # Add SD (Stable Diffusion) image generation command
    sd_parser = subparsers.add_parser(
        "sd",
        help="Generate images using Stable Diffusion",
        parents=[parent_parser],
    )
    sd_parser.add_argument(
        "prompt",
        nargs="?",
        help="Text description of the image to generate",
    )
    sd_parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run in interactive mode",
    )
    sd_parser.add_argument(
        "--sd-model",
        dest="sd_model",
        choices=["SD-1.5", "SD-Turbo", "SDXL-Base-1.0", "SDXL-Turbo"],
        default="SDXL-Turbo",
        help="SD model: SDXL-Turbo (fast, good quality, default), SD-Turbo (faster but lower quality), SDXL-Base-1.0 (photorealistic, slow)",
    )
    sd_parser.add_argument(
        "--size",
        choices=["512x512", "768x768", "1024x1024"],
        help="Image size (auto-selected if not specified: 512px for SD-1.5/Turbo, 1024px for SDXL)",
    )
    sd_parser.add_argument(
        "--steps",
        type=int,
        help="Inference steps (auto-selected if not specified: 4 for Turbo, 20 for Base)",
    )
    sd_parser.add_argument(
        "--cfg-scale",
        dest="cfg_scale",
        type=float,
        help="CFG scale (auto-selected if not specified: 1.0 for Turbo, 7.5 for Base)",
    )
    sd_parser.add_argument(
        "--output-dir",
        default=".gaia/cache/sd/images",
        help="Directory to save generated images",
    )
    sd_parser.add_argument(
        "--seed",
        type=int,
        help="Random seed for reproducibility",
    )
    sd_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Skip prompt to open image in viewer (for automation/scripting)",
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

    # Add Email Triage Agent command (#962)
    email_parser = subparsers.add_parser(
        "email",
        help=(
            "Email Triage Agent — read, organize, and reply to Gmail with "
            "all body inference running locally on Lemonade. Requires the "
            "Google connector to be configured (Settings → Connections)."
        ),
        parents=[parent_parser],
    )
    email_parser.add_argument(
        "-q",
        "--query",
        type=str,
        default=None,
        help="One-shot query to send to the agent (non-interactive).",
    )
    email_parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run in interactive mode (loop reading queries from stdin).",
    )
    email_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Verbose mode — emit structured logs for every triage decision "
            "and tool call (recommended when benchmarking against other "
            "email agents)."
        ),
    )
    email_parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Debug mode — adds full prompt + LLM response logging to "
            "verbose output. Sensitive payloads in logs."
        ),
    )

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

    # Telegram adapter command (v0.18.2) - supports start|stop|status
    telegram_parser = subparsers.add_parser(
        "telegram",
        help="Manage Telegram messaging adapter (start|stop|status)",
        parents=[parent_parser],
    )
    telegram_subparsers = telegram_parser.add_subparsers(
        dest="telegram_action", help="telegram action to perform"
    )

    # Start subcommand
    t_start = telegram_subparsers.add_parser(
        "start", help="Start the Telegram adapter (polling)"
    )
    t_start.add_argument("--token", required=True, help="Telegram bot token")
    t_start.add_argument(
        "--allowed-users",
        help="Comma-separated Telegram user IDs allowed to interact (default: allow all)",
    )
    t_start.add_argument(
        "--background",
        action="store_true",
        help="Run adapter in background/daemon mode (writes PID and health endpoint)",
    )

    # Stop subcommand
    t_stop = telegram_subparsers.add_parser(
        "stop", help="Stop background Telegram adapter"
    )
    t_stop.add_argument(
        "--force",
        action="store_true",
        help="Force stop even if graceful shutdown fails",
    )

    # Status subcommand
    t_status = telegram_subparsers.add_parser(
        "status", help="Show status of Telegram adapter"
    )
    t_status.add_argument(
        "--health-host",
        default="127.0.0.1",
        help="Health server host (default: 127.0.0.1)",
    )
    t_status.add_argument(
        "--health-port",
        type=int,
        default=8765,
        help="Health server port (default: 8765)",
    )

    telegram_parser.set_defaults(action="telegram")

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
        default=13305,
        help="Lemonade server port (default: 13305)",
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
        "--port", type=int, default=None, help="Port number to kill process on"
    )
    kill_parser.add_argument(
        "--lemonade",
        action="store_true",
        help="Kill Lemonade server (port 13305)",
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

    # Add evaluation subparser
    eval_parser = subparsers.add_parser(
        "eval",
        help="Agent evaluation framework",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  gaia eval agent [OPTIONS]    Run agent eval benchmark scenarios

Run 'gaia eval agent --help' for full options.
        """,
    )

    # Nested eval subcommands
    eval_subparsers = eval_parser.add_subparsers(
        dest="eval_command",
        help="Evaluation utilities",
    )

    # Agent eval subcommand: gaia eval agent [OPTIONS]
    agent_eval_parser = eval_subparsers.add_parser(
        "agent",
        help="Run agent eval benchmark scenarios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all scenarios
  gaia eval agent

  # Run a specific scenario by ID
  gaia eval agent --scenario simple_factual_rag

  # Run all scenarios in a category
  gaia eval agent --category rag_quality

  # Regenerate corpus documents and validate manifest
  gaia eval agent --generate-corpus

  # Run architecture audit only (no LLM calls)
  gaia eval agent --audit-only

  # Run against a custom backend
  gaia eval agent --backend http://localhost:8080

  # Run eval then auto-fix failures with Claude Code
  gaia eval agent --fix

  # Fix mode with custom iteration limit and target
  gaia eval agent --fix --max-fix-iterations 5 --target-pass-rate 0.95

  # Fix a specific category
  gaia eval agent --category rag_quality --fix

  # Compare two runs for regressions
  gaia eval agent --compare eval/results/run1/scorecard.json eval/results/run2/scorecard.json

  # Save this run as the new baseline
  gaia eval agent --save-baseline

  # Compare current run against saved baseline (auto-detects eval/results/baseline.json)
  gaia eval agent --compare eval/results/latest/scorecard.json

  # Convert a real Agent UI conversation into a scenario YAML
  gaia eval agent --capture-session 29c211c7-31b5-4084-bb3f-1825c0210942

  # Scan an extra directory for custom scenario YAML files
  gaia eval agent --scenario-dir ~/my-eval-scenarios

  # Use a custom corpus directory with its own manifest.json
  gaia eval agent --corpus-dir ~/my-eval-corpus

  # Run only scenarios tagged with "healthcare"
  gaia eval agent --tag healthcare

  # Run scenarios matching any of multiple tags
  gaia eval agent --tag healthcare --tag critical

  # Output results in JUnit XML format for CI integration
  gaia eval agent --output-format junit
        """,
    )
    agent_eval_parser.add_argument(
        "--scenario",
        default=None,
        help="Run specific scenario by ID",
    )
    agent_eval_parser.add_argument(
        "--category",
        default=None,
        help="Run all scenarios in category",
    )
    agent_eval_parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Run architecture audit only (no LLM calls)",
    )
    agent_eval_parser.add_argument(
        "--generate-corpus",
        action="store_true",
        help="Regenerate corpus documents (CSV, etc.) and validate manifest.json",
    )
    agent_eval_parser.add_argument(
        "--backend",
        default="http://localhost:4200",
        help="Agent UI backend URL (default: http://localhost:4200)",
    )
    agent_eval_parser.add_argument(
        "--agent-type",
        default=None,
        metavar="AGENT_ID",
        help=(
            "Agent registration ID to target (e.g. 'gaia-lite'). When set, "
            "the eval runner instructs the simulator to create sessions with "
            "this agent_type so scenarios run against the chosen agent. Omit "
            "to use the backend default."
        ),
    )
    agent_eval_parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Eval model (default: claude-sonnet-4-6)",
    )
    agent_eval_parser.add_argument(
        "--budget",
        default="2.00",
        help="Max budget per scenario in USD (default: 2.00)",
    )
    agent_eval_parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Timeout per scenario in seconds (default: 900, scaled up automatically for multi-turn/large-doc scenarios)",
    )
    agent_eval_parser.add_argument(
        "--fix",
        action="store_true",
        help="After eval, invoke Claude Code to fix failures and re-eval (up to --max-fix-iterations)",
    )
    agent_eval_parser.add_argument(
        "--max-fix-iterations",
        type=int,
        default=3,
        help="Max fix-then-re-eval iterations in --fix mode (default: 3)",
    )
    agent_eval_parser.add_argument(
        "--target-pass-rate",
        type=float,
        default=0.90,
        help="Stop --fix iterations early when pass rate reaches this threshold (default: 0.90)",
    )
    agent_eval_parser.add_argument(
        "--compare",
        nargs="+",
        metavar="PATH",
        help="Compare two scorecard.json files (BASELINE CURRENT) or compare a run against saved baseline (CURRENT only)",
    )
    agent_eval_parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="After eval, save this run's scorecard as eval/results/baseline.json for future --compare",
    )
    agent_eval_parser.add_argument(
        "--capture-session",
        metavar="SESSION_ID",
        help="Convert an Agent UI session from the database into a YAML scenario file",
    )
    agent_eval_parser.add_argument(
        "--keep-sessions",
        action="store_true",
        help="Do not delete Agent UI sessions after eval — leave them for manual inspection",
    )
    agent_eval_parser.add_argument(
        "--scenario-dir",
        action="append",
        metavar="PATH",
        help="Additional directory to scan for scenario YAML files (can be repeated). "
        "User scenarios override built-in scenarios with the same ID.",
    )

    agent_eval_parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        metavar="N",
        help="Run each scenario N times for reliability measurement (default: 1)",
    )
    # NOTE: --reset-between-scenarios / --lemonade-model / --lemonade-ctx-size
    # are NOT YET IMPLEMENTED. They are accepted by the parser so a future
    # commit can wire them into AgentEvalRunner without changing the user-
    # facing surface, but passing a non-None value today raises
    # NotImplementedError (see the eval-agent handler). Driver scripts that
    # want clean-state reliability runs should restart Lemonade/Agent UI
    # externally between iterations until this lands.
    agent_eval_parser.add_argument(
        "--reset-between-scenarios",
        choices=["fast", "full"],
        default=None,
        metavar="MODE",
        help="[NOT YET IMPLEMENTED] Will reset services between scenarios "
        "for clean-state reliability testing. Passing this flag today "
        "raises NotImplementedError; restart Lemonade / Agent UI from a "
        "driver script in the meantime.",
    )
    agent_eval_parser.add_argument(
        "--lemonade-model",
        metavar="MODEL",
        help="[NOT YET IMPLEMENTED] Will pair with --reset-between-scenarios "
        "to reload a specific Lemonade model between scenarios. Today the "
        "Lemonade model is whatever the running server has loaded.",
    )
    agent_eval_parser.add_argument(
        "--lemonade-ctx-size",
        type=int,
        metavar="SIZE",
        help="[NOT YET IMPLEMENTED] Will pair with --lemonade-model to "
        "reload the model at this ctx size between scenarios.",
    )
    agent_eval_parser.add_argument(
        "--corpus-dir",
        action="append",
        metavar="PATH",
        help="Additional corpus directory with documents and manifest.json (can be repeated)",
    )
    agent_eval_parser.add_argument(
        "--tag",
        action="append",
        metavar="TAG",
        help="Run only scenarios with this tag (can be repeated; OR logic — "
        "scenarios matching ANY tag are included)",
    )
    agent_eval_parser.add_argument(
        "--output-format",
        choices=["json", "markdown", "junit"],
        default=None,
        help="Output format for results (default: json+markdown as today). "
        "'junit' writes a JUnit XML file for CI integration.",
    )
    agent_eval_parser.add_argument(
        "--device",
        choices=["cpu", "gpu", "npu"],
        default=None,
        help="Inference device for eval scenarios: cpu, gpu (default), or npu. "
        "Selects the model and backend from the agent's device_configs. "
        "Results are tagged with the device for cross-device comparison.",
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
        default="./output/evaluations",
        help="Directory containing .eval.json files to analyze (default: ./output/evaluations)",
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

    perf_vis_parser = subparsers.add_parser(
        "perf-vis",
        help="Visualize llama.cpp performance metrics from log files",
        parents=[parent_parser],
    )
    perf_vis_parser.add_argument(
        "log_paths",
        type=Path,
        nargs="+",
        help="One or more llama.cpp server log files to visualize",
    )
    perf_vis_parser.add_argument(
        "--show",
        action="store_true",
        help="Display plots interactively in addition to saving images",
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

    # MCP serve command (Agent UI MCP server)
    mcp_serve_parser = mcp_subparsers.add_parser(
        "serve", help="Start Agent UI MCP server (wraps the Agent UI backend)"
    )
    mcp_serve_parser.add_argument(
        "--host", default="localhost", help="Host to bind to (default: localhost)"
    )
    mcp_serve_parser.add_argument(
        "--port", type=int, default=8766, help="Port to listen on (default: 8766)"
    )
    mcp_serve_parser.add_argument(
        "--backend",
        default="http://localhost:4200",
        help="GAIA Agent UI backend URL (default: http://localhost:4200)",
    )
    mcp_serve_parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport instead of HTTP (for Claude Code / eval runner integration)",
    )

    # MCP Client commands (connect to external MCP servers).
    # `add` and `remove` moved to `gaia connectors mcp add/remove` (#977) so
    # configuration goes through the connectors framework with keyring-backed
    # secrets and per-agent grants.

    mcp_list_parser = mcp_subparsers.add_parser(
        "list", help="List configured MCP servers"
    )
    mcp_list_parser.add_argument(
        "--config",
        help="Path to MCP servers config file (default: ~/.gaia/mcp_servers.json)",
    )

    mcp_tools_parser = mcp_subparsers.add_parser(
        "tools", help="List tools from an MCP server"
    )
    mcp_tools_parser.add_argument("name", help="Name of the MCP server")
    mcp_tools_parser.add_argument(
        "--config",
        help="Path to MCP servers config file (default: ~/.gaia/mcp_servers.json)",
    )

    mcp_test_client_parser = mcp_subparsers.add_parser(
        "test-client", help="Test MCP client connection"
    )
    mcp_test_client_parser.add_argument("name", help="Name of the MCP server to test")

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

    # Memory command (agent memory management)
    memory_parser = subparsers.add_parser(
        "memory",
        help="Manage agent memory (bootstrap onboarding, view status)",
    )
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_action", help="Memory action to perform"
    )

    # Memory status command
    _ = memory_subparsers.add_parser("status", help="Show memory statistics")

    # Memory bootstrap command
    memory_bootstrap_parser = memory_subparsers.add_parser(
        "bootstrap",
        help="Run day-zero onboarding (conversational + system discovery)",
    )
    memory_bootstrap_parser.add_argument(
        "--chat-only",
        action="store_true",
        help="Conversational onboarding only",
    )
    memory_bootstrap_parser.add_argument(
        "--discover",
        action="store_true",
        help="System discovery only (re-scannable)",
    )
    memory_bootstrap_parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear source='discovery' items (with confirmation)",
    )
    memory_bootstrap_parser.add_argument(
        "--system",
        action="store_true",
        help="Re-scan and refresh system context (OS, hardware, apps, versions)",
    )
    memory_bootstrap_parser.add_argument(
        "--reset-system",
        action="store_true",
        help="Clear system context entries and optionally disable auto-collection",
    )
    memory_bootstrap_parser.add_argument(
        "--infer",
        action="store_true",
        help="LLM-assisted profile inference from browser history and installed apps",
    )

    # Diagnostics command (bundle logs + system info for bug reports)
    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="Bundle GAIA logs and system info into a tarball for bug reports",
    )
    diagnostics_parser.add_argument(
        "--output",
        default=None,
        help=(
            "Destination path for the diagnostics tarball "
            "(default: ~/.gaia/diagnostics-<YYYYMMDD-HHMMSS>.tgz)"
        ),
    )
    diagnostics_parser.add_argument(
        "--no-logs",
        action="store_true",
        help=(
            "Omit log files from the bundle (useful when logs may contain "
            "sensitive chat content)"
        ),
    )

    # Agent command (export/import custom agent bundles)
    agent_parser = subparsers.add_parser(
        "agent",
        help="Manage custom agents (export/import bundles)",
    )
    agent_subparsers = agent_parser.add_subparsers(
        dest="agent_action", help="Agent action to perform"
    )

    # Agent export command
    agent_export_parser = agent_subparsers.add_parser(
        "export",
        help="Export custom agents from ~/.gaia/agents/ into a .zip bundle",
    )
    agent_export_parser.add_argument(
        "--output",
        default=None,
        help="Destination path for the .zip bundle (default: ~/.gaia/export.zip)",
    )

    # Agent import command
    agent_import_parser = agent_subparsers.add_parser(
        "import",
        help="Import a custom agent .zip bundle into ~/.gaia/agents/",
    )
    agent_import_parser.add_argument(
        "path",
        help="Path to the .zip bundle produced by 'gaia agent export'",
    )
    agent_import_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation prompt (non-interactive/CI use)",
    )

    # Connectors framework (issue #927, parent of #915) — manage OAuth +
    # MCP-server connectors + per-agent grants. The subparser tree lives in
    # gaia.connectors.cli to keep this file lean.
    from gaia.connectors import cli as connectors_cli

    connectors_cli.add_subparser(subparsers)

    # Init command (one-stop GAIA setup)
    # Note: Does not use parent_parser to avoid showing irrelevant global options
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize GAIA: install Lemonade and download models",
    )
    init_parser.add_argument(
        "--profile",
        "-p",
        default="chat",
        choices=["minimal", "sd", "chat", "code", "rag", "mcp", "vlm", "npu", "all"],
        help="Profile to initialize: minimal, sd (image gen), chat, code, rag, mcp, vlm (vision), npu (Ryzen AI NPU), all (default: chat)",
    )
    init_parser.add_argument(
        "--minimal",
        action="store_true",
        help="Use minimal profile (~400 MB) - shortcut for --profile minimal",
    )
    init_parser.add_argument(
        "--skip-models",
        action="store_true",
        help="Skip model downloads (only install Lemonade)",
    )
    init_parser.add_argument(
        "--skip-lemonade",
        action="store_true",
        help="Skip Lemonade installation check (for CI with pre-installed Lemonade)",
    )
    init_parser.add_argument(
        "--force-reinstall",
        action="store_true",
        help="Force reinstall even if compatible version exists",
    )
    init_parser.add_argument(
        "--force-models",
        action="store_true",
        help="Force re-download models (deletes then re-downloads each model)",
    )
    init_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts (non-interactive)",
    )
    init_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    init_parser.add_argument(
        "--remote",
        action="store_true",
        help="Use remote Lemonade Server (skip local install/start; downloads models via API). Auto-detected when LEMONADE_BASE_URL points to a non-localhost URL.",
    )

    # Install command (install specific components)
    install_parser = subparsers.add_parser(
        "install",
        help="Install GAIA components",
        parents=[parent_parser],
    )
    install_parser.add_argument(
        "--lemonade",
        action="store_true",
        help="Install Lemonade Server",
    )
    install_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts",
    )
    install_parser.add_argument(
        "--silent",
        action="store_true",
        help="Silent installation (no UI, no desktop shortcuts)",
    )

    # Uninstall command: tiered cleanup of the Python-side GAIA install.
    # The actual flag set + handler live in gaia.installer.uninstall_command
    # so every desktop installer (NSIS, DMG, DEB, AppImage) can delegate to
    # the same implementation.
    from gaia.installer.uninstall_command import (
        register_subparser as _register_uninstall_subparser,
    )

    _register_uninstall_subparser(subparsers, parent_parser)
    return parser


def main():
    parser = build_parser()
    log = get_logger(__name__)

    args = parser.parse_args()

    # Check if action is specified
    if not args.action:
        # Top-level --ui flag: launch Agent UI
        if getattr(args, "ui", False):
            _launch_agent_ui(
                port=getattr(args, "ui_port", 4200),
                base_url=getattr(args, "base_url", None),
                log=log,
                debug=getattr(args, "debug", False),
                webui_dist=getattr(args, "ui_dist", None),
            )
            return

        # Top-level --cli flag: launch interactive CLI chat
        if getattr(args, "cli", False):
            _launch_interactive_cli(log=log)
            return

        # No flags: launch Agent UI (default experience)
        _launch_agent_ui(
            port=getattr(args, "ui_port", 4200),
            base_url=getattr(args, "base_url", None),
            log=log,
            debug=getattr(args, "debug", False),
            webui_dist=getattr(args, "ui_dist", None),
        )
        return

    # Set logging level using the GaiaLogger manager (if provided)
    from gaia.logger import log_manager

    if hasattr(args, "logging_level"):
        log_manager.set_level("gaia", getattr(logging, args.logging_level))

    # Handle chat --ui: launch Agent UI server (backward compat)
    if args.action == "chat" and getattr(args, "ui", False):
        max_files = getattr(args, "max_indexed_files", 0)
        if max_files:
            os.environ["GAIA_MAX_INDEXED_FILES"] = str(max_files)
        _launch_agent_ui(
            port=getattr(args, "ui_port", 4200),
            base_url=getattr(args, "base_url", None),
            log=log,
            debug=getattr(args, "debug", False),
            webui_dist=getattr(args, "ui_dist", None),
        )
        return

    # Handle telegram scaffold command
    if args.action == "telegram":
        # Telegram management: start | stop | status
        action = getattr(args, "telegram_action", None)
        if action == "start":
            try:
                from gaia.messaging.telegram import run_telegram
            except Exception as e:  # pragma: no cover - runtime import error
                print(f"❌ Telegram support is not available: {e}", file=sys.stderr)
                sys.exit(1)

            allowed = None
            if getattr(args, "allowed_users", None):
                try:
                    allowed = set(
                        int(x.strip())
                        for x in args.allowed_users.split(",")
                        if x.strip()
                    )
                except ValueError:
                    print(
                        "Invalid --allowed-users format; expected comma-separated integers",
                        file=sys.stderr,
                    )
                    sys.exit(2)

            run_telegram(
                token=args.token,
                allowed_users=allowed,
                background=getattr(args, "background", False),
            )
            return

        if action == "stop":
            import signal

            pid_path = os.path.expanduser("~/.gaia/telegram.pid")
            if not os.path.exists(pid_path):
                print("Telegram adapter is not running (no PID file).")
                return
            try:
                with open(pid_path, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Sent SIGTERM to Telegram adapter (pid {pid}).")
                try:
                    os.remove(pid_path)
                except OSError:
                    pass
            except ProcessLookupError:
                print("Process not found; removing stale PID file.")
                try:
                    os.remove(pid_path)
                except OSError:
                    pass
            except PermissionError:
                print("Permission denied when attempting to stop process. Try sudo.")
                sys.exit(1)
            except OSError as e:
                print(f"Failed to stop Telegram adapter: {e}")
                sys.exit(1)
            return

        if action == "status":
            # Prefer health endpoint; fallback to pid file existence
            import urllib.error
            import urllib.request

            host = getattr(args, "health_host", "127.0.0.1")
            port = getattr(args, "health_port", 8765)
            url = f"http://{host}:{port}/healthz"
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    body = resp.read().decode("utf-8").strip()
                    if resp.status == 200 and body == "ok":
                        print(f"Telegram adapter: healthy ({url})")
                        return
            except urllib.error.URLError:
                pass

            pid_path = os.path.expanduser("~/.gaia/telegram.pid")
            if os.path.exists(pid_path):
                print(
                    "Telegram adapter: PID file exists, but health check failed (may be starting or unhealthy)."
                )
            else:
                print("Telegram adapter: not running")
            return

        print(
            "No telegram action specified. Use: gaia telegram start|stop|status",
            file=sys.stderr,
        )
        return

    # Handle core Gaia CLI commands
    if args.action in ["prompt", "chat", "browse", "analyze", "talk", "stats"]:
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

    # Handle summarize command
    if args.action == "summarize":

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
                                "❌ Error: PDF output requires reportlab. Install with: uv pip install reportlab"
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
                                "⚠️  Warning: PDF output requires reportlab. Install with: uv pip install reportlab"
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

            test_text = args.test_text or """
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
                    'WhisperAsr not found. Please install voice support with: uv pip install -e ".[talk]"'
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
                    print(f"    Max Input Channels: {device['max_input_channels']}")
                    print(f"    Default Sample Rate: {device['default_samplerate']}")
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
                    "Please install: uv pip install llama-index-readers-youtube-transcript"
                )
                print(f"Import error: {e}")
                sys.exit(1)
            return

    # Handle kill command
    if args.action == "kill":
        if args.lemonade:
            # Use lemonade-server stop for graceful shutdown
            try:
                result = subprocess.run(
                    ["lemonade-server", "stop"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    print("✅ Lemonade server stopped")
                else:
                    # Fallback to port kill if stop command fails
                    log.warning(f"lemonade-server stop failed: {result.stderr}")
                    port_result = kill_process_by_port(13305)
                    if port_result["success"]:
                        print(f"✅ {port_result['message']}")
                    else:
                        print(f"❌ {port_result['message']}")
            except FileNotFoundError:
                # lemonade-server not in PATH, fallback to port kill
                log.warning("lemonade-server not found, falling back to port kill")
                port_result = kill_process_by_port(13305)
                if port_result["success"]:
                    print(f"✅ {port_result['message']}")
                else:
                    print(f"❌ {port_result['message']}")
        elif args.port:
            port = args.port
            log.info(f"Attempting to kill process on port {port}")
            result = kill_process_by_port(port)
            if result["success"]:
                print(f"✅ {result['message']}")
            else:
                print(f"❌ {result['message']}")
        else:
            print("❌ Specify --lemonade or --port <number>")
        return

    # Import LemonadeManager for model commands error handling
    from gaia.llm.lemonade_manager import LemonadeManager

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
                    LemonadeManager.print_server_error()
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
                LemonadeManager.print_server_error()
                return

            agent_name = args.agent.lower()
            model_ids = client.get_required_models(agent_name)

            console = AgentConsole()

            if not model_ids:
                if agent_name != "all":
                    profile = client.get_agent_profile(agent_name)
                    if not profile:
                        console.print_error(f"Unknown agent: {agent_name}")
                        console.print_info(
                            f"Available: {', '.join(client.list_agents())}"
                        )
                        sys.exit(1)
                console.print_info(f"No models to download for '{agent_name}'")
                return

            console.print_info(
                f"Downloading {len(model_ids)} model(s) for '{agent_name}'"
            )

            # Download each model with progress display
            success_count = 0
            skip_count = 0
            fail_count = 0

            for model_id in model_ids:
                # Check if already available
                if client.check_model_available(model_id):
                    console.print_download_skipped(model_id)
                    skip_count += 1
                    continue

                console.print_download_start(model_id)

                try:
                    completed = False
                    event_count = 0
                    last_bytes = 0
                    last_time = time.time()

                    for event in client.pull_model_stream(model_name=model_id):
                        event_count += 1
                        event_type = event.get("event")

                        if event_type == "progress":
                            # Skip first 2 spurious events from Lemonade
                            if event_count <= 2:
                                continue

                            # Calculate download speed
                            current_bytes = event.get("bytes_downloaded", 0)
                            current_time = time.time()
                            time_delta = current_time - last_time

                            speed_mbps = 0.0
                            if time_delta > 0.1 and current_bytes > last_bytes:
                                bytes_delta = current_bytes - last_bytes
                                speed_mbps = (bytes_delta / time_delta) / (1024 * 1024)
                                last_bytes = current_bytes
                                last_time = current_time

                            console.print_download_progress(
                                percent=event.get("percent", 0),
                                bytes_downloaded=current_bytes,
                                bytes_total=event.get("bytes_total", 0),
                                speed_mbps=speed_mbps,
                            )

                        elif event_type == "complete":
                            console.print_download_complete(model_id)
                            completed = True

                        elif event_type == "error":
                            console.print_download_error(
                                event.get("error", "Unknown error"), model_id
                            )
                            fail_count += 1
                            break

                    if completed:
                        success_count += 1
                except LemonadeClientError as e:
                    console.print_download_error(str(e), model_id)
                    fail_count += 1

            # Summary
            console.print_info("=" * 50)
            console.print_info("Download Summary:")
            console.print_success(f"Downloaded: {success_count}")
            console.print_info(f"Skipped (already available): {skip_count}")
            if fail_count > 0:
                console.print_error(f"Failed: {fail_count}")
            console.print_info("=" * 50)

            if fail_count > 0:
                sys.exit(1)

        except LemonadeClientError as e:
            console.print_error(str(e))
            sys.exit(1)
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "refused" in error_msg:
                LemonadeManager.print_server_error()
            else:
                console.print_error(str(e))
            sys.exit(1)
        return

    # Handle LLM command
    if args.action == "llm":
        # Initialize Lemonade with minimal profile for direct LLM queries
        success, _ = initialize_lemonade_for_agent(
            agent="minimal",
            quiet=False,
            base_url=getattr(args, "base_url", None),
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
                LemonadeManager.print_server_error()
            else:
                print(f"❌ Error: {str(e)}")
            return

    # Handle evaluation
    if args.action == "eval":
        if getattr(args, "eval_command", None) == "agent":
            # --capture-session: convert Agent UI session → YAML scenario
            capture_sid = getattr(args, "capture_session", None)
            if capture_sid:
                from gaia.eval.runner import capture_session

                capture_session(capture_sid)
                return

            # --generate-corpus: regenerate corpus documents and validate manifest
            if getattr(args, "generate_corpus", False):
                from gaia.eval.runner import generate_corpus

                generate_corpus()
                return

            # --compare: diff two scorecard files, no eval run needed
            compare_paths = getattr(args, "compare", None)
            if compare_paths:
                from gaia.eval.runner import RESULTS_DIR, compare_scorecards

                try:
                    if len(compare_paths) == 1:
                        # Single path: compare against saved baseline
                        baseline_path = RESULTS_DIR / "baseline.json"
                        if not baseline_path.exists():
                            print(f"[ERROR] No saved baseline found at {baseline_path}")
                            print(
                                "  Run `gaia eval agent --save-baseline` first to save a baseline."
                            )
                            sys.exit(1)
                        result = compare_scorecards(
                            str(baseline_path), compare_paths[0]
                        )
                    elif len(compare_paths) == 2:
                        result = compare_scorecards(compare_paths[0], compare_paths[1])
                    else:
                        print("[ERROR] --compare accepts 1 or 2 paths")
                        sys.exit(1)

                    # If compare detected regressions or significant score drops, fail non-zero
                    regressed = result.get("regressed", [])
                    score_regressed = result.get("score_regressed", [])
                    time_regressed = result.get("time_regressed", [])
                    total_issues = (
                        len(regressed) + len(score_regressed) + len(time_regressed)
                    )
                    if total_issues > 0:
                        print(
                            f"[ERROR] Detected {total_issues} issue(s) (status regressions, score regressions, or time regressions); failing."
                        )
                        sys.exit(2)
                    # Otherwise success
                    sys.exit(0)
                except FileNotFoundError as e:
                    print(f"[ERROR] {e}")
                    sys.exit(1)
                # compare handled; no further action

            # --reset-between-scenarios / --lemonade-model / --lemonade-ctx-size
            # are accepted by the parser but not yet wired into
            # AgentEvalRunner. Fail loudly rather than silently no-op.
            reset = getattr(args, "reset_between_scenarios", None)
            lemonade_model = getattr(args, "lemonade_model", None)
            lemonade_ctx = getattr(args, "lemonade_ctx_size", None)
            if (
                reset is not None
                or lemonade_model is not None
                or lemonade_ctx is not None
            ):
                raise NotImplementedError(
                    "--reset-between-scenarios / --lemonade-model / "
                    "--lemonade-ctx-size are reserved for a future commit "
                    "and are not yet wired into AgentEvalRunner. Restart "
                    "Lemonade and the Agent UI from your driver script "
                    "between iterations to get the same effect."
                )

            iterations = getattr(args, "iterations", 1)
            fix_mode = getattr(args, "fix", False)

            if iterations < 1:
                print("Error: --iterations must be >= 1", file=sys.stderr)
                return

            if iterations > 1 and fix_mode:
                print(
                    "Error: --fix is incompatible with --iterations > 1. "
                    "Fix mode operates on a single run's failures.",
                    file=sys.stderr,
                )
                return

            from gaia.eval.runner import AgentEvalRunner

            all_scorecards = []
            for iter_idx in range(iterations):
                if iterations > 1:
                    print(f"\n{'=' * 60}")
                    print(f"[ITER] Iteration {iter_idx + 1}/{iterations}")
                    print(f"{'=' * 60}")

                # Resolve --device to model when --model not explicit
                eval_model = args.model
                eval_device = getattr(args, "device", None)
                if eval_device and not eval_model:
                    from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

                    for dc in DEFAULT_DEVICE_CONFIGS:
                        if dc.device == eval_device:
                            eval_model = dc.model
                            break
                    device_labels = {"cpu": "CPU", "gpu": "GPU", "npu": "NPU"}
                    print(
                        f"🖥️  Eval device: {device_labels.get(eval_device, eval_device)}  |  "
                        f"Model: {eval_model}"
                    )

                runner = AgentEvalRunner(
                    backend_url=args.backend,
                    model=eval_model,
                    budget_per_scenario=args.budget,
                    timeout_per_scenario=args.timeout,
                    agent_type=getattr(args, "agent_type", None),
                    extra_scenario_dirs=getattr(args, "scenario_dir", None),
                    extra_corpus_dirs=getattr(args, "corpus_dir", None),
                    tags=getattr(args, "tag", None),
                    output_format=getattr(args, "output_format", None),
                )
                scorecard = runner.run(
                    scenario_id=getattr(args, "scenario", None),
                    category=getattr(args, "category", None),
                    audit_only=getattr(args, "audit_only", False),
                    fix_mode=fix_mode,
                    max_fix_iterations=getattr(args, "max_fix_iterations", 3),
                    target_pass_rate=getattr(args, "target_pass_rate", 0.90),
                    keep_sessions=getattr(args, "keep_sessions", False),
                )
                all_scorecards.append(scorecard)

            if iterations > 1 and all_scorecards:
                _print_reliability_summary(
                    all_scorecards,
                    pass_threshold=getattr(args, "target_pass_rate", 0.90),
                )

            # --save-baseline: copy scorecard to eval/results/baseline.json
            # (saves the last iteration's scorecard)
            last_scorecard = all_scorecards[-1] if all_scorecards else None
            if getattr(args, "save_baseline", False) and last_scorecard:

                from gaia.eval.runner import RESULTS_DIR

                baseline_path = RESULTS_DIR / "baseline.json"
                baseline_path.write_text(
                    json.dumps(last_scorecard, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(f"[BASELINE] Saved baseline → {baseline_path}")
            return

        # Bare "gaia eval" without subcommand - show help
        print("Usage: gaia eval agent [OPTIONS]")
        print("")
        print("Run 'gaia eval agent --help' for full options.")
        return

    # Handle MCP command
    if args.action == "mcp":
        handle_mcp_command(args)
        return

    # Handle Cache command
    if args.action == "cache":
        handle_cache_command(args)
        return

    # Handle Memory command
    if args.action == "memory":
        handle_memory_command(args)
        return

    # Handle Connectors command (issue #927, parent of #915)
    if args.action == "connectors":
        from gaia.connectors import cli as connectors_cli  # pylint: disable=reimported

        rc = connectors_cli.handle(args)
        sys.exit(rc)

    # Handle Diagnostics command
    if args.action == "diagnostics":
        handle_diagnostics_command(args)
        return

    # Handle Agent (export/import) command
    if args.action == "agent":
        handle_agent_command(args)
        return

    # Handle Blender command
    if args.action == "blender":
        handle_blender_command(args)
        return

    # Handle SD (image generation) command
    if args.action == "sd":
        handle_sd_command(args)
        return

    # Handle Jira command
    if args.action == "jira":
        handle_jira_command(args)
        return

    if args.action == "email":
        handle_email_command(args)
        return

    # Handle Docker command
    if args.action == "docker":
        handle_docker_command(args)
        return

    # Handle API server command
    if args.action == "api":
        handle_api_command(args)
        return

    if args.action == "perf-vis":
        handle_perf_vis_command(args)
        return

    # Handle init command
    if args.action == "init":
        # --minimal flag overrides --profile
        profile = "minimal" if args.minimal else args.profile

        # MCP profile has its own init flow (no Lemonade/models)
        if profile == "mcp":
            from gaia.installer.mcp_init import run_mcp_init

            exit_code = run_mcp_init(
                yes=args.yes,
                verbose=getattr(args, "verbose", False),
            )
            sys.exit(exit_code)

        from gaia.installer.init_command import run_init

        exit_code = run_init(
            profile=profile,
            skip_models=args.skip_models,
            skip_lemonade=getattr(args, "skip_lemonade", False),
            force_reinstall=args.force_reinstall,
            force_models=args.force_models,
            yes=args.yes,
            verbose=getattr(args, "verbose", False),
            remote=getattr(args, "remote", False),
        )
        sys.exit(exit_code)

    # Handle install command
    if args.action == "install":
        if args.lemonade:
            from gaia.installer.lemonade_installer import LemonadeInstaller
            from gaia.version import LEMONADE_VERSION

            installer = LemonadeInstaller()

            # Check if already installed
            info = installer.check_installation()
            if info.installed and info.version:
                # Use needs_install (returns True if current < target)
                if not installer.needs_install(info):
                    print(
                        f"✅ Lemonade Server v{info.version} is already installed"
                        f" (>= v{LEMONADE_VERSION})"
                    )
                    sys.exit(0)
                else:
                    print(f"Lemonade Server v{info.version} is installed")
                    print(f"GAIA expects v{LEMONADE_VERSION}+")
                    print("")
                    print("To update, run:")
                    print("  gaia uninstall --purge --purge-lemonade")
                    print("  gaia install --lemonade")
                    sys.exit(1)

            # Confirm installation
            if not args.yes:
                response = input(f"Install Lemonade v{LEMONADE_VERSION}? [Y/n]: ")
                if response.lower() == "n":
                    print("Installation cancelled")
                    sys.exit(0)

            # Download and install
            try:
                if installer.system == "linux":
                    print("Adding Lemonade PPA and installing lemonade-server...")
                    installer_path = None
                else:
                    print("Downloading Lemonade Server...")
                    installer_path = installer.download_installer()
                    print("Installing...")
                result = installer.install(installer_path, silent=args.silent)

                if result.success:
                    # Verify installation
                    verify_info = installer.check_installation()
                    if verify_info.installed:
                        print(f"✅ Installed Lemonade Server v{verify_info.version}")
                    else:
                        print(f"✅ Installed Lemonade Server v{result.version}")
                    sys.exit(0)
                else:
                    print(f"❌ Installation failed: {result.error}")
                    sys.exit(1)
            except Exception as e:
                print(f"❌ Installation failed: {e}")
                sys.exit(1)
        else:
            print("Specify what to install: --lemonade")
            sys.exit(1)

    # Handle uninstall command — delegates to the shared implementation in
    # gaia.installer.uninstall_command so every desktop installer platform
    # (NSIS / DMG / DEB / AppImage) uses the same tiered-cleanup code path.
    if args.action == "uninstall":
        from gaia.installer.uninstall_command import run as _run_uninstall

        sys.exit(_run_uninstall(args))

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


def handle_jira_command(args):
    """
    Handle the Jira app command.

    Args:
        args: Parsed command line arguments for the jira command
    """
    log = get_logger(__name__)

    # Initialize Lemonade with jira agent profile (32768 context)
    # Skip if --no-lemonade-check is specified
    if not getattr(args, "no_lemonade_check", False):
        success, _ = initialize_lemonade_for_agent(
            agent="jira",
            skip_if_external=True,
            use_claude=getattr(args, "use_claude", False),
            use_chatgpt=getattr(args, "use_chatgpt", False),
            base_url=getattr(args, "base_url", None),
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
        print("Make sure GAIA is installed properly: uv pip install -e .")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error running Jira app: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)


def handle_email_command(args):
    """
    Handle the ``gaia email`` command.

    Wires the Email Triage Agent (#962) to a CLI session. AC3-critical:
    this handler does NOT pass ``--use-claude`` / ``--use-chatgpt`` to
    the agent (the agent's config has no such field). The local-LLM-only
    path is the only path.

    Args:
        args: Parsed command line arguments for the email command
    """
    log = get_logger(__name__)

    # Initialize Lemonade — local LLM only. The email agent's config will
    # also reject any non-local base_url at construction time, but the
    # CLI manager check gives a friendlier "start Lemonade first" message.
    if not getattr(args, "no_lemonade_check", False):
        success, _ = initialize_lemonade_for_agent(
            agent="email",
            skip_if_external=True,
            # Deliberately omitted: use_claude / use_chatgpt — see AC3.
            base_url=getattr(args, "base_url", None),
        )
        if not success:
            sys.exit(1)

    try:
        from gaia.agents.email.cli import main as email_main

        # Normalize args the agent CLI expects.
        if not hasattr(args, "verbose"):
            args.verbose = False
        if not hasattr(args, "debug"):
            args.debug = False
        if not hasattr(args, "model"):
            args.model = None
        if not hasattr(args, "query"):
            args.query = None
        if not hasattr(args, "interactive"):
            args.interactive = False

        result = asyncio.run(email_main(args))
        sys.exit(result)

    except ImportError as e:
        log.error(f"Failed to import Email agent: {e}")
        print("❌ Error: Email agent components are not available")
        print("Make sure GAIA is installed properly: uv pip install -e .")
        sys.exit(1)
    except Exception as e:
        log.error(f"Error running Email agent: {e}")
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
    # Skip if --no-lemonade-check is specified
    if not getattr(args, "no_lemonade_check", False):
        success, _ = initialize_lemonade_for_agent(
            agent="docker",
            skip_if_external=True,
            use_claude=getattr(args, "use_claude", False),
            use_chatgpt=getattr(args, "use_chatgpt", False),
            base_url=getattr(args, "base_url", None),
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
        print("Make sure GAIA is installed properly: uv pip install -e .")
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
            success, _ = initialize_lemonade_for_agent(
                agent="mcp",
                quiet=False,
                base_url=getattr(args, "base_url", None),
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
            print("Make sure uvicorn is installed: uv pip install uvicorn")
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


def handle_perf_vis_command(args):
    """Generate llama.cpp performance plots from one or more log files."""
    try:
        exit_code = run_perf_visualization(args.log_paths, show=args.show)
    except Exception as exc:
        log = get_logger(__name__)
        log.error(f"Error running perf-vis: {exc}")
        print(f"❌ Error running perf-vis: {exc}")
        sys.exit(1)

    if exit_code != 0:
        sys.exit(exit_code)


def handle_sd_command(args):
    """
    Handle the SD (Stable Diffusion) image generation command.

    Args:
        args: Parsed command line arguments for the sd command
    """
    # No prompt and not interactive - show help (no server needed)
    if not args.prompt and not args.interactive:
        print("Usage: gaia sd <prompt> [options]")
        print("       gaia sd -i  (interactive mode)")
        print()
        print("Examples:")
        print('  gaia sd "a sunset over mountains"')
        print('  gaia sd "cyberpunk city" --sd-model SDXL-Turbo --size 1024x1024')
        print("  gaia sd -i")
        return

    from gaia.agents.sd import SDAgent, SDAgentConfig

    # Ensure Lemonade is ready with proper context size for SD agent
    # SD agent needs 8K context for image + story workflow
    success, _ = initialize_lemonade_for_agent(
        agent="sd",
        use_claude=getattr(args, "use_claude", False),
        use_chatgpt=getattr(args, "use_chatgpt", False),
        quiet=False,
        base_url=getattr(args, "base_url", None),
    )

    if not success and not (
        getattr(args, "use_claude", False) or getattr(args, "use_chatgpt", False)
    ):
        print("Failed to initialize Lemonade Server with required 8K context.")
        print("Try: lemonade-server serve --ctx-size 8192")
        sys.exit(1)

    # Create config - ensure LLM model is set
    llm_model = getattr(args, "model", None)
    if not llm_model:
        llm_model = "Gemma-4-E4B-it-GGUF"  # Default LLM for prompt enhancement

    config = SDAgentConfig(
        sd_model=args.sd_model,
        output_dir=args.output_dir,
        prompt_to_open=not args.no_open,
        show_stats=getattr(args, "stats", False),
        use_claude=getattr(args, "use_claude", False),
        use_chatgpt=getattr(args, "use_chatgpt", False),
        base_url=getattr(args, "base_url", "http://localhost:13305/api/v1"),
        model_id=llm_model,
    )

    # Create agent with LLM prompt enhancement
    agent = SDAgent(config)

    # Check health
    health = agent.sd_health_check()
    if health["status"] != "healthy":
        print(f"Error: {health.get('error', 'SD endpoint unavailable')}")
        print("Make sure Lemonade Server is running and SD model is available:")
        print("  lemonade-server serve")
        print("  lemonade-server pull SD-Turbo")
        sys.exit(1)

    print()
    print("=" * 80)
    print(f"🖼️  SD Image Generator - {args.sd_model}")
    print("=" * 80)
    print("LLM-powered prompt enhancement for better image quality")
    print(f"Output: {args.output_dir}")
    if not args.no_open:
        print("You'll be prompted to open images after generation")
    print("=" * 80)
    print()

    # Interactive mode
    if args.interactive:
        print("Type 'quit' to exit.")
        print()

        while True:
            try:
                user_prompt = input("You: ").strip()
                if not user_prompt:
                    continue
                if user_prompt.lower() in ("quit", "exit", "q"):
                    print("Goodbye!")
                    break

                # Track images before this query
                initial_count = len(agent.sd_generations)

                # Use agent.process_query() for LLM enhancement
                result = agent.process_query(user_prompt)
                if result.get("final_answer"):
                    print(f"\nAgent: {result['final_answer']}\n")
                else:
                    print("\nAgent: Generation complete\n")

                # Prompt to open image(s) after agent completes
                if not args.no_open and result.get("status") != "error":
                    try:
                        # Get all newly generated images from this query
                        new_images = agent.sd_generations[initial_count:]

                        if new_images:
                            num_images = len(new_images)
                            prompt_text = (
                                f"Open {num_images} images in default viewer? [Y/n]: "
                                if num_images > 1
                                else "Open image in default viewer? [Y/n]: "
                            )
                            response = input(prompt_text).strip().lower()

                            if response in ("", "y", "yes"):
                                for img in new_images:
                                    path = str(Path(img["image_path"]).resolve())
                                    if sys.platform == "win32":
                                        os.startfile(path)  # pylint: disable=no-member
                                    elif sys.platform == "darwin":
                                        subprocess.run(["open", path], check=False)
                                    else:
                                        subprocess.run(["xdg-open", path], check=False)
                                plural = "s" if num_images > 1 else ""
                                print(f"[{num_images} image{plural} opened]\n")
                    except (KeyboardInterrupt, EOFError):
                        pass

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break

    # Single prompt mode
    else:
        # Track images before this command
        initial_count = len(agent.sd_generations)

        # Use agent.process_query() for LLM enhancement
        result = agent.process_query(args.prompt)
        if result.get("final_answer"):
            print(f"\n{result['final_answer']}\n")

        # Prompt to open image(s) after agent completes
        if not args.no_open and result.get("status") != "error":
            try:
                # Get all newly generated images from this command
                new_images = agent.sd_generations[initial_count:]

                if new_images:
                    num_images = len(new_images)
                    prompt_text = (
                        f"Open {num_images} images in default viewer? [Y/n]: "
                        if num_images > 1
                        else "Open image in default viewer? [Y/n]: "
                    )
                    response = input(prompt_text).strip().lower()

                    if response in ("", "y", "yes"):
                        for img in new_images:
                            path = str(Path(img["image_path"]).resolve())
                            if sys.platform == "win32":
                                os.startfile(path)  # pylint: disable=no-member
                            elif sys.platform == "darwin":
                                subprocess.run(["open", path], check=False)
                            else:
                                subprocess.run(["xdg-open", path], check=False)
                        plural = "s" if num_images > 1 else ""
                        print(f"[{num_images} image{plural} opened]\n")
            except (KeyboardInterrupt, EOFError):
                pass


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
        print('Install blender dependencies with: uv pip install -e ".[blender]"')
        sys.exit(1)

    # Initialize Lemonade with blender agent profile (32768 context)
    # Skip if --no-lemonade-check is specified
    if not getattr(args, "no_lemonade_check", False):
        log.info("Initializing Lemonade for Blender agent...")
        success, _ = initialize_lemonade_for_agent(
            agent="blender",
            skip_if_external=True,
            use_claude=getattr(args, "use_claude", False),
            use_chatgpt=getattr(args, "use_chatgpt", False),
            base_url=getattr(args, "base_url", None),
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
            show_stats=args.show_stats,
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


def handle_memory_command(args):
    """Handle the memory management command.

    Subcommands:
        status    — Show memory stats (entries by source/category/context, DB size)
        bootstrap — Run day-zero onboarding (conversation + discovery)
    """
    if not hasattr(args, "memory_action") or args.memory_action is None:
        print("❌ Error: No memory action specified")
        print("Available actions: status, bootstrap")
        print("Run 'gaia memory --help' for more information")
        return

    if args.memory_action == "status":
        _handle_memory_status()
    elif args.memory_action == "bootstrap":
        _handle_memory_bootstrap(args)


def _handle_memory_status():
    """Show memory statistics: entries by source/category/context, DB size, session count."""
    from gaia.agents.base.memory_store import MemoryStore

    try:
        store = MemoryStore()
    except Exception as e:
        print(f"❌ Error opening memory database: {e}")
        sys.exit(1)

    try:
        stats = store.get_stats()

        # Also query by_source (not in get_stats, do a direct query)
        by_source = {}
        try:
            by_source = store.get_source_counts()
        except Exception:
            pass

        # --- Format output ---
        print("\n=== GAIA Agent Memory ===\n")

        # Knowledge section
        k = stats["knowledge"]
        print(f"  Knowledge entries: {k['total']}")
        if k["by_category"]:
            cats = ", ".join(
                f"{cat}: {count}" for cat, count in sorted(k["by_category"].items())
            )
            print(f"    By category:  {cats}")
        if k["by_context"]:
            ctxs = ", ".join(
                f"{ctx}: {count}" for ctx, count in sorted(k["by_context"].items())
            )
            print(f"    By context:   {ctxs}")
        if by_source:
            srcs = ", ".join(
                f"{src}: {count}" for src, count in sorted(by_source.items())
            )
            print(f"    By source:    {srcs}")
        if k["sensitive_count"] > 0:
            print(f"    Sensitive:    {k['sensitive_count']}")
        if k["entity_count"] > 0:
            print(f"    Entities:     {k['entity_count']}")
        if k["total"] > 0:
            print(f"    Avg confidence: {k['avg_confidence']:.2f}")

        # Conversations section
        c = stats["conversations"]
        print(
            f"\n  Conversations:     {c['total_turns']} turns across {c['total_sessions']} sessions"
        )
        if c["first_session"]:
            print(f"    First session: {c['first_session'][:10]}")
        if c["last_session"]:
            print(f"    Last session:  {c['last_session'][:10]}")

        # Tools section
        t = stats["tools"]
        print(
            f"\n  Tool calls:        {t['total_calls']} ({t['unique_tools']} unique tools)"
        )
        if t["total_calls"] > 0:
            print(f"    Success rate:  {t['overall_success_rate']:.0%}")
            print(f"    Total errors:  {t['total_errors']}")

        # Temporal section
        tp = stats["temporal"]
        if tp["upcoming_count"] > 0 or tp["overdue_count"] > 0:
            print("\n  Temporal:")
            if tp["upcoming_count"] > 0:
                print(f"    Upcoming (7d): {tp['upcoming_count']}")
            if tp["overdue_count"] > 0:
                print(f"    Overdue:       {tp['overdue_count']}")

        # DB size
        db_mb = stats["db_size_bytes"] / (1024 * 1024)
        if db_mb >= 1.0:
            print(f"\n  Database size:     {db_mb:.1f} MB")
        else:
            db_kb = stats["db_size_bytes"] / 1024
            print(f"\n  Database size:     {db_kb:.1f} KB")

        print()

    except Exception as e:
        print(f"❌ Error reading memory stats: {e}")
        sys.exit(1)
    finally:
        store.close()


def _handle_memory_bootstrap(args):
    """Handle the memory bootstrap subcommand.

    Flags:
        --chat-only     Conversational onboarding only
        --discover      System discovery only (interactive, with approval)
        --system        Re-scan and refresh silent system context
        --reset         Clear source='discovery' items
        --reset-system  Clear system context entries (optionally disable)
        (default)       Run chat-only then discover
    """
    try:
        if getattr(args, "reset_system", False):
            _bootstrap_reset_system()
        elif getattr(args, "system", False):
            _bootstrap_system(force=True)
        elif args.reset:
            _bootstrap_reset()
        elif args.chat_only:
            _bootstrap_chat()
        elif args.discover:
            _bootstrap_discover()
        elif getattr(args, "infer", False):
            _bootstrap_infer()
        else:
            # Default: run both phases
            _bootstrap_chat()
            print()
            _bootstrap_discover()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)


# ---- Bootstrap: conversational onboarding ----

_BOOTSTRAP_QUESTIONS = [
    {
        "prompt": "What's your name?",
        "category": "profile",
        "template": "User's name is {answer}",
    },
    {
        "prompt": "What do you do? (role, profession, or student)",
        "category": "profile",
        "template": "User's role/profession: {answer}",
    },
    {
        "prompt": "What will you mainly use GAIA for?",
        "category": "profile",
        "template": "User's primary use cases for GAIA: {answer}",
    },
    {
        "prompt": "What programming languages or tools do you use most?",
        "category": "profile",
        "template": "User's primary tools and languages: {answer}",
    },
    {
        "prompt": "What are your interests or hobbies outside of work?",
        "category": "profile",
        "template": "User's interests and hobbies: {answer}",
    },
    {
        "prompt": "How should I communicate with you? (concise/detailed, casual/formal)",
        "category": "preference",
        "template": "Preferred communication style: {answer}",
    },
    {
        "prompt": "Anything else you'd like me to know about you?",
        "category": "profile",
        "template": "Additional user context: {answer}",
    },
]


def _bootstrap_chat():
    """Phase 1: Conversational onboarding — ask questions, store answers."""
    from gaia.agents.base.memory_store import MemoryStore

    print("\n=== Welcome to GAIA — Your Personal AI Assistant ===")
    print("I run locally on your AMD hardware, so everything stays private.")
    print("Let me get to know you so I can be more helpful from the start.")
    print("(Press Enter to skip any question)\n")

    try:
        store = MemoryStore()
    except Exception as e:
        raise RuntimeError(f"Error opening memory database: {e}") from e

    stored_count = 0
    try:
        for q in _BOOTSTRAP_QUESTIONS:
            try:
                answer = input(f"  {q['prompt']} ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\nBootstrap cancelled.")
                return

            if not answer:
                continue

            content = q["template"].format(answer=answer)
            try:
                store.store(
                    category=q["category"],
                    content=content,
                    source="user",
                    context="global",
                    confidence=0.8,
                )
                stored_count += 1
            except Exception as e:
                print(f"  ⚠ Failed to store: {e}")

        print(f"\n✅ Stored {stored_count} memory entries from onboarding.")
    finally:
        store.close()


# ---- Bootstrap: LLM-assisted profile inference ----

_INFER_PROMPT = """\
You are analyzing a user's computing environment to generate personalized profile insights.

Based on the data below, generate 5-10 concise profile facts about this user. Focus on:
- Professional role or domain (e.g. software developer, data scientist, designer)
- Key technologies, languages, and tools they regularly use
- Technical interests and areas of focus
- Non-technical interests or hobbies (if clearly evident)

Raw data:
{sections}

Respond with a JSON array of insight objects. Each object must have:
  "content"    - A clear, specific fact about the user (1 sentence, ≤120 chars)
  "confidence" - Float 0.0–1.0 (how confident you are, given the evidence)
  "domain"     - One of: "work", "technical", "personal", "general"

Rules:
- Only state what the data clearly supports — do not guess.
- Skip generic facts that apply to almost everyone.
- Return ONLY the JSON array, no other text, no markdown fences.

Example output:
[
  {{"content": "Primarily a Python developer with strong AI/ML focus", "confidence": 0.9, "domain": "work"}},
  {{"content": "Regularly contributes to open-source projects on GitHub", "confidence": 0.8, "domain": "technical"}}
]"""

_INFER_REFRESH_DAYS = 30  # Re-run LLM inference after this many days


def _bootstrap_infer():
    """Phase 3 (optional): LLM-assisted profile inference from browser history + system data.

    Collects raw signals (browser domains, installed apps, git identity), sends
    them to the local LLM, and stores the generated profile insights after user
    review.  All browser data is sensitive — explicit consent is requested before
    reading it.
    """
    from gaia.agents.base.discovery import SystemDiscovery
    from gaia.agents.base.memory_store import MemoryStore

    print("\n=== GAIA Memory — AI Profile Inference ===")
    print(
        "GAIA will read your browser history and installed apps, then use the local\n"
        "LLM to generate personalised profile facts.  Raw data is NEVER stored or\n"
        "sent anywhere — only the LLM-generated insights are proposed for storage.\n"
    )

    # Check for stale inferred facts — offer refresh
    try:
        store_check = MemoryStore()
        try:
            inferred = store_check.get_by_category("profile", context="global", limit=1)
            # Filter to source="inferred"
            inferred = [r for r in inferred if r.get("source") == "inferred"]
            if inferred:
                newest_updated = inferred[0].get("updated_at", "")
                if newest_updated:
                    from datetime import datetime as _dt

                    age_days = (
                        _dt.now()
                        - _dt.fromisoformat(newest_updated).replace(tzinfo=None)
                    ).days
                    if age_days < _INFER_REFRESH_DAYS:
                        print(
                            f"  LLM-inferred profile facts are {age_days} day(s) old "
                            f"(refresh threshold: {_INFER_REFRESH_DAYS} days)."
                        )
                        try:
                            resp = (
                                input("  Re-run inference anyway? [y/N]: ")
                                .strip()
                                .lower()
                            )
                        except (EOFError, KeyboardInterrupt):
                            print("\nInference cancelled.")
                            return
                        if resp != "y":
                            print("  Skipping — your profile is up to date.")
                            return
        finally:
            store_check.close()
    except Exception:
        pass  # Non-critical — proceed anyway

    # Explicit consent for browser history access
    try:
        consent = input("  Read browser history for inference? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nInference cancelled.")
        return
    use_browser = consent != "n"

    print("\n  Collecting data...", end="", flush=True)
    try:
        discovery = SystemDiscovery()
    except Exception as e:
        raise RuntimeError(f"Error initializing system discovery: {e}") from e

    # --- Collect raw signals ---
    sections: list[str] = []

    # 1. Browser history (top domains, visit counts)
    if use_browser:
        try:
            browser_results = discovery.scan_browser_history(days=30)
            if browser_results:
                lines = []
                for item in browser_results[:40]:
                    # content is "Frequently visited: domain.com (N visits)"
                    lines.append(f"  {item['content']}")
                sections.append(
                    "BROWSER HISTORY (top domains, last 30 days):\n" + "\n".join(lines)
                )
        except Exception:
            pass

    # 2. Installed applications
    try:
        app_results = discovery.scan_installed_apps()
        if app_results:
            # Extract just the app names from content strings like "Installed app: VS Code"
            apps = []
            for item in app_results:
                content = item.get("content", "")
                if content.startswith("Installed app: "):
                    apps.append(content[len("Installed app: ") :].strip())
            if apps:
                sections.append("INSTALLED APPS:\n  " + ", ".join(apps))
    except Exception:
        pass

    # 3. Git identity (name / employer domain — not raw email)
    try:
        git_results = discovery.scan_git_identity()
        if git_results:
            git_lines = []
            for item in git_results:
                # Skip sensitive (raw email) items
                if not item.get("sensitive") and item.get("content"):
                    git_lines.append(f"  {item['content']}")
            if git_lines:
                sections.append("GIT IDENTITY:\n" + "\n".join(git_lines))
    except Exception:
        pass

    # 4. Project languages / manifests (non-sensitive)
    try:
        manifest_results = discovery.scan_project_manifests()
        if manifest_results:
            manifest_lines = []
            for item in manifest_results[:10]:
                if not item.get("sensitive") and item.get("content"):
                    manifest_lines.append(f"  {item['content']}")
            if manifest_lines:
                sections.append(
                    "PROJECT MANIFESTS (sample):\n" + "\n".join(manifest_lines)
                )
    except Exception:
        pass

    # 5. App launch frequency (UserAssist — covers consumer apps like Spotify, Outlook)
    try:
        userassist_results = discovery.scan_windows_userassist()
        if userassist_results:
            lines = [f"  {item['content']}" for item in userassist_results[:20]]
            sections.append(
                "FREQUENTLY LAUNCHED APPS (actual usage frequency):\n"
                + "\n".join(lines)
            )
    except Exception:
        pass

    # 6. Recent file type patterns
    try:
        filetype_results = discovery.scan_recent_file_types()
        if filetype_results:
            lines = [f"  {item['content']}" for item in filetype_results]
            sections.append("RECENT FILE TYPES (work patterns):\n" + "\n".join(lines))
    except Exception:
        pass

    # 7. Gaming and media
    try:
        gaming_results = discovery.scan_gaming_and_media()
        if gaming_results:
            lines = [f"  {item['content']}" for item in gaming_results]
            sections.append("GAMING AND MEDIA:\n" + "\n".join(lines))
    except Exception:
        pass

    print(" done.")

    if not sections:
        print("\n  No data collected for inference.  Nothing to process.")
        return

    # --- Build and send LLM prompt ---
    prompt_sections = "\n\n".join(sections)
    prompt = _INFER_PROMPT.format(sections=prompt_sections)

    print(
        "  Calling local LLM for insights (this may take ~30s)...", end="", flush=True
    )
    try:
        llm = create_client()
        raw_response = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_new_tokens=1024,
        )
        # Collect streamed response if needed
        if hasattr(raw_response, "__iter__") and not isinstance(raw_response, str):
            raw_response = "".join(raw_response)
    except Exception as e:
        print(f"\n\n  ❌ LLM call failed: {e}")
        print("  Make sure Lemonade Server is running: lemonade-server serve")
        return

    print(" done.")

    # --- Parse JSON array from response ---
    insights: list[dict] = []
    try:
        # Strip markdown fences if LLM wrapped output
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]

        insights = json.loads(cleaned.strip())
        if not isinstance(insights, list):
            raise ValueError("Expected a JSON array")
        # Validate each item
        insights = [
            i
            for i in insights
            if isinstance(i, dict)
            and isinstance(i.get("content"), str)
            and i["content"].strip()
        ]
    except Exception as e:
        print(f"\n  ❌ Failed to parse LLM response: {e}")
        print(f"  Raw response:\n{raw_response[:500]}")
        return

    if not insights:
        print("\n  No insights generated.")
        return

    print(f"\n  Generated {len(insights)} insights. Review each one:\n")
    print("  [Y] = approve (default)   [n] = skip   [q] = quit\n")

    # --- Review and store ---
    try:
        store = MemoryStore()
    except Exception as e:
        raise RuntimeError(f"Error opening memory database: {e}") from e

    # Silence noisy store INFO logs
    _store_logger = logging.getLogger("gaia.agents.base.memory_store")
    _orig_level = _store_logger.level
    _store_logger.setLevel(logging.WARNING)

    approved = 0
    skipped = 0

    # Delete stale inferred facts before storing new ones (only if user approves at least one)
    inferred_deleted = False

    try:
        for i, insight in enumerate(insights, 1):
            content = insight["content"].strip()[:200]
            confidence = float(insight.get("confidence", 0.7))
            domain = insight.get("domain", "general")
            confidence = max(0.0, min(1.0, confidence))

            print(f"  ({i}/{len(insights)}) [{confidence:.0%}] {content}")
            try:
                choice = input("    Approve? [Y/n/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n\nReview cancelled.")
                break

            if choice == "q":
                print("  Review stopped.")
                break
            elif choice == "n":
                skipped += 1
                continue
            else:
                # Clear old inferred facts on first approval
                if not inferred_deleted:
                    try:
                        store.delete_by_source("inferred")
                    except Exception:
                        pass
                    inferred_deleted = True

                try:
                    store.store(
                        category="profile",
                        content=content,
                        source="inferred",
                        context="global",
                        sensitive=False,
                        confidence=confidence,
                        domain=domain,
                    )
                    approved += 1
                except Exception as e:
                    print(f"    ⚠ Failed to store: {e}")

        print(f"\n✅ Inference complete: {approved} approved, {skipped} skipped.")
    finally:
        _store_logger.setLevel(_orig_level)
        store.close()


# ---- Bootstrap: system discovery ----


def _bootstrap_discover():
    """Phase 2: System discovery — scan local system, present findings for review."""
    from gaia.agents.base.discovery import SystemDiscovery
    from gaia.agents.base.memory_store import MemoryStore

    print("\n=== GAIA Memory Bootstrap — System Discovery ===")
    print("Scanning your system for projects, apps, and more...")
    print("Nothing is stored without your approval.\n")

    try:
        discovery = SystemDiscovery()
    except Exception as e:
        raise RuntimeError(f"Error initializing system discovery: {e}") from e

    try:
        all_results = discovery.scan_all()
    except Exception as e:
        raise RuntimeError(f"Error during system scan: {e}") from e

    # Flatten and count
    findings = []
    for source_name, items in all_results.items():
        for item in items:
            item["_source_name"] = source_name
            findings.append(item)

    if not findings:
        print("No discoveries found on this system.")
        return

    print(f"Found {len(findings)} items. Review each one:\n")
    print("  [Y] = approve (default)   [n] = skip   [q] = quit review\n")

    try:
        store = MemoryStore()
    except Exception as e:
        raise RuntimeError(f"Error opening memory database: {e}") from e

    approved_count = 0
    skipped_count = 0
    try:
        for i, item in enumerate(findings, 1):
            sensitive_tag = " [SENSITIVE]" if item.get("sensitive") else ""
            ctx_tag = f" [{item.get('context', 'unclassified')}]"
            print(f"  ({i}/{len(findings)}) {item['content']}{ctx_tag}{sensitive_tag}")

            try:
                choice = input("    Approve? [Y/n/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n\nReview cancelled.")
                break

            if choice == "q":
                print("  Review stopped.")
                break
            elif choice == "n":
                skipped_count += 1
                continue
            else:
                # Default = approve (empty string or 'y')
                try:
                    store.store(
                        category=item.get("category", "fact"),
                        content=item["content"],
                        source="discovery",
                        context=item.get("context", "global"),
                        sensitive=item.get("sensitive", False),
                        entity=item.get("entity") or None,
                        confidence=item.get("confidence", 0.4),
                    )
                    approved_count += 1
                except Exception as e:
                    print(f"    ⚠ Failed to store: {e}")

        print(
            f"\n✅ Discovery complete: {approved_count} approved, {skipped_count} skipped."
        )
    finally:
        store.close()


# ---- Bootstrap: reset discovery items ----


def _bootstrap_reset():
    """Clear all source='discovery' knowledge items after user confirmation."""
    from gaia.agents.base.memory_store import MemoryStore

    try:
        store = MemoryStore()
    except Exception as e:
        raise RuntimeError(f"Error opening memory database: {e}") from e

    try:
        # Count discovery items
        by_source = store.get_source_counts()
        count = by_source.get("discovery", 0)

        if count == 0:
            print("No discovery items found in memory. Nothing to reset.")
            return

        # Prompt for confirmation
        try:
            response = (
                input(
                    f"Delete {count} discovered item(s)? "
                    "User-edited items (source='user') are preserved. [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nReset cancelled.")
            return

        if response != "y":
            print("Reset cancelled.")
            return

        # Delete discovery items atomically (FTS + knowledge in one transaction)
        deleted = store.delete_by_source("discovery")
        print(f"✅ Deleted {deleted} discovery item(s).")

    except Exception as e:
        raise RuntimeError(f"Error during reset: {e}") from e
    finally:
        store.close()


# ---- Bootstrap: system context (silent, non-interactive) ----


def _bootstrap_system(force: bool = True):
    """Re-scan system context and store facts in memory (no LLM required)."""
    from gaia.agents.base.memory import (
        _save_memory_settings,
        _system_context_is_enabled,
    )
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.agents.base.system_context import collect_system_info

    print("\n=== GAIA Memory — System Context Refresh ===")

    if not _system_context_is_enabled():
        print("⚠  System context collection is disabled.")
        try:
            choice = input("Re-enable and collect now? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
        if choice != "y":
            print(
                "Skipped. Run 'gaia memory bootstrap --reset-system' to manage this setting."
            )
            return
        _save_memory_settings({"system_context_enabled": True})
        print("✅ System context collection re-enabled.\n")

    print("Collecting system information...\n")
    try:
        facts = collect_system_info()
    except Exception as e:
        print(f"❌ Error collecting system info: {e}")
        return

    for f in facts:
        print(f"  [{f['domain']}] {f['content']}")

    if not facts:
        print("No facts collected.")
        return

    try:
        store = MemoryStore()
    except Exception as e:
        print(f"❌ Error opening memory database: {e}")
        return

    _store_logger = logging.getLogger("gaia.agents.base.memory_store")
    _orig_level = _store_logger.level

    try:
        # Silence INFO-level store logs — end users don't need per-row output
        _store_logger.setLevel(logging.WARNING)

        if force:
            # Clear existing system entries before re-storing
            cleared = store.delete_by_category("system")
            if cleared:
                print(f"\n  (replaced {cleared} existing system entries)")

        stored = 0
        for fact in facts:
            try:
                store.store(
                    category="system",
                    content=fact["content"],
                    domain=fact.get("domain"),
                    context="global",
                    confidence=1.0,
                    source="system",
                )
                stored += 1
            except Exception as e:
                print(f"  ⚠ Failed to store: {e}")

        print(f"\n✅ Stored {stored} system context item(s).")
    finally:
        _store_logger.setLevel(_orig_level)
        store.close()


# ---- Bootstrap: reset system context ----


def _bootstrap_reset_system():
    """Clear all system context entries and optionally disable auto-collection."""
    from gaia.agents.base.memory import (
        _save_memory_settings,
        _system_context_is_enabled,
    )
    from gaia.agents.base.memory_store import MemoryStore

    print("\n=== GAIA Memory — Reset System Context ===")

    try:
        store = MemoryStore()
    except Exception as e:
        print(f"❌ Error opening memory database: {e}")
        return

    try:
        items = store.get_by_category("system", context="global", limit=200)
        count = len(items)

        if count == 0:
            print("No system context entries found in memory.")
        else:
            print(f"Found {count} system context entries:\n")
            for item in items[:10]:
                print(f"  - {item['content']}")
            if count > 10:
                print(f"  ... and {count - 10} more.")

            try:
                choice = (
                    input(f"\nDelete all {count} system context entries? [y/N]: ")
                    .strip()
                    .lower()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled.")
                return

            if choice == "y":
                deleted = store.delete_by_category("system")
                print(f"✅ Deleted {deleted} system context entry(ies).")
            else:
                print("Deletion cancelled.")
                return
    finally:
        store.close()

    # Ask whether to disable auto-collection going forward
    currently_enabled = _system_context_is_enabled()
    if currently_enabled:
        try:
            choice = (
                input(
                    "\nDisable automatic system context collection on startup? [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice == "y":
            _save_memory_settings({"system_context_enabled": False})
            print("✅ System context collection disabled.")
            print("   Run 'gaia memory bootstrap --system' to re-enable and refresh.")
        else:
            print(
                "Auto-collection remains enabled — system context will be re-collected on next startup."
            )
    else:
        print("\nAuto-collection is already disabled.")
        try:
            choice = input("Re-enable it? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice == "y":
            _save_memory_settings({"system_context_enabled": True})
            print("✅ System context collection re-enabled.")


def handle_diagnostics_command(args):
    """Handle the 'gaia diagnostics' command.

    Bundles GAIA log files and a short system-info snapshot into a compressed
    tarball suitable for attaching to bug reports. Captures:

    - ``~/.gaia/electron-install.log``
    - ``~/.gaia/gaia.log``
    - ``~/.gaia/electron-main.log`` (if present; emitted by the Electron shell)
    - ``~/.gaia/electron-install-state.json``
    - ``uname -a`` output
    - ``lsb_release -a`` output, falling back to ``/etc/os-release``
    - ``env`` entries matching ``gaia|lemonade|xdg|wayland|display`` (case-insensitive)
    - ``lsof -iTCP:4200`` output (when ``lsof`` is available)

    Args:
        args: Parsed command-line arguments. Supports ``--output`` to override
            the destination path and ``--no-logs`` to omit log files from the
            bundle.
    """
    import datetime
    import io
    import re
    import tarfile

    diag_log = get_logger(__name__)

    gaia_dir = Path.home() / ".gaia"
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = gaia_dir / f"diagnostics-{timestamp}.tgz"

    # Ensure the parent directory exists (e.g. first-ever run before ~/.gaia
    # has been created by any other GAIA command).
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        diag_log.error(f"Unable to create diagnostics output directory: {e}")
        print(f"❌ Error: unable to create {output_path.parent}: {e}")
        sys.exit(1)

    # Log files and state file collected from ~/.gaia
    log_files = [
        gaia_dir / "electron-install.log",
        gaia_dir / "gaia.log",
        gaia_dir / "electron-main.log",
    ]
    state_files = [
        gaia_dir / "electron-install-state.json",
    ]

    def _run(cmd):
        """Run a shell command and return its combined stdout/stderr as text.

        Returns a human-readable error string on failure instead of raising,
        so a single missing tool does not abort the bundle.
        """
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            out = result.stdout or ""
            if result.stderr:
                out += f"\n[stderr]\n{result.stderr}"
            return out
        except FileNotFoundError:
            return f"[command not found: {cmd[0]}]"
        except (subprocess.SubprocessError, OSError) as e:
            # Narrow to the specific failure modes this function is
            # expected to tolerate (tool hung, spawn failed, permission
            # denied). Real logic errors must still propagate so we
            # don't silently bury them per CLAUDE.md's no-silent-fallback
            # rule.
            return f"[error running {' '.join(cmd)}: {e}]"

    # System info snapshot
    sysinfo_parts = []
    sysinfo_parts.append("=== uname -a ===\n" + _run(["uname", "-a"]))

    lsb = _run(["lsb_release", "-a"])
    if lsb.startswith("[command not found"):
        os_release = Path("/etc/os-release")
        if os_release.is_file():
            try:
                lsb = os_release.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                lsb = f"[error reading /etc/os-release: {e}]"
        else:
            lsb = "[lsb_release and /etc/os-release both unavailable]"
    sysinfo_parts.append("=== lsb_release / os-release ===\n" + lsb)

    env_filter = re.compile(
        r"^(GAIA|LEMONADE|XDG|WAYLAND|DISPLAY|X_|QT_|GTK_)", re.IGNORECASE
    )
    # Redact values for keys that look like they might carry secrets. The
    # filter above already restricts the set of keys, but a user may still
    # have set e.g. GAIA_API_KEY or LEMONADE_TOKEN, and we must not ship
    # those in a bug-report tarball.
    secret_filter = re.compile(
        r"key|token|secret|pass(word|wd)?|auth|credential", re.IGNORECASE
    )
    env_lines = []
    for k, v in sorted(os.environ.items()):
        if not env_filter.search(k):
            continue
        v_safe = "[redacted]" if secret_filter.search(k) else v
        env_lines.append(f"{k}={v_safe}")
    sysinfo_parts.append(
        "=== env (filtered: GAIA|LEMONADE|XDG|WAYLAND|DISPLAY|X_|QT_|GTK_; secrets redacted) ===\n"
        + "\n".join(env_lines)
    )

    # lsof on port 4200 — only if lsof is present; capture_output handles absence.
    sysinfo_parts.append(
        "=== lsof -iTCP:4200 (legacy default) ===\n" + _run(["lsof", "-iTCP:4200"])
    )
    sysinfo_parts.append(
        "=== ss -tlnp (all TCP listeners) ===\n" + _run(["ss", "-tlnp"])
    )

    sysinfo_blob = "\n\n".join(sysinfo_parts).encode("utf-8")

    # Build the tarball
    try:
        with tarfile.open(output_path, "w:gz") as tar:
            # Always include the system-info snapshot
            info = tarfile.TarInfo(name="system-info.txt")
            info.size = len(sysinfo_blob)
            info.mtime = int(datetime.datetime.now().timestamp())
            tar.addfile(info, io.BytesIO(sysinfo_blob))

            # Always include state files (no chat content)
            for entry in state_files:
                if entry.is_file():
                    tar.add(
                        str(entry),
                        arcname=entry.name,
                        filter=lambda ti: ti if ti.isfile() or ti.isdir() else None,
                    )

            # Log files gated by --no-logs
            if not args.no_logs:
                for entry in log_files:
                    if entry.is_file():
                        tar.add(
                            str(entry),
                            arcname=entry.name,
                            filter=lambda ti: ti if ti.isfile() or ti.isdir() else None,
                        )
            else:
                note = b"Log files omitted (--no-logs was passed).\n"
                info = tarfile.TarInfo(name="LOGS-OMITTED.txt")
                info.size = len(note)
                info.mtime = int(datetime.datetime.now().timestamp())
                tar.addfile(info, io.BytesIO(note))
    except OSError as e:
        diag_log.error(f"Error writing diagnostics bundle: {e}")
        print(f"❌ Error writing {output_path}: {e}")
        sys.exit(1)

    print(f"✓ Diagnostics bundle written to: {output_path}")


def handle_agent_command(args):
    """Handle the 'gaia agent' command group (export / import).

    Args:
        args: Parsed command-line arguments
    """
    if not hasattr(args, "agent_action") or args.agent_action is None:
        print("❌ Error: No agent action specified")
        print("Available actions: export, import")
        print("Run 'gaia agent --help' for more information")
        sys.exit(1)

    if args.agent_action == "export":
        handle_agent_export(args)
    elif args.agent_action == "import":
        handle_agent_import(args)
    else:
        print(f"❌ Unknown agent action: {args.agent_action}")
        sys.exit(1)


def handle_agent_export(args):
    """Export custom agents under ~/.gaia/agents/ into a .zip bundle."""
    # Lazy import to keep CLI startup fast.
    from gaia.installer.export_import import export_custom_agents

    output = args.output
    if output is None:
        output_path = Path.home() / ".gaia" / "export.zip"
    else:
        output_path = Path(output).expanduser()

    # Warn about secrets before writing anything.
    print(
        "Warning: exported bundle contains your agent source files as-is. "
        "Any API keys or credentials in agent.py will be included. "
        "Review before sharing.",
        file=sys.stderr,
    )

    try:
        result = export_custom_agents(output_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    ids = ", ".join(result.agent_ids)
    print(f"Exported {len(result.agent_ids)} agent(s) to {result.output_path}: {ids}")


def handle_agent_import(args):
    """Import a custom agent .zip bundle into ~/.gaia/agents/."""
    import zipfile

    # Lazy import to keep CLI startup fast.
    from gaia.installer.export_import import import_agent_bundle

    bundle_path = Path(args.path).expanduser()

    if not bundle_path.exists():
        print(f"Error: bundle not found: {bundle_path}", file=sys.stderr)
        sys.exit(1)

    # Peek at bundle.json so we can show agent ids in the trust prompt.
    # Guard against a maliciously oversized bundle.json before reading it.
    bundle_agent_ids = []
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            info = zf.getinfo("bundle.json")
            if info.file_size > 1 * 1024 * 1024:  # 1 MB hard cap on manifest
                raise ValueError("bundle.json exceeds 1 MB — bundle appears malformed")
            raw = zf.read("bundle.json")
            manifest = json.loads(raw.decode("utf-8"))
            bundle_agent_ids = manifest.get("agent_ids", []) or []
    except (
        zipfile.BadZipFile,
        KeyError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        print(f"Error: invalid bundle: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Trust gate.
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "Error: refusing to import non-interactively without --yes. "
                "Re-run with --yes to confirm.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(
            "Importing this bundle will install third-party Python code on your machine."
        )
        if bundle_agent_ids:
            print("Agents in bundle:")
            for aid in bundle_agent_ids:
                print(f"  - {aid}")
        answer = input("[y/N] Continue? ").strip().lower()
        if answer not in ("y", "yes"):
            print("Import cancelled.")
            sys.exit(0)

    try:
        result = import_agent_bundle(bundle_path)
    except (ValueError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if result.imported:
        print(f"Imported: {', '.join(result.imported)}")
    if result.overwritten:
        print(f"Overwritten: {', '.join(result.overwritten)}")
    if result.errors:
        print(f"Errors: {', '.join(result.errors)}", file=sys.stderr)
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
    elif args.mcp_action == "serve":
        handle_mcp_serve(args)
    elif args.mcp_action == "list":
        handle_mcp_list(args)
    elif args.mcp_action == "tools":
        handle_mcp_tools(args)
    elif args.mcp_action == "test-client":
        handle_mcp_test_client(args)
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
            print('  uv pip install -e ".[mcp]"')
            return

        # Import and start the HTTP-native MCP bridge
        from gaia.mcp.mcp_bridge import start_server as start_mcp_http

        # Initialize Lemonade with mcp agent profile (unless --no-lemonade-check)
        if not getattr(args, "no_lemonade_check", False):
            success, _ = initialize_lemonade_for_agent(
                agent="mcp",
                quiet=False,
                base_url=getattr(args, "base_url", None),
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
            try:
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
            except Exception:
                log_handle.close()
                raise

            # Write PID to dedicated PID file
            pid_file_path = os.path.abspath("gaia.mcp.pid")
            with open(pid_file_path, "w", encoding="utf-8") as pid_file:
                pid_file.write(str(process.pid))

            # Append PID to log (banner was written before subprocess started)
            ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
            log_handle.write(f"{ts} | INFO | Process ID: {process.pid}\n")
            log_handle.flush()
            # Close parent's handle — subprocess inherited its own fd copy
            log_handle.close()

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


def handle_mcp_serve(args):
    """Start the Agent UI MCP server (wraps the GAIA Agent UI REST API)."""
    log = get_logger(__name__)

    try:
        from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

        mcp = create_agent_ui_mcp(backend_url=args.backend)

        if args.stdio:
            print(
                "Starting GAIA Agent UI MCP Server (stdio mode)...",
                file=__import__("sys").stderr,
            )
            mcp.run(transport="stdio")
        else:
            mcp.settings.host = args.host
            mcp.settings.port = args.port

            print("=" * 60)
            print("🤖 GAIA Agent UI MCP Server")
            print("=" * 60)
            print(f"   Backend : {args.backend}")
            print(f"   MCP     : http://{args.host}:{args.port}/mcp")
            try:
                tool_count = len(
                    mcp._tool_manager._tools
                )  # pylint: disable=protected-access
                print(f"   Tools   : {tool_count} registered")
            except Exception:
                pass
            print("\nPress Ctrl+C to stop")
            print("=" * 60)
            mcp.run(transport="streamable-http")

    except KeyboardInterrupt:
        print("\n✅ Agent UI MCP server stopped")
    except ImportError as e:
        log.error(f"Failed to import Agent UI MCP server: {e}")
        print("❌ Error: Could not load Agent UI MCP server")
        print(f"   {e}")
        print("   Make sure the Agent UI backend is running: gaia chat --ui")
    except Exception as e:
        log.error(f"Error starting Agent UI MCP server: {e}")
        print(f"❌ Error starting Agent UI MCP server: {e}")


def handle_mcp_list(args):
    """List configured MCP servers."""
    from gaia.mcp import MCPConfig

    config = MCPConfig(args.config) if args.config else MCPConfig()
    servers = config.get_servers()

    config_path = args.config if args.config else "~/.gaia/mcp_servers.json"

    if not servers:
        print("📋 No MCP servers configured")
        print(f"   Config: {config_path}")
        print("\nAdd a server with: gaia mcp add <name> <command>")
        if args.config:
            print(
                f"                or: gaia mcp add <name> <command> --config {args.config}"
            )
        return

    print(f"📋 Configured MCP Servers ({len(servers)}):")
    print(f"   Config: {config_path}")
    print("=" * 60)

    for name, server_config in servers.items():
        command = server_config.get("command", "Unknown")
        print(f"\n🔹 {name}")
        print(f"   Command: {command}")


def handle_mcp_tools(args):
    """List tools from an MCP server."""
    from gaia.mcp import MCPClientManager
    from gaia.mcp.client.config import MCPConfig

    config = MCPConfig(args.config) if args.config else MCPConfig()
    manager = MCPClientManager(config=config)

    try:
        # Try to get existing client or connect
        client = manager.get_client(args.name)
        if not client:
            print(f"📡 Connecting to MCP server '{args.name}'...")
            # Load from config
            manager.load_from_config()
            client = manager.get_client(args.name)

        if not client:
            print(f"❌ MCP server '{args.name}' not found")
            print("\nAvailable servers:")
            for server in manager.list_servers():
                print(f"  - {server}")
            return

        tools = client.list_tools()

        print(f"🔧 Tools from '{args.name}' ({len(tools)}):")
        print("=" * 60)

        for tool in tools:
            print(f"\n🔹 {tool.name}")
            print(f"   Description: {tool.description}")
            params = tool.input_schema.get("properties", {})
            if params:
                print(f"   Parameters: {', '.join(params.keys())}")

    except Exception as e:
        print(f"❌ Error listing tools: {e}")


def handle_mcp_test_client(args):
    """Test MCP client connection."""
    from gaia.mcp import MCPClientManager

    manager = MCPClientManager()

    try:
        print(f"🧪 Testing MCP server '{args.name}'...")

        # Try to get existing client or connect
        client = manager.get_client(args.name)
        if not client:
            print("📡 Connecting...")
            manager.load_from_config()
            client = manager.get_client(args.name)

        if not client:
            print(f"❌ MCP server '{args.name}' not found")
            return

        # Test connection
        print("✅ Connection: OK")
        print(f"   Server: {client.server_info.get('name', 'Unknown')}")

        # List tools
        tools = client.list_tools()
        print(f"✅ Tools: {len(tools)} available")

        if tools:
            # Test first tool if it exists
            test_tool = tools[0]
            print(f"\n🧪 Testing tool: {test_tool.name}")
            print(f"   Description: {test_tool.description}")

            # Show parameters
            params = test_tool.input_schema.get("properties", {})
            if params:
                print(f"   Parameters: {', '.join(params.keys())}")

        print("\n✅ All tests passed!")

    except Exception as e:
        print(f"❌ Test failed: {e}")


if __name__ == "__main__":
    main()
