# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GET /v1/gaia/memory`` — the daemon-transport counterpart of the stdio
``MEMORY_DUMP_QUERY`` sentinel (#3978).

Drives the real ``build_app()`` over HTTP, same seam-injection approach as
``test_server_query.py``: a scripted agent stands in for ``GaiaAgent`` so the
test proves the route's plumbing (agent built, dump returned, agent closed)
without needing Lemonade or a real memory store.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import __version__ as agent_version  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _ScriptedAgent:
    """Just enough of GaiaAgent for build_memory_dump() and close_agent()."""

    def __init__(self, memory_store=None, unavailable_message=None, **kwargs):
        self.kwargs = kwargs
        self.memory_store = memory_store
        self._unavailable_message = unavailable_message
        self.closed = 0

    def memory_unavailable_message(self):
        return self._unavailable_message

    def close(self):
        self.closed += 1


class _FakeMemoryStore:
    """The two calls build_memory_dump() makes on a real MemoryStore."""

    def __init__(self, items):
        self._items = items

    def get_stats(self):
        return {
            "knowledge": {
                "total": len(self._items),
                "by_category": {},
                "by_context": {},
                "sensitive_count": 0,
                "entity_count": 0,
                "avg_confidence": 0.0,
            }
        }

    def get_all_knowledge(self, sort_by, order, limit):
        page = self._items[:limit]
        return {"items": page, "total": len(self._items)}

    def get_contexts(self):
        return [{"context": "global", "count": len(self._items)}]


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)

    agents: list[_ScriptedAgent] = []

    def build(**kw):
        agent = _ScriptedAgent(**kw)
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)

    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
    try:
        yield client, agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def test_memory_route_returns_the_dump_shape(built, monkeypatch):
    client, agents = built
    item = {
        "id": "1",
        "category": "fact",
        "content": "prefers dark mode",
        "entity": None,
        "context": "global",
        "confidence": 0.8,
        "sensitive": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "last_used": None,
    }

    def build(**kw):
        agent = _ScriptedAgent(memory_store=_FakeMemoryStore([item]), **kw)
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)

    r = client.get("/v1/gaia/memory")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["shown"] == 1
    assert body["total"] == 1
    assert body["items"][0]["content"] == "prefers dark mode"
    assert body["contexts"] == [{"context": "global", "count": 1}]
    # required by MemoryDump (tui/internal/client/memory.go) even when empty
    assert "stats" in body


def test_memory_route_reports_unavailable_not_an_empty_dump(built):
    """No silent fallback: an absent store is a named reason, not a blank view."""
    client, _agents = built

    r = client.get("/v1/gaia/memory")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"]


def test_memory_route_closes_its_one_shot_agent(built):
    client, agents = built

    r = client.get("/v1/gaia/memory")

    assert r.status_code == 200, r.text
    assert len(agents) == 1
    assert agents[0].closed == 1


def test_memory_route_surfaces_a_genuine_build_failure_as_a_500(built, monkeypatch):
    """A real bug in the dump (not 'no store') must not read as a 200."""
    client, _agents = built

    def failing_build_memory_dump(agent):
        raise RuntimeError("scripted memory store failure")

    monkeypatch.setattr(server_mod, "build_memory_dump", failing_build_memory_dump)

    r = client.get("/v1/gaia/memory")

    assert r.status_code == 500
    assert "scripted memory store failure" in r.json()["detail"]


def test_api_version_meets_the_memory_contract_floor():
    """`/v1/gaia/memory` requires apiVersion >= 2.13 (SPEC.md). A later bump to
    2.14, 3.0, etc. must keep clearing this floor, not just match one literal."""
    major, minor = (int(p) for p in server_mod.API_VERSION.split(".")[:2])
    memory_contract_floor = (2, 13)
    assert (major, minor) >= memory_contract_floor


def test_version_endpoints_report_the_version_key(built):
    client, _agents = built

    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["apiVersion"] == server_mod.API_VERSION
    assert body["agentVersion"] == agent_version

    r = client.get("/v1/gaia/version")
    assert r.status_code == 200
    body = r.json()
    assert body["apiVersion"] == server_mod.API_VERSION
    # The Go TUI header reads this key (tui/internal/client) — renaming it
    # (e.g. to agentVersion, matching /version above) must fail here.
    assert body["version"] == agent_version
