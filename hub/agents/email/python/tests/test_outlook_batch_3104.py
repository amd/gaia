# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for Outlook's Graph ``$batch`` message fetch (#3104)."""

from __future__ import annotations

import json
from typing import Callable, List

import httpx
import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.outlook_backend import (  # noqa: E402
    _BATCH_MAX_SUBREQUESTS,
    _MESSAGE_SELECT,
    _MESSAGE_SELECT_METADATA,
    LiveOutlookBackend,
)
from gaia_agent_email.tools.read_tools import _fetch_messages  # noqa: E402

from gaia.connectors.errors import ConnectorsError  # noqa: E402


class _Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: List[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _backend(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[LiveOutlookBackend, _Recorder]:
    recorder = _Recorder(handler)
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    return LiveOutlookBackend(lambda: "GRAPH-TOKEN-1", http_client=client), recorder


def _graph_message(message_id: str) -> dict:
    return {
        "id": message_id,
        "conversationId": f"conversation-{message_id}",
        "subject": f"Subject {message_id}",
        "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
        "toRecipients": [],
        "receivedDateTime": "2026-06-01T09:30:00Z",
        "isRead": True,
        "isDraft": False,
        "flag": {"flagStatus": "notFlagged"},
        "bodyPreview": f"Preview {message_id}",
        "categories": [],
        "parentFolderId": "inbox",
        "body": {"contentType": "text", "content": f"Body {message_id}"},
    }


def _batch_response(*items: dict) -> httpx.Response:
    return httpx.Response(200, json={"responses": list(items)})


def _item(request_id: str, message: dict, *, status: int = 200) -> dict:
    return {"id": request_id, "status": status, "body": message}


def test_empty_and_single_fetch_keep_existing_request_paths():
    backend, recorder = _backend(
        lambda _: httpx.Response(200, json=_graph_message("m1"))
    )

    assert backend.get_messages_batch([]) == {}
    assert recorder.requests == []

    result = backend.get_messages_batch(["m1"], format="metadata")

    assert result["m1"]["id"] == "m1"
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.path.endswith("/me/messages/m1")
    assert recorder.requests[0].url.params["$select"] == _MESSAGE_SELECT_METADATA


def test_multiple_messages_use_one_graph_batch_and_correlate_by_response_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/$batch")
        assert request.headers["authorization"] == "Bearer GRAPH-TOKEN-1"
        assert request.headers["content-type"] == "application/json"
        captured["payload"] = json.loads(request.content)
        return _batch_response(
            _item("1", _graph_message("m2")),
            _item("0", _graph_message("m/1")),
        )

    backend, recorder = _backend(handler)
    result = backend.get_messages_batch(["m/1", "m2"])

    assert len(recorder.requests) == 1
    requests = captured["payload"]["requests"]
    assert [request["id"] for request in requests] == ["0", "1"]
    assert [request["method"] for request in requests] == ["GET", "GET"]
    assert "/me/messages/m%2F1?$select=" in requests[0]["url"]
    assert requests[0]["url"].endswith(_MESSAGE_SELECT)
    assert result["m/1"]["id"] == "m/1"
    assert result["m2"]["id"] == "m2"


def test_metadata_batch_omits_body_from_graph_select_and_translation():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _batch_response(
            _item("0", _graph_message("m1")),
            _item("1", _graph_message("m2")),
        )

    backend, _recorder = _backend(handler)
    result = backend.get_messages_batch(["m1", "m2"], format="metadata")

    requests = captured["payload"]["requests"]
    assert all(
        request["url"].endswith(_MESSAGE_SELECT_METADATA) for request in requests
    )
    assert all("body," not in request["url"] for request in requests)
    assert "data" not in result["m1"]["payload"]["body"]


def test_read_tools_uses_outlook_batch_capability():
    def handler(request: httpx.Request) -> httpx.Response:
        requests = json.loads(request.content)["requests"]
        return _batch_response(
            *(
                _item(item["id"], _graph_message(f"message-{item['id']}"))
                for item in requests
            )
        )

    backend, recorder = _backend(handler)
    fetched, dropped = _fetch_messages(backend, ["m1", "m2"], format="metadata")

    assert dropped == []
    assert set(fetched) == {"m1", "m2"}
    assert len(recorder.requests) == 1
    assert json.loads(recorder.requests[0].content)["requests"][0]["url"].endswith(
        _MESSAGE_SELECT_METADATA
    )


def test_batch_requests_are_chunked_at_graph_limit():
    ids = [f"m{index}" for index in range(_BATCH_MAX_SUBREQUESTS + 1)]
    chunk_sizes: List[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests = json.loads(request.content)["requests"]
        chunk_sizes.append(len(requests))
        return _batch_response(
            *(_item(item["id"], _graph_message("response")) for item in requests)
        )

    backend, recorder = _backend(handler)
    result = backend.get_messages_batch(ids)

    assert len(recorder.requests) == 2
    assert chunk_sizes == [_BATCH_MAX_SUBREQUESTS, 1]
    assert set(result) == set(ids)


def test_partial_or_failed_batch_response_raises_loudly():
    def missing_handler(request: httpx.Request) -> httpx.Response:
        return _batch_response(_item("0", _graph_message("m1")))

    backend, _recorder = _backend(missing_handler)
    with pytest.raises(ConnectorsError, match="missing"):
        backend.get_messages_batch(["m1", "m2"])

    def failed_handler(request: httpx.Request) -> httpx.Response:
        return _batch_response(
            _item("0", _graph_message("m1")),
            _item("1", {"error": {"message": "not found"}}, status=404),
        )

    backend, _recorder = _backend(failed_handler)
    with pytest.raises(ConnectorsError, match="m2.*404"):
        backend.get_messages_batch(["m1", "m2"])
