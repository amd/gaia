# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.installer — install/uninstall/rollback lifecycle.

All HTTP and pip work is mocked; no live network, no real ``uv``.
"""

import hashlib
import json
import os
import stat

import pytest

from gaia.hub import installer
from gaia.hub.installer import (
    ChecksumError,
    DiskSpaceError,
    InstallError,
    InstallInProgressError,
    NotInstalledError,
    install,
    list_installed,
    read_sentinel,
    rollback,
    uninstall,
)

BASE = "https://hub.test"


def _artifact_bytes(tag=b"wheel-bytes"):
    return tag


def _manifest(agent_id="demo", version="1.0.0", artifact_bytes=b"wheel-bytes", **reqs):
    sha = hashlib.sha256(artifact_bytes).hexdigest()
    path = f"agents/{agent_id}/{version}/{agent_id}-{version}.whl"
    return {
        "id": agent_id,
        "language": "python",
        "latest_version": version,
        "requirements": {"platforms": [], **reqs},
        "versions": {
            version: {
                "version": version,
                "artifact": {
                    "filename": f"{agent_id}-{version}-py3-none-any.whl",
                    "path": path,
                    "size_bytes": len(artifact_bytes),
                    "sha256": sha,
                    "content_type": "application/octet-stream",
                },
            }
        },
    }


def _make_fetcher(manifest, agent_id="demo", artifact_bytes=b"wheel-bytes"):
    version = manifest["latest_version"]
    artifact_path = manifest["versions"][version]["artifact"]["path"]

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: demo\nname: Demo\n"
        if url == f"{BASE}/{artifact_path}":
            return artifact_bytes
        raise AssertionError(f"unexpected fetch url: {url}")

    return fetcher


class _FakeRegistry:
    def __init__(self):
        self.discovered = 0
        self._agents = {}
        import threading

        self._lock = threading.Lock()

    def discover_installed_agents(self):
        self.discovered += 1
        self._agents["demo"] = object()

    def get(self, agent_id):
        return self._agents.get(agent_id)


@pytest.fixture(autouse=True)
def _clean_state():
    installer.clear_progress()
    installer._IN_PROGRESS.clear()  # noqa: SLF001
    yield
    installer.clear_progress()
    installer._IN_PROGRESS.clear()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_install_happy_path(tmp_path):
    manifest = _manifest()
    pip_calls = []
    reg = _FakeRegistry()

    result = install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_make_fetcher(manifest),
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        registry=reg,
    )

    assert result.version == "1.0.0"
    assert result.updated is False
    assert result.hot_registered is True
    # sentinel written
    sentinel = read_sentinel("demo", tmp_path)
    assert sentinel is not None
    assert sentinel.version == "1.0.0"
    # gaia-agent.yaml copied
    assert (tmp_path / "demo" / "gaia-agent.yaml").exists()
    # uv pip install called targeting site-packages
    assert pip_calls and "--target" in pip_calls[0]
    # progress completed
    status = installer.get_install_status("demo")
    assert status["status"] == "completed"
    assert status["percent"] == 100
    # registry hot-registered
    assert reg.discovered == 1
    # list_installed reflects it
    assert "demo" in list_installed(tmp_path)


# ---------------------------------------------------------------------------
# Checksum mismatch
# ---------------------------------------------------------------------------


def test_install_checksum_mismatch(tmp_path):
    manifest = _manifest(artifact_bytes=b"correct")
    # Fetcher returns DIFFERENT bytes than the manifest's sha256.
    bad_fetcher = _make_fetcher(manifest, artifact_bytes=b"tampered")

    with pytest.raises(ChecksumError):
        install(
            "demo",
            manifest=manifest,
            base_url=BASE,
            fetcher=bad_fetcher,
            run_pip=lambda args: None,
            install_root=tmp_path,
        )
    # Nothing installed.
    assert read_sentinel("demo", tmp_path) is None
    assert installer.get_install_status("demo")["status"] == "failed"


# ---------------------------------------------------------------------------
# Disk-space block
# ---------------------------------------------------------------------------


def test_install_disk_space_block(tmp_path, monkeypatch):
    from gaia.hub import compatibility

    monkeypatch.setattr(compatibility, "detect_free_disk_gb", lambda _p: 0.1)
    manifest = _manifest(min_disk_gb=100)  # needs 100 GB, only 0.1 free

    with pytest.raises(DiskSpaceError):
        install(
            "demo",
            manifest=manifest,
            base_url=BASE,
            fetcher=_make_fetcher(manifest),
            run_pip=lambda args: None,
            install_root=tmp_path,
        )
    assert read_sentinel("demo", tmp_path) is None


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------


def test_install_concurrency_guard(tmp_path):
    manifest = _manifest()
    with installer._install_slot("demo"):  # noqa: SLF001 - simulate in-flight
        with pytest.raises(InstallInProgressError):
            install(
                "demo",
                manifest=manifest,
                base_url=BASE,
                fetcher=_make_fetcher(manifest),
                run_pip=lambda args: None,
                install_root=tmp_path,
            )


# ---------------------------------------------------------------------------
# Update creates backup; rollback restores
# ---------------------------------------------------------------------------


def test_update_then_rollback(tmp_path):
    v1 = _manifest(version="1.0.0")
    install(
        "demo",
        manifest=v1,
        base_url=BASE,
        fetcher=_make_fetcher(v1),
        run_pip=lambda args: None,
        install_root=tmp_path,
    )
    assert read_sentinel("demo", tmp_path).version == "1.0.0"

    v2 = _manifest(version="2.0.0", artifact_bytes=b"wheel-v2")
    result = install(
        "demo",
        manifest=v2,
        base_url=BASE,
        fetcher=_make_fetcher(v2, artifact_bytes=b"wheel-v2"),
        run_pip=lambda args: None,
        install_root=tmp_path,
    )
    assert result.updated is True
    assert read_sentinel("demo", tmp_path).version == "2.0.0"

    restored = rollback("demo", install_root=tmp_path)
    assert restored.version == "1.0.0"
    assert read_sentinel("demo", tmp_path).version == "1.0.0"


def test_rollback_without_backup_raises(tmp_path):
    with pytest.raises(InstallError):
        rollback("demo", install_root=tmp_path)


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def test_uninstall_refuses_builtin(tmp_path):
    with pytest.raises(InstallError):
        uninstall("chat", install_root=tmp_path)


def test_uninstall_not_installed_raises(tmp_path):
    with pytest.raises(NotInstalledError):
        uninstall("demo", install_root=tmp_path)


def test_uninstall_removes_agent(tmp_path):
    manifest = _manifest()
    install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_make_fetcher(manifest),
        run_pip=lambda args: None,
        install_root=tmp_path,
    )
    assert (tmp_path / "demo").exists()
    uninstall("demo", install_root=tmp_path)
    assert not (tmp_path / "demo").exists()
    assert read_sentinel("demo", tmp_path) is None


# ---------------------------------------------------------------------------
# C++ artifact extraction
# ---------------------------------------------------------------------------


def test_install_cpp_artifact_extracted(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bin/demo", "binary")
    archive = buf.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    path = "agents/native/1.0.0/native.zip"
    manifest = {
        "id": "native",
        "language": "cpp",
        "latest_version": "1.0.0",
        "requirements": {"platforms": []},
        "versions": {
            "1.0.0": {
                "version": "1.0.0",
                "artifact": {
                    "filename": "native.zip",
                    "path": path,
                    "size_bytes": len(archive),
                    "sha256": sha,
                    "content_type": "application/zip",
                },
            }
        },
    }

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: native\n"
        if url == f"{BASE}/{path}":
            return archive
        raise AssertionError(url)

    result = install(
        "native",
        manifest=manifest,
        base_url=BASE,
        fetcher=fetcher,
        install_root=tmp_path,
        trust_native=True,  # experimental native agent — explicit trust required
    )
    assert result.language == "cpp"
    assert (tmp_path / "native" / "bin" / "demo").exists()
    # Native agents do not hot-register in-process.
    assert result.hot_registered is False


# ---------------------------------------------------------------------------
# Setup executor: progressive / resumable / parallel (#468)
# ---------------------------------------------------------------------------


def _sized_manifest(agent_id, size_bytes, version="1.0.0"):
    """A manifest whose artifact declares a specific size (for ordering tests)."""
    m = _manifest(agent_id=agent_id, version=version)
    m["id"] = agent_id
    m["versions"][version]["artifact"]["size_bytes"] = size_bytes
    return m


def test_run_setup_orders_smallest_first(tmp_path):
    manifests = {
        "big": _sized_manifest("big", 9000),
        "small": _sized_manifest("small", 100),
        "mid": _sized_manifest("mid", 500),
    }
    order = []

    def fake_install(agent_id, **kwargs):
        order.append(agent_id)
        return None

    result = installer.run_setup(
        manifests,
        max_parallel=1,  # serial so observed order == plan order
        install_root=tmp_path,
        state_path=tmp_path / "setup_state.json",
        installer_fn=fake_install,
    )
    assert order == ["small", "mid", "big"]
    assert result.all_ok
    assert set(result.completed) == {"small", "mid", "big"}


def test_run_setup_resume_skips_completed(tmp_path):
    state_path = tmp_path / "setup_state.json"
    # Prior run completed "a"; "b" is still pending.
    state_path.write_text(
        json.dumps(
            {
                "status": "running",
                "steps": [
                    {"agent_id": "a", "status": "completed"},
                    {"agent_id": "b", "status": "pending"},
                ],
            }
        ),
        encoding="utf-8",
    )
    manifests = {"a": _sized_manifest("a", 100), "b": _sized_manifest("b", 200)}
    installed = []

    def fake_install(agent_id, **kwargs):
        installed.append(agent_id)
        return None

    result = installer.run_setup(
        manifests,
        max_parallel=2,
        resume=True,
        install_root=tmp_path,
        state_path=state_path,
        installer_fn=fake_install,
    )
    # "a" was already completed → not re-installed; only "b" runs.
    assert installed == ["b"]
    assert result.all_ok


def test_run_setup_respects_concurrency_bound(tmp_path):
    import threading
    import time

    manifests = {f"agent{i}": _sized_manifest(f"agent{i}", i) for i in range(6)}

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_install(agent_id, **kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)  # hold the slot so overlap is observable
        with lock:
            state["active"] -= 1
        return None

    installer.run_setup(
        manifests,
        max_parallel=2,
        install_root=tmp_path,
        state_path=tmp_path / "setup_state.json",
        installer_fn=fake_install,
    )
    assert state["peak"] <= 2


def test_run_setup_failed_step_does_not_block_others(tmp_path):
    manifests = {
        "good": _sized_manifest("good", 100),
        "bad": _sized_manifest("bad", 50),
    }

    def fake_install(agent_id, **kwargs):
        if agent_id == "bad":
            raise installer.InstallError("boom")
        return None

    result = installer.run_setup(
        manifests,
        max_parallel=2,
        install_root=tmp_path,
        state_path=tmp_path / "setup_state.json",
        installer_fn=fake_install,
    )
    assert "good" in result.completed
    assert "bad" in result.failed
    assert not result.all_ok


def test_run_setup_persists_state_file(tmp_path):
    state_path = tmp_path / "setup_state.json"
    manifests = {"a": _sized_manifest("a", 100)}

    installer.run_setup(
        manifests,
        install_root=tmp_path,
        state_path=state_path,
        installer_fn=lambda agent_id, **kwargs: None,
    )
    assert state_path.exists()
    saved = installer.read_setup_state(state_path)
    assert saved["status"] == "completed"
    assert saved["steps"][0]["agent_id"] == "a"
    assert saved["steps"][0]["status"] == "completed"


def test_run_setup_rejects_bad_concurrency(tmp_path):
    with pytest.raises(InstallError):
        installer.run_setup(
            {"a": _sized_manifest("a", 1)},
            max_parallel=0,
            install_root=tmp_path,
            state_path=tmp_path / "s.json",
            installer_fn=lambda agent_id, **kwargs: None,
        )


# ---------------------------------------------------------------------------
# Platform-selection binary installs (#2084)
#
# The hub installer only read the manifest's legacy singular ``artifact``
# field (always the first-published macOS binary), so Windows/Linux hosts got
# fed a macOS executable into ``uv pip install``. These tests exercise the
# fix: selecting the right per-platform entry from ``versions[v].artifacts[]``
# via the new ``install(..., platform_key=...)`` DI seam. Manifest shapes
# mirror the real ``hub.amd-gaia.ai/agents/email/manifest.json``.
# ---------------------------------------------------------------------------

_PLATFORM_FILENAMES = {
    "win32-x64": "email-agent-win32-x64.exe",
    "darwin-arm64": "email-agent-darwin-arm64",
    "darwin-x64": "email-agent-darwin-x64",
    "linux-x64": "email-agent-linux-x64",
}

_FILENAME_BYTES = {
    "email-agent-win32-x64.exe": b"win32-x64-binary-bytes",
    "email-agent-darwin-arm64": b"darwin-arm64-binary-bytes",
    "email-agent-darwin-x64": b"darwin-x64-binary-bytes",
    "email-agent-linux-x64": b"linux-x64-binary-bytes",
}


def _artifact_entry(filename, data, path_prefix):
    return {
        "filename": filename,
        "path": f"{path_prefix}/{filename}",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": "application/octet-stream",
    }


def _binary_manifest(
    agent_id="email",
    version="0.1.0",
    platform_keys=("win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"),
    filename_bytes=None,
    singular_key="darwin-arm64",
    extra_wheel=False,
):
    """Live-shape manifest: singular ``artifact`` is the macOS-first entry the
    pre-fix installer always used, plus a full ``artifacts[]`` array."""
    merged_bytes = {**_FILENAME_BYTES, **(filename_bytes or {})}
    prefix = f"agents/{agent_id}/{version}"
    artifacts = [
        _artifact_entry(
            _PLATFORM_FILENAMES[k], merged_bytes[_PLATFORM_FILENAMES[k]], prefix
        )
        for k in platform_keys
    ]
    if extra_wheel:
        wheel_filename = f"{agent_id}-{version}-py3-none-any.whl"
        artifacts.append(
            _artifact_entry(wheel_filename, b"wheel-bytes-in-artifacts", prefix)
        )
    singular_filename = _PLATFORM_FILENAMES[singular_key]
    singular = next(a for a in artifacts if a["filename"] == singular_filename)
    return {
        "id": agent_id,
        "language": "python",
        "latest_version": version,
        "requirements": {"platforms": []},
        "versions": {
            version: {
                "version": version,
                "artifact": singular,
                "artifacts": artifacts,
            }
        },
    }


def _legacy_binary_manifest(
    agent_id="email",
    version="0.1.0",
    filename="email-agent-darwin-arm64",
    data=b"legacy-binary-bytes",
):
    """Old manifest shape: a bare-executable singular ``artifact``, no
    ``artifacts[]`` array at all (pre-#1648 published versions)."""
    sha = hashlib.sha256(data).hexdigest()
    path = f"agents/{agent_id}/{version}/{filename}"
    return {
        "id": agent_id,
        "language": "python",
        "latest_version": version,
        "requirements": {"platforms": []},
        "versions": {
            version: {
                "version": version,
                "artifact": {
                    "filename": filename,
                    "path": path,
                    "size_bytes": len(data),
                    "sha256": sha,
                    "content_type": "application/octet-stream",
                },
            }
        },
    }


def _binary_fetcher(manifest, filename_bytes=None):
    merged_bytes = {**_FILENAME_BYTES, **(filename_bytes or {})}
    version = manifest["latest_version"]
    entry = manifest["versions"][version]
    by_path = {
        a["path"]: merged_bytes.get(a["filename"], b"")
        for a in entry.get("artifacts") or []
    }
    singular = entry["artifact"]
    by_path.setdefault(singular["path"], merged_bytes.get(singular["filename"], b""))

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: email\nname: Email\n"
        for path, data in by_path.items():
            if url == f"{BASE}/{path}":
                return data
        raise AssertionError(f"unexpected fetch url: {url}")

    return fetcher


def _refuse_pip(args):
    raise AssertionError(f"run_pip must not be called for a binary install: {args}")


# --- T1: platform_key picks the matching per-platform binary, never pip ----


def test_install_binary_selects_win32_and_skips_pip(tmp_path):
    manifest = _binary_manifest()
    downloaded_urls = []
    inner_fetcher = _binary_fetcher(manifest)

    def tracking_fetcher(url):
        downloaded_urls.append(url)
        return inner_fetcher(url)

    result = install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=tracking_fetcher,
        run_pip=_refuse_pip,
        install_root=tmp_path,
        platform_key="win32-x64",
    )

    assert any(u.endswith("email-agent-win32-x64.exe") for u in downloaded_urls)
    exe_path = tmp_path / "email" / "email-agent.exe"
    assert exe_path.exists()
    sentinel = read_sentinel("email", tmp_path)
    assert sentinel is not None
    assert sentinel.artifact_kind == "binary"
    assert result.hot_registered is False


# --- T2: correct artifact + generic name per platform; +x bit on POSIX -----


@pytest.mark.parametrize(
    "platform_key,expected_filename,expected_generic",
    [
        ("win32-x64", "email-agent-win32-x64.exe", "email-agent.exe"),
        ("darwin-arm64", "email-agent-darwin-arm64", "email-agent"),
        ("linux-x64", "email-agent-linux-x64", "email-agent"),
    ],
)
def test_install_binary_platform_selection(
    tmp_path, platform_key, expected_filename, expected_generic
):
    manifest = _binary_manifest()
    downloaded_urls = []
    inner_fetcher = _binary_fetcher(manifest)

    def tracking_fetcher(url):
        downloaded_urls.append(url)
        return inner_fetcher(url)

    install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=tracking_fetcher,
        run_pip=_refuse_pip,
        install_root=tmp_path,
        platform_key=platform_key,
    )
    assert any(u.endswith(expected_filename) for u in downloaded_urls)
    installed_path = tmp_path / "email" / expected_generic
    assert installed_path.exists()
    if platform_key != "win32-x64" and os.name == "posix":
        mode = installed_path.stat().st_mode
        assert mode & stat.S_IXUSR


# --- T3: artifacts[] with both a wheel and binaries; never falls back ------


def test_install_binary_prefers_binary_over_wheel_in_artifacts(tmp_path):
    manifest = _binary_manifest(extra_wheel=True)
    pip_calls = []
    install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=_binary_fetcher(manifest),
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        platform_key="linux-x64",
    )
    assert pip_calls == []
    assert (tmp_path / "email" / "email-agent").exists()


def test_install_binary_no_match_never_falls_back_to_wheel(tmp_path):
    manifest = _binary_manifest(extra_wheel=True)
    pip_calls = []
    with pytest.raises(InstallError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=_binary_fetcher(manifest),
            run_pip=lambda args: pip_calls.append(args),
            install_root=tmp_path,
            platform_key="freebsd-riscv64",
        )
    assert pip_calls == []


# --- T4: wheel-only manifest is unaffected (regression guard) --------------


def test_install_wheel_only_manifest_unaffected_by_platform_selection(tmp_path):
    manifest = _manifest()
    pip_calls = []
    install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_make_fetcher(manifest),
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        platform_key="linux-x64",
    )
    # Byte-identical to test_install_happy_path's pip-args assertion.
    assert pip_calls and "--target" in pip_calls[0]
    sentinel = read_sentinel("demo", tmp_path)
    assert sentinel.artifact_kind == "wheel"


# --- T4b: modern worker shape — wheel-only artifacts[] still routes to pip --


def test_install_modern_wheel_only_artifacts_routes_to_pip(tmp_path):
    # The hub worker writes artifacts: [artifact] for EVERY publish, so a modern
    # wheel-only agent has an artifacts[] containing exactly one .whl entry (the
    # same dict as the singular artifact). artifacts[] presence must NOT imply
    # binary: a wheel/sdist-only artifacts[] selects the wheel and takes pip
    # regardless of platform_key.
    manifest = _manifest()
    version = manifest["latest_version"]
    manifest["versions"][version]["artifacts"] = [
        manifest["versions"][version]["artifact"]
    ]
    pip_calls = []
    install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_make_fetcher(manifest),
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        platform_key="win32-x64",
    )
    assert pip_calls and "--target" in pip_calls[0]
    assert any(str(a).endswith(".whl") for a in pip_calls[0])
    sentinel = read_sentinel("demo", tmp_path)
    assert sentinel.artifact_kind == "wheel"


# --- T4c: cpp agents with an artifacts[] list behave like the legacy route --


def test_install_cpp_with_artifacts_list_uses_singular_zip(tmp_path):
    # cpp is classified FIRST, before any artifacts[] logic — a cpp manifest that
    # also carries artifacts[] extracts the singular zip exactly like the legacy
    # cpp route (mirror of test_install_cpp_artifact_extracted).
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bin/demo", "binary")
    archive = buf.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    path = "agents/native/1.0.0/native.zip"
    artifact = {
        "filename": "native.zip",
        "path": path,
        "size_bytes": len(archive),
        "sha256": sha,
        "content_type": "application/zip",
    }
    manifest = {
        "id": "native",
        "language": "cpp",
        "latest_version": "1.0.0",
        "requirements": {"platforms": []},
        "versions": {
            "1.0.0": {
                "version": "1.0.0",
                "artifact": artifact,
                "artifacts": [artifact],
            }
        },
    }

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: native\n"
        if url == f"{BASE}/{path}":
            return archive
        raise AssertionError(url)

    pip_calls = []
    result = install(
        "native",
        manifest=manifest,
        base_url=BASE,
        fetcher=fetcher,
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        platform_key="linux-x64",
        trust_native=True,
    )
    assert result.language == "cpp"
    assert (tmp_path / "native" / "bin" / "demo").exists()
    assert pip_calls == []
    sentinel = read_sentinel("native", tmp_path)
    assert sentinel.artifact_kind == "cpp"


# --- T5: binaries-only manifest, no artifact for platform_key --------------


def test_install_binary_missing_platform_raises_loud_error(tmp_path):
    manifest = _binary_manifest(
        platform_keys=("win32-x64", "linux-x64"), singular_key="win32-x64"
    )

    with pytest.raises(InstallError) as excinfo:
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=_binary_fetcher(manifest),
            run_pip=_refuse_pip,
            install_root=tmp_path,
            platform_key="darwin-x64",
        )
    message = str(excinfo.value)
    assert "email" in message
    assert "darwin-x64" in message
    assert "email-agent-win32-x64.exe" in message
    assert "email-agent-linux-x64" in message
    assert installer.get_install_status("email")["status"] == "failed"
    install_dir = tmp_path / "email"
    assert not install_dir.exists() or list(install_dir.iterdir()) == []


# --- T6: old-manifest shape (singular artifact IS a binary, no artifacts[]) -


def test_install_legacy_singular_binary_matches_platform(tmp_path):
    manifest = _legacy_binary_manifest(
        filename="email-agent-darwin-arm64", data=b"legacy-bytes"
    )

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: email\n"
        if url.endswith("email-agent-darwin-arm64"):
            return b"legacy-bytes"
        raise AssertionError(url)

    pip_calls = []
    install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=fetcher,
        run_pip=lambda args: pip_calls.append(args),
        install_root=tmp_path,
        platform_key="darwin-arm64",
    )
    assert pip_calls == []
    assert (tmp_path / "email" / "email-agent").exists()
    sentinel = read_sentinel("email", tmp_path)
    assert sentinel.artifact_kind == "binary"


def test_install_legacy_singular_binary_mismatch_raises(tmp_path):
    manifest = _legacy_binary_manifest(
        filename="email-agent-darwin-arm64", data=b"legacy-bytes"
    )

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: email\n"
        if url.endswith("email-agent-darwin-arm64"):
            return b"legacy-bytes"
        raise AssertionError(url)

    pip_calls = []
    with pytest.raises(InstallError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=fetcher,
            run_pip=lambda args: pip_calls.append(args),
            install_root=tmp_path,
            platform_key="win32-x64",
        )
    assert pip_calls == []


# --- T7: checksum mismatch on the platform-matched binary -------------------


def test_install_binary_checksum_mismatch(tmp_path):
    manifest = _binary_manifest()
    good_fetcher = _binary_fetcher(manifest)

    def tampered_fetcher(url):
        if url.endswith("email-agent-linux-x64"):
            return b"tampered-bytes"
        return good_fetcher(url)

    with pytest.raises(ChecksumError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=tampered_fetcher,
            run_pip=lambda args: None,
            install_root=tmp_path,
            platform_key="linux-x64",
        )
    assert read_sentinel("email", tmp_path) is None


# --- T8: update-in-place, locked-file errors, rollback/uninstall on binary --


def test_install_binary_update_replaces_without_backup_dir(tmp_path):
    manifest_v1 = _binary_manifest(version="0.1.0")
    install(
        "email",
        manifest=manifest_v1,
        base_url=BASE,
        fetcher=_binary_fetcher(manifest_v1),
        run_pip=lambda args: None,
        install_root=tmp_path,
        platform_key="linux-x64",
    )
    v2_bytes = {fn: data + b"-v2" for fn, data in _FILENAME_BYTES.items()}
    manifest_v2 = _binary_manifest(version="0.2.0", filename_bytes=v2_bytes)
    result = install(
        "email",
        manifest=manifest_v2,
        base_url=BASE,
        fetcher=_binary_fetcher(manifest_v2, filename_bytes=v2_bytes),
        run_pip=lambda args: None,
        install_root=tmp_path,
        platform_key="linux-x64",
    )
    assert result.updated is True
    assert not (tmp_path / installer.BACKUP_DIRNAME / "email").exists()
    assert read_sentinel("email", tmp_path).version == "0.2.0"


def test_install_binary_locked_file_raises_actionable_error(tmp_path, monkeypatch):
    manifest = _binary_manifest()

    def boom(*_a, **_k):
        raise PermissionError("file in use")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(InstallError) as excinfo:
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=_binary_fetcher(manifest),
            run_pip=lambda args: None,
            install_root=tmp_path,
            platform_key="linux-x64",
        )
    message = str(excinfo.value).lower()
    assert "running" in message or "close" in message


def test_rollback_binary_kind_raises_clean_error(tmp_path):
    manifest = _binary_manifest()
    install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=_binary_fetcher(manifest),
        run_pip=lambda args: None,
        install_root=tmp_path,
        platform_key="linux-x64",
    )
    with pytest.raises(InstallError) as excinfo:
        rollback("email", install_root=tmp_path)
    message = str(excinfo.value).lower()
    assert "rollback" in message or "backup" in message


def test_uninstall_binary_locked_raises_actionable_error(tmp_path, monkeypatch):
    manifest = _binary_manifest()
    install(
        "email",
        manifest=manifest,
        base_url=BASE,
        fetcher=_binary_fetcher(manifest),
        run_pip=lambda args: None,
        install_root=tmp_path,
        platform_key="linux-x64",
    )

    def boom(*_a, **_k):
        raise PermissionError("in use")

    monkeypatch.setattr(installer.shutil, "rmtree", boom)
    with pytest.raises(InstallError):
        uninstall("email", install_root=tmp_path)


# --- T9: sentinel back-compat + filename sanitization -----------------------


def test_read_sentinel_missing_artifact_kind_defaults_to_wheel(tmp_path):
    install_dir = installer.agent_install_dir("legacy", tmp_path)
    install_dir.mkdir(parents=True, exist_ok=True)
    sentinel_data = {
        "id": "legacy",
        "version": "1.0.0",
        "language": "python",
        "installed_at": "2026-01-01T00:00:00+00:00",
        "artifact_sha256": "deadbeef",
        # No "artifact_kind" key at all — simulates a pre-fix install.
    }
    (install_dir / installer.SENTINEL_NAME).write_text(
        json.dumps(sentinel_data), encoding="utf-8"
    )
    sentinel = read_sentinel("legacy", tmp_path)
    assert sentinel.artifact_kind == "wheel"


@pytest.mark.parametrize(
    "bad_filename",
    ["../evil-binary", "sub/email-agent-win32-x64.exe", "sub\\evil.exe"],
)
def test_install_rejects_path_traversal_filename_in_singular_artifact(
    tmp_path, bad_filename
):
    manifest = _legacy_binary_manifest(filename=bad_filename, data=b"evil-bytes")

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: email\n"
        return b"evil-bytes"

    with pytest.raises(InstallError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=fetcher,
            run_pip=lambda args: None,
            install_root=tmp_path,
            platform_key="win32-x64",
        )
    install_dir = tmp_path / "email"
    assert not install_dir.exists() or list(install_dir.iterdir()) == []


@pytest.mark.parametrize(
    "bad_filename", ["../evil-win32-x64.exe", "sub/email-agent-win32-x64.exe"]
)
def test_install_rejects_path_traversal_filename_in_artifacts_array(
    tmp_path, bad_filename
):
    manifest = _binary_manifest()
    version = manifest["latest_version"]
    manifest["versions"][version]["artifacts"][0]["filename"] = bad_filename
    manifest["versions"][version]["artifacts"][0][
        "path"
    ] = f"agents/email/{version}/{bad_filename}"

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: email\n"
        return b"evil-bytes"

    with pytest.raises(InstallError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=fetcher,
            run_pip=lambda args: None,
            install_root=tmp_path,
            platform_key="win32-x64",
        )
    install_dir = tmp_path / "email"
    assert not install_dir.exists() or list(install_dir.iterdir()) == []


# --- T10: parity + drift guards ---------------------------------------------


def test_generic_executable_names_match_binaries_lock(tmp_path):
    from gaia.daemon.sidecars import platform as sidecar_platform

    lock = sidecar_platform.load_lock()
    for platform_key in sidecar_platform.SUPPORTED_PLATFORMS:
        root = tmp_path / platform_key
        manifest = _binary_manifest(singular_key=platform_key)
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=_binary_fetcher(manifest),
            run_pip=_refuse_pip,
            install_root=root,
            platform_key=platform_key,
        )
        expected = lock.binaries[platform_key].executable
        assert (root / "email" / expected).exists()


@pytest.mark.parametrize(
    "plat,arch",
    [
        ("win32", "AMD64"),
        ("win32", "x86_64"),
        ("darwin", "arm64"),
        ("darwin", "x86_64"),
        ("linux", "x86_64"),
        ("linux2", "aarch64"),
        ("freebsd", "riscv64"),
    ],
)
def test_current_platform_key_parity_with_email_sidecar(plat, arch):
    from gaia.daemon.sidecars import platform as sidecar_platform
    from gaia.hub import compatibility

    assert compatibility.current_platform_key(plat, arch) == (
        sidecar_platform.current_platform_key(plat, arch)
    )


def test_install_unsupported_platform_key_hits_loud_error_not_crash(tmp_path):
    manifest = _binary_manifest()
    with pytest.raises(InstallError):
        install(
            "email",
            manifest=manifest,
            base_url=BASE,
            fetcher=_binary_fetcher(manifest),
            run_pip=lambda args: None,
            install_root=tmp_path,
            platform_key="freebsd-riscv64",
        )


# ---------------------------------------------------------------------------
# Agent-id path safety (py/path-injection)
# ---------------------------------------------------------------------------


class TestAgentIdPathSafety:
    """Traversal-shaped agent ids must fail loudly before any path is built.

    Agent ids reach the installer from untrusted surfaces (the Agent UI's
    ``POST /api/agents/install`` body, the downloaded hub catalog) and are
    joined onto ``~/.gaia/agents/`` for rmtree/move/write operations.
    """

    @pytest.mark.parametrize(
        "bad_id",
        [
            "..",
            "../evil",
            "..\\evil",
            "a/b",
            "a\\b",
            "/etc/passwd",
            "C:\\Windows\\evil",
            ".hidden",
            "",
            "a" * 200,
            "id with spaces",
            "id\x00null",
            None,
            123,
        ],
    )
    def test_traversal_ids_rejected(self, tmp_path, bad_id):
        with pytest.raises(InstallError, match="Invalid agent id"):
            installer.agent_install_dir(bad_id, tmp_path)

    def test_valid_ids_accepted(self, tmp_path):
        for good in ("email", "my-agent", "Agent_1.2", "a", "chat"):
            assert installer.agent_install_dir(good, tmp_path) == tmp_path / good

    def test_uninstall_rejects_traversal_id_without_touching_target(self, tmp_path):
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "data.txt").write_text("keep me")
        with pytest.raises(InstallError, match="Invalid agent id"):
            uninstall("../victim", install_root=tmp_path / "agents")
        assert (victim / "data.txt").read_text() == "keep me"

    def test_backup_dir_rejects_traversal_id(self, tmp_path):
        with pytest.raises(InstallError, match="Invalid agent id"):
            installer._backup_dir("../../evil", tmp_path)

    def test_lifecycle_configure_rejects_traversal_id(self, tmp_path):
        from gaia.hub import lifecycle

        with pytest.raises(lifecycle.LifecycleError, match="Invalid agent id"):
            lifecycle.configure("../evil", {"model": "x"}, install_root=tmp_path)
        assert not (tmp_path.parent / "evil").exists()

    def test_sentinel_with_unsafe_executable_is_ignored(self, tmp_path, caplog):
        agent_dir = tmp_path / "demo"
        agent_dir.mkdir(parents=True)
        (agent_dir / installer.SENTINEL_NAME).write_text(
            json.dumps(
                {
                    "id": "demo",
                    "version": "1.0.0",
                    "language": "cpp",
                    "installed_at": "now",
                    "artifact_kind": "binary",
                    "executable": "../../../usr/bin/env",
                }
            )
        )
        with caplog.at_level("WARNING"):
            assert read_sentinel("demo", tmp_path) is None
        assert "unsafe executable" in caplog.text

    def test_sentinel_with_bare_executable_is_kept(self, tmp_path):
        agent_dir = tmp_path / "demo"
        agent_dir.mkdir(parents=True)
        (agent_dir / installer.SENTINEL_NAME).write_text(
            json.dumps(
                {
                    "id": "demo",
                    "version": "1.0.0",
                    "language": "cpp",
                    "installed_at": "now",
                    "artifact_kind": "binary",
                    "executable": "gaia-email-agent.exe",
                }
            )
        )
        sentinel = read_sentinel("demo", tmp_path)
        assert sentinel is not None
        assert sentinel.executable == "gaia-email-agent.exe"

    def test_list_installed_skips_non_agent_directories(self, tmp_path):
        junk = tmp_path / "weird name!"
        junk.mkdir()
        (junk / installer.SENTINEL_NAME).write_text("{}")
        assert list_installed(tmp_path) == {}
