# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A client that hangs up is not an error in the server's log.

On Windows, quitting the terminal app mid-connection makes asyncio's proactor
transport raise ``ConnectionResetError`` (WinError 10054) inside its own
connection-lost callback. The loop's default handler logs that as an ERROR with
a traceback, though nothing went wrong on this side.
"""

import asyncio
import logging

import pytest

from gaia.client_hangups import quiet_client_hangups


def _report(loop, context):
    loop.call_exception_handler(context)


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError(10054, "An existing connection was forcibly closed"),
        ConnectionAbortedError(10053, "An established connection was aborted"),
        BrokenPipeError(32, "Broken pipe"),
    ],
)
def test_a_client_hanging_up_is_logged_quietly(loop, caplog, error):
    quiet_client_hangups(loop)

    with caplog.at_level(logging.DEBUG):
        _report(
            loop,
            {
                "message": "Exception in callback "
                "_ProactorBasePipeTransport._call_connection_lost(None)",
                "exception": error,
            },
        )

    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "client closed its connection" in caplog.records[0].getMessage()
    assert caplog.records[0].exc_info is None


@pytest.mark.parametrize(
    "context",
    [
        {"message": "Task exception was never retrieved", "exception": ValueError("x")},
        {"message": "Exception in callback job()", "exception": OSError(5, "I/O")},
        {"message": "no exception at all"},
        # GAIA's own outgoing call that dropped: a real failure, not a client.
        {
            "message": "Task exception was never retrieved",
            "exception": ConnectionResetError(10054, "reset"),
            "task": object(),
        },
        {
            "message": "Future exception was never retrieved",
            "exception": BrokenPipeError(32, "Broken pipe"),
            "future": object(),
        },
    ],
)
def test_anything_else_is_still_an_error(loop, caplog, context):
    quiet_client_hangups(loop)

    with caplog.at_level(logging.DEBUG):
        _report(loop, context)

    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert caplog.records[0].name == "asyncio"


def test_a_handler_already_installed_still_gets_other_errors(loop):
    seen = []
    loop.set_exception_handler(lambda _loop, context: seen.append(context))
    quiet_client_hangups(loop)
    boom = {"message": "m", "exception": RuntimeError("boom")}

    _report(loop, boom)
    _report(loop, {"message": "m", "exception": ConnectionResetError(10054, "x")})

    assert seen == [boom]


def test_the_daemon_installs_it_when_it_starts(caplog):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    seen = {}

    def started():
        running = asyncio.get_running_loop()
        with caplog.at_level(logging.DEBUG):
            running.call_exception_handler(
                {"message": "m", "exception": ConnectionResetError(10054, "reset")}
            )
        seen["levels"] = [r.levelno for r in caplog.records]

    app = create_app(token="t", port=1, pid=1, started_at=0.0, on_startup=started)
    with TestClient(app):
        pass

    assert seen["levels"] == [logging.INFO]


def test_the_agent_ui_server_installs_it_when_it_starts(caplog):
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app

    async def report_a_hangup():
        asyncio.get_running_loop().call_exception_handler(
            {"message": "m", "exception": ConnectionResetError(10054, "reset")}
        )

    with TestClient(create_app(db_path=":memory:")) as client:
        with caplog.at_level(logging.DEBUG, logger="gaia.client_hangups"):
            client.portal.call(report_a_hangup)

    hangups = [r for r in caplog.records if r.name == "gaia.client_hangups"]
    assert [r.levelno for r in hangups] == [logging.INFO]
