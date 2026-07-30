# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A slow credential-store read must not take the whole sidecar down.

``GET /v1/email/connectors`` reads the OS credential store. That read has no
bounded worst case: on macOS a keychain access can sit in ``SecItemCopyMatching``
waiting on an authorization decision that a background process never gets, and
a corrupted or contended store can stall it too.

Run on the event loop, a stall like that does not cost one request — it costs
the whole process, ``/health`` included, because nothing else can be scheduled
until it returns. That is what makes it invisible to a supervisor: the process
stays alive and keeps its port, so it looks healthy while serving nothing.

The guard is that the read happens off the loop, so however long the credential
store takes, it costs one request and the rest of the sidecar keeps answering.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("gaia_agent_email")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

# Long enough that a loop-blocking implementation cannot finish inside the
# concurrent probe's timeout, short enough not to slow the suite when the
# healthy path (a fast store) is taken.
_STORE_STALL_SECONDS = 3.0
_PROBE_TIMEOUT_SECONDS = 1.5

# The middleware serves loopback Hosts only; a non-loopback Host is rejected 400
# as a DNS-rebinding attempt.
_BASE_URL = "http://127.0.0.1:8131"


def test_health_still_answers_while_connector_store_is_stuck(monkeypatch):
    """/health must answer while /v1/email/connectors sits in a stuck store read."""
    import httpx
    from gaia_agent_email import caller_auth, server

    # Build with caller auth off so the connectors route is reachable without
    # minting a token; the wedge under test is upstream of authentication.
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    app = server.build_app()

    entered = threading.Event()
    entered_at: list = []

    def _stuck_store():
        entered_at.append(time.monotonic())
        entered.set()
        time.sleep(_STORE_STALL_SECONDS)
        return []

    # Patched at its source module: the route imports it inside the call.
    monkeypatch.setattr("gaia.connectors.api.connected_mailbox_providers", _stuck_store)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as c:
            connectors = asyncio.create_task(c.get("/v1/email/connectors"))

            # Wait until the stuck read is actually in flight, so the probe
            # below measures the wedge and not a race against request startup.
            deadline = time.monotonic() + 5.0
            while not entered.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert entered.is_set(), "connectors route never reached the store read"

            health = await c.get("/health")
            assert health.status_code == 200
            assert health.json()["service"] == "gaia-agent-email"

            # The real assertion is WHEN it answered, not that it eventually
            # did. A loop-blocking read cannot be observed while it blocks —
            # the probe, its timeout, and this coroutine are all frozen with
            # it — so completion alone proves nothing and the elapsed time
            # since the read began is the only honest discriminator. Served
            # off the loop this is milliseconds; served on it, it cannot come
            # back before the read finishes.
            served_after = time.monotonic() - entered_at[0]
            assert served_after < _PROBE_TIMEOUT_SECONDS, (
                f"/health was served {served_after:.2f}s after the credential "
                f"read began, i.e. only once the read finished — the read is "
                f"blocking the event loop and wedging every other route"
            )

            resp = await asyncio.wait_for(connectors, timeout=_STORE_STALL_SECONDS + 5.0)
            assert resp.status_code == 200

    asyncio.run(scenario())
