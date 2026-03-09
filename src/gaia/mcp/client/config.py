# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Configuration management for MCP clients."""

import json
from pathlib import Path
from typing import Any, Dict, List

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
            local_path = Path.cwd() / "mcp_servers.json"

            # Ensure the global config directory exists.
            global_path.parent.mkdir(parents=True, exist_ok=True)

            # Read each file independently so we can track overrides.
            global_servers = (
                self._read_servers(global_path) if global_path.exists() else {}
            )
            local_servers = (
                self._read_servers(local_path) if local_path.exists() else {}
            )

            # Servers defined in both files — local wins.
            overrides: List[str] = [k for k in local_servers if k in global_servers]

            # Merge: global first, local on top.
            self._servers = {**global_servers, **local_servers}

            # Writes go to local if it exists, otherwise to global.
            self.config_file = local_path if local_path.exists() else global_path

            self.load_report = {
                "mode": "auto",
                "config_file": self.config_file,
                "global": {
                    "path": global_path,
                    "exists": global_path.exists(),
                    "servers": list(global_servers.keys()),
                },
                "local": {
                    "path": local_path,
                    "exists": local_path.exists(),
                    "servers": list(local_servers.keys()),
                },
                "overrides": overrides,
            }

    def _read_servers(self, path: Path) -> Dict[str, Any]:
        """Read and return the server dict from *path* without side effects."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("mcpServers", data.get("servers", {}))
        except Exception as e:
            logger.error(f"Error reading config from {path}: {e}")
            return {}

    def _load(self) -> None:
        """Load configuration from ``self.config_file`` (replaces current servers)."""
        if not self.config_file.exists():
            logger.debug(f"Config file not found: {self.config_file}")
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Support both new 'mcpServers' and legacy 'servers' key
                self._servers = data.get("mcpServers", data.get("servers", {}))
            logger.debug(f"Loaded {len(self._servers)} servers from config")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self._servers = {}

    def _save(self) -> None:
        """Save configuration to file (always uses 'mcpServers' key)."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": self._servers}, f, indent=2)
            logger.debug(f"Saved config to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    def add_server(self, name: str, config: Dict[str, Any]) -> None:
        """Add or update a server configuration.

        Args:
            name: Server name
            config: Server configuration dictionary
        """
        self._servers[name] = config
        self._save()

    def remove_server(self, name: str) -> None:
        """Remove a server configuration.

        Args:
            name: Server name
        """
        if name in self._servers:
            del self._servers[name]
            self._save()

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
