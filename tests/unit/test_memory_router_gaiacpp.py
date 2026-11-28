# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Memory page serves the store of the agent this PC is set to use.

gaiacpp keeps its own memory and serves it from its sidecar; the Python agent
keeps another. These tests run the router against a real temp Python store and
a fake gaiacpp sidecar that records every call, so a fact in one store can
never be mistaken for a fact in the other.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui.routers.memory as memory_router_mod
from gaia.agents.base.memory_store import MemoryStore
from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars.errors import (
    KIND_CAPACITY,
    SidecarError,
    SidecarHTTPError,
)
from gaia.ui.sidecar import daemon_client
from gaia.ui.sidecar.profiles import GAIA_PROFILE

PYTHON_FACT = "The Python agent knows the user's dog is Rex"
CPP_FACT = "gaiacpp knows the user drinks oolong tea"

ALL_CAPABILITIES = ("search", "create", "edit", "delete")


def _row(
    row_id,
    content,
    category="fact",
    *,
    sensitive=False,
    entity=None,
    context="global",
    updated="2026-11-01T10:00:00Z",
    created="2026-10-01T10:00:00Z",
):
    return {
        "id": row_id,
        "category": category,
        "content": content,
        "entity": entity,
        "context": context,
        "confidence": None,
        "sensitive": sensitive,
        "created_at": created,
        "updated_at": updated,
        "last_used": None,
    }


class FakeGaiacpp:
    """gaiacpp's memory routes as ``SidecarProxy`` exposes them, recording calls.

    ``capabilities=None`` is an older gaiacpp: it only lists, ignores the query
    parameters and its reply has no ``store`` key.
    """

    def __init__(self, rows=(), *, capabilities=ALL_CAPABILITIES, exists=True):
        self.rows = [dict(r) for r in rows]
        self.capabilities = capabilities
        self.exists = exists
        self.total = None
        self.fail = None
        self.calls = []

    def _store(self):
        return {
            "agent": "gaiacpp",
            "path": "/home/u/.gaia/memory-cpp.db",
            "capabilities": list(self.capabilities),
        }

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    def memory(self, *, q=None, category=None, limit=None):
        self.calls.append(("memory", {"q": q, "category": category, "limit": limit}))
        self._maybe_fail()
        if self.capabilities is None:
            rows = self.rows[:300]
            return {
                "available": True,
                "shown": len(rows),
                "total": len(self.rows),
                "items": rows,
            }
        if not self.exists:
            return {
                "available": False,
                "reason": "Nothing has been remembered yet.",
                "store": self._store(),
            }
        rows = self.rows
        if q:
            rows = [
                r for r in rows if any(w in r["content"].lower() for w in q.split())
            ]
        if category:
            rows = [r for r in rows if r["category"] == category]
        rows = rows[: limit or 300]
        return {
            "available": True,
            "shown": len(rows),
            "total": self.total if self.total is not None else len(self.rows),
            "items": rows,
            "store": self._store(),
        }

    def create_memory(self, content, **fields):
        self.calls.append(("create_memory", {"content": content, **fields}))
        self._maybe_fail()
        self.rows.insert(0, _row("new-1", content, fields.get("category") or "fact"))
        return {"status": "created", "id": "new-1"}

    def _find(self, memory_id, path):
        for row in self.rows:
            if row["id"] == memory_id:
                return row
        raise SidecarHTTPError(
            404, f"No memory with id {memory_id}", path=path, agent_id="gaia"
        )

    def update_memory(self, memory_id, content):
        self.calls.append(("update_memory", {"id": memory_id, "content": content}))
        self._maybe_fail()
        self._find(memory_id, f"/v1/gaia/memory/{memory_id}")["content"] = content
        return {"status": "updated", "id": memory_id}

    def delete_memory(self, memory_id):
        self.calls.append(("delete_memory", {"id": memory_id}))
        self._maybe_fail()
        self.rows.remove(self._find(memory_id, f"/v1/gaia/memory/{memory_id}"))
        return {"status": "deleted", "id": memory_id}

    def writes(self):
        return [c for c in self.calls if c[0] != "memory"]


class _Handle:
    def __init__(self, fake):
        self._fake = fake

    def proxy(self, **_kwargs):
        return self._fake


@pytest.fixture
def python_store(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    store.store(category="fact", content=PYTHON_FACT, source="user")
    yield store
    store.close()


@pytest.fixture
def acquired(monkeypatch):
    """Every agent id the router asked the daemon for."""
    asked = []
    monkeypatch.setattr(
        daemon_client,
        "acquire_handle",
        lambda agent_id: (_ for _ in ()).throw(
            AssertionError(f"the daemon was asked for {agent_id!r}")
        ),
    )
    return asked


@pytest.fixture
def client(python_store, acquired):
    from gaia.ui.database import ChatDatabase
    from gaia.ui.dependencies import get_db

    app = FastAPI()
    app.include_router(memory_router_mod.router)
    memory_router_mod._store = python_store
    db = ChatDatabase(":memory:")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, headers={"X-Gaia-UI": "1"}) as c:
        yield c
    memory_router_mod._store = None
    db.close()


def _use(monkeypatch, acquired, agent, fake=None):
    """Save *agent* as the flagship; the daemon then hands out *fake*."""
    monkeypatch.setattr(fetch, "saved_flagship", lambda: agent)
    if fake is not None:

        def _acquire(agent_id):
            acquired.append(agent_id)
            return _Handle(fake)

        monkeypatch.setattr(daemon_client, "acquire_handle", _acquire)


@pytest.fixture
def gaiacpp(monkeypatch, acquired):
    fake = FakeGaiacpp([_row("c1", CPP_FACT, "preference")])
    _use(monkeypatch, acquired, "gaiacpp", fake)
    return fake


def _python_rows(store):
    return [r["content"] for r in store.get_all_knowledge(limit=200)["items"]]


# -- which store the page serves ---------------------------------------------


def test_gaiacpp_selected_lists_its_fact_and_not_the_python_stores(
    client, gaiacpp, acquired
):
    resp = client.get("/api/memory/knowledge", params={"about_user": True})
    assert resp.status_code == 200
    body = resp.json()
    assert [i["content"] for i in body["items"]] == [CPP_FACT]
    assert body["total"] == 1
    assert acquired == [GAIA_PROFILE.agent_id]


def test_gaiapy_selected_lists_the_python_store_and_never_asks_the_daemon(
    client, monkeypatch, acquired
):
    # `acquired` makes any daemon call an AssertionError.
    _use(monkeypatch, acquired, "gaiapy")
    resp = client.get("/api/memory/knowledge", params={"about_user": True})
    assert resp.status_code == 200
    assert [i["content"] for i in resp.json()["items"]] == [PYTHON_FACT]
    assert client.get("/api/memory/stats").json()["knowledge"]["total"] == 1
    assert client.get("/api/memory/activity").status_code == 200


def test_a_gaiacpp_row_has_the_python_rows_keys_with_nothing_invented(client, gaiacpp):
    item = client.get("/api/memory/knowledge").json()["items"][0]
    python_keys = set(
        memory_router_mod._store.get_all_knowledge(limit=1)["items"][0].keys()
    )
    assert set(item.keys()) == python_keys
    assert item["id"] == "c1" and item["category"] == "preference"
    assert item["context"] == "global" and item["sensitive"] is False
    for absent in (
        "source",
        "confidence",
        "use_count",
        "last_used",
        "due_at",
        "domain",
        "metadata",
        "reminded_at",
        "superseded_by",
    ):
        assert item[absent] is None, absent


# -- GET /api/memory/store ---------------------------------------------------


def test_store_says_gaiacpp_and_what_its_memory_cannot_do(
    client, monkeypatch, acquired
):
    _use(monkeypatch, acquired, "gaiacpp")  # no fake: a daemon call would fail
    assert client.get("/api/memory/store").json() == {
        "agent": "gaiacpp",
        "other_available": True,
        "supports": {
            "due_dates": False,
            "sources": False,
            "sensitive_toggle": False,
            "recategorize": False,
            "goals": False,
            "profile": False,
            "advanced": False,
        },
    }


@pytest.mark.parametrize("installed", [True, False])
def test_store_says_gaiapy_and_whether_gaiacpp_is_installed(
    client, monkeypatch, acquired, tmp_path, installed
):
    _use(monkeypatch, acquired, "gaiapy")
    monkeypatch.setattr(
        fetch, "native_flagship", lambda: tmp_path / "gaiacpp" if installed else None
    )
    assert client.get("/api/memory/store").json() == {
        "agent": "gaiapy",
        "other_available": installed,
        "supports": {
            "due_dates": True,
            "sources": True,
            "sensitive_toggle": True,
            "recategorize": True,
            "goals": True,
            "profile": True,
            "advanced": True,
        },
    }


# -- listing gaiacpp's store -------------------------------------------------


@pytest.fixture
def many(monkeypatch, acquired):
    fake = FakeGaiacpp(
        [
            _row("a", "likes oolong tea", "preference", updated="2026-11-05T00:00:00Z"),
            _row("b", "sister is Ana", "fact", updated="2026-11-04T00:00:00Z"),
            _row("c", "shell tool timed out", "error", updated="2026-11-03T00:00:00Z"),
            _row(
                "d",
                "passport is in the desk",
                "note",
                sensitive=True,
                updated="2026-11-02T00:00:00Z",
            ),
            _row(
                "e",
                "call the dentist",
                "reminder",
                entity="person:dentist",
                context="health",
                updated="2026-11-01T00:00:00Z",
                created="2026-11-01T00:00:00Z",
            ),
        ]
    )
    _use(monkeypatch, acquired, "gaiacpp", fake)
    return fake


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()["items"]]


def test_list_asks_for_every_row_and_hides_sensitive_ones_by_default(client, many):
    assert _ids(client.get("/api/memory/knowledge")) == ["a", "b", "c", "e"]
    assert many.calls == [("memory", {"q": None, "category": None, "limit": 1000})]
    assert _ids(
        client.get("/api/memory/knowledge", params={"include_sensitive": True})
    ) == ["a", "b", "c", "d", "e"]
    assert _ids(client.get("/api/memory/knowledge", params={"sensitive": True})) == [
        "d"
    ]


def test_search_is_sent_to_gaiacpp_as_q(client, many):
    assert _ids(client.get("/api/memory/knowledge", params={"search": "oolong"})) == [
        "a"
    ]
    assert many.calls == [("memory", {"q": "oolong", "category": None, "limit": 1000})]


def test_one_category_is_sent_to_gaiacpp_and_several_are_filtered_here(client, many):
    assert _ids(client.get("/api/memory/knowledge", params={"category": "fact"})) == [
        "b"
    ]
    assert many.calls[-1] == ("memory", {"q": None, "category": "fact", "limit": 1000})
    assert _ids(
        client.get("/api/memory/knowledge", params={"category": ["fact", "reminder"]})
    ) == ["b", "e"]
    assert many.calls[-1] == ("memory", {"q": None, "category": None, "limit": 1000})


def test_about_user_leaves_out_the_agents_own_error_lessons(client, many):
    assert _ids(
        client.get(
            "/api/memory/knowledge",
            params={"about_user": True, "include_sensitive": True},
        )
    ) == ["a", "b", "d", "e"]


def test_context_entity_and_time_filters_apply(client, many):
    assert _ids(client.get("/api/memory/knowledge", params={"context": "health"})) == [
        "e"
    ]
    assert _ids(
        client.get("/api/memory/knowledge", params={"entity": "person:dentist"})
    ) == ["e"]
    assert _ids(
        client.get(
            "/api/memory/knowledge", params={"time_from": "2026-10-15T00:00:00+00:00"}
        )
    ) == ["e"]
    assert _ids(
        client.get(
            "/api/memory/knowledge", params={"time_to": "2026-10-15T00:00:00+00:00"}
        )
    ) == ["a", "b", "c"]


def test_paging_and_sorting_happen_here_over_what_gaiacpp_returned(client, many):
    params = {"include_sensitive": True, "limit": 2}
    first = client.get("/api/memory/knowledge", params={**params, "offset": 0})
    assert _ids(first) == ["a", "b"]
    assert first.json()["total"] == 5
    assert (first.json()["offset"], first.json()["limit"]) == (0, 2)
    assert _ids(
        client.get("/api/memory/knowledge", params={**params, "offset": 2})
    ) == [
        "c",
        "d",
    ]
    assert _ids(
        client.get("/api/memory/knowledge", params={**params, "offset": 4})
    ) == ["e"]
    assert _ids(
        client.get(
            "/api/memory/knowledge",
            params={"include_sensitive": True, "sort_by": "content", "order": "asc"},
        )
    ) == ["e", "a", "d", "c", "b"]
    assert _ids(
        client.get(
            "/api/memory/knowledge",
            params={"include_sensitive": True, "order": "asc"},
        )
    ) == ["e", "d", "c", "b", "a"]
    # A column gaiacpp doesn't have leaves the newest first.
    assert _ids(
        client.get(
            "/api/memory/knowledge",
            params={"include_sensitive": True, "sort_by": "confidence"},
        )
    ) == ["a", "b", "c", "d", "e"]


def test_a_store_larger_than_the_row_limit_says_so(client, many):
    body = client.get("/api/memory/knowledge").json()
    assert body["truncated"] is False and body["store_total"] == 5
    many.rows = [_row(f"r{i}", f"fact {i}") for i in range(1000)]
    many.total = 1500
    body = client.get("/api/memory/knowledge", params={"limit": 10}).json()
    assert body["truncated"] is True
    assert body["store_total"] == 1500
    assert body["total"] == 1000
    stats = client.get("/api/memory/stats").json()
    assert stats["truncated"] is True and stats["knowledge"]["total"] == 1500
    assert stats["knowledge"]["oldest"] is None


def test_nothing_remembered_yet_is_an_empty_list(client, monkeypatch, acquired):
    _use(monkeypatch, acquired, "gaiacpp", FakeGaiacpp(exists=False))
    body = client.get("/api/memory/knowledge").json()
    assert body["items"] == [] and body["total"] == 0 and body["truncated"] is False
    assert client.get("/api/memory/stats").json()["knowledge"]["total"] == 0


def test_stats_count_gaiacpps_store_and_leave_untracked_figures_null(client, many):
    stats = client.get("/api/memory/stats").json()
    python_stats = memory_router_mod._store.get_stats()
    assert stats["agent"] == "gaiacpp"
    assert set(stats["knowledge"]) == set(python_stats["knowledge"])
    for section in ("conversations", "tools", "temporal", "procedures"):
        assert set(stats[section]) == set(python_stats[section]), section
        assert set(stats[section].values()) == {None}, section
    assert stats["knowledge"] == {
        "total": 5,
        "total_retrievals": None,
        "by_category": {
            "preference": 1,
            "fact": 1,
            "error": 1,
            "note": 1,
            "reminder": 1,
        },
        "by_context": {"global": 4, "health": 1},
        "sensitive_count": 1,
        "entity_count": 1,
        "avg_confidence": None,
        "oldest": "2026-10-01T10:00:00Z",
        "newest": "2026-11-01T00:00:00Z",
    }
    assert stats["embedding"] is None and stats["db_size_bytes"] is None


def test_get_one_item_and_an_unknown_id(client, many):
    assert client.get("/api/memory/knowledge/d").json()["content"] == (
        "passport is in the desk"
    )
    missing = client.get("/api/memory/knowledge/zzz")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Knowledge entry zzz not found"


# -- changing gaiacpp's store ------------------------------------------------


def test_create_reaches_gaiacpp_and_leaves_the_python_store_alone(
    client, gaiacpp, python_store
):
    resp = client.post(
        "/api/memory/knowledge",
        json={
            "content": "Works night shifts",
            "category": "note",
            "sensitive": True,
            "entity": "person:user",
            "context": "work",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "created", "knowledge_id": "new-1"}
    assert gaiacpp.writes() == [
        (
            "create_memory",
            {
                "content": "Works night shifts",
                "category": "note",
                "sensitive": True,
                "entity": "person:user",
                "context": "work",
            },
        )
    ]
    assert _python_rows(python_store) == [PYTHON_FACT]


def test_create_with_the_default_context_sends_none(client, gaiacpp):
    assert (
        client.post("/api/memory/knowledge", json={"content": "x y z"}).status_code
        == 200
    )
    assert gaiacpp.writes()[0][1] == {
        "content": "x y z",
        "category": "fact",
        "sensitive": False,
        "entity": None,
        "context": None,
    }


def test_edit_and_delete_reach_gaiacpp_and_leave_the_python_store_alone(
    client, gaiacpp, python_store
):
    edited = client.put(
        "/api/memory/knowledge/c1", json={"content": "drinks green tea"}
    )
    assert edited.json() == {"status": "updated", "knowledge_id": "c1"}
    deleted = client.delete("/api/memory/knowledge/c1")
    assert deleted.json() == {"status": "deleted", "knowledge_id": "c1"}
    assert gaiacpp.writes() == [
        ("update_memory", {"id": "c1", "content": "drinks green tea"}),
        ("delete_memory", {"id": "c1"}),
    ]
    assert gaiacpp.rows == []
    assert _python_rows(python_store) == [PYTHON_FACT]


@pytest.mark.parametrize(
    "body, message",
    [
        (
            {
                "content": "dentist",
                "category": "reminder",
                "due_at": "2026-12-01T09:00:00",
            },
            "gaiacpp, the agent this PC is set to use, can't keep a due date on a "
            "memory. Save it without one, or switch agent under Settings › Agent "
            "& model.",
        ),
        (
            {"content": "is a nurse", "category": "profile"},
            "gaiacpp, the agent this PC is set to use, has no 'profile' memories. "
            "Save it as one of: fact, preference, error, skill, note, reminder.",
        ),
        (
            {"content": "is a nurse", "domain": "work"},
            "gaiacpp, the agent this PC is set to use, can't keep a domain on a "
            "memory. Save it without one.",
        ),
    ],
)
def test_create_refuses_what_gaiacpps_store_cannot_hold(client, gaiacpp, body, message):
    resp = client.post("/api/memory/knowledge", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"] == message
    assert gaiacpp.calls == []


def test_edit_refuses_anything_but_the_text(client, gaiacpp):
    resp = client.put(
        "/api/memory/knowledge/c1",
        json={"content": "tea", "sensitive": True, "category": "note"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "gaiacpp, the agent this PC is set to use, can only change a memory's "
        "text. It can't change: category, sensitive."
    )
    assert gaiacpp.calls == []


def test_gaiacpps_own_refusals_pass_through_with_their_reason(client, gaiacpp):
    for call in (
        lambda: client.put("/api/memory/knowledge/nope", json={"content": "x"}),
        lambda: client.delete("/api/memory/knowledge/nope"),
    ):
        resp = call()
        assert resp.status_code == 404
        assert resp.json()["detail"] == "No memory with id nope"
    gaiacpp.fail = None
    gaiacpp.create_memory = lambda content, **_f: (_ for _ in ()).throw(
        SidecarHTTPError(422, "That looks like a credential, so it was not saved.")
    )
    resp = client.post("/api/memory/knowledge", json={"content": "sk-abc123"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "That looks like a credential, so it was not saved."


def test_gaiacpp_that_cannot_start_is_a_503_and_never_the_python_store(
    client, monkeypatch, acquired, python_store
):
    monkeypatch.setattr(fetch, "saved_flagship", lambda: "gaiacpp")

    def _fail(_agent_id):
        raise SidecarError("spawn failed", kind=KIND_CAPACITY)

    monkeypatch.setattr(daemon_client, "acquire_handle", _fail)
    for resp in (
        client.get("/api/memory/knowledge"),
        client.get("/api/memory/stats"),
        client.post("/api/memory/knowledge", json={"content": "x"}),
        client.delete("/api/memory/knowledge/c1"),
    ):
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail.startswith(
            "gaiacpp, the agent this PC is set to use, could not be started, so "
            "its memory can't be shown or changed. "
        )
        assert "Open Memory again in a moment." in detail
        assert PYTHON_FACT not in resp.text
    assert _python_rows(python_store) == [PYTHON_FACT]


def test_gaiacpp_that_stops_answering_is_a_502_that_names_it(client, gaiacpp):
    gaiacpp.fail = SidecarError(
        "could not reach the gaia sidecar to list its memory: refused"
    )
    resp = client.get("/api/memory/knowledge")
    assert resp.status_code == 502
    assert resp.json()["detail"] == (
        "gaiacpp could not list its memory: could not reach the gaia sidecar "
        "to list its memory: refused"
    )


# -- an older gaiacpp: lists, cannot edit ------------------------------------


@pytest.fixture
def old_gaiacpp(monkeypatch, acquired):
    fake = FakeGaiacpp(
        [_row("c1", CPP_FACT, "preference"), _row("c2", "sister is Ana")],
        capabilities=None,
    )
    _use(monkeypatch, acquired, "gaiacpp", fake)
    return fake


def test_old_gaiacpp_lists_and_searches_read_only(client, old_gaiacpp):
    assert _ids(client.get("/api/memory/knowledge")) == ["c1", "c2"]
    # It ignores q and category, so both are applied here.
    assert _ids(
        client.get("/api/memory/knowledge", params={"search": "OOLONG tea"})
    ) == ["c1"]
    assert _ids(client.get("/api/memory/knowledge", params={"category": "fact"})) == [
        "c2"
    ]


def test_old_gaiacpp_refuses_every_edit_with_how_to_update(
    client, old_gaiacpp, python_store
):
    for resp in (
        client.post("/api/memory/knowledge", json={"content": "x y z"}),
        client.put("/api/memory/knowledge/c1", json={"content": "x y z"}),
        client.delete("/api/memory/knowledge/c1"),
    ):
        assert resp.status_code == 409
        assert resp.json()["detail"] == (
            "This gaiacpp is too old to change its memory from the app. Update "
            "gaiacpp under Settings › Agent & model, then try again."
        )
    assert old_gaiacpp.writes() == []
    assert len(old_gaiacpp.rows) == 2
    assert _python_rows(python_store) == [PYTHON_FACT]


def test_a_gaiacpp_without_one_capability_refuses_only_that_edit(
    client, monkeypatch, acquired
):
    fake = FakeGaiacpp([_row("c1", CPP_FACT)], capabilities=("search", "create"))
    _use(monkeypatch, acquired, "gaiacpp", fake)
    assert (
        client.post("/api/memory/knowledge", json={"content": "x y z"}).status_code
        == 200
    )
    assert client.delete("/api/memory/knowledge/c1").status_code == 409


# -- the Python-only tools ---------------------------------------------------

PYTHON_ONLY = [
    ("get", "/api/memory/activity", None, "activity timeline"),
    ("get", "/api/memory/entities", None, "list of the people and things it knows"),
    (
        "get",
        "/api/memory/entities/person:ana",
        None,
        "list of the people and things it knows",
    ),
    ("get", "/api/memory/tools", None, "tool history"),
    ("get", "/api/memory/conversations", None, "conversation log"),
    ("get", "/api/memory/conversations/search?query=tea", None, "conversation log"),
    ("get", "/api/memory/conversations/s1", None, "conversation log"),
    ("get", "/api/memory/upcoming", None, "due dates"),
    ("post", "/api/memory/consolidate", None, "conversation log to consolidate"),
    ("post", "/api/memory/rebuild-embeddings", None, "embeddings"),
    ("post", "/api/memory/reconcile", None, "embeddings to compare memories with"),
    ("get", "/api/memory/embedding-coverage", None, "embeddings"),
    ("post", "/api/memory/rebuild-fts", None, "search index to rebuild"),
    ("post", "/api/memory/prune", None, "tool history or conversation log to prune"),
    ("get", "/api/memory/stream-discovery", None, "profile discovery"),
    ("get", "/api/memory/stream-inference", None, "profile discovery"),
    ("post", "/api/memory/commit-discovery", {"items": []}, "profile discovery"),
    ("post", "/api/memory/commit-inference", {"items": []}, "profile discovery"),
    ("post", "/api/memory/admin/clear", {"scope": "all"}, "eval reset tools"),
    ("post", "/api/memory/admin/seed", {"items": []}, "eval reset tools"),
    ("delete", "/api/memory/all", None, "erase-everything action"),
    ("post", "/api/memory/reinitialize", None, "start-over action"),
    ("post", "/api/memory/refresh-system-context", None, "facts about this PC"),
]


@pytest.mark.parametrize("method, path, body, thing", PYTHON_ONLY)
def test_python_only_tools_refuse_while_gaiacpp_is_the_agent(
    client, monkeypatch, acquired, python_store, method, path, body, thing
):
    monkeypatch.setenv("GAIA_MEMORY_ADMIN", "1")
    _use(monkeypatch, acquired, "gaiacpp")  # no fake: a daemon call would fail
    resp = client.request(method, path, json=body)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == (
        "This works on the Python agent's memory. gaiacpp, the agent this PC is "
        f"set to use, keeps its own memory, which has no {thing}. Switch agent "
        "under Settings › Agent & model to use it."
    )
    assert _python_rows(python_store) == [PYTHON_FACT]


def test_every_python_store_route_is_either_served_for_gaiacpp_or_guarded():
    guarded = {(m, p.split("?")[0]) for m, p, _b, _t in PYTHON_ONLY}
    guarded = {
        (m, p.replace("person:ana", "{entity}").replace("/s1", "/{session_id}"))
        for m, p in guarded
    }
    both_agents = {
        ("get", "/api/memory/store"),
        ("get", "/api/memory/stats"),
        ("get", "/api/memory/knowledge"),
        ("post", "/api/memory/knowledge"),
        ("put", "/api/memory/knowledge/{knowledge_id}"),
        ("delete", "/api/memory/knowledge/{knowledge_id}"),
        ("get", "/api/memory/knowledge/{knowledge_id}"),
        ("get", "/api/memory/embedder"),
        ("put", "/api/memory/embedder"),
        ("get", "/api/memory/settings"),
        ("put", "/api/memory/settings"),
    }
    routes = {
        (method.lower(), route.path)
        for route in memory_router_mod.router.routes
        for method in route.methods
    }
    assert routes == guarded | both_agents


def test_memory_settings_work_while_gaiacpp_is_the_agent(client, monkeypatch, acquired):
    _use(monkeypatch, acquired, "gaiacpp")  # no fake: a daemon call would fail
    assert client.get("/api/memory/settings").status_code == 200
    put = client.put("/api/memory/settings", json={"memory_enabled": False})
    assert put.status_code == 200, put.text
    assert client.get("/api/memory/settings").json()["memory_enabled"] is False


def test_python_only_tools_still_work_while_gaiapy_is_the_agent(
    client, monkeypatch, acquired
):
    _use(monkeypatch, acquired, "gaiapy")
    assert client.get("/api/memory/entities").status_code == 200
    assert client.get("/api/memory/upcoming").status_code == 200
    assert client.post("/api/memory/rebuild-fts").json() == {"status": "rebuilt"}
