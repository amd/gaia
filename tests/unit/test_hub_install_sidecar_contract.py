# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract test for the install -> run seam that issue #2347 exposed.

The Agent Hub installer (:mod:`gaia.hub.installer`) writes a binary agent's
``.installed`` sentinel; user-mode sidecar spawn (:mod:`gaia.daemon.sidecars.fetch`)
reads that sentinel to locate the verified binary *without* a binaries.lock.json.
Every other test on this path either mocks the fetch or hand-writes the sentinel,
so a drift between what the installer WRITES and what the fetch RESOLVER ACCEPTS
(sentinel field names, the ``artifact_kind`` value, the generic executable name,
the SHA) would pass every existing test and still re-break the exact users #2347
is about.

This test runs the REAL ``installer.install`` (offline — injected manifest,
fetcher, run_pip) and feeds its on-disk output to the REAL ``fetch.fetch_binary``,
asserting the two modules agree on the contract. No network, no real binary.
"""

import hashlib

import pytest

from gaia.daemon.sidecars import fetch as sidecar_fetch
from gaia.hub import installer

# A fixed platform key keeps the test host-independent: a bare (no ``.exe``)
# filename installs and resolves identically on every OS (fetch never execs the
# file's bytes; on POSIX the installer chmod +x's it, on Windows it no-ops).
_PLATFORM_KEY = "linux-x64"
_BINARY_BYTES = b"FROZEN-SIDECAR-BINARY-CONTENT"
_BINARY_SHA = hashlib.sha256(_BINARY_BYTES).hexdigest()
_AGENT_ID = "contract-toy"
_VERSION = "1.0.0"
_ARTIFACT_PATH = f"agents/{_AGENT_ID}/{_VERSION}/{_AGENT_ID}-{_PLATFORM_KEY}"
_BASE_URL = "https://hub.test.example"


def _manifest() -> dict:
    return {
        "id": _AGENT_ID,
        "language": "python",  # a python agent that ships a frozen binary artifact
        "security_tier": "experimental",
        "latest_version": _VERSION,
        "versions": {
            _VERSION: {
                "version": _VERSION,
                "artifacts": [
                    {
                        "filename": f"{_AGENT_ID}-{_PLATFORM_KEY}",
                        "path": _ARTIFACT_PATH,
                        "sha256": _BINARY_SHA,
                        "size_bytes": len(_BINARY_BYTES),
                    }
                ],
            }
        },
    }


def _fetcher(url: str, timeout: float = 120) -> bytes:
    # The artifact download returns the frozen binary; the best-effort
    # gaia-agent.yaml fetch returns a trivial manifest.
    if url.endswith(_ARTIFACT_PATH):
        return _BINARY_BYTES
    if url.endswith("gaia-agent.yaml"):
        return f"id: {_AGENT_ID}\n".encode()
    raise AssertionError(f"unexpected fetch url: {url}")


def test_installer_output_is_a_sentinel_the_sidecar_fetch_accepts(tmp_path):
    """installer.install writes a binary sentinel that fetch_binary resolves with
    NO lock — the #2347 install->run contract, exercised on both real modules."""
    result = installer.install(
        _AGENT_ID,
        version=_VERSION,
        manifest=_manifest(),
        # Non-verified python agent (models the email sidecar): the install
        # now requires the explicit trust opt-in every non-verified agent needs.
        trusted=True,
        base_url=_BASE_URL,
        fetcher=_fetcher,
        run_pip=lambda args: pytest.fail("binary install must never call pip"),
        install_root=tmp_path,
        skip_compatibility_check=True,
        platform_key=_PLATFORM_KEY,
    )

    # The installer must classify the artifact as a BINARY (the #2347 worry was a
    # mis-route to the wheel/pip path).
    install_dir = tmp_path / _AGENT_ID
    sentinel = installer.read_sentinel(_AGENT_ID, tmp_path)
    assert sentinel is not None
    assert sentinel.artifact_kind == installer.ARTIFACT_KIND_BINARY
    assert sentinel.executable == _AGENT_ID  # generic name: platform suffix stripped
    assert sentinel.artifact_sha256 == _BINARY_SHA
    assert (install_dir / sentinel.executable).read_bytes() == _BINARY_BYTES
    assert result.hot_registered is False  # nothing to import for a binary

    # The sidecar fetch resolver must accept that sentinel and return the binary
    # WITHOUT consulting a lock. Point lock_path at a nonexistent file so a
    # regression that fell through to the lock fails loudly here instead of
    # silently resolving some other lock.
    fetched = sidecar_fetch.fetch_binary(
        out_dir=install_dir,
        agent_dir_name=_AGENT_ID,
        platform_key=_PLATFORM_KEY,
        lock_path=tmp_path / "must-not-be-read.lock.json",
    )
    assert fetched.binary_path == install_dir / _AGENT_ID
    assert fetched.sha256 == _BINARY_SHA
    assert fetched.version == _VERSION
    assert fetched.cached is True


def test_wheel_kind_install_is_not_accepted_as_a_sidecar_binary(tmp_path):
    """The negative half of the contract: a wheel-kind install (no binary
    artifact) must NOT be resolved as a runnable sidecar binary — fetch falls
    through to the lock. Guards the artifact_kind discriminator both modules
    share."""
    wheel_bytes = b"PK\x03\x04 fake wheel"
    wheel_sha = hashlib.sha256(wheel_bytes).hexdigest()
    wheel_path = (
        f"agents/{_AGENT_ID}/{_VERSION}/{_AGENT_ID}-{_VERSION}-py3-none-any.whl"
    )

    def _wheel_fetcher(url: str, timeout: float = 120) -> bytes:
        if url.endswith(wheel_path):
            return wheel_bytes
        if url.endswith("gaia-agent.yaml"):
            return f"id: {_AGENT_ID}\n".encode()
        raise AssertionError(f"unexpected fetch url: {url}")

    manifest = _manifest()
    manifest["versions"][_VERSION]["artifacts"] = [
        {
            "filename": f"{_AGENT_ID}-{_VERSION}-py3-none-any.whl",
            "path": wheel_path,
            "sha256": wheel_sha,
            "size_bytes": len(wheel_bytes),
        }
    ]

    installer.install(
        _AGENT_ID,
        version=_VERSION,
        manifest=manifest,
        trusted=True,
        base_url=_BASE_URL,
        fetcher=_wheel_fetcher,
        run_pip=lambda args: None,  # wheel path shells out to pip; stub it
        install_root=tmp_path,
        skip_compatibility_check=True,
        platform_key=_PLATFORM_KEY,
    )
    sentinel = installer.read_sentinel(_AGENT_ID, tmp_path)
    assert sentinel.artifact_kind == installer.ARTIFACT_KIND_WHEEL

    # fetch must NOT treat the wheel install as a spawnable binary; with no lock
    # present it raises the (now actionable) lock error, not a false success.
    install_dir = tmp_path / _AGENT_ID
    from gaia.daemon.sidecars.errors import PlatformError

    with pytest.raises(PlatformError, match="cannot read the sidecar binary lock"):
        sidecar_fetch.fetch_binary(
            out_dir=install_dir,
            agent_dir_name=_AGENT_ID,
            platform_key=_PLATFORM_KEY,
            lock_path=tmp_path / "absent.lock.json",
        )
