# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's Agent Hub control plane: ``/daemon/v1/catalog``,
``/daemon/v1/agents/{id}/install``, ``.../install-status`` and
``DELETE /daemon/v1/agents/{id}``.

Every test runs from the COLD state — an empty install root with no
``~/.gaia/agents/<id>/`` at all — because that is the state a new user is in and
the one a warm dev box hides. The hub is exercised through a real fixture
origin (a ``file://`` GAIA_HUB_URL serving genuine index/manifest JSON) rather
than a stubbed installer, so the assertions cover the SHAPE of what gets
requested and downloaded, not merely that a mock was called.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from gaia.daemon.sidecars import install as install_svc

# ---------------------------------------------------------------------------
# Fixture hub origin
# ---------------------------------------------------------------------------

BINARY_BYTES = b"fake-frozen-email-agent-binary"
BINARY_SHA = hashlib.sha256(BINARY_BYTES).hexdigest()
PLATFORM_KEY = "darwin-arm64"
ARTIFACT_NAME = f"email-agent-{PLATFORM_KEY}"


def _index(agent_ids=("email",)) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-07-16T17:28:33.873Z",
        "agents": [
            {
                "id": aid,
                "name": f"{aid.title()} Agent",
                "description": f"the {aid} agent",
                "latest_version": "0.5.0",
                "language": "python",
                "security_tier": "experimental",
                "download_size_bytes": 32578800,
            }
            for aid in agent_ids
        ],
    }


def _manifest(agent_id="email", sha=BINARY_SHA) -> dict:
    return {
        "id": agent_id,
        "name": "Email Triage",
        "language": "python",
        "latest_version": "0.5.0",
        "security_tier": "experimental",
        "versions": {
            "0.5.0": {
                "version": "0.5.0",
                "artifact": {
                    "filename": ARTIFACT_NAME,
                    "path": f"agents/{agent_id}/0.5.0/{ARTIFACT_NAME}",
                    "size_bytes": len(BINARY_BYTES),
                    "sha256": sha,
                },
                "artifacts": [
                    {
                        "filename": ARTIFACT_NAME,
                        "path": f"agents/{agent_id}/0.5.0/{ARTIFACT_NAME}",
                        "size_bytes": len(BINARY_BYTES),
                        "sha256": sha,
                    }
                ],
            }
        },
    }


class _RecordingFetcher:
    """A hub fetcher that records every URL it is asked for.

    Asserting on ``calls`` is how these tests check the outgoing request SHAPE
    (which manifest, which artifact path) instead of only that "something was
    fetched".
    """

    def __init__(self, files: dict):
        self.files = files
        self.calls: list = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        for suffix, payload in self.files.items():
            if url.endswith(suffix):
                return payload
        raise RuntimeError(f"404 fixture-hub has no object for {url}")


def _hub_files(*, agent_id="email", sha=BINARY_SHA, index_ids=("email",)) -> dict:
    return {
        "/index.json": json.dumps(_index(index_ids)).encode(),
        f"/agents/{agent_id}/manifest.json": json.dumps(
            _manifest(agent_id, sha)
        ).encode(),
        f"/agents/{agent_id}/0.5.0/{ARTIFACT_NAME}": BINARY_BYTES,
        f"/agents/{agent_id}/0.5.0/gaia-agent.yaml": b"id: email\nversion: 0.5.0\n",
    }


# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------


class _FakeRegistry:
    """Sidecar registry stub mirroring the real ``hold_for_mutation`` contract:
    stop + verify on entry, ensure blocked for the body."""

    def __init__(self, agent_ids=("email",), *, stop_error=None):
        self._agent_ids = list(agent_ids)
        self._stop_error = stop_error
        self.stop_calls: list = []
        self.held: list = []

    def list_agents(self):
        return [{"agent_id": aid, "state": "stopped"} for aid in self._agent_ids]

    def stop(self, agent_id):
        self.stop_calls.append(agent_id)
        if agent_id not in self._agent_ids:
            from gaia.daemon.sidecars.errors import UnknownAgentError

            raise UnknownAgentError(f"unknown agent '{agent_id}'")
        if self._stop_error is not None:
            raise self._stop_error
        return {"agent_id": agent_id, "state": "stopped"}

    @contextmanager
    def hold_for_mutation(self, agent_id):
        self.stop(agent_id)
        self.held.append(agent_id)
        try:
            yield
        finally:
            self.held.remove(agent_id)


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """Cold state: empty install root, no catalog cache, no progress carried in."""
    from gaia.hub import catalog as catalog_mod
    from gaia.hub import installer as installer_mod

    root = tmp_path / "agents"
    monkeypatch.setattr(installer_mod, "default_install_root", lambda: root)
    monkeypatch.setattr(
        catalog_mod, "default_cache_path", lambda: tmp_path / "catalog-cache.json"
    )
    catalog_mod.clear_cache()
    installer_mod.clear_progress()
    install_svc._QUEUED.clear()
    yield root
    catalog_mod.clear_cache()
    installer_mod.clear_progress()
    install_svc._QUEUED.clear()


@pytest.fixture()
def install_root(_clean_state):
    return _clean_state


def _client(registry, token="secret-tok"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.sidecars.routes import build_agents_router

    app = FastAPI()
    app.include_router(build_agents_router(token, registry))
    return TestClient(app, raise_server_exceptions=False)


def _auth(token="secret-tok"):
    return {"Authorization": f"Bearer {token}"}


def _patch_hub(monkeypatch, fetcher):
    """Route every hub read in the install service through *fetcher*."""
    real_start = install_svc.start_install
    real_catalog = install_svc.build_catalog

    def _catalog(**kwargs):
        kwargs.setdefault("fetcher", fetcher)
        return real_catalog(**kwargs)

    def _start(agent_id, **kwargs):
        kwargs.setdefault("fetcher", fetcher)
        return real_start(agent_id, **kwargs)

    monkeypatch.setattr(install_svc, "build_catalog", _catalog)
    monkeypatch.setattr(install_svc, "start_install", _start)
    # Route tests want the worker to finish before the assertions: run it inline.
    monkeypatch.setattr(install_svc, "_spawn_thread", lambda work: work())
    # Binary selection is platform-dependent; pin the host key so the fixture
    # artifact matches on every CI runner.
    monkeypatch.setattr(
        "gaia.hub.installer.current_platform_key", lambda *a, **k: PLATFORM_KEY
    )


# ===========================================================================
# Auth — every new route is behind the daemon client token
# ===========================================================================


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/daemon/v1/catalog"),
        ("post", "/daemon/v1/agents/email/install"),
        ("get", "/daemon/v1/agents/email/install-status"),
        ("delete", "/daemon/v1/agents/email"),
    ],
)
def test_hub_routes_require_a_token(method, url):
    client = _client(_FakeRegistry())
    r = getattr(client, method)(url)
    assert r.status_code == 401
    assert "Authorization" in r.json()["detail"]


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/daemon/v1/catalog"),
        ("post", "/daemon/v1/agents/email/install"),
        ("get", "/daemon/v1/agents/email/install-status"),
        ("delete", "/daemon/v1/agents/email"),
    ],
)
def test_hub_routes_reject_a_wrong_token(method, url):
    client = _client(_FakeRegistry())
    r = getattr(client, method)(url, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ===========================================================================
# Catalog
# ===========================================================================


def test_catalog_returns_hub_agents_with_cold_installed_state(monkeypatch):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    r = client.get("/daemon/v1/catalog", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    entry = body["agents"][0]
    assert entry["id"] == "email"
    assert entry["installed"] is False
    assert entry["installed_version"] is None
    assert entry["supervised"] is True
    assert body["offline"] is False
    # Shape of the outgoing call: the hub INDEX, not a per-agent manifest.
    assert fetcher.calls == [f"{_hub_base()}/index.json"]


def _hub_base():
    from gaia.hub import catalog as catalog_mod

    return catalog_mod.get_hub_base_url()


def test_catalog_reports_installed_version_from_the_sentinel(monkeypatch, install_root):
    _write_sentinel(install_root, "email", "0.4.0")
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    entry = client.get("/daemon/v1/catalog", headers=_auth()).json()["agents"][0]
    assert entry["installed"] is True
    assert entry["installed_version"] == "0.4.0"
    assert entry["update_available"] is True


def test_catalog_filters_agents_the_daemon_cannot_supervise(monkeypatch):
    fetcher = _RecordingFetcher(_hub_files(index_ids=("email", "spreadsheet")))
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry(agent_ids=("email",)))

    body = client.get("/daemon/v1/catalog", headers=_auth()).json()
    assert [a["id"] for a in body["agents"]] == ["email"]
    # Filtered, never silently: the client can say WHY it is missing.
    assert body["unsupervised_filtered"] == ["spreadsheet"]


def test_catalog_include_unsupervised_shows_them_flagged(monkeypatch):
    fetcher = _RecordingFetcher(_hub_files(index_ids=("email", "spreadsheet")))
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry(agent_ids=("email",)))

    body = client.get(
        "/daemon/v1/catalog?include_unsupervised=true", headers=_auth()
    ).json()
    flags = {a["id"]: a["supervised"] for a in body["agents"]}
    assert flags == {"email": True, "spreadsheet": False}


def test_catalog_hides_deprecated_agents_nobody_installed(monkeypatch, install_root):
    files = _hub_files(index_ids=("email",))
    index = _index(("email",))
    index["agents"][0]["deprecated"] = True
    files["/index.json"] = json.dumps(index).encode()
    _patch_hub(monkeypatch, _RecordingFetcher(files))
    client = _client(_FakeRegistry())

    assert client.get("/daemon/v1/catalog", headers=_auth()).json()["agents"] == []

    # An installed one stays visible so it can be seen and removed.
    _write_sentinel(install_root, "email", "0.5.0")
    entry = client.get("/daemon/v1/catalog", headers=_auth()).json()["agents"][0]
    assert entry["id"] == "email" and entry["installed"] is True


def test_catalog_installed_only_answers_without_the_network(monkeypatch, install_root):
    """`gaia hub list --installed` is a local question and must not 503 offline."""
    _write_sentinel(install_root, "email", "0.5.0")

    def _boom(url, timeout=10):
        raise AssertionError("installed_only must not touch the network")

    monkeypatch.setattr("gaia.hub.catalog.fetch_bytes", _boom)
    client = _client(_FakeRegistry())

    body = client.get("/daemon/v1/catalog?installed_only=true", headers=_auth()).json()
    assert body["source"] == "local"
    assert [a["id"] for a in body["agents"]] == ["email"]
    assert body["agents"][0]["installed_version"] == "0.5.0"
    assert body["agents"][0]["supervised"] is True


def test_catalog_unreachable_hub_without_cache_is_503(monkeypatch):
    """Cold state (no on-disk cache) + no network = a loud 503, never an empty list."""

    def _boom(url, timeout=10):
        raise RuntimeError("network down")

    monkeypatch.setattr("gaia.hub.catalog.fetch_bytes", _boom)
    client = _client(_FakeRegistry())

    r = client.get("/daemon/v1/catalog", headers=_auth())
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "Hub" in detail and "network down" in detail


# ===========================================================================
# Install
# ===========================================================================


def _write_sentinel(install_root: Path, agent_id: str, version: str) -> Path:
    d = install_root / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / ".installed").write_text(
        json.dumps(
            {
                "id": agent_id,
                "version": version,
                "language": "python",
                "installed_at": "2026-07-01T00:00:00+00:00",
                "artifact_sha256": BINARY_SHA,
                "artifact_kind": "binary",
                "executable": "email-agent",
            }
        ),
        encoding="utf-8",
    )
    return d


def test_install_queues_and_reaches_completed(monkeypatch, install_root):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    r = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert r.status_code == 202
    assert r.json()["agent_id"] == "email"
    assert r.json()["status"] == "queued"

    status = client.get(
        "/daemon/v1/agents/email/install-status", headers=_auth()
    ).json()
    assert status["agent_id"] == "email"
    assert status["status"] == "completed"
    assert status["version"] == "0.5.0"
    assert status["error"] is None

    # The on-disk layout the daemon re-verifies before every spawn.
    sentinel = json.loads((install_root / "email" / ".installed").read_text())
    assert sentinel["artifact_kind"] == "binary"
    assert sentinel["artifact_sha256"] == BINARY_SHA
    assert sentinel["executable"] == "email-agent"
    assert (install_root / "email" / "email-agent").read_bytes() == BINARY_BYTES
    assert (install_root / "email" / "gaia-agent.yaml").exists()

    # Outgoing call shape: manifest, then the version's artifact path, then yaml.
    base = _hub_base()
    assert fetcher.calls == [
        f"{base}/agents/email/manifest.json",
        f"{base}/agents/email/0.5.0/{ARTIFACT_NAME}",
        f"{base}/agents/email/0.5.0/gaia-agent.yaml",
    ]


def test_install_stops_a_running_sidecar_before_touching_the_dir(
    monkeypatch, install_root
):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    registry = _FakeRegistry()
    client = _client(registry)

    client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    # Once synchronously (so a survivor is a 500 on the request) and once around
    # the worker's mutation (so a mid-download ensure cannot respawn it).
    assert registry.stop_calls == ["email", "email"]
    assert registry.held == []  # released again afterwards


def test_install_aborts_when_the_sidecar_pid_survives(monkeypatch, install_root):
    from gaia.daemon.sidecars.errors import StopFailedError

    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    registry = _FakeRegistry(stop_error=StopFailedError("pid 4242 survived"))
    client = _client(registry)

    r = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert r.status_code == 500
    assert "4242" in r.json()["detail"]
    # Nothing downloaded, nothing written: the artifact was never requested.
    assert not any(ARTIFACT_NAME in c for c in fetcher.calls)
    assert not (install_root / "email").exists()


@pytest.mark.parametrize("bad_id", ["../evil", "..%2Fevil", "Email", "em ail"])
def test_mutating_routes_reject_a_malformed_agent_id(bad_id, install_root):
    """The id becomes a path segment under ~/.gaia/agents — a traversal attempt
    must be refused before it can reach the filesystem."""
    victim = install_root.parent / "evil"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / ".installed").write_text("{}", encoding="utf-8")
    client = _client(_FakeRegistry())

    assert (
        client.post(f"/daemon/v1/agents/{bad_id}/install", headers=_auth()).status_code
        == 404
    )
    assert (
        client.delete(f"/daemon/v1/agents/{bad_id}", headers=_auth()).status_code == 404
    )
    assert victim.exists()


def test_worker_failure_always_reaches_a_terminal_status(monkeypatch, install_root):
    """A poller must never hang on 'queued': an error the installer does not
    record itself (e.g. another process holds the install slot) is still turned
    into a terminal failed state carrying the reason."""
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    def _slot_taken(agent_id, **kwargs):
        raise install_svc._installer().InstallInProgressError(
            "An install for 'email' is already in progress."
        )

    monkeypatch.setattr("gaia.hub.installer.install", _slot_taken)
    client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})

    status = client.get(
        "/daemon/v1/agents/email/install-status", headers=_auth()
    ).json()
    assert status["status"] == "failed"
    assert "already in progress" in status["error"]


def test_install_sha_mismatch_is_a_hard_failure_leaving_nothing_installed(
    monkeypatch, install_root
):
    bad = "0" * 64
    fetcher = _RecordingFetcher(_hub_files(sha=bad))
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    r = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert r.status_code == 202

    status = client.get(
        "/daemon/v1/agents/email/install-status", headers=_auth()
    ).json()
    assert status["status"] == "failed"
    assert "checksum" in status["error"].lower()
    # No "use it anyway": no sentinel, no binary.
    assert not (install_root / "email" / ".installed").exists()
    assert not (install_root / "email" / "email-agent").exists()


def test_install_unknown_agent_is_404_listing_installable_ids(monkeypatch):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry(agent_ids=("email",)))

    r = client.post("/daemon/v1/agents/nope/install", headers=_auth(), json={})
    assert r.status_code == 404
    assert "email" in r.json()["detail"]
    # An unknown id must never reach the hub.
    assert fetcher.calls == []


def test_install_reserved_builtin_is_refused(monkeypatch):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry(agent_ids=("email", "builder")))

    r = client.post("/daemon/v1/agents/builder/install", headers=_auth(), json={})
    assert r.status_code == 400
    assert "built-in" in r.json()["detail"]
    assert fetcher.calls == []


def test_install_hub_manifest_failure_is_502(monkeypatch):
    # index.json resolves but the per-agent manifest 404s.
    files = _hub_files()
    del files["/agents/email/manifest.json"]
    fetcher = _RecordingFetcher(files)
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    r = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert r.status_code == 502
    assert "manifest" in r.json()["detail"].lower()


def test_install_status_404_before_any_install_was_requested():
    client = _client(_FakeRegistry())
    r = client.get("/daemon/v1/agents/email/install-status", headers=_auth())
    assert r.status_code == 404
    assert "install" in r.json()["detail"]


def test_concurrent_installs_of_the_same_id_serialize(monkeypatch, install_root):
    """The second POST while one is in flight is a loud 409, never a second
    worker racing the first one's download into the same directory."""
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    started = threading.Event()
    release = threading.Event()
    real_install = install_svc._installer().install

    def _blocking_install(agent_id, **kwargs):
        started.set()
        assert release.wait(5), "test deadlock: release never set"
        return real_install(agent_id, **kwargs)

    monkeypatch.setattr("gaia.hub.installer.install", _blocking_install)
    monkeypatch.setattr(
        install_svc,
        "_spawn_thread",
        lambda work: threading.Thread(target=work, daemon=True).start(),
    )

    first = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert first.status_code == 202
    assert started.wait(5), "install worker never started"

    second = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert second.status_code == 409
    assert "in progress" in second.json()["detail"]

    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = install_svc.install_status("email")
        if state and state["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert install_svc.install_status("email")["status"] == "completed"
    # Exactly one download happened.
    assert sum(1 for c in fetcher.calls if ARTIFACT_NAME in c) == 1


def test_install_arriving_during_an_uninstall_is_refused(monkeypatch, install_root):
    """Install and uninstall share the slot: a POST landing while a DELETE is
    removing the directory must 409, not queue a download into it."""
    _write_sentinel(install_root, "email", "0.5.0")
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    in_uninstall = threading.Event()
    release = threading.Event()
    real_uninstall = install_svc._installer().uninstall

    def _slow_uninstall(agent_id, **kwargs):
        in_uninstall.set()
        assert release.wait(5), "test deadlock: release never set"
        return real_uninstall(agent_id, **kwargs)

    monkeypatch.setattr("gaia.hub.installer.uninstall", _slow_uninstall)
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            code=client.delete("/daemon/v1/agents/email", headers=_auth()).status_code
        ),
        daemon=True,
    )
    t.start()
    assert in_uninstall.wait(5), "uninstall never started"

    r = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert r.status_code == 409
    assert "in progress" in r.json()["detail"]
    # Nothing was downloaded into the directory being removed.
    assert not any(ARTIFACT_NAME in c for c in fetcher.calls)

    release.set()
    t.join(5)
    assert result["code"] == 200


def test_a_runner_that_cannot_start_releases_the_slot(monkeypatch, install_root):
    """If the worker can never run, the id must not stay wedged as 'installing'
    for the life of the daemon."""
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)

    def _cannot_spawn(work):
        raise RuntimeError("can't start a thread")

    monkeypatch.setattr(install_svc, "_spawn_thread", _cannot_spawn)
    client = _client(_FakeRegistry())

    first = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert first.status_code == 500
    assert install_svc._QUEUED == set()

    # A retry is accepted rather than wedged behind a phantom install.
    monkeypatch.setattr(install_svc, "_spawn_thread", lambda work: work())
    retry = client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})
    assert retry.status_code == 202
    assert (install_root / "email" / ".installed").exists()


def test_registry_hold_blocks_ensure_for_the_duration(monkeypatch):
    """The real registry contract behind the guard: while an install holds the
    agent, an ensure of that agent waits instead of respawning it mid-mutation
    (a different agent is unaffected)."""
    from gaia.daemon.sidecars import registry as registry_mod
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    spec = AgentSidecarSpec(
        agent_id="toy",
        service_id="gaia-agent-toy",
        display_name="Toy",
        expected_api_major="1",
        token_env_var="GAIA_TOY_SIDECAR_TOKEN",
        mode_env_var="GAIA_TOY_AGENT_MODE",
        cache_dir_name="toy",
    )

    class _Manager:
        def __init__(self, spec, mode=None, **kwargs):
            self.spec = spec
            self._running = False
            self.pid = None
            self.port = None
            self.base_url = None
            self.api_version = "1.0"
            self.agent_version = "0.1.0"
            self.resolved_mode = "user"
            self.auth_token = "tok"
            self.started_at = None
            self.mode = "user"

        @property
        def is_running(self):
            return self._running

        def start(self):
            self._running = True
            self.pid = 4242
            self.port = 51000
            self.base_url = "http://127.0.0.1:51000"
            self.started_at = 1.0
            return self.base_url

        def shutdown(self):
            self._running = False

    reg = registry_mod.SidecarRegistry({"toy": spec})
    reg._manager_factory = _Manager
    monkeypatch.setattr(registry_mod.psutil, "pid_exists", lambda pid: False)
    reg.ensure("toy")

    ensured = threading.Event()
    released = threading.Event()
    order: list = []

    with reg.hold_for_mutation("toy"):
        assert reg.list_agents()[0]["state"] == "stopped"  # stopped on entry

        def _ensure():
            reg.ensure("toy")
            order.append("ensure")
            ensured.set()

        t = threading.Thread(target=_ensure, daemon=True)
        t.start()
        assert not ensured.wait(0.5), "ensure ran while the agent was held"
        order.append("mutation-done")
        released.set()

    assert ensured.wait(5), "ensure never completed after the hold released"
    t.join(5)
    assert order == ["mutation-done", "ensure"]


def test_install_honours_an_explicit_version(monkeypatch, install_root):
    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())

    r = client.post(
        "/daemon/v1/agents/email/install", headers=_auth(), json={"version": "0.5.0"}
    )
    assert r.status_code == 202
    assert r.json()["version"] == "0.5.0"

    r = client.post(
        "/daemon/v1/agents/email/install", headers=_auth(), json={"version": "9.9.9"}
    )
    status = client.get(
        "/daemon/v1/agents/email/install-status", headers=_auth()
    ).json()
    assert status["status"] == "failed"
    assert "9.9.9" in status["error"]


# ===========================================================================
# Uninstall
# ===========================================================================


def test_uninstall_stops_the_sidecar_then_removes_the_dir(monkeypatch, install_root):
    _write_sentinel(install_root, "email", "0.5.0")
    registry = _FakeRegistry()
    client = _client(registry)

    r = client.delete("/daemon/v1/agents/email", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"agent_id": "email", "status": "uninstalled"}
    assert registry.stop_calls == ["email"]
    assert not (install_root / "email").exists()


def test_uninstall_aborts_when_the_pid_survives_and_keeps_the_install(
    monkeypatch, install_root
):
    from gaia.daemon.sidecars.errors import StopFailedError

    _write_sentinel(install_root, "email", "0.5.0")
    registry = _FakeRegistry(stop_error=StopFailedError("pid 4242 survived shutdown"))
    client = _client(registry)

    r = client.delete("/daemon/v1/agents/email", headers=_auth())
    assert r.status_code == 500
    assert "4242" in r.json()["detail"]
    # The live process's directory is untouched.
    assert (install_root / "email" / ".installed").exists()


def test_uninstall_not_installed_is_404(install_root):
    client = _client(_FakeRegistry())
    r = client.delete("/daemon/v1/agents/email", headers=_auth())
    assert r.status_code == 404
    assert "not installed" in r.json()["detail"]


def test_uninstall_reserved_builtin_is_refused(install_root):
    client = _client(_FakeRegistry(agent_ids=("email", "builder")))
    r = client.delete("/daemon/v1/agents/builder", headers=_auth())
    assert r.status_code == 400
    assert "built-in" in r.json()["detail"]


def test_uninstall_of_an_unsupervised_but_installed_agent_still_works(install_root):
    """An agent with no sidecar spec has nothing to stop — removal proceeds."""
    _write_sentinel(install_root, "spreadsheet", "0.1.0")
    registry = _FakeRegistry(agent_ids=("email",))
    client = _client(registry)

    r = client.delete("/daemon/v1/agents/spreadsheet", headers=_auth())
    assert r.status_code == 200
    assert not (install_root / "spreadsheet").exists()


def test_uninstall_during_an_install_is_409(monkeypatch, install_root):
    _write_sentinel(install_root, "email", "0.5.0")
    install_svc._QUEUED.add("email")
    client = _client(_FakeRegistry())

    r = client.delete("/daemon/v1/agents/email", headers=_auth())
    assert r.status_code == 409
    assert (install_root / "email" / ".installed").exists()


# ===========================================================================
# The install must produce exactly what the spawn path re-verifies
# ===========================================================================


def test_installed_layout_is_what_the_sidecar_fetch_accepts(monkeypatch, install_root):
    """End-to-end contract: install through the daemon route, then let the real
    spawn-time fetch resolve the binary. It must short-circuit on the sentinel
    WITHOUT consulting binaries.lock.json (which is not wheel package data and
    still carries placeholder SHAs)."""
    from gaia.daemon.sidecars import fetch as fetchmod

    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    client = _client(_FakeRegistry())
    client.post("/daemon/v1/agents/email/install", headers=_auth(), json={})

    result = fetchmod.fetch_binary(
        out_dir=install_root / "email",
        platform_key=PLATFORM_KEY,
        lock_path=install_root / "no-such-lock.json",
        agent_dir_name="email",
    )
    assert Path(result.binary_path) == install_root / "email" / "email-agent"
    assert result.sha256 == BINARY_SHA
    assert result.version == "0.5.0"


def test_cold_fetch_without_an_install_names_the_hub_install_remedy(
    monkeypatch, install_root
):
    """No install and no lock (the pip-installed wheel's real state) must say
    'run gaia hub install', not 'your binaries.lock.json is broken'."""
    from gaia.daemon.sidecars import fetch as fetchmod
    from gaia.daemon.sidecars.errors import BinaryNotFoundError

    monkeypatch.setattr(
        "gaia.daemon.sidecars.platform.default_lock_path",
        lambda: install_root / "missing" / "binaries.lock.json",
    )
    with pytest.raises(BinaryNotFoundError, match="gaia hub install email"):
        fetchmod.fetch_binary(
            out_dir=install_root / "email",
            platform_key=PLATFORM_KEY,
            agent_dir_name="email",
        )


# ===========================================================================
# Token hygiene — no sidecar bearer may leak through the hub plane
# ===========================================================================


def test_no_route_response_contains_a_token(monkeypatch, install_root, caplog):
    """The sidecar bearer must never appear in a hub response body or a log line."""
    import logging

    secret = "SIDECAR-BEARER-DO-NOT-LEAK"

    class _TokenRegistry(_FakeRegistry):
        def list_agents(self):
            # A real registry entry never carries a token here; assert the hub
            # plane does not resurrect one even if the manager holds it.
            self.auth_token = secret
            return super().list_agents()

    fetcher = _RecordingFetcher(_hub_files())
    _patch_hub(monkeypatch, fetcher)
    registry = _TokenRegistry()
    client = _client(registry)

    # Set the level on the `gaia` logger itself: the child logger carries an
    # explicit level, so a root-level-only caplog would never see DEBUG records
    # and this assertion would be vacuous.
    caplog.set_level(logging.DEBUG, logger="gaia")
    with caplog.at_level(logging.DEBUG):
        bodies = [
            client.get("/daemon/v1/catalog", headers=_auth()).text,
            client.post(
                "/daemon/v1/agents/email/install", headers=_auth(), json={}
            ).text,
            client.get("/daemon/v1/agents/email/install-status", headers=_auth()).text,
            client.delete("/daemon/v1/agents/email", headers=_auth()).text,
        ]

    for body in bodies:
        assert secret not in body
        assert '"token"' not in body
    assert secret not in caplog.text


# ===========================================================================
# `gaia hub` CLI wiring
# ===========================================================================


def test_cli_parses_the_hub_subcommands():
    from gaia.cli import build_parser

    parser = build_parser()
    listed = parser.parse_args(["hub", "list", "--installed"])
    assert (listed.action, listed.hub_action, listed.installed) == ("hub", "list", True)

    inst = parser.parse_args(["hub", "install", "email", "--version", "0.5.0"])
    assert (inst.action, inst.hub_action, inst.agent_id, inst.version) == (
        "hub",
        "install",
        "email",
        "0.5.0",
    )

    rm = parser.parse_args(["hub", "uninstall", "email"])
    assert (rm.action, rm.hub_action, rm.agent_id) == ("hub", "uninstall", "email")


class _CannedResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _canned_cli(monkeypatch, responses):
    """Drive the CLI handlers against a scripted sequence of daemon responses."""
    from gaia import cli as cli_mod

    calls: list = []
    seq = list(responses)

    def _request(inst, method, path, **kwargs):
        calls.append((method, path))
        return seq.pop(0)

    monkeypatch.setattr(cli_mod, "_hub_daemon", lambda: object())
    monkeypatch.setattr(cli_mod, "_hub_request", _request)
    monkeypatch.setattr(cli_mod.time, "sleep", lambda _s: None)
    return cli_mod, calls


def test_cli_install_polls_until_completed(monkeypatch, capsys):
    import argparse

    cli_mod, calls = _canned_cli(
        monkeypatch,
        [
            _CannedResp(202, {"agent_id": "email", "status": "queued"}),
            _CannedResp(
                200, {"status": "running", "phase": "downloading", "percent": 30}
            ),
            _CannedResp(
                200,
                {
                    "status": "completed",
                    "phase": "completed",
                    "percent": 100,
                    "version": "0.5.0",
                },
            ),
        ],
    )
    cli_mod._handle_hub_install(argparse.Namespace(agent_id="email", version=None))

    out = capsys.readouterr().out
    assert "installed" in out and "0.5.0" in out
    assert calls[0] == ("POST", "/daemon/v1/agents/email/install")
    assert calls[1] == ("GET", "/daemon/v1/agents/email/install-status")


def test_cli_install_failure_exits_1_with_the_reason(monkeypatch, capsys):
    import argparse

    cli_mod, _ = _canned_cli(
        monkeypatch,
        [
            _CannedResp(202, {"agent_id": "email", "status": "queued"}),
            _CannedResp(
                200,
                {
                    "status": "failed",
                    "phase": "failed",
                    "percent": 0,
                    "error": "Artifact checksum mismatch",
                },
            ),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod._handle_hub_install(argparse.Namespace(agent_id="email", version=None))
    assert exc.value.code == 1
    assert "checksum mismatch" in capsys.readouterr().out


def test_cli_uninstall_reports_the_daemon_detail_and_exits_1(monkeypatch, capsys):
    import argparse

    cli_mod, calls = _canned_cli(
        monkeypatch, [_CannedResp(500, {"detail": "pid 4242 survived the tree-kill"})]
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod._handle_hub_uninstall(argparse.Namespace(agent_id="email"))
    assert exc.value.code == 1
    assert "4242" in capsys.readouterr().out
    assert calls == [("DELETE", "/daemon/v1/agents/email")]


def test_cli_list_installed_asks_the_daemon_for_local_state(monkeypatch, capsys):
    import argparse

    cli_mod, calls = _canned_cli(
        monkeypatch,
        [_CannedResp(200, {"agents": [], "offline": False, "source": "local"})],
    )
    cli_mod._handle_hub_list(argparse.Namespace(installed=True, refresh=False))
    assert calls == [("GET", "/daemon/v1/catalog?installed_only=true")]


def test_cli_list_never_prints_a_token_even_if_one_appears_in_the_body(
    monkeypatch, capsys
):
    """Defense in depth: the renderer touches named fields only, so a token
    smuggled into a catalog entry can never reach the terminal."""
    import argparse

    from gaia import cli as cli_mod

    secret = "SIDECAR-BEARER-DO-NOT-LEAK"

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "agents": [
                    {
                        "id": "email",
                        "name": "Email Triage",
                        "latest_version": "0.5.0",
                        "installed": True,
                        "installed_version": "0.5.0",
                        "download_size_bytes": 32578800,
                        "token": secret,
                    }
                ],
                "offline": False,
                "unsupervised_filtered": [],
            }

    monkeypatch.setattr(cli_mod, "_hub_daemon", lambda: object())
    monkeypatch.setattr(
        cli_mod, "_hub_request", lambda *a, **k: _Resp()  # noqa: ARG005
    )
    cli_mod._handle_hub_list(argparse.Namespace(installed=False))

    out = capsys.readouterr().out
    assert "email" in out and "0.5.0" in out
    assert secret not in out
