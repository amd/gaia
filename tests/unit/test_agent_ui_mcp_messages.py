# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP transcript retrieval includes every page and reports partial failures."""

import asyncio
import json
import socket
import threading
import time
from unittest.mock import patch

import pytest

from gaia.mcp.servers.agent_ui_mcp import create_agent_ui_mcp

pytest.importorskip("mcp")  # This server requires the optional MCP extra.


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_MCP_ALWAYS", "1")
    return create_agent_ui_mcp("http://backend")


def row(number):
    return {"role": "user", "content": f"message-{number}"}


def test_registered_tool_retrieves_all_pages_and_keeps_summaries(server):
    rows = [row(n) for n in range(205)]
    rows[-1].update(
        content="z" * 2100,
        agent_steps=[{"type": "tool", "tool": "read_file", "result": "x" * 400}],
        stats={"tokens": 7},
    )

    def page(_backend, _method, _path, params=None):
        params = params or {"offset": 0, "limit": 100}
        start = params["offset"]
        return {"messages": rows[start : start + params["limit"]], "total": 205}

    with patch("gaia.mcp.servers.agent_ui_mcp._api", side_effect=page) as api:
        result = server._tool_manager._tools["get_messages"].fn("session")
    assert len(result["messages"]) == result["total"] == 205
    assert [call.kwargs["params"] for call in api.call_args_list] == [
        {"limit": 100, "offset": 0},
        {"limit": 100, "offset": 100},
        {"limit": 5, "offset": 200},
    ]
    assert result["messages"][-1]["content"] == "z" * 2000
    assert result["messages"][-1]["agent_steps"][0]["result"] == "x" * 300
    assert result["messages"][-1]["stats"] == {"tokens": 7}


@pytest.mark.parametrize(
    "later", [{"status": "error", "detail": "HTTP 500"}, {"messages": [], "total": 101}]
)
def test_later_page_failure_is_not_a_partial_success(server, later):
    with patch(
        "gaia.mcp.servers.agent_ui_mcp._api",
        side_effect=[{"messages": [row(n) for n in range(100)], "total": 101}, later],
    ):
        result = server._tool_manager._tools["get_messages"].fn("session")
    assert result["status"] == "error"
    assert result["detail"]
    assert "messages" not in result


def test_growing_total_is_bounded_by_first_page(server):
    with patch(
        "gaia.mcp.servers.agent_ui_mcp._api",
        side_effect=[
            {"messages": [row(n) for n in range(100)], "total": 101},
            {"messages": [row(100), row(101)], "total": 102},
        ],
    ) as api:
        result = server._tool_manager._tools["get_messages"].fn("session")
    assert len(result["messages"]) == result["total"] == 101
    assert api.call_count == 2


@pytest.mark.parametrize("total", [-1, "unknown"])
def test_invalid_initial_total_is_an_explicit_error(server, total):
    with patch(
        "gaia.mcp.servers.agent_ui_mcp._api",
        return_value={"messages": [], "total": total},
    ):
        result = server._tool_manager._tools["get_messages"].fn("session")
    assert result["status"] == "error"
    assert "Invalid message total" in result["detail"]


@pytest.mark.parametrize("count", [0, 3, 205])
@pytest.mark.allow_network
def test_mcp_client_gets_transcript_through_live_http_route(
    tmp_path, monkeypatch, count
):
    """A real MCP tools/call reaches the existing HTTP route and SQLite rows."""
    import anyio
    import uvicorn
    from fastapi import FastAPI
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from gaia.ui.database import ChatDatabase
    from gaia.ui.dependencies import get_db
    from gaia.ui.routers.sessions import router

    monkeypatch.setenv("GAIA_MEMORY_MCP_ALWAYS", "1")
    db = ChatDatabase(str(tmp_path / "chat.db"))
    session_id = db.create_session(title="MCP pagination")["id"]
    for n in range(count):
        db.add_message(session_id, "user", f"message-{n}")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    requested_pages = []

    @app.middleware("http")
    async def capture_pages(request, call_next):
        requested_pages.append(dict(request.query_params))
        return await call_next(request)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    backend = f"http://127.0.0.1:{listener.getsockname()[1]}"
    http = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    worker = threading.Thread(
        target=http.run, kwargs={"sockets": [listener]}, daemon=True
    )
    worker.start()

    async def call_mcp():
        mcp = create_agent_ui_mcp(backend)
        lowlevel = mcp._lowlevel_server
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            async with anyio.create_task_group() as group:
                group.start_soon(
                    lowlevel.run,
                    *server_streams,
                    lowlevel.create_initialization_options(),
                )
                async with ClientSession(*client_streams) as client:
                    await client.initialize()
                    result = await client.call_tool(
                        "get_messages", {"session_id": session_id}
                    )
                    assert not result.is_error
                    payload = json.loads(result.content[0].text)
                group.cancel_scope.cancel()
        return payload

    try:
        deadline = time.monotonic() + 5
        while not http.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert http.started, "HTTP test backend did not start"
        result = asyncio.run(asyncio.wait_for(call_mcp(), timeout=10))
        assert result == {"messages": [row(n) for n in range(count)], "total": count}
        assert [int(page.get("offset", 0)) for page in requested_pages] == list(
            range(0, max(count, 1), 100)
        )
    finally:
        http.should_exit = True
        worker.join(5)
        listener.close()
        db.close()
        assert not worker.is_alive(), "HTTP test backend did not shut down"
