# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Configuration management for MCP clients."""

import json
from pathlib import Path
from typing import Any, Dict

from gaia.logger import get_logger

logger = get_logger(__name__)


class MCPConfig:
    """Configuration manager for MCP servers.

    Stores server configurations in a JSON file for persistence.

    Config file lookup order (when config_file is not specified):
    1. ./mcp_servers.json (current working directory - project-local config)
    2. ~/.gaia/mcp_servers.json (user home directory - global config)

    Args:
        config_file: Path to configuration file (default: two-stage lookup)
    """

    def __init__(self, config_file: str = None):
        if config_file is None:
            # Two-stage lookup:
            # 1. Check current working directory first (project-local config)
            local_config = Path.cwd() / "mcp_servers.json"
            if local_config.exists():
                config_file = local_config
                logger.debug(f"Using local config: {config_file}")
            else:
                # 2. Fall back to user's home directory (global config)
                config_dir = Path.home() / ".gaia"
                config_dir.mkdir(parents=True, exist_ok=True)
                config_file = config_dir / "mcp_servers.json"
                logger.debug(f"Using global config: {config_file}")

        self.config_file = Path(config_file)
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load configuration from file."""
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
