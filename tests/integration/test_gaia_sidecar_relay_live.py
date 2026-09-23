# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's relay against a REAL flagship sidecar (#4161).

Every unit test around this path mocks the proxy, which proves the relay calls
a method — never that the request it builds is one the sidecar accepts. That
is the exact gap CLAUDE.md's "mocks prove 'we called it', not 'the call is
valid'" rule names, and it is what let the flagship ship unreachable from the
Agent UI: the wire was fine, nothing ever drove it.

So this spawns the sidecar from source and drives the real contract:

    GET  /v1/gaia/health     — the prefix is built per agent, not hardcoded
    GET  /v1/gaia/version    — and clears the profile's contract floor
    GET  /v1/gaia/init       — 200 or 503, both contract, never an exception
    POST /v1/gaia/query      — streamed, relayed, terminated by one answer

A wrong prefix, a 422 from an unexpected body field (both sidecar request
models are ``extra="forbid"``), or a contract floor set above what ships all
fail HERE rather than in front of a user.

Skipped unless the flagship package and uvicorn are importable. The query leg needs a
working model backend; it is deliberately NOT gated on ``/init`` saying ready,
because that probe reports unreachable against an auth-protected Lemonade
that answers ``/query`` normally — which is why the flagship treats readiness
as advisory.
"""

import importlib.util
import threading

import pytest

from gaia.daemon.sidecars.spec import builtin_specs
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE, api_version_supported
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.email_sidecar.relay import relay_query

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="flagship agent + uvicorn required for the live sidecar round-trip",
)


class _Handler:
    """The surface ``relay_query`` actually touches on SSEOutputHandler."""

    def __init__(self):
        self.events = []
        self.cancelled = threading.Event()
        self.active_relay_response = None
        self.active_relay_proxy = None
        self.active_relay_run_id = None

    def _emit(self, event):
        self.events.append(event)


@pytest.fixture(scope="module")
def live_gaia_proxy():
    from gaia.daemon.sidecars.manager import AgentSidecarManager

    with AgentSidecarManager(
        builtin_specs()["gaia"], mode="dev", health_timeout=180.0
    ) as manager:
        yield SidecarProxy(
            manager.base_url,
            agent_id="gaia",
            auth_token=manager.auth_token,
            timeout=300.0,
        )


def test_health_is_the_flagship_not_email(live_gaia_proxy):
    assert live_gaia_proxy.health()["service"] == "gaia-agent-gaia"


def test_shipped_contract_clears_the_profile_floor(live_gaia_proxy):
    """Guards a floor set above what is actually published — which would
    refuse every install with an 'update it from the Hub' message that no
    update can satisfy."""
    api_version = live_gaia_proxy.version()["apiVersion"]
    assert api_version_supported(GAIA_PROFILE, api_version), (
        f"the sidecar ships contract {api_version} but GAIA_PROFILE requires "
        f"{GAIA_PROFILE.min_api_version} — no released binary would be accepted"
    )


def test_init_answers_the_readiness_contract(live_gaia_proxy):
    """200 or 503 with a body either way; a 503 is "not ready", not a fault,
    and must never reach the caller as an exception."""
    status_code, body = live_gaia_proxy.init()
    assert status_code in (200, 503)
    assert "ready" in body
    if status_code == 503:
        assert body.get("hint"), "a not-ready answer must say what to do next"


def test_query_relays_to_a_terminal_answer(live_gaia_proxy):
    """The whole path: the body the relay builds is accepted, the SSE frames
    parse, and the run ends with exactly one terminal UI event."""
    handler = _Handler()
    relay_query(
        handler,
        live_gaia_proxy,
        query="Reply with exactly the word PONG and nothing else.",
        context=[],
        profile=GAIA_PROFILE,
        session_id="live-relay-test",
        read_timeout=300.0,
    )

    types = [e["type"] for e in handler.events]
    assert "agent_error" not in types, handler.events
    terminals = [t for t in types if t == "answer"]
    assert len(terminals) == 1, f"expected exactly one answer, got {types}"
    assert handler.events[-1]["content"].strip()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
