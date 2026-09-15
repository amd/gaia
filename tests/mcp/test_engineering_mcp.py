# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real stdio requests, including refusal after an already-connected client loses access."""

import json
import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from gaia.engineering.service import EngineeringService
from gaia.mcp.servers.engineering_mcp import create_engineering_mcp


def test_mcp_default_is_disabled(tmp_path):
    with pytest.raises(PermissionError, match="developer mode"):
        create_engineering_mcp(tmp_path, "claude", "anything")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_real_client_roundtrip_and_revocation(tmp_path, backend):
    service = EngineeringService(tmp_path, developer_mode=True)
    connection = service.connection(backend)
    raw = json.loads(open(connection["configuration_file"]).read())

    # Connection file follows each application's native schema; token is private.
    def find_token(value):
        if isinstance(value, dict):
            if "GAIA_ENGINEERING_TOKEN" in value:
                return value["GAIA_ENGINEERING_TOKEN"]
            for nested in value.values():
                result = find_token(nested)
                if result:
                    return result
        return None

    token = find_token(raw)
    assert token
    job = service.share(backend, "Synthetic citation issue", "violet-otter-92")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "gaia.mcp.servers.engineering_mcp",
            "--developer-mode",
            "--root",
            str(tmp_path),
            "--backend",
            backend,
        ],
        env={**os.environ, "GAIA_ENGINEERING_TOKEN": token},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "get_context" in names
            assert names.isdisjoint(
                {"share", "approve_code", "publish", "merge", "execute_shell"}
            )
            result = await session.call_tool("get_context", {"job_id": job["id"]})
            assert not result.is_error
            assert "violet-otter-92" in result.content[0].text
            result = await session.call_tool(
                "prepare_worktree",
                {"job_id": job["id"], "operation_id": "op1", "expected_revision": 1},
            )
            assert result.is_error
            result = await session.call_tool(
                "report_diagnosis",
                {"job_id": job["id"], "report": "Configuration issue; no patch needed"},
            )
            assert not result.is_error
            service.append(job["id"], "second iteration feedback")
            result = await session.call_tool(
                "get_context", {"job_id": job["id"], "after_seq": 1}
            )
            assert "second iteration feedback" in result.content[0].text
            assert "violet-otter-92" not in result.content[0].text
            service.revoke(job["id"])
            result = await session.call_tool("get_context", {"job_id": job["id"]})
            assert result.is_error
            assert "violet-otter-92" not in result.content[0].text
