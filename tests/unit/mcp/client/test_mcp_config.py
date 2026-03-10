# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCPConfig stacking and local config detection."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gaia.mcp.client.config import MCPConfig


class TestFindLocalConfig:
    """Tests for MCPConfig._find_local_config()."""

    def test_returns_cwd_path_when_exists(self, tmp_path, monkeypatch):
        """Returns CWD/mcp_servers.json when it exists."""
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text('{"mcpServers": {}}')

        result = MCPConfig._find_local_config()
        assert result == config_file

    def test_returns_script_dir_when_cwd_has_no_config(self, tmp_path, monkeypatch):
        """Falls back to script dir when CWD has no config."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        script_config = script_dir / "mcp_servers.json"
        script_config.write_text('{"mcpServers": {}}')

        fake_main = SimpleNamespace(__file__=str(script_dir / "agent.py"))
        with patch.dict(sys.modules, {"__main__": fake_main}):
            result = MCPConfig._find_local_config()

        assert result == script_config

    def test_returns_none_when_neither_exists(self, tmp_path, monkeypatch):
        """Returns None when neither CWD nor script dir has a config."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        fake_main = SimpleNamespace(__file__=str(tmp_path / "agent.py"))
        with patch.dict(sys.modules, {"__main__": fake_main}):
            result = MCPConfig._find_local_config()

        assert result is None

    def test_returns_none_in_repl_mode(self, tmp_path, monkeypatch):
        """Returns None when __main__ has no __file__ (REPL mode)."""
        monkeypatch.chdir(tmp_path)

        fake_main = SimpleNamespace()  # no __file__ attribute
        with patch.dict(sys.modules, {"__main__": fake_main}):
            result = MCPConfig._find_local_config()

        assert result is None

    def test_cwd_takes_priority_over_script_dir(self, tmp_path, monkeypatch):
        """CWD config takes priority even when script dir also has one."""
        monkeypatch.chdir(tmp_path)
        cwd_config = tmp_path / "mcp_servers.json"
        cwd_config.write_text('{"mcpServers": {"cwd_server": {}}}')

        script_dir = tmp_path / "scripts"
        script_dir.mkdir()
        script_config = script_dir / "mcp_servers.json"
        script_config.write_text('{"mcpServers": {"script_server": {}}}')

        fake_main = SimpleNamespace(__file__=str(script_dir / "agent.py"))
        with patch.dict(sys.modules, {"__main__": fake_main}):
            result = MCPConfig._find_local_config()

        assert result == cwd_config


class TestMCPConfigStacking:
    """Tests for config stacking (global + local merge)."""

    def _write_config(self, path: Path, servers: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"mcpServers": servers}))

    def test_global_and_local_merge(self, tmp_path, monkeypatch):
        """Global and local configs are merged; local wins on conflicts."""
        global_path = tmp_path / ".gaia" / "mcp_servers.json"
        self._write_config(
            global_path,
            {"global_server": {"command": "g"}, "shared": {"command": "global"}},
        )

        local_path = tmp_path / "project" / "mcp_servers.json"
        self._write_config(
            local_path,
            {"local_server": {"command": "l"}, "shared": {"command": "local"}},
        )

        monkeypatch.chdir(local_path.parent)

        with patch("pathlib.Path.home", return_value=tmp_path):
            config = MCPConfig()

        servers = config.get_servers()
        assert "global_server" in servers
        assert "local_server" in servers
        assert servers["shared"]["command"] == "local"  # local wins

    def test_global_only_when_no_local(self, tmp_path, monkeypatch):
        """Only global config loaded when no local config exists."""
        global_path = tmp_path / ".gaia" / "mcp_servers.json"
        self._write_config(global_path, {"global_only": {"command": "g"}})

        cwd = tmp_path / "empty"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        fake_main = SimpleNamespace(__file__=str(cwd / "agent.py"))
        with patch.dict(sys.modules, {"__main__": fake_main}):
            with patch("pathlib.Path.home", return_value=tmp_path):
                config = MCPConfig()

        assert "global_only" in config.get_servers()
        assert config.load_report["local"]["exists"] is False

    def test_local_only_when_no_global(self, tmp_path, monkeypatch):
        """Only local config loaded when no global config exists."""
        local_path = tmp_path / "mcp_servers.json"
        self._write_config(local_path, {"local_only": {"command": "l"}})

        monkeypatch.chdir(tmp_path)

        empty_home = tmp_path / "home"
        empty_home.mkdir()
        with patch("pathlib.Path.home", return_value=empty_home):
            config = MCPConfig()

        assert "local_only" in config.get_servers()
        assert config.load_report["global"]["exists"] is False

    def test_explicit_config_skips_stacking(self, tmp_path):
        """Explicit config_file skips stacking entirely."""
        explicit_path = tmp_path / "explicit.json"
        self._write_config(explicit_path, {"explicit_server": {"command": "e"}})

        config = MCPConfig(config_file=str(explicit_path))

        assert config.load_report["mode"] == "explicit"
        assert "explicit_server" in config.get_servers()

    def test_overrides_reported_correctly(self, tmp_path, monkeypatch):
        """load_report lists servers overridden by local config."""
        global_path = tmp_path / ".gaia" / "mcp_servers.json"
        self._write_config(
            global_path, {"alpha": {"command": "ga"}, "beta": {"command": "gb"}}
        )

        local_path = tmp_path / "project" / "mcp_servers.json"
        self._write_config(local_path, {"alpha": {"command": "la"}})

        monkeypatch.chdir(local_path.parent)

        with patch("pathlib.Path.home", return_value=tmp_path):
            config = MCPConfig()

        assert "alpha" in config.load_report["overrides"]
        assert "beta" not in config.load_report["overrides"]

    def test_load_report_structure_auto_mode(self, tmp_path, monkeypatch):
        """load_report has correct keys in auto mode."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        empty_home = tmp_path / "home"
        empty_home.mkdir()
        fake_main = SimpleNamespace(__file__=str(cwd / "agent.py"))
        with patch.dict(sys.modules, {"__main__": fake_main}):
            with patch("pathlib.Path.home", return_value=empty_home):
                config = MCPConfig()

        report = config.load_report
        assert report["mode"] == "auto"
        assert "global" in report
        assert "local" in report
        assert "overrides" in report
        assert "path" in report["global"]
        assert "exists" in report["global"]
        assert "servers" in report["global"]
        assert "path" in report["local"]
        assert "exists" in report["local"]
        assert "servers" in report["local"]

    def test_script_dir_config_detected_from_non_cwd(self, tmp_path, monkeypatch):
        """Script dir config is used when running from a different CWD."""
        cwd = tmp_path / "repo_root"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        script_dir = tmp_path / "examples"
        script_dir.mkdir()
        script_config = script_dir / "mcp_servers.json"
        script_config.write_text('{"mcpServers": {"time2": {"command": "uvx"}}}')

        empty_home = tmp_path / "home"
        empty_home.mkdir()

        fake_main = SimpleNamespace(
            __file__=str(script_dir / "mcp_config_based_agent.py")
        )
        with patch.dict(sys.modules, {"__main__": fake_main}):
            with patch("pathlib.Path.home", return_value=empty_home):
                config = MCPConfig()

        assert "time2" in config.get_servers()
        assert config.load_report["local"]["exists"] is True
        assert config.load_report["local"]["path"] == script_config
