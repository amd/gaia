# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the ``/host/v1/*`` custody API (issue #2153, V2-12).

Covers the store (per-agent scoping at the data layer), the secret→agent-id
auth binding, the HTTP boundary (agent A cannot read agent B — real app
instance, not mocks; 403 on missing/wrong secret), the sidecar-side
``CustodyProvider`` adapters, and the create_app / manager / registry wiring.

Every test that touches on-disk custody state uses tmp_path so it never touches
the real ~/.gaia/host.
"""

from __future__ import annotations

import pytest

from gaia.daemon.custody.auth import CustodyAuth
from gaia.daemon.custody.errors import (
    AuditConflictError,
    InvalidScopeError,
    ScopeDeniedError,
    SessionNotFoundError,
    UnknownSecretError,
)
from gaia.daemon.custody.store import CustodyStore


@pytest.fixture()
def store(tmp_path):
    s = CustodyStore(tmp_path / "custody.db")
    yield s
    s.close()


# ===========================================================================
# constants — the custody API carries its own MAJOR, independent of /daemon/v1
# ===========================================================================


def test_host_api_version_is_independent_of_daemon_api_version():
    from gaia.daemon.constants import DAEMON_API_VERSION
    from gaia.daemon.custody.constants import HOST_API_PREFIX, HOST_API_VERSION

    assert HOST_API_PREFIX == "/host/v1"
    assert int(HOST_API_VERSION.split(".")[0]) == 1
    # It is a SEPARATE surface — not slaved to the client API version.
    assert HOST_API_VERSION is not DAEMON_API_VERSION


# ===========================================================================
# CustodyStore — per-agent scoping at the data layer
# ===========================================================================


def test_memory_roundtrip_and_scoping(store):
    store.add_memory("agent-a", "a's secret note")
    store.add_memory("agent-b", "b's private note")

    a_items = store.get_memory("agent-a")
    b_items = store.get_memory("agent-b")

    assert [i["content"] for i in a_items] == ["a's secret note"]
    assert [i["content"] for i in b_items] == ["b's private note"]
    # A's read never surfaces B's row and vice-versa.
    assert all("b's private" not in i["content"] for i in a_items)


def test_memory_query_substring_filters_within_agent(store):
    store.add_memory("agent-a", "buy milk")
    store.add_memory("agent-a", "call dentist")
    hits = store.get_memory("agent-a", query="milk")
    assert [i["content"] for i in hits] == ["buy milk"]


def test_memory_invalid_scope_raises(store):
    with pytest.raises(InvalidScopeError):
        store.add_memory("agent-a", "x", scope="bogus")


def test_session_owner_can_read_transcript(store):
    store.create_session("agent-a", "sess-1")
    store.append_session_message("agent-a", "sess-1", "user", "hello")
    store.append_session_message("agent-a", "sess-1", "assistant", "hi")
    transcript = store.get_session("agent-a", "sess-1")
    assert [m["content"] for m in transcript] == ["hello", "hi"]
    assert [m["seq"] for m in transcript] == [0, 1]


def test_session_cross_agent_read_is_scope_denied(store):
    store.create_session("agent-a", "sess-1")
    with pytest.raises(ScopeDeniedError):
        store.get_session("agent-b", "sess-1")


def test_session_cross_agent_write_is_scope_denied(store):
    store.create_session("agent-a", "sess-1")
    with pytest.raises(ScopeDeniedError):
        store.append_session_message("agent-b", "sess-1", "user", "sneaky")


def test_session_unknown_id_is_not_found(store):
    with pytest.raises(SessionNotFoundError):
        store.get_session("agent-a", "nope")


def test_audit_append_returns_monotonic_seq(store):
    s1 = store.append_audit("agent-a", "act-1", "send", "sent email", 1.0)
    s2 = store.append_audit("agent-a", "act-2", "archive", "archived", 2.0)
    assert s2 > s1


def test_audit_duplicate_action_id_conflicts(store):
    store.append_audit("agent-a", "act-1", "send", "x", 1.0)
    with pytest.raises(AuditConflictError):
        store.append_audit("agent-a", "act-1", "send", "x again", 2.0)


def test_audit_is_scoped_per_agent(store):
    store.append_audit("agent-a", "act-1", "send", "a", 1.0)
    store.append_audit("agent-b", "act-1", "send", "b", 1.0)  # same id, other agent OK
    assert len(store.get_audit("agent-a")) == 1
    assert len(store.get_audit("agent-b")) == 1


def test_audit_rows_survive_store_reopen(tmp_path):
    """Audit rows survive a sidecar uninstall (acceptance criterion): the store
    is daemon-owned, so closing/reopening it (as a restart would) preserves the
    log."""
    db = tmp_path / "custody.db"
    s1 = CustodyStore(db)
    s1.append_audit("agent-a", "act-1", "send", "durable", 1.0)
    s1.close()

    s2 = CustodyStore(db)
    rows = s2.get_audit("agent-a")
    s2.close()
    assert [r["action_id"] for r in rows] == ["act-1"]


def test_rag_query_is_scoped_per_agent(store):
    store.add_rag_chunk("agent-a", "the mitochondria is the powerhouse")
    store.add_rag_chunk("agent-b", "b private corpus about mitochondria")
    hits = store.query_rag("agent-a", "mitochondria")
    assert len(hits) == 1
    assert "powerhouse" in hits[0]["content"]


def test_store_uses_wal_journal_mode(store):
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


# ===========================================================================
# CustodyAuth — secret→agent-id binding at mint, resolve per request
# ===========================================================================


def test_mint_then_resolve_roundtrip():
    auth = CustodyAuth()
    secret = auth.mint("agent-a")
    assert auth.resolve(secret) == "agent-a"


def test_resolve_unknown_secret_raises():
    auth = CustodyAuth()
    with pytest.raises(UnknownSecretError):
        auth.resolve("never-minted")


def test_resolve_empty_secret_raises():
    auth = CustodyAuth()
    with pytest.raises(UnknownSecretError):
        auth.resolve("")


def test_two_agents_get_distinct_secrets_resolving_to_themselves():
    auth = CustodyAuth()
    sa = auth.mint("agent-a")
    sb = auth.mint("agent-b")
    assert sa != sb
    assert auth.resolve(sa) == "agent-a"
    assert auth.resolve(sb) == "agent-b"


def test_remint_rotates_and_invalidates_old_secret():
    auth = CustodyAuth()
    old = auth.mint("agent-a")
    new = auth.mint("agent-a")
    assert old != new
    assert auth.resolve(new) == "agent-a"
    with pytest.raises(UnknownSecretError):
        auth.resolve(old)


def test_revoke_invalidates_secret():
    auth = CustodyAuth()
    secret = auth.mint("agent-a")
    auth.revoke("agent-a")
    with pytest.raises(UnknownSecretError):
        auth.resolve(secret)
    auth.revoke("agent-a")  # idempotent — no raise


# ===========================================================================
# HTTP boundary — real app instance (TestClient), the acceptance criteria
# ===========================================================================


@pytest.fixture()
def custody_client(tmp_path):
    """A real FastAPI app with the custody API mounted, plus two pre-bound
    agents' secrets. Returns (client, secret_a, secret_b)."""
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    store = CustodyStore(tmp_path / "custody.db")
    auth = CustodyAuth()
    secret_a = auth.mint("agent-a")
    secret_b = auth.mint("agent-b")

    app = create_app(
        token="client-tok",
        port=4001,
        pid=1,
        started_at=1.0,
        custody_auth=auth,
        custody_store=store,
    )
    client = TestClient(app, raise_server_exceptions=False)
    yield client, secret_a, secret_b
    store.close()


def _h(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_http_missing_secret_is_403(custody_client):
    client, _, _ = custody_client
    r = client.get("/host/v1/memory")
    assert r.status_code == 403
    assert "secret" in r.json()["detail"].lower()


def test_http_wrong_secret_is_403(custody_client):
    client, _, _ = custody_client
    r = client.get("/host/v1/memory", headers=_h("bogus-secret"))
    assert r.status_code == 403


def test_http_malformed_auth_header_is_403(custody_client):
    client, secret_a, _ = custody_client
    r = client.get("/host/v1/memory", headers={"Authorization": secret_a})  # no scheme
    assert r.status_code == 403


def test_http_memory_scoped_to_caller(custody_client):
    client, secret_a, secret_b = custody_client
    client.post("/host/v1/memory", headers=_h(secret_a), json={"item": "a note"})
    client.post("/host/v1/memory", headers=_h(secret_b), json={"item": "b note"})

    a_items = client.get("/host/v1/memory", headers=_h(secret_a)).json()["items"]
    b_items = client.get("/host/v1/memory", headers=_h(secret_b)).json()["items"]
    assert [i["content"] for i in a_items] == ["a note"]
    assert [i["content"] for i in b_items] == ["b note"]


def test_http_agent_a_cannot_read_agent_b_session_403(custody_client):
    """The headline acceptance criterion: A cannot read B's session → 403."""
    client, secret_a, secret_b = custody_client
    sid = client.post("/host/v1/sessions", headers=_h(secret_b), json={}).json()[
        "session_id"
    ]
    client.post(
        f"/host/v1/sessions/{sid}/messages",
        headers=_h(secret_b),
        json={"role": "user", "content": "b's private message"},
    )
    # Owner reads fine.
    owner = client.get(f"/host/v1/sessions/{sid}", headers=_h(secret_b))
    assert owner.status_code == 200
    assert owner.json()["transcript"][0]["content"] == "b's private message"
    # A cannot.
    intruder = client.get(f"/host/v1/sessions/{sid}", headers=_h(secret_a))
    assert intruder.status_code == 403


def test_http_unknown_session_is_404(custody_client):
    client, secret_a, _ = custody_client
    r = client.get("/host/v1/sessions/does-not-exist", headers=_h(secret_a))
    assert r.status_code == 404


def test_http_audit_scoped_and_conflict_409(custody_client):
    client, secret_a, _ = custody_client
    r1 = client.post(
        "/host/v1/audit",
        headers=_h(secret_a),
        json={"action_id": "a1", "action": "send", "summary": "s", "ts": 1.0},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/host/v1/audit",
        headers=_h(secret_a),
        json={"action_id": "a1", "action": "send", "ts": 2.0},
    )
    assert r2.status_code == 409


def test_http_rag_query_scoped(custody_client):
    client, secret_a, secret_b = custody_client
    # Seed via the store directly is not exposed over HTTP (no ingest route in
    # v1), so assert the empty-but-scoped contract: a query returns a list.
    r = client.post("/host/v1/rag/query", headers=_h(secret_a), json={"query": "x"})
    assert r.status_code == 200
    assert r.json()["chunks"] == []


def test_http_version_route_is_unauthenticated(custody_client):
    client, _, _ = custody_client
    r = client.get("/host/v1/version")
    assert r.status_code == 200
    assert int(r.json()["apiVersion"].split(".")[0]) == 1


def test_create_app_requires_both_custody_args_or_neither(tmp_path):
    from gaia.daemon.app import create_app

    with pytest.raises(ValueError):
        create_app(
            token="t",
            port=4001,
            pid=1,
            started_at=1.0,
            custody_auth=CustodyAuth(),  # store missing
        )


def test_create_app_without_custody_does_not_mount_host_routes():
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    app = create_app(token="t", port=4001, pid=1, started_at=1.0)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/host/v1/version").status_code == 404


# ===========================================================================
# CustodyProvider — sidecar-side adapters (§0.37)
# ===========================================================================


def test_embedded_provider_roundtrip(store):
    from gaia.daemon.custody.provider import EmbeddedCustodyProvider

    p = EmbeddedCustodyProvider(store, "agent-a")
    mid = p.add_memory("remember this")
    assert isinstance(mid, int)
    assert [i["content"] for i in p.get_memory()] == ["remember this"]

    sid = p.create_session()
    p.append_session_message(sid, "user", "hi")
    assert [m["content"] for m in p.get_session(sid)] == ["hi"]

    p.append_audit("act-1", "send", "sent")
    assert store.get_audit("agent-a")[0]["action_id"] == "act-1"


def test_embedded_provider_is_isolated_per_agent(store):
    from gaia.daemon.custody.provider import EmbeddedCustodyProvider

    a = EmbeddedCustodyProvider(store, "agent-a")
    b = EmbeddedCustodyProvider(store, "agent-b")
    a.add_memory("a only")
    assert a.get_memory()
    assert b.get_memory() == []


def test_ephemeral_provider_persists_nothing():
    from gaia.daemon.custody.provider import EphemeralCustodyProvider

    p = EphemeralCustodyProvider()
    assert p.add_memory("ignored") is None
    assert p.get_memory() == []
    sid = p.create_session()
    assert p.append_session_message(sid, "user", "x") is None
    assert p.get_session(sid) == []
    assert p.query_rag("x") == []
    assert p.append_audit("a", "b") is None


def test_delegated_provider_roundtrip_against_real_app(custody_client, monkeypatch):
    """The delegated adapter drives the real /host/v1 routes end-to-end — the
    embedded and delegated adapters must behave identically (§0.37 conformance).

    Route httpx.request through the TestClient so no socket server is needed.
    """
    import httpx

    from gaia.daemon.custody import provider as provider_mod

    client, secret_a, secret_b = custody_client

    def _via_testclient(method, url, headers=None, timeout=None, **kwargs):
        path = url.split("http://custody", 1)[1]
        return client.request(method, path, headers=headers, **kwargs)

    monkeypatch.setattr(httpx, "request", _via_testclient)

    pa = provider_mod.DelegatedCustodyProvider("http://custody", secret_a)
    pb = provider_mod.DelegatedCustodyProvider("http://custody", secret_b)

    pa.add_memory("a note")
    pb.add_memory("b note")
    assert [i["content"] for i in pa.get_memory()] == ["a note"]
    assert [i["content"] for i in pb.get_memory()] == ["b note"]

    sid = pa.create_session()
    pa.append_session_message(sid, "user", "hello")
    assert [m["content"] for m in pa.get_session(sid)] == ["hello"]

    # Cross-agent read raises a loud request error (403), never a silent empty.
    with pytest.raises(provider_mod.CustodyRequestError) as exc:
        pb.get_session(sid)
    assert exc.value.status_code == 403


def test_select_provider_prefers_ephemeral_flag():
    from gaia.daemon.custody.provider import (
        EphemeralCustodyProvider,
        select_custody_provider,
    )

    env = {"GAIA_CUSTODY_EPHEMERAL": "1", "GAIA_HOST_CUSTODY_URL": "http://x"}
    p = select_custody_provider("agent-a", env=env)
    assert isinstance(p, EphemeralCustodyProvider)


def test_select_provider_delegated_when_url_present():
    from gaia.daemon.custody.provider import (
        DelegatedCustodyProvider,
        select_custody_provider,
    )

    env = {
        "GAIA_HOST_CUSTODY_URL": "http://127.0.0.1:5000",
        "GAIA_HOST_CUSTODY_SECRET": "s",
    }
    p = select_custody_provider("agent-a", env=env)
    assert isinstance(p, DelegatedCustodyProvider)


def test_select_provider_url_without_secret_fails_loud():
    from gaia.daemon.custody.provider import select_custody_provider

    env = {"GAIA_HOST_CUSTODY_URL": "http://127.0.0.1:5000"}
    with pytest.raises(ValueError):
        select_custody_provider("agent-a", env=env)


def test_select_provider_embedded_default(store):
    from gaia.daemon.custody.provider import (
        EmbeddedCustodyProvider,
        select_custody_provider,
    )

    p = select_custody_provider("agent-a", embedded_store=store, env={})
    assert isinstance(p, EmbeddedCustodyProvider)


def test_select_provider_no_url_no_store_fails_loud():
    from gaia.daemon.custody.provider import select_custody_provider

    with pytest.raises(ValueError):
        select_custody_provider("agent-a", env={})


# ===========================================================================
# Registry / manager wiring — secret bound at mint, injected on spawn
# ===========================================================================


def test_registry_mints_custody_secret_at_manager_construction():
    from gaia.daemon.sidecars.registry import SidecarRegistry
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    spec = AgentSidecarSpec(
        agent_id="toy-a",
        service_id="gaia-agent-toy-a",
        display_name="Toy A",
        expected_api_major="1",
        token_env_var="GAIA_TOY_A_SIDECAR_TOKEN",
        mode_env_var="GAIA_TOY_A_AGENT_MODE",
        cache_dir_name="toy-a",
    )
    auth = CustodyAuth()

    captured = {}

    class _FakeManager:
        def __init__(self, spec, mode=None, **kwargs):
            self.spec = spec
            self.custody_url = None
            self.custody_secret = None
            self.on_process_spawned = None
            self.on_process_reaped = None

    reg = SidecarRegistry(
        {"toy-a": spec},
        custody_auth=auth,
        custody_base_url="http://127.0.0.1:4321",
    )
    reg._manager_factory = _FakeManager
    manager = reg._new_manager("toy-a", spec, None)
    captured["url"] = manager.custody_url
    captured["secret"] = manager.custody_secret

    assert captured["url"] == "http://127.0.0.1:4321"
    assert captured["secret"]
    # The minted secret resolves back to this agent id.
    assert auth.resolve(captured["secret"]) == "toy-a"


def test_manager_injects_custody_env_on_spawn(tmp_path, monkeypatch):
    from gaia.daemon.custody.constants import (
        CUSTODY_SECRET_ENV_VAR,
        CUSTODY_URL_ENV_VAR,
    )
    from gaia.daemon.sidecars import manager as mgr_mod
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    src = tmp_path / "toy-src"
    (src / "packaging").mkdir(parents=True, exist_ok=True)
    spec = AgentSidecarSpec(
        agent_id="toy-a",
        service_id="gaia-agent-toy-a",
        display_name="Toy A",
        expected_api_major="1",
        token_env_var="GAIA_TOY_A_SIDECAR_TOKEN",
        mode_env_var="GAIA_TOY_A_AGENT_MODE",
        cache_dir_name="toy-a",
        dev_src_dir=src,
    )
    monkeypatch.setenv(spec.mode_env_var, "dev")

    captured_env = {}

    class _Proc:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    def _fake_popen(argv, env=None, **kw):
        captured_env.update(env or {})
        return _Proc()

    monkeypatch.setattr(mgr_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr_mod.atexit, "register", lambda fn: None)

    m = mgr_mod.AgentSidecarManager(spec, cache_dir=tmp_path, log_dir=tmp_path / "logs")
    m.custody_url = "http://127.0.0.1:4321"
    m.custody_secret = "the-custody-secret"
    argv, popen_kwargs = m.build_spawn_command(port=55123)
    m._spawn_process(argv, popen_kwargs, 55123)

    assert captured_env[CUSTODY_URL_ENV_VAR] == "http://127.0.0.1:4321"
    assert captured_env[CUSTODY_SECRET_ENV_VAR] == "the-custody-secret"
