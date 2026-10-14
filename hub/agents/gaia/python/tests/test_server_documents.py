# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``documents`` field (contract 2.16, #4166).

The Agent UI relays a chat to a Hub-installed flagship over ``/query``. Until
this field existed the body carried the transcript but not the chat's attached
files, so the agent answered "no documents are indexed" about a file the user
had just attached. These drive the real app with an agent whose RAG checks the
real ``PathValidator`` the way ``RAGSDK.index_document`` does, so a grant that
is missing — or wider than the one file — fails here.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.security import PathValidator  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Rag:
    """Indexes only what the shared validator allows, like ``RAGSDK``."""

    def __init__(self, validator):
        self.validator = validator
        self.indexed: list = []

    def index_document(self, path):
        if not self.validator.is_path_allowed(path, prompt_user=False):
            return {"success": False, "error": f"Access denied: {path}"}
        self.indexed.append(path)
        return {"success": True, "num_chunks": 3}


class _DocAgent:
    """Stands in for GaiaAgent's document surface and records each turn."""

    def __init__(self, allowed_dir, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.indexed_files: set = set()
        self.path_validator = PathValidator([str(allowed_dir)])
        self.rag = _Rag(self.path_validator)
        self.prompt_rebuilds = 0
        self.indexed_at_query: list = []

    def rebuild_system_prompt(self):
        self.prompt_rebuilds += 1

    def process_query(self, query, max_steps=None):
        self.indexed_at_query.append(sorted(self.indexed_files))
        return {"answer": f"indexed={len(self.indexed_files)}"}

    def close(self):
        pass


@pytest.fixture
def docs(tmp_path):
    """A planted document, a sibling it must not unlock, and a folder."""
    folder = tmp_path / "docs"
    folder.mkdir()
    doc = folder / "plan.txt"
    doc.write_text("The launch code word is HERON-42.", encoding="utf-8")
    sibling = folder / "private.txt"
    sibling.write_text("not attached", encoding="utf-8")
    return folder, doc, sibling


@pytest.fixture
def built(monkeypatch, tmp_path):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    # The agent's own scope, deliberately NOT containing the documents.
    scope = tmp_path / "scope"
    scope.mkdir()
    agents: list = []

    def build(**kw):
        agents.append(_DocAgent(scope, **kw))
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
    body = {"query": "what is the code word?", "run_id": str(uuid.uuid4())}
    body.update({"context": []}, **fields)
    return client.post("/v1/gaia/query", json=body)


def _events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _index_step(response):
    results = [
        e
        for e in _events(response)
        if e.get("type") == "tool_result" and e.get("tool") == "index_documents"
    ]
    assert len(results) == 1, _events(response)
    return results[0]["data"]


def test_the_contract_advertises_documents():
    # Documents arrived in 2.16; later contracts keep them.
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 16)
    assert "documents" in server_mod.QueryRequest.model_fields


def test_an_attached_document_is_indexed_before_the_turn_runs(built, docs):
    client, agents = built
    _, doc, sibling = docs

    r = _query(client, session_id="s1", documents=[str(doc)])

    assert r.status_code == 200, r.text
    agent = agents[0]
    assert agent.rag.indexed == [str(doc)]
    assert agent.indexed_at_query == [[str(doc)]]
    assert agent.prompt_rebuilds == 1
    assert _index_step(r)["success"] is True
    # Exactly that file: its folder-mate stays out of reach.
    assert (
        agent.path_validator.is_path_allowed(str(sibling), prompt_user=False) is False
    )


def test_a_document_attached_mid_conversation_reaches_the_retained_agent(built, docs):
    client, agents = built
    _, doc, _ = docs

    for _ in range(2):
        assert _query(client, session_id="s1").status_code == 200
    r = _query(client, session_id="s1", documents=[str(doc)])

    assert r.status_code == 200, r.text
    assert len(agents) == 1
    assert agents[0].indexed_at_query == [[], [], [str(doc)]]


def test_a_resent_document_is_not_indexed_again(built, docs):
    client, agents = built
    _, doc, _ = docs

    _query(client, session_id="s1", documents=[str(doc)])
    r = _query(client, session_id="s1", documents=[str(doc)])

    assert agents[0].rag.indexed == [str(doc)]
    assert not [e for e in _events(r) if e.get("tool") == "index_documents"]


def test_a_relative_path_is_refused(built):
    client, agents = built

    r = _query(client, documents=["notes/plan.txt"])

    assert r.status_code == 422
    assert "absolute" in r.text
    assert agents == []


def test_unreadable_attachments_are_named_and_the_turn_still_runs(
    built, docs, tmp_path
):
    client, agents = built
    folder, doc, _ = docs
    secret = folder / ".env"
    secret.write_text("API_KEY=x", encoding="utf-8")
    missing = tmp_path / "gone.txt"

    r = _query(client, documents=[str(doc), str(missing), str(folder), str(secret)])

    assert r.status_code == 200, r.text
    step = _index_step(r)
    assert step["success"] is False
    summary = step["message"]
    assert "plan.txt: 3 chunks" in summary
    assert "gone.txt: not indexed: file not found" in summary
    assert "docs: not indexed: not a file" in summary
    assert ".env: not indexed" in summary
    assert agents[0].rag.indexed == [str(doc)]
    assert agents[0].indexed_at_query == [[str(doc)]]
    assert (
        agents[0].path_validator.is_path_allowed(str(folder), prompt_user=False)
        is False
    )


def test_a_session_without_document_search_says_so(built, docs, monkeypatch):
    client, agents = built
    _, doc, _ = docs

    def build(**kw):
        agent = _DocAgent(doc.parent.parent / "scope", **kw)
        agent.rag = None
        agents.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    r = _query(client, documents=[str(doc)])

    assert r.status_code == 200, r.text
    assert "document search is not available" in _index_step(r)["message"]


def test_agents_are_never_built_at_the_same_time(monkeypatch):
    """Two builds at once crossed tool closures: the first session's
    query_specific_file ran on the warm-up agent, which had nothing indexed
    and no grant, so a freshly attached document read as access denied."""
    import threading
    import time

    from gaia_agent import agent as agent_mod

    active = []
    peak = []

    class _Built:
        def __init__(self, config):
            active.append(self)
            peak.append(len(active))
            time.sleep(0.05)
            active.remove(self)

    monkeypatch.setattr(agent_mod, "GaiaAgent", _Built)
    monkeypatch.setattr(agent_mod, "GaiaAgentConfig", lambda **kw: kw)
    builders = [sr.build_session_agent, server_mod.build_query_agent] * 3
    threads = [threading.Thread(target=b) for b in builders]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(peak) == len(builders)
    assert max(peak) == 1
