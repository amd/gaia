# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2643 levers 1+2 — metadata-only fetch and batched fetches on
``LiveGmailBackend``.

Lever 1: ``get_message(..., format="metadata")`` requests Gmail's
``format=metadata`` (headers + labelIds + snippet, no body) instead of
``format=full`` (the whole MIME body) so the heuristic pass never pays for a
body it doesn't read. The default (``format`` omitted, or ``"full"``) is
byte-identical to the pre-#2643 call — every other read path keeps working
unmodified.

Lever 2: ``get_messages_batch(ids, format=...)`` turns N sequential
``get_message`` round-trips into a single Gmail batch HTTP request
(multipart/mixed, up to 100 subrequests) — these tests build a spec-accurate
batch response by hand (per Google's documented batch wire format) and verify
the parser reconstructs the exact ``{id: message}`` map, correlating by each
part's own ``Content-ID`` rather than assuming response order matches request
order.
"""

from __future__ import annotations

import json

import httpx
import pytest

from gaia_agent_email.gmail_backend import LiveGmailBackend, METADATA_SCAN_HEADERS

from gaia.connectors.errors import ConnectorsError


def _client_recording(handler):
    calls = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(_wrapped)), calls


def _backend(handler):
    client, calls = _client_recording(handler)
    return LiveGmailBackend(access_token_fn=lambda: "TOKEN", http_client=client), calls


# ---------------------------------------------------------------------------
# Lever 1 — format-aware get_message
# ---------------------------------------------------------------------------


class TestGetMessageFormat:
    def test_default_format_is_full_unchanged(self):
        backend, calls = _backend(lambda r: httpx.Response(200, json={"id": "m1"}))
        backend.get_message("m1")
        assert len(calls) == 1
        assert calls[0].url.params.get("format") == "full"
        assert "metadataHeaders" not in calls[0].url.params

    def test_explicit_full_format_matches_default(self):
        backend, calls = _backend(lambda r: httpx.Response(200, json={"id": "m1"}))
        backend.get_message("m1", format="full")
        assert calls[0].url.params.get("format") == "full"
        assert "metadataHeaders" not in calls[0].url.params

    def test_metadata_format_requests_metadata_and_named_headers(self):
        backend, calls = _backend(lambda r: httpx.Response(200, json={"id": "m1"}))
        backend.get_message("m1", format="metadata")
        assert calls[0].url.params.get("format") == "metadata"
        got_headers = calls[0].url.params.get_list("metadataHeaders")
        assert set(got_headers) == set(METADATA_SCAN_HEADERS)

    def test_metadata_format_hits_the_message_path(self):
        backend, calls = _backend(lambda r: httpx.Response(200, json={"id": "m1"}))
        backend.get_message("m7", format="metadata")
        assert calls[0].url.path.endswith("/messages/m7")


# ---------------------------------------------------------------------------
# Lever 2 — batched fetches
# ---------------------------------------------------------------------------


def _batch_response_part(content_id: str, status: int, body_obj: dict) -> str:
    body_json = json.dumps(body_obj)
    return (
        "Content-Type: application/http\r\n"
        f"Content-ID: <{content_id}>\r\n"
        "\r\n"
        f"HTTP/1.1 {status} OK\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n"
        "\r\n"
        f"{body_json}"
    )


def _batch_response(
    boundary: str, parts: list, *, shuffled: bool = False
) -> httpx.Response:
    """``parts``: list of (content_id, status, body_obj). Builds a spec-shaped
    multipart/mixed response. ``shuffled`` reorders parts in the wire body to
    prove correlation is by Content-ID, never by position."""
    ordered = list(reversed(parts)) if shuffled else parts
    chunks = [f"--{boundary}\r\n{_batch_response_part(*p)}\r\n" for p in ordered]
    text = "".join(chunks) + f"--{boundary}--\r\n"
    return httpx.Response(
        200,
        content=text.encode("utf-8"),
        headers={"Content-Type": f'multipart/mixed; boundary="{boundary}"'},
    )


class TestGetMessagesBatch:
    def test_empty_list_makes_no_call(self):
        backend, calls = _backend(lambda r: httpx.Response(500))
        out = backend.get_messages_batch([])
        assert out == {}
        assert calls == []

    def test_single_id_skips_the_batch_endpoint(self):
        """A batch of 1 is pure multipart-framing overhead -- fall back to a
        plain get_message call instead."""
        backend, calls = _backend(lambda r: httpx.Response(200, json={"id": "m1"}))
        out = backend.get_messages_batch(["m1"], format="metadata")
        assert out == {"m1": {"id": "m1"}}
        assert len(calls) == 1
        assert "/batch/" not in str(calls[0].url)
        assert calls[0].url.params.get("format") == "metadata"

    def test_multiple_ids_hits_batch_endpoint_once(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/batch/gmail/v1" in str(request.url)
            return _batch_response(
                "batch_xyz",
                [
                    ("response-item0", 200, {"id": "m1", "snippet": "one"}),
                    ("response-item1", 200, {"id": "m2", "snippet": "two"}),
                    ("response-item2", 200, {"id": "m3", "snippet": "three"}),
                ],
            )

        backend, calls = _backend(handler)
        out = backend.get_messages_batch(["m1", "m2", "m3"], format="metadata")
        assert len(calls) == 1, "3 ids must cost exactly ONE HTTP round-trip"
        assert out == {
            "m1": {"id": "m1", "snippet": "one"},
            "m2": {"id": "m2", "snippet": "two"},
            "m3": {"id": "m3", "snippet": "three"},
        }

    def test_correlation_is_by_content_id_not_response_order(self):
        """The response part order is shuffled relative to the request order
        -- the map must still come back correct because correlation reads
        each part's own Content-ID, never assumes positional order."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _batch_response(
                "batch_shuf",
                [
                    ("response-item0", 200, {"id": "m1", "tag": "first"}),
                    ("response-item1", 200, {"id": "m2", "tag": "second"}),
                ],
                shuffled=True,
            )

        backend, _calls = _backend(handler)
        out = backend.get_messages_batch(["m1", "m2"], format="full")
        assert out["m1"]["tag"] == "first"
        assert out["m2"]["tag"] == "second"

    def test_batch_request_body_has_one_subrequest_per_id_with_content_ids(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content.decode("utf-8")
            return _batch_response(
                "b1",
                [
                    ("response-item0", 200, {"id": "a"}),
                    ("response-item1", 200, {"id": "b"}),
                ],
            )

        backend, _calls = _backend(handler)
        backend.get_messages_batch(["a", "b"], format="metadata")
        body = captured["body"]
        assert body.count("Content-Type: application/http") == 2
        assert "Content-ID: <item0>" in body
        assert "Content-ID: <item1>" in body
        assert "GET /gmail/v1/users/me/messages/a?" in body
        assert "GET /gmail/v1/users/me/messages/b?" in body
        assert "format=metadata" in body
        for h in METADATA_SCAN_HEADERS:
            assert f"metadataHeaders={h}" in body

    def test_per_item_failure_raises_naming_the_message_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _batch_response(
                "b2",
                [
                    ("response-item0", 200, {"id": "m1"}),
                    ("response-item1", 404, {"error": {"message": "not found"}}),
                ],
            )

        backend, _calls = _backend(handler)
        with pytest.raises(ConnectorsError) as exc:
            backend.get_messages_batch(["m1", "m2"], format="full")
        assert "m2" in str(exc.value)
        assert "404" in str(exc.value)

    def test_missing_response_part_raises_loudly_not_silently_partial(self):
        """One fewer response part than requested must never resolve to a
        partial-but-silently-accepted map."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _batch_response("b3", [("response-item0", 200, {"id": "m1"})])

        backend, _calls = _backend(handler)
        with pytest.raises(ConnectorsError):
            backend.get_messages_batch(["m1", "m2"], format="full")

    def test_more_than_100_ids_are_chunked_into_multiple_batches(self):
        seen_chunk_sizes = []

        def handler(request: httpx.Request) -> httpx.Response:
            n_parts = request.content.count(b"Content-Type: application/http")
            seen_chunk_sizes.append(n_parts)
            boundary = "chunkb"
            parts = [
                (f"response-item{i}", 200, {"echo": i}) for i in range(n_parts)
            ]
            return _batch_response(boundary, parts)

        backend, calls = _backend(handler)
        ids = [f"id{i}" for i in range(150)]
        out = backend.get_messages_batch(ids, format="full")
        assert len(calls) == 2, "150 ids at a 100-cap must cost exactly 2 round-trips"
        assert seen_chunk_sizes == [100, 50], (
            "first chunk must cap at Gmail's 100-subrequest batch limit, "
            f"got {seen_chunk_sizes}"
        )
        assert set(out) == set(ids), "every requested id must resolve, across chunks"
