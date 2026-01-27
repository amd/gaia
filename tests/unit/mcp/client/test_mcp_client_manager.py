"""Unit tests for MCPClientManager."""

from unittest.mock import Mock, patch

import pytest

from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager


class TestMCPClientManager:
    """Test MCPClientManager functionality."""

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_creates_and_connects_client(self, mock_client_class):
        """Test that add_server creates and connects a client."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()
        client = manager.add_server("test", command="echo test")

        assert client == mock_client
        mock_client_class.from_command.assert_called_once()
        mock_client.connect.assert_called_once()
        assert "test" in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_raises_if_connection_fails(self, mock_client_class):
        """Test that add_server raises error if connection fails."""
        mock_client = Mock()
        mock_client.connect.return_value = False
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()

        with pytest.raises(RuntimeError, match="Failed to connect"):
            manager.add_server("test", command="echo test")

        # Should not be added to manager
        assert "test" not in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_add_server_raises_if_already_exists(self, mock_client_class):
        """Test that add_server raises error if server already exists."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("test", command="echo test")

        with pytest.raises(ValueError, match="already exists"):
            manager.add_server("test", command="echo test2")

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_remove_server_disconnects_and_removes(self, mock_client_class):
        """Test that remove_server disconnects and removes client."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("test", command="echo test")
        manager.remove_server("test")

        mock_client.disconnect.assert_called_once()
        assert "test" not in manager.list_servers()

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_get_client_returns_client(self, mock_client_class):
        """Test that get_client returns the correct client."""
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("test", command="echo test")

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
        mock_client_class.from_command.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("server1", command="echo 1")
        manager.add_server("server2", command="echo 2")

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
        mock_client_class.from_command.side_effect = [mock_client1, mock_client2]

        manager = MCPClientManager()
        manager.add_server("server1", command="echo 1")
        manager.add_server("server2", command="echo 2")
        manager.disconnect_all()

        mock_client1.disconnect.assert_called_once()
        mock_client2.disconnect.assert_called_once()
        assert len(manager.list_servers()) == 0


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

        config.add_server("test", {"command": "echo test"})

        assert config_file.exists()
        assert config.server_exists("test")

    def test_remove_server_deletes_from_config(self, tmp_path):
        """Test that remove_server removes from file."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("test", {"command": "echo test"})
        config.remove_server("test")

        assert not config.server_exists("test")

    def test_get_server_returns_config(self, tmp_path):
        """Test that get_server returns correct configuration."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("test", {"command": "echo test", "timeout": 30})

        server_config = config.get_server("test")
        assert server_config["command"] == "echo test"
        assert server_config["timeout"] == 30

    def test_get_servers_returns_all(self, tmp_path):
        """Test that get_servers returns all configurations."""
        config_file = tmp_path / "test_config.json"
        config = MCPConfig(str(config_file))

        config.add_server("server1", {"command": "echo 1"})
        config.add_server("server2", {"command": "echo 2"})

        servers = config.get_servers()
        assert len(servers) == 2
        assert "server1" in servers
        assert "server2" in servers

    def test_config_persists_across_instances(self, tmp_path):
        """Test that configuration persists across instances."""
        config_file = tmp_path / "test_config.json"

        # First instance
        config1 = MCPConfig(str(config_file))
        config1.add_server("test", {"command": "echo test"})

        # Second instance should load from file
        config2 = MCPConfig(str(config_file))
        assert config2.server_exists("test")
        assert config2.get_server("test")["command"] == "echo test"
