# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Security-tier, native-trust, and deprecation tests for the Agent Hub.

Covers issue #1100 (Phase 3D/3E): non-verified native agents require an explicit
trust opt-in to install, deprecated agents warn on install and are hidden from
the default catalog listing, and the catalog surfaces ``security_tier`` and a
``requires_trust`` flag. All HTTP/pip work is mocked.
"""

import hashlib
import io
import logging
import zipfile

import pytest

from gaia.hub import catalog as catalog_mod
from gaia.hub import installer
from gaia.hub.installer import (
    TrustRequiredError,
    ensure_trust_ack,
    install,
    read_sentinel,
    requires_trust_ack,
)

BASE = "https://hub.test"


# ---------------------------------------------------------------------------
# Manifest builders
# ---------------------------------------------------------------------------


def _python_manifest(agent_id="demo", version="1.0.0", **extra):
    artifact_bytes = b"wheel-bytes"
    sha = hashlib.sha256(artifact_bytes).hexdigest()
    path = f"agents/{agent_id}/{version}/{agent_id}-{version}.whl"
    return {
        "id": agent_id,
        "language": "python",
        # Default to verified so helpers that don't care about the trust gate
        # (e.g. the deprecation-warning test) install without an opt-in; callers
        # that test the gate pass an explicit security_tier via **extra.
        "security_tier": "verified",
        "latest_version": version,
        "requirements": {"platforms": []},
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
        **extra,
    }


def _python_fetcher(manifest):
    version = manifest["latest_version"]
    artifact_path = manifest["versions"][version]["artifact"]["path"]

    def fetcher(url):
        if url.endswith("/gaia-agent.yaml"):
            return b"id: demo\nname: Demo\n"
        if url == f"{BASE}/{artifact_path}":
            return b"wheel-bytes"
        raise AssertionError(f"unexpected fetch url: {url}")

    return fetcher


def _native_manifest(agent_id="native", version="1.0.0", security_tier="experimental"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bin/native", "binary")
    archive = buf.getvalue()
    sha = hashlib.sha256(archive).hexdigest()
    path = f"agents/{agent_id}/{version}/{agent_id}.zip"
    manifest = {
        "id": agent_id,
        "language": "cpp",
        "security_tier": security_tier,
        "latest_version": version,
        "requirements": {"platforms": []},
        "versions": {
            version: {
                "version": version,
                "artifact": {
                    "filename": f"{agent_id}.zip",
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
        raise AssertionError(f"unexpected fetch url: {url}")

    return manifest, fetcher


@pytest.fixture(autouse=True)
def _clean_state():
    installer.clear_progress()
    installer._IN_PROGRESS.clear()  # noqa: SLF001
    yield
    installer.clear_progress()
    installer._IN_PROGRESS.clear()  # noqa: SLF001


# ---------------------------------------------------------------------------
# requires_trust_ack / ensure_trust_ack
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "language,tier,expected",
    [
        ("cpp", "experimental", True),
        ("cpp", "community", True),
        ("cpp", "verified", False),
        ("python", "experimental", True),
        ("python", "community", True),
        ("python", "verified", False),
    ],
)
def test_requires_trust_ack_matrix(language, tier, expected):
    manifest = {"language": language, "security_tier": tier}
    assert requires_trust_ack(manifest) is expected


def test_ensure_trust_ack_raises_without_optin():
    manifest = {"language": "cpp", "security_tier": "community"}
    with pytest.raises(TrustRequiredError):
        ensure_trust_ack("native", manifest, trusted=False)


def test_ensure_trust_ack_allows_with_optin():
    manifest = {"language": "cpp", "security_tier": "community"}
    # Should not raise.
    ensure_trust_ack("native", manifest, trusted=True)


def test_ensure_trust_ack_allows_verified_native_without_optin():
    manifest = {"language": "cpp", "security_tier": "verified"}
    ensure_trust_ack("native", manifest, trusted=False)


# ---------------------------------------------------------------------------
# install() enforcement
# ---------------------------------------------------------------------------


def test_install_native_non_verified_refused_without_trust(tmp_path):
    manifest, fetcher = _native_manifest(security_tier="experimental")
    with pytest.raises(TrustRequiredError):
        install(
            "native",
            manifest=manifest,
            base_url=BASE,
            fetcher=fetcher,
            install_root=tmp_path,
        )
    # Nothing installed; progress recorded the failure.
    assert read_sentinel("native", tmp_path) is None
    assert installer.get_install_status("native")["status"] == "failed"


def test_install_native_non_verified_succeeds_with_trust(tmp_path):
    manifest, fetcher = _native_manifest(security_tier="community")
    result = install(
        "native",
        manifest=manifest,
        base_url=BASE,
        fetcher=fetcher,
        install_root=tmp_path,
        trusted=True,
    )
    assert result.language == "cpp"
    assert (tmp_path / "native" / "bin" / "native").exists()


def test_install_native_verified_succeeds_without_trust(tmp_path):
    manifest, fetcher = _native_manifest(security_tier="verified")
    result = install(
        "native",
        manifest=manifest,
        base_url=BASE,
        fetcher=fetcher,
        install_root=tmp_path,
    )
    assert result.language == "cpp"


def test_install_python_non_verified_refused_without_trust(tmp_path):
    # A non-verified PYTHON agent also runs third-party code on the user's
    # machine, so it must require the same explicit trust opt-in as a native one.
    manifest = _python_manifest(security_tier="experimental")
    with pytest.raises(TrustRequiredError):
        install(
            "demo",
            manifest=manifest,
            base_url=BASE,
            fetcher=_python_fetcher(manifest),
            run_pip=lambda args: None,
            install_root=tmp_path,
        )
    assert read_sentinel("demo", tmp_path) is None


def test_install_python_non_verified_succeeds_with_trust(tmp_path):
    manifest = _python_manifest(security_tier="community")
    result = install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_python_fetcher(manifest),
        run_pip=lambda args: None,
        install_root=tmp_path,
        trusted=True,
    )
    assert result.language == "python"


def test_install_python_verified_succeeds_without_trust(tmp_path):
    manifest = _python_manifest(security_tier="verified")
    result = install(
        "demo",
        manifest=manifest,
        base_url=BASE,
        fetcher=_python_fetcher(manifest),
        run_pip=lambda args: None,
        install_root=tmp_path,
    )
    assert result.language == "python"


# ---------------------------------------------------------------------------
# Deprecation warning on install
# ---------------------------------------------------------------------------


def test_install_deprecated_agent_warns(tmp_path, caplog):
    manifest = _python_manifest(deprecated=True)
    with caplog.at_level(logging.WARNING, logger="gaia.hub.installer"):
        install(
            "demo",
            manifest=manifest,
            base_url=BASE,
            fetcher=_python_fetcher(manifest),
            run_pip=lambda args: None,
            install_root=tmp_path,
        )
    # Install still succeeds, but a deprecation warning was emitted.
    assert read_sentinel("demo", tmp_path) is not None
    assert any("deprecated" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Catalog: requires_trust + deprecated filtering
# ---------------------------------------------------------------------------


def _index_entry(agent_id, **extra):
    return {"id": agent_id, "name": agent_id, "latest_version": "1.0.0", **extra}


def test_catalog_surfaces_security_tier_and_requires_trust():
    agents = [
        _index_entry("verified-cpp", language="cpp", security_tier="verified"),
        _index_entry("community-cpp", language="cpp", security_tier="community"),
        _index_entry(
            "experimental-py", language="python", security_tier="experimental"
        ),
    ]
    merged = catalog_mod.merge_with_registry(agents, registry=None)
    by_id = {a["id"]: a for a in merged}
    assert by_id["verified-cpp"]["security_tier"] == "verified"
    assert by_id["verified-cpp"]["requires_trust"] is False
    assert by_id["community-cpp"]["requires_trust"] is True
    assert by_id["experimental-py"]["requires_trust"] is True


def test_catalog_hides_deprecated_available_by_default():
    agents = [
        _index_entry("good", language="python", security_tier="verified"),
        _index_entry(
            "old", language="python", security_tier="community", deprecated=True
        ),
    ]
    merged = catalog_mod.merge_with_registry(agents, registry=None)
    ids = {a["id"] for a in merged}
    assert "good" in ids
    assert "old" not in ids  # deprecated + available ⇒ hidden


def test_catalog_includes_deprecated_when_requested():
    agents = [
        _index_entry(
            "old", language="python", security_tier="community", deprecated=True
        ),
    ]
    merged = catalog_mod.merge_with_registry(
        agents, registry=None, include_deprecated=True
    )
    assert {a["id"] for a in merged} == {"old"}
    assert merged[0]["deprecated"] is True
