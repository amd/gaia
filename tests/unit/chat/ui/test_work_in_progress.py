# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the desktop app asks before it quits or frees the model's memory.

``GET /api/system/work-in-progress`` lists answers still being written and
documents still indexing; ``POST /api/system/unload-model`` frees the model
server's memory, and refuses while either is running.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.llm.lemonade_client import LemonadeClientError
from gaia.ui.run_manager import run_manager
from gaia.ui.server import create_app


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_registry():
    run_manager._runs.clear()
    yield
    run_manager._runs.clear()


_BODY = {"model_name": "Gemma-4-E4B-it-GGUF"}


class _FakeRun:
    def __init__(self, session_id):
        self.session_id = session_id
        self.handler = None


def _session(client, title):
    return client.post("/api/sessions", json={"title": title}).json()["id"]


def test_nothing_running(client):
    resp = client.get("/api/system/work-in-progress")
    assert resp.status_code == 200
    assert resp.json() == {"answers": [], "indexing": []}


def test_running_answers_are_named(client):
    sid = _session(client, "Trip plan")
    run_manager._runs[sid] = _FakeRun(sid)
    run_manager._runs["gone"] = _FakeRun("gone")

    answers = client.get("/api/system/work-in-progress").json()["answers"]

    assert {"session_id": sid, "title": "Trip plan"} in answers
    # A run whose chat was deleted mid-answer still counts; it just has no title.
    assert {"session_id": "gone", "title": None} in answers


def test_indexing_lists_uploads_and_monitor_reindexes(app, client):
    app.state.indexing_tasks["doc-1"] = object()
    monitor = MagicMock()
    monitor.reindexing_docs = {"doc-2", "doc-1"}
    app.state.document_monitor = monitor
    with patch("gaia.ui.database.ChatDatabase.get_document") as get_doc:
        get_doc.side_effect = lambda doc_id: (
            {"filename": "report.pdf"} if doc_id == "doc-1" else None
        )
        indexing = client.get("/api/system/work-in-progress").json()["indexing"]
    assert indexing == [
        {"document_id": "doc-1", "filename": "report.pdf"},
        {"document_id": "doc-2", "filename": None},
    ]


def test_unload_needs_the_ui_header_the_desktop_app_sends(app):
    # As the desktop app's main process calls it: no browser Origin, so only
    # the X-Gaia-UI header gets it past the guard. The suite's TestClient adds
    # that header by default, so it is dropped here.
    bare = TestClient(app, raise_server_exceptions=False)
    bare.headers.pop("x-gaia-ui", None)
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        assert bare.post("/api/system/unload-model", json=_BODY).status_code == 403
        resp = bare.post(
            "/api/system/unload-model", json=_BODY, headers={"X-Gaia-UI": "1"}
        )
    assert resp.status_code == 200
    cls.return_value.unload_model.assert_called_once_with(_BODY["model_name"])


def test_unload_frees_the_model_server(client):
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        resp = client.post("/api/system/unload-model", json=_BODY)
    assert resp.status_code == 200
    assert resp.json() == {"status": "unloaded"}
    # Only the chat model the user named: the embedder stays loaded.
    cls.return_value.unload_model.assert_called_once_with("Gemma-4-E4B-it-GGUF")


def test_unload_needs_a_model_name(client):
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        assert client.post("/api/system/unload-model", json={}).status_code == 422
        assert (
            client.post("/api/system/unload-model", json={"model_name": ""}).status_code
            == 422
        )
    cls.return_value.unload_model.assert_not_called()


def test_unload_refused_while_an_answer_runs(client):
    run_manager._runs["s1"] = _FakeRun("s1")
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        resp = client.post("/api/system/unload-model", json=_BODY)
    assert resp.status_code == 409
    assert "still being written" in resp.json()["detail"]
    cls.return_value.unload_model.assert_not_called()


def test_unload_refused_while_a_document_indexes(app, client):
    app.state.indexing_tasks["doc-1"] = object()
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        resp = client.post("/api/system/unload-model", json=_BODY)
    assert resp.status_code == 409
    assert "still being indexed" in resp.json()["detail"]
    cls.return_value.unload_model.assert_not_called()


def test_unload_failure_says_why(client):
    with patch("gaia.llm.lemonade_client.LemonadeClient") as cls:
        cls.return_value.unload_model.side_effect = LemonadeClientError(
            "Lemonade Server not reachable at http://localhost:13305"
        )
        resp = client.post("/api/system/unload-model", json=_BODY)
    assert resp.status_code == 502
    assert resp.json()["detail"] == (
        "The model server did not unload Gemma-4-E4B-it-GGUF: "
        "Lemonade Server not reachable at http://localhost:13305"
    )
