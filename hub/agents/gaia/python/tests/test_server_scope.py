# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``allowed_paths`` field (contract 2.18) and who can be asked.

The Agent UI relays a chat to the flagship sidecar. It used to send no scope,
so the sidecar worked in the user's whole home folder while the UI's session
panel showed something else. And with Full access on, the sidecar's handler
pre-approved tools without full access, so every out-of-scope folder the user
named was refused with nobody asked.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.security import PathValidator  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _ScopeAgent:
    """Builds its validator from the scope it is given, like ChatAgent."""

    def __init__(self, default_scope, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.path_validator = PathValidator(
            kwargs.get("allowed_paths", [str(default_scope)])
        )
        self.seen = []

    def process_query(self, query, max_steps=None):
        self.seen.append(
            (
                self.console.auto_approve_gated_tools,
                self.console.full_access,
                self.console.answers_confirmations,
            )
        )
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch, tmp_path):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    agents: list = []

    def build(**kw):
        agents.append(_ScopeAgent(home, **kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL), agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _query(client, **fields):
    body = {"query": "list my files", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def _allowed(agent, path):
    return agent.path_validator.is_path_allowed(str(path), prompt_user=False)


def test_the_contract_advertises_allowed_paths():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 18)
    assert "allowed_paths" in server_mod.QueryRequest.model_fields


def test_a_new_session_works_in_the_scope_it_was_sent(built, tmp_path):
    client, agents = built
    work = tmp_path / "work"
    work.mkdir()

    r = _query(client, session_id="s1", allowed_paths=[str(work)])

    assert r.status_code == 200, r.text
    [agent] = agents
    assert agent.kwargs["allowed_paths"] == [str(work)]
    assert _allowed(agent, work / "a.txt") is True
    # The sidecar's own default (the home folder) is not added on top.
    assert _allowed(agent, tmp_path / "home" / "b.txt") is False


def test_a_retained_session_gains_a_later_turns_scope(built, tmp_path):
    client, agents = built
    first = tmp_path / "first"
    later = tmp_path / "later"
    first.mkdir()
    later.mkdir()

    assert _query(client, session_id="s1", allowed_paths=[str(first)]).status_code == 200
    r = _query(client, session_id="s1", allowed_paths=[str(first), str(later)])

    assert r.status_code == 200, r.text
    [agent] = agents
    assert _allowed(agent, first / "x") and _allowed(agent, later / "y")


def test_omitting_allowed_paths_keeps_the_agents_default(built, tmp_path):
    client, agents = built

    assert _query(client, session_id="s1").status_code == 200

    [agent] = agents
    assert "allowed_paths" not in agent.kwargs
    assert _allowed(agent, tmp_path / "home" / "b.txt") is True


def test_a_relative_scope_entry_is_refused(built):
    client, agents = built

    r = _query(client, session_id="s1", allowed_paths=["work"])

    assert r.status_code == 422
    assert "absolute" in r.text
    assert agents == []


def test_full_access_over_http_leaves_a_watched_session_askable(built):
    """Pre-approved, not full access: an out-of-scope path is asked about."""
    client, agents = built
    assert _query(client, session_id="s1").status_code == 200
    r = client.post("/v1/gaia/sessions/s1/bypass", json={"enabled": True})
    assert r.status_code == 200, r.text

    assert _query(client, session_id="s1").status_code == 200

    assert agents[0].seen[-1] == (True, False, True)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="one-shot"),
        pytest.param(
            {"session_id": "s1", "can_answer_questions": False}, id="unattended"
        ),
    ],
)
def test_nobody_is_asked_on_an_unwatched_run(built, fields):
    client, agents = built

    assert _query(client, **fields).status_code == 200

    assert agents[0].seen[-1][2] is False
