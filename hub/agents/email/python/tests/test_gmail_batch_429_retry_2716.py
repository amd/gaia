# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Gmail 429 (per-user concurrency limit) retry/degrade tests (#2720, #2716).

A single embedded sub-request 429 inside a Gmail batch response used to
raise ``ConnectorsError`` and discard the other 99 already-successful
results, killing the whole scan (#2720). Root cause: no 429 handling
anywhere in ``gmail_backend.py``, and a 100-subrequest batch is itself
oversized enough to reliably trigger Gmail's per-user concurrency limit.

This file covers the wire-level fix directly against ``LiveGmailBackend``
(``httpx.MockTransport``, hand-built multipart/mixed bodies) rather than the
``FakeGmailBackend`` eval fixture, which has no 429 concept and never routes
through the live HTTP code being fixed here.
"""

from __future__ import annotations

import json

import httpx
import pytest
from gaia_agent_email.gmail_backend import (
    _BATCH_MAX_SUBREQUESTS,
    _RATE_LIMIT_MAX_ATTEMPTS,
    _RATE_LIMIT_MAX_BACKOFF_SECONDS,
    LiveGmailBackend,
)

from gaia.connectors.errors import ConnectorsError, RateLimitedError


def _client_recording(handler):
    calls = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(_wrapped)), calls


def _backend(handler):
    client, calls = _client_recording(handler)
    return LiveGmailBackend(access_token_fn=lambda: "TOKEN", http_client=client), calls


def _batch_response_part(content_id: str, status: int, body_obj: dict) -> str:
    body_json = json.dumps(body_obj)
    reason = "Too Many Requests" if status == 429 else "OK"
    return (
        "Content-Type: application/http\r\n"
        f"Content-ID: <{content_id}>\r\n"
        "\r\n"
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n"
        "\r\n"
        f"{body_json}"
    )


def _batch_response(boundary: str, parts: list) -> httpx.Response:
    chunks = [f"--{boundary}\r\n{_batch_response_part(*p)}\r\n" for p in parts]
    text = "".join(chunks) + f"--{boundary}--\r\n"
    return httpx.Response(
        200,
        content=text.encode("utf-8"),
        headers={"Content-Type": f'multipart/mixed; boundary="{boundary}"'},
    )


def _rate_limit_body() -> dict:
    return {
        "error": {
            "code": 429,
            "message": "Too many concurrent requests for user.",
            "status": "RESOURCE_EXHAUSTED",
        }
    }


class TestChunkCeiling:
    def test_batch_max_subrequests_is_25(self):
        # Correction 1: 100 -> 25, measured against the live mailbox
        # (100 -> cold 429 in 0.3s; 25 -> OK in 0.7s; n=50 only survived
        # PACED >=3s apart, and failed immediately after a prior 429 —
        # this repo's own chunk loop is back-to-back, the untested
        # condition for 50). Google's "50 is not recommended" guidance is
        # about generic batch sizing, not this per-user concurrency limit.
        assert _BATCH_MAX_SUBREQUESTS == 25


class TestEmbeddedRetryRecovers:
    def test_one_of_n_429_recovers_batch_stays_whole(self, monkeypatch):
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)
        post_bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            if "/batch/gmail/v1" not in str(request.url):
                raise AssertionError(f"unexpected request to {request.url}")
            post_bodies.append(request.content.decode("utf-8"))
            if len(post_bodies) == 1:
                return _batch_response(
                    "b1",
                    [
                        ("response-item0", 200, {"id": "m1"}),
                        ("response-item1", 429, _rate_limit_body()),
                        ("response-item2", 200, {"id": "m3"}),
                    ],
                )
            # Retry: only the failed subset is re-sent.
            assert post_bodies[1].count("Content-Type: application/http") == 1
            assert "/messages/m2" in post_bodies[1]
            return _batch_response("b2", [("response-item0", 200, {"id": "m2"})])

        backend, calls = _backend(handler)
        out = backend.get_messages_batch(["m1", "m2", "m3"], format="full")
        assert len(calls) == 2
        assert out == {"m1": {"id": "m1"}, "m2": {"id": "m2"}, "m3": {"id": "m3"}}

    def test_two_noncontiguous_failures_correlate_by_message_id_not_index(
        self, monkeypatch
    ):
        """D1's load-bearing invariant: a retry batch is renumbered from
        zero, so merging by an index inherited from the first attempt would
        silently swap message content between ids."""
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)
        post_bodies = []

        def handler(request: httpx.Request) -> httpx.Response:
            post_bodies.append(request.content.decode("utf-8"))
            if len(post_bodies) == 1:
                return _batch_response(
                    "b1",
                    [
                        ("response-item0", 200, {"id": "m0", "tag": "m0"}),
                        ("response-item1", 429, _rate_limit_body()),
                        ("response-item2", 200, {"id": "m2", "tag": "m2"}),
                        ("response-item3", 429, _rate_limit_body()),
                        ("response-item4", 200, {"id": "m4", "tag": "m4"}),
                    ],
                )
            # Retry batch is renumbered from zero: item0 -> m1, item1 -> m3.
            return _batch_response(
                "b2",
                [
                    ("response-item0", 200, {"id": "m1", "tag": "m1"}),
                    ("response-item1", 200, {"id": "m3", "tag": "m3"}),
                ],
            )

        backend, _calls = _backend(handler)
        out = backend.get_messages_batch(["m0", "m1", "m2", "m3", "m4"], format="full")
        for mid in ("m0", "m1", "m2", "m3", "m4"):
            assert out[mid]["tag"] == mid, f"{mid} landed under the wrong content"

    def test_single_id_short_circuit_retries_and_recovers(self, monkeypatch):
        """AC-7h / D5 -- the len(ids)==1 path bypasses the batch endpoint
        entirely and must still retry a 429 from get_message -> _get."""
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)
        responses = [
            httpx.Response(429, json=_rate_limit_body()),
            httpx.Response(200, json={"id": "only"}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        backend, calls = _backend(handler)
        out = backend.get_messages_batch(["only"], format="full")
        assert out == {"only": {"id": "only"}}
        assert len(calls) == 2

    def test_get_thread_429_retries_and_recovers(self, monkeypatch):
        """AC-4d / D5 -- the non-batched get_thread call (used by
        waiting-on-you detection on every scan) gets the same retry."""
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)
        responses = [
            httpx.Response(429, json=_rate_limit_body()),
            httpx.Response(200, json={"id": "t1", "messages": []}),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        backend, calls = _backend(handler)
        out = backend.get_thread("t1")
        assert out == {"id": "t1", "messages": []}
        assert len(calls) == 2


class TestBackoffSchedule:
    def test_retry_k_sleeps_within_documented_bounds(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(
            "gaia_agent_email.gmail_backend.time.sleep", lambda s: sleeps.append(s)
        )
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] < 4:
                return httpx.Response(429, json=_rate_limit_body())
            return httpx.Response(200, json={"emailAddress": "me@example.com"})

        backend, calls = _backend(handler)
        backend.get_user_email()
        assert len(calls) == 4
        assert len(sleeps) == 3
        assert 1 <= sleeps[0] < 2
        assert 2 <= sleeps[1] < 3
        assert 4 <= sleeps[2] < 5

    def test_exhaustion_sleeps_k_minus_1_times_then_raises(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(
            "gaia_agent_email.gmail_backend.time.sleep", lambda s: sleeps.append(s)
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=_rate_limit_body())

        backend, calls = _backend(handler)
        with pytest.raises(RateLimitedError):
            backend.get_user_email()
        assert len(calls) == _RATE_LIMIT_MAX_ATTEMPTS == 4
        assert len(sleeps) == _RATE_LIMIT_MAX_ATTEMPTS - 1 == 3

    def test_outer_post_retry_after_header_used_verbatim_not_blended(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(
            "gaia_agent_email.gmail_backend.time.sleep", lambda s: sleeps.append(s)
        )
        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return httpx.Response(
                    429, json=_rate_limit_body(), headers={"Retry-After": "2"}
                )
            return _batch_response(
                "ok",
                [
                    ("response-item0", 200, {"id": "m1"}),
                    ("response-item1", 200, {"id": "m2"}),
                ],
            )

        backend, calls = _backend(handler)
        out = backend.get_messages_batch(["m1", "m2"], format="full")
        assert out == {"m1": {"id": "m1"}, "m2": {"id": "m2"}}
        assert len(calls) == 2
        assert sleeps[0] == 2.0

    def test_outer_post_429_is_its_own_failure_mode(self, monkeypatch):
        """AC-7f -- the whole batch call rejected before any subrequest is
        parsed (distinct from an embedded per-item 429)."""
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=_rate_limit_body())

        backend, calls = _backend(handler)
        with pytest.raises(RateLimitedError):
            backend.get_messages_batch(["m1", "m2"], format="full")
        assert len(calls) == _RATE_LIMIT_MAX_ATTEMPTS


class TestExhaustionAndPartialResults:
    def test_multi_chunk_exhaustion_keeps_earlier_chunk_results(self, monkeypatch):
        """AC-4c / D6.2 -- a later chunk exhausting retries must not discard
        an earlier chunk's already-successful messages."""
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)
        n_ids = _BATCH_MAX_SUBREQUESTS + 1  # forces exactly two chunks
        ids = [f"id{i}" for i in range(n_ids)]

        def handler(request: httpx.Request) -> httpx.Response:
            n_parts = request.content.count(b"Content-Type: application/http")
            if n_parts == _BATCH_MAX_SUBREQUESTS:
                # First chunk always succeeds.
                parts = [
                    (f"response-item{i}", 200, {"id": ids[i]})
                    for i in range(_BATCH_MAX_SUBREQUESTS)
                ]
                return _batch_response("ok", parts)
            # Second chunk (the trailing single id) always 429s.
            return httpx.Response(429, json=_rate_limit_body())

        backend, _calls = _backend(handler)
        with pytest.raises(RateLimitedError) as exc:
            backend.get_messages_batch(ids, format="full")
        assert set(exc.value.partial_results) == set(ids[:_BATCH_MAX_SUBREQUESTS])
        assert exc.value.message_ids == [ids[-1]]

    def test_exhaustion_message_names_id_and_remedy(self, monkeypatch):
        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)

        def handler(request: httpx.Request) -> httpx.Response:
            n_parts = request.content.count(b"Content-Type: application/http")
            if n_parts == 2:
                return _batch_response(
                    "b",
                    [
                        ("response-item0", 200, {"id": "m1"}),
                        ("response-item1", 429, _rate_limit_body()),
                    ],
                )
            # Every retry only re-sends the still-failing m2.
            return _batch_response("b2", [("response-item0", 429, _rate_limit_body())])

        backend, _calls = _backend(handler)
        with pytest.raises(RateLimitedError) as exc:
            backend.get_messages_batch(["m1", "m2"], format="full")
        msg = str(exc.value).lower()
        assert "m2" in msg
        assert any(t in msg for t in ("retry", "rate limit", "try again"))


class TestSharedBudgetAcrossChunks:
    def test_sustained_429_shares_one_budget_not_chunks_times_budget(self, monkeypatch):
        """The ``_RATE_LIMIT_BUDGET_SECONDS`` wall-clock budget must be
        computed ONCE per ``get_messages_batch`` call and shared across every
        chunk -- not recomputed fresh per chunk, which would let a sustained
        429 burn a full budget per chunk and balloon total wait time and POST
        count toward chunks * budget instead of staying bounded by one
        budget."""
        import gaia_agent_email.gmail_backend as gmail_backend_module

        fake_now = {"t": 0.0}

        def fake_monotonic():
            return fake_now["t"]

        def fake_sleep(seconds):
            fake_now["t"] += seconds

        monkeypatch.setattr(gmail_backend_module.time, "monotonic", fake_monotonic)
        monkeypatch.setattr(gmail_backend_module.time, "sleep", fake_sleep)
        monkeypatch.setattr(gmail_backend_module, "_RATE_LIMIT_BUDGET_SECONDS", 10.0)
        monkeypatch.setattr(gmail_backend_module, "_RATE_LIMIT_MAX_ATTEMPTS", 4)
        # Deterministic backoff -- the jitter in the real formula would make
        # the elapsed-time assertions below flaky.
        monkeypatch.setattr(
            gmail_backend_module, "_backoff_seconds", lambda attempt: 3.0
        )

        post_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            post_count["n"] += 1
            return httpx.Response(429, json=_rate_limit_body())

        backend, _calls = _backend(handler)
        # 2 chunks: _BATCH_MAX_SUBREQUESTS ids + 1 trailing id.
        ids = [f"id{i}" for i in range(_BATCH_MAX_SUBREQUESTS + 1)]

        with pytest.raises(RateLimitedError):
            backend.get_messages_batch(ids, format="full")

        # Worked out by hand for budget=10, backoff=3, max_attempts=4:
        #   chunk 1 (25 ids): 4 POSTs at t=0,3,6,9, exhausts on attempt==max.
        #   chunk 2 (1 id):   2 POSTs at t=9,12 -- attempt 1 starts just
        #     under the shared deadline (9 < 10) so it retries once more,
        #     then attempt 2 sees the deadline has passed and raises.
        # A per-chunk-fresh budget (the bug) would instead let chunk 2 run
        # its own full 4 attempts (POSTs at t=9,12,15,18), for 8 total POSTs
        # and ~18s elapsed -- not bounded by one budget.
        assert post_count["n"] == 6, (
            f"expected 6 POSTs bounded by ONE shared budget, got "
            f"{post_count['n']} (chunks*max_attempts=8 would mean the "
            "budget was NOT shared across chunks)"
        )
        assert fake_now["t"] == 12.0, (
            f"expected ~12s elapsed under a single shared 10s budget, got "
            f"{fake_now['t']}s"
        )


class TestSanitizedErrorMessages:
    def test_embedded_non_429_failure_strips_control_bytes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            boundary = "b"
            evil = b'{"error":"\x1b[31mFAKE\x07"}'
            part = (
                "Content-Type: application/http\r\n"
                "Content-ID: <response-item1>\r\n"
                "\r\n"
                "HTTP/1.1 500 Internal Server Error\r\n"
                "Content-Type: application/json\r\n"
                "\r\n"
            ).encode("utf-8") + evil
            good = _batch_response_part("response-item0", 200, {"id": "m1"}).encode(
                "utf-8"
            )
            body = (
                f"--{boundary}\r\n".encode("utf-8")
                + good
                + f"\r\n--{boundary}\r\n".encode("utf-8")
                + part
                + f"\r\n--{boundary}--\r\n".encode("utf-8")
            )
            return httpx.Response(
                200,
                content=body,
                headers={"Content-Type": f'multipart/mixed; boundary="{boundary}"'},
            )

        backend, _calls = _backend(handler)
        with pytest.raises(ConnectorsError) as exc:
            backend.get_messages_batch(["m1", "m2"], format="full")
        text = str(exc.value)
        assert "\x1b" not in text
        assert "\x07" not in text
        assert "\n" not in text
        assert "m2" in text

    def test_generic_fallback_strips_control_bytes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="oops\x1b[31mFAKE\x07\ninjected")

        backend, _calls = _backend(handler)
        with pytest.raises(ConnectorsError) as exc:
            backend.get_user_email()
        text = str(exc.value)
        assert "\x1b" not in text
        assert "\x07" not in text


class TestNonRetryablePathsUnchanged:
    def test_non_429_failure_is_not_retried(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(
            "gaia_agent_email.gmail_backend.time.sleep", lambda s: sleeps.append(s)
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return _batch_response(
                "b",
                [
                    ("response-item0", 200, {"id": "m1"}),
                    ("response-item1", 403, {"error": {"message": "forbidden"}}),
                ],
            )

        backend, calls = _backend(handler)
        with pytest.raises(ConnectorsError):
            backend.get_messages_batch(["m1", "m2"], format="full")
        assert len(calls) == 1
        assert sleeps == []

    def test_transport_exception_is_not_rate_limited_and_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        backend, _calls = _backend(handler)
        with pytest.raises(httpx.ConnectError):
            backend.get_messages_batch(["m1", "m2"], format="full")

    def test_no_credentials_in_retry_log(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr("gaia_agent_email.gmail_backend.time.sleep", lambda s: None)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json=_rate_limit_body())

        backend, _calls = _backend(handler)
        with caplog.at_level(logging.DEBUG, logger="gaia_agent_email.gmail_backend"):
            with pytest.raises(RateLimitedError):
                backend.get_user_email()
        for record in caplog.records:
            text = record.getMessage()
            assert "Bearer" not in text
            assert "Authorization" not in text
            assert "TOKEN" not in text


class TestFakeAndLiveChunkSizeParity:
    def test_fake_and_live_chunk_size_match(self):
        from tests.fixtures.email import fake_gmail

        assert fake_gmail._BATCH_MAX_SUBREQUESTS == _BATCH_MAX_SUBREQUESTS
