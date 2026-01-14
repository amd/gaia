# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Lazy Lemonade Server Manager for GAIA.

Provides singleton initialization shared by CLI and SDK flows.
Operates at the LLM level (not agent level) for flexibility with community agents.
"""

import os
import sys
import threading
from enum import Enum
from typing import Optional

from gaia.llm.lemonade_client import LemonadeClient
from gaia.logger import get_logger

# Default context size for GAIA agents (supports most complex tasks)
DEFAULT_CONTEXT_SIZE = 32768
DEFAULT_LEMONADE_URL = "http://localhost:8000"


class MessageType(Enum):
    """Message type for context size notifications."""

    ERROR = "error"
    WARNING = "warning"


class LemonadeManager:
    """Singleton manager for lazy Lemonade server initialization.

    Operates at the LLM level, not tied to specific agent implementations.
    This allows community agents to use GAIA without being hardcoded into profiles.

    Example:
        # Basic usage - just ensure Lemonade is running (default: 32768 context)
        if LemonadeManager.ensure_ready():
            print("Lemonade is ready")

        # With smaller context size for simple tasks
        LemonadeManager.ensure_ready(min_context_size=4096)

        # CLI usage (verbose)
        LemonadeManager.ensure_ready(quiet=False)

        # Get base URL after initialization if needed
        base_url = LemonadeManager.get_base_url()
    """

    _initialized = False
    _base_url: Optional[str] = None
    _context_size: int = 0
    _lock = threading.Lock()
    _log = get_logger(__name__)

    @classmethod
    def is_lemonade_installed(cls) -> bool:
        """Check if Lemonade server is installed."""
        client = LemonadeClient(verbose=False)
        return client.get_lemonade_version() is not None

    @classmethod
    def print_server_error(cls, min_context_size: int = DEFAULT_CONTEXT_SIZE):
        """Print informative error when Lemonade server is not running.

        Shared by CLI and SDK for consistent error messages.

        Args:
            min_context_size: Context size to recommend in error message.
        """
        print(
            "❌ Error: Lemonade server is not running or not accessible.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)

        if not cls.is_lemonade_installed():
            print(
                "📥 Lemonade server is not installed on your system.", file=sys.stderr
            )
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
            if min_context_size >= 32768:
                print(
                    f"  • Running: lemonade-server serve --ctx-size {min_context_size}",
                    file=sys.stderr,
                )
            else:
                print("  • Running: lemonade-server serve", file=sys.stderr)
            print("", file=sys.stderr)
            if min_context_size >= 32768:
                print(
                    f"Note: GAIA requires larger context size ({min_context_size} tokens)",
                    file=sys.stderr,
                )
                print("", file=sys.stderr)
            base_url = os.getenv("LEMONADE_BASE_URL", f"{DEFAULT_LEMONADE_URL}/api/v1")
            print(
                f"The server should be accessible at {base_url}/health",
                file=sys.stderr,
            )
            print("Then try your command again.", file=sys.stderr)

    @classmethod
    def print_context_message(
        cls,
        current_size: int,
        required_size: int,
        message_type: MessageType = MessageType.ERROR,
    ):
        """Print message when context size is insufficient.

        Shared by CLI and SDK for consistent messages.

        Args:
            current_size: Current server context size in tokens.
            required_size: Required context size in tokens.
            message_type: MessageType.WARNING for warning, MessageType.ERROR for error.
        """
        if message_type == MessageType.WARNING:
            symbol = "⚠️ "
            label = "Context size below recommended"
        else:
            symbol = "❌"
            label = "Insufficient context size"

        print("", file=sys.stderr)
        print(f"{symbol} {label}.", file=sys.stderr)
        print(
            f"   Current: {current_size} tokens, Required: {required_size} tokens",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("   To fix this issue:", file=sys.stderr)
        print("   1. Stop the Lemonade server (if running)", file=sys.stderr)
        print(
            f"   2. Restart with: lemonade-server serve --ctx-size {required_size}",
            file=sys.stderr,
        )
        print("", file=sys.stderr)

    @classmethod
    def ensure_ready(
        cls,
        min_context_size: int = DEFAULT_CONTEXT_SIZE,
        quiet: bool = True,
        base_url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> bool:
        """Ensure Lemonade server is running with sufficient context size.

        This is the main entry point for both CLI and SDK flows.
        Safe to call multiple times - validates context size on each call.

        Args:
            min_context_size: Minimum context size required (default: 32768).
            quiet: Suppress output (default: True for SDK, set False for CLI)
            base_url: Full base URL (e.g., "http://localhost:8000/api/v1").
                     If provided, host and port are parsed from it.
            host: Override host (default: from LEMONADE_BASE_URL env or localhost)
            port: Override port (default: from LEMONADE_BASE_URL env or 8000)

        Returns:
            True if Lemonade server is ready, False otherwise.
            Use get_base_url() to retrieve the server URL after initialization.

        Note:
            The Lemonade server must be running before calling this method.
            Start it with: lemonade-server serve --ctx-size 32768
        """
        # Parse host and port from base_url if provided
        if base_url and (host is None or port is None):
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            if host is None:
                host = parsed.hostname
            if port is None:
                port = parsed.port
        with cls._lock:
            # If already initialized, just verify context size
            if cls._initialized:
                if cls._context_size >= min_context_size:
                    cls._log.debug(
                        "Lemonade already initialized with sufficient context"
                    )
                    return True
                else:
                    # Context size insufficient - warn and continue
                    cls._log.warning(
                        f"Lemonade running with {cls._context_size} tokens, "
                        f"but {min_context_size} requested. "
                        f"Restart with: lemonade-server serve --ctx-size {min_context_size}"
                    )
                    if not quiet:
                        cls.print_context_message(
                            cls._context_size, min_context_size, MessageType.WARNING
                        )
                    return True

            cls._log.debug(f"Initializing Lemonade (min context: {min_context_size})")

            try:
                client = LemonadeClient(
                    host=host,
                    port=port,
                    keep_alive=True,
                    verbose=not quiet,
                )

                # Just check server status - no agent profile required
                status = client.get_status()

                if not status.running:
                    cls._log.warning("Lemonade server is not running")
                    if not quiet:
                        cls.print_server_error(min_context_size)
                    return False

                # Cache server state for subsequent calls.
                # We set initialized=True even if context check fails below,
                # so future calls can use cached state without reconnecting.
                cls._initialized = True
                cls._base_url = client.base_url
                cls._context_size = status.context_size or 0

                cls._log.debug(
                    f"Lemonade ready at {cls._base_url} "
                    f"(context: {cls._context_size} tokens)"
                )

                # Verify context size - warn if insufficient
                if cls._context_size < min_context_size:
                    cls._log.warning(
                        f"Context size {cls._context_size} is less than "
                        f"requested {min_context_size}. Some features may not work correctly."
                    )
                    if not quiet:
                        cls.print_context_message(
                            cls._context_size, min_context_size, MessageType.WARNING
                        )
                    return True

                return True

            except Exception as e:
                cls._log.warning(f"Failed to initialize Lemonade: {e}")
                if not quiet:
                    cls.print_server_error(min_context_size)
                return False

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if Lemonade has been initialized."""
        return cls._initialized

    @classmethod
    def get_base_url(cls) -> Optional[str]:
        """Get the base URL if initialized."""
        return cls._base_url

    @classmethod
    def get_context_size(cls) -> int:
        """Get the current context size."""
        return cls._context_size

    @classmethod
    def reset(cls):
        """Reset initialization state.

        Primarily used for testing to allow re-initialization.
        """
        with cls._lock:
            cls._initialized = False
            cls._base_url = None
            cls._context_size = 0
            cls._log.debug("LemonadeManager state reset")
