# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for gaia.hub.installer — install/uninstall/rollback lifecycle.

All HTTP and pip work is mocked; no live network, no real ``uv``.
"""

import hashlib

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
