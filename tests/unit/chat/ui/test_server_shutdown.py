# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the Agent UI server cleans up when it stops.

The ngrok tunnel is a separate process, so a server that exits without
stopping it leaves a public URL open. Indexing tasks write to the chat
database, so they must end before it closes.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from gaia.ui.restart_recovery import INTERRUPTED_INDEXING_ERROR
from gaia.ui.server import DEFAULT_PORT, create_app


def _app_with_fake_tunnel(**kwargs):
    fake = MagicMock()
    fake.stop = AsyncMock()
    with patch("gaia.ui.server.TunnelManager", return_value=fake) as manager:
        app = create_app(db_path=":memory:", **kwargs)
    return app, fake, manager


def test_stopping_the_server_stops_the_tunnel():
    app, fake, _ = _app_with_fake_tunnel()
    with TestClient(app):
        fake.stop.assert_not_awaited()
    fake.stop.assert_awaited_once()


def test_tunnel_forwards_to_the_port_the_server_listens_on():
    _, _, manager = _app_with_fake_tunnel(port=4312)
    manager.assert_called_once_with(port=4312)


def test_tunnel_defaults_to_the_standard_port():
    _, _, manager = _app_with_fake_tunnel()
    manager.assert_called_once_with(port=DEFAULT_PORT)


def test_indexing_ends_before_the_database_closes():
    app, _, _ = _app_with_fake_tunnel()
    seen = {}

    async def indexing():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # What the real indexing task does on cancel: record it in the database.
            try:
                app.state.db.list_sessions()
                seen["db_open"] = True
            except Exception:  # noqa: BLE001 - the probe only records the outcome
                seen["db_open"] = False
            raise

    async def start_indexing():
        app.state.indexing_tasks["doc1"] = asyncio.create_task(indexing())
        await asyncio.sleep(0)

    with patch.object(
        app.state.db,
        "update_document_status",
        wraps=app.state.db.update_document_status,
    ) as update:
        with TestClient(app) as client:
            client.portal.call(start_indexing)

    assert seen == {"db_open": True}
    # Shutdown isn't the user's Cancel: the document keeps its Retry.
    update.assert_any_call("doc1", "failed", last_error=INTERRUPTED_INDEXING_ERROR)
