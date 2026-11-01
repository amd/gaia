# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia mcp code-index`` over real stdio, driven by GAIA's own MCP client (#869).

No Lemonade here, so nothing is embedded: these tests cover the launch, the
advertised tool schemas, and the errors a client sees before an index exists.
Ranking with real embeddings is the CodeIndexSDK's own test suite.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp.server", reason="mcp SDK not installed ([mcp] extra)")

import gaia  # noqa: E402
from gaia.mcp.client.mcp_client import MCPClient  # noqa: E402
from gaia.mcp.client.transports.stdio import StdioTransport  # noqa: E402

SRC = str(Path(gaia.__file__).resolve().parents[1])


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    repo = tmp_path_factory.mktemp("code_index") / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text(
        "def refresh_token(user):\n    return user.token\n", encoding="utf-8"
    )
    return repo


def _client(tmp_path, *argv, **extra_env) -> MCPClient:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        **extra_env,
        # The child must import this checkout, not whichever one is installed.
        "PYTHONPATH": SRC,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GAIA_MEMORY_DISABLED": "1",
    }
    transport = StdioTransport(
        sys.executable, args=list(argv), env=env, timeout=60, name="code-index"
    )
    return MCPClient("code-index", transport)


@pytest.fixture(scope="module")
def client(repo):
    # One server for the read-only tests; each launch imports all of gaia.
    client = _client(
        repo.parent, "-m", "gaia.mcp.servers.code_index_mcp", "--repo", str(repo)
    )
    assert client.connect(), client.last_error
    yield client
    client.disconnect()


def test_advertises_the_three_tools_with_typed_schemas(client):
    tools = {t.name: t.input_schema for t in client.list_tools()}

    assert set(tools) == {"search_code", "get_index_status", "rebuild_index"}
    search = tools["search_code"]
    assert search["required"] == ["query"]
    assert search["properties"]["query"]["type"] == "string"
    assert search["properties"]["top_k"]["type"] == "integer"
    assert search["properties"]["top_k"]["default"] == 10
    assert search["properties"]["scope"]["default"] == "all"
    assert not tools["get_index_status"].get("required")
    assert set(tools["rebuild_index"]["properties"]) == {"max_files", "model"}
    assert not tools["rebuild_index"].get("required")


def test_status_from_a_cold_cache_reports_no_index(client, repo):
    result = client.call_tool("get_index_status", {})

    assert not result.get("isError")
    status = json.loads(result["content"][0]["text"])
    assert status["indexed"] is False
    assert status["rebuild_running"] is False
    assert Path(status["repo_path"]) == repo.resolve()


def test_search_before_any_index_is_an_error_naming_the_fix(client):
    result = client.call_tool("search_code", {"query": "where are tokens refreshed"})

    assert result.get("isError") is True
    assert "rebuild_index" in result["content"][0]["text"]


@pytest.mark.parametrize(
    "tool,args,needle",
    [
        ("search_code", {"query": "x", "top_k": 0}, "top_k"),
        ("search_code", {"query": "x", "scope": "docs"}, "scope"),
        ("rebuild_index", {"max_files": 0}, "max_files"),
    ],
)
def test_bad_arguments_come_back_as_tool_errors(client, tool, args, needle):
    result = client.call_tool(tool, args)

    assert result.get("isError") is True
    assert needle in result["content"][0]["text"]
    # The server is still up for the next call.
    assert not client.call_tool("get_index_status", {}).get("isError")


def test_rebuild_with_lemonade_down_says_why(tmp_path, repo):
    pytest.importorskip("faiss", reason="faiss not installed ([rag] extra)")
    client = _client(
        tmp_path,
        "-m",
        "gaia.mcp.servers.code_index_mcp",
        "--repo",
        str(repo),
        # Nothing listens on the discard port, so the embedder can't connect.
        LEMONADE_BASE_URL="http://127.0.0.1:9/api/v1",
    )
    try:
        assert client.connect(), client.last_error
        result = client.call_tool("rebuild_index", {})
    finally:
        client.disconnect()

    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "Verify Lemonade Server is running" in text
    assert "port=9" in text


def test_cli_entry_point_serves_the_same_tools(tmp_path, repo):
    client = _client(
        tmp_path,
        "-c",
        "import sys; from gaia.cli import main; sys.exit(main())",
        "mcp",
        "code-index",
        "--repo",
        str(repo),
    )
    try:
        assert client.connect(), client.last_error
        names = {t.name for t in client.list_tools()}
    finally:
        client.disconnect()

    assert names == {"search_code", "get_index_status", "rebuild_index"}


def test_home_directory_repo_fails_at_launch(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    client = _client(
        tmp_path, "-m", "gaia.mcp.servers.code_index_mcp", "--repo", str(home)
    )

    assert client.connect() is False
    assert "home directory" in client.last_error


def test_missing_repo_fails_at_launch(tmp_path):
    client = _client(
        tmp_path,
        "-m",
        "gaia.mcp.servers.code_index_mcp",
        "--repo",
        str(tmp_path / "nope"),
    )

    assert client.connect() is False
    assert "does not exist" in client.last_error


@pytest.mark.skipif(os.name != "nt", reason="Windows path spelling")
def test_repo_given_with_forward_slashes_on_windows(tmp_path, repo):
    client = _client(
        tmp_path,
        "-m",
        "gaia.mcp.servers.code_index_mcp",
        "--repo",
        repo.as_posix(),
    )
    try:
        assert client.connect(), client.last_error
    finally:
        client.disconnect()
