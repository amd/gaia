# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Installer custom-agent + MCP harness tests.

These tests drive custom-agent installation through the user-facing
``gaia agent import`` command, then create the registered agent from the
installed ``~/.gaia/agents`` directory. The agents avoid Lemonade inference so
the harness remains deterministic on installer runners.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable

import pytest

from gaia.agents.registry import AgentRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def artifact_dir(tmp_path):
    path = Path(os.environ.get("GAIA_INSTALLER_HARNESS_ARTIFACT_DIR", tmp_path))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gaia_cli_command() -> list[str]:
    configured = os.environ.get("GAIA_CLI")
    if configured:
        return [configured]
    installed = shutil.which("gaia")
    if installed:
        return [installed]
    return [sys.executable, "-m", "gaia.cli"]


def _subprocess_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    pythonpath = str(REPO_ROOT / "src")
    if env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = pythonpath
    if extra:
        env.update(extra)
    return env


def _build_agent_bundle(
    bundle_path: Path, agent_ids: Iterable[str], fixture_names: Iterable[str]
) -> None:
    fixture_root = REPO_ROOT / "tests" / "fixtures" / "custom_agents"
    agent_ids = list(agent_ids)
    fixture_names = list(fixture_names)
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "bundle.json",
            json.dumps(
                {
                    "format_version": 1,
                    "exported_at": "2026-05-16T00:00:00Z",
                    "gaia_version": "test",
                    "agent_ids": agent_ids,
                }
            ),
        )
        for agent_id, fixture_name in zip(agent_ids, fixture_names):
            src = fixture_root / fixture_name
            for path in src.rglob("*"):
                if path.is_file():
                    zf.write(path, f"{agent_id}/{path.relative_to(src).as_posix()}")


def _import_bundle(bundle_path: Path, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_gaia_cli_command(), "agent", "import", str(bundle_path), "--yes"],
        cwd=REPO_ROOT,
        env=_subprocess_env(home),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )


def _discover_agent(agent_id: str):
    registry = AgentRegistry()
    registry.discover()
    registration = registry.get(agent_id)
    assert registration is not None
    assert registration.source == "custom_python"
    return registry


def _dummy_mcp_config(log_path: Path) -> dict:
    return {
        "mcpServers": {
            "dummy": {
                "command": sys.executable,
                "args": [str(REPO_ROOT / "tests/fixtures/mcp/dummy_server/server.py")],
                "env": {"GAIA_DUMMY_MCP_LOG": str(log_path)},
            }
        }
    }


def test_custom_agent_dummy_mcp_path_uses_installed_bundle(
    fake_home, tmp_path, artifact_dir
):
    bundle_path = tmp_path / "installer-mcp.zip"
    mcp_log = artifact_dir / "dummy-mcp.jsonl"
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(json.dumps(_dummy_mcp_config(mcp_log)), encoding="utf-8")

    _build_agent_bundle(bundle_path, ["installer-mcp"], ["installer_mcp"])
    result = _import_bundle(bundle_path, fake_home)
    assert "Imported: installer-mcp" in result.stdout

    registry = _discover_agent("installer-mcp")
    agent = registry.create_agent("installer-mcp", mcp_config_file=str(mcp_config))

    try:
        response = agent.process_query("add 7 and 35")
        assert response["status"] == "success"
        assert json.loads(response["data"]["content"][0]["text"]) == {"sum": 42}

        records = [
            json.loads(line)
            for line in mcp_log.read_text(encoding="utf-8").splitlines()
        ]
        assert records == [
            {
                "tool": "add_two_numbers",
                "arguments": {"a": 7, "b": 35},
                "result": {"sum": 42},
            }
        ]
    finally:
        agent._mcp_manager.disconnect_all()


def test_custom_agent_with_mcp_reports_diagnosable_connection_failure(
    fake_home, tmp_path
):
    bundle_path = tmp_path / "installer-mcp.zip"
    mcp_config = tmp_path / "mcp_servers.json"
    mcp_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dummy": {
                        "command": sys.executable,
                        "args": ["-c", "import sys; sys.exit(17)"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    _build_agent_bundle(bundle_path, ["installer-mcp"], ["installer_mcp"])
    _import_bundle(bundle_path, fake_home)

    registry = _discover_agent("installer-mcp")
    agent = registry.create_agent("installer-mcp", mcp_config_file=str(mcp_config))

    assert agent.get_mcp_status_report() == [
        {
            "name": "dummy",
            "connected": False,
            "tool_count": 0,
            "error": (
                "Transport error for 'dummy': "
                "MCP server process died (exit code: 17)"
            ),
        }
    ]
    assert agent.process_query("add 7 and 35") == {
        "status": "error",
        "error": "Tool 'mcp_dummy_add_two_numbers' not found",
    }


def test_custom_agent_no_mcp_path_imports_and_emits_no_mcp_traffic(
    fake_home, tmp_path, artifact_dir
):
    bundle_path = tmp_path / "installer-no-mcp.zip"
    mcp_log = artifact_dir / "dummy-mcp-no-mcp.jsonl"

    _build_agent_bundle(bundle_path, ["installer-no-mcp"], ["installer_no_mcp"])
    result = _import_bundle(bundle_path, fake_home)
    assert "Imported: installer-no-mcp" in result.stdout

    registry = _discover_agent("installer-no-mcp")
    agent = registry.create_agent("installer-no-mcp")

    assert agent.process_query("double 21") == {"doubled": 42}
    assert not mcp_log.exists()
