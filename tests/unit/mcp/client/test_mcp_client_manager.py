# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCPClientManager."""

from unittest.mock import Mock, patch

import pytest

from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager


class TestMCPClientManager:
    """Test MCPClientManager functionality."""

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_creates_and_connects_client(self, mock_client_class):
        """Test that add_server creates and connects a client using config dict."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config = {"command": "npx", "args": ["-y", "server"]}
        client = manager.add_server("test", config)

        assert client == mock_client
        mock_client_class.from_config.assert_called_once_with(
            "test", config, debug=False
        )
        mock_client.connect.assert_called_once()
        assert "test" in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_raises_if_connection_fails(self, mock_client_class):
        """Test that add_server raises error if connection fails."""
        mock_client = Mock()
        mock_client.connect.return_value = False
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config = {"command": "npx", "args": ["-y", "server"]}

        with pytest.raises(RuntimeError, match="Failed to connect"):
            manager.add_server("test", config)

        # Should not be added to manager
        assert "test" not in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_raises_if_already_exists(self, mock_client_class):
        """Test that add_server raises error if server already exists."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config1 = {"command": "npx", "args": ["-y", "server1"]}
        config2 = {"command": "npx", "args": ["-y", "server2"]}
        manager.add_server("test", config1)

        with pytest.raises(ValueError, match="already exists"):
            manager.add_server("test", config2)

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_remove_server_disconnects_and_removes(self, mock_client_class):
        """Test that remove_server disconnects and removes client."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config = {"command": "npx", "args": ["-y", "server"]}
        manager.add_server("test", config)
        manager.remove_server("test")

        mock_client.disconnect.assert_called_once()
        assert "test" not in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_get_client_returns_client(self, mock_client_class):
        """Test that get_client returns the correct client."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config = {"command": "npx", "args": ["-y", "server"]}
        manager.add_server("test", config)

        client = manager.get_client("test")
        assert client == mock_client

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_get_client_returns_none_if_not_found(self, mock_client_class):
        """Test that get_client returns None for non-existent server."""
        manager = MCPClientManager()

        client = manager.get_client("nonexistent")
        assert client is None

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_list_servers_returns_all_names(self, mock_client_class):
        """Test that list_servers returns all server names."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("server1", {"command": "npx", "args": ["-y", "s1"]})
        manager.add_server("server2", {"command": "npx", "args": ["-y", "s2"]})

        servers = manager.list_servers()
        assert len(servers) == 2
        assert "server1" in servers
        assert "server2" in servers

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_disconnect_all_disconnects_all_clients(self, mock_client_class):
        """Test that disconnect_all disconnects all clients."""
        mock_client1 = Mock()
        mock_client1.connect.return_value = True
        mock_client2 = Mock()
        mock_client2.connect.return_value = True
        mock_client_class.from_config.side_effect = [mock_client1, mock_client2]

        manager = MCPClientManager()
        manager.add_server("server1", {"command": "npx", "args": ["-y", "s1"]})
        manager.add_server("server2", {"command": "npx", "args": ["-y", "s2"]})
        manager.disconnect_all()

        mock_client1.disconnect.assert_called_once()
        mock_client2.disconnect.assert_called_once()
        assert len(manager.list_servers()) == 0

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_requires_config_dict(self, mock_client_class):
        """Test that add_server requires a config dict, not a command string."""
        manager = MCPClientManager()

        # Should raise when passing a string instead of dict
        with pytest.raises(ValueError, match="config dict"):
            manager.add_server("test", "echo test")

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_extracts_command_args_env(self, mock_client_class):
        """Test that add_server properly extracts command, args, env from config."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        config = {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_xxx"},
        }
        manager.add_server("github", config)

        # Verify from_config was called with the full config
        mock_client_class.from_config.assert_called_once_with(
            "github", config, debug=False
        )

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    @patch("gaia.mcp.client.mcp_client_manager.logger")
    def test_load_from_config_skips_http_type(self, mock_logger, mock_client_class):
        """Test that HTTP/sse type connections are skipped with a warning."""
        manager = MCPClientManager()

        # Manually set up config with an HTTP server
        manager.config._servers = {
            "sse_server": {
                "type": "sse",
                "url": "http://localhost:8080/sse",
            },
            "http_server": {
                "type": "http",
                "url": "http://localhost:8081",
            },
        }

        manager.load_from_config()

        # No servers should be loaded
        assert len(manager.list_servers()) == 0

        # Should have logged warnings about unsupported type
        warning_calls = [call for call in mock_logger.warning.call_args_list]
        assert len(warning_calls) >= 2  # At least 2 warnings for the 2 servers

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    @patch("gaia.mcp.client.mcp_client_manager.logger")
    def test_add_server_rejects_http_type(self, mock_logger, mock_client_class):
        """Test that add_server raises error for non-stdio transport types."""
        manager = MCPClientManager()
        config = {
            "type": "sse",
            "url": "http://localhost:8080/sse",
        }

        with pytest.raises(ValueError, match="only supports stdio"):
            manager.add_server("http_server", config)


class TestMCPConfig:
    """Test MCPConfig functionality."""

    def test_config_creates_default_file_path(self, tmp_path, monkeypatch):
        """Test that config uses default path if not specified."""
        monkeypatch.setenv("HOME", str(tmp_path))

        config = MCPConfig()

        expected_path = tmp_path / ".gaia" / "mcp_servers.json"
        assert config.config_file == expected_path

    def test_add_server_saves_config(self, tmp_path):
        """Test that add_server persists to file."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("test", {"command": "npx", "args": ["-y", "server"]})

        assert config_file.exists()
        assert config.server_exists("test")

    def test_remove_server_deletes_from_config(self, tmp_path):
        """Test that remove_server removes from file."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("test", {"command": "npx", "args": ["-y", "server"]})
        config.remove_server("test")

        assert not config.server_exists("test")

    def test_get_server_returns_config(self, tmp_path):
        """Test that get_server returns correct configuration."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server(
            "test", {"command": "npx", "args": ["-y", "server"], "env": {"DEBUG": "1"}}
        )

        server_config = config.get_server("test")
        assert server_config["command"] == "npx"
        assert server_config["args"] == ["-y", "server"]
        assert server_config["env"] == {"DEBUG": "1"}

    def test_get_servers_returns_all(self, tmp_path):
        """Test that get_servers returns all configurations."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("server1", {"command": "npx", "args": ["-y", "s1"]})
        config.add_server("server2", {"command": "npx", "args": ["-y", "s2"]})

        servers = config.get_servers()
        assert len(servers) == 2
        assert "server1" in servers
        assert "server2" in servers

    def test_config_persists_across_instances(self, tmp_path):
        """Test that configuration persists across instances."""
        config_file = tmp_path / "test_config.json"

        # First instance
        config1 = MCPConfig(str(config_file))
        config1.add_server("test", {"command": "npx", "args": ["-y", "server"]})

        # Second instance should load from file
        config2 = MCPConfig(str(config_file))
        assert config2.server_exists("test")
        assert config2.get_server("test")["command"] == "npx"
        assert config2.get_server("test")["args"] == ["-y", "server"]

    def test_config_uses_mcpServers_key(self, tmp_path):
        """Test that config file uses 'mcpServers' as root key (Anthropic format)."""
        import json

        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server(
            "github",
            {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_xxx"},
            },
        )

        # Read the file directly and verify format
        with open(config_file, "r") as f:
            data = json.load(f)

        assert "mcpServers" in data
        assert "servers" not in data  # Old key should not be present
        assert "github" in data["mcpServers"]
        assert data["mcpServers"]["github"]["command"] == "npx"
        assert data["mcpServers"]["github"]["args"] == [
            "-y",
            "@modelcontextprotocol/server-github",
        ]

    def test_config_reads_command_and_args_separately(self, tmp_path):
        """Test that config correctly reads command and args as separate fields."""
        import json

        config_file = tmp_path / "test_config.json"

        # Write config in new format directly
        with open(config_file, "w") as f:
            json.dump(
                {
                    "mcpServers": {
                        "time": {"command": "uvx", "args": ["mcp-server-time"]}
                    }
                },
                f,
            )

        config = MCPConfig(str(config_file))
        server = config.get_server("time")

        assert server["command"] == "uvx"
        assert server["args"] == ["mcp-server-time"]

    def test_config_handles_missing_file(self, tmp_path):
        """Test that config handles missing file gracefully."""
        # Config file doesn't exist yet (but parent dir does)
        config_file = tmp_path / "config.json"
        assert not config_file.exists()

        config = MCPConfig(str(config_file))

        # Should have empty servers when file doesn't exist
        assert config.get_servers() == {}
        assert not config.server_exists("any")

        # Should be able to add servers (creates file)
        config.add_server("test", {"command": "echo", "args": ["test"]})
        assert config.server_exists("test")
        assert config_file.exists()

        # Verify file contents
        import json

        data = json.loads(config_file.read_text())
        assert "mcpServers" in data
        assert "test" in data["mcpServers"]

    def test_config_reads_env_field(self, tmp_path):
        """Test that config reads env field from mcpServers format."""
        import json

        config_file = tmp_path / "test_config.json"

        # Write config with env
        with open(config_file, "w") as f:
            json.dump(
                {
                    "mcpServers": {
                        "github": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-github"],
                            "env": {"GITHUB_TOKEN": "ghp_xxx", "DEBUG": "true"},
                        }
                    }
                },
                f,
            )

        config = MCPConfig(str(config_file))
        server = config.get_server("github")

        assert server["env"]["GITHUB_TOKEN"] == "ghp_xxx"
        assert server["env"]["DEBUG"] == "true"
