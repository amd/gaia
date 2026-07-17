# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia.ui.email_sidecar.daemon_client`` — the UI backend's client seam onto
the daemon's ``/daemon/v1/agents`` control plane (#2142 T3 cutover).

Patch-seam choice (documented per the T3 increment spec, so the implementer
matches it): tests patch ``daemon_client_module.start_or_attach`` /
``daemon_client_module.attach`` — module-level names resolved inside
``gaia.ui.email_sidecar.daemon_client`` (mirroring how every other
``gaia.daemon.client`` consumer imports it) — and
``daemon_client_module.requests.post`` for the HTTP boundary. The module must
therefore ``import requests`` at module level, exactly like
``gaia.daemon.client`` does for ``request_shutdown``. A separate
``_post_ensure(inst, agent_id, mode)`` helper is not independently tested here.
"""

from __future__ import annotations

import pytest

import gaia.ui.email_sidecar.daemon_client as daemon_client_module  # noqa: E402
from gaia.daemon import paths
from gaia.daemon.errors import DaemonStartError, DaemonVersionError
from gaia.daemon.instance import DaemonInstance
from gaia.daemon.sidecars.errors import SidecarError
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy  # noqa: E402

ENSURE_PAYLOAD = {
    "agent_id": "email",
    "state": "running",
    "mode": "user",
    "pid": 111,
    "port": 55001,
    "base_url": "http://127.0.0.1:55001",
    "api_version": "2.4",
    "agent_version": "0.3.0",
    "started_at": 1.0,
    "dev_src_dir": None,
    "token": "sidecar-tok",
}


def _daemon_instance(**overrides) -> DaemonInstance:
    fields = dict(pid=999, port=54321, token="daemon-tok", api_version="1.1")
    fields.update(overrides)
    return DaemonInstance(**fields)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _RecordingPost:
    """Records every call (url + headers/json kwargs); returns a fixed response."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(
            {"url": url, "headers": kwargs.get("headers"), "json": kwargs.get("json")}
        )
        return self._response


class _ForbiddenPost:
    """Fails loudly if ``requests.post`` is ever called — pins call ordering."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("requests.post must not be called")


@pytest.fixture(autouse=True)
def _daemon_home(tmp_path, monkeypatch):
    # Isolates every test from the real ~/.gaia/host and gives log_path() a
    # deterministic value to assert against in the wrapped-error messages.
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path))
    return tmp_path


# ── SidecarHandle.proxy() ────────────────────────────────────────────────────


def test_handle_proxy_returns_bound_proxy_instance():
    handle = daemon_client_module.SidecarHandle(
        base_url="http://127.0.0.1:55001",
        token="sidecar-tok",
        api_version="2.4",
        agent_version="0.3.0",
        mode="user",
        pid=111,
    )
    proxy = handle.proxy()
    assert isinstance(proxy, EmailSidecarProxy)
    assert proxy.base_url == "http://127.0.0.1:55001"
    assert proxy._auth_token == "sidecar-tok"
    assert proxy._session.headers["Authorization"] == "Bearer sidecar-tok"


# ── acquire_handle: the DaemonError -> SidecarError seam ───────────────────


def test_acquire_daemon_start_error_wraps_as_sidecar_error(monkeypatch):
    def _raise(*a, **k):
        raise DaemonStartError("boom-xyz")

    monkeypatch.setattr(daemon_client_module, "start_or_attach", _raise)

    with pytest.raises(SidecarError) as excinfo:
        daemon_client_module.acquire_handle()

    message = str(excinfo.value)
    assert "gaia daemon status" in message
    assert str(paths.log_path()) in message
    assert "boom-xyz" in message


def test_acquire_generic_daemon_error_wraps_as_sidecar_error(monkeypatch):
    def _raise(*a, **k):
        raise DaemonVersionError("version-skew-xyz")

    monkeypatch.setattr(daemon_client_module, "start_or_attach", _raise)

    with pytest.raises(SidecarError) as excinfo:
        daemon_client_module.acquire_handle()

    message = str(excinfo.value)
    assert "gaia daemon status" in message
    assert str(paths.log_path()) in message
    assert "version-skew-xyz" in message


# ── acquire_handle: the stale-daemon MINOR floor (pre-#2142 daemon) ────────


def test_acquire_stale_daemon_minor_zero_raises_restart_hint(monkeypatch):
    inst = _daemon_instance(api_version="1.0")
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    monkeypatch.setattr(daemon_client_module.requests, "post", _ForbiddenPost())

    with pytest.raises(SidecarError, match="gaia daemon restart"):
        daemon_client_module.acquire_handle()


def test_acquire_stale_daemon_major_only_raises_restart_hint(monkeypatch):
    inst = _daemon_instance(api_version="1")
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    monkeypatch.setattr(daemon_client_module.requests, "post", _ForbiddenPost())

    with pytest.raises(SidecarError, match="gaia daemon restart"):
        daemon_client_module.acquire_handle()


def test_acquire_stale_daemon_floor_check_precedes_http_call(monkeypatch):
    # _ForbiddenPost raises AssertionError (not SidecarError) if the HTTP call
    # happens at all — pinning that the floor check runs BEFORE any request.
    inst = _daemon_instance(api_version="1.0")
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    monkeypatch.setattr(daemon_client_module.requests, "post", _ForbiddenPost())

    with pytest.raises(SidecarError):
        daemon_client_module.acquire_handle()


# ── acquire_handle: the ensure POST + success path ──────────────────────────


def test_acquire_success_returns_handle_from_ensure_payload(monkeypatch):
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(200, ENSURE_PAYLOAD))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    handle = daemon_client_module.acquire_handle()

    assert isinstance(handle, daemon_client_module.SidecarHandle)
    assert handle.base_url == ENSURE_PAYLOAD["base_url"]
    assert handle.token == "sidecar-tok"
    assert handle.api_version == "2.4"
    assert handle.agent_version == "0.3.0"
    assert handle.mode == "user"
    assert handle.pid == 111
    assert len(recorder.calls) == 1


def test_acquire_posts_bearer_token_and_ensure_url(monkeypatch):
    inst = _daemon_instance(token="daemon-tok-xyz")
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(200, ENSURE_PAYLOAD))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    daemon_client_module.acquire_handle(agent_id="email")

    call = recorder.calls[0]
    assert call["url"] == f"{inst.base_url}/daemon/v1/agents/email/ensure"
    assert call["headers"]["Authorization"] == "Bearer daemon-tok-xyz"


def test_acquire_uses_agent_id_in_url(monkeypatch):
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(200, {**ENSURE_PAYLOAD, "agent_id": "foo"}))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    daemon_client_module.acquire_handle(agent_id="foo")

    assert recorder.calls[0]["url"].endswith("/daemon/v1/agents/foo/ensure")


def test_acquire_default_mode_is_user(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(200, ENSURE_PAYLOAD))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    daemon_client_module.acquire_handle()

    assert recorder.calls[0]["json"] == {"mode": "user"}


def test_acquire_mode_from_env_override(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(200, {**ENSURE_PAYLOAD, "mode": "dev"}))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    handle = daemon_client_module.acquire_handle()

    assert recorder.calls[0]["json"] == {"mode": "dev"}
    assert handle.mode == "dev"


def test_acquire_non_200_response_raises_sidecar_error_with_detail(monkeypatch):
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "start_or_attach", lambda **k: inst)
    recorder = _RecordingPost(_FakeResponse(502, {"detail": "DISTINCTIVE-502"}))
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    with pytest.raises(SidecarError, match="DISTINCTIVE-502"):
        daemon_client_module.acquire_handle()


# ── stop_sidecar: attach-only, genuine no-op when no daemon is live ────────


def test_stop_sidecar_noop_when_daemon_absent_no_http_call(monkeypatch):
    monkeypatch.setattr(daemon_client_module, "attach", lambda **k: None)
    monkeypatch.setattr(daemon_client_module.requests, "post", _ForbiddenPost())

    daemon_client_module.stop_sidecar()  # must not raise


def test_stop_sidecar_stale_daemon_below_minor_floor_raises_restart_hint(
    monkeypatch,
):
    inst = _daemon_instance(api_version="1.0")
    monkeypatch.setattr(daemon_client_module, "attach", lambda **k: inst)
    monkeypatch.setattr(daemon_client_module.requests, "post", _ForbiddenPost())

    with pytest.raises(SidecarError, match="gaia daemon restart"):
        daemon_client_module.stop_sidecar()


def test_stop_sidecar_success_posts_bearer_and_returns_none(monkeypatch):
    inst = _daemon_instance(token="daemon-tok-abc")
    monkeypatch.setattr(daemon_client_module, "attach", lambda **k: inst)
    recorder = _RecordingPost(
        _FakeResponse(200, {"agent_id": "email", "state": "stopped"})
    )
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    result = daemon_client_module.stop_sidecar("email")

    assert result is None
    call = recorder.calls[0]
    assert call["url"] == f"{inst.base_url}/daemon/v1/agents/email/stop"
    assert call["headers"]["Authorization"] == "Bearer daemon-tok-abc"


def test_stop_sidecar_failure_raises_sidecar_error_with_detail(monkeypatch):
    inst = _daemon_instance()
    monkeypatch.setattr(daemon_client_module, "attach", lambda **k: inst)
    recorder = _RecordingPost(
        _FakeResponse(500, {"detail": "pid 4242 survived a tree-kill"})
    )
    monkeypatch.setattr(daemon_client_module.requests, "post", recorder)

    with pytest.raises(SidecarError, match="4242"):
        daemon_client_module.stop_sidecar("email")


def test_stop_sidecar_daemon_error_from_attach_wraps_as_sidecar_error(monkeypatch):
    def _raise(*a, **k):
        raise DaemonVersionError("attach-boom-xyz")

    monkeypatch.setattr(daemon_client_module, "attach", _raise)

    with pytest.raises(SidecarError) as excinfo:
        daemon_client_module.stop_sidecar()

    message = str(excinfo.value)
    assert "gaia daemon status" in message
    assert str(paths.log_path()) in message
    assert "attach-boom-xyz" in message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
