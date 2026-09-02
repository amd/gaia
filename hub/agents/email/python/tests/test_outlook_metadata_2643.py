# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 lever 1 — metadata-only fetch on ``LiveOutlookBackend``.

Mirrors ``test_gmail_metadata_batch_2643.py``'s Gmail coverage for the
Outlook/Graph translation: ``get_message(..., format="metadata")`` requests a
``$select`` that excludes the heavy ``body`` field, and the resulting
Gmail-shaped ``payload.body`` carries no ``data`` — so a caller that
accidentally tries to decode a metadata-mode message's body gets nothing
(matching what live Graph would actually return), never a stale/wrong
"complete" body.

Outlook's batched fetch path is covered separately in
``test_outlook_batch_3104.py``. ``get_messages_batch`` remains a duck-typed
capability (see ``gmail_backend.py``), so the read-tools scan loop continues
to fall back to a per-id ``get_message`` loop for any backend lacking it.
"""

from __future__ import annotations

from typing import Callable, List, Tuple

import httpx
import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.outlook_backend import (  # noqa: E402
    _MESSAGE_SELECT,
    _MESSAGE_SELECT_METADATA,
    LiveOutlookBackend,
    graph_message_to_gmail,
)


class _Recorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: List[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _backend(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Tuple[LiveOutlookBackend, _Recorder]:
    rec = _Recorder(handler)
    client = httpx.Client(transport=httpx.MockTransport(rec))
    return LiveOutlookBackend(lambda: "GRAPH-TOKEN-1", http_client=client), rec


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _graph_message(*, include_body: bool = True) -> dict:
    msg = {
        "id": "m1",
        "conversationId": "c1",
        "subject": "Weekly digest",
        "from": {"emailAddress": {"name": "", "address": "news@example.com"}},
        "toRecipients": [],
        "receivedDateTime": "2026-06-01T09:30:00Z",
        "isRead": True,
        "isDraft": False,
        "flag": {"flagStatus": "notFlagged"},
        "bodyPreview": "This week's roundup",
        "categories": [],
        "parentFolderId": "inbox",
    }
    if include_body:
        msg["body"] = {"contentType": "text", "content": "This week's roundup..."}
    return msg


class TestGetMessageMetadataSelect:
    def test_default_format_uses_the_full_select_unchanged(self):
        backend, rec = _backend(lambda r: _ok(_graph_message()))
        backend.get_message("m1")
        select = rec.requests[0].url.params.get("$select")
        assert select == _MESSAGE_SELECT
        assert "body" in select.split(",")

    def test_metadata_format_uses_a_select_without_body(self):
        backend, rec = _backend(lambda r: _ok(_graph_message(include_body=False)))
        backend.get_message("m1", format="metadata")
        select = rec.requests[0].url.params.get("$select")
        assert select == _MESSAGE_SELECT_METADATA
        assert "body" not in select.split(",")
        # Every field the metadata-only heuristic pass needs must survive.
        for field in ("subject", "from", "bodyPreview", "isRead"):
            assert field in select.split(",")

    def test_metadata_result_has_no_body_data(self):
        backend, _rec = _backend(lambda r: _ok(_graph_message(include_body=False)))
        msg = backend.get_message("m1", format="metadata")
        assert "data" not in msg["payload"]["body"]
        assert msg["payload"]["body"]["size"] == 0
        # Snippet (bodyPreview) must still be present -- that's what the
        # heuristic actually reads.
        assert msg["snippet"] == "This week's roundup"

    def test_full_result_still_has_body_data(self):
        backend, _rec = _backend(lambda r: _ok(_graph_message()))
        msg = backend.get_message("m1", format="full")
        assert "data" in msg["payload"]["body"]


class TestGraphMessageToGmailIncludeBody:
    def test_include_body_false_omits_data_key(self):
        out = graph_message_to_gmail(_graph_message(include_body=False), include_body=False)
        assert "data" not in out["payload"]["body"]
        assert out["payload"]["body"]["size"] == 0

    def test_include_body_true_is_the_default_and_unchanged(self):
        msg = _graph_message()
        default_call = graph_message_to_gmail(msg)
        explicit_call = graph_message_to_gmail(msg, include_body=True)
        assert default_call == explicit_call
        assert "data" in default_call["payload"]["body"]
