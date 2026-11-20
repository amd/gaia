# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The desktop app tells the backend when its window is out of sight.

``POST /api/system/window-visibility`` passes that to the daemon, which gives
the models' memory back once the app has sat hidden and unused. Only the
desktop app at this PC may say so: a phone over mobile access must not be able
to make GAIA unload its models, nor keep them loaded.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

ROUTE = "/api/system/window-visibility"
UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


@pytest.fixture
def at_this_pc(app):
    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


@pytest.fixture
def said():
    with patch("gaia.llm.lemonade_service.set_window_hidden") as said:
        yield said


@pytest.mark.parametrize("hidden", [True, False])
def test_the_window_state_reaches_the_lease(at_this_pc, said, hidden):
    resp = at_this_pc.post(ROUTE, json={"hidden": hidden}, headers=UI)

    assert resp.status_code == 200
    assert resp.json() == {"hidden": hidden}
    said.assert_called_once_with(hidden)


@pytest.mark.parametrize("body", [{}, {"hidden": "sometimes"}, {"hidden": None}])
def test_a_body_that_does_not_say_is_refused(at_this_pc, said, body):
    resp = at_this_pc.post(ROUTE, json=body, headers=UI)

    assert resp.status_code == 422
    said.assert_not_called()


def test_a_request_without_the_ui_header_is_refused(at_this_pc, said):
    # tests/conftest.py gives every TestClient the header; this one goes without.
    at_this_pc.headers.pop("x-gaia-ui")

    resp = at_this_pc.post(ROUTE, json={"hidden": True})

    assert resp.status_code == 403
    said.assert_not_called()


def test_mobile_access_cannot_report_the_window(at_this_pc, said):
    """The tunnel connects from loopback; its forwarded headers give it away."""
    resp = at_this_pc.post(
        ROUTE,
        json={"hidden": True},
        headers={**UI, "X-Forwarded-For": "203.0.113.7"},
    )

    assert resp.status_code == 403
    said.assert_not_called()


def test_another_computer_cannot_report_the_window(app, said):
    remote = TestClient(app, client=("192.168.1.20", 50000))

    resp = remote.post(ROUTE, json={"hidden": True}, headers=UI)

    assert resp.status_code == 403
    said.assert_not_called()
