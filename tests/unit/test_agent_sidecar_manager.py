# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AgentSidecarManager: the generalized, spec-driven successor to
EmailSidecarManager — mode select, ephemeral port, spawn-arg shape, health,
tree-kill, PLUS the new spec-parametrization and resolved_mode-capture
mechanics (#2142 T1)."""

import dataclasses
import importlib.util
import logging
import os
import stat as _stat
import sys
import threading
import time as _t
from pathlib import Path

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import (
    BinaryNotFoundError,
    HealthTimeoutError,
    IntegrityError,
    PlatformError,
    RouteNotAvailableError,
    SidecarError,
    SidecarHTTPError,
    SidecarSpawnError,
    VersionMismatchError,
)
from gaia.daemon.sidecars.spec import AgentSidecarSpec, builtin_specs

# ---------------------------------------------------------------------------
# spec.py — AgentSidecarSpec + builtin_specs()
# ---------------------------------------------------------------------------


def test_spec_is_frozen():
    spec = builtin_specs()["email"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.agent_id = "not-email"


def test_builtin_specs_has_an_email_entry():
    specs = builtin_specs()
    assert "email" in specs
    email = specs["email"]
    assert email.agent_id == "email"
    assert email.service_id == "gaia-agent-email"
    assert email.expected_api_major == "2"
    assert email.cache_dir_name == "email"


def test_builtin_email_spec_token_env_var_matches_the_hub_wheel_contract():
    # Cross-repo literal: must equal gaia_agent_email.caller_auth.TOKEN_ENV_VAR.
    # Never silently drift — this env var is how the daemon hands the sidecar
    # its per-session auth token.
    assert builtin_specs()["email"].token_env_var == "GAIA_EMAIL_SIDECAR_TOKEN"


def test_builtin_email_spec_token_file_env_var_matches_the_hub_wheel_contract():
    # File-delivery leg (#2149): same mirror rule as the token env var — must
    # equal gaia_agent_email.caller_auth.TOKEN_FILE_ENV_VAR.
    email = builtin_specs()["email"]
    assert email.token_file_env_var == "GAIA_EMAIL_SIDECAR_TOKEN_FILE"
    assert email.secret_file_min_version == "0.6.0"


def test_email_spec_literals_match_the_installed_caller_auth_module():
    # When the hub wheel is importable, assert the mirrored literals for real —
    # the drift the plain-string contract cannot catch by itself.
    caller_auth = pytest.importorskip("gaia_agent_email.caller_auth")
    email = builtin_specs()["email"]
    assert email.token_env_var == caller_auth.TOKEN_ENV_VAR
    assert email.token_file_env_var == caller_auth.TOKEN_FILE_ENV_VAR


def test_builtin_email_spec_mode_env_var():
    assert builtin_specs()["email"].mode_env_var == "GAIA_EMAIL_AGENT_MODE"


def test_email_spec_dev_src_dir_resolves_under_repo_root():
    # tests/unit/test_agent_sidecar_manager.py -> repo root is parents[2].
    repo_root = Path(__file__).resolve().parents[2]
    expected = repo_root / "hub" / "agents" / "email" / "python"
    assert builtin_specs()["email"].dev_src_dir == expected


def test_real_email_spec_dev_spawn_has_nonempty_module_and_existing_app_dir(
    monkeypatch,
):
    """Regression guard for #2441's misdiagnosis: the dev-mode spawn was NEVER
    the source of the "Empty module name" crash (that was an invalid
    PYTHON_KEYRING_BACKEND). Prove it here against the REAL builtin email spec —
    the dev spawn always yields a non-empty import module and an app-dir that
    exists on disk in a source checkout."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    spec = builtin_specs()["email"]
    # Non-empty import module (the value uvicorn __import__s).
    module = spec.dev_module.split(":", 1)[0]
    assert module, "dev_module import path must not be empty"
    assert spec.dev_module == "server:app"

    m = mgr.AgentSidecarManager(spec)
    argv, kwargs = m.build_spawn_command(port=9127)
    app_dir = Path(argv[argv.index("--app-dir") + 1])
    assert app_dir == spec.dev_src_dir / spec.dev_app_dir
    # In this source checkout the app-dir (and server.py) really exist.
    assert app_dir.is_dir(), f"dev app-dir does not exist: {app_dir}"
    assert (app_dir / "server.py").is_file()
    # The module uvicorn loads is the non-empty top-level `server`, not empty.
    assert "server:app" in argv and "" not in argv


# ---------------------------------------------------------------------------
# errors.py — relocated hierarchy + backward-compat shim
# ---------------------------------------------------------------------------


def test_error_hierarchy_relocated_verbatim():
    assert issubclass(SidecarError, Exception)
    for cls in (
        PlatformError,
        IntegrityError,
        BinaryNotFoundError,
        HealthTimeoutError,
        SidecarSpawnError,
        RouteNotAvailableError,
        SidecarHTTPError,
        VersionMismatchError,
    ):
        assert issubclass(cls, SidecarError)
    err = SidecarHTTPError(502, "boom", path="/v1/x")
    assert err.status_code == 502
    assert err.detail == "boom"
    assert err.path == "/v1/x"
    assert "502" in str(err) and "boom" in str(err) and "/v1/x" in str(err)


def test_ui_email_sidecar_errors_is_a_reexport_shim():
    # gaia.ui.email_sidecar.errors becomes a pure re-export shim: identity, not
    # just equality, so existing `from ... import SidecarError` callers still
    # get the exact same class object as the new location.
    import gaia.daemon.sidecars.errors as new_errors
    import gaia.ui.email_sidecar.errors as old_errors

    assert old_errors.SidecarError is new_errors.SidecarError
    assert old_errors.VersionMismatchError is new_errors.VersionMismatchError
    assert old_errors.SidecarSpawnError is new_errors.SidecarSpawnError


# ---------------------------------------------------------------------------
# manager.py — find_free_port / mode / build_spawn_command
# ---------------------------------------------------------------------------

_TOY_SPEC = AgentSidecarSpec(
    agent_id="toy",
    service_id="gaia-agent-toy",
    display_name="Toy Agent",
    expected_api_major="1",
    token_env_var="GAIA_TOY_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_AGENT_MODE",
    cache_dir_name="toy",
)


def _email_spec_with_src(src_dir: Path) -> AgentSidecarSpec:
    return dataclasses.replace(builtin_specs()["email"], dev_src_dir=src_dir)


def test_find_free_port_never_4001():
    for _ in range(20):
        assert mgr.find_free_port() != 4001


def test_mode_defaults_to_user(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    assert mgr.AgentSidecarManager(builtin_specs()["email"]).mode == "user"


def test_mode_reads_env(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    assert mgr.AgentSidecarManager(builtin_specs()["email"]).mode == "dev"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE"):
        mgr.AgentSidecarManager(builtin_specs()["email"]).mode


def test_invalid_mode_error_names_the_spec_mode_env_var_not_email(monkeypatch):
    """A non-email spec's invalid-mode error must be truly spec-driven — no
    leftover hardcoded 'GAIA_EMAIL_AGENT_MODE' string anywhere in the message."""
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    monkeypatch.setenv("GAIA_TOY_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError) as exc_info:
        mgr.AgentSidecarManager(_TOY_SPEC).mode
    msg = str(exc_info.value)
    assert "GAIA_TOY_AGENT_MODE" in msg
    assert "GAIA_EMAIL_AGENT_MODE" not in msg


def test_user_mode_spawn_command_uses_fetched_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    fake_binary = tmp_path / "email-agent"
    fake_binary.write_bytes(b"x")

    class _Res:
        binary_path = fake_binary
        version = "0.6.0"

    monkeypatch.setattr(mgr.fetch, "fetch_binary", lambda **kw: _Res())
    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    argv, kwargs = m.build_spawn_command(port=9123)
    assert argv[0] == str(fake_binary)
    assert "--port" in argv and "9123" in argv
    assert "4001" not in argv


def test_user_mode_fetch_failure_raises_with_remedy(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")

    def _boom(**kw):
        raise RuntimeError("placeholder sha256")

    monkeypatch.setattr(mgr.fetch, "fetch_binary", _boom)
    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE=dev"):
        m.build_spawn_command(port=9123)


def test_user_mode_failure_names_working_remedies(monkeypatch):
    """The user-mode failure must name remedies that actually work today (#2347):
    the Agent Hub install (with a headless one-liner), dev mode, the log dir,
    and the docs URL — and must NOT wrap the original fetch cause."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")

    def _boom(**kw):
        raise RuntimeError("cannot read the sidecar binary lock at /x")

    monkeypatch.setattr(mgr.fetch, "fetch_binary", _boom)
    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    with pytest.raises(SidecarSpawnError) as exc_info:
        m.build_spawn_command(port=9123)
    msg = str(exc_info.value)
    # Working remedies, spec-driven.
    assert "Agent Hub" in msg
    assert "gaia agent install email" in msg  # headless CLI path
    assert "GAIA_EMAIL_AGENT_MODE=dev" in msg
    assert "Sidecar logs:" in msg
    assert "https://amd-gaia.ai/docs/guides/email" in msg
    # The original cause is preserved for debugging.
    assert "cannot read the sidecar binary lock" in msg


def test_user_mode_failure_is_spec_driven_no_email_leak(monkeypatch):
    """A non-email spec's failure must be truly generic — its own agent_id and
    mode env var, no leftover hardcoded email strings, and no docs line when the
    spec declares no docs_url."""
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    monkeypatch.setenv("GAIA_TOY_AGENT_MODE", "user")

    def _boom(**kw):
        raise RuntimeError("no binary")

    monkeypatch.setattr(mgr.fetch, "fetch_binary", _boom)
    m = mgr.AgentSidecarManager(_TOY_SPEC)
    with pytest.raises(SidecarSpawnError) as exc_info:
        m.build_spawn_command(port=9123)
    msg = str(exc_info.value)
    assert "gaia agent install toy" in msg
    assert "GAIA_TOY_AGENT_MODE=dev" in msg
    assert "email" not in msg.lower()
    assert "GAIA_EMAIL_AGENT_MODE" not in msg
    assert "Docs:" not in msg  # _TOY_SPEC has no docs_url


def test_user_mode_spawns_hub_installed_binary_despite_placeholder_lock(
    monkeypatch, tmp_path
):
    # #2095: a Hub-installed, checksum-verified binary must spawn even while
    # binaries.lock.json still ships placeholder SHAs. Real fetch, no mocks —
    # exercises the new gaia.daemon.sidecars.fetch module for real.
    import hashlib
    import json

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    cache = tmp_path / "email"
    cache.mkdir()
    data = b"hub-verified-binary"
    binary = cache / "email-agent"
    binary.write_bytes(data)
    (cache / ".installed").write_text(
        json.dumps(
            {
                "id": "email",
                "version": "0.5.0",
                "language": "python",
                "installed_at": "2026-07-15T00:00:00+00:00",
                "artifact_sha256": hashlib.sha256(data).hexdigest(),
                "artifact_kind": "binary",
                "executable": "email-agent",
            }
        )
    )
    lock = tmp_path / "binaries.lock.json"
    lock.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.5.0",
                "baseUrl": "https://r2.example",
                "binaries": {
                    "darwin-arm64": {
                        "filename": "email-agent-darwin-arm64",
                        "executable": "email-agent",
                        "sha256": "PENDING-1648-replace-with-real-sha256",
                        "size": 0,
                    }
                },
            }
        )
    )
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=cache, lock_path=lock
    )
    argv, kwargs = m.build_spawn_command(port=9126)
    assert argv[0] == str(binary)
    assert "--port" in argv and "9126" in argv


def test_dev_mode_spawn_command_is_uvicorn_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.AgentSidecarManager(_email_spec_with_src(src))
    argv, kwargs = m.build_spawn_command(port=9124)
    assert argv[0] == sys.executable
    assert "uvicorn" in argv
    # Loaded as TOP-LEVEL module `server` via --app-dir, NOT `packaging.server:app`
    # (which would resolve to the PyPI packaging library).
    assert "server:app" in argv
    assert "packaging.server:app" not in argv
    # No --reload: the daemon supervises the sidecar; uvicorn's own spawn-based
    # reload supervisor is what fails on macOS with "Empty module name" (#2441).
    assert "--reload" not in argv
    assert "--app-dir" in argv
    app_dir = argv[argv.index("--app-dir") + 1]
    assert app_dir == str(src / "packaging")


def test_dev_mode_missing_src_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(_email_spec_with_src(tmp_path / "does-not-exist"))
    with pytest.raises(SidecarSpawnError, match="uv pip install -e"):
        m.build_spawn_command(port=9125)


def test_spawn_port_4001_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.AgentSidecarManager(_email_spec_with_src(src))
    with pytest.raises(ValueError, match="4001"):
        m.build_spawn_command(port=4001)


# ---------------------------------------------------------------------------
# start() / shutdown() with a faked Popen + faked HTTP
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return None  # still running

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = str(payload)

    def json(self):
        return self._payload


def _install_fake_spawn(monkeypatch, tmp_path, *, spec=None, version_payload=None):
    """Wire a manager with a fake Popen + fake HTTP so start() runs without a
    real subprocess. Returns (manager, captured) where captured records calls."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spec = spec or _email_spec_with_src(src)
    captured = {"popen_kwargs": None, "atexit": []}

    def _fake_popen(argv, **kwargs):
        captured["popen_kwargs"] = kwargs
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        mgr.atexit, "register", lambda fn: captured["atexit"].append(fn)
    )
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)

    m = mgr.AgentSidecarManager(spec, cache_dir=tmp_path, log_dir=tmp_path / "logs")

    vp = version_payload or {"apiVersion": "1.0", "agentVersion": "0.2.2"}

    def _fake_http_get(url, timeout):
        if url.endswith("/health"):
            return _FakeResp({"status": "ok", "service": spec.service_id})
        if url.endswith("/version"):
            return _FakeResp(vp)
        raise AssertionError(url)

    monkeypatch.setattr(m, "_http_get", _fake_http_get)
    return m, captured


def test_spawn_redirects_output_to_logfile_not_pipe(monkeypatch, tmp_path):
    # The deadlock fix: stdout must go to a real file (has fileno), stderr merged
    # — NOT subprocess.PIPE, which would deadlock once the buffer fills.
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    kwargs = captured["popen_kwargs"]
    assert kwargs["stdout"] is not mgr.subprocess.PIPE
    assert hasattr(kwargs["stdout"], "fileno")  # an open file object
    assert kwargs["stderr"] == mgr.subprocess.STDOUT
    assert (tmp_path / "logs").is_dir()


def test_start_registers_atexit_reaper(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    assert m.shutdown in captured["atexit"]


def test_dev_mode_delivers_secret_via_0600_file_never_env(monkeypatch, tmp_path):
    # #2149: dev mode runs from source (which reads the secret file), so the
    # token reaches the sidecar as a 0600 owner-only file — its PATH is in the
    # env, the secret itself is not, and it never appears on the command line.
    spec = builtin_specs()["email"]
    monkeypatch.delenv(spec.token_env_var, raising=False)
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    env = captured["popen_kwargs"]["env"]
    assert m.secret_delivery == "file"
    secret_path = Path(env[spec.token_file_env_var])
    assert secret_path.read_text(encoding="utf-8") == m.auth_token
    if os.name != "nt":
        assert _stat.S_IMODE(secret_path.stat().st_mode) == 0o600
        assert _stat.S_IMODE(secret_path.parent.stat().st_mode) == 0o700
    # The secret VALUE is absent from the spawn env and argv.
    assert spec.token_env_var not in env
    assert m.auth_token not in env.values()
    assert all(m.auth_token not in str(a) for a in captured["argv"])
    # The inherited environment is preserved (merged, not replaced).
    assert "PATH" in env or "Path" in env
    m.shutdown()


def test_secret_file_is_removed_on_shutdown(monkeypatch, tmp_path):
    # The 0600 file must not outlive the sidecar — removal hangs off the same
    # shutdown/reap path that tree-kills the process (atexit covers crashes).
    spec = builtin_specs()["email"]
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    secret_path = Path(captured["popen_kwargs"]["env"][spec.token_file_env_var])
    assert secret_path.exists()
    m.shutdown()
    assert not secret_path.exists()
    assert not secret_path.parent.exists()  # the private 0700 dir goes too


def _install_fake_user_spawn(monkeypatch, tmp_path, *, version):
    """User-mode twin of _install_fake_spawn: fake fetched binary at *version*."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    fake_binary = tmp_path / "email-agent"
    fake_binary.write_bytes(b"x")

    class _Res:
        binary_path = fake_binary

    _Res.version = version
    monkeypatch.setattr(mgr.fetch, "fetch_binary", lambda **kw: _Res())
    captured = {}

    def _fake_popen(argv, **kwargs):
        captured["popen_kwargs"] = kwargs
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "2.0", "agentVersion": version or "?"}
        ),
    )
    return m, captured


def test_user_mode_new_binary_negotiates_file_delivery(monkeypatch, tmp_path):
    # Negotiation is keyed off the INSTALLED binary's version (known pre-spawn),
    # not the runtime /version probe (which answers only after spawn).
    m, captured = _install_fake_user_spawn(monkeypatch, tmp_path, version="0.6.0")
    m.start()
    spec = builtin_specs()["email"]
    env = captured["popen_kwargs"]["env"]
    assert m.secret_delivery == "file"
    assert spec.token_file_env_var in env
    assert spec.token_env_var not in env
    m.shutdown()


def test_user_mode_old_binary_keeps_env_leg_with_loud_deprecation(
    monkeypatch, tmp_path, caplog
):
    # Explicit, versioned compatibility: a published binary that predates file
    # delivery still spawns and authenticates via the env leg — loudly.
    m, captured = _install_fake_user_spawn(monkeypatch, tmp_path, version="0.5.0")
    with caplog.at_level(logging.WARNING, logger="gaia.daemon.sidecars.manager"):
        m.start()
    spec = builtin_specs()["email"]
    env = captured["popen_kwargs"]["env"]
    assert m.secret_delivery == "env"
    assert env[spec.token_env_var] == m.auth_token
    assert spec.token_file_env_var not in env
    assert any("DEPRECATED" in r.getMessage() for r in caplog.records)
    m.shutdown()


def test_user_mode_unknown_binary_version_fails_loudly(monkeypatch, tmp_path):
    # No version in the install metadata → the manager cannot know which leg
    # the binary understands. Refuse loudly; never guess a delivery channel.
    m, captured = _install_fake_user_spawn(monkeypatch, tmp_path, version=None)
    with pytest.raises(SidecarSpawnError, match="version is unknown"):
        m.start()
    assert captured.get("popen_kwargs") is None  # never spawned


def test_secret_file_creation_failure_is_spawn_error_not_env_fallback(
    monkeypatch, tmp_path
):
    # Fail-loudly rule: if the 0600 file cannot be created, that is a startup
    # error — the manager must NOT quietly fall back to bare-env delivery.
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)

    def _boom(prefix=None):
        raise OSError("disk full")

    monkeypatch.setattr(mgr.tempfile, "mkdtemp", _boom)
    with pytest.raises(SidecarSpawnError, match="launch-secret"):
        m.start()
    assert captured.get("popen_kwargs") is None  # never spawned, no env leg


def test_version_tuple_parses_and_rejects_garbage():
    assert mgr._version_tuple("0.6.0") == (0, 6, 0)
    assert mgr._version_tuple("v1.2") == (1, 2)
    assert mgr._version_tuple("0.10.0") > mgr._version_tuple("0.6.0")
    with pytest.raises(SidecarSpawnError, match="cannot parse"):
        mgr._version_tuple("not-a-version")


def test_start_captures_version(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(
        monkeypatch,
        tmp_path,
        version_payload={"apiVersion": "1.3", "agentVersion": "0.9"},
    )
    m.start()
    assert m.api_version == "1.3"
    assert m.agent_version == "0.9"


def test_version_major_mismatch_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        expected_api_version="2.0",
    )
    # shutdown is called on failure; stub it so the fake proc isn't tree-killed.
    monkeypatch.setattr(m, "shutdown", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0"}
        ),
    )
    with pytest.raises(VersionMismatchError, match="major"):
        m.start()


def test_old_sidecar_logs_are_pruned(monkeypatch, tmp_path):
    # Per-port log files accumulate across restarts (ephemeral ports differ).
    # Opening a new log prunes the oldest, keeping only the most recent few.
    logs = tmp_path / "logs"
    logs.mkdir()
    for p in range(9000, 9000 + 10):
        (logs / f"sidecar-{p}.log").write_text("old")
    m, _ = _install_fake_spawn(monkeypatch, tmp_path)
    m.log_dir = logs
    m.start()
    remaining = sorted(logs.glob("sidecar-*.log"))
    assert len(remaining) <= mgr._MAX_SIDECAR_LOGS
    # The just-opened log for the live port must survive the prune.
    assert (logs / f"sidecar-{m.port}.log").exists()


def test_context_manager_starts_and_shuts_down(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    shut = {"called": 0}
    real_shutdown = m.shutdown

    def _spy(*a, **k):
        shut["called"] += 1
        return real_shutdown(*a, **k)

    monkeypatch.setattr(m, "shutdown", _spy)
    with m as started:
        assert started is m
        assert m.is_running
    assert shut["called"] >= 1


def test_health_rejects_foreign_server_on_port(monkeypatch, tmp_path):
    # A non-GAIA server returning {"status":"ok"} must NOT be accepted as our
    # sidecar — require the service identity (spec.service_id).
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        health_timeout=0.3,
    )
    # Foreign server: status ok but no/wrong service identity.
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp({"status": "ok", "service": "nginx"}),
    )
    with pytest.raises(HealthTimeoutError):
        m.start()


def test_pinned_version_with_missing_apiversion_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        expected_api_version="1.0",
    )
    monkeypatch.setattr(m, "shutdown", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"agentVersion": "0.2"}  # apiVersion intentionally absent
        ),
    )
    with pytest.raises(VersionMismatchError, match="apiVersion"):
        m.start()


def test_concurrent_start_spawns_only_one_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spawn_count = {"n": 0}

    def _fake_popen(argv, **kwargs):
        spawn_count["n"] += 1
        return _FakeProc()

    # Widen the pre-spawn window so both threads would race past the is_running
    # check if start() were not serialized — the lock must collapse it to one.
    def _slow_free_port(host="127.0.0.1"):
        _t.sleep(0.2)
        return 50000 + spawn_count["n"]

    monkeypatch.setattr(mgr, "find_free_port", _slow_free_port)
    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src), cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0", "agentVersion": "0.2"}
        ),
    )

    results = []

    def _run():
        try:
            results.append(m.start())
        except Exception as e:  # noqa: BLE001
            results.append(e)

    t1, t2 = threading.Thread(target=_run), threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    # Only one spawn despite two concurrent start() calls; both got the base_url.
    assert spawn_count["n"] == 1, spawn_count
    assert all(isinstance(r, str) for r in results), results


class _ExitedProc:
    """A proc that exited early (e.g. uvicorn lost a port race on bind)."""

    def __init__(self, pid=1, code=1):
        self.pid = pid
        self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_start_retries_on_early_exit_then_succeeds(monkeypatch, tmp_path):
    # Port-race mitigation: if the sidecar exits early (bind failure), start()
    # retries with a fresh port instead of surfacing a spurious failure.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    procs = [_ExitedProc(), _FakeProc()]  # first dies, second lives
    ports = []

    def _fake_popen(argv, **kwargs):
        ports.append(argv[argv.index("--port") + 1])
        return procs.pop(0)

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    # killpg must be a no-op for the fake procs on the early-exit shutdown.
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src), cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0"}
        ),
    )
    base = m.start()
    assert base.startswith("http://127.0.0.1:")
    assert m.api_version == "1.0"
    assert len(ports) == 2  # retried once with a fresh port


def test_start_does_not_retry_on_health_timeout(monkeypatch, tmp_path):
    # A genuine hang (process alive but never healthy) must NOT be retried —
    # retrying a 30s timeout would multiply latency. Fail loudly, once.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spawns = []

    def _fake_popen(argv, **kwargs):
        spawns.append(argv)
        return _FakeProc()

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        health_timeout=0.3,
    )

    class _Boom:
        status_code = 503
        text = "starting"

        def json(self):
            return {"status": "starting"}

    monkeypatch.setattr(m, "_http_get", lambda url, timeout: _Boom())
    with pytest.raises(HealthTimeoutError):
        m.start()
    assert len(spawns) == 1  # no retry on timeout


# ---------------------------------------------------------------------------
# NEW in this generalization: resolved_mode is captured at spawn time, not
# recomputed live from the (spec-driven) mode property.
# ---------------------------------------------------------------------------


def test_resolved_mode_captures_spawn_time_mode_not_live_env(monkeypatch, tmp_path):
    """After a successful start(), resolved_mode reports the mode that was
    ACTUALLY used to spawn — captured once, not live-recomputed. Changing the
    env var after start() must move .mode (the live property) but leave
    .resolved_mode unchanged."""
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)  # spawns in "dev"
    m.start()
    assert m.resolved_mode == "dev"

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    assert m.mode == "user"
    assert m.resolved_mode == "dev"


# ---------------------------------------------------------------------------
# Live spawn (skipped unless the email agent + uvicorn are installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live dev-mode spawn",
)
def test_dev_mode_real_spawn_health_and_treekill(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    monkeypatch.delenv("GAIA_EMAIL_SIDECAR_TOKEN", raising=False)
    m = mgr.AgentSidecarManager(builtin_specs()["email"], health_timeout=60.0)
    base = m.start()
    try:
        import requests

        assert requests.get(f"{base}/health", timeout=5).json()["status"] == "ok"
        assert m.is_running
        # #2149: the secret must be absent from the live child's environment.
        # Linux exposes the real spawn-time record via /proc.
        if sys.platform.startswith("linux"):
            environ = Path(f"/proc/{m.pid}/environ").read_bytes()
            assert m.auth_token.encode() not in environ
        # Real HTTP boundary (no mocks): the file-delivered token gates the
        # surface — missing/wrong bearer → 401, the delivered token → 200.
        draft = {"to": [{"email": "a@b.com"}], "subject": "x", "body": "y"}
        url = f"{base}/v1/email/draft"
        assert requests.post(url, json=draft, timeout=10).status_code == 401
        assert (
            requests.post(
                url,
                json=draft,
                timeout=10,
                headers={"Authorization": "Bearer wrong"},
            ).status_code
            == 401
        )
        ok = requests.post(
            url,
            json=draft,
            timeout=10,
            headers={"Authorization": f"Bearer {m.auth_token}"},
        )
        assert ok.status_code == 200
    finally:
        m.shutdown()
    assert not m.is_running
