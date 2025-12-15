#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Lemonade Server Client for GAIA.

This module provides a client for interacting with the Lemonade server's
OpenAI-compatible API and additional functionality.
"""

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Event, Thread
from typing import Any, Callable, Dict, Generator, List, Optional, Union

import openai  # For exception types
import psutil
import requests
from dotenv import load_dotenv

# Import OpenAI client for internal use
from openai import OpenAI

from gaia.logger import get_logger

# Load environment variables from .env file
load_dotenv()

# =========================================================================
# Server Configuration Defaults
# =========================================================================
# Default server host and port (can be overridden via LEMONADE_BASE_URL env var)
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000
# API version supported by this client
LEMONADE_API_VERSION = "v1"
# Default URL includes /api/v1 to match documentation and other clients
DEFAULT_LEMONADE_URL = (
    f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/api/{LEMONADE_API_VERSION}"
)


def _get_lemonade_config() -> tuple:
    """
    Get Lemonade host, port, and base_url from environment or defaults.

    Parses LEMONADE_BASE_URL env var if set, otherwise uses defaults.
    The base_url is expected to include /api/v1 suffix per documentation.

    Returns:
        Tuple of (host, port, base_url)
    """
    from urllib.parse import urlparse

    base_url = os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)
    # Parse the URL to extract host and port for backwards compatibility
    parsed = urlparse(base_url)
    host = parsed.hostname or DEFAULT_HOST
    port = (
        80
        if (parsed.port is None and host is not None)
        else (parsed.port or DEFAULT_PORT)
    )
    return (host, port, base_url)


# =========================================================================
# Model Configuration Defaults
# =========================================================================
# Default model for text generation - lightweight CPU model for testing
DEFAULT_MODEL_NAME = "Qwen2.5-0.5B-Instruct-CPU"
# DEFAULT_MODEL_NAME = "Llama-3.2-3B-Instruct-Hybrid"

# =========================================================================
# Request Configuration Defaults
# =========================================================================
# Default timeout in seconds for regular API requests
# Increased to accommodate long-running coding and evaluation tasks
DEFAULT_REQUEST_TIMEOUT = 900
# Default timeout in seconds for model loading operations
# Increased for large model downloads and loading
DEFAULT_MODEL_LOAD_TIMEOUT = 1200


# =========================================================================
# Model Types and Agent Profiles
# =========================================================================


class ModelType(Enum):
    """Types of models supported by Lemonade"""

    LLM = "llm"  # Large Language Model for chat/reasoning
    EMBEDDING = "embed"  # Embedding model for RAG
    VLM = "vlm"  # Vision-Language Model for image understanding
    ASR = "asr"  # Automatic Speech Recognition
    TTS = "tts"  # Text-to-Speech


@dataclass
class ModelRequirement:
    """Defines a model requirement for an agent"""

    model_type: ModelType
    model_id: str
    display_name: str
    required: bool = True
    min_ctx_size: int = 4096  # Minimum context size needed


@dataclass
class AgentProfile:
    """Defines the requirements for an agent"""

    name: str
    display_name: str
    models: list = field(default_factory=list)
    min_ctx_size: int = 4096
    description: str = ""


@dataclass
class LemonadeStatus:
    """Status of Lemonade Server"""

    running: bool = False
    url: str = field(
        default_factory=lambda: os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)
    )
    context_size: int = 0
    loaded_models: list = field(default_factory=list)
    health_data: dict = field(default_factory=dict)
    error: Optional[str] = None


# Define available models
MODELS = {
    # LLM Models
    "qwen3-coder-30b": ModelRequirement(
        model_type=ModelType.LLM,
        model_id="Qwen3-Coder-30B-A3B-Instruct-GGUF",
        display_name="Qwen3 Coder 30B",
        min_ctx_size=32768,
    ),
    "qwen2.5-0.5b": ModelRequirement(
        model_type=ModelType.LLM,
        model_id="Qwen2.5-0.5B-Instruct-CPU",
        display_name="Qwen2.5 0.5B (Fast)",
        min_ctx_size=4096,
    ),
    # Embedding Models
    "nomic-embed": ModelRequirement(
        model_type=ModelType.EMBEDDING,
        model_id="nomic-embed-text-v2-moe-GGUF",
        display_name="Nomic Embed Text v2",
        min_ctx_size=2048,
    ),
    # VLM Models
    "qwen2.5-vl-7b": ModelRequirement(
        model_type=ModelType.VLM,
        model_id="Qwen2.5-VL-7B-Instruct-GGUF",
        display_name="Qwen2.5 VL 7B",
        min_ctx_size=8192,
    ),
}

# Define agent profiles with their model requirements
AGENT_PROFILES = {
    "chat": AgentProfile(
        name="chat",
        display_name="Chat Agent",
        models=["qwen3-coder-30b", "nomic-embed", "qwen2.5-vl-7b"],
        min_ctx_size=32768,
        description="Interactive chat with RAG and vision support",
    ),
    "code": AgentProfile(
        name="code",
        display_name="Code Agent",
        models=["qwen3-coder-30b"],
        min_ctx_size=32768,
        description="Autonomous coding assistant",
    ),
    "talk": AgentProfile(
        name="talk",
        display_name="Talk Agent",
        models=["qwen3-coder-30b"],
        min_ctx_size=32768,
        description="Voice-enabled chat",
    ),
    "rag": AgentProfile(
        name="rag",
        display_name="RAG System",
        models=["qwen3-coder-30b", "nomic-embed", "qwen2.5-vl-7b"],
        min_ctx_size=32768,
        description="Document Q&A with retrieval and vision",
    ),
    "blender": AgentProfile(
        name="blender",
        display_name="Blender Agent",
        models=["qwen3-coder-30b"],
        min_ctx_size=32768,
        description="3D content generation in Blender",
    ),
    "jira": AgentProfile(
        name="jira",
        display_name="Jira Agent",
        models=["qwen3-coder-30b"],
        min_ctx_size=32768,
        description="Jira issue management",
    ),
    "docker": AgentProfile(
        name="docker",
        display_name="Docker Agent",
        models=["qwen3-coder-30b"],
        min_ctx_size=32768,
        description="Docker container management",
    ),
    "vlm": AgentProfile(
        name="vlm",
        display_name="Vision Agent",
        models=["qwen2.5-vl-7b"],
        min_ctx_size=8192,
        description="Image understanding and analysis",
    ),
    "minimal": AgentProfile(
        name="minimal",
        display_name="Minimal (Fast)",
        models=["qwen2.5-0.5b"],
        min_ctx_size=4096,
        description="Fast responses with smaller model",
    ),
    "mcp": AgentProfile(
        name="mcp",
        display_name="MCP Bridge",
        models=["qwen3-coder-30b", "nomic-embed", "qwen2.5-vl-7b"],
        min_ctx_size=32768,
        description="Model Context Protocol bridge server with vision",
    ),
}


class LemonadeClientError(Exception):
    """Base exception for Lemonade client errors."""


class ModelDownloadCancelledError(LemonadeClientError):
    """Raised when a model download is cancelled by user."""


class InsufficientDiskSpaceError(LemonadeClientError):
    """Raised when there's not enough disk space for model download."""


@dataclass
class DownloadTask:
    """Represents an ongoing model download."""

    model_name: str
    size_gb: float = 0.0
    start_time: float = field(default_factory=time.time)
    cancel_event: Event = field(default_factory=Event)
    progress_percent: float = 0.0

    def cancel(self):
        """Cancel this download."""
        self.cancel_event.set()

    def is_cancelled(self) -> bool:
        """Check if download was cancelled."""
        return self.cancel_event.is_set()

    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time


def _supports_unicode() -> bool:
    """
    Check if the terminal supports Unicode output.

    Returns:
        True if UTF-8 encoding is supported, False otherwise
    """
    try:
        # Check stdout encoding
        encoding = sys.stdout.encoding
        if encoding and "utf" in encoding.lower():
            return True
        # Try encoding a test emoji
        "✓".encode(encoding or "utf-8")
        return True
    except (UnicodeEncodeError, AttributeError, LookupError):
        return False


# Cache unicode support check
_UNICODE_SUPPORTED = _supports_unicode()


def _emoji(unicode_char: str, ascii_fallback: str) -> str:
    """
    Return emoji if terminal supports unicode, otherwise ASCII fallback.

    Args:
        unicode_char: Unicode emoji character
        ascii_fallback: ASCII fallback string

    Returns:
        Unicode emoji or ASCII fallback

    Examples:
        _emoji("✅", "[OK]")    # Returns "✅" or "[OK]"
        _emoji("❌", "[X]")     # Returns "❌" or "[X]"
        _emoji("📥", "[DL]")    # Returns "📥" or "[DL]"
    """
    return unicode_char if _UNICODE_SUPPORTED else ascii_fallback


def kill_process_on_port(port):
    """Kill any process that is using the specified port."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            connections = proc.net_connections()
            for conn in connections:
                if conn.laddr.port == port:
                    proc_name = proc.name()
                    proc_pid = proc.pid
                    proc.kill()
                    print(
                        f"Killed process {proc_name} (PID: {proc_pid}) using port {port}"
                    )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def _prompt_user_for_download(
    model_name: str, size_gb: float, estimated_minutes: int
) -> bool:
    """
    Prompt user for confirmation before downloading a large model.

    Args:
        model_name: Name of the model to download
        size_gb: Size in gigabytes
        estimated_minutes: Estimated download time in minutes

    Returns:
        True if user confirms, False otherwise
    """
    # Check if we're in an interactive terminal
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        # Non-interactive environment - auto-approve
        return True

    print("\n" + "=" * 60)
    print(f"{_emoji('📥', '[DOWNLOAD]')} Model Download Required")
    print("=" * 60)
    print(f"Model: {model_name}")
    print(f"Size: {size_gb:.1f} GB")
    print(f"Estimated time: ~{estimated_minutes} minutes (@ 100Mbps)")
    print("=" * 60)

    while True:
        response = input("Download this model? [Y/n]: ").strip().lower()
        if response in ("", "y", "yes"):
            return True
        elif response in ("n", "no"):
            return False
        else:
            print("Please enter 'y' or 'n'")


def _check_disk_space(size_gb: float, path: Optional[str] = None) -> bool:
    """
    Check if there's enough disk space for download.

    Args:
        size_gb: Required space in GB
        path: Path to check. If None (default), checks current working directory.
              This is cross-platform compatible (works on Windows and Unix).

    Returns:
        True if enough space available

    Raises:
        InsufficientDiskSpaceError: If not enough space

    Note:
        The default checks the current working directory's drive/partition.
        Ideally, this should check the actual model storage location, but that
        requires server API support to report the storage path.
    """
    try:
        # Use current working directory if no path specified (cross-platform)
        check_path = path if path is not None else os.getcwd()
        stat = shutil.disk_usage(check_path)
        free_gb = stat.free / (1024**3)
        required_gb = size_gb * 1.5  # Need 50% buffer for extraction/temp files

        if free_gb < required_gb:
            raise InsufficientDiskSpaceError(
                f"Insufficient disk space: need {required_gb:.1f}GB, "
                f"have {free_gb:.1f}GB free"
            )
        return True
    except InsufficientDiskSpaceError:
        raise
    except Exception as e:
        # If we can't check disk space, log warning but continue
        logger = logging.getLogger(__name__)
        logger.warning(f"Could not check disk space: {e}")
        return True


class LemonadeClient:
    """Client for interacting with the Lemonade server REST API."""

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        verbose: bool = True,
        keep_alive: bool = False,
    ):
        """
        Initialize the Lemonade client.

        Args:
            model: Name of the model to load (optional)
            host: Host address of the Lemonade server (defaults to LEMONADE_BASE_URL env var)
            port: Port number of the Lemonade server (defaults to LEMONADE_BASE_URL env var)
            verbose: If False, reduce logging verbosity during initialization
            keep_alive: If True, don't terminate server in __del__
        """
        # Use provided host/port, or get from env var, or use defaults
        env_host, env_port, env_base_url = _get_lemonade_config()
        self.host = host if host is not None else env_host
        self.port = port if port is not None else env_port
        # If host/port explicitly provided, construct URL; otherwise use env URL directly
        if host is not None or port is not None:
            self.base_url = f"http://{self.host}:{self.port}/api/{LEMONADE_API_VERSION}"
        else:
            self.base_url = env_base_url
        self.model = model
        self.server_process = None
        self.log = get_logger(__name__)
        self.keep_alive = keep_alive

        # Track active downloads for cancellation support
        self.active_downloads: Dict[str, DownloadTask] = {}
        self._downloads_lock = threading.Lock()

        # Set logging level based on verbosity
        if not verbose:
            self.log.setLevel(logging.WARNING)

        self.log.info(f"Initialized Lemonade client for {host}:{port}")
        if model:
            self.log.info(f"Initial model set to: {model}")

    def launch_server(self, log_level="info", background="none", ctx_size=None):
        """
        Launch the Lemonade server using subprocess.

        Args:
            log_level: Logging level for the server
                       ('critical', 'error', 'warning', 'info', 'debug', 'trace').
                       Defaults to 'info'.
            background: How to run the server:
                       - "terminal": Launch in a new terminal window
                       - "silent": Run in background with output to log file
                       - "none": Run in foreground (default)
            ctx_size: Context size for the model (default: None, uses server default).
                     For chat/RAG applications, use 32768 or higher.

        This method follows the approach in test_lemonade_server.py.
        """
        self.log.info("Starting Lemonade server...")

        # Ensure we kill anything using the port
        kill_process_on_port(self.port)

        # Build the base command
        base_cmd = ["lemonade-server", "serve"]
        if log_level != "info":
            base_cmd.extend(["--log-level", log_level])
        if ctx_size is not None:
            base_cmd.extend(["--ctx-size", str(ctx_size)])
            self.log.info(f"Context size set to: {ctx_size}")

        if background == "terminal":
            # Launch in a new terminal window
            cmd = f'start cmd /k "{" ".join(base_cmd)}"'
            self.server_process = subprocess.Popen(cmd, shell=True)
        elif background == "silent":
            # Run in background with subprocess
            log_file = open("lemonade.log", "w", encoding="utf-8")
            self.server_process = subprocess.Popen(
                base_cmd,
                stdout=log_file,
                stderr=log_file,
                text=True,
                bufsize=1,
                shell=True,
            )
        else:  # "none" or any other value
            # Run in foreground with real-time output
            self.server_process = subprocess.Popen(
                base_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=True,
            )

            # Print stdout and stderr in real-time only for foreground mode
            def print_output():
                while True:
                    if self.server_process is None:
                        break
                    try:
                        stdout = self.server_process.stdout.readline()
                        stderr = self.server_process.stderr.readline()
                        if stdout:
                            self.log.debug(f"[Server stdout] {stdout.strip()}")
                        if stderr:
                            self.log.warning(f"[Server stderr] {stderr.strip()}")
                        if (
                            not stdout
                            and not stderr
                            and self.server_process is not None
                            and self.server_process.poll() is not None
                        ):
                            break
                    except AttributeError:
                        # This happens if server_process becomes None
                        # while we're executing this function
                        break

            output_thread = Thread(target=print_output, daemon=True)
            output_thread.start()

        # Wait for the server to start by checking port
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log.error("Server failed to start within 60 seconds")
                raise TimeoutError("Server failed to start within 60 seconds")
            try:
                conn = socket.create_connection((self.host, self.port))
                conn.close()
                break
            except socket.error:
                time.sleep(1)

        # Wait a few other seconds after the port is available
        time.sleep(5)
        self.log.info("Lemonade server started successfully")

    def terminate_server(self):
        """Terminate the Lemonade server process if it exists."""
        if not self.server_process:
            return

        try:
            self.log.info("Terminating Lemonade server...")

            # Handle different process types
            if hasattr(self.server_process, "join"):
                # Handle multiprocessing.Process objects
                self.server_process.terminate()
                self.server_process.join(timeout=5)
            else:
                # For subprocess.Popen
                if sys.platform.startswith("win") and self.server_process.pid:
                    # On Windows, use taskkill to ensure process tree is terminated
                    os.system(f"taskkill /F /PID {self.server_process.pid} /T")
                elif self.server_process.pid:
                    # On Linux/Unix, kill the process group to terminate child processes
                    try:
                        os.killpg(os.getpgid(self.server_process.pid), signal.SIGTERM)
                        # Wait a bit for graceful termination
                        try:
                            self.server_process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            # Force kill if graceful termination failed
                            os.killpg(
                                os.getpgid(self.server_process.pid), signal.SIGKILL
                            )
                    except (OSError, ProcessLookupError):
                        # Process or process group doesn't exist, try individual kill
                        try:
                            self.server_process.kill()
                        except ProcessLookupError:
                            pass  # Process already terminated
                else:
                    # Fallback: try to kill normally
                    self.server_process.kill()
                # Wait for process to terminate
                try:
                    self.server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log.warning("Process did not terminate within timeout")

            # Ensure port is free
            kill_process_on_port(self.port)

            # Reset reference
            self.server_process = None
            self.log.info("Lemonade server terminated successfully")
        except Exception as e:
            self.log.error(f"Error terminating server process: {e}")
            # Reset reference even on error
            self.server_process = None

    def __del__(self):
        """Cleanup server process on deletion."""
        # Check if keep_alive attribute exists (might not if __init__ failed early)
        if hasattr(self, "keep_alive") and not self.keep_alive:
            self.terminate_server()
        elif hasattr(self, "server_process") and self.server_process:
            if hasattr(self, "log"):
                self.log.info("Not terminating server because keep_alive=True")

    def get_model_info(self, model_name: str) -> Dict[str, Any]:
        """
        Get information about a model from the server.

        Args:
            model_name: Name of the model

        Returns:
            Dict with model info including size_gb estimate
        """
        try:
            models_response = self.list_models()
            for model in models_response.get("data", []):
                if model.get("id", "").lower() == model_name.lower():
                    # Estimate size based on model name if not provided
                    size_gb = model.get(
                        "size_gb", self._estimate_model_size(model_name)
                    )
                    return {
                        "id": model.get("id"),
                        "size_gb": size_gb,
                        "downloaded": model.get("downloaded", False),
                    }

            # Model not found in list, provide estimate
            return {
                "id": model_name,
                "size_gb": self._estimate_model_size(model_name),
                "downloaded": False,
            }
        except Exception:
            # If we can't get info, provide conservative estimate
            return {
                "id": model_name,
                "size_gb": self._estimate_model_size(model_name),
                "downloaded": False,
            }

    def _estimate_model_size(self, model_name: str) -> float:
        """
        Estimate model size in GB based on model name.

        Args:
            model_name: Name of the model

        Returns:
            Estimated size in GB
        """
        model_lower = model_name.lower()

        # Look for billion parameter indicators
        if "70b" in model_lower or "72b" in model_lower:
            return 40.0  # ~40GB for 70B models
        elif "30b" in model_lower or "34b" in model_lower:
            return 20.0  # ~20GB for 30B models
        elif "13b" in model_lower or "14b" in model_lower:
            return 8.0  # ~8GB for 13B models
        elif "7b" in model_lower:
            return 5.0  # ~5GB for 7B models
        elif "3b" in model_lower:
            return 2.0  # ~2GB for 3B models
        elif "1b" in model_lower or "0.5b" in model_lower:
            return 1.0  # ~1GB for small models
        elif "embed" in model_lower:
            return 0.5  # Embedding models are usually small
        else:
            return 10.0  # Conservative default

    def _estimate_download_time(self, size_gb: float, mbps: int = 100) -> int:
        """
        Estimate download time in minutes.

        Args:
            size_gb: Size in gigabytes
            mbps: Connection speed in megabits per second

        Returns:
            Estimated time in minutes
        """
        # Convert GB to megabits: 1 GB = 8000 megabits
        megabits = size_gb * 8000
        # Time in seconds
        seconds = megabits / mbps
        # Convert to minutes and round up
        return int(seconds / 60) + 1

    def cancel_download(self, model_name: str) -> bool:
        """
        Stop waiting for an ongoing model download.

        **IMPORTANT:** This only stops the client from waiting for the download.
        The server will continue downloading the model in the background.
        This limitation exists because the server's `/api/v1/pull` endpoint does not
        support cancellation.

        To truly cancel a download, you would need to:
        1. Stop the Lemonade server process, or
        2. Wait for server API to support download cancellation

        Args:
            model_name: Name of the model being downloaded

        Returns:
            True if waiting was stopped, False if download not found

        Example:
            # User initiates download
            client.load_model("large-model", auto_download=True)

            # In another thread, user wants to "cancel"
            client.cancel_download("large-model")
            # Client stops waiting, but server keeps downloading

        See Also:
            - get_active_downloads(): List downloads client is waiting for
            - Future: Server will support DELETE /api/v1/downloads/{id}
        """
        with self._downloads_lock:
            if model_name in self.active_downloads:
                task = self.active_downloads[model_name]
                task.cancel()
                self.log.warning(
                    f"Stopped waiting for {model_name} download. "
                    f"Note: Server continues downloading in background."
                )
                return True
        return False

    def get_active_downloads(self) -> List[DownloadTask]:
        """Get list of active download tasks."""
        with self._downloads_lock:
            return list(self.active_downloads.values())

    def _extract_error_info(self, error: Union[str, Dict, Exception]) -> Dict[str, Any]:
        """
        Extract structured error information from various error formats.

        Lemonade server returns errors in two formats:
        1. Structured: {"error": {"message": "...", "type": "not_found"}}
        2. Operation: {"status": "error", "message": "..."}

        Args:
            error: Error as string, dict, or exception

        Returns:
            Dict with normalized error info:
            - message: Error message text
            - type: Error type if available (e.g., "not_found")
            - code: Error code if available
            - is_structured: Whether error had type/code field

        Examples:
            # From exception
            info = self._extract_error_info(LemonadeClientError("Model not found"))
            # Returns: {"message": "Model not found", "type": None, ...}

            # From structured response
            response = {"error": {"message": "Not found", "type": "not_found"}}
            info = self._extract_error_info(response)
            # Returns: {"message": "Not found", "type": "not_found", ...}
        """
        result = {
            "message": "",
            "type": None,
            "code": None,
            "is_structured": False,
        }

        # Handle exception objects
        if isinstance(error, Exception):
            error = str(error)

        # Handle string errors
        if isinstance(error, str):
            result["message"] = error
            return result

        # Handle dict responses
        if isinstance(error, dict):
            # Format 1: {"error": {"message": "...", "type": "..."}}
            if "error" in error and isinstance(error["error"], dict):
                error_obj = error["error"]
                result["message"] = error_obj.get("message", "")
                result["type"] = error_obj.get("type")
                result["code"] = error_obj.get("code")
                result["is_structured"] = (
                    result["type"] is not None or result["code"] is not None
                )

            # Format 2: {"status": "error", "message": "..."}
            elif error.get("status") == "error":
                result["message"] = error.get("message", "")

            # Fallback: use the dict as string
            else:
                result["message"] = str(error)

        return result

    def _is_model_error(self, error: Union[str, Dict, Exception]) -> bool:
        """
        Check if an error is related to model not being loaded.

        Uses structured error types when available (e.g., type="not_found"),
        falls back to string matching for unstructured errors.

        Args:
            error: Error as string, dict, or exception

        Returns:
            True if this is a model loading error

        Examples:
            # Structured error (preferred)
            error = {"error": {"message": "...", "type": "not_found"}}
            is_model_error = self._is_model_error(error)  # Returns True

            # String error (fallback)
            is_model_error = self._is_model_error("model not loaded")  # Returns True
        """
        # Extract structured error info
        error_info = self._extract_error_info(error)

        # Check structured error type first (more reliable)
        error_type = error_info.get("type")
        if error_type:
            error_type_lower = error_type.lower()
            if error_type_lower in ["not_found", "model_not_found", "model_not_loaded"]:
                return True

        # Fallback to string matching for unstructured errors
        error_message = error_info.get("message") or ""
        error_message = error_message.lower()
        return any(
            phrase in error_message
            for phrase in [
                "model not loaded",
                "no model loaded",
                "model not found",
                "model is not loaded",
                "model does not exist",
                "model not available",
            ]
        )

    def _execute_with_auto_download(
        self, api_call: Callable, model: str, auto_download: bool = True
    ):
        """
        Execute an API call with auto-download retry logic.

        Args:
            api_call: Function to call (should raise exception if model not loaded)
            model: Model name
            auto_download: Whether to auto-download on model error

        Returns:
            Result of api_call()

        Raises:
            ModelDownloadCancelledError: If user cancels download
            InsufficientDiskSpaceError: If not enough disk space
            LemonadeClientError: If download/load fails
        """
        try:
            return api_call()
        except Exception as e:
            # Check if this is a model loading error and auto_download is enabled
            if auto_download and self._is_model_error(e):
                self.log.info(
                    f"{_emoji('📥', '[AUTO-DOWNLOAD]')} Model '{model}' not loaded, "
                    f"attempting auto-download and load..."
                )

                # Load model with auto-download (includes prompt, validation, etc.)
                self.load_model(model, timeout=60, auto_download=True)

                # Retry the API call
                self.log.info(
                    f"{_emoji('🔄', '[RETRY]')} Retrying API call with model: {model}"
                )
                return api_call()

            # Re-raise original error
            raise

    def chat_completions(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_completion_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        auto_download: bool = True,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the chat completions endpoint.

        If the model is not loaded, it will be automatically downloaded and loaded.

        Args:
            model: The model to use for completion
            messages: List of conversation messages with 'role' and 'content'
            temperature: Controls randomness (higher = more random)
            max_completion_tokens: Maximum number of output tokens to generate (preferred)
            max_tokens: Maximum number of output tokens to generate
                        (deprecated, use max_completion_tokens)
            stop: Sequences where generation should stop
            stream: Whether to stream the response
            timeout: Request timeout in seconds
            logprobs: Whether to include log probabilities
            tools: List of tools the model may call
            auto_download: Automatically download model if not available (default: True)
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with completion data
            For streaming: Generator yielding completion chunks

        Example response (non-streaming):
        {
          "id": "0",
          "object": "chat.completion",
          "created": 1742927481,
          "model": "model-name",
          "choices": [{
            "index": 0,
            "message": {
              "role": "assistant",
              "content": "Response text here"
            },
            "finish_reason": "stop"
          }]
        }
        """
        # Handle max_tokens vs max_completion_tokens
        if max_completion_tokens is None and max_tokens is None:
            max_completion_tokens = 1000  # Default value
        elif max_completion_tokens is not None and max_tokens is not None:
            self.log.warning(
                "Both max_completion_tokens and max_tokens provided. Using max_completion_tokens."
            )
        elif max_tokens is not None:
            max_completion_tokens = max_tokens

        # Use the OpenAI client for streaming if requested
        if stream:
            return self._stream_chat_completions_with_openai(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                stop=stop,
                timeout=timeout,
                logprobs=logprobs,
                tools=tools,
                auto_download=auto_download,
                **kwargs,
            )

        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/chat/completions"
        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "stream": stream,
            **kwargs,
        }

        if stop:
            data["stop"] = stop

        if logprobs:
            data["logprobs"] = logprobs

        if tools:
            data["tools"] = tools

        # Helper function for the actual API call
        def _make_request():
            self.log.debug(f"Sending chat completion request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = (
                    f"Error in chat completions "
                    f"(status {response.status_code}): {response.text}"
                )
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                token_count = len(
                    result["choices"][0].get("message", {}).get("content", "")
                )
                self.log.debug(
                    f"Chat completion successful. "
                    f"Approximate response length: {token_count} characters"
                )

            return result

        # Execute with auto-download retry logic
        try:
            return _make_request()
        except (requests.exceptions.RequestException, LemonadeClientError):
            # Use helper to handle auto-download and retry
            return self._execute_with_auto_download(_make_request, model, auto_download)

    def _stream_chat_completions_with_openai(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_completion_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        auto_download: bool = True,
        **kwargs,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream chat completions using the OpenAI client.

        Returns chunks in the format:
        {
            "id": "...",
            "object": "chat.completion.chunk",
            "created": 1742927481,
            "model": "...",
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "..."
                },
                "finish_reason": null
            }]
        }
        """
        # Create a client just for this request
        client = OpenAI(
            base_url=self.base_url,
            api_key="lemonade",  # required, but unused
            timeout=timeout,
        )

        # Create request parameters
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "stream": True,
            **kwargs,
        }

        if stop:
            request_params["stop"] = stop

        if logprobs:
            request_params["logprobs"] = logprobs

        if tools:
            request_params["tools"] = tools

        try:
            # Use the client to stream responses
            self.log.debug(f"Starting streaming chat completion with model: {model}")
            stream = client.chat.completions.create(**request_params)

            # Convert OpenAI client responses to our format
            tokens_generated = 0
            for chunk in stream:
                tokens_generated += 1
                # Convert to dict format expected by our API
                yield {
                    "id": chunk.id,
                    "object": "chat.completion.chunk",
                    "created": chunk.created,
                    "model": chunk.model,
                    "choices": [
                        {
                            "index": choice.index,
                            "delta": {
                                "role": (
                                    choice.delta.role
                                    if hasattr(choice.delta, "role")
                                    and choice.delta.role
                                    else None
                                ),
                                "content": (
                                    choice.delta.content
                                    if hasattr(choice.delta, "content")
                                    and choice.delta.content
                                    else None
                                ),
                            },
                            "finish_reason": choice.finish_reason,
                        }
                        for choice in chunk.choices
                    ],
                }

            self.log.debug(
                f"Completed streaming chat completion. Generated {tokens_generated} tokens."
            )

        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
            error_type = e.__class__.__name__
            error_msg = str(e)

            # Check if this is a model loading error and auto_download is enabled
            if auto_download and self._is_model_error(e):
                self.log.info(
                    f"{_emoji('📥', '[AUTO-DOWNLOAD]')} Model '{model}' not loaded, "
                    f"attempting auto-download and load..."
                )
                try:
                    # Load model with auto-download (may take hours for large models)
                    self.load_model(model, timeout=60, auto_download=True)

                    # Retry streaming
                    self.log.info(
                        f"{_emoji('🔄', '[RETRY]')} Retrying streaming chat completion "
                        f"with model: {model}"
                    )
                    stream = client.chat.completions.create(**request_params)

                    tokens_generated = 0
                    for chunk in stream:
                        tokens_generated += 1
                        yield {
                            "id": chunk.id,
                            "object": "chat.completion.chunk",
                            "created": chunk.created,
                            "model": chunk.model,
                            "choices": [
                                {
                                    "index": choice.index,
                                    "delta": {
                                        "role": (
                                            choice.delta.role
                                            if hasattr(choice.delta, "role")
                                            and choice.delta.role
                                            else None
                                        ),
                                        "content": (
                                            choice.delta.content
                                            if hasattr(choice.delta, "content")
                                            and choice.delta.content
                                            else None
                                        ),
                                    },
                                    "finish_reason": choice.finish_reason,
                                }
                                for choice in chunk.choices
                            ],
                        }

                    self.log.debug(
                        f"Completed streaming chat completion. Generated {tokens_generated} tokens."
                    )
                    return

                except Exception as load_error:
                    self.log.error(f"Auto-download/load failed: {load_error}")
                    raise LemonadeClientError(
                        f"Model '{model}' not loaded and auto-load failed: {load_error}"
                    )

            # Re-raise original error
            self.log.error(f"OpenAI {error_type}: {error_msg}")
            raise LemonadeClientError(f"OpenAI {error_type}: {error_msg}")
        except Exception as e:
            self.log.error(f"Error using OpenAI client for streaming: {str(e)}")
            raise LemonadeClientError(f"Streaming request failed: {str(e)}")

    def completions(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        echo: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        auto_download: bool = True,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the completions endpoint.

        If the model is not loaded, it will be automatically downloaded and loaded.

        Args:
            model: The model to use for completion
            prompt: The prompt to generate a completion for
            temperature: Controls randomness (higher = more random)
            max_tokens: Maximum number of tokens to generate (including input tokens)
            stop: Sequences where generation should stop
            stream: Whether to stream the response
            echo: Whether to include the prompt in the response
            timeout: Request timeout in seconds
            logprobs: Whether to include log probabilities
            auto_download: Automatically download model if not available (default: True)
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with completion data
            For streaming: Generator yielding completion chunks

        Example response:
        {
          "id": "0",
          "object": "text_completion",
          "created": 1742927481,
          "model": "model-name",
          "choices": [{
            "index": 0,
            "text": "Response text here",
            "finish_reason": "stop"
          }]
        }
        """
        # Use the OpenAI client for streaming if requested
        if stream:
            return self._stream_completions_with_openai(
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                echo=echo,
                timeout=timeout,
                logprobs=logprobs,
                auto_download=auto_download,
                **kwargs,
            )

        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/completions"
        data = {
            "model": model,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            "echo": echo,
            **kwargs,
        }

        if stop:
            data["stop"] = stop

        if logprobs:
            data["logprobs"] = logprobs

        # Helper function for the actual API call
        def _make_request():
            self.log.debug(f"Sending text completion request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = f"Error in completions (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                token_count = len(result["choices"][0].get("text", ""))
                self.log.debug(
                    f"Text completion successful. "
                    f"Approximate response length: {token_count} characters"
                )

            return result

        # Execute with auto-download retry logic
        try:
            return _make_request()
        except (requests.exceptions.RequestException, LemonadeClientError):
            # Use helper to handle auto-download and retry
            return self._execute_with_auto_download(_make_request, model, auto_download)

    def _stream_completions_with_openai(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        echo: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        **kwargs,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream completions using the OpenAI client.

        Returns chunks in the format:
        {
            "id": "...",
            "object": "text_completion",
            "created": 1742927481,
            "model": "...",
            "choices": [{
                "index": 0,
                "text": "...",
                "finish_reason": null
            }]
        }
        """
        client = OpenAI(
            base_url=self.base_url,
            api_key="lemonade",  # required, but unused
            timeout=timeout,
        )

        try:
            self.log.debug(f"Starting streaming text completion with model: {model}")
            # Create request parameters
            request_params = {
                "model": model,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stop": stop,
                "echo": echo,
                "stream": True,
                **kwargs,
            }

            if logprobs is not None:
                request_params["logprobs"] = logprobs

            response = client.completions.create(**request_params)

            tokens_generated = 0
            for chunk in response:
                tokens_generated += 1
                yield chunk.model_dump()

            self.log.debug(
                f"Completed streaming text completion. Generated {tokens_generated} tokens."
            )

        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
            error_type = e.__class__.__name__
            self.log.error(f"OpenAI {error_type}: {str(e)}")
            raise LemonadeClientError(f"OpenAI {error_type}: {str(e)}")
        except Exception as e:
            self.log.error(f"Error in OpenAI completion streaming: {str(e)}")
            raise LemonadeClientError(f"Error in OpenAI completion streaming: {str(e)}")

    def embeddings(
        self,
        input_texts: Union[str, List[str]],
        model: Optional[str] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Generate embeddings for input text(s) using Lemonade server.

        Args:
            input_texts: Single string or list of strings to embed
            model: Embedding model to use (defaults to self.model or nomic-embed-text-v2)
            timeout: Request timeout in seconds

        Returns:
            Dict with 'data' containing list of embedding vectors
        """
        try:
            # Ensure input is a list
            if isinstance(input_texts, str):
                input_texts = [input_texts]

            # Use specified model or default
            embedding_model = model or self.model or "nomic-embed-text-v2"

            payload = {"model": embedding_model, "input": input_texts}

            url = f"{self.base_url}/embeddings"
            response = self._send_request("POST", url, data=payload, timeout=timeout)

            return response

        except Exception as e:
            self.log.error(f"Error generating embeddings: {str(e)}")
            raise LemonadeClientError(f"Error generating embeddings: {str(e)}")

    def list_models(self, show_all: bool = False) -> Dict[str, Any]:
        """
        List available models from the server.

        Args:
            show_all: If True, returns full catalog including models not yet downloaded.
                      If False (default), returns only downloaded models.
                      When True, response includes additional fields:
                      - name: Human-readable model name
                      - downloaded: Boolean indicating local availability
                      - labels: Array of descriptive tags (e.g., "hot", "cpu", "hybrid")

        Returns:
            Dict containing the list of available models

        Examples:
            # List only downloaded models
            downloaded = client.list_models()

            # List full catalog for model discovery
            all_models = client.list_models(show_all=True)
            available = [m for m in all_models["data"] if not m.get("downloaded")]
        """
        url = f"{self.base_url}/models"
        if show_all:
            url += "?show_all=true"
        return self._send_request("get", url)

    def get_model_details(self, model_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific model.

        Args:
            model_id: The model identifier (e.g., "Qwen3-Coder-30B-GGUF")

        Returns:
            Dict containing model metadata:
            - id: Model identifier
            - created: Unix timestamp
            - object: Always "model"
            - owned_by: Attribution field
            - checkpoint: HuggingFace checkpoint reference
            - recipe: Framework/device specification (e.g., "oga-cpu", "oga-hybrid")

        Raises:
            LemonadeClientError: If model not found (404 error)

        Examples:
            # Get model checkpoint and recipe
            model = client.get_model_details("Qwen3-Coder-30B-GGUF")
            print(f"Checkpoint: {model['checkpoint']}")
            print(f"Recipe: {model['recipe']}")

            # Verify model exists before loading
            try:
                details = client.get_model_details(model_name)
                client.load_model(model_name)
            except LemonadeClientError as e:
                print(f"Model not found: {e}")
        """
        url = f"{self.base_url}/models/{model_id}"
        return self._send_request("get", url)

    def pull_model(
        self,
        model_name: str,
        checkpoint: Optional[str] = None,
        recipe: Optional[str] = None,
        reasoning: Optional[bool] = None,
        mmproj: Optional[str] = None,
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Install a model on the server.

        Args:
            model_name: Model name to install
            checkpoint: HuggingFace checkpoint to install (for registering new models)
            recipe: Lemonade API recipe to load the model with (for registering new models)
            reasoning: Whether the model is a reasoning model (for registering new models)
            mmproj: Multimodal Projector file for vision models (for registering new models)
            timeout: Request timeout in seconds (longer for model installation)

        Returns:
            Dict containing the status of the pull operation

        Raises:
            LemonadeClientError: If the model installation fails
        """
        self.log.info(f"Installing {model_name}")

        request_data = {"model_name": model_name}

        if checkpoint:
            request_data["checkpoint"] = checkpoint
        if recipe:
            request_data["recipe"] = recipe
        if reasoning is not None:
            request_data["reasoning"] = reasoning
        if mmproj:
            request_data["mmproj"] = mmproj

        url = f"{self.base_url}/pull"
        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Installed {model_name} successfully: response={response}")
            return response
        except Exception as e:
            message = f"Failed to install {model_name}: {e}"
            self.log.error(message)
            raise LemonadeClientError(message)

    def pull_model_stream(
        self,
        model_name: str,
        checkpoint: Optional[str] = None,
        recipe: Optional[str] = None,
        reasoning: Optional[bool] = None,
        vision: Optional[bool] = None,
        embedding: Optional[bool] = None,
        reranking: Optional[bool] = None,
        mmproj: Optional[str] = None,
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Install a model on the server with streaming progress updates.

        This method streams Server-Sent Events (SSE) during the download,
        providing real-time progress information.

        Args:
            model_name: Model name to install
            checkpoint: HuggingFace checkpoint to install (for registering new models)
            recipe: Lemonade API recipe to load the model with (for registering new models)
            reasoning: Whether the model is a reasoning model (for registering new models)
            vision: Whether the model has vision capabilities (for registering new models)
            embedding: Whether the model is an embedding model (for registering new models)
            reranking: Whether the model is a reranking model (for registering new models)
            mmproj: Multimodal Projector file for vision models (for registering new models)
            timeout: Request timeout in seconds (longer for model installation)
            progress_callback: Optional callback function called with progress dict on each event.
                               Signature: callback(event_type: str, data: dict) -> None
                               event_type is one of: "progress", "complete", "error"

        Yields:
            Dict containing progress event data with fields:
            - For "progress" events: file, file_index, total_files, bytes_downloaded,
              bytes_total, percent
            - For "complete" events: file_index, total_files, percent (100)
            - For "error" events: error message

        Raises:
            LemonadeClientError: If the model installation fails

        Example:
            # Using as generator
            for event in client.pull_model_stream("Qwen3-0.6B-GGUF"):
                if event.get("event") == "progress":
                    print(f"Downloading: {event['percent']}%")

            # Using with callback
            def on_progress(event_type, data):
                if event_type == "progress":
                    print(f"{data['file']}: {data['percent']}%")

            for _ in client.pull_model_stream("Qwen3-0.6B-GGUF", progress_callback=on_progress):
                pass
        """
        self.log.info(f"Installing {model_name} with streaming progress")

        request_data = {"model_name": model_name, "stream": True}

        if checkpoint:
            request_data["checkpoint"] = checkpoint
        if recipe:
            request_data["recipe"] = recipe
        if reasoning is not None:
            request_data["reasoning"] = reasoning
        if vision is not None:
            request_data["vision"] = vision
        if embedding is not None:
            request_data["embedding"] = embedding
        if reranking is not None:
            request_data["reranking"] = reranking
        if mmproj:
            request_data["mmproj"] = mmproj

        url = f"{self.base_url}/pull"

        try:
            response = requests.post(
                url,
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
                stream=True,
            )

            if response.status_code != 200:
                error_msg = f"Error pulling model (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            # Parse SSE stream
            event_type = None
            received_complete = False
            try:
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            data = json.loads(data_str)
                            data["event"] = event_type or "progress"

                            # Call the progress callback if provided
                            if progress_callback:
                                progress_callback(event_type or "progress", data)

                            yield data

                            # Track complete event
                            if event_type == "complete":
                                received_complete = True

                            # Check for error event
                            if event_type == "error":
                                error_msg = data.get(
                                    "error", "Unknown error during model pull"
                                )
                                raise LemonadeClientError(error_msg)

                        except json.JSONDecodeError:
                            self.log.warning(f"Failed to parse SSE data: {data_str}")
                            continue
            except requests.exceptions.ChunkedEncodingError:
                # Connection closed by server - this is normal after complete event
                if not received_complete:
                    raise

            self.log.info(f"Installed {model_name} successfully via streaming")

        except requests.exceptions.RequestException as e:
            message = f"Failed to install {model_name}: {e}"
            self.log.error(message)
            raise LemonadeClientError(message)

    def delete_model(
        self,
        model_name: str,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Delete a model from the server.

        Args:
            model_name: Model name to delete
            timeout: Request timeout in seconds

        Returns:
            Dict containing the status of the delete operation

        Raises:
            LemonadeClientError: If the model deletion fails
        """
        self.log.info(f"Deleting {model_name}")

        request_data = {"model_name": model_name}

        url = f"{self.base_url}/delete"
        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Deleted {model_name} successfully: response={response}")
            return response
        except Exception as e:
            message = f"Failed to delete {model_name}: {e}"
            self.log.error(message)
            raise LemonadeClientError(message)

    def ensure_model_downloaded(
        self,
        model_name: str,
        show_progress: bool = True,
        timeout: int = 7200,
    ) -> bool:
        """
        Ensure a model is downloaded, downloading if necessary.

        This method checks if the model is available on the server,
        and if not, downloads it via the /api/v1/pull endpoint.

        Large models can be 100GB+ and take hours to download on typical connections.

        Args:
            model_name: Model name to ensure is downloaded
            show_progress: Show progress messages during download
            timeout: Download timeout in seconds (default: 7200 = 2 hours)

        Returns:
            True if model is available (was already downloaded or successfully downloaded),
            False if download failed

        Example:
            client = LemonadeClient()
            if client.ensure_model_downloaded("Qwen3-0.6B-GGUF"):
                client.load_model("Qwen3-0.6B-GGUF")
        """
        try:
            # Check if model is already downloaded
            models_response = self.list_models()
            for model in models_response.get("data", []):
                if model.get("id") == model_name:
                    if model.get("downloaded", False):
                        if show_progress:
                            self.log.info(
                                f"{_emoji('✅', '[OK]')} Model already downloaded: {model_name}"
                            )
                        return True

            # Model not downloaded - attempt download
            if show_progress:
                self.log.info(
                    f"{_emoji('📥', '[DOWNLOADING]')} Downloading model: {model_name}"
                )
                self.log.info(
                    "   This may take minutes to hours depending on model size..."
                )

            # Download via pull_model
            self.pull_model(model_name, timeout=timeout)

            # Use the centralized download waiter
            return self._wait_for_model_download(
                model_name, timeout=timeout, show_progress=show_progress
            )

        except Exception as e:
            self.log.error(f"Failed to ensure model downloaded: {e}")
            return False

    def responses(
        self,
        model: str,
        input: Union[str, List[Dict[str, str]]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        stream: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the responses endpoint.

        Args:
            model: The model to use for the response
            input: A string or list of dictionaries input for the model to respond to
            temperature: Controls randomness (higher = more random)
            max_output_tokens: Maximum number of output tokens to generate
            stream: Whether to stream the response
            timeout: Request timeout in seconds
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with response data
            For streaming: Generator yielding response events

        Example response (non-streaming):
        {
          "id": "0",
          "created_at": 1746225832.0,
          "model": "model-name",
          "object": "response",
          "output": [{
            "id": "0",
            "content": [{
              "annotations": [],
              "text": "Response text here"
            }]
          }]
        }
        """
        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/responses"
        data = {
            "model": model,
            "input": input,
            "temperature": temperature,
            "stream": stream,
            **kwargs,
        }

        if max_output_tokens:
            data["max_output_tokens"] = max_output_tokens

        try:
            self.log.debug(f"Sending responses request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = f"Error in responses (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            if stream:
                # For streaming responses, we need to handle server-sent events
                # This is a simplified implementation - full SSE parsing might be needed
                return self._parse_sse_stream(response)
            else:
                result = response.json()
                if "output" in result and len(result["output"]) > 0:
                    content = result["output"][0].get("content", [])
                    if content and len(content) > 0:
                        text_length = len(content[0].get("text", ""))
                        self.log.debug(
                            f"Response successful. "
                            f"Approximate response length: {text_length} characters"
                        )
                return result

        except requests.exceptions.RequestException as e:
            self.log.error(f"Request failed: {str(e)}")
            raise LemonadeClientError(f"Request failed: {str(e)}")

    def _parse_sse_stream(self, response) -> Generator[Dict[str, Any], None, None]:
        """
        Parse server-sent events from streaming responses endpoint.

        This is a simplified implementation that may need enhancement
        for full SSE specification compliance.
        """
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                try:
                    data = line[6:]  # Remove "data: " prefix
                    if data.strip() == "[DONE]":
                        break
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    def _wait_for_model_download(
        self,
        model_name: str,
        timeout: int = 7200,
        show_progress: bool = True,
        download_task: Optional[DownloadTask] = None,
    ) -> bool:
        """
        Wait for a model download to complete by polling the models endpoint.

        Large models (up to 100GB) can take hours to download on typical connections:
        - 100GB @ 100Mbps = ~2-3 hours
        - 100GB @ 1Gbps = ~15-20 minutes

        Args:
            model_name: Model name to wait for
            timeout: Maximum time to wait in seconds (default: 7200 = 2 hours)
            show_progress: Show progress messages
            download_task: Optional DownloadTask for cancellation support

        Returns:
            True if model download completed, False if timeout or error

        Raises:
            ModelDownloadCancelledError: If download is cancelled
        """
        poll_interval = 30  # Check every 30 seconds for large downloads
        elapsed = 0

        while elapsed < timeout:
            # Check for cancellation
            if download_task and download_task.is_cancelled():
                if show_progress:
                    self.log.warning(
                        f"{_emoji('🚫', '[CANCELLED]')} Download cancelled for {model_name}"
                    )
                raise ModelDownloadCancelledError(f"Download cancelled: {model_name}")

            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                # Check if model is now downloaded
                models_response = self.list_models()
                for model in models_response.get("data", []):
                    if model.get("id") == model_name:
                        if model.get("downloaded", False):
                            if show_progress:
                                minutes = elapsed // 60
                                seconds = elapsed % 60
                                self.log.info(
                                    f"{_emoji('✅', '[OK]')} Model downloaded successfully: "
                                    f"{model_name} ({minutes}m {seconds}s)"
                                )
                            return True

                if show_progress and elapsed % 60 == 0:  # Show every 60s
                    minutes = elapsed // 60
                    self.log.info(
                        f"   {_emoji('⏳', '[WAIT]')} Downloading... {minutes} minutes elapsed"
                    )
            except ModelDownloadCancelledError:
                raise  # Re-raise cancellation
            except Exception as e:
                self.log.warning(f"Error checking download status: {e}")

        # Timeout reached
        if show_progress:
            minutes = timeout // 60
            self.log.warning(
                f"{_emoji('⏰', '[TIMEOUT]')} Download timeout ({minutes} minutes) "
                f"reached for {model_name}"
            )
        return False

    def load_model(
        self,
        model_name: str,
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
        auto_download: bool = False,
        download_timeout: int = 7200,
    ) -> Dict[str, Any]:
        """
        Load a model on the server.

        If auto_download is enabled and the model is not available:
        1. Prompts user for confirmation (with size and ETA)
        2. Validates disk space
        3. Downloads model with cancellation support
        4. Retries loading

        Args:
            model_name: Model name to load
            timeout: Request timeout in seconds (longer for model loading)
            auto_download: If True, automatically download the model if not available
            download_timeout: Timeout for model download in seconds (default: 7200 = 2 hours)
                             Large models can be 100GB+ and take hours to download

        Returns:
            Dict containing the status of the load operation

        Raises:
            ModelDownloadCancelledError: If user declines download or cancels
            InsufficientDiskSpaceError: If not enough disk space
            LemonadeClientError: If model loading fails
        """
        self.log.info(f"Loading {model_name}")

        request_data = {"model_name": model_name}
        url = f"{self.base_url}/load"

        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Loaded {model_name} successfully: response={response}")
            self.model = model_name
            return response
        except Exception as e:
            original_error = str(e)

            # Check if this is a "model not found" error and auto_download is enabled
            if not (auto_download and self._is_model_error(e)):
                # Not a model error or auto_download disabled - re-raise
                self.log.error(f"Failed to load {model_name}: {original_error}")
                if isinstance(e, LemonadeClientError):
                    raise
                raise LemonadeClientError(
                    f"Failed to load {model_name}: {original_error}"
                )

            # Auto-download flow
            self.log.info(
                f"{_emoji('📥', '[AUTO-DOWNLOAD]')} Model '{model_name}' not found, "
                f"initiating auto-download..."
            )

            # Get model info and size estimate
            model_info = self.get_model_info(model_name)
            size_gb = model_info["size_gb"]
            estimated_minutes = self._estimate_download_time(size_gb)

            # Prompt user for confirmation
            if not _prompt_user_for_download(model_name, size_gb, estimated_minutes):
                raise ModelDownloadCancelledError(
                    f"User declined download of {model_name}"
                )

            # Validate disk space
            _check_disk_space(size_gb)

            # Create and track download task
            download_task = DownloadTask(model_name=model_name, size_gb=size_gb)
            with self._downloads_lock:
                self.active_downloads[model_name] = download_task

            try:
                # Trigger model download
                self.pull_model(model_name, timeout=download_timeout)

                # Wait for download to complete (with cancellation support)
                self.log.info(
                    f"   {_emoji('⏳', '[WAIT]')} Waiting for model download to complete..."
                )
                self.log.info(
                    f"   {_emoji('💡', '[TIP]')} Tip: You can cancel with "
                    f"client.cancel_download(model_name)"
                )

                if self._wait_for_model_download(
                    model_name,
                    timeout=download_timeout,
                    show_progress=True,
                    download_task=download_task,
                ):
                    # Retry loading after successful download
                    self.log.info(
                        f"{_emoji('🔄', '[RETRY]')} Retrying model load: {model_name}"
                    )
                    response = self._send_request(
                        "post", url, request_data, timeout=timeout
                    )
                    self.log.info(
                        f"{_emoji('✅', '[OK]')} Loaded {model_name} successfully after download"
                    )
                    self.model = model_name
                    return response
                else:
                    raise LemonadeClientError(
                        f"Model download timed out for '{model_name}'"
                    )

            except ModelDownloadCancelledError:
                self.log.warning(f"Download cancelled for {model_name}")
                raise
            except InsufficientDiskSpaceError:
                self.log.error(f"Insufficient disk space for {model_name}")
                raise
            except Exception as download_error:
                self.log.error(f"Auto-download failed: {download_error}")
                raise LemonadeClientError(
                    f"Failed to auto-download '{model_name}': {download_error}"
                )
            finally:
                # Clean up download task
                with self._downloads_lock:
                    self.active_downloads.pop(model_name, None)

    def unload_model(self) -> Dict[str, Any]:
        """
        Unload the current model from the server.

        Returns:
            Dict containing the status of the unload operation
        """
        url = f"{self.base_url}/unload"
        response = self._send_request("post", url)
        self.model = None
        self.log.info(f"Model unloaded successfully: {response}")
        return response

    def set_params(
        self,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        do_sample: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Set generation parameters for text completion.

        Args:
            temperature: Controls randomness (higher = more random)
            top_p: Controls diversity via nucleus sampling
            top_k: Controls diversity by limiting to k most likely tokens
            min_length: Minimum length of generated text in tokens
            max_length: Maximum length of generated text in tokens
            do_sample: Whether to use sampling or greedy decoding

        Returns:
            Dict containing the status and updated parameters
        """
        request_data = {}

        if temperature is not None:
            request_data["temperature"] = temperature
        if top_p is not None:
            request_data["top_p"] = top_p
        if top_k is not None:
            request_data["top_k"] = top_k
        if min_length is not None:
            request_data["min_length"] = min_length
        if max_length is not None:
            request_data["max_length"] = max_length
        if do_sample is not None:
            request_data["do_sample"] = do_sample

        url = f"{self.base_url}/params"
        return self._send_request("post", url, request_data)

    def health_check(self) -> Dict[str, Any]:
        """
        Check server health.

        Returns:
            Dict containing the server status and loaded model

        Raises:
            LemonadeClientError: If the health check fails
        """
        url = f"{self.base_url}/health"
        return self._send_request("get", url)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics from the last request.

        Returns:
            Dict containing performance statistics
        """
        url = f"{self.base_url}/stats"
        return self._send_request("get", url)

    def get_system_info(self, verbose: bool = False) -> Dict[str, Any]:
        """
        Get system hardware information and device enumeration.

        Args:
            verbose: If True, returns additional details like Python packages
                     and extended system information

        Returns:
            Dict containing system information:
            - OS Version
            - Processor details
            - Physical Memory (RAM)
            - devices: Dictionary with device information
              - cpu: Name, cores, threads, availability
              - gpu: AMD iGPU/dGPU name, memory (MB), driver version, availability
              - npu: Name, driver version, power mode, availability

        Examples:
            # Check available devices
            sysinfo = client.get_system_info()
            devices = sysinfo.get("devices", {})

            # Select best device
            if devices.get("npu", {}).get("available"):
                print("Using NPU for acceleration")
            elif devices.get("gpu", {}).get("available"):
                print("Using GPU for acceleration")
            else:
                print("Using CPU")

            # Get detailed info
            detailed = client.get_system_info(verbose=True)
        """
        url = f"{self.base_url}/system-info"
        if verbose:
            url += "?verbose=true"
        return self._send_request("get", url)

    def ready(self) -> bool:
        """
        Check if the client is ready for use.

        Returns:
            bool: True if the client exists and the server is healthy, False otherwise
        """
        try:
            # Check if client exists and server is healthy
            health = self.health_check()
            return health.get("status") == "ok"
        except Exception:
            return False

    def validate_context_size(
        self,
        required_tokens: int = 32768,
        quiet: bool = False,
    ) -> tuple:
        """
        Validate that Lemonade server has sufficient context size.

        Checks the /health endpoint to verify the server's context size
        meets the required minimum.

        Args:
            required_tokens: Minimum required context size in tokens (default: 32768)
            quiet: Suppress output messages

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
            - success: True if context size is sufficient
            - error_message: Description of the issue if validation failed, None if successful

        Example:
            client = LemonadeClient()
            success, error = client.validate_context_size(required_tokens=32768)
            if not success:
                print(f"Context validation failed: {error}")
                sys.exit(1)
        """
        try:
            health = self.health_check()
            reported_ctx = health.get("context_size", 0)

            if reported_ctx >= required_tokens:
                self.log.debug(
                    f"Context size validated: {reported_ctx} >= {required_tokens}"
                )
                return True, None
            else:
                error_msg = (
                    f"Insufficient context size: server has {reported_ctx} tokens, "
                    f"but {required_tokens} tokens are required. "
                    f"Restart with: lemonade-server serve --ctx-size {required_tokens}"
                )
                if not quiet:
                    print(f"❌ {error_msg}")
                return False, error_msg

        except Exception as e:
            self.log.warning(f"Context validation failed: {e}")
            if not quiet:
                print(f"⚠️  Context validation failed: {e}")
            return True, None  # Don't block on connection errors

    def get_status(self) -> LemonadeStatus:
        """
        Get comprehensive Lemonade status.

        Returns:
            LemonadeStatus with server status and loaded models
        """
        status = LemonadeStatus(url=f"http://{self.host}:{self.port}")

        try:
            health = self.health_check()
            status.running = True
            status.health_data = health
            status.context_size = health.get("context_size", 0)

            # Get loaded models
            models_response = self.list_models()
            status.loaded_models = models_response.get("data", [])
        except Exception as e:
            self.log.debug(f"Failed to get status: {e}")
            status.running = False
            status.error = str(e)

        return status

    def get_agent_profile(self, agent: str) -> Optional[AgentProfile]:
        """
        Get agent profile by name.

        Args:
            agent: Name of the agent (chat, code, rag, talk, blender, etc.)

        Returns:
            AgentProfile if found, None otherwise
        """
        return AGENT_PROFILES.get(agent.lower())

    def list_agents(self) -> List[str]:
        """
        List all available agent profiles.

        Returns:
            List of agent profile names
        """
        return list(AGENT_PROFILES.keys())

    def get_required_models(self, agent: str = "all") -> List[str]:
        """
        Get list of model IDs required for an agent or all agents.

        Args:
            agent: Agent name or "all" for all unique models

        Returns:
            List of model IDs (e.g., ["Qwen3-Coder-30B-A3B-Instruct-GGUF", ...])
        """
        model_ids = set()

        if agent.lower() == "all":
            # Collect all unique models across all agents
            for profile in AGENT_PROFILES.values():
                for model_key in profile.models:
                    if model_key in MODELS:
                        model_ids.add(MODELS[model_key].model_id)
        else:
            # Get models for specific agent
            profile = self.get_agent_profile(agent)
            if profile:
                for model_key in profile.models:
                    if model_key in MODELS:
                        model_ids.add(MODELS[model_key].model_id)

        return list(model_ids)

    def check_model_available(self, model_id: str) -> bool:
        """
        Check if a model is available (downloaded) on the server.

        Args:
            model_id: Model ID to check

        Returns:
            True if model is available, False otherwise
        """
        try:
            # Use list_models with show_all=True to get download status
            models = self.list_models(show_all=True)
            for model in models.get("data", []):
                if model.get("id", "").lower() == model_id.lower():
                    return model.get("downloaded", False)
        except Exception:
            pass
        return False

    def download_agent_models(
        self,
        agent: str = "all",
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Download all models required for an agent with streaming progress.

        This method downloads all models needed by an agent (or all agents)
        and provides real-time progress updates via SSE streaming.

        Args:
            agent: Agent name (chat, code, rag, etc.) or "all" for all models
            timeout: Timeout per model in seconds
            progress_callback: Optional callback for progress updates.
                               Signature: callback(event_type: str, data: dict) -> None

        Returns:
            Dict with download results:
            - success: bool - True if all models downloaded
            - models: List[Dict] - Status for each model
            - errors: List[str] - Any error messages

        Example:
            def on_progress(event_type, data):
                if event_type == "progress":
                    print(f"{data['file']}: {data['percent']}%")

            result = client.download_agent_models("chat", progress_callback=on_progress)
        """
        model_ids = self.get_required_models(agent)

        if not model_ids:
            return {
                "success": True,
                "models": [],
                "errors": [],
                "message": f"No models required for agent '{agent}'",
            }

        results = {"success": True, "models": [], "errors": []}

        for model_id in model_ids:
            model_result = {"model_id": model_id, "status": "pending", "skipped": False}

            # Check if already available
            if self.check_model_available(model_id):
                model_result["status"] = "already_available"
                model_result["skipped"] = True
                results["models"].append(model_result)
                self.log.info(f"Model {model_id} already available, skipping download")
                continue

            # Download with streaming
            try:
                self.log.info(f"Downloading model: {model_id}")
                completed = False

                for event in self.pull_model_stream(
                    model_name=model_id,
                    timeout=timeout,
                    progress_callback=progress_callback,
                ):
                    if event.get("event") == "complete":
                        completed = True
                        model_result["status"] = "completed"
                    elif event.get("event") == "error":
                        model_result["status"] = "error"
                        model_result["error"] = event.get("error", "Unknown error")
                        results["errors"].append(f"{model_id}: {model_result['error']}")
                        results["success"] = False

                if not completed and model_result["status"] == "pending":
                    model_result["status"] = "completed"  # No explicit complete event

            except LemonadeClientError as e:
                model_result["status"] = "error"
                model_result["error"] = str(e)
                results["errors"].append(f"{model_id}: {e}")
                results["success"] = False

            results["models"].append(model_result)

        return results

    def check_model_loaded(self, model_id: str) -> bool:
        """
        Check if a specific model is loaded.

        Args:
            model_id: Model ID to check

        Returns:
            True if model is loaded, False otherwise
        """
        try:
            models_response = self.list_models()
            for model in models_response.get("data", []):
                if model.get("id", "").lower() == model_id.lower():
                    return True
                # Also check for partial match
                if model_id.lower() in model.get("id", "").lower():
                    return True
        except Exception:
            pass
        return False

    def _check_lemonade_installed(self) -> bool:
        """
        Check if lemonade-server is available.

        Checks in this order:
        1. Try health check on configured URL (LEMONADE_BASE_URL or default)
        2. If localhost and health check fails, check if binary is in PATH (for auto-start)
        3. If remote server and health check fails, return False (can't auto-start)

        Returns:
            True if server is available or can be started, False otherwise
        """
        # First, always try health check to see if server is already running
        try:
            health = self.health_check()
            if health.get("status") == "ok":
                return True
        except Exception:
            pass

        # Health check failed - determine if we can auto-start
        is_localhost = self.host in ("localhost", "127.0.0.1", "::1")

        if is_localhost:
            # Local server not running - check if binary is installed for auto-start
            return shutil.which("lemonade-server") is not None
        else:
            # Remote server not responding and we can't auto-start it
            return False

    def get_lemonade_version(self) -> Optional[str]:
        """
        Get the installed lemonade-server version.

        Returns:
            Version string (e.g., "8.2.2") or None if unable to determine
        """
        try:
            result = subprocess.run(
                ["lemonade-server", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,  # We handle errors by checking the output
            )

            # Combine stdout and stderr to get complete output
            full_output = result.stdout + result.stderr

            # Extract version number using regex (e.g., "8.2.2")
            version_match = re.search(r"(\d+\.\d+(?:\.\d+)?)", full_output)
            if version_match:
                return version_match.group(1)

            return None

        except Exception:
            return None

    def _check_version_compatibility(
        self, expected_version: str, quiet: bool = False
    ) -> bool:
        """
        Check if the installed lemonade-server version is compatible.

        Checks only the major version for compatibility.

        Args:
            expected_version: Expected version string (e.g., "8.2.2")
            quiet: Suppress warning output

        Returns:
            True if compatible (or version check failed), False if incompatible major version
        """
        actual_version = self.get_lemonade_version()

        if not actual_version:
            # Can't determine version, assume compatible (don't block)
            return True

        try:
            # Parse versions
            expected_parts = expected_version.split(".")
            actual_parts = actual_version.split(".")

            expected_major = int(expected_parts[0])
            actual_major = int(actual_parts[0])

            if expected_major != actual_major:
                if not quiet:
                    print("")
                    print(
                        f"{_emoji('⚠️', '[WARN]')}  Lemonade Server version mismatch detected!"
                    )
                    print(f"   Expected major version: {expected_major}.x.x")
                    print(f"   Installed version: {actual_version}")
                    print("")
                    print(
                        "   This may cause compatibility issues. "
                        f"Please install Lemonade Server {expected_version}:"
                    )
                    print("   https://lemonade-server.ai")
                    print("")

                return False

            return True

        except Exception:
            # If parsing fails, assume compatible (don't block)
            return True

    def initialize(
        self,
        agent: str = "mcp",
        ctx_size: Optional[int] = None,
        auto_start: bool = True,
        timeout: int = 120,
        verbose: bool = False,  # pylint: disable=unused-argument
        quiet: bool = False,
    ) -> LemonadeStatus:
        """
        Initialize Lemonade Server for a specific agent.

        This method:
        1. Checks if lemonade-server is installed
        2. Checks if server is running (health endpoint)
        3. Auto-starts with ctx-size=32768 if not running
        4. Validates context size and shows warning if too small

        With auto-download enabled, models are downloaded on-demand when needed,
        so we don't validate model availability during initialization.

        Args:
            agent: Agent name (chat, code, rag, talk, blender, jira, docker, vlm, minimal, mcp)
            ctx_size: Override context size (default: 32768 for most agents)
            auto_start: Automatically start server if not running
            timeout: Timeout in seconds for server startup
            verbose: Enable verbose output
            quiet: Suppress output (only errors)

        Returns:
            LemonadeStatus with server status and loaded models

        Example:
            client = LemonadeClient()
            status = client.initialize(agent="chat")

            # Initialize with custom context size
            status = client.initialize(agent="code", ctx_size=65536)
        """
        profile = self.get_agent_profile(agent)
        if not profile:
            if not quiet:
                print(
                    f"{_emoji('⚠️', '[WARN]')}  Unknown agent '{agent}', using 'mcp' profile"
                )
            profile = AGENT_PROFILES["mcp"]

        # Use 32768 as default context size for all agents (suitable for most tasks)
        # User can override with ctx_size parameter if needed
        required_ctx = ctx_size or 32768

        if not quiet:
            print(f"🍋 Initializing Lemonade for {profile.display_name}")
            print(f"   Context size: {required_ctx}")

        # Check if lemonade-server is installed
        if not self._check_lemonade_installed():
            if not quiet:
                print(f"{_emoji('❌', '[ERROR]')} Lemonade Server is not installed")
                print("")
                print(f"{_emoji('📥', '[DOWNLOAD]')} Download and install from:")
                print("   https://lemonade-server.ai")
                print("")
                print("GAIA will automatically start Lemonade Server once installed.")
                print("")
            status = LemonadeStatus(url=f"http://{self.host}:{self.port}")
            status.running = False
            status.error = "Lemonade Server not installed"
            return status

        # Check version compatibility (warning only, not fatal)
        from gaia.version import LEMONADE_VERSION

        self._check_version_compatibility(LEMONADE_VERSION, quiet=quiet)

        # Check current status
        status = self.get_status()

        if status.running:
            if not quiet:
                print("✅ Lemonade Server is running")
                print(f"   Current context size: {status.context_size}")

            # Check context size (warning only, not fatal)
            if status.context_size < required_ctx:
                if not quiet:
                    print("")
                    print(
                        f"{_emoji('⚠️', '[WARN]')}  Context size ({status.context_size}) "
                        f"is less than recommended ({required_ctx})"
                    )
                    print(
                        f"   For better performance, restart with: "
                        f"lemonade-server serve --ctx-size {required_ctx}"
                    )
                    print("")

            return status

        # Server not running
        if not auto_start:
            if not quiet:
                print(f"{_emoji('❌', '[ERROR]')} Lemonade Server is not running")
                print(f"   Start with: lemonade-server serve --ctx-size {required_ctx}")
            status.error = "Server not running"
            return status

        # Auto-start server
        if not quiet:
            print(
                f"{_emoji('🚀', '[START]')} Starting Lemonade Server "
                f"with ctx-size={required_ctx}..."
            )

        try:
            self.launch_server(ctx_size=required_ctx, background="terminal")

            # Wait for server to be ready
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    health = self.health_check()
                    if health.get("status") == "ok":
                        if not quiet:
                            print(
                                f"{_emoji('✅', '[OK]')} Lemonade Server started successfully"
                            )
                        status = self.get_status()
                        status.running = True
                        return status
                except Exception:
                    pass
                time.sleep(2)

            if not quiet:
                print(f"{_emoji('❌', '[ERROR]')} Failed to start Lemonade Server")
            status.error = "Failed to start server"
        except Exception as e:
            self.log.error(f"Failed to start server: {e}")
            if not quiet:
                print(f"{_emoji('❌', '[ERROR]')} Failed to start Lemonade Server: {e}")
            status.error = str(e)

        return status

    def _send_request(
        self,
        method: str,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Send a request to the server and return the response.

        Args:
            method: HTTP method (get, post, etc.)
            url: URL to send the request to
            data: Request payload
            timeout: Request timeout in seconds

        Returns:
            Response as a dict

        Raises:
            LemonadeClientError: If the request fails
        """
        try:
            headers = {"Content-Type": "application/json"}

            if method.lower() == "get":
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method.lower() == "post":
                response = requests.post(
                    url, json=data, headers=headers, timeout=timeout
                )
            else:
                raise LemonadeClientError(f"Unsupported HTTP method: {method}")

            if response.status_code >= 400:
                raise LemonadeClientError(
                    f"Request failed with status {response.status_code}: {response.text}"
                )

            return response.json()

        except requests.exceptions.RequestException as e:
            raise LemonadeClientError(f"Request failed: {str(e)}")
        except json.JSONDecodeError:
            raise LemonadeClientError(
                f"Failed to parse response as JSON: {response.text}"
            )


def create_lemonade_client(
    model: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    auto_start: bool = False,
    auto_load: bool = False,
    auto_pull: bool = True,
    verbose: bool = True,
    background: str = "terminal",
    keep_alive: bool = False,
) -> LemonadeClient:
    """
    Factory function to create and configure a LemonadeClient instance.

    This function provides a simplified way to create a LemonadeClient instance
    with proper configuration from environment variables and/or explicit parameters.

    Args:
        model: Name of the model to use
               (defaults to env var LEMONADE_MODEL or DEFAULT_MODEL_NAME)
        host: Host address for the Lemonade server
              (defaults to env var LEMONADE_HOST or DEFAULT_HOST)
        port: Port number for the Lemonade server
              (defaults to env var LEMONADE_PORT or DEFAULT_PORT)
        auto_start: Automatically start the server
        auto_load: Automatically load the model
        auto_pull: Whether to automatically pull the model if it's not available
                   (when auto_load=True)
        verbose: Whether to enable verbose logging
        background: How to run the server if auto_start is True:
                   - "terminal": Launch in a new terminal window (default)
                   - "silent": Run in background with output to log file
                   - "none": Run in foreground
        keep_alive: If True, don't terminate server when client is deleted

    Returns:
        A configured LemonadeClient instance
    """
    # Get configuration from environment variables with fallbacks to defaults
    env_model = os.environ.get("LEMONADE_MODEL")
    env_host = os.environ.get("LEMONADE_HOST")
    env_port = os.environ.get("LEMONADE_PORT")

    # Prioritize explicit parameters over environment variables over defaults
    model_name = model or env_model or DEFAULT_MODEL_NAME
    server_host = host or env_host or DEFAULT_HOST
    server_port = port or (int(env_port) if env_port else DEFAULT_PORT)

    # Create the client
    client = LemonadeClient(
        model=model_name,
        host=server_host,
        port=server_port,
        verbose=verbose,
        keep_alive=keep_alive,
    )

    # Auto-start server if requested
    if auto_start:
        try:
            # Check if server is already running
            try:
                client.health_check()
                client.log.info("Lemonade server is already running")
            except LemonadeClientError:
                # Server not running, start it
                client.log.info(
                    f"Starting Lemonade server at {server_host}:{server_port}"
                )
                client.launch_server(background=background)

                # Perform a health check to verify the server is running
                client.health_check()
        except Exception as e:
            client.log.error(f"Failed to start Lemonade server: {str(e)}")
            raise LemonadeClientError(f"Failed to start Lemonade server: {str(e)}")

    # Auto-load model if requested
    if auto_load:
        try:
            # Check if auto_pull is enabled and model needs to be pulled first
            if auto_pull:
                # Check if model is available
                models_response = client.list_models()
                available_models = [
                    model.get("id", "") for model in models_response.get("data", [])
                ]

                if model_name not in available_models:
                    client.log.info(
                        f"Model '{model_name}' not found in registry. "
                        f"Available models: {available_models}"
                    )
                    client.log.info(
                        f"Attempting to pull model '{model_name}' before loading..."
                    )

                    try:
                        # Try to pull the model first
                        pull_result = client.pull_model(
                            model_name, timeout=300
                        )  # 5 min timeout for download
                        client.log.info(f"Successfully pulled model: {pull_result}")
                    except Exception as pull_error:
                        client.log.warning(
                            f"Failed to pull model '{model_name}': {pull_error}"
                        )
                        client.log.info(
                            "Proceeding with load anyway - server may auto-install"
                        )
                else:
                    client.log.info(
                        f"Model '{model_name}' found in registry, proceeding with load"
                    )

            # Now attempt to load the model
            client.load_model(model_name, timeout=60)
        except Exception as e:
            # Extract detailed error information
            error_details = str(e)
            client.log.error(f"Failed to load {model_name}: {error_details}")

            # Try to get more details about available models for debugging
            try:
                models_response = client.list_models()
                available_models = [
                    model.get("id", "unknown")
                    for model in models_response.get("data", [])
                ]
                client.log.error(f"Available models: {available_models}")
                client.log.error(f"Attempted to load: {model_name}")
                if available_models:
                    client.log.error(
                        "Consider using one of the available models instead"
                    )
            except Exception as list_error:
                client.log.error(f"Could not list available models: {list_error}")

            # Include both original error and context in the raised exception
            enhanced_message = f"Failed to load {model_name}: {error_details}"
            if "available_models" in locals() and available_models:
                enhanced_message += f" (Available models: {available_models})"

            raise LemonadeClientError(enhanced_message)

    return client


def initialize_lemonade(
    agent: str = "mcp",
    ctx_size: Optional[int] = None,
    auto_start: bool = True,
    timeout: int = 120,
    verbose: bool = False,
    quiet: bool = False,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> LemonadeStatus:
    """
    Convenience function to initialize Lemonade Server.

    This is a simplified interface for initializing Lemonade with agent-specific
    profiles. It creates a temporary client and runs initialization.

    Args:
        agent: Agent name (chat, code, rag, talk, blender, jira, docker, vlm, minimal, mcp)
        ctx_size: Override context size
        auto_start: Automatically start server if not running
        timeout: Timeout for server startup
        verbose: Enable verbose output
        quiet: Suppress output
        host: Lemonade server host
        port: Lemonade server port

    Returns:
        LemonadeStatus with server status

    Example:
        from gaia.llm.lemonade_client import initialize_lemonade

        # Initialize for chat agent
        status = initialize_lemonade(agent="chat")

        # Initialize for code agent with larger context
        status = initialize_lemonade(agent="code", ctx_size=65536)
    """
    client = LemonadeClient(host=host, port=port, keep_alive=True)
    return client.initialize(
        agent=agent,
        ctx_size=ctx_size,
        auto_start=auto_start,
        timeout=timeout,
        verbose=verbose,
        quiet=quiet,
    )


def print_agent_profiles():
    """Print all available agent profiles and their requirements."""
    print("\n📋 Available Agent Profiles:\n")
    print(f"{'Agent':<12} {'Display Name':<20} {'Context Size':<15} {'Models'}")
    print("-" * 80)

    for name, profile in AGENT_PROFILES.items():
        models = ", ".join(profile.models) if profile.models else "None"
        print(
            f"{name:<12} {profile.display_name:<20} {profile.min_ctx_size:<15} {models}"
        )

    print("\n📦 Available Models:\n")
    print(f"{'Key':<20} {'Model ID':<40} {'Type'}")
    print("-" * 80)

    for key, model in MODELS.items():
        print(f"{key:<20} {model.model_id:<40} {model.model_type.value}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Show agent profiles
    print_agent_profiles()
    print("\n" + "=" * 80 + "\n")

    # Use the new factory function instead of direct instantiation
    client = create_lemonade_client(
        model=DEFAULT_MODEL_NAME,
        auto_start=True,
        auto_load=True,
        verbose=True,
    )

    try:
        # Check server health
        try:
            health = client.health_check()
            print(f"Server health: {health}")
        except Exception as e:
            print(f"Health check failed: {e}")

        # List available models
        try:
            print("\nListing available models:")
            models_list = client.list_models()
            print(json.dumps(models_list, indent=2))
        except Exception as e:
            print(f"Failed to list models: {e}")

        # Example: Using chat completions
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ]

        try:
            print("\nNon-streaming response:")
            response = client.chat_completions(
                model=DEFAULT_MODEL_NAME, messages=messages, timeout=30
            )
            print(response["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"Chat completion failed: {e}")

        try:
            print("\nStreaming response:")
            for chunk in client.chat_completions(
                model=DEFAULT_MODEL_NAME, messages=messages, stream=True, timeout=30
            ):
                if "choices" in chunk and chunk["choices"][0].get("delta", {}).get(
                    "content"
                ):
                    print(chunk["choices"][0]["delta"]["content"], end="", flush=True)
        except Exception as e:
            print(f"Streaming chat completion failed: {e}")

        print("\n\nDone!")

    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        # Make sure to terminate the server when done
        client.terminate_server()
