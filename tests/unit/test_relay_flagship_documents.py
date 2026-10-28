# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat's attached documents reach a relayed flagship (#4166).

A Hub-installed flagship runs as a daemon sidecar, and the Agent UI relays each
turn over its ``/query`` contract. That body used to carry the transcript but
not the chat's attached files, so the agent answered "no documents are
indexed". These drive the real chat stream — session row, attached document,
relay, proxy — against the real flagship app over HTTP, because a stubbed
proxy cannot catch a field the sidecar refuses or never reads.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.security import PathValidator
from gaia.ui import permissions as session_permissions
from gaia.ui import session_environment
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    documents_supported,
)
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.models import ChatRequest

pytestmark = pytest.mark.allow_network


@pytest.mark.parametrize(
    "version, expected",
    [("2.16", True), ("3.0", True), ("2.15", False), (None, False), ("x", False)],
)
def test_only_a_flagship_at_2_16_takes_documents(version, expected):
    assert documents_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_documents():
    assert documents_supported(EMAIL_PROFILE, "9.9") is False


class _Rag:
    """Indexes only what the shared validator allows, like ``RAGSDK``."""

    def __init__(self, validator):
        self.validator = validator

    def index_document(self, path):
        if not self.validator.is_path_allowed(path, prompt_user=False):
            return {"success": False, "error": f"Access denied: {path}"}
        return {"success": True, "num_chunks": 1}


class _DocAgent:
    """Stands in for GaiaAgent: answers with the documents it can read."""

    def __init__(self, scope, **kwargs):
        self.built_with = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.indexed_files: set = set()
        self.path_validator = PathValidator([str(scope)])
        self.rag = _Rag(self.path_validator)
        self.queries: list = []

    def rebuild_system_prompt(self):
        pass

    def process_query(self, query, max_steps=None):
        from pathlib import Path

        self.queries.append(query)
        readable = sorted(
            Path(p).read_text(encoding="utf-8") for p in self.indexed_files
        )
        return {"answer": " | ".join(readable) or "no documents are indexed"}

    def close(self):
        pass


@pytest.fixture
def live_gaia_sidecar(monkeypatch, tmp_path):
    """The real flagship ``/v1/gaia`` app on a real port, with a document agent."""
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("gaia_agent")
    import requests
    from gaia_agent import caller_auth
    from gaia_agent import server as server_mod
    from gaia_agent import session_registry as sr

    from gaia.daemon.sidecars.manager import find_free_port

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    scope = tmp_path / "agent-scope"
    scope.mkdir()
    agents: list = []

    def build(**kw):
        agents.append(_DocAgent(scope, **kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    # /init would probe a real Lemonade; the relay only needs a ready answer.
    monkeypatch.setattr(
        server_mod,
        "_probe_lemonade",
        lambda: {
            "base_url": "http://lemonade.invalid/api/v1",
            "reachable": True,
            "version": "99.0.0",
            "present": True,
            "ctx_size": None,
            "model_id": "m",
        },
    )
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                break
        except requests.exceptions.RequestException:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("flagship sidecar test server never became reachable")
    try:
        yield base_url, agents, server_mod.API_VERSION
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


@pytest.fixture
def sidecar_flagship_registry(tmp_path, monkeypatch):
    """The registry after a binary Hub install of the flagship — no wheel."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-09-23T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    assert registry.get("gaia").is_sidecar is True
    return registry


@pytest.fixture
def chat(monkeypatch, sidecar_flagship_registry, tmp_path):
    """A flagship chat in the UI database, and a way to send it a turn."""
    session_permissions.reset_all()

    async def _noop_retitle(**_kwargs):
        return None

    monkeypatch.setattr(
        chat_helpers_module, "_agent_registry", sidecar_flagship_registry
    )
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )
    db = ChatDatabase(":memory:")
    session_id = db.create_session(agent_type="gaia")["id"]

    def send(message):
        session = db.get_session(session_id)
        request = ChatRequest(
            session_id=session_id, message=message, stream=True, agent_type="gaia"
        )
        events: list = []

        async def _drive():
            async for chunk in _stream_chat_impl(
                SimpleNamespace(handler=None), db, session, request
            ):
                for line in str(chunk).splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: ") :]))

        asyncio.run(_drive())
        return events

    def attach(path):
        content = path.read_bytes()
        doc = db.add_document(
            path.name, str(path), hashlib.sha256(content).hexdigest(), len(content)
        )
        db.attach_document(session_id, doc["id"])

    yield SimpleNamespace(db=db, send=send, attach=attach, session_id=session_id)
    session_permissions.reset_all()


def _point_ui_at(monkeypatch, base_url, api_version):
    class _Handle:
        def __init__(self):
            self.api_version = api_version

        def proxy(self):
            return SidecarProxy(base_url, agent_id="gaia")

    monkeypatch.setattr(daemon_client_module, "acquire_handle", lambda _id: _Handle())


def _answer(events):
    answers = [e["content"] for e in events if e.get("type") == "answer"]
    assert answers, events
    return answers[-1]


def test_a_document_attached_on_turn_three_is_read_by_the_relayed_flagship(
    monkeypatch, live_gaia_sidecar, chat, tmp_path
):
    base_url, agents, api_version = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    doc = tmp_path / "launch.txt"
    doc.write_text("The launch code word is HERON-42.", encoding="utf-8")
    sibling = tmp_path / "unattached.txt"
    sibling.write_text("never attached", encoding="utf-8")

    assert _answer(chat.send("hi")) == "no documents are indexed"
    assert _answer(chat.send("anything new?")) == "no documents are indexed"
    chat.attach(doc)
    events = chat.send("What is the launch code word?")

    assert _answer(events) == "The launch code word is HERON-42."
    # The user sees which file was indexed, not a bare "Result with keys".
    [step] = [
        e
        for e in events
        if e.get("tool") == "index_documents" and e.get("type") == "tool_result"
    ]
    assert step["summary"] == "indexed: launch.txt: 1 chunks"
    # One retained sidecar agent for the whole chat, granted that file only.
    assert len(agents) == 1
    assert agents[0].indexed_files == {str(doc.resolve())}
    assert (
        agents[0].path_validator.is_path_allowed(str(sibling), prompt_user=False)
        is False
    )


def test_an_older_flagship_says_documents_need_an_update(
    monkeypatch, live_gaia_sidecar, chat, tmp_path
):
    base_url, agents, _ = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, "2.15")
    doc = tmp_path / "launch.txt"
    doc.write_text("HERON-42", encoding="utf-8")
    chat.attach(doc)

    events = chat.send("What is the launch code word?")

    errors = [e for e in events if e.get("type") == "agent_error"]
    assert errors, events
    assert "can't read them" in json.dumps(errors)
    assert "Update GAIA to the latest version" in json.dumps(errors)
    assert agents == []


def test_an_older_flagship_still_chats_without_documents(
    monkeypatch, live_gaia_sidecar, chat
):
    base_url, _, _ = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, "2.15")

    assert _answer(chat.send("hi")) == "no documents are indexed"


def test_a_folder_added_to_the_chat_reaches_the_relayed_flagship(
    monkeypatch, live_gaia_sidecar, chat, tmp_path
):
    base_url, agents, api_version = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    folder = tmp_path / "q3-report"
    folder.mkdir()
    session_environment.grant_folder(chat.db, chat.session_id, str(folder))

    _answer(chat.send("what's in my folder?"))

    [agent] = agents
    assert str(folder.resolve()) in agent.built_with["allowed_paths"]
    # The model is told which folder "my folder" is; the chat keeps the question.
    [query] = agent.queries
    assert query.startswith(f"[This chat is working in the folder {folder.resolve()}.")
    assert query.endswith("\n\nwhat's in my folder?")
    assert all(
        "This chat is working in" not in (m["content"] or "")
        for m in chat.db.get_messages(chat.session_id)
    )


def test_an_older_flagship_says_folders_need_an_update(
    monkeypatch, live_gaia_sidecar, chat, tmp_path
):
    base_url, agents, _ = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, "2.17")
    folder = tmp_path / "q3-report"
    folder.mkdir()
    session_environment.grant_folder(chat.db, chat.session_id, str(folder))

    events = chat.send("what's in my folder?")

    [error] = [e for e in events if e.get("type") == "agent_error"]
    assert "too old to be given folders" in error["content"]
    assert "Update GAIA to the latest version" in error["content"]
    assert agents == []


def test_email_turns_never_carry_documents(monkeypatch, tmp_path):
    class _Handle:
        api_version = "2.99"

        def proxy(self):
            class _Proxy:
                def init(self):
                    return 200, {}

            return _Proxy()

    monkeypatch.setattr(daemon_client_module, "acquire_handle", lambda _id: _Handle())
    calls: list = []
    monkeypatch.setattr(relay, "relay_query", lambda *a, **k: calls.append(k))
    from gaia.ui.sse_handler import SSEOutputHandler

    chat_helpers_module._dispatch_sidecar_query(
        SSEOutputHandler(),
        ChatRequest(session_id="s", message="triage", agent_type="email"),
        [],
        None,
        "email",
        documents=[str(tmp_path / "a.txt")],
    )

    [kwargs] = calls
    assert kwargs["documents"] is None
