# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""pytest fixtures for MCP integration tests."""

import sys
from pathlib import Path

import pytest

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "fixture_mcp_server.py"


@pytest.fixture
def fixture_server_config():
    """stdio config for the local fixture MCP server (needs the [mcp] extra)."""
    pytest.importorskip("mcp.server", reason="mcp SDK not installed ([mcp] extra)")
    return {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point ``Path.home()`` and the cwd at an empty dir so no real config leaks in."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(home)
    return home
