# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ASGI request bounds, including unknown-length and slow uploads."""

import asyncio
import json

import pytest
from gaia_agent.service_limits import RequestBodyLimitMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
async def test_actual_chunked_bytes_enforced_even_with_small_declared_length(headers):
    messages = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def app(*_args):
        pytest.fail("Oversized body reached application")

    await RequestBodyLimitMiddleware(app, 5)(
        {"type": "http", "headers": headers}, receive, send
    )
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_exact_limit_replays_body_and_preserves_disconnect():
    messages = iter(
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"c", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    async def app(_scope, receive, _send):
        assert await receive() == {
            "type": "http.request",
            "body": b"abc",
            "more_body": False,
        }
        assert await receive() == {"type": "http.disconnect"}

    await RequestBodyLimitMiddleware(app, 3)(
        {"type": "http", "headers": []}, receive, None
    )


@pytest.mark.asyncio
async def test_slow_upload_has_finite_deadline():
    sent = []

    async def receive():
        await asyncio.sleep(2)

    async def send(message):
        sent.append(message)

    async def app(*_args):
        pytest.fail("Incomplete body reached application")

    await RequestBodyLimitMiddleware(app, 3, read_timeout=0.01)(
        {"type": "http", "headers": []}, receive, send
    )
    assert sent[0]["status"] == 408
    assert json.loads(sent[1]["body"])["detail"] == "Request body timed out."


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"-1", b"invalid"])
async def test_invalid_length_rejected_before_read(value):
    sent = []

    async def unexpected(*_args):
        pytest.fail("Invalid length reached application or body reader")

    async def send(message):
        sent.append(message)

    await RequestBodyLimitMiddleware(unexpected, 3)(
        {"type": "http", "headers": [(b"content-length", value)]}, unexpected, send
    )
    assert sent[0]["status"] == 400
