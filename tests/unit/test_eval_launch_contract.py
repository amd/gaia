# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pin credential selection and the real MCP launcher used by agent evals."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.eval import runner


@pytest.mark.parametrize("api_key", [None, "", "test-key"])
@pytest.mark.parametrize("oauth_token", [None, "test-oauth"])
def test_eval_launch_preserves_auth_and_mcp_contract(
    tmp_path, monkeypatch, mocker, api_key, oauth_token
):
    for name, value in (
        ("ANTHROPIC_API_KEY", api_key),
        ("CLAUDE_CODE_OAUTH_TOKEN", oauth_token),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    mocker.patch.object(runner.shutil, "which", return_value="/test/claude")
    run = mocker.patch.object(
        runner.subprocess,
        "run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "structured_output": {
                        "scenario_id": "launch",
                        "status": "PASS",
                        "turns": [],
                        "overall_score": 10,
                    }
                }
            ),
            stderr="",
        ),
    )
    scenario = {"id": "launch", "category": "tool_selection", "setup": {}, "turns": []}
    result = runner.run_scenario_subprocess(
        tmp_path / "scenario.yaml",
        scenario,
        tmp_path,
        "http://localhost:4200",
        "test-model",
        "1.00",
        30,
    )
    assert result["status"] == "PASS"
    command = run.call_args.args[0]
    assert command[:2] == ["/test/claude", "-p"]
    assert ("--bare" in command) == bool(api_key)
    assert "--strict-mcp-config" in command
    # The subprocess gets the *resolved* copy, never the tracked template.
    mcp_config = Path(command[command.index("--mcp-config") + 1])
    assert mcp_config != runner.MCP_CONFIG
    assert mcp_config.is_file()
    resolved = json.loads(mcp_config.read_text(encoding="utf-8"))
    assert resolved["mcpServers"]["gaia-agent-ui"]["command"] == sys.executable
    assert runner.MCP_CONFIG.is_file()
    assert run.call_args.kwargs["cwd"] == str(runner.REPO_ROOT)
    assert run.call_args.kwargs["timeout"] == 30
    assert "env" not in run.call_args.kwargs  # Inherit the selected credentials.


def test_configured_mcp_launcher_imports_real_server(tmp_path, monkeypatch):
    config = json.loads(runner.MCP_CONFIG.read_text(encoding="utf-8"))
    server = config["mcpServers"]["gaia-agent-ui"]
    launcher = runner.REPO_ROOT / server["args"][0]
    assert launcher.is_file()
    assert "--stdio" in server["args"]
    monkeypatch.setenv("GAIA_MCP_LOG_DIR", str(tmp_path))
    # The command the runner will actually spawn — the template names a generic
    # interpreter, and the runner resolves it before handing the config to
    # `claude -p`. Asserting on the raw template would fail on every host
    # without a bare `python` (macOS, most Linux distros) — see #3981.
    command = runner._resolve_mcp_command(server["command"])
    assert shutil.which(command) or Path(command).is_file(), (
        f"the eval's MCP config spawns {command!r}, which is not executable — "
        "the eval would fail at spawn time"
    )
    result = subprocess.run(
        [command, str(launcher), *server["args"][1:], "--help"],
        cwd=runner.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--stdio" in result.stdout
    assert "GAIA Agent UI MCP Server" in result.stdout
