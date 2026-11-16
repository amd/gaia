# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch — the SHA-256 security boundary for daemon-supervised
sidecar agents (ported from test_email_sidecar_fetch.py, issue #2142 T3)."""

import hashlib
import json
from pathlib import Path

import pytest

from gaia.daemon.sidecars import fetch as fetchmod
from gaia.daemon.sidecars.errors import (
    BinaryNotFoundError,
    IntegrityError,
    PlatformError,
)

REAL_BYTES = b"fake-binary-payload"
REAL_SHA = hashlib.sha256(REAL_BYTES).hexdigest()


def _lock(tmp_path: Path, sha: str, base="https://r2.example") -> Path:
    p = tmp_path / "binaries.lock.json"
    p.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.2.2",
                "baseUrl": base,
                "binaries": {
                    "darwin-arm64": {
                        "filename": "email-agent-darwin-arm64",
                        "executable": "email-agent",
                        "sha256": sha,
                        "size": len(REAL_BYTES),
                    }
                },
            }
        )
    )
    return p


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status
        self.reason = "OK" if status == 200 else "ERR"
        self.text = ""

    @property
    def ok(self):
        return self.status_code == 200


class _FakeSession:
    def __init__(self, content, status=200):
        self._content, self._status = content, status
        self.calls = []

    def get(self, url, timeout=None, headers=None, stream=False):
        self.calls.append(url)
        return _FakeResp(self._content, self._status)


def test_verify_sha256_tamper_raises():
    with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
        fetchmod.verify_sha256(b"tampered", REAL_SHA, "test")


def test_verify_sha256_match_returns_hash():
    assert fetchmod.verify_sha256(REAL_BYTES, REAL_SHA, "test") == REAL_SHA


def test_fetch_downloads_and_verifies(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(REAL_BYTES)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert res.cached is False
    assert res.sha256 == REAL_SHA
    assert Path(res.binary_path).read_bytes() == REAL_BYTES
    assert sess.calls == ["https://r2.example/email-agent-darwin-arm64"]


def test_fetch_cache_hit_skips_download(tmp_path):
    out = tmp_path / "cache"
    out.mkdir()
    (out / "email-agent").write_bytes(REAL_BYTES)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert res.cached is True
    assert sess.calls == []  # no network on cache hit


def test_fetch_tampered_download_raises_and_leaves_no_file(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(b"corrupted-bytes")
    with pytest.raises(IntegrityError):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA),
            session=sess,
        )
    # A failed verify must NOT leave a binary on disk.
    assert not (out / "email-agent").exists()


def test_fetch_placeholder_sha_refuses(tmp_path):
    sess = _FakeSession(REAL_BYTES)
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=sess,
        )
    # And no download was attempted for an unpublished platform.
    assert sess.calls == []


def test_fetch_http_error_raises(tmp_path):
    sess = _FakeSession(b"", status=404)
    with pytest.raises(RuntimeError, match="download failed"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache",
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA),
            session=sess,
        )


def test_fetch_base_url_override(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(REAL_BYTES)
    fetchmod.fetch_binary(
        out_dir=out,
        base_url="https://mirror.example/dir/",
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
    )
    assert sess.calls == ["https://mirror.example/dir/email-agent-darwin-arm64"]


def test_default_cache_dir():
    p = fetchmod.default_cache_dir()
    assert p.parts[-3:] == (".gaia", "agents", "email")


# ---------------------------------------------------------------------------
# Hub-installed binary short-circuit (#2095)
# ---------------------------------------------------------------------------


def _hub_install(cache: Path, data: bytes = REAL_BYTES, **overrides) -> Path:
    """Simulate a Hub install (#2086): verified binary + .installed sentinel."""
    cache.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "id": "email",
        "version": "0.5.0",
        "language": "python",
        "installed_at": "2026-07-15T00:00:00+00:00",
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
        "artifact_kind": "binary",
        "executable": "email-agent",
        **overrides,
    }
    binary = cache / (sentinel["executable"] or "email-agent")
    binary.write_bytes(data)
    (cache / ".installed").write_text(json.dumps(sentinel))
    return binary


def test_hub_installed_binary_spawns_despite_placeholder_lock(tmp_path):
    out = tmp_path / "email"
    binary = _hub_install(out)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, "PENDING-1648-replace"),
        session=sess,
    )
    assert res.cached is True
    assert Path(res.binary_path) == binary
    assert res.sha256 == REAL_SHA
    assert sess.calls == []


def test_hub_installed_short_circuit_precedes_lock(tmp_path):
    # Pin the ordering: a verified Hub install is returned BEFORE the lock is
    # consulted at all — an unreadable lock must not block the spawn.
    out = tmp_path / "email"
    binary = _hub_install(out)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=tmp_path / "does-not-exist.lock.json",
        session=_FakeSession(b"SHOULD-NOT-BE-USED"),
    )
    assert Path(res.binary_path) == binary


def test_placeholder_still_refuses_without_hub_install(tmp_path):
    out = tmp_path / "email"
    out.mkdir()
    with pytest.raises(PlatformError, match="placeholder sha256"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_placeholder_message_points_at_hub_not_email_release(tmp_path):
    """The placeholder-refusal message must be generic (this code serves any
    sidecar agent) and name the working install command, not the email-only
    release workflow it used to hardcode (#2347)."""
    out = tmp_path / "email"
    out.mkdir()
    with pytest.raises(PlatformError) as exc_info:
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )
    msg = str(exc_info.value)
    assert "gaia hub install email" in msg
    assert "Agent Hub" not in msg
    assert "independent of this lock" in msg
    assert "release_agent_email" not in msg
    assert "GAIA_EMAIL_AGENT_MODE" not in msg  # dev-mode remedy is the manager's job


def test_hub_installed_binary_tampered_raises(tmp_path):
    out = tmp_path / "email"
    _hub_install(out)
    (out / "email-agent").write_bytes(b"tampered-after-install")
    with pytest.raises(IntegrityError, match="reinstall"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_hub_sentinel_without_binary_kind_is_ignored(tmp_path):
    # A wheel-kind sentinel (pre-#2084 email install) vouches for no binary.
    out = tmp_path / "email"
    _hub_install(out, artifact_kind="wheel", executable="")
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_hub_sentinel_missing_binary_file_falls_through(tmp_path):
    out = tmp_path / "email"
    _hub_install(out)
    (out / "email-agent").unlink()
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=out,
            platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"),
            session=_FakeSession(REAL_BYTES),
        )


def test_force_refetches_past_hub_install(tmp_path):
    # force=True is an explicit "re-fetch from the lock" — the short-circuit
    # must not mask it. With real lock SHAs that means a fresh download.
    out = tmp_path / "email"
    _hub_install(out, artifact_sha256="0" * 64)
    sess = _FakeSession(REAL_BYTES)
    res = fetchmod.fetch_binary(
        out_dir=out,
        platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA),
        session=sess,
        force=True,
    )
    assert res.cached is False
    assert sess.calls == ["https://r2.example/email-agent-darwin-arm64"]


def test_flagship_default_lock_is_its_own_not_the_email_agents(tmp_path, monkeypatch):
    """With no explicit lock, the flagship's cache check and download use the
    flagship's own pins — a binary matching the email lock must not pass."""
    from gaia.daemon.sidecars import platform as plat

    monkeypatch.delenv("ASSETS_BASE_URL", raising=False)
    lock = plat.load_lock(agent_id="gaia")
    expected = lock.binaries["linux-x64"]
    sess = _FakeSession(b"not-the-flagship-binary")
    with pytest.raises(IntegrityError) as exc_info:
        fetchmod.fetch_binary(
            out_dir=tmp_path / "gaia",
            platform_key="linux-x64",
            session=sess,
            agent_dir_name="gaia",
        )
    assert expected.sha256 in str(exc_info.value)
    assert sess.calls == [fetchmod._join_url(lock.base_url, expected.filename)]
    assert "/agents/gaia/" in sess.calls[0]


# ---------------------------------------------------------------------------
# Installer-placed flagship (#7187): install.sh / install.ps1 leave gaia-agent
# in GAIA's bin dir with gaia-agent.install.json beside it, and no hub sentinel.
# ---------------------------------------------------------------------------

AGENT_BYTES = b"installer-placed-flagship"
AGENT_SHA = hashlib.sha256(AGENT_BYTES).hexdigest()


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    """A fresh GAIA home with no lock: what an installed (frozen) gaia sees."""
    from gaia.daemon.sidecars import platform as plat

    home = tmp_path / "home" / ".gaia"
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setattr(
        plat, "default_lock_path", lambda agent_id="email": tmp_path / "no.lock"
    )
    monkeypatch.setattr("gaia.frozen.is_frozen", lambda: False)
    return home


def _installer_place(bin_dir: Path, data=AGENT_BYTES, record=None) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    agent = bin_dir / fetchmod.flagship_executable_name()
    agent.write_bytes(data)
    agent.chmod(0o755)
    if record is not False:
        body = record or {"id": "gaia", "version": "0.3.9", "sha256": AGENT_SHA}
        (bin_dir / "gaia-agent.install.json").write_text(
            body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
        )
    return agent


def _fetch_flagship():
    return fetchmod.fetch_binary(agent_dir_name="gaia", session=_FakeSession(b"no"))


def test_installer_placed_agent_is_started_with_its_recorded_version(gaia_home):
    agent = _installer_place(gaia_home / "bin")

    res = _fetch_flagship()

    assert Path(res.binary_path) == agent
    assert res.version == "0.3.9"
    assert res.sha256 == AGENT_SHA


def test_installer_record_from_windows_powershell_with_a_bom_is_read(gaia_home):
    body = json.dumps({"id": "gaia", "version": "0.3.9", "sha256": AGENT_SHA.upper()})
    agent = _installer_place(gaia_home / "bin", record="\ufeff" + body)

    assert Path(_fetch_flagship().binary_path) == agent


def test_installer_placed_agent_without_a_record_is_not_started(gaia_home):
    _installer_place(gaia_home / "bin", record=False)

    with pytest.raises(BinaryNotFoundError) as exc_info:
        _fetch_flagship()
    msg = str(exc_info.value)
    assert "gaia init" in msg
    # The lock is a source-checkout file; an installed GAIA must not lead with it.
    assert "binaries.lock.json" not in msg
    assert "no.lock" not in msg


def test_a_replaced_installer_agent_is_refused(gaia_home):
    agent = _installer_place(gaia_home / "bin")
    agent.write_bytes(b"swapped-after-install")

    with pytest.raises(IntegrityError, match="does not match the SHA-256") as e:
        _fetch_flagship()
    assert "Re-run the GAIA installer" in str(e.value)


def test_a_truncated_installer_agent_is_refused(gaia_home):
    agent = _installer_place(gaia_home / "bin")
    agent.write_bytes(AGENT_BYTES[:5])

    with pytest.raises(IntegrityError):
        _fetch_flagship()


@pytest.mark.parametrize(
    "record",
    [
        "{not json",
        "[]",
        json.dumps({"id": "gaia", "version": "0.3.9"}),
        json.dumps({"id": "gaia", "sha256": AGENT_SHA}),
        json.dumps({"id": "gaia", "version": "", "sha256": AGENT_SHA}),
        json.dumps({"id": "gaia", "version": "0.3.9", "sha256": "PENDING"}),
        json.dumps({"id": "gaia", "version": "0.3.9", "sha256": AGENT_SHA + "00"}),
        json.dumps({"id": "gaia", "version": 3, "sha256": AGENT_SHA}),
    ],
)
def test_an_unusable_install_record_is_refused_not_trusted(gaia_home, record):
    _installer_place(gaia_home / "bin", record=record)

    with pytest.raises(IntegrityError, match="install record"):
        _fetch_flagship()


def test_a_record_with_no_agent_beside_it_vouches_for_nothing(gaia_home):
    bin_dir = gaia_home / "bin"
    _installer_place(bin_dir).unlink()

    with pytest.raises(BinaryNotFoundError):
        _fetch_flagship()


def test_a_directory_named_like_the_agent_is_not_the_agent(gaia_home):
    bin_dir = gaia_home / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / fetchmod.flagship_executable_name()).mkdir()
    (bin_dir / "gaia-agent.install.json").write_text(
        json.dumps({"id": "gaia", "version": "0.3.9", "sha256": AGENT_SHA})
    )

    with pytest.raises(BinaryNotFoundError):
        _fetch_flagship()


def test_the_hub_install_wins_over_the_installer_copy(gaia_home):
    """`gaia init` may have installed a newer flagship since the installer ran."""
    _installer_place(gaia_home / "bin")
    hub_bytes = b"hub-installed-flagship"
    hub = _hub_install(
        gaia_home / "agents" / "gaia",
        data=hub_bytes,
        id="gaia",
        executable=fetchmod.flagship_executable_name(),
    )

    assert Path(_fetch_flagship().binary_path) == hub


def test_only_the_flagship_uses_the_installer_copy(gaia_home):
    _installer_place(gaia_home / "bin")

    assert fetchmod.installer_verified_binary("email") is None


def test_a_recorded_agent_on_a_pip_installs_path_is_not_trusted(
    gaia_home, tmp_path, monkeypatch
):
    """Only a frozen gaia trusts PATH; a pip install's PATH is not an installer's."""
    path_dir = tmp_path / "path"
    _installer_place(path_dir)
    monkeypatch.setenv("PATH", str(path_dir))

    assert fetchmod.installer_verified_binary("gaia") is None


def test_an_installer_agent_beside_the_frozen_gaia_is_verified(
    gaia_home, tmp_path, monkeypatch
):
    install_dir = tmp_path / "Programs" / "GAIA"
    agent = _installer_place(install_dir)
    monkeypatch.setattr("gaia.frozen.is_frozen", lambda: True)
    monkeypatch.setattr(fetchmod.sys, "executable", str(install_dir / "gaia.exe"))

    assert Path(_fetch_flagship().binary_path) == agent
