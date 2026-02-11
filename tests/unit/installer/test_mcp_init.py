# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for MCP init command."""

import json
from pathlib import Path


class TestMCPInitCommand:
    """Tests for MCPInitCommand class."""

    def test_creates_gaia_directory(self, tmp_path, monkeypatch):
        """Test that MCPInitCommand creates ~/.gaia/ directory."""
        from gaia.installer.mcp_init import MCPInitCommand

        # Mock Path.home() to use tmp_path
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cmd = MCPInitCommand(yes=True)
        exit_code = cmd.run()

        assert exit_code == 0
        assert (tmp_path / ".gaia").exists()
        assert (tmp_path / ".gaia").is_dir()

    def test_creates_mcp_servers_json(self, tmp_path, monkeypatch):
        """Test that MCPInitCommand creates mcp_servers.json file."""
        from gaia.installer.mcp_init import MCPInitCommand

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cmd = MCPInitCommand(yes=True)
        exit_code = cmd.run()

        config_path = tmp_path / ".gaia" / "mcp_servers.json"
        assert exit_code == 0
        assert config_path.exists()
        assert config_path.is_file()

    def test_config_file_has_correct_content(self, tmp_path, monkeypatch):
        """Test that mcp_servers.json contains {"mcpServers": {}}."""
        from gaia.installer.mcp_init import MCPInitCommand

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cmd = MCPInitCommand(yes=True)
        cmd.run()

        config_path = tmp_path / ".gaia" / "mcp_servers.json"
        with open(config_path) as f:
            config = json.load(f)

        assert config == {"mcpServers": {}}

    def test_idempotency_does_not_overwrite_existing_config(
        self, tmp_path, monkeypatch
    ):
        """Test that running twice doesn't overwrite existing config."""
        from gaia.installer.mcp_init import MCPInitCommand

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create directory and config with existing data
        gaia_dir = tmp_path / ".gaia"
        gaia_dir.mkdir()
        config_path = gaia_dir / "mcp_servers.json"
        existing_config = {
            "mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}
        }
        with open(config_path, "w") as f:
            json.dump(existing_config, f)

        # Run the init command
        cmd = MCPInitCommand(yes=True)
        exit_code = cmd.run()

        # Verify existing config is preserved
        assert exit_code == 0
        with open(config_path) as f:
            config = json.load(f)
        assert config == existing_config

    def test_idempotency_directory_already_exists(self, tmp_path, monkeypatch):
        """Test that init succeeds when ~/.gaia/ already exists."""
        from gaia.installer.mcp_init import MCPInitCommand

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Pre-create the directory
        (tmp_path / ".gaia").mkdir()

        cmd = MCPInitCommand(yes=True)
        exit_code = cmd.run()

        assert exit_code == 0
        assert (tmp_path / ".gaia" / "mcp_servers.json").exists()


class TestRunMCPInit:
    """Tests for run_mcp_init function."""

    def test_run_mcp_init_returns_zero_on_success(self, tmp_path, monkeypatch):
        """Test that run_mcp_init returns 0 on success."""
        from gaia.installer.mcp_init import run_mcp_init

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        exit_code = run_mcp_init(yes=True, verbose=False)

        assert exit_code == 0

    def test_run_mcp_init_creates_config(self, tmp_path, monkeypatch):
        """Test that run_mcp_init creates the config file."""
        from gaia.installer.mcp_init import run_mcp_init

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        run_mcp_init(yes=True, verbose=False)

        assert (tmp_path / ".gaia" / "mcp_servers.json").exists()
