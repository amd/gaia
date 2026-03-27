# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCPClientManager."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager


def _mock_home(monkeypatch, home_dir: Path):
    """Monkeypatch Path.home() and HOME/USERPROFILE to use a custom directory."""
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home_dir))


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

    def test_disconnect_all_clears_failed_dict(self):
        """disconnect_all must clear _failed so stale errors don't persist."""
        manager = MCPClientManager()
        # Seed _failed directly to simulate a previous failed connection
        manager._failed["removed_server"] = "connection refused"
        manager.disconnect_all()
        assert len(manager._failed) == 0

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
        """Test that config uses global path when no local mcp_servers.json exists."""
        empty_dir = tmp_path / "no_local"
        empty_dir.mkdir()
        monkeypatch.chdir(empty_dir)
        _mock_home(monkeypatch, tmp_path)

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

    def test_config_prefers_local_over_global(self, tmp_path, monkeypatch):
        """Test that local config wins when both configs define the same server."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_config = local_dir / "mcp_servers.json"
        global_config = global_dir / ".gaia" / "mcp_servers.json"

        local_dir.mkdir(parents=True)
        global_config.parent.mkdir(parents=True)

        # Both configs define "shared_server" — local value should win.
        local_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "local_server": {"command": "echo", "args": ["local"]},
                        "shared_server": {"command": "echo", "args": ["local-value"]},
                    }
                }
            )
        )
        global_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "global_server": {"command": "echo", "args": ["global"]},
                        "shared_server": {"command": "echo", "args": ["global-value"]},
                    }
                }
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        # Both servers present (stacking), shared_server uses local value.
        assert config.server_exists("local_server")
        assert config.server_exists("global_server")
        assert config.get_server("shared_server")["args"] == ["local-value"]
        # Save target is the local file.
        assert config.config_file == local_config

    def test_config_falls_back_to_global_when_no_local(self, tmp_path, monkeypatch):
        """Test that global config is loaded when no local config exists."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        global_config = global_dir / ".gaia" / "mcp_servers.json"

        local_dir.mkdir(parents=True)
        global_config.parent.mkdir(parents=True)

        global_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "global_server": {"command": "echo", "args": ["global"]}
                    }
                }
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.server_exists("global_server")
        assert config.config_file == global_config

    # ---------------------------------------------------------------------------
    # Config stacking tests
    # ---------------------------------------------------------------------------

    def test_config_stacking_merges_global_and_local(self, tmp_path, monkeypatch):
        """Both global and local configs are loaded and merged."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        (local_dir / "mcp_servers.json").parent.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)

        (local_dir / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"local_server": {"command": "echo", "args": ["l"]}}}
            )
        )
        (global_dir / ".gaia" / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"global_server": {"command": "echo", "args": ["g"]}}}
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.server_exists("local_server")
        assert config.server_exists("global_server")

    def test_config_stacking_local_overrides_global(self, tmp_path, monkeypatch):
        """Local config wins when the same server key appears in both."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_dir.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)

        (local_dir / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"myserver": {"command": "echo", "args": ["local"]}}}
            )
        )
        (global_dir / ".gaia" / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"myserver": {"command": "echo", "args": ["global"]}}}
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.get_server("myserver")["args"] == ["local"]

    def test_config_stacking_global_only(self, tmp_path, monkeypatch):
        """Only global config is loaded when no local config exists."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_dir.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)

        (global_dir / ".gaia" / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"global_server": {"command": "echo", "args": ["g"]}}}
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.server_exists("global_server")
        assert config.config_file == global_dir / ".gaia" / "mcp_servers.json"

    def test_config_stacking_local_only(self, tmp_path, monkeypatch):
        """Only local config is loaded when no global config file exists."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_dir.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)  # dir exists but no file

        (local_dir / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"local_server": {"command": "echo", "args": ["l"]}}}
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.server_exists("local_server")
        assert config.config_file == local_dir / "mcp_servers.json"

    def test_config_stacking_neither_exists(self, tmp_path, monkeypatch):
        """No servers are loaded when neither config file exists."""
        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_dir.mkdir(parents=True)

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.get_servers() == {}
        assert config.config_file == global_dir / ".gaia" / "mcp_servers.json"

    def test_config_explicit_file_no_stacking(self, tmp_path, monkeypatch):
        """When an explicit config_file is given, only that file is loaded."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        explicit_dir = tmp_path / "explicit"
        local_dir.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)
        explicit_dir.mkdir(parents=True)

        explicit_config = explicit_dir / "my_config.json"
        explicit_config.write_text(
            json.dumps(
                {"mcpServers": {"explicit_server": {"command": "echo", "args": ["e"]}}}
            )
        )
        (local_dir / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"local_server": {"command": "echo", "args": ["l"]}}}
            )
        )
        (global_dir / ".gaia" / "mcp_servers.json").write_text(
            json.dumps(
                {"mcpServers": {"global_server": {"command": "echo", "args": ["g"]}}}
            )
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig(config_file=str(explicit_config))

        # Only the explicit file is loaded; local and global are ignored.
        assert config.server_exists("explicit_server")
        assert not config.server_exists("local_server")
        assert not config.server_exists("global_server")
        assert config.config_file == explicit_config
        assert config.load_report["mode"] == "explicit"
        assert config.load_report["servers"] == ["explicit_server"]

    def test_load_report_auto_with_overrides(self, tmp_path, monkeypatch):
        """load_report captures overrides when the same server key appears in both."""
        import json

        local_dir = tmp_path / "local"
        global_dir = tmp_path / "global"
        local_dir.mkdir(parents=True)
        (global_dir / ".gaia").mkdir(parents=True)

        (local_dir / "mcp_servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "echo", "args": ["l"]}}})
        )
        (global_dir / ".gaia" / "mcp_servers.json").write_text(
            json.dumps({"mcpServers": {"shared": {"command": "echo", "args": ["g"]}}})
        )

        monkeypatch.chdir(local_dir)
        _mock_home(monkeypatch, global_dir)

        config = MCPConfig()

        assert config.load_report["mode"] == "auto"
        assert config.load_report["overrides"] == ["shared"]
        assert config.load_report["global"]["servers"] == ["shared"]
        assert config.load_report["local"]["servers"] == ["shared"]


class TestMCPClientManagerStatusReport:
    """Tests for MCPClientManager._failed tracking and get_status_report()."""

    def test_get_status_report_empty_when_no_servers(self):
        manager = MCPClientManager()
        assert manager.get_status_report() == []

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_get_status_report_connected_server(self, mock_client_class):
        mock_client = Mock()
        mock_client.connect.return_value = True
        mock_client.list_tools.return_value = [Mock(), Mock(), Mock()]
        mock_client_class.from_config.return_value = mock_client

        manager = MCPClientManager()
        manager.add_server("github", {"command": "npx", "args": ["-y", "server"]})

        report = manager.get_status_report()
        assert len(report) == 1
        assert report[0]["name"] == "github"
        assert report[0]["connected"] is True
        assert report[0]["tool_count"] == 3
        assert report[0]["error"] is None

    def test_failed_servers_tracked_during_load_from_config(self):
        manager = MCPClientManager()

        def _fail_connect(name, cfg, debug=False):
            m = Mock()
            m.connect.return_value = False
            m.last_error = "Connection refused"
            return m

        with patch("gaia.mcp.client.mcp_client_manager.MCPClient") as mock_cls:
            mock_cls.from_config.side_effect = _fail_connect
            manager.config._servers = {
                "bad_server": {"command": "npx", "args": ["-y", "server"]}
            }
            manager.load_from_config()

        assert "bad_server" in manager._failed
        assert manager._failed["bad_server"] == "Connection refused"

    def test_get_status_report_includes_failed_servers(self):
        manager = MCPClientManager()

        def _fail_connect(name, cfg, debug=False):
            m = Mock()
            m.connect.return_value = False
            m.last_error = "Timeout"
            return m

        with patch("gaia.mcp.client.mcp_client_manager.MCPClient") as mock_cls:
            mock_cls.from_config.side_effect = _fail_connect
            manager.config._servers = {
                "broken": {"command": "npx", "args": ["-y", "server"]}
            }
            manager.load_from_config()

        report = manager.get_status_report()
        assert len(report) == 1
        assert report[0]["name"] == "broken"
        assert report[0]["connected"] is False
        assert report[0]["tool_count"] == 0
        assert report[0]["error"] == "Timeout"

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_get_status_report_mixed_connected_and_failed(self, mock_client_class):
        ok_client = Mock()
        ok_client.connect.return_value = True
        ok_client.list_tools.return_value = [Mock()]

        fail_client = Mock()
        fail_client.connect.return_value = False
        fail_client.last_error = "Refused"

        def side_effect(name, cfg, debug=False):
            if name == "ok":
                return ok_client
            return fail_client

        mock_client_class.from_config.side_effect = side_effect

        manager = MCPClientManager()
        manager.config._servers = {
            "ok": {"command": "npx", "args": ["-y", "ok"]},
            "broken": {"command": "npx", "args": ["-y", "broken"]},
        }
        manager.load_from_config()

        report = manager.get_status_report()
        assert len(report) == 2
        connected = {r["name"]: r for r in report}
        assert connected["ok"]["connected"] is True
        assert connected["ok"]["tool_count"] == 1
        assert connected["broken"]["connected"] is False
        assert connected["broken"]["error"] == "Refused"

    @patch("gaia.mcp.client.mcp_client_manager.MCPClient")
    def test_failed_cleared_on_successful_reconnect(self, mock_client_class):
        """A server that was previously failed should be removed from _failed on success."""
        fail_client = Mock()
        fail_client.connect.return_value = False
        fail_client.last_error = "Refused"

        ok_client = Mock()
        ok_client.connect.return_value = True
        ok_client.list_tools.return_value = []

        mock_client_class.from_config.side_effect = [fail_client, ok_client]

        manager = MCPClientManager()
        manager.config._servers = {"s": {"command": "npx", "args": []}}
        manager.load_from_config()
        assert "s" in manager._failed

        # Simulate successful reconnect via load_from_config again
        # disconnect_all() clears _clients so load_from_config will retry
        manager.disconnect_all()
        manager.config._servers = {"s": {"command": "npx", "args": []}}
        manager.load_from_config()

        assert "s" not in manager._failed
        assert "s" in manager._clients
