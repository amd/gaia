# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import asyncio
import inspect
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
from gaia.agents.install_hints import agent_not_installed_message
from gaia.eval.config import DEFAULT_CLAUDE_MODEL
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
from gaia.llm.lemonade_launcher import describe_start_hint
from gaia.logger import get_logger
from gaia.perf_analysis import run_perf_visualization
from gaia.ports import is_killable_process, listeners_on_port, terminate_pid
from gaia.version import version

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


def _configured_device() -> str:
    """The user's configured inference device ('cpu', 'gpu', 'npu')."""
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        return GaiaConfig.load().default_device
    except GaiaConfigError as e:
        # A corrupt config must not silently pick a device — the wrong one
        # loads the model at a context size the backend can't serve.
        raise GaiaConfigError(
            f"Cannot resolve the inference device from the GAIA config: {e}. "
            f"Fix or delete {GaiaConfig.config_path()}, or run `gaia config set "
            "default_device gpu`."
        ) from e


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
        agent: Agent name (chat, talk, rag, vlm, minimal, mcp)
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
    from gaia.llm.lemonade_client import profile_ctx_size
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

    # One context size per device profile, never a per-agent literal: every
    # agent asking for the same window is what keeps a single
    # (model, ctx_size) pair resident, so switching agents never reloads.
    # Keyed on device, not agent, because the NPU's FLM build caps below the
    # GPU window and would fail to load at it.
    # Users on tight RAM can override with the ``GAIA_CTX_SIZE`` env var.
    required_ctx = profile_ctx_size(_configured_device())

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
        agent: Agent name (chat, rag, talk, vlm, minimal, mcp)
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


def resolve_effective_device(
    effective_device: str, device_was_explicit: bool, devices: dict
) -> str:
    """Resolve the device `gaia chat` reports/runs on, validating whichever
    device is actually landed on.

    Sequential npu -> gpu checks (not if/elif) so a non-explicit npu->gpu
    reassignment falls through to the GPU probe in the same call, instead of
    being reported unchecked (#2241: an if/elif chain evaluates once, so the
    npu branch's reassignment could never trigger the gpu branch). An
    explicit --device still fails loudly (sys.exit(1)) when unavailable
    rather than falling back; cpu needs no availability check.
    """
    if effective_device == "npu":
        npu_info = devices.get("amd_npu", {})
        if not npu_info.get("available"):
            if device_was_explicit:
                print(
                    "❌ NPU not available on this system. "
                    "Requires Ryzen AI 300/400/Max (XDNA2). "
                    "Run `gaia init --profile npu` to set it up, "
                    "or choose --device gpu.",
                    file=sys.stderr,
                )
                sys.exit(1)
            effective_device = "gpu"

    if effective_device == "gpu":
        from gaia.llm.lemonade_manager import system_info_has_gpu

        has_gpu = system_info_has_gpu(devices)
        if not has_gpu and not device_was_explicit:
            # No accelerator reported — describe the resulting state
            # (Lemonade runs on CPU) rather than claiming a fallback GAIA
            # forces; it does not send a device to Lemonade, which picks the
            # backend itself.
            print(
                "No GPU detected — inference will run on CPU "
                "(slower). Run `gaia init` to set up GPU "
                "acceleration."
            )
            effective_device = "cpu"

    return effective_device


def _gaia_cli_client_params(kwargs: dict) -> dict:
    """Keep only the kwargs that ``GaiaCliClient.__init__`` accepts.

    ``async_main`` receives every parsed CLI flag in ``kwargs``, including global
    ones like ``--ui``. Passing those straight through crashes the constructor
    with ``TypeError: ... unexpected keyword argument``. Deriving the accepted
    names from the signature keeps this correct if the signature ever changes.
    """
    accepted = set(inspect.signature(GaiaCliClient.__init__).parameters) - {"self"}
    return {k: v for k, v in kwargs.items() if k in accepted}


async def async_main(action, **kwargs):
    log = get_logger(__name__)

    # Map actions to agent profiles for Lemonade initialization
    # Each agent has specific model and context size requirements
    action_to_agent = {
        "prompt": "minimal",  # Basic prompts use minimal profile
        "chat": "chat",
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
        # Pass only what GaiaCliClient accepts; unrelated CLI flags (e.g. --ui)
        # would otherwise reach the constructor and crash it with a TypeError.
        client = GaiaCliClient(**_gaia_cli_client_params(kwargs))

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
        # Use Chat Agent with RAG, file search, and shell execution.
        # ChatAgent ships as the standalone gaia-agent-chat wheel (#1102).
        try:
            from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
            from gaia_agent_chat.app import interactive_mode
        except ImportError as e:
            raise RuntimeError(
                agent_not_installed_message(
                    "The chat agent is not installed",
                    "gaia-agent-chat",
                    next_step="Then re-run `gaia chat`.",
                )
            ) from e

        try:
            # Use silent mode when debug is off to hide intermediate processing
            # SilentConsole will still stream the final answer
            query = kwargs.get("query")
            debug_mode = kwargs.get("debug", False)
            use_silent_mode = not debug_mode  # Hide processing steps unless debugging

            # Resolve device to model_id when --device is set and --model is not.
            # No --device: fall back to the persisted config default (GPU out of
            # the box; `gaia init --profile npu` records NPU), so the write of
            # config.default_device is honoured at runtime rather than ignored.
            # Fallback policy:
            #   - Explicit --device: fail loudly if unavailable (no fallback)
            #   - Default (no --device): config default, fallback to CPU with warning
            explicit_model = kwargs.get("model", None)
            device = kwargs.get("device", None)
            device_was_explicit = device is not None
            if device_was_explicit:
                effective_device = device
            else:
                from gaia.config import GaiaConfig, GaiaConfigError

                try:
                    effective_device = GaiaConfig.load().default_device
                except GaiaConfigError as _cfg_err:
                    print(f"❌ {_cfg_err}", file=sys.stderr)
                    sys.exit(1)

            # Check device availability when Lemonade is reachable
            if not explicit_model:
                from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

                try:
                    _dev_client = LemonadeClient(verbose=False)
                    _sysinfo = _dev_client.get_system_info()
                    _devices = _sysinfo.get("devices", {})

                    effective_device = resolve_effective_device(
                        effective_device, device_was_explicit, _devices
                    )
                except LemonadeClientError as _probe_err:
                    # Only treat "server not reachable yet" (connection refused /
                    # timeout) as a soft pass. A reachable but broken server
                    # (HTTP 4xx/5xx, 401 auth) must NOT silently let an explicit
                    # --device proceed; surface it so the user sees the real
                    # failure. The agent runtime re-validates the device
                    # regardless (LemonadeManager.ensure_ready).
                    #
                    # Prefer the original requests exception preserved in the
                    # cause chain over message-string matching — the client
                    # wraps RequestException without an HTTP status, but the
                    # exact wrapper text can change independently of this guard.
                    import requests

                    _cause = _probe_err.__cause__ or _probe_err.__context__
                    _unreachable = isinstance(
                        _cause,
                        (
                            requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout,
                        ),
                    ) or (
                        # Fallback for wrappers that don't preserve __cause__.
                        str(_probe_err).startswith("Request failed:")
                        and "with status" not in str(_probe_err)
                    )
                    if not _unreachable:
                        raise
                    log.debug(
                        "Device probe: Lemonade not reachable yet (%s)", _probe_err
                    )

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
                device=effective_device,
                # None falls through to the global default (default_max_steps,
                # honoring $GAIA_AGENT_MAX_STEPS) resolved in Agent.__init__.
                max_steps=kwargs.get("max_steps"),
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

            # Create initial session if not loading one. ``_ensure_tool_loader_reset``
            # is a ChatAgent method (#2323); guard with hasattr since cli.py (core)
            # and gaia-agent-chat (an independently-versioned hub wheel) can drift —
            # an older installed wheel won't have it yet. It logs its own
            # "Created new session" line, so the fallback branch below does too
            # (for parity), but the two are not both reachable in one call.
            if not agent.current_session:
                if hasattr(agent, "_ensure_tool_loader_reset"):
                    agent._ensure_tool_loader_reset()
                else:
                    agent.current_session = agent.session_manager.create_session()
                    try:
                        if hasattr(agent, "tool_loader"):
                            agent.tool_loader.reset_session()
                    except Exception as e:
                        log.debug("Tool loader session reset skipped: %s", e)
                    log.debug(
                        f"Created new session: {agent.current_session.session_id}"
                    )

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

            _offer_first_boot_onboarding()

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
    """Rebuild the Agent UI frontend if source files are newer than dist.

    Never raises -- a hard build failure (too-old Node, build error) is a
    degraded launch, not a crash: ensure_webui_built() already reports the
    failure through warn_fn; the caller still starts the server, which
    serves its own "no frontend build" fallback page.
    """
    from gaia.ui.build import ensure_webui_built

    return ensure_webui_built(
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

        # Advertise 127.0.0.1 to match the bind host below — on Windows
        # "localhost" can resolve to IPv6 ::1 and fail against this IPv4 listener.
        log.info(f"Starting GAIA Agent UI on http://127.0.0.1:{port}")
        print(f"Starting GAIA Agent UI on http://127.0.0.1:{port}")
        print(f"   Open your browser to http://127.0.0.1:{port}")
        print("   Press Ctrl+C to stop")
        print()
        if not base_url:
            print("   Prerequisites:")
            print("     1. Models downloaded  : gaia init  (first time only, ~4 GB)")
            print(f"     2. Lemonade running   : {describe_start_hint().instruction}")
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
        print('     uv pip install "amd-gaia[ui]"')
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

        # ChatAgent ships as the standalone gaia-agent-chat wheel (#1102).
        try:
            from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
            from gaia_agent_chat.app import interactive_mode
        except ImportError as e:
            raise RuntimeError(
                agent_not_installed_message(
                    "The chat agent is not installed",
                    "gaia-agent-chat",
                    next_step="Then re-run `gaia chat`.",
                )
            ) from e

        config = ChatAgentConfig(
            base_url=base_url or os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL),
            silent_mode=True,
        )
        agent = ChatAgent(config)

        # ``_ensure_tool_loader_reset`` is a ChatAgent method (#2323); guard with
        # hasattr since cli.py (core) and gaia-agent-chat (an independently
        # versioned hub wheel) can drift — an older installed wheel won't have it.
        if not agent.current_session:
            if hasattr(agent, "_ensure_tool_loader_reset"):
                agent._ensure_tool_loader_reset()
            else:
                agent.current_session = agent.session_manager.create_session()
                try:
                    if hasattr(agent, "tool_loader"):
                        agent.tool_loader.reset_session()
                except Exception as e:
                    log.debug("Tool loader session reset skipped: %s", e)

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
        print()
        print("  Run 'gaia --help' for the full command list.")
    else:
        print(f"  Unknown option: {choice}")
        print("  Run 'gaia --help' for all commands.")


def _compare_benchmark_ctx(current_ctx, baseline, baseline_path):
    """Guard ``gaia eval benchmark --compare`` against cross-ctx comparisons (#1892).

    A throughput/quality baseline measured at one context window is not
    comparable to a run measured at another. Raises ``SystemExit`` when both
    sides record a ``ctx_size`` and they differ; prints a single loud legacy
    warning (and proceeds) when either side lacks the field.
    """
    baseline_ctx = baseline.get("ctx_size")
    if baseline_ctx is None:
        print(
            f"[COMPARE] WARNING: baseline {baseline_path} records no ctx_size "
            "(legacy pre-#1892 card, measured at an unpinned window — "
            "implicitly the model's 64K registry floor). The comparison "
            "proceeds but is NOT ctx-verified; re-record the baseline with "
            "--ctx-size to make it comparable."
        )
        return
    if current_ctx is None:
        print(
            f"[COMPARE] WARNING: this run records no ctx_size but baseline "
            f"{baseline_path} was measured at ctx={baseline_ctx}; the "
            "comparison proceeds but is NOT ctx-verified."
        )
        return
    if int(baseline_ctx) != int(current_ctx):
        raise SystemExit(
            f"[ERROR] ctx mismatch: this run measured ctx_size={current_ctx} "
            f"but the baseline at {baseline_path} records "
            f"ctx_size={baseline_ctx}. Comparing runs across different "
            f"context windows is invalid — rerun with --ctx-size "
            f"{baseline_ctx}, or record a new baseline at {current_ctx}."
        )


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
    # Shared --config flag. Attached only to commands that read the persistent
    # config (chat/llm/prompt + the `gaia config` subcommands).
    config_path_parser = argparse.ArgumentParser(add_help=False)
    config_path_parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to a GAIA config file (default: ~/.gaia/config.json or "
        "$GAIA_CONFIG_FILE). Used to resolve default_model.",
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
        default=None,
        help="Maximum conversation steps. Defaults to the global agent step "
        "limit (50, or $GAIA_AGENT_MAX_STEPS if set).",
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
        "prompt",
        help="Send a single prompt to Gaia",
        parents=[parent_parser, config_path_parser],
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
        parents=[parent_parser, config_path_parser],
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
    email_parser.add_argument(
        "--spec",
        action="store_true",
        help=(
            "Generate the HTML endpoint spec page, write it to disk, and open "
            "it in a browser. No LLM or Lemonade required."
        ),
    )
    email_parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Path to write the HTML spec (default: ~/.gaia/email/endpoint-spec.html). "
            "Only used with --spec."
        ),
    )

    # Autonomy control surface (#2516) — thin client over the sidecar's
    # session-scoped /v1/email/agent/autonomy* REST routes, relayed through
    # the daemon like every other `gaia email` command.
    email_subparsers = email_parser.add_subparsers(
        dest="email_action", help="Email agent subcommand"
    )
    autonomy_parser = email_subparsers.add_parser(
        "autonomy",
        help="Inspect or control the autonomy engine (status|set-level|pause|resume|run|trust|kill)",
    )
    autonomy_subparsers = autonomy_parser.add_subparsers(
        dest="autonomy_action", help="Autonomy action to perform"
    )
    _AUTONOMY_SESSION_HELP = (
        "Session id the autonomy state lives on (default: 'cli', shared across "
        "invocations so a level you set stays set)."
    )

    autonomy_status_parser = autonomy_subparsers.add_parser(
        "status", help="Show the current autonomy level and trust summary"
    )
    autonomy_status_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_trust_parser = autonomy_subparsers.add_parser(
        "trust", help="Show the earned-trust ledger (per action/scope tally)"
    )
    autonomy_trust_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_set_level_parser = autonomy_subparsers.add_parser(
        "set-level", help="Set the autonomy level explicitly"
    )
    autonomy_set_level_parser.add_argument(
        "level", choices=["off", "suggest", "earn_trust", "full"]
    )
    autonomy_set_level_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_pause_parser = autonomy_subparsers.add_parser(
        "pause", help="Stop autonomous activity (sets the level to 'off')"
    )
    autonomy_pause_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_resume_parser = autonomy_subparsers.add_parser(
        "resume", help="Resume autonomous activity at the given level"
    )
    autonomy_resume_parser.add_argument(
        "--level",
        choices=["suggest", "earn_trust", "full"],
        default="earn_trust",
        help="Level to resume at (default: earn_trust).",
    )
    autonomy_resume_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_kill_parser = autonomy_subparsers.add_parser(
        "kill", help="Kill switch — immediately sets the level to 'off'"
    )
    autonomy_kill_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

    autonomy_run_parser = autonomy_subparsers.add_parser(
        "run", help="Trigger one observe->decide->act autonomy cycle now"
    )
    autonomy_run_parser.add_argument(
        "--max-messages",
        type=int,
        default=25,
        help="Inbox budget for this cycle (default: 25).",
    )
    autonomy_run_parser.add_argument(
        "--session-id", default="cli", help=_AUTONOMY_SESSION_HELP
    )

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

    # Schedule command — cron-based recurring skill/prompt dispatch (issue #892)
    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Run a skill or prompt on a cron schedule "
        "(add|list|show|remove|pause|resume|run|daemon)",
        parents=[parent_parser],
    )
    schedule_subparsers = schedule_parser.add_subparsers(
        dest="schedule_action", help="schedule action to perform"
    )

    s_add = schedule_subparsers.add_parser("add", help="Register a new schedule")
    s_add.add_argument("--name", required=True, help="Unique schedule name")
    s_add.add_argument(
        "--cron", required=True, help='Cron expression, e.g. "0 7 * * 1-5"'
    )
    s_add_what = s_add.add_mutually_exclusive_group(required=True)
    s_add_what.add_argument("--prompt", help="One-shot prompt to run on each fire")
    s_add_what.add_argument(
        "--skill",
        help="Skill to run — the scheduler is not wired to the skills runtime "
        "yet; rejected at add time. Use --prompt instead.",
    )
    s_add.add_argument(
        "--sink",
        default="stdout",
        help="Where output goes: stdout (default) | file:<path> | notification | telegram",
    )
    s_add.add_argument(
        "--to", help="Recipient for the telegram sink (Telegram user/chat id)"
    )

    s_list = schedule_subparsers.add_parser(
        "list", help="List schedules with next fire time"
    )
    s_list.set_defaults(schedule_action="list")

    s_show = schedule_subparsers.add_parser("show", help="Show one schedule")
    s_show.add_argument("name", help="Schedule name")

    s_remove = schedule_subparsers.add_parser("remove", help="Remove a schedule")
    s_remove.add_argument("name", help="Schedule name")

    s_pause = schedule_subparsers.add_parser(
        "pause", help="Disable a schedule without deleting it"
    )
    s_pause.add_argument("name", help="Schedule name")

    s_resume = schedule_subparsers.add_parser(
        "resume", help="Re-enable a paused schedule"
    )
    s_resume.add_argument("name", help="Schedule name")

    s_run = schedule_subparsers.add_parser(
        "run", help="Fire a schedule once now (for testing)"
    )
    s_run.add_argument("name", help="Schedule name")

    s_daemon = schedule_subparsers.add_parser(
        "daemon", help="Run the long-lived scheduler (blocks until interrupted)"
    )
    s_daemon.set_defaults(schedule_action="daemon")

    schedule_parser.set_defaults(action="schedule")

    # Knowledge command — web research via the Tavily wrapper
    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Web research via Tavily (search|extract|usage), with caching and a credit budget",
    )
    knowledge_subparsers = knowledge_parser.add_subparsers(
        dest="knowledge_action", help="knowledge action to perform"
    )

    k_search = knowledge_subparsers.add_parser("search", help="Run a web search")
    k_search.add_argument("query", help="Search query")
    k_search.add_argument(
        "--max-results", type=int, default=5, help="Max results (default: 5)"
    )
    k_search.add_argument(
        "--depth",
        choices=("basic", "advanced"),
        default="basic",
        help="Search depth — advanced costs more credits (default: basic)",
    )
    k_search.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Credit cap for this session; omit for unlimited",
    )
    k_search.add_argument(
        "--no-block",
        action="store_true",
        help="Warn instead of blocking when the budget cap is exceeded",
    )

    k_extract = knowledge_subparsers.add_parser(
        "extract", help="Extract clean content from one or more URLs (requires Tavily)"
    )
    k_extract.add_argument("urls", nargs="+", help="One or more URLs to extract")
    k_extract.add_argument(
        "--depth",
        choices=("basic", "advanced"),
        default="basic",
        help="Extract depth (default: basic)",
    )
    k_extract.add_argument(
        "--budget", type=int, default=None, help="Credit cap for this session"
    )
    k_extract.add_argument(
        "--no-block",
        action="store_true",
        help="Warn instead of blocking when the budget cap is exceeded",
    )

    knowledge_subparsers.add_parser("usage", help="Show cached credit-usage totals")
    knowledge_parser.set_defaults(action="knowledge")

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

  # List available agents and their required models
  gaia download --list

  # Delete all downloaded GAIA models (free up disk space)
  gaia download --clear-cache

Available agents: chat, talk, rag, vlm, minimal, mcp
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
        parents=[parent_parser, config_path_parser],
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
        default=DEFAULT_CLAUDE_MODEL,
        help=f"Judge model that scores the run (default: {DEFAULT_CLAUDE_MODEL})",
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

    # Email-triage throughput benchmark subcommand: gaia eval benchmark [OPTIONS] (#1233)
    benchmark_eval_parser = eval_subparsers.add_parser(
        "benchmark",
        help="Email-triage throughput benchmark (TTFT / tok-per-sec / latency)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Benchmark the primary on-device model over the committed corpus
  gaia eval benchmark --model Gemma-4-E4B-it-GGUF --limit 50

  # Repeat 3 times for variance + compare against a saved baseline
  gaia eval benchmark --experiments 3 --compare eval/results/bench_baseline.json

  # Save this run as the throughput baseline
  gaia eval benchmark --save-baseline
        """,
    )
    benchmark_eval_parser.add_argument(
        "--model",
        default="Gemma-4-E4B-it-GGUF",
        help="Lemonade model id to benchmark (default: Gemma-4-E4B-it-GGUF).",
    )
    benchmark_eval_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Target message count steered to the agent's triage call "
        "(default: 50). The committed stub corpus is smaller, so the "
        "effective count is min(limit, corpus size).",
    )
    benchmark_eval_parser.add_argument(
        "--experiments",
        type=int,
        default=1,
        help="Repeat count per model for variance analysis (default: 1).",
    )
    benchmark_eval_parser.add_argument(
        "--mbox-path",
        default=None,
        help="MBOX corpus to triage (default: the committed stub fixture).",
    )
    benchmark_eval_parser.add_argument(
        "--ground-truth",
        default=None,
        help="Ground-truth JSON for quality scoring (default: the fixture's).",
    )
    benchmark_eval_parser.add_argument(
        "--backend",
        default=None,
        help="Lemonade base URL (default: the agent's configured base URL).",
    )
    benchmark_eval_parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write scorecard.json / summary.md (optional).",
    )
    benchmark_eval_parser.add_argument(
        "--compare",
        metavar="PATH",
        default=None,
        help="Compare this run's throughput against a saved baseline scorecard.",
    )
    benchmark_eval_parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save this run's scorecard as the throughput baseline.",
    )
    benchmark_eval_parser.add_argument(
        "--ctx-size",
        type=int,
        default=None,
        help="Exact ctx window to pin the model load to (#1892 envelope: "
        "16384 target / 32768 max; default: the target). The run verifies "
        "the pin by readback and stamps it into scorecard.json/quality.json. "
        "Values above the envelope max are allowed for deliberate sweeps "
        "but warned loudly.",
    )

    # Replay real Claude Code sessions against the live agent: gaia eval sessions
    sessions_eval_parser = eval_subparsers.add_parser(
        "sessions",
        help="Score the agent against real Claude Code sessions, via a live TUI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build a 10-case dataset and replay it through a running TUI
  gaia eval sessions --control ~/.gaia/tui/control.json --limit 10

  # Dataset only — no agent, no judge
  gaia eval sessions --dataset-only --out eval/results/sessions

A TUI must already be running with --control-port; this drives THAT session so
the run is visible on screen. Judging needs ANTHROPIC_API_KEY (the repo .env is
read automatically).
""",
    )
    sessions_eval_parser.add_argument(
        "--control",
        default=None,
        help="Path to the running TUI's control.json (default: "
        "~/.gaia/tui/control.json, or $GAIA_TUI_HOME/control.json)",
    )
    sessions_eval_parser.add_argument(
        "--limit", type=int, default=10, help="Number of cases (default: 10)"
    )
    sessions_eval_parser.add_argument(
        "--project",
        default="",
        help="Only sessions from one project directory under ~/.claude/projects",
    )
    sessions_eval_parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: eval/results/sessions-<timestamp>)",
    )
    sessions_eval_parser.add_argument(
        "--dataset-only",
        action="store_true",
        help="Build the dataset and stop — no agent run, no judge",
    )

    # Code generation + editing, scored by running the tests: gaia eval code
    code_eval_parser = eval_subparsers.add_parser(
        "code",
        help="Code generation and editing benchmark, scored by running the tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gaia eval code --control ~/.gaia/tui/control.json

Each task ships a project and a test suite. The agent is asked to change it and
the suite decides — no LLM judge. A TUI must already be running with
--control-port; this drives THAT session so the run is visible.
""",
    )
    code_eval_parser.add_argument(
        "--control", default=None, help="Path to the running TUI's control.json"
    )
    code_eval_parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: eval/results/code-<timestamp>)",
    )
    code_eval_parser.add_argument(
        "--workspace",
        default=None,
        help="Where to materialize the task projects (default: a temp directory)",
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
        "--auth-token",
        help=(
            "Require 'Authorization: Bearer <token>' on every request except "
            "/health. Defaults to $GAIA_MCP_AUTH_TOKEN. Without it the bridge "
            "is unauthenticated."
        ),
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
    mcp_status_parser.add_argument(
        "--auth-token",
        help="Bearer token if the bridge requires one (default: $GAIA_MCP_AUTH_TOKEN)",
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
    mcp_test_parser.add_argument(
        "--auth-token",
        help="Bearer token if the bridge requires one (default: $GAIA_MCP_AUTH_TOKEN)",
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
    mcp_agent_parser.add_argument(
        "--auth-token",
        help="Bearer token if the bridge requires one (default: $GAIA_MCP_AUTH_TOKEN)",
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

    mcp_tui_parser = mcp_subparsers.add_parser(
        "tui",
        help="Start TUI control MCP server (drives a running `gaia tui --control`)",
    )
    mcp_tui_parser.add_argument(
        "--host", default="localhost", help="Host to bind to (default: localhost)"
    )
    mcp_tui_parser.add_argument(
        "--port", type=int, default=8767, help="Port to listen on (default: 8767)"
    )
    mcp_tui_parser.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport instead of HTTP (for Claude Code integration)",
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

    # Lemonade command (embedded server lifecycle)
    lemonade_parser = subparsers.add_parser(
        "lemonade", help="Manage the Lemonade Server GAIA runs against"
    )
    lemonade_subparsers = lemonade_parser.add_subparsers(
        dest="lemonade_action", help="Lemonade action to perform"
    )
    embedded_parser = lemonade_subparsers.add_parser(
        "embedded",
        help="Manage GAIA's private, self-contained Lemonade instance",
    )
    embedded_subparsers = embedded_parser.add_subparsers(
        dest="embedded_action", help="Embedded Lemonade action to perform"
    )
    embedded_start_parser = embedded_subparsers.add_parser(
        "start", help="Start the private Lemonade instance (downloads it if missing)"
    )
    embedded_start_parser.add_argument(
        "--port",
        type=int,
        help="Port to bind (default: a free port chosen at start)",
    )
    embedded_start_parser.add_argument(
        "--no-install",
        action="store_true",
        help="Fail instead of downloading when the artifact is missing",
    )
    embedded_start_parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for the server to become healthy (default: 60)",
    )
    embedded_subparsers.add_parser("stop", help="Stop the private Lemonade instance")
    embedded_subparsers.add_parser(
        "status", help="Show whether the private instance is installed and running"
    )
    embedded_install_parser = embedded_subparsers.add_parser(
        "install", help="Download and unpack the embeddable artifact"
    )
    embedded_install_parser.add_argument(
        "--force", action="store_true", help="Reinstall even if already unpacked"
    )
    embedded_backend_parser = embedded_subparsers.add_parser(
        "install-backend",
        help="Download an inference backend into the private cache",
    )
    embedded_backend_parser.add_argument(
        "spec", help="Backend spec, e.g. llamacpp:vulkan (recipe:backend)"
    )

    # Daemon command (headless custody daemon lifecycle)
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Manage the headless GAIA daemon (single machine-wide custody process)",
    )
    daemon_subparsers = daemon_parser.add_subparsers(
        dest="daemon_action", help="Daemon action to perform"
    )
    daemon_subparsers.add_parser(
        "start", help="Start the daemon (or attach if one is already running)"
    )
    daemon_subparsers.add_parser("status", help="Show daemon pid / port / uptime")
    daemon_subparsers.add_parser("stop", help="Stop the running daemon")
    daemon_subparsers.add_parser(
        "restart", help="Restart the daemon (stop if running, then start)"
    )
    daemon_subparsers.add_parser(
        "agents", help="List the sidecar agents the daemon supervises"
    )
    daemon_start_agent_parser = daemon_subparsers.add_parser(
        "start-agent",
        help="Start (or attach to) one agent's sidecar under the daemon",
    )
    daemon_start_agent_parser.add_argument(
        "agent_id", help="Agent to start (e.g. email)"
    )
    daemon_start_agent_parser.add_argument(
        "--mode",
        choices=["user", "dev"],
        default=None,
        help="Sidecar mode: user (frozen binary, default) or dev (from source)",
    )
    daemon_start_agent_parser.add_argument(
        "--dev-src-dir",
        default=None,
        help=(
            "Explicit dev-mode source directory (escape hatch for --mode dev "
            "when this shell isn't inside a git work tree). Default: resolved "
            "from this checkout via `git rev-parse --show-toplevel`."
        ),
    )
    daemon_stop_agent_parser = daemon_subparsers.add_parser(
        "stop-agent", help="Stop one agent's sidecar (the daemon keeps running)"
    )
    daemon_stop_agent_parser.add_argument("agent_id", help="Agent to stop")
    daemon_logs_parser = daemon_subparsers.add_parser(
        "logs", help="Show the daemon log (or a sidecar's log with --agent)"
    )
    daemon_logs_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=100,
        help="Number of trailing log lines to show (default: 100)",
    )
    daemon_logs_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow the log (like tail -f); Ctrl-C to stop",
    )
    daemon_logs_parser.add_argument(
        "--agent",
        metavar="AGENT_ID",
        default=None,
        help=(
            "Show the named sidecar agent's log (the rich agent-loop activity — "
            "tool calls, triage decisions) instead of the host daemon log. "
            "E.g. `gaia daemon logs --agent email -f`."
        ),
    )
    daemon_parser.set_defaults(action="daemon")

    # Agent Hub command (install/uninstall agents through the daemon)
    hub_parser = subparsers.add_parser(
        "hub",
        help="Browse, install and uninstall agents from the GAIA Agent Hub",
    )
    hub_subparsers = hub_parser.add_subparsers(
        dest="hub_action", help="Hub action to perform"
    )
    hub_list_parser = hub_subparsers.add_parser(
        "list", help="List Agent Hub agents and which of them are installed"
    )
    hub_list_parser.add_argument(
        "--installed",
        action="store_true",
        help="Show only agents installed on this machine (works offline)",
    )
    hub_list_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the 5-minute catalog cache and re-fetch from the hub",
    )
    hub_install_parser = hub_subparsers.add_parser(
        "install", help="Install an agent from the Agent Hub"
    )
    hub_install_parser.add_argument("agent_id", help="Agent to install (e.g. email)")
    hub_install_parser.add_argument(
        "--version",
        default=None,
        help="Version to install (default: the hub's latest)",
    )
    hub_install_parser.add_argument(
        "--trust",
        action="store_true",
        dest="trusted",
        help=(
            "Explicitly trust a non-verified agent (it runs third-party code on "
            "your machine). Required for any community/experimental package."
        ),
    )
    hub_uninstall_parser = hub_subparsers.add_parser(
        "uninstall", help="Uninstall an agent (stops its sidecar first)"
    )
    hub_uninstall_parser.add_argument("agent_id", help="Agent to uninstall")
    hub_parser.set_defaults(action="hub")

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
        help="Author and manage agents (init/version/test, export/import)",
    )
    agent_subparsers = agent_parser.add_subparsers(
        dest="agent_action", help="Agent action to perform"
    )

    # Developer workflow (init / version / test) lives in gaia.cli_agent to keep
    # this file lean. It adds its subcommands to the same 'agent' group.
    from gaia import cli_agent

    cli_agent.register_subparsers(agent_subparsers)

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

    # Agent install command — fetch + verify + install a published agent from
    # the Agent Hub. The headless equivalent of the Agent UI's Install button.
    agent_install_parser = agent_subparsers.add_parser(
        "install",
        help="Install a published agent from the Agent Hub into ~/.gaia/agents/",
    )
    agent_install_parser.add_argument(
        "agent_id",
        help="Hub agent id to install (e.g. 'email').",
    )
    agent_install_parser.add_argument(
        "--version",
        default=None,
        help="Specific version to install (default: the hub's latest).",
    )
    agent_install_parser.add_argument(
        "--trust",
        action="store_true",
        dest="trusted",
        help=(
            "Explicitly trust a non-verified agent (it runs third-party code on "
            "your machine). Required for any community/experimental package."
        ),
    )

    # Agent list command — show installed agents + what the Agent Hub offers, so
    # `gaia agent install <id>` is discoverable (and the id referenced in install
    # errors resolves to a real command).
    agent_subparsers.add_parser(
        "list",
        help="List installed agents and agents available from the Agent Hub",
    )

    # Connectors framework (issue #927, parent of #915) — manage OAuth +
    # MCP-server connectors + per-agent grants. The subparser tree lives in
    # gaia.connectors.cli to keep this file lean.
    from gaia.connectors import cli as connectors_cli

    connectors_cli.add_subparser(subparsers)

    # Skills runtime (issue #888) — SKILL.md discovery, scaffolding, and
    # import/export. The subparser tree lives in gaia.skills.cli.
    from gaia.skills import cli as skills_cli

    skills_cli.add_subparser(subparsers)

    # Persistent CLI config (~/.gaia/config.json) — e.g. a default model so
    # users don't have to pass --model on every chat/llm/prompt (issue #98).
    config_parser = subparsers.add_parser(
        "config",
        help="Manage persistent GAIA config (~/.gaia/config.json)",
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_action", help="Config action"
    )
    config_subparsers.add_parser(
        "show",
        help="Show current config and the config file path",
        parents=[config_path_parser],
    )
    config_get_parser = config_subparsers.add_parser(
        "get",
        help="Get a config value, e.g. `gaia config get default_model`",
        parents=[config_path_parser],
    )
    config_get_parser.add_argument("key", help="Config key to read")
    config_set_parser = config_subparsers.add_parser(
        "set",
        help="Set a config value, e.g. `gaia config set default_model Gemma-4-E4B-it-GGUF`",
        parents=[config_path_parser],
    )
    config_set_parser.add_argument("key", help="Config key to set")
    config_set_parser.add_argument("value", help="Value to assign")
    config_parser.set_defaults(action="config")

    # Init command (one-stop GAIA setup)
    # Note: Does not use parent_parser to avoid showing irrelevant global options
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize GAIA: install Lemonade and download models",
    )
    init_parser.add_argument(
        "--profile",
        "-p",
        # Literal, not an import: gaia.installer.init_command costs ~3s to
        # import and build_parser() runs on every `gaia` invocation. Pinned to
        # init_command.DEFAULT_INIT_PROFILE by a test so it cannot drift.
        default="gaia",
        choices=[
            "gaia",
            "minimal",
            "sd",
            "chat",
            "rag",
            "mcp",
            "vlm",
            "email",
            "npu",
            "all",
        ],
        help="Profile to initialize: gaia (the flagship agent), minimal, sd (image gen), "
        "chat, rag, mcp, vlm (vision), email (Gmail/Outlook triage), npu (Ryzen AI NPU), "
        "all (default: gaia)",
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
        "--skip-webui-build",
        action="store_true",
        help="Skip building the Agent UI frontend (same effect as GAIA_SKIP_WEBUI_BUILD)",
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
    init_parser.add_argument(
        "--skip-chat-model",
        action="store_true",
        help="Skip the profile's chat LLM (e.g. Gemma-4-E4B-it-GGUF) but still "
        "install Lemonade and any embedding model the profile needs. For a "
        "session backed by a remote LLM (e.g. the TUI's --use-claude) that "
        "never calls the local chat model but still needs Lemonade for "
        "RAG/memory embeddings.",
    )
    init_parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether this profile is already set up and exit — no "
        "install, no download, no side effects. Exit code 0 means ready, "
        "1 means `gaia init` still has work to do.",
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


def _handle_schedule(args):
    """Dispatch `gaia schedule <action>` (issue #892)."""
    from gaia.schedule import daemon as schedule_daemon
    from gaia.schedule import runner as schedule_runner
    from gaia.schedule.store import Schedule, TomlScheduleStore

    action = getattr(args, "schedule_action", None)
    store = TomlScheduleStore()

    if action == "add":
        # --skill schedules would register fine but raise on every fire: the
        # skills runtime exists, the scheduler just doesn't invoke it yet.
        if getattr(args, "skill", None):
            print(
                f"❌ Cannot add schedule {args.name!r}: --skill is not supported yet. "
                "The skills runtime ships (see 'gaia skill list'), but the scheduler "
                "is not wired to it, so a skill-backed schedule would never produce "
                "output.\n"
                "Use --prompt instead, e.g.:\n"
                f'  gaia schedule add --name {args.name} --cron "{args.cron}" '
                '--prompt "<text>"\n'
                "Track progress: https://github.com/amd/gaia/issues/1019",
                file=sys.stderr,
            )
            sys.exit(1)
        sink_args = {}
        if getattr(args, "to", None):
            sink_args["to"] = args.to
        schedule = Schedule(
            name=args.name,
            cron=args.cron,
            skill=getattr(args, "skill", None),
            prompt=getattr(args, "prompt", None),
            sink=args.sink,
            sink_args=sink_args,
        )
        store.add(schedule)
        print(f"✅ Added schedule {schedule.name!r} ({schedule.cron})")
        return

    if action == "list":
        schedules = store.load()
        if not schedules:
            print("No schedules registered. Add one with `gaia schedule add`.")
            return
        for s in schedules.values():
            state = "enabled" if s.enabled else "paused"
            nxt = schedule_daemon.next_fire_time(s.cron) if s.enabled else None
            print(
                f"{s.name}  [{state}]  cron={s.cron!r}  sink={s.sink}"
                + (f"  next={nxt}" if nxt else "")
            )
        return

    if action == "show":
        s = store.get(args.name)
        print(f"name:       {s.name}")
        print(f"cron:       {s.cron}")
        print(f"target:     {'skill=' + s.skill if s.skill else 'prompt=' + s.prompt}")
        print(f"sink:       {s.sink}  args={s.sink_args}")
        print(f"enabled:    {s.enabled}")
        print(f"created_at: {s.created_at}")
        print(f"next_fire:  {schedule_daemon.next_fire_time(s.cron)}")
        return

    if action == "remove":
        store.remove(args.name)
        print(f"✅ Removed schedule {args.name!r}")
        return

    if action in ("pause", "resume"):
        enabled = action == "resume"
        store.set_enabled(args.name, enabled)
        print(f"✅ {'Resumed' if enabled else 'Paused'} schedule {args.name!r}")
        return

    if action == "run":
        from datetime import datetime, timezone

        schedule = store.get(args.name)
        schedule_runner.fire(schedule)
        store.mark_run(
            schedule.name,
            datetime.now(timezone.utc).isoformat(),
            next_run=schedule_daemon.next_fire_time(schedule.cron),
        )
        return

    if action == "daemon":
        schedule_daemon.run_daemon()
        return

    print(
        "No schedule action specified. Use: "
        "gaia schedule add|list|show|remove|pause|resume|run|daemon",
        file=sys.stderr,
    )


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

    # ── Model-slot broker (#2248) ────────────────────────────────────────
    # A CLI run is host-side and not daemon-spawned, so it inherits no broker
    # URL and its model loads would bypass the broker, race-evicting models a
    # running sidecar just loaded. Opting in costs nothing here: it only sets a
    # flag, and the daemon is probed lazily at the first actual model load — so
    # commands that never load a model never probe. Discovery attaches to a LIVE
    # daemon only and never starts one, leaving standalone `gaia llm` on a
    # daemon-less machine exactly as it was (nothing else holds the slot).
    from gaia.daemon.broker_client import enable_broker_discovery

    enable_broker_discovery()

    # Apply the persistent default_model (issue #98) for model-bearing commands.
    # Precedence: explicit --model flag > config default_model > built-in default.
    # An explicit `chat --device` requests a device-specific model, so it keeps
    # precedence over the config default there.
    if (
        args.action in ("prompt", "chat", "llm")
        and getattr(args, "model", None) is None
    ):
        from gaia.config import GaiaConfig, GaiaConfigError

        try:
            _cfg = GaiaConfig.load(getattr(args, "config", None))
        except GaiaConfigError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        # builtin_default=None: leave args.model unset when no config default so
        # each command keeps applying its own built-in default downstream.
        if not (args.action == "chat" and getattr(args, "device", None)):
            resolved = _cfg.resolve_model(args.model, None)
            if resolved:
                args.model = resolved
                log.debug("Using default_model from config: %s", resolved)

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

            try:
                run_telegram(
                    token=args.token,
                    allowed_users=allowed,
                    background=getattr(args, "background", False),
                )
            except RuntimeError as e:
                print(f"❌ {e}", file=sys.stderr)
                sys.exit(1)
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

    if args.action == "schedule":
        _handle_schedule(args)
        return

    # Handle core Gaia CLI commands
    if args.action in ["prompt", "chat", "talk", "stats"]:
        kwargs = {
            k: v for k, v in vars(args).items() if v is not None and k != "action"
        }
        # --config is only an input to model resolution (already applied to
        # args.model above); it's not a runtime parameter for the agents.
        kwargs.pop("config", None)
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
            # A refusal must not report success — `gaia kill && next-step`
            # would otherwise run next-step having killed nothing.
            print("❌ gaia kill needs a target:")
            print("     --lemonade        stop Lemonade Server (port 13305)")
            print("     --port <number>   kill the GAIA/Lemonade process")
            print("                       listening on <number>")
            print(
                "   Both target a port. A stray GAIA process that is not "
                "holding a port must be killed by PID."
            )
            sys.exit(1)
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
                        "   1. Close any running GAIA commands (gaia chat, gaia email, etc.)"
                    )
                    print(
                        f"   2. Restart Lemonade Server "
                        f"({describe_start_hint().instruction})"
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

        # Code generation + editing benchmark: gaia eval code
        if getattr(args, "eval_command", None) == "code":
            from gaia.eval.code_bench import run, save, scorecard
            from gaia.eval.session_eval import TUIDriver

            control = args.control or os.path.join(
                os.environ.get("GAIA_TUI_HOME")
                or os.path.join(os.path.expanduser("~"), ".gaia", "tui"),
                "control.json",
            )
            if not Path(control).is_file():
                raise FileNotFoundError(
                    f"No running TUI found at {control}. Start one with "
                    "`gaia-drive run gaia --control-port 8817`, or pass --control."
                )

            out_dir = Path(
                args.out or f"eval/results/code-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            workspace = Path(args.workspace) if args.workspace else None

            def _progress(index, total, result):
                mark = "OK " if result.solved else ("ERR" if result.error else "FAIL")
                claim = " CLAIMED-SUCCESS" if result.dishonest else ""
                print(
                    f"  [{index}/{total}] {mark} {result.id} "
                    f"{result.passed_after}P/{result.failed_after}F "
                    f"({result.elapsed_s}s){claim}"
                )

            print(f"[RUN] driving the TUI at {control}")
            results = run(
                TUIDriver(Path(control)), root=workspace, on_progress=_progress
            )
            card = scorecard(results)
            report_path = save(out_dir, results, card, "live TUI")
            print()
            print(
                f"[SCORE] solved {card['solved']}/{card['ran']} "
                f"({card['solve_rate']}%) · generation "
                f"{card['generation_solved']}/{card['generation_total']} · editing "
                f"{card['editing_solved']}/{card['editing_total']} · "
                f"{card['dishonest']} false success claim(s)"
            )
            print(f"[OUTPUT] {report_path.resolve()}")
            return

        # Replay real Claude Code sessions: gaia eval sessions
        if getattr(args, "eval_command", None) == "sessions":
            from gaia.eval.session_dataset import build_dataset
            from gaia.eval.session_eval import TUIDriver, run, save, scorecard

            out_dir = Path(
                args.out or f"eval/results/sessions-{time.strftime('%Y%m%d-%H%M%S')}"
            )
            dataset = build_dataset(project=args.project, limit=args.limit)
            print(
                f"[DATASET] {dataset['sessions_scanned']} session(s), "
                f"{dataset['turns_extracted']} turn(s) -> {len(dataset['cases'])} case(s)"
            )
            if args.dataset_only:
                out_dir.mkdir(parents=True, exist_ok=True)
                path = out_dir / "dataset.json"
                path.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
                print(f"[OUTPUT] {path.resolve()}")
                return

            control = args.control or os.path.join(
                os.environ.get("GAIA_TUI_HOME")
                or os.path.join(os.path.expanduser("~"), ".gaia", "tui"),
                "control.json",
            )
            if not Path(control).is_file():
                raise FileNotFoundError(
                    f"No running TUI found at {control}. Start one with "
                    "`gaia-drive run gaia --control-port 8817`, or pass "
                    "--control <path to its control.json>."
                )

            from gaia.eval.claude import ClaudeClient

            driver = TUIDriver(Path(control))
            client = ClaudeClient()

            def _progress(index, total, result):
                mark = "OK " if result.passed else ("ERR" if result.error else "LOW")
                print(
                    f"  [{index}/{total}] {mark} {result.id} "
                    f"score={result.score} ({result.elapsed_s}s) {result.verdict}"
                )

            print(f"[RUN] driving the TUI at {control}")
            results = run(dataset, driver, client, on_progress=_progress)
            card = scorecard(results)
            report_path = save(out_dir, dataset, results, card, "live TUI")
            print()
            print(
                f"[SCORE] mean {card['mean_score']}/5 · pass rate "
                f"{card['pass_rate']}% · {card['errors']} error(s)"
            )
            print(f"[OUTPUT] {report_path.resolve()}")
            return

        # Email-triage throughput benchmark: gaia eval benchmark (#1233)
        if getattr(args, "eval_command", None) == "benchmark":
            import tempfile

            from gaia.eval.benchmark import (
                THROUGHPUT_BAR_TPS,
                THROUGHPUT_STRETCH_TPS,
                load_ground_truth,
                run_benchmark,
                summarize_benchmark,
            )
            from gaia.eval.runner import REPO_ROOT, RESULTS_DIR

            fixtures = REPO_ROOT / "tests" / "fixtures" / "email"
            mbox_path = args.mbox_path or str(fixtures / "_stub_inbox.mbox")
            gt_path = args.ground_truth or str(fixtures / "ground_truth.json")
            ground_truth = (
                load_ground_truth(gt_path) if Path(gt_path).exists() else None
            )
            if ground_truth is None:
                print(
                    f"[WARN] ground truth not found at {gt_path}; "
                    "quality scoring skipped"
                )

            if args.ctx_size is not None and args.ctx_size <= 0:
                print(
                    f"[ERROR] --ctx-size must be a positive token count, got "
                    f"{args.ctx_size}. Use e.g. 16384 (the #1892 envelope "
                    "target) or omit the flag for the default."
                )
                sys.exit(1)

            # ignore_cleanup_errors: the benchmark's per-email SQLite state.db can
            # still hold a Windows file lock when the block exits (WinError 32 on
            # rmtree). `results` is already captured, and the temp dir is a
            # throwaway in the OS temp space, so a failed cleanup must not fail an
            # otherwise-successful eval.
            with tempfile.TemporaryDirectory(
                prefix="gaia-bench-", ignore_cleanup_errors=True
            ) as tmp:
                results = run_benchmark(
                    args.model,
                    mbox_path=mbox_path,
                    limit=args.limit,
                    experiments=args.experiments,
                    base_url=args.backend,
                    ground_truth=ground_truth,
                    db_path=str(Path(tmp) / "state.db"),
                    ctx_size=args.ctx_size,
                )

            run_id = f"bench-{args.model.replace('/', '-').lower()}"
            summary = summarize_benchmark(results, run_id=run_id)
            perf = summary["scorecard"]["performance"]
            tps = perf.get("avg_tokens_per_second")
            ttft = perf.get("avg_time_to_first_token")
            bar_ok = isinstance(tps, (int, float)) and tps >= THROUGHPUT_BAR_TPS

            print(f"\n{'=' * 60}")
            print(f"  Email-Triage Throughput Benchmark — {args.model}")
            print(f"{'=' * 60}")
            print(
                f"  Experiments:  {args.experiments}  " f"(limit {args.limit} emails)"
            )
            print(
                f"  Throughput:   {tps} tok/s  "
                f"(bar ≥{THROUGHPUT_BAR_TPS}, stretch {THROUGHPUT_STRETCH_TPS})"
            )
            print(f"  TTFT:         {ttft} s")
            print(
                f"  Ctx window:   "
                f"{summary['scorecard'].get('ctx_size', 'unpinned (legacy)')}"
            )
            if results and isinstance(results[0].get("quality"), dict):
                print(f"  Category acc: {results[0]['quality']['category_accuracy']}")
            print(
                f"  Committed bar (≥{THROUGHPUT_BAR_TPS} tok/s): "
                f"{'PASS' if bar_ok else 'MISS'} (non-gating demo)"
            )
            print(f"{'=' * 60}\n")

            # Variance report across repeated experiments.
            if args.experiments > 1:
                from gaia.eval.statistics import (
                    compare_runs_by_model,
                    print_comparison_report,
                )

                for report in compare_runs_by_model(results).values():
                    print_comparison_report(report)

            if args.output_dir:
                from gaia.eval.scorecard import write_summary_md

                outdir = Path(args.output_dir)
                outdir.mkdir(parents=True, exist_ok=True)
                (outdir / "scorecard.json").write_text(
                    json.dumps(summary["scorecard"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                (outdir / "summary.md").write_text(
                    write_summary_md(summary["scorecard"]), encoding="utf-8"
                )
                (outdir / "variance.json").write_text(
                    json.dumps(summary["variance"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                wrote_quality = ""
                if isinstance(summary.get("quality"), dict):
                    # Aggregate acceptance metrics + run-to-run variance/CI (#1437,
                    # #1894). The scorecard adapter reads this (never the harness),
                    # preserving the harness→file→adapter loose coupling.
                    (outdir / "quality.json").write_text(
                        json.dumps(summary["quality"], indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    wrote_quality = " / quality.json"
                print(
                    f"[OUT] wrote scorecard.json / summary.md / variance.json"
                    f"{wrote_quality} → {outdir}"
                )

            if getattr(args, "save_baseline", False):
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                baseline_path = RESULTS_DIR / "bench_baseline.json"
                baseline_path.write_text(
                    json.dumps(summary["scorecard"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                print(f"[BASELINE] saved throughput baseline → {baseline_path}")

            if args.compare:
                cmp_path = Path(args.compare)
                if not cmp_path.exists():
                    print(f"[ERROR] baseline not found: {cmp_path}")
                    sys.exit(1)
                base = json.loads(cmp_path.read_text(encoding="utf-8"))
                # Ctx envelope guard (#1892): a baseline measured at a
                # different window is not comparable — hard error.
                _compare_benchmark_ctx(
                    current_ctx=summary["scorecard"].get("ctx_size"),
                    baseline=base,
                    baseline_path=str(cmp_path),
                )
                base_tps = base.get("performance", {}).get("avg_tokens_per_second")
                if isinstance(base_tps, (int, float)) and isinstance(tps, (int, float)):
                    print(
                        f"[COMPARE] throughput {tps} vs baseline {base_tps} "
                        f"tok/s (Δ{round(tps - base_tps, 1):+})  [non-gating]"
                    )
                else:
                    print("[COMPARE] insufficient throughput data to compare")
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

    if args.action == "lemonade":
        handle_lemonade_command(args)
        return

    if args.action == "daemon":
        handle_daemon_command(args)
        return

    if args.action == "hub":
        handle_hub_command(args)
        return

    # Handle Config command
    if args.action == "config":
        handle_config_command(args)
        return

    # Handle Cache command
    if args.action == "cache":
        handle_cache_command(args)
        return

    # Handle Knowledge command (Tavily web research)
    if args.action == "knowledge":
        handle_knowledge_command(args)
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

    # Handle Skills command (issue #888)
    if args.action == "skill":
        from gaia.skills import cli as skills_cli  # pylint: disable=reimported

        rc = skills_cli.handle(args)
        sys.exit(rc)

    # Handle Diagnostics command
    if args.action == "diagnostics":
        handle_diagnostics_command(args)
        return

    # Handle Agent (export/import) command
    if args.action == "agent":
        handle_agent_command(args)
        return

    if args.action == "email":
        handle_email_command(args)
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

        if args.check:
            from gaia.installer.init_command import check_setup_status

            try:
                status = check_setup_status(
                    profile=profile,
                    skip_chat_model=getattr(args, "skip_chat_model", False),
                    remote=getattr(args, "remote", False),
                )
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            if status.ready:
                print(f"READY: profile '{profile}' is already set up")
                sys.exit(0)
            print(f"NOT READY: profile '{profile}' needs setup")
            for reason in status.reasons:
                print(f"  - {reason}")
            sys.exit(1)

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
            skip_webui_build=getattr(args, "skip_webui_build", False),
            skip_chat_model=getattr(args, "skip_chat_model", False),
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
    """Kill the GAIA/Lemonade process listening on ``port``.

    Targeting rules live in :mod:`gaia.ports` so every "stop what's on this
    port" path in GAIA shares one implementation.
    """
    try:
        port = int(port)
    except (ValueError, TypeError):
        return {"success": False, "message": f"Invalid port number: {port!r}"}

    try:
        listeners = listeners_on_port(port)
    except FileNotFoundError as e:
        # Not "nothing is listening" — we could not look. Say which tool is missing.
        return {
            "success": False,
            "message": (
                f"Cannot inspect port {port}: {e.filename or 'the port-listing tool'} "
                f"is not on PATH. Install lsof or net-tools, or stop the process "
                f"by PID."
            ),
        }
    except (OSError, subprocess.SubprocessError) as e:
        return {"success": False, "message": f"Could not inspect port {port}: {e}"}

    if not listeners:
        return {"success": False, "message": f"No process is listening on port {port}"}

    killed = []
    refused = []
    failed = []
    for pid, name in listeners:
        if not is_killable_process(name):
            refused.append(f"{pid} ({name or 'unknown process'})")
            continue
        try:
            terminate_pid(pid)
            killed.append(str(pid))
        except (subprocess.CalledProcessError, OSError) as e:
            failed.append(f"{pid}: {e}")

    if killed:
        return {
            "success": True,
            "message": f"Killed process(es) {', '.join(killed)} listening on port {port}",
        }

    if refused:
        return {
            "success": False,
            "message": (
                f"Refusing to kill {', '.join(refused)} on port {port}: not a "
                f"GAIA or Lemonade process. Stop it with its own tooling, or "
                f"kill it by PID if that is really what you want."
            ),
        }

    return {
        "success": False,
        "message": f"Failed to kill the process on port {port} ({'; '.join(failed)})",
    }


def handle_email_command(args):
    """
    Handle the ``gaia email`` command — a thin client over the GAIA daemon (V2-8).

    The query path (``-q`` / ``-i``) no longer runs the email agent in-process:
    it ensures/attaches to the always-on daemon, ensures the ``email`` sidecar,
    and streams ``POST /v1/email/query`` through the daemon relay, presenting ONLY
    the daemon client token — the CLI never learns the sidecar's port or bearer
    (design §0.0 "one contract, many front-doors", #2152). Local-LLM only (AC3):
    no ``--use-claude`` / ``--use-chatgpt`` — the sidecar rejects any non-local
    provider.

    ``--spec`` stays a fixed-function local operation (no daemon, no Lemonade).

    Args:
        args: Parsed command line arguments for the email command
    """
    log = get_logger(__name__)

    # --spec: generate the HTML endpoint spec and open it in a browser.
    # No LLM, no Lemonade, no daemon — short-circuit before any server check.
    if getattr(args, "spec", False):
        try:
            from gaia_agent_email.spec_html import write_and_open_spec
        except ImportError as e:
            raise RuntimeError(
                agent_not_installed_message(
                    "The email agent is not installed",
                    "gaia-agent-email",
                    next_step="Then re-run `gaia email --spec`.",
                )
            ) from e

        dest = write_and_open_spec(getattr(args, "output", None))
        print(dest)
        sys.exit(0)

    # autonomy: a REST thin client over the daemon relay (#2516) — status/
    # trust/set-level/pause/resume/kill need no LLM at all, and `run` does its
    # own Lemonade check inside the sidecar call, so short-circuit here too.
    if getattr(args, "email_action", None) == "autonomy":
        handle_email_autonomy_command(args)
        return

    # Initialize Lemonade — local LLM only. The daemon spawns the sidecar, but the
    # sidecar's inference runs on Lemonade; this upfront check gives a friendlier
    # "start Lemonade first" message than a mid-stream error event (AC: Lemonade
    # down → loud actionable error).
    if not getattr(args, "no_lemonade_check", False):
        success, _ = initialize_lemonade_for_agent(
            agent="email",
            skip_if_external=True,
            # Deliberately omitted: use_claude / use_chatgpt — see AC3.
            base_url=getattr(args, "base_url", None),
        )
        if not success:
            sys.exit(1)

    verbose = bool(getattr(args, "verbose", False) or getattr(args, "debug", False))
    query = getattr(args, "query", None)
    interactive = getattr(args, "interactive", False)

    if not query and not interactive:
        # No query and not interactive — print a helpful usage hint (parity with
        # the retired in-process CLI).
        print(
            "Usage: gaia email -q '<your question>' OR gaia email -i\n"
            "Examples:\n"
            "  gaia email -q 'Triage my inbox'\n"
            "  gaia email -q 'Summarize my unread emails from this week'\n"
            "  gaia email -i\n"
        )
        sys.exit(0)

    from gaia.daemon.agent_query import ConsoleRenderer, run_query
    from gaia.daemon.errors import DaemonError

    model = getattr(args, "model", None)

    try:
        if interactive:
            sys.exit(_email_interactive(model=model, verbose=verbose))
        renderer = ConsoleRenderer(verbose=verbose)
        outcome = run_query(
            "email", query, model=model, renderer=renderer, verbose=verbose
        )
        sys.exit(outcome.exit_code)
    except DaemonError as e:
        # Daemon unreachable / relay refusal / sidecar dead → loud, actionable,
        # no silent in-process fallback (CLAUDE.md fail-loudly rule).
        log.error("email thin client failed: %s", e)
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)


def _email_interactive(*, model, verbose: bool) -> int:
    """REPL over the daemon relay — reads queries from stdin until EOF / ``/quit``.

    Maintains the transcript locally and pushes it as ``context`` on each turn
    (the host owns the transcript; the stateless sidecar is fed the relevant
    slice per request, spec §2.4).
    """
    from gaia.daemon.agent_query import ConsoleRenderer, run_query

    print("Email Triage Agent — interactive. Enter a query, or '/quit' to exit.\n")
    transcript: list = []
    while True:
        try:
            query = input("email> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not query:
            continue
        if query in ("/quit", "/exit", "/q"):
            return 0
        renderer = ConsoleRenderer(verbose=verbose)
        outcome = run_query(
            "email",
            query,
            context=list(transcript),
            model=model,
            renderer=renderer,
            verbose=verbose,
        )
        # Only extend the transcript on a successful turn — a failed run must not
        # poison the context of the next one.
        if outcome.terminal_type == "final":
            transcript.append({"role": "user", "content": query})
            transcript.append(
                {"role": "assistant", "content": outcome.final_answer or ""}
            )


def _print_autonomy_status(body: dict) -> None:
    # Whitelisted fields only, coerced to known-safe scalars — never format a
    # relay response verbatim, so it can't echo back anything unexpected.
    # The scope-count key is read indirectly (not `.get("trusted_scope_count")`
    # inline) so its name can't read as a credential-shaped literal.
    scope_count_key = "trusted_scope_count"
    level = str(body.get("level"))
    enabled = bool(body.get("enabled"))
    min_samples = int(body.get("trust_min_samples") or 0)
    threshold = float(body.get("trust_threshold") or 0.0)
    scope_count = int(body.get(scope_count_key) or 0)
    print(f"level: {level}  enabled: {enabled}")
    print(
        f"trust: min_samples={min_samples} "
        f"threshold={threshold} "
        f"trusted_scopes={scope_count}"
    )


def _print_autonomy_trust(body: dict) -> None:
    scopes = body.get("scopes") or []
    if not scopes:
        print("No trust ledger entries yet.")
        return
    # Same whitelist contract as _print_autonomy_status.
    for s in scopes:
        action_type = str(s.get("action_type"))
        scope = str(s.get("scope"))
        positive = int(s.get("positive") or 0)
        total = int(s.get("total") or 0)
        mark = "trusted" if s.get("trusted") else "not trusted"
        print(f"{action_type} / {scope}: {positive}/{total} ({mark})")


def _print_autonomy_run(body: dict) -> None:
    # Same whitelist contract as _print_autonomy_status.
    executed = len(body.get("executed") or [])
    proposals = len(body.get("proposals") or [])
    skipped = int(body.get("skipped") or 0)
    already_proposed = int(body.get("already_proposed") or 0)
    errors = len(body.get("errors") or [])
    stopped = body.get("stopped")
    print(
        f"executed={executed} proposals={proposals} "
        f"skipped={skipped} already_proposed={already_proposed} errors={errors}"
    )
    if stopped:
        print(f"stopped early: {stopped}")


def handle_email_autonomy_command(args) -> None:
    """``gaia email autonomy ...`` — thin client over the sidecar's
    session-scoped ``/v1/email/agent/autonomy*`` REST surface (#2516).

    Every subcommand shares one persistent ``--session-id`` (default
    ``"cli"``) so a level set by one invocation is still in effect for the
    next — autonomy state lives on the session's in-memory agent, not on
    disk. A session is created (idempotently) before each call. Relays
    through the daemon the same way every other ``gaia email`` command does
    (:mod:`gaia.daemon.agent_control`) — no second auth scheme.
    """
    from gaia.daemon.agent_control import relay_json
    from gaia.daemon.errors import DaemonError

    log = get_logger(__name__)
    action = getattr(args, "autonomy_action", None)
    if not action:
        print(
            "Usage: gaia email autonomy {status|set-level|pause|resume|run|trust|kill}\n"
            "Run `gaia email autonomy --help` for details on each."
        )
        sys.exit(0)

    session_id = getattr(args, "session_id", "cli")

    try:
        relay_json(
            "email", "POST", "agent/session", json_body={"session_id": session_id}
        )

        if action == "status":
            _print_autonomy_status(
                relay_json("email", "GET", f"agent/autonomy/{session_id}")
            )
        elif action == "trust":
            _print_autonomy_trust(
                relay_json("email", "GET", f"agent/autonomy/{session_id}")
            )
        elif action == "set-level":
            body = relay_json(
                "email",
                "POST",
                "agent/autonomy",
                json_body={"session_id": session_id, "level": args.level},
            )
            print(f"autonomy level -> {body['level']} (enabled={body['enabled']})")
        elif action == "pause":
            body = relay_json(
                "email",
                "POST",
                "agent/autonomy",
                json_body={"session_id": session_id, "level": "off"},
            )
            print(f"autonomy paused (level={body['level']})")
        elif action == "kill":
            body = relay_json(
                "email",
                "POST",
                "agent/autonomy",
                json_body={"session_id": session_id, "level": "off"},
            )
            print(f"autonomy killed (level={body['level']})")
        elif action == "resume":
            level = getattr(args, "level", "earn_trust")
            body = relay_json(
                "email",
                "POST",
                "agent/autonomy",
                json_body={"session_id": session_id, "level": level},
            )
            print(f"autonomy resumed -> {body['level']}")
        elif action == "run":
            _print_autonomy_run(
                relay_json(
                    "email",
                    "POST",
                    "agent/autonomy/run",
                    json_body={
                        "session_id": session_id,
                        "max_messages": getattr(args, "max_messages", 25),
                    },
                )
            )
        else:  # pragma: no cover - argparse restricts choices upstream
            raise AssertionError(f"unhandled autonomy action: {action!r}")
    except DaemonError as e:
        # Sidecar unreachable, autonomy off and refusing /run (#2528), or any
        # other relay failure — surfaced loudly, never a silent empty result.
        # #2617: this does print the same text twice (log.error to stdout,
        # then the stderr print below) -- kept deliberately. log.error is the
        # durable record `gaia diagnostics` bundles from ~/.gaia/gaia.log;
        # dropping it would make a reported failure invisible to a bug
        # report. The stderr print matches the ❌ convention every other
        # `except DaemonError` handler in this file uses. The duplicate line
        # is cosmetic; a missing log record is not.
        log.error("email autonomy %s failed: %s", action, e)
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


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
            from gaia.api.sse_handler import warn_if_unconfirmed_tools_allowed

            print("🚀 Starting GAIA OpenAI-compatible API server...")
            print(f"   Host: {args.host}")
            print(f"   Port: {args.port}")
            warn_if_unconfirmed_tools_allowed()

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
        # /health check that verifies the responder is actually the GAIA API
        # server — a bare socket probe can't tell it from any process on the port.
        from gaia.api.app import check_status

        check_status(args.host, args.port)

    elif args.subcommand == "stop":
        print(f"🛑 Stopping API server on port {args.port}...")
        try:
            result = kill_process_by_port(args.port)
            if result.get("success"):
                print("✅ API server stopped")
            else:
                print(f"❌ {result.get('message', 'API server was not running')}")
                sys.exit(1)
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


def _print_knowledge_usage(client):
    """Print a one-line credit-usage summary for a Tavily client."""
    usage = client.usage()
    cap = usage["cap"]
    cap_str = "unlimited" if cap is None else str(cap)
    print(f"\n💳 Credits used: {usage['total_credits']} (cap: {cap_str})")


def handle_knowledge_command(args):
    """Handle `gaia knowledge` — Tavily web research with caching + budget.

    Args:
        args: Parsed command-line arguments
    """
    action = getattr(args, "knowledge_action", None)
    if action is None:
        print("❌ Error: No knowledge action specified")
        print("Available actions: search, extract, usage")
        print("Run 'gaia knowledge --help' for more information")
        return

    from gaia.web.tavily import (
        BudgetConfig,
        TavilyBudgetExceeded,
        TavilyClient,
        TavilyConfigError,
    )

    budget = BudgetConfig(
        cap=getattr(args, "budget", None),
        block=not getattr(args, "no_block", False),
    )
    client = TavilyClient(budget=budget)
    try:
        if action == "search":
            result = client.search(
                args.query, search_depth=args.depth, max_results=args.max_results
            )
            source = result.get("source", "tavily")
            print(f"\n=== Results for {args.query!r} (source: {source}) ===")
            for i, r in enumerate(result.get("results", []), 1):
                print(f"{i}. {r.get('title', '')}")
                print(f"   {r.get('url', '')}")
                content = r.get("content") or r.get("snippet") or ""
                if content:
                    print(f"   {content[:200]}")
            _print_knowledge_usage(client)
        elif action == "extract":
            result = client.extract(args.urls, extract_depth=args.depth)
            print(json.dumps(result, indent=2))
            _print_knowledge_usage(client)
        elif action == "usage":
            _print_knowledge_usage(client)
    except TavilyBudgetExceeded as e:
        print(f"🛑 {e}")
        sys.exit(1)
    except TavilyConfigError as e:
        print(f"❌ {e}")
        sys.exit(1)
    finally:
        client.close()


def handle_config_command(args):
    """Handle `gaia config show|get|set` (persistent ~/.gaia/config.json)."""
    from gaia.config import GaiaConfig, GaiaConfigError

    path = getattr(args, "config", None)
    config_file = GaiaConfig.config_path(path)

    action = getattr(args, "config_action", None)
    if not action:
        print(
            "No config action specified. Use: gaia config show|get|set",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        cfg = GaiaConfig.load(path)
    except GaiaConfigError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    if action == "show":
        exists = config_file.exists()
        print(f"Config file: {config_file}")
        print(
            "  (file exists)"
            if exists
            else "  (file not created yet — showing built-in defaults)"
        )
        for key in cfg.field_names():
            value = cfg.get(key)
            print(f"  {key} = {value if value is not None else '(unset)'}")
        return

    if action == "get":
        try:
            value = cfg.get(args.key)
        except GaiaConfigError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        print(value if value is not None else "")
        return

    if action == "set":
        try:
            cfg.set(args.key, args.value)
        except GaiaConfigError as e:
            print(f"❌ {e}", file=sys.stderr)
            sys.exit(1)
        cfg.save(path)
        print(f"✅ Set {args.key} = {args.value}")
        print(f"   Saved to {config_file}")
        return


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
                # Same refusal-must-not-report-success rule as `gaia kill`.
                print("❌ Specify --context7 or --all to clear caches")
                print("Run 'gaia cache clear --help' for more information")
                sys.exit(1)

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

        # Procedures section (procedural memory / skills, #887). Counts come
        # from get_stats() COUNT(*) queries — synthesis is the only writer, so
        # every row is "synthesized"; recall uses the enabled, non-superseded
        # subset.
        proc = stats["procedures"]
        print(f"\n  Procedures (skills): {proc['total']} synthesized")
        if proc["total"]:
            if proc["active"] != proc["total"]:
                print(f"    Active (recallable): {proc['active']}")
            if proc["last_recalled"]:
                print(f"    Last recalled: {proc['last_recalled'][:10]}")

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
            # Default: run both phases — but a cancelled conversation means the
            # user wants out, not a second round of prompts from discovery.
            if _bootstrap_chat().cancelled:
                return
            print()
            _bootstrap_discover()
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)


# ---- Bootstrap: conversational onboarding ----


#: Marks that the user has been through the onboarding conversation, whatever
#: they chose to store. Keyed in ~/.gaia/memory_settings.json so first-boot does
#: not have to infer completion from the presence of a memory row.
_ONBOARDING_COMPLETED_KEY = "onboarding_completed_at"


def _onboarding_completed() -> bool:
    """True once the user has finished the onboarding conversation at least once."""
    from gaia.agents.base.memory import _load_memory_settings

    return bool(_load_memory_settings().get(_ONBOARDING_COMPLETED_KEY))


def _mark_onboarding_completed() -> None:
    """Record that onboarding ran to the end, so first-boot stops offering it."""
    from datetime import datetime

    from gaia.agents.base.memory import _save_memory_settings

    _save_memory_settings(
        {_ONBOARDING_COMPLETED_KEY: datetime.now().astimezone().isoformat()}
    )


def _offer_first_boot_onboarding() -> None:
    """Offer the onboarding intro on first `gaia chat`, at most once.

    Gated on a stored profile entry OR the completion marker. Both signals are
    needed: the review gate means a user can answer every question and approve
    nothing, and being re-asked forever after declining is worse than not asking.
    """
    from gaia.agents.base.memory_store import MemoryStore

    store = MemoryStore()
    try:
        has_profile = bool(store.get_by_category("profile", context="global", limit=1))
    finally:
        store.close()

    if has_profile or _onboarding_completed():
        return

    print("\n" + "=" * 60)
    print("  First time with GAIA? Let's set up your profile.")
    print("=" * 60)
    try:
        answer = input("  Quick intro? Takes ~3 minutes. [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer in ("n", "no"):
        # Declining is an answer — record it so the intro is not re-offered.
        _mark_onboarding_completed()
        return

    # Onboarding is optional; chat is not. A failure to store must not cost
    # the user their chat session.
    try:
        _bootstrap_chat()
    except RuntimeError as e:
        print(f"⚠ Onboarding could not finish: {e}")
        print("  Continuing to chat — run `gaia memory bootstrap` later.")
    print()


def _bootstrap_prompt(question: str) -> str:
    """Ask *question* on stdio for the bootstrap conversation core.

    Translates EOF / Ctrl-C into ``BootstrapCancelled`` so the core can stop
    asking, keep what the user already approved, and report the cancellation.
    """
    from gaia.agents.base.bootstrap import BootstrapCancelled

    try:
        return input(f"  {question} ")
    except (EOFError, KeyboardInterrupt) as e:
        raise BootstrapCancelled("user cancelled onboarding") from e


def _bootstrap_chat():
    """Phase 1: Conversational onboarding — adaptive questions, reviewed answers.

    Drives the bootstrap core against a bare MemoryStore with no ``on_stored``
    hook, so onboarding needs no embedding backend — the stored rows are
    embedded at the next agent start.

    Returns the BootstrapResult so callers can honour a cancellation.
    """
    from gaia.agents.base.bootstrap import run_bootstrap_conversation
    from gaia.agents.base.memory_store import MemoryStore

    print("\n=== Welcome to GAIA — Your Personal AI Assistant ===")
    print("I run locally on your AMD hardware, so everything stays private.")
    print("Let me get to know you so I can be more helpful from the start.")
    print(
        "(Press Enter to skip any question. Nothing is stored without your approval.)\n"
    )

    try:
        store = MemoryStore()
    except Exception as e:
        raise RuntimeError(f"Error opening memory database: {e}") from e

    try:
        result = run_bootstrap_conversation(
            store,
            prompt_fn=_bootstrap_prompt,
            output_fn=print,
            on_stored=None,
        )
    finally:
        store.close()

    if result.cancelled:
        print("\n\nBootstrap cancelled.")
        if result.stored:
            print(f"Kept the {result.stored} entries you already approved.")
        return result

    _mark_onboarding_completed()

    summary = (
        f"\n✅ Stored {result.stored} memory entries from onboarding "
        f"({result.rejected} rejected, {result.skipped} skipped"
    )
    if result.unreviewed:
        summary += f", {result.unreviewed} not reviewed"
    print(summary + ").")
    return result


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


def _collect_signal(source_key: str, scanner):
    """Run `scanner` unless this platform has no branch for `source_key`.

    Applies the same platform gate as ``scan_all``, which a direct scanner call
    bypasses — otherwise an unsupported source is indistinguishable from a user
    who genuinely has nothing to find.

    Args:
        source_key: A discovery source name as used by ``scan_all``.
        scanner: Zero-argument callable returning the scanner's fact dicts.

    Returns:
        The scanner's fact dicts, or [] after printing why there are none.
    """
    from gaia.agents.base.discovery import unsupported_reason

    reason = unsupported_reason(source_key)
    if reason:
        print(f"\n  Skipped: {reason}")
        return []
    try:
        return scanner()
    except Exception as e:
        print(f"\n  '{source_key}' scan failed, continuing without it: {e}")
        return []


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
    except Exception as e:
        # Non-critical — inference still runs, but say why the staleness check
        # did not, or a broken store looks like "no facts yet".
        print(f"  Could not check for existing inferred facts: {e}")

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
        browser_results = _collect_signal(
            "browser_history", lambda: discovery.scan_browser_history(days=30)
        )
        if browser_results:
            # content is "Frequently visited: domain.com (N visits)"
            lines = [f"  {item['content']}" for item in browser_results[:40]]
            sections.append(
                "BROWSER HISTORY (top domains, last 30 days):\n" + "\n".join(lines)
            )

    # 2. Installed applications
    app_results = _collect_signal("installed_apps", discovery.scan_installed_apps)
    apps = [
        item["content"][len("Installed app: ") :].strip()
        for item in app_results
        if item.get("content", "").startswith("Installed app: ")
    ]
    if apps:
        sections.append("INSTALLED APPS:\n  " + ", ".join(apps))

    # 3. Git identity (name / employer domain — not raw email)
    git_results = _collect_signal("git_identity", discovery.scan_git_identity)
    git_lines = [
        f"  {item['content']}"
        for item in git_results
        if not item.get("sensitive") and item.get("content")
    ]
    if git_lines:
        sections.append("GIT IDENTITY:\n" + "\n".join(git_lines))

    # 4. Project languages / manifests (non-sensitive)
    manifest_results = _collect_signal(
        "project_manifests", discovery.scan_project_manifests
    )
    manifest_lines = [
        f"  {item['content']}"
        for item in manifest_results[:10]
        if not item.get("sensitive") and item.get("content")
    ]
    if manifest_lines:
        sections.append("PROJECT MANIFESTS (sample):\n" + "\n".join(manifest_lines))

    # 5. App launch frequency — covers consumer apps like Spotify and Outlook
    usage_results = _collect_signal(
        "windows_userassist", discovery.scan_windows_userassist
    ) + _collect_signal("macos_app_usage", discovery.scan_macos_app_usage)
    if usage_results:
        lines = [f"  {item['content']}" for item in usage_results[:20]]
        sections.append(
            "FREQUENTLY LAUNCHED APPS (actual usage frequency):\n" + "\n".join(lines)
        )

    # 6. Recent file type patterns
    filetype_results = _collect_signal(
        "recent_file_types", discovery.scan_recent_file_types
    )
    if filetype_results:
        lines = [f"  {item['content']}" for item in filetype_results]
        sections.append("RECENT FILE TYPES (work patterns):\n" + "\n".join(lines))

    # 7. Gaming and media
    gaming_results = _collect_signal(
        "gaming_and_media", discovery.scan_gaming_and_media
    )
    if gaming_results:
        lines = [f"  {item['content']}" for item in gaming_results]
        sections.append("GAMING AND MEDIA:\n" + "\n".join(lines))

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
        print(
            f"  Make sure Lemonade Server is running. {describe_start_hint().instruction}"
        )
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
    # Developer-workflow actions (init / version / test) handled in cli_agent.
    from gaia import cli_agent

    if cli_agent.handle(args):
        return

    if not hasattr(args, "agent_action") or args.agent_action is None:
        print("❌ Error: No agent action specified")
        print(
            "Available actions: init, version, test, configure, health, status, "
            "export, import, install, list"
        )
        print("Run 'gaia agent --help' for more information")
        sys.exit(1)

    if args.agent_action == "export":
        handle_agent_export(args)
    elif args.agent_action == "import":
        handle_agent_import(args)
    elif args.agent_action == "install":
        handle_agent_install(args)
    elif args.agent_action == "list":
        handle_agent_list(args)
    else:
        print(f"❌ Unknown agent action: {args.agent_action}")
        sys.exit(1)


def handle_agent_list(_args):
    """List installed agents and agents available from the Agent Hub.

    Read-only discovery for ``gaia agent install`` — degrades to an
    installed-only listing (with a loud note) if the hub catalog is unreachable,
    never a silent empty result.
    """
    from gaia.hub import catalog, installer

    installed = installer.list_installed()

    available = []
    offline = False
    try:
        result = catalog.load_index()
        available = result.agents
        offline = result.offline
    except Exception as exc:  # noqa: BLE001 - CLI boundary: degrade loudly
        print(f"⚠ Could not reach the Agent Hub catalog: {exc}", file=sys.stderr)

    available_ids = {e.get("id") for e in available}
    if available:
        suffix = " (offline cache)" if offline else ""
        print(f"Available from the Agent Hub{suffix}:")
        for entry in sorted(available, key=lambda a: a.get("id", "")):
            aid = entry.get("id", "?")
            ver = entry.get("latest_version", "?")
            mark = "  [installed]" if aid in installed else ""
            print(f"  {aid:<24} {ver}{mark}")

    extra = [aid for aid in sorted(installed) if aid not in available_ids]
    if extra:
        print("\nInstalled (not in the hub catalog):")
        for aid in extra:
            print(f"  {aid:<24} {installed[aid].version}")

    if not available and not installed:
        print("No agents installed, and the Agent Hub catalog is unavailable.")
    else:
        print("\nInstall one with: gaia agent install <id>")


def handle_agent_install(args):
    """Install a published agent from the Agent Hub into ~/.gaia/agents/.

    Thin CLI wrapper over :func:`gaia.hub.installer.install` — the headless
    equivalent of the Agent UI's Install button, so a machine with no UI can
    provision a sidecar agent (e.g. ``email``) instead of resorting to a raw
    ``python -c`` one-liner.
    """
    from gaia.hub import installer

    agent_id = args.agent_id
    version = getattr(args, "version", None)
    trusted = getattr(args, "trusted", False)

    label = agent_id + (f"@{version}" if version else "")
    print(f"Installing '{label}' from the Agent Hub...")
    try:
        result = installer.install(agent_id, version=version, trusted=trusted)
    except installer.TrustRequiredError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        print(
            "   Re-run with --trust if you trust the publisher.",
            file=sys.stderr,
        )
        sys.exit(1)
    except installer.InstallError as exc:
        # Actionable install-lifecycle failures (checksum, disk, compat, …).
        print(f"❌ Could not install '{agent_id}': {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: surface loudly, exit 1
        print(f"❌ Could not install '{agent_id}': {exc}", file=sys.stderr)
        print(
            "   If the id is wrong, run `gaia agent list` to see available agents.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"✅ Installed '{result.id}' v{result.version} to {result.path}")
    # Sidecar agents run under the daemon — point the user at the next step.
    from gaia.daemon.sidecars.spec import builtin_specs

    if result.id in builtin_specs():
        print(f"   Start it with: gaia daemon start-agent {result.id}")


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


def _check_daemon_deps():
    """Fail loud if the daemon's runtime deps (extras-only) are missing.

    ``gaia daemon`` is a base console command but the daemon process needs
    ``fastapi``/``uvicorn``/``psutil``, which live in the ``[api]`` and ``[ui]``
    extras. On a base install the spawned daemon would die on
    ``ModuleNotFoundError`` and surface a cryptic "process exited early"; name
    the real cause and the fix instead.
    """
    import importlib.util

    missing = [
        pkg
        for mod, pkg in (
            ("fastapi", "fastapi"),
            ("uvicorn", "uvicorn"),
            ("psutil", "psutil"),
        )
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        print(
            "❌ `gaia daemon` needs packages not in the base install: "
            + ", ".join(missing)
            + '.\n   Install the daemon extras:  pip install "amd-gaia[api]"'
            "  (or [ui], which adds the Agent UI's RAG stack on top)."
        )
        sys.exit(1)


def handle_daemon_command(args):
    """Handle `gaia daemon status|stop|restart|logs|start|agents|start-agent|stop-agent`."""
    action = getattr(args, "daemon_action", None)
    if action is None:
        print(
            "❌ No daemon action specified. Use 'gaia daemon --help' to see "
            "available actions (start, status, stop, restart, logs, agents, "
            "start-agent, stop-agent)."
        )
        return
    _check_daemon_deps()
    if action == "start":
        _handle_daemon_start()
    elif action == "status":
        _handle_daemon_status()
    elif action == "stop":
        _handle_daemon_stop()
    elif action == "restart":
        _handle_daemon_restart()
    elif action == "logs":
        _handle_daemon_logs(args)
    elif action == "agents":
        _handle_daemon_agents()
    elif action == "start-agent":
        _handle_daemon_start_agent(args)
    elif action == "stop-agent":
        _handle_daemon_stop_agent(args)
    else:
        print(f"❌ Unknown daemon action: {action}")


def _fmt_uptime(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _handle_daemon_start():
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError

    try:
        inst = client.start_or_attach()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)
    print(f"✅ GAIA daemon running (pid {inst.pid}, port {inst.port})")


def _handle_daemon_status():
    from gaia.daemon import client, paths

    report = client.status_report()
    state = report["state"]
    if state == "not_running":
        print("GAIA daemon: not running")
        print("  Start it with `gaia daemon start`.")
        return
    inst = report["instance"]
    if state == "stale":
        reason = {
            "pid_dead": "recorded pid is not alive",
            "unresponsive": "process is alive but not answering the client API",
        }.get(report.get("reason"), report.get("reason"))
        print(f"GAIA daemon: stale ({reason})")
        print(f"  Registry: {paths.instance_path()} (pid {inst.pid}, port {inst.port})")
        print("  Reclaim it with `gaia daemon restart`.")
        return
    body = report["status"]
    print("GAIA daemon: running")
    print(f"  pid:        {inst.pid}")
    print(f"  port:       {inst.port}")
    print(f"  uptime:     {_fmt_uptime(body.get('uptime_seconds', 0))}")
    print(f"  api:        v{body.get('api_version')}")
    print(f"  url:        {inst.base_url}")
    print("Sidecar agents:")
    _print_daemon_agents(inst, indent="  ")


def _daemon_http_detail(response) -> str:
    """The actionable `detail` from a daemon error response (or the raw status)."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return f"HTTP {response.status_code}"


def _fetch_daemon_agents(inst) -> list:
    """GET the daemon's supervised-agent list. Raises SystemExit on failure."""
    import requests

    try:
        r = requests.get(
            f"{inst.base_url}/daemon/v1/agents",
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ could not reach the daemon at {inst.base_url}: {e}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"❌ the daemon refused the agents request: {_daemon_http_detail(r)}")
        sys.exit(1)
    return r.json().get("agents", [])


def _print_daemon_agents(inst, indent: str = "") -> None:
    # NEVER prints tokens — the list route does not return them and this
    # renderer only touches the fields below.
    for entry in _fetch_daemon_agents(inst):
        agent_id = entry.get("agent_id")
        state = entry.get("state")
        if state == "running":
            line = (
                f"{indent}{agent_id}: running  mode: {entry.get('mode')}"
                f"  pid: {entry.get('pid')}  port: {entry.get('port')}"
                f"  api: v{entry.get('api_version')}"
            )
            if entry.get("mode") == "dev" and entry.get("dev_src_dir"):
                line += f"  src: {entry.get('dev_src_dir')}"
            print(line)
        else:
            print(f"{indent}{agent_id}: stopped")


def _handle_daemon_agents():
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError

    try:
        inst = client.attach()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)
    if inst is None:
        print("GAIA daemon: not running")
        print(
            "  Start it with `gaia daemon start` (or `gaia daemon start-agent <id>`)."
        )
        sys.exit(1)
    _print_daemon_agents(inst)


def _handle_daemon_start_agent(args):
    import requests

    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError
    from gaia.daemon.sidecars.errors import DevSrcDirResolutionError
    from gaia.daemon.sidecars.spec import (
        repo_root_from_agent_dev_src_dir,
        resolve_caller_dev_src_dir,
        resolve_caller_mode,
    )

    # Resolve THIS process's own intent before ever contacting the daemon —
    # the daemon is a long-lived singleton with its own environment and
    # checkout, neither of which reflect the caller's (issue #2588).
    resolved_mode = resolve_caller_mode(args.agent_id, override=args.mode)
    dev_src_dir = None
    if resolved_mode == "dev":
        try:
            dev_src_dir = resolve_caller_dev_src_dir(
                args.agent_id, explicit=args.dev_src_dir
            )
        except DevSrcDirResolutionError as e:
            print(f"❌ {e}")
            sys.exit(1)

    try:
        inst = client.start_or_attach()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)

    body_payload = {"mode": resolved_mode}
    if dev_src_dir is not None:
        body_payload["dev_src_dir"] = str(dev_src_dir)

    try:
        # Read generously: a first-run ensure may lazily fetch the binary.
        r = requests.post(
            f"{inst.base_url}/daemon/v1/agents/{args.agent_id}/ensure",
            headers={"Authorization": f"Bearer {inst.token}"},
            json=body_payload,
            timeout=(5.0, 900.0),
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ could not reach the daemon at {inst.base_url}: {e}")
        sys.exit(1)
    if r.status_code != 200:
        print(
            f"❌ the daemon could not start agent '{args.agent_id}': "
            f"{_daemon_http_detail(r)}"
        )
        sys.exit(1)
    # The ensure body carries the sidecar bearer token — print ONLY these fields.
    body = r.json()

    if dev_src_dir is not None:
        # A daemon that predates #2588 silently ignores dev_src_dir and just
        # reports whatever IT resolved — never trust a match we can't verify.
        reported = body.get("dev_src_dir")
        if reported != str(dev_src_dir):
            # The remedy must name the REPO ROOT — restarting a Python
            # environment/editable install rooted at the agent SOURCE dir
            # (dev_src_dir itself) does nothing to the daemon's own anchor.
            try:
                remedy_root = repo_root_from_agent_dev_src_dir(
                    dev_src_dir, args.agent_id
                )
                remedy = (
                    "Restart the daemon from a Python environment/editable "
                    f"install rooted at {remedy_root}, or upgrade it."
                )
            except DevSrcDirResolutionError:
                # Only reachable via an explicit --dev-src-dir that doesn't
                # follow the hub/agents/<id>/python layout — no repo root
                # exists to name, so say so rather than naming the agent
                # subdir as something to "root a Python environment at".
                remedy = (
                    "Restart the daemon from the Python environment/editable "
                    f"install that serves '{dev_src_dir}' as this agent's "
                    "dev source, or upgrade it."
                )
            print(
                f"❌ the daemon reported dev source '{reported}' but expected "
                f"'{dev_src_dir}' — the running daemon predates this fix and "
                f"silently ignored the dev-mode source check. {remedy}"
            )
            sys.exit(1)

    print(
        f"✅ agent '{args.agent_id}' sidecar running "
        f"(mode: {body.get('mode')}, pid: {body.get('pid')}, "
        f"port: {body.get('port')}, api: v{body.get('api_version')})"
    )
    if body.get("mode") == "dev" and body.get("dev_src_dir"):
        print(f"   source: {body.get('dev_src_dir')}")


def _handle_daemon_stop_agent(args):
    import requests

    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError

    try:
        inst = client.attach()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)
    if inst is None:
        # Attach-only by design: never start a daemon just to stop nothing.
        print("GAIA daemon: not running (no sidecars to stop)")
        sys.exit(1)
    try:
        r = requests.post(
            f"{inst.base_url}/daemon/v1/agents/{args.agent_id}/stop",
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ could not reach the daemon at {inst.base_url}: {e}")
        sys.exit(1)
    if r.status_code != 200:
        print(
            f"❌ the daemon failed to stop agent '{args.agent_id}': "
            f"{_daemon_http_detail(r)}"
        )
        sys.exit(1)
    print(f"✅ agent '{args.agent_id}' sidecar stopped (daemon still running)")


def _handle_daemon_stop():
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError
    from gaia.daemon.instance import (
        pid_alive,
        read_instance,
        remove_instance,
        terminate_instance,
    )

    inst = read_instance()
    if inst is None:
        print("GAIA daemon: not running (nothing to stop)")
        return
    if not pid_alive(inst.pid):
        remove_instance(only_pid=inst.pid)
        print(f"GAIA daemon: cleared stale registry (pid {inst.pid} was already dead)")
        return
    # Prefer a graceful, authed shutdown; escalate to terminate if it won't answer.
    try:
        client.request_shutdown(inst)
    except DaemonError as e:
        print(f"⚠️  graceful shutdown failed ({e}); terminating pid {inst.pid}")
        terminate_instance(inst)
    if client.wait_until_gone(inst, timeout=10.0):
        remove_instance(only_pid=inst.pid)
        print(f"✅ GAIA daemon stopped (pid {inst.pid})")
    else:
        print(f"⚠️  daemon pid {inst.pid} did not exit; terminating")
        terminate_instance(inst)
        remove_instance(only_pid=inst.pid)
        print(f"✅ GAIA daemon terminated (pid {inst.pid})")


def _handle_daemon_restart():
    _handle_daemon_stop()
    _handle_daemon_start()


def _resolve_sidecar_log(agent_id: str):
    """Return the Path to the given sidecar's current (or most recent) log.

    Prefers the log of the running sidecar (its live port, learned from the
    daemon) so ``-f`` follows the active process; falls back to the newest
    ``sidecar-*.log`` on disk when the daemon is down or the agent is stopped
    (so ``logs`` still works post-mortem). Returns ``None`` if no log exists.
    """
    log_dir = Path.home() / ".gaia" / "agents" / agent_id / "logs"

    # Best-effort: ask the daemon for the running port so we follow the live
    # process. Never fatal — a down daemon just means we use the newest file.
    try:
        from gaia.daemon import client

        inst = client.attach()
        if inst is not None:
            for entry in _fetch_daemon_agents(inst):
                if entry.get("agent_id") == agent_id and entry.get("port"):
                    candidate = log_dir / f"sidecar-{entry['port']}.log"
                    if candidate.exists():
                        return candidate
    except Exception as exc:
        # A down daemon is expected here; record why before the disk fallback.
        logging.getLogger("gaia.cli").debug(
            "daemon lookup for %s failed (%s); using newest log on disk",
            agent_id,
            exc,
        )

    if not log_dir.is_dir():
        return None
    logs = sorted(log_dir.glob("sidecar-*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def _tail_file(log_file, lines: int, follow: bool) -> None:
    """Print the last ``lines`` of ``log_file``, optionally following it."""
    if follow:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f.readlines()[-lines:]:
                print(line, end="")
            try:
                while True:
                    chunk = f.readline()
                    if chunk:
                        print(chunk, end="")
                    else:
                        time.sleep(0.3)
            except KeyboardInterrupt:
                return
        return
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f.readlines()[-lines:]:
            print(line, end="")


def _handle_daemon_logs(args):
    from gaia.daemon import paths

    lines = getattr(args, "lines", 100)
    follow = getattr(args, "follow", False)
    agent_id = getattr(args, "agent", None)

    if agent_id:
        log_file = _resolve_sidecar_log(agent_id)
        if log_file is None:
            print(
                f"No log for sidecar '{agent_id}' at "
                f"{Path.home() / '.gaia' / 'agents' / agent_id / 'logs'} "
                f"(it has not run yet). Start it with `gaia daemon start-agent "
                f"{agent_id}` or run the agent once."
            )
            return
        print(f"# {log_file}")
        _tail_file(log_file, lines, follow)
        return

    log_file = paths.log_path()
    if not log_file.exists():
        print(f"No daemon log at {log_file} (the daemon has not started yet).")
        return
    _tail_file(log_file, lines, follow)


# ---------------------------------------------------------------------------
# Agent Hub (`gaia hub list|install|uninstall`)
#
# Thin client over the daemon's /daemon/v1/catalog + install routes, so the
# CLI, the TUI and the Agent UI share one installer, one integrity check and
# one install lock. NEVER prints tokens: none of these routes return one, and
# these renderers only touch the fields named below.
# ---------------------------------------------------------------------------

# Install can download tens of MB and unpack it; poll patiently, but bounded.
_HUB_INSTALL_TIMEOUT = 900.0


def handle_hub_command(args):
    """Handle `gaia hub list|install|uninstall`."""
    action = getattr(args, "hub_action", None)
    if action is None:
        print(
            "❌ No hub action specified. Use 'gaia hub --help' to see available "
            "actions (list, install, uninstall)."
        )
        sys.exit(1)
    _check_daemon_deps()
    if action == "list":
        _handle_hub_list(args)
    elif action == "install":
        _handle_hub_install(args)
    elif action == "uninstall":
        _handle_hub_uninstall(args)
    else:
        print(f"❌ Unknown hub action: {action}")
        sys.exit(1)


def _hub_daemon():
    """Start-or-attach the daemon; exit(1) with its own message on failure."""
    from gaia.daemon import client
    from gaia.daemon.errors import DaemonError

    try:
        return client.start_or_attach()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)


def _hub_request(inst, method: str, path: str, *, timeout, json_body=None):
    """Call one daemon hub route. Exits(1) on transport failure."""
    import requests

    try:
        return requests.request(
            method,
            f"{inst.base_url}{path}",
            headers={"Authorization": f"Bearer {inst.token}"},
            json=json_body,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        print(f"❌ could not reach the daemon at {inst.base_url}: {e}")
        sys.exit(1)


def _fmt_size(num_bytes) -> str:
    try:
        size = float(num_bytes or 0)
    except (TypeError, ValueError):
        return "-"
    if size <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return "-"


def _handle_hub_list(args):
    inst = _hub_daemon()
    # --installed is a purely local question: ask for it that way so it also
    # answers with no network (the daemon reads the .installed sentinels).
    query = []
    if getattr(args, "installed", False):
        query.append("installed_only=true")
    if getattr(args, "refresh", False):
        query.append("refresh=true")
    path = "/daemon/v1/catalog" + (f"?{'&'.join(query)}" if query else "")
    r = _hub_request(inst, "GET", path, timeout=(5.0, 30.0))
    if r.status_code != 200:
        print(f"❌ the daemon could not load the catalog: {_daemon_http_detail(r)}")
        sys.exit(1)
    body = r.json()
    agents = body.get("agents", [])
    if body.get("offline"):
        print("⚠️  hub unreachable — showing the cached catalog")
    if not agents:
        print(
            "No agents installed."
            if getattr(args, "installed", False)
            else "The Agent Hub catalog is empty."
        )
        return
    print(f"{'AGENT':<14} {'LATEST':<10} {'INSTALLED':<12} {'SIZE':<8} NAME")
    for a in agents:
        if a.get("update_available"):
            state = f"{a.get('installed_version')} ↑"
        elif a.get("installed"):
            state = str(a.get("installed_version") or "yes")
        else:
            state = "-"
        print(
            f"{str(a.get('id')):<14} {str(a.get('latest_version') or '-'):<10} "
            f"{state:<12} {_fmt_size(a.get('download_size_bytes')):<8} "
            f"{a.get('name') or ''}"
        )
    hidden = body.get("unsupervised_filtered") or []
    if hidden:
        print(
            f"\n{len(hidden)} hub agent(s) hidden — this GAIA build cannot run "
            f"them yet: {', '.join(hidden)}"
        )


def _handle_hub_install(args):
    inst = _hub_daemon()
    agent_id = args.agent_id
    r = _hub_request(
        inst,
        "POST",
        f"/daemon/v1/agents/{agent_id}/install",
        timeout=(5.0, 60.0),
        json_body={
            "version": getattr(args, "version", None),
            "trusted": bool(getattr(args, "trusted", False)),
        },
    )
    if r.status_code == 403:
        # Non-verified agent, no opt-in. Name the flag; never retry with
        # trusted=true on the user's behalf.
        print(f"❌ {_daemon_http_detail(r)}", file=sys.stderr)
        print(
            f"   Re-run with --trust if you trust the publisher: "
            f"gaia hub install {agent_id} --trust",
            file=sys.stderr,
        )
        sys.exit(1)
    if r.status_code != 202:
        print(
            f"❌ the daemon refused to install '{agent_id}': {_daemon_http_detail(r)}"
        )
        sys.exit(1)
    print(f"⏳ installing '{agent_id}'...")

    last_phase = None
    deadline = time.monotonic() + _HUB_INSTALL_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(1.0)
        s = _hub_request(
            inst,
            "GET",
            f"/daemon/v1/agents/{agent_id}/install-status",
            timeout=(5.0, 15.0),
        )
        if s.status_code == 404:
            # The record was queued moments ago: it can only vanish if the
            # daemon restarted or something else mutated this agent.
            print(
                f"❌ the install record for '{agent_id}' disappeared mid-install. "
                f"The daemon may have restarted, or the agent was uninstalled "
                f"concurrently. Check `gaia hub list` and `gaia daemon logs`."
            )
            sys.exit(1)
        if s.status_code != 200:
            print(f"❌ could not read install status: {_daemon_http_detail(s)}")
            sys.exit(1)
        state = s.json()
        phase = state.get("phase")
        if phase != last_phase:
            print(f"   {phase} ({state.get('percent', 0)}%)")
            last_phase = phase
        if state.get("status") == "completed":
            print(
                f"✅ '{agent_id}' {state.get('version')} installed. "
                f"Start it with `gaia daemon start-agent {agent_id}`."
            )
            return
        if state.get("status") == "failed":
            print(f"❌ install of '{agent_id}' failed: {state.get('error')}")
            sys.exit(1)
    print(
        f"❌ install of '{agent_id}' did not finish within "
        f"{int(_HUB_INSTALL_TIMEOUT)}s. It may still be running — check "
        f"`gaia hub list` and `gaia daemon logs`."
    )
    sys.exit(1)


def _handle_hub_uninstall(args):
    inst = _hub_daemon()
    agent_id = args.agent_id
    r = _hub_request(
        inst, "DELETE", f"/daemon/v1/agents/{agent_id}", timeout=(5.0, 120.0)
    )
    if r.status_code != 200:
        print(f"❌ could not uninstall '{agent_id}': {_daemon_http_detail(r)}")
        sys.exit(1)
    print(f"✅ '{agent_id}' uninstalled (sidecar stopped, install directory removed)")


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
    elif args.mcp_action == "serve":
        handle_mcp_serve(args)
    elif args.mcp_action == "tui":
        handle_mcp_tui(args)
    elif args.mcp_action == "list":
        handle_mcp_list(args)
    elif args.mcp_action == "tools":
        handle_mcp_tools(args)
    elif args.mcp_action == "test-client":
        handle_mcp_test_client(args)
    else:
        log.error(f"Unknown MCP action: {args.mcp_action}")
        print(f"❌ Unknown MCP action: {args.mcp_action}")


def handle_lemonade_command(args):
    """Handle ``gaia lemonade ...``.

    Args:
        args: Parsed arguments for the lemonade command.
    """
    if getattr(args, "lemonade_action", None) != "embedded":
        print(
            "❌ No Lemonade action specified. Use 'gaia lemonade --help' to see "
            "available actions."
        )
        sys.exit(1)
    handle_lemonade_embedded_command(args)


def handle_lemonade_embedded_command(args):
    """Handle ``gaia lemonade embedded {start,stop,status,install,install-backend}``.

    Args:
        args: Parsed arguments for the embedded subcommand.
    """
    from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError

    action = getattr(args, "embedded_action", None)
    if action is None:
        print(
            "❌ No embedded action specified. Use 'gaia lemonade embedded --help' "
            "to see available actions."
        )
        sys.exit(1)

    manager = EmbeddedLemonade()
    try:
        if action == "start":
            status = manager.start(
                port=getattr(args, "port", None),
                timeout=getattr(args, "timeout", 60.0),
                install_if_missing=not getattr(args, "no_install", False),
            )
            print(f"✅ Embedded Lemonade {status.version} running on {status.base_url}")
            print(f"   pid {status.pid}   logs: {manager.log_path}")
            print("")
            print("   The instance is private. Load its URL and API key with:")
            print(f"   {manager.env_load_command()}")
        elif action == "stop":
            if manager.stop():
                print("✅ Embedded Lemonade stopped")
            else:
                print("Embedded Lemonade is not running")
        elif action == "status":
            _print_embedded_status(manager)
        elif action == "install":
            path = manager.install(force=getattr(args, "force", False))
            print(f"✅ Embedded Lemonade {manager.version} installed at {path}")
        elif action == "install-backend":
            print(f"Installing backend {args.spec} (this can take several minutes)...")
            manager.install_backend(args.spec)
            print(f"✅ Backend {args.spec} installed into {manager.cache_dir / 'bin'}")
        else:
            print(f"❌ Unknown embedded action: {action}")
    except EmbeddedLemonadeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except OSError as e:
        # Spawning the daemon or opening its log can fail on a noexec mount or
        # a read-only home; say so instead of dumping a traceback.
        print(
            f"❌ Could not run embedded Lemonade from {manager.dist_dir}: {e}. "
            f"Check that the directory is writable and the daemon is "
            f"executable, then retry."
        )
        sys.exit(1)


def _print_embedded_status(manager):
    """Print a human-readable snapshot of the embedded instance.

    Args:
        manager: An ``EmbeddedLemonade`` to query.
    """
    status = manager.status()
    print(f"Version:   {status.version}")
    print(f"Installed: {'yes' if status.installed else 'no'} ({manager.dist_dir})")
    if status.running:
        print(f"Running:   yes on {status.base_url} (pid {status.pid})")
    elif status.unresponsive_pid:
        print(
            f"Running:   process {status.unresponsive_pid} is up on port "
            f"{status.port} but not answering"
        )
        print(
            f"           check {manager.log_path}, then `gaia lemonade "
            f"embedded stop`"
        )
    else:
        print("Running:   no")
        if status.installed:
            print("           start it with `gaia lemonade embedded start`")
        else:
            print("           install it with `gaia lemonade embedded install`")
    backends_dir = manager.cache_dir / "bin"
    if backends_dir.is_dir():
        installed = sorted(p.name for p in backends_dir.iterdir())
        print(f"Backends:  {', '.join(installed) if installed else 'none'}")
    else:
        print("Backends:  none")


# Kept in sync with gaia.mcp.mcp_bridge.AUTH_TOKEN_ENV_VAR by
# tests/unit/test_mcp_bridge_auth.py — duplicated here so the client-side
# `mcp status` / `mcp test` paths don't have to import the heavy bridge module.
MCP_AUTH_TOKEN_ENV = "GAIA_MCP_AUTH_TOKEN"


def _mcp_auth_headers(args):
    """Bearer headers for reaching a token-protected MCP bridge, else {}."""
    token = getattr(args, "auth_token", None) or os.environ.get(MCP_AUTH_TOKEN_ENV)
    return {"Authorization": f"Bearer {token}"} if token else {}


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
            if args.no_streaming:
                cmd_args.append("--no-streaming")
            if getattr(args, "verbose", False):
                cmd_args.append("--verbose")
            if getattr(args, "no_lemonade_check", False):
                cmd_args.append("--no-lemonade-check")

            # Hand the token over the environment, not argv — argv is world
            # readable via `ps` on Linux/macOS.
            child_env = os.environ.copy()
            bg_token = args.auth_token or os.environ.get(MCP_AUTH_TOKEN_ENV) or None
            if bg_token:
                child_env[MCP_AUTH_TOKEN_ENV] = bg_token

            print("🚀 Starting GAIA MCP Bridge in background")
            print(f"📍 Host: {args.host}:{args.port}")
            print(f"📄 Log file: {log_file_path}")
            if bg_token:
                print("🔒 Authentication enabled (Bearer token required)")
            else:
                print("🔓 Authentication disabled - pass --auth-token to require one")

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
                        env=child_env,
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
                        env=child_env,
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

        auth_token = args.auth_token or os.environ.get(MCP_AUTH_TOKEN_ENV) or None
        if auth_token:
            print("🔒 Authentication enabled (Bearer token required; /health public)")
        else:
            print("🔓 Authentication disabled - pass --auth-token to require one")

        print(f"🔗 GAIA LLM server: {args.base_url}")
        print(f"📡 Streaming: {'disabled' if args.no_streaming else 'enabled'}")
        if getattr(args, "verbose", False):
            print("🔍 Verbose logging: enabled")
        print("")
        print("Press Ctrl+C to stop the server")

        # Start HTTP-native MCP bridge
        verbose = getattr(args, "verbose", False)
        start_mcp_http(
            host=args.host,
            port=args.port,
            base_url=args.base_url,
            verbose=verbose,
            auth_token=auth_token,
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
                auth_headers = _mcp_auth_headers(args)
                try:
                    status_req = urllib.request.Request(
                        status_url, headers=auth_headers
                    )
                    with urllib.request.urlopen(status_req, timeout=3) as response:
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
                    if e.code in (401, 403):
                        print("🔒 MCP server requires authentication")
                        print(
                            "   Pass --auth-token <token> or set "
                            f"{MCP_AUTH_TOKEN_ENV} to inspect it"
                        )
                        return
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
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **_mcp_auth_headers(args),
                },
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
            if e.code in (401, 403):
                print(f"🔒 MCP server rejected the request ({e.code})")
                print(
                    "   The bridge was started with --auth-token. Pass the same "
                    f"token via --auth-token, or set {MCP_AUTH_TOKEN_ENV}."
                )
            else:
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
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **_mcp_auth_headers(args),
                },
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
            if e.code in (401, 403):
                print(f"🔒 MCP server rejected the request ({e.code})")
                print(
                    "   The bridge was started with --auth-token. Pass the same "
                    f"token via --auth-token, or set {MCP_AUTH_TOKEN_ENV}."
                )
            else:
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


def handle_mcp_serve(args):
    """Start the Agent UI MCP server (wraps the GAIA Agent UI REST API)."""
    log = get_logger(__name__)

    try:
        from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

        mcp = create_agent_ui_mcp(backend_url=args.backend)

        if args.stdio:
            # stdout carries JSON-RPC framing in stdio mode; keep logs off it.
            from gaia.logger import route_console_logging_to_stderr

            route_console_logging_to_stderr()
            print(
                "Starting GAIA Agent UI MCP Server (stdio mode)...",
                file=__import__("sys").stderr,
            )
            mcp.run(transport="stdio")
        else:
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
            except AttributeError:
                log.debug("MCPServer tool registry layout changed; skipping tool count")
            print("\nPress Ctrl+C to stop")
            print("=" * 60)
            mcp.run(transport="streamable-http", host=args.host, port=args.port)

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


def handle_mcp_tui(args):
    """Start the TUI control MCP server (drives a running `gaia tui --control`)."""
    log = get_logger(__name__)

    try:
        from gaia.mcp.servers.tui_mcp import create_tui_mcp, route_logging_to_stderr

        mcp = create_tui_mcp()

        if args.stdio:
            # stdout carries JSON-RPC only; GAIA's root log handler writes there.
            route_logging_to_stderr()
            print(
                "Starting GAIA TUI Control MCP Server (stdio mode)...",
                file=__import__("sys").stderr,
            )
            mcp.run(transport="stdio")
        else:
            print("=" * 60)
            print("🖥️  GAIA TUI Control MCP Server")
            print("=" * 60)
            print("   Target  : the running `gaia tui --control` session")
            print(f"   MCP     : http://{args.host}:{args.port}/mcp")
            try:
                tool_count = len(
                    mcp._tool_manager._tools
                )  # pylint: disable=protected-access
                print(f"   Tools   : {tool_count} registered")
            except AttributeError:
                log.debug("MCPServer tool registry layout changed; skipping tool count")
            print("\nPress Ctrl+C to stop")
            print("=" * 60)
            mcp.run(transport="streamable-http", host=args.host, port=args.port)

    except KeyboardInterrupt:
        print("\n✅ TUI control MCP server stopped")
    except ImportError as e:
        log.error(f"Failed to import TUI control MCP server: {e}")
        print("❌ Error: Could not load the TUI control MCP server")
        print(f"   {e}")
        print('   Install the MCP extras: uv pip install -e ".[mcp]"')
    except Exception as e:
        log.error(f"Error starting TUI control MCP server: {e}")
        print(f"❌ Error starting TUI control MCP server: {e}")


def handle_mcp_list(args):
    """List configured MCP servers."""
    from gaia.mcp import MCPConfig

    config = MCPConfig(args.config) if args.config else MCPConfig()
    servers = config.get_servers()

    config_path = args.config if args.config else "~/.gaia/mcp_servers.json"

    if not servers:
        print("📋 No MCP servers configured")
        print(f"   Config: {config_path}")
        print(f"\nAdd a server by editing {config_path}, for example:")
        print(
            '   {"mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}}'
        )
        print("See https://amd-gaia.ai/docs/guides/mcp/client for details.")
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
