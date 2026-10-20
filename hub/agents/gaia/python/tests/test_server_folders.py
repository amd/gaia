# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s ``folders``, ``withheld_folders`` and ``working_folder`` (2.18).

The Agent UI relays a chat to the flagship sidecar over ``/query``. Before
these fields a folder the user added to that chat never reached the agent, so
the Agent UI refused to add one at all. These drive the real app and check the
agent's real ``PathValidator``.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

from gaia.security import PathValidator  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


class _Agent:
    def __init__(self, scope, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.indexed_files: set = set()
        self.rag = None
        self.path_validator = PathValidator([str(scope)])
        self.seen: list = []

    def process_query(self, query, max_steps=None):
        target = Path(query)
        self.seen.append(
            self.path_validator.is_path_allowed(str(target), prompt_user=False)
        )
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def built(monkeypatch, tmp_path):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    scope = tmp_path / "scope"
    scope.mkdir()
    agents: list = []

    def build(**kw):
        agents.append(_Agent(scope, **kw))
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


def _query(client, query, **fields):
    body = {"query": query, "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def _terminal(response):
    events = [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return events[-1]


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "notes.txt").write_text("x", encoding="utf-8")
    return folder


def test_an_added_folder_is_granted_and_a_removed_one_refused(built, project):
    client, agents = built
    target = str(project / "notes.txt")
    sid = str(uuid.uuid4())

    granted = _query(client, target, session_id=sid, folders=[str(project)])
    assert _terminal(granted)["type"] == "final", granted.text

    removed = _query(
        client, target, session_id=sid, folders=[], withheld_folders=[str(project)]
    )
    assert _terminal(removed)["type"] == "final", removed.text
    assert agents[-1].seen == [True, False]


def test_working_folder_is_granted_and_becomes_the_project_root(built, project):
    client, agents = built
    response = _query(
        client,
        str(project / "notes.txt"),
        session_id=str(uuid.uuid4()),
        working_folder=str(project),
    )
    assert _terminal(response)["type"] == "final", response.text
    assert agents[-1].kwargs["project_root"] == str(project)
    assert agents[-1].seen == [True]


def test_without_folders_the_agent_keeps_its_own_scope(built, project):
    client, agents = built
    response = _query(client, str(project / "notes.txt"))
    assert _terminal(response)["type"] == "final"
    assert agents[-1].seen == [False]
    assert "project_root" not in agents[-1].kwargs


def test_relative_folders_are_rejected_before_the_run(built):
    client, _ = built
    assert _query(client, "q", folders=["relative/dir"]).status_code == 422
    assert _query(client, "q", working_folder="relative").status_code == 422


def test_a_folder_too_broad_to_grant_ends_the_turn(built):
    client, agents = built
    response = _query(client, "q", folders=[str(Path.home())])
    terminal = _terminal(response)
    assert terminal["type"] == "error"
    assert "home directory" in terminal["detail"]
    assert agents[-1].seen == []
