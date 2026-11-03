# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GET /api/sessions/{id}/capabilities`` — the session panel's Agent info (#440).

The tools come from the agent the chat actually talks to: the cached
in-process agent, or the relayed sidecar's own session. Every way that agent
can be unavailable is an error naming why, never an empty list.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.tools import describe_registry
from gaia.ui import _chat_helpers, session_capabilities, session_environment
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.server import create_app


def _doc_tool():
    """Find a document."""


_REGISTRY = {
    "query_documents": {
        "description": "Search the indexed documents.",
        "function": SimpleNamespace(__module__="gaia.agents.tools.rag_tools"),
    },
    "mcp_github_list_issues": {
        "description": "[MCP:github] List issues.",
        "_mcp_server": "github",
    },
    "rss-digest/fetch_rss": {"description": "Fetch a feed.", "function": _doc_tool},
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    gaia_home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(gaia_home))
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", gaia_home / "config.json")
    return gaia_home


@pytest.fixture(autouse=True)
def in_process(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)


@pytest.fixture(autouse=True)
def clean_cache():
    with _chat_helpers._agent_cache_lock:
        saved = dict(_chat_helpers._agent_cache)
        _chat_helpers._agent_cache.clear()
    yield
    with _chat_helpers._agent_cache_lock:
        _chat_helpers._agent_cache.clear()
        _chat_helpers._agent_cache.update(saved)


@pytest.fixture
def client(home):
    return TestClient(create_app(db_path=":memory:"))


def _new_session(client, agent_type=None):
    # Straight to the DB: whether /api/sessions accepts "gaia" depends on
    # which agent packages this test environment has installed.
    session = client.app.state.db.create_session(agent_type=agent_type)
    return session["id"], session["agent_type"]


def _cache(session_id, agent_type, registry):
    agent = SimpleNamespace(describe_tools=lambda: describe_registry(dict(registry)))
    _chat_helpers._store_agent(session_id, "m", [], agent, agent_type)
    return agent


def _get(client, sid):
    return client.get(f"/api/sessions/{sid}/capabilities")


# -- in process ---------------------------------------------------------------


def test_lists_the_cached_agents_tools_with_a_category_each(client):
    sid, agent_type = _new_session(client)
    _cache(sid, agent_type, _REGISTRY)

    r = _get(client, sid)

    assert r.status_code == 200, r.text
    assert r.json() == {
        "session_id": sid,
        "agent_type": agent_type,
        "tools": [
            {
                "name": "query_documents",
                "description": "Search the indexed documents.",
                "category": "rag",
            },
            {
                "name": "mcp_github_list_issues",
                "description": "[MCP:github] List issues.",
                "category": "mcp",
            },
            {
                "name": "rss-digest/fetch_rss",
                "description": "Fetch a feed.",
                "category": "skill",
            },
        ],
    }


def test_an_unknown_chat_is_a_404(client):
    assert _get(client, "no-such-chat").status_code == 404


def test_a_chat_whose_agent_has_not_started_is_a_409_not_an_empty_list(client):
    sid, _ = _new_session(client)

    r = _get(client, sid)

    assert r.status_code == 409
    assert r.json()["detail"] == session_capabilities.NOT_RUNNING


def test_an_agent_with_no_tools_is_an_empty_list_not_an_error(client):
    """Empty is reported only when the running agent really has none."""
    sid, agent_type = _new_session(client)
    _cache(sid, agent_type, {})

    r = _get(client, sid)

    assert r.status_code == 200
    assert r.json()["tools"] == []


def test_a_cached_agent_of_another_type_does_not_speak_for_the_chat(client):
    """Switched agents keep the old one cached until the next turn."""
    sid, _ = _new_session(client)
    _cache(sid, "some-other-agent", _REGISTRY)

    assert _get(client, sid).status_code == 409


def test_another_chats_agent_is_never_used(client):
    sid, agent_type = _new_session(client)
    other, _ = _new_session(client)
    _cache(other, agent_type, _REGISTRY)

    assert _get(client, sid).status_code == 409


def test_a_tool_registered_twice_is_listed_once():
    """The registry is keyed by name: a re-registration replaces the first."""
    registry = dict(_REGISTRY)
    registry["query_documents"] = {"description": "Newer.", "function": _doc_tool}

    names = [t["name"] for t in describe_registry(registry)]

    assert names.count("query_documents") == 1


# -- relayed (sidecar) --------------------------------------------------------


class _Proxy:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def session_tools(self, session_id):
        self.asked.append(session_id)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def sidecar(monkeypatch):
    """The flagship runs as a daemon sidecar, as on a Hub binary install."""
    from gaia.ui.email_sidecar import daemon_client

    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    state = SimpleNamespace(
        api_version="2.27",
        acquired=[],
        proxy=_Proxy({"session_id": "x", "tools": describe_registry(_REGISTRY)}),
    )

    def acquire(agent_id):
        state.acquired.append(agent_id)
        return SimpleNamespace(api_version=state.api_version, proxy=lambda: state.proxy)

    monkeypatch.setattr(daemon_client, "acquire_handle", acquire)
    return state


def test_a_relayed_chat_asks_its_own_sidecar_session(client, sidecar):
    sid, _ = _new_session(client, "gaia")

    r = _get(client, sid)

    assert r.status_code == 200, r.text
    assert sidecar.acquired == ["gaia"]
    assert sidecar.proxy.asked == [sid]
    assert [t["category"] for t in r.json()["tools"]] == ["rag", "mcp", "skill"]


def test_a_sidecar_with_no_session_yet_is_a_409(client, sidecar):
    sid, _ = _new_session(client, "gaia")
    sidecar.proxy.answer = SidecarHTTPError(404, "No session", path="/x")

    r = _get(client, sid)

    assert r.status_code == 409
    assert r.json()["detail"] == session_capabilities.NOT_RUNNING


def test_a_sidecar_too_old_to_list_tools_is_a_503_and_is_not_asked(client, sidecar):
    sid, _ = _new_session(client, "gaia")
    sidecar.api_version = "2.26"

    r = _get(client, sid)

    assert r.status_code == 503
    assert "too old to list its tools" in r.json()["detail"]
    assert sidecar.proxy.asked == []


@pytest.mark.parametrize("version", [None, "", "garbage"])
def test_a_sidecar_with_no_readable_version_is_not_trusted(client, sidecar, version):
    sid, _ = _new_session(client, "gaia")
    sidecar.api_version = version

    assert _get(client, sid).status_code == 503
    assert sidecar.proxy.asked == []


def test_the_email_agent_says_it_cannot_list_tools_without_being_started(
    client, sidecar
):
    sid, _ = _new_session(client, "email")

    r = _get(client, sid)

    assert r.status_code == 503
    assert "email agent can't list its tools" in r.json()["detail"]
    assert sidecar.acquired == []


def test_a_sidecar_that_cannot_start_is_a_503(client, sidecar, monkeypatch):
    from gaia.ui.email_sidecar import daemon_client

    def fail(_agent_id):
        raise SidecarError("sidecar crashed")

    monkeypatch.setattr(daemon_client, "acquire_handle", fail)
    sid, _ = _new_session(client, "gaia")

    r = _get(client, sid)

    assert r.status_code == 503
    assert "couldn't start" in r.json()["detail"]


@pytest.mark.parametrize(
    "error",
    [SidecarHTTPError(500, "boom", path="/x"), SidecarError("connection refused")],
)
def test_a_sidecar_that_fails_to_answer_is_a_502_naming_it(client, sidecar, error):
    sid, _ = _new_session(client, "gaia")
    sidecar.proxy.answer = error

    r = _get(client, sid)

    assert r.status_code == 502
    assert "couldn't list its tools" in r.json()["detail"]


def test_a_sidecar_listing_the_same_tool_twice_is_refused(client, sidecar):
    sid, _ = _new_session(client, "gaia")
    tool = {"name": "write_file", "description": "Write.", "category": "file_io"}
    sidecar.proxy.answer = {"session_id": sid, "tools": [tool, dict(tool)]}

    r = _get(client, sid)

    assert r.status_code == 502
    assert "'write_file' listed twice" in r.json()["detail"]


@pytest.mark.parametrize(
    "answer",
    [
        [],
        {"session_id": "x"},
        {"tools": "write_file"},
        {"tools": [None]},
        {"tools": [{"name": "write_file", "description": "Write."}]},
        {"tools": [{"name": 3, "description": "Write.", "category": "file_io"}]},
    ],
)
def test_a_malformed_sidecar_list_is_a_502_not_a_partial_list(client, sidecar, answer):
    sid, _ = _new_session(client, "gaia")
    sidecar.proxy.answer = answer

    r = _get(client, sid)

    assert r.status_code == 502
    assert "tool list GAIA can't read" in r.json()["detail"]


def test_extra_sidecar_fields_are_not_passed_through(client, sidecar):
    sid, _ = _new_session(client, "gaia")
    sidecar.proxy.answer = {
        "tools": [
            {
                "name": "write_file",
                "description": "Write.",
                "category": "file_io",
                "function": "<function>",
            }
        ]
    }

    r = _get(client, sid)

    assert r.json()["tools"] == [
        {"name": "write_file", "description": "Write.", "category": "file_io"}
    ]


def test_proxy_asks_the_sessions_tools_route():
    """The wire shape: GET /v1/<agent>/sessions/<id>/tools on that sidecar."""
    from gaia.ui.email_sidecar.proxy import SidecarProxy

    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"session_id": "s1", "tools": []}

    class _Session:
        headers = {}

        def get(self, url, params=None, timeout=None):
            calls.append(url)
            return _Resp()

    proxy = SidecarProxy(
        "http://127.0.0.1:9", agent_id="gaia", session=_Session(), timeout=1
    )
    assert proxy.session_tools("s1") == {"session_id": "s1", "tools": []}
    assert calls == ["http://127.0.0.1:9/v1/gaia/sessions/s1/tools"]
