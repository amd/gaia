# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Configuration management for MCP clients (read-only as of #976).

Mutations to ``~/.gaia/mcp_servers.json`` go through the connectors framework
(``gaia.connectors.mcp_server.McpServerHandler.configure`` / ``disconnect``).
This class is the read path used by ``MCPClient``, ``ChatAgent`` and the
agent-builder template.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)


class MCPConfig:
    """Configuration manager for MCP servers.

    Stores server configurations in a JSON file for persistence.

    Config loading behaviour:
    - When ``config_file`` is provided: load only that file (no stacking).
    - When ``config_file`` is None (auto-load): stack configs in order of
      increasing priority:
      1. ``~/.gaia/mcp_servers.json`` (global, loaded first as base)
      2. ``./mcp_servers.json``       (local, overlaid on top; wins on conflicts)
      ``self.config_file`` is set to the local path when it exists, otherwise
      to the global path (determines where ``add_server`` / ``remove_server``
      writes).

    After ``__init__``, ``self.load_report`` contains a summary of what was
    loaded (used by callers that want to display config info to the user).

    Args:
        config_file: Explicit path to a configuration file.  When provided,
            auto-loading and config stacking are disabled.
    """

    def __init__(self, config_file: str = None):
        self._servers: Dict[str, Dict[str, Any]] = {}
        self.load_report: Dict[str, Any] = {}

        if config_file is not None:
            # Explicit file: load only this file, no stacking.
            self.config_file = Path(config_file)
            self._load()
            self.load_report = {
                "mode": "explicit",
                "config_file": self.config_file,
                "servers": list(self._servers.keys()),
            }
        else:
            # Auto-load with config stacking: global as base, local on top.
            global_path = Path.home() / ".gaia" / "mcp_servers.json"
            local_path = self._find_local_config()

            # Ensure the global config directory exists.
            global_path.parent.mkdir(parents=True, exist_ok=True)

            # Read each file independently so we can track overrides.
            global_servers = (
                self._read_servers(global_path) if global_path.exists() else {}
            )
            local_servers = self._read_servers(local_path) if local_path else {}

            # Servers defined in both files — local wins.
            overrides: List[str] = [k for k in local_servers if k in global_servers]

            # Merge: global first, local on top.
            self._servers = {**global_servers, **local_servers}

            # Writes go to local if it exists, otherwise to global.
            self.config_file = local_path if local_path else global_path

            # Use found local path for display, or CWD fallback if none found.
            display_local_path = (
                local_path if local_path else Path.cwd() / "mcp_servers.json"
            )

            self.load_report = {
                "mode": "auto",
                "config_file": self.config_file,
                "global": {
                    "path": global_path,
                    "exists": global_path.exists(),
                    "servers": list(global_servers.keys()),
                },
                "local": {
                    "path": display_local_path,
                    "exists": local_path is not None,
                    "servers": list(local_servers.keys()),
                },
                "overrides": overrides,
            }

    @staticmethod
    def _find_local_config() -> Optional[Path]:
        """Find local mcp_servers.json: check CWD first, then script directory."""
        cwd_path = Path.cwd() / "mcp_servers.json"
        if cwd_path.exists():
            return cwd_path

        main = sys.modules.get("__main__")
        if main and getattr(main, "__file__", None):
            script_dir = Path(main.__file__).resolve().parent
            script_path = script_dir / "mcp_servers.json"
            if script_path.exists():
                return script_path

        return None

    def _read_servers(self, path: Path) -> Dict[str, Any]:
        """Read and return the server dict from *path* without side effects.

        Fails loudly on malformed JSON: returning an empty dict on any error
        masks the underlying problem and lets corrupted state propagate
        silently into ``MCPClient`` (which then sees zero servers with no
        actionable error). A missing file returns an empty dict — the caller
        in ``__init__`` already gates on ``path.exists()``.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            from gaia.connectors.errors import ConnectorsError

            raise ConnectorsError(
                f"mcp_servers.json at {path} is corrupt: {e}. "
                f"Inspect the file or remove it to start fresh; the connectors "
                f"framework will recreate it on next configure()."
            ) from e
        except OSError as e:
            from gaia.connectors.errors import ConnectorsError

            raise ConnectorsError(
                f"failed to read mcp_servers.json at {path}: {e}"
            ) from e
        return data.get("mcpServers", data.get("servers", {}))

    def _load(self) -> None:
        """Load configuration from ``self.config_file`` (replaces current servers).

        Fails loudly via :meth:`_read_servers`; an actionable
        ``ConnectorsError`` propagates rather than silently masking corruption.
        """
        if not self.config_file.exists():
            logger.debug(f"Config file not found: {self.config_file}")
            return

        self._servers = self._read_servers(self.config_file)
        logger.debug(f"Loaded {len(self._servers)} servers from config")

    def get_server(self, name: str) -> Dict[str, Any]:
        """Get a server configuration.

        Args:
            name: Server name

        Returns:
            dict: Server configuration or empty dict if not found
        """
        return self._servers.get(name, {})

    def get_servers(self) -> Dict[str, Dict[str, Any]]:
        """Get all server configurations.

        Returns:
            dict: All server configurations
        """
        return self._servers.copy()

    def server_exists(self, name: str) -> bool:
        """Check if a server exists in configuration.

        Args:
            name: Server name

        Returns:
            bool: True if server exists
        """
        return name in self._servers
