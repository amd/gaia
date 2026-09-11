# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for installer/tui/fetch_sidecar.py.

This script is the integrity boundary for a binary that ends up inside a signed
installer, so every branch is exercised offline: the committed lock is replaced
with a temp one and urlopen is stubbed. Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "installer" / "tui" / "fetch_sidecar.py"
_spec = importlib.util.spec_from_file_location("fetch_sidecar", _SCRIPT)
fetch_sidecar = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["fetch_sidecar"] = fetch_sidecar
_spec.loader.exec_module(fetch_sidecar)  # type: ignore[union-attr]

PLACEHOLDER = "PENDING-replace-with-real-sha256"
VERSION = "0.1.1"
WIN_KEY = "win32-x64"
WIN_FILE = "gaia-agent-stdio-win32-x64.exe"

BODY = b"\x4d\x5a" + b"pretend this is a signed sidecar" * 8
BODY_SHA = hashlib.sha256(BODY).hexdigest()
# A well-formed digest that is deliberately NOT the body's.
DECOY_SHA = "a" * 64


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_OMIT = object()  # pass as a value to drop that key from the lock entry


def _platform_entry(**overrides) -> dict:
    entry = {
        "filename": WIN_FILE,
        "executable": "gaia-agent.exe",
        "sha256": BODY_SHA,
        "size": len(BODY),
    }
    entry.update(overrides)
    return {k: v for k, v in entry.items() if v is not _OMIT}


def _lock(platforms: dict, *, version: str = VERSION) -> dict:
    # The `stdio` lane, not `sidecar`: what gets staged here is installed on PATH
    # as `gaia-agent`, and only the stdio build answers the TUI's stdin (#3062).
    return {
        "components": {
            fetch_sidecar.LOCK_COMPONENT: {
                "componentVersion": version,
                "platforms": platforms,
            }
        }
    }


@pytest.fixture
def write_lock(tmp_path, monkeypatch):
    """Point the script at a temp repo root; return a writer for its lock."""
    monkeypatch.setattr(fetch_sidecar, "_repo_root", lambda: tmp_path)

    def _write(lock: dict) -> Path:
        path = tmp_path / fetch_sidecar.BINARIES_LOCK
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lock), encoding="utf-8")
        return path

    return _write


class _FakeResponse:
    """Just enough of an http.client.HTTPResponse for the two call sites."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self._sent = False

    def read(self, _size=None) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _manifest(*, filename: str = WIN_FILE, sha256: str = DECOY_SHA) -> dict:
    """A hub manifest whose digest deliberately disagrees with the lock."""
    return {
        "versions": {
            VERSION: {
                "artifacts": [
                    {
                        "filename": filename,
                        "path": f"/agents/gaia/{VERSION}/{filename}",
                        "sha256": sha256,
                    }
                ]
            }
        }
    }


@pytest.fixture
def serve(monkeypatch):
    """Stub urlopen; return the list of URLs the script asked for."""

    def _serve(*, manifest: dict | None = None, body: bytes = BODY) -> list[str]:
        asked: list[str] = []
        payload = json.dumps(_manifest() if manifest is None else manifest).encode()

        def _urlopen(request, timeout=None):  # noqa: ARG001
            url = request.full_url
            asked.append(url)
            return _FakeResponse(payload if url.endswith("manifest.json") else body)

        monkeypatch.setattr(fetch_sidecar.urllib.request, "urlopen", _urlopen)
        return asked

    return _serve


def run_main(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["fetch_sidecar.py", *[str(a) for a in argv]])
    return fetch_sidecar.main()


def exit_status(excinfo) -> int:
    """The status the interpreter would exit with for this SystemExit."""
    code = excinfo.value.code
    return code if isinstance(code, int) else 1


# ---------------------------------------------------------------------------
# _expected_sha256 -- the lock must carry a real digest or nothing proceeds
# ---------------------------------------------------------------------------


def test_placeholder_digest_is_a_hard_stop(write_lock):
    lock_path = write_lock(_lock({WIN_KEY: _platform_entry(sha256=PLACEHOLDER)}))
    sidecar, _ = fetch_sidecar._sidecar_lock()
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._expected_sha256(sidecar, WIN_KEY, lock_path)
    message = str(e.value)
    assert PLACEHOLDER in message
    assert fetch_sidecar.LOCK_GENERATOR in message  # names the fix
    assert "origin verifying itself" in message  # rules out the tempting fallback


def test_truncated_digest_is_a_hard_stop(write_lock):
    lock_path = write_lock(_lock({WIN_KEY: _platform_entry(sha256=BODY_SHA[:32])}))
    sidecar, _ = fetch_sidecar._sidecar_lock()
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._expected_sha256(sidecar, WIN_KEY, lock_path)
    assert fetch_sidecar.LOCK_GENERATOR in str(e.value)


def test_missing_sha256_key_is_a_hard_stop(write_lock):
    lock_path = write_lock(_lock({WIN_KEY: _platform_entry(sha256=_OMIT)}))
    sidecar, _ = fetch_sidecar._sidecar_lock()
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._expected_sha256(sidecar, WIN_KEY, lock_path)
    assert "no real sha256" in str(e.value)
    assert "cannot be verified" in str(e.value)


def test_absent_platform_is_not_an_error_but_a_distinct_signal(write_lock):
    lock_path = write_lock(_lock({"linux-x64": _platform_entry()}))
    sidecar, _ = fetch_sidecar._sidecar_lock()
    with pytest.raises(fetch_sidecar.NoSidecarForPlatform):
        fetch_sidecar._expected_sha256(sidecar, WIN_KEY, lock_path)


def test_digest_case_is_normalized(write_lock):
    lock_path = write_lock(_lock({WIN_KEY: _platform_entry(sha256=BODY_SHA.upper())}))
    sidecar, _ = fetch_sidecar._sidecar_lock()
    assert fetch_sidecar._expected_sha256(sidecar, WIN_KEY, lock_path) == BODY_SHA


def test_unreadable_lock_names_the_path_and_the_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_sidecar, "_repo_root", lambda: tmp_path)
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._sidecar_lock()
    assert str(fetch_sidecar.BINARIES_LOCK.name) in str(e.value)
    assert fetch_sidecar.LOCK_GENERATOR in str(e.value)


# ---------------------------------------------------------------------------
# _download_and_verify -- a mismatch leaves nothing behind
# ---------------------------------------------------------------------------


def test_digest_mismatch_deletes_the_partial_and_stops(tmp_path, serve):
    serve()
    dest = tmp_path / "gaia-agent.exe"
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._download_and_verify(
            "https://hub.amd-gaia.ai/x", dest, DECOY_SHA, None
        )
    assert exit_status(e) == 1
    assert "SHA-256 mismatch" in str(e.value)
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []  # no .partial left for a later step


def test_size_mismatch_deletes_the_partial_and_stops(tmp_path, serve):
    serve()
    dest = tmp_path / "gaia-agent"
    with pytest.raises(SystemExit) as e:
        fetch_sidecar._download_and_verify(
            "https://hub.amd-gaia.ai/x", dest, BODY_SHA, len(BODY) + 1
        )
    assert "size mismatch" in str(e.value)
    assert list(tmp_path.iterdir()) == []


def test_verified_download_replaces_any_existing_file(tmp_path, serve):
    serve()
    dest = tmp_path / "gaia-agent"
    dest.write_bytes(b"stale")
    fetch_sidecar._download_and_verify(
        "https://hub.amd-gaia.ai/x", dest, BODY_SHA, len(BODY)
    )
    assert dest.read_bytes() == BODY
    assert list(tmp_path.iterdir()) == [dest]


# ---------------------------------------------------------------------------
# main() -- the lock is the authority, not the origin
# ---------------------------------------------------------------------------


def test_happy_path_verifies_against_the_lock_not_the_manifest(
    tmp_path, write_lock, serve, monkeypatch, capsys
):
    write_lock(_lock({WIN_KEY: _platform_entry()}))
    # The manifest advertises a different digest; the lock's must be the one used.
    asked = serve(manifest=_manifest(sha256=DECOY_SHA))
    out = tmp_path / "payload"

    assert run_main(monkeypatch, "--platform", "win-x64", "--out", out) == 0
    assert (out / "gaia-agent.exe").read_bytes() == BODY
    assert BODY_SHA in capsys.readouterr().out
    assert any(WIN_FILE in url for url in asked)


def test_lock_digest_wins_even_when_the_manifest_matches_the_bytes(
    tmp_path, write_lock, serve, monkeypatch
):
    write_lock(_lock({WIN_KEY: _platform_entry(sha256=DECOY_SHA)}))
    serve(manifest=_manifest(sha256=BODY_SHA))
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
    assert "SHA-256 mismatch" in str(e.value)


def test_absent_platform_exits_three_and_placeholder_exits_one(
    tmp_path, write_lock, serve, monkeypatch, capsys
):
    # The workflow skips an installer only on exit 3, so these must not converge.
    write_lock(_lock({"linux-x64": _platform_entry(filename="gaia-agent-linux-x64")}))
    serve()
    assert (
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "a")
        == fetch_sidecar.EXIT_NO_SIDECAR_FOR_PLATFORM
    )
    assert "Build no installer for win-x64" in capsys.readouterr().err

    write_lock(_lock({WIN_KEY: _platform_entry(sha256=PLACEHOLDER)}))
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "b")
    assert exit_status(e) == 1
    assert not (tmp_path / "b").exists()  # nothing staged


def test_version_disagreeing_with_the_lock_is_refused(
    tmp_path, write_lock, serve, monkeypatch
):
    write_lock(_lock({WIN_KEY: _platform_entry()}))
    serve()
    with pytest.raises(SystemExit) as e:
        run_main(
            monkeypatch,
            "--platform",
            "win-x64",
            "--out",
            tmp_path / "payload",
            "--version",
            "9.9.9",
        )
    assert "9.9.9" in str(e.value)
    assert VERSION in str(e.value)
    assert not (tmp_path / "payload").exists()


def test_lock_without_a_component_version_is_refused(
    tmp_path, write_lock, serve, monkeypatch
):
    write_lock(_lock({WIN_KEY: _platform_entry()}, version=""))
    serve()
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
    assert "componentVersion" in str(e.value)


# ---------------------------------------------------------------------------
# The artifact name comes from the lock, not from a rebuilt string
# ---------------------------------------------------------------------------


def test_artifact_name_is_read_from_the_lock(tmp_path, write_lock, serve, monkeypatch):
    renamed = "gaia-agent-flagship-win32-x64.exe"
    write_lock(_lock({WIN_KEY: _platform_entry(filename=renamed)}))
    asked = serve(manifest=_manifest(filename=renamed))

    assert (
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
        == 0
    )
    assert any(renamed in url for url in asked)


def test_missing_filename_in_the_lock_is_a_hard_stop(
    tmp_path, write_lock, serve, monkeypatch
):
    lock_path = write_lock(_lock({WIN_KEY: _platform_entry(filename=_OMIT)}))
    serve()
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
    assert str(lock_path) in str(e.value)
    assert fetch_sidecar.LOCK_GENERATOR in str(e.value)


def test_empty_filename_in_the_lock_is_a_hard_stop(
    tmp_path, write_lock, serve, monkeypatch
):
    write_lock(_lock({WIN_KEY: _platform_entry(filename="")}))
    serve()
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
    assert "filename" in str(e.value)


def test_artifact_missing_from_the_manifest_names_what_is_published(
    tmp_path, write_lock, serve, monkeypatch
):
    write_lock(_lock({WIN_KEY: _platform_entry()}))
    serve(manifest=_manifest(filename="something-else.exe"))
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", "win-x64", "--out", tmp_path / "payload")
    assert "something-else.exe" in str(e.value)
    assert "do not build one" in str(e.value)


# ---------------------------------------------------------------------------
# Platforms the flagship has no build for are rejected by argparse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win-arm64", "linux-arm64"])
def test_platforms_without_a_flagship_build_are_argparse_errors(
    tmp_path, write_lock, monkeypatch, platform
):
    write_lock(_lock({WIN_KEY: _platform_entry()}))
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "--platform", platform, "--out", tmp_path / "payload")
    assert exit_status(e) == 2
    assert platform not in fetch_sidecar.PLATFORM_KEYS


def test_platform_has_sidecar_reflects_the_lock(write_lock):
    write_lock(_lock({WIN_KEY: _platform_entry()}))
    assert fetch_sidecar.platform_has_sidecar("win-x64") is True
    assert fetch_sidecar.platform_has_sidecar("linux-x64") is False


# ---------------------------------------------------------------------------
# The lane: the stdio child, never the REST sidecar
# ---------------------------------------------------------------------------
#
# Every test above builds its lock from ``LOCK_COMPONENT``, so it would stay
# green if the constant were flipped back to "sidecar" -- and the native
# installers would silently go back to putting a program that binds a port and
# ignores stdin on PATH under the name the TUI spawns (#3062). These two pin it
# against the real, committed lock.


def test_the_staged_lane_is_the_stdio_child():
    assert fetch_sidecar.LOCK_COMPONENT == "stdio"


def test_the_committed_lock_publishes_stdio_artifacts_for_every_platform():
    lock_path = Path(__file__).resolve().parents[2] / fetch_sidecar.BINARIES_LOCK
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lane = lock["components"][fetch_sidecar.LOCK_COMPONENT]

    # Every platform the fetcher can be asked for must be published.
    assert set(lane["platforms"]) == set(fetch_sidecar.PLATFORM_KEYS.values())

    for key, entry in lane["platforms"].items():
        assert entry["filename"].startswith("gaia-agent-stdio-"), (
            f"{key} stages {entry['filename']!r}: that is the REST sidecar, which "
            "never answers the stdin the TUI writes to it"
        )
        # What it is INSTALLED as stays `gaia-agent` -- that is the name the
        # TUI resolves on PATH.
        assert entry["executable"] in ("gaia-agent", "gaia-agent.exe")
