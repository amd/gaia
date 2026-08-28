# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The refusal paths of the flagship installer's payload stager.

Every one of these is a case where packaging must STOP. A regression here does
not fail a build — it ships an installer carrying a binary nobody verified, so
each branch is pinned by name.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "installer" / "tui" / "fetch_payload.py"
)
_spec = importlib.util.spec_from_file_location("fetch_payload", _MODULE_PATH)
fetch_payload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_payload)

TUI_BYTES = b"pretend this is the Go terminal UI"
SIDECAR_BYTES = b"pretend this is the frozen Python agent"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lock(**overrides) -> dict:
    lock = {
        "schemaVersion": "3.0",
        "agentVersion": "9.9.9",
        "components": {
            "tui": {
                "componentVersion": "0.23.0",
                "baseUrl": "https://hub.example/agents/terminal-hub/0.23.0",
                "platforms": {
                    "win32-x64": {
                        "filename": "gaia-win-x64.exe",
                        "executable": "gaia-tui.exe",
                        "sha256": _digest(TUI_BYTES),
                        "size": len(TUI_BYTES),
                    }
                },
            },
            "sidecar": {
                "componentVersion": "9.9.9",
                "baseUrl": "https://hub.example/agents/gaia/9.9.9",
                "platforms": {
                    "win32-x64": {
                        "filename": "gaia-agent-win32-x64.exe",
                        "executable": "gaia-agent.exe",
                        "sha256": _digest(SIDECAR_BYTES),
                        "size": len(SIDECAR_BYTES),
                    }
                },
            },
        },
    }
    lock.update(overrides)
    return lock


@pytest.fixture
def lock_file(tmp_path):
    def _write(lock: dict) -> Path:
        path = tmp_path / "binaries.lock.json"
        path.write_text(json.dumps(lock), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def served(monkeypatch):
    """Serve fixed bytes for every URL, so nothing touches the network."""

    def _serve(body_for_url):
        def fake_download(url, dest):
            data = body_for_url(url)
            dest.write_bytes(data)
            return data

        monkeypatch.setattr(fetch_payload, "_download", fake_download)

    return _serve


def _bodies(url):
    return SIDECAR_BYTES if "gaia-agent" in url else TUI_BYTES


def test_stages_both_binaries_when_every_digest_matches(lock_file, served, tmp_path):
    served(_bodies)
    dest = tmp_path / "payload"

    staged = fetch_payload.stage(lock_file(_lock()), "win32-x64", dest)

    assert {s["executable"] for s in staged} == {"gaia-tui.exe", "gaia-agent.exe"}
    assert (dest / "gaia-tui.exe").read_bytes() == TUI_BYTES
    assert (dest / "gaia-agent.exe").read_bytes() == SIDECAR_BYTES
    # The receipt is what a later step reads to prove what was staged.
    receipt = json.loads((dest / "payload.json").read_text(encoding="utf-8"))
    assert receipt["agentVersion"] == "9.9.9"


def test_hash_mismatch_refuses_and_deletes_the_bad_file(lock_file, served, tmp_path):
    """The whole point of the script: served bytes that are not the pinned bytes."""
    served(lambda url: b"substituted payload")
    dest = tmp_path / "payload"

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(_lock()), "win32-x64", dest)

    message = str(excinfo.value)
    assert "SHA-256 mismatch" in message
    assert _digest(TUI_BYTES) in message, "the expected digest must be named"
    assert _digest(b"substituted payload") in message, "the actual digest must be named"
    # Nothing downstream may find a usable file to package.
    assert not (dest / "gaia-tui.exe").exists()


def test_placeholder_hash_refuses_before_downloading_anything(
    lock_file, served, tmp_path
):
    """The in-repo lock is a template; only the published one has real digests."""
    lock = _lock()
    lock["components"]["tui"]["platforms"]["win32-x64"][
        "sha256"
    ] = "PENDING-replace-with-real-sha256"
    calls = []
    served(lambda url: calls.append(url) or TUI_BYTES)

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "win32-x64", tmp_path / "payload")

    assert "placeholder" in str(excinfo.value)
    assert calls == [], "a placeholder must be caught without a download"


def test_platform_without_a_sidecar_names_the_ones_that_exist(
    lock_file, served, tmp_path
):
    """Linux/Windows arm64: the TUI publishes, the sidecar does not."""
    served(_bodies)
    lock = _lock()
    lock["components"]["tui"]["platforms"]["linux-arm64"] = dict(
        lock["components"]["tui"]["platforms"]["win32-x64"], executable="gaia-tui"
    )

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "linux-arm64", tmp_path / "payload")

    message = str(excinfo.value)
    assert "no 'sidecar' build for linux-arm64" in message
    assert "win32-x64" in message, "the supported set must be named, not just the gap"


def test_size_disagreement_is_reported_even_though_the_digest_matched(
    lock_file, served, tmp_path
):
    lock = _lock()
    lock["components"]["tui"]["platforms"]["win32-x64"]["size"] = 999999

    served(_bodies)

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "win32-x64", tmp_path / "payload")

    assert "999999" in str(excinfo.value)


def test_a_future_schema_version_refuses_rather_than_guessing(
    lock_file, served, tmp_path
):
    served(_bodies)
    lock = _lock(schemaVersion="4.0")

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "win32-x64", tmp_path / "payload")

    assert "schemaVersion" in str(excinfo.value)


def test_a_missing_lock_says_how_to_get_one(tmp_path):
    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(tmp_path / "nope.json", "win32-x64", tmp_path / "payload")

    assert "npm pack @amd-gaia/gaia" in str(excinfo.value)


def test_an_executable_that_is_a_path_is_refused(lock_file, served, tmp_path):
    """`executable` is joined onto --dest before any digest exists to trust."""
    served(_bodies)
    lock = _lock()
    lock["components"]["tui"]["platforms"]["win32-x64"][
        "executable"
    ] = "../../escaped.exe"

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "win32-x64", tmp_path / "payload")

    assert "is a path, not a filename" in str(excinfo.value)
    assert not (tmp_path / "escaped.exe").exists()


@pytest.mark.parametrize("field", ["filename", "executable", "sha256"])
def test_a_missing_required_field_is_an_actionable_error_not_a_traceback(
    lock_file, served, tmp_path, field
):
    served(_bodies)
    lock = _lock()
    del lock["components"]["tui"]["platforms"]["win32-x64"][field]

    # PayloadError, not KeyError — main() only translates the former into
    # an ::error:: line.
    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.stage(lock_file(lock), "win32-x64", tmp_path / "payload")

    assert field in str(excinfo.value)


def test_main_reports_a_bad_lock_as_an_error_line_and_exits_nonzero(
    lock_file, capsys, tmp_path
):
    lock = _lock()
    del lock["components"]["tui"]["platforms"]["win32-x64"]["sha256"]
    path = lock_file(lock)

    code = fetch_payload.main(
        ["--lock", str(path), "--platform", "win32-x64", "--dest", str(tmp_path / "p")]
    )

    assert code == 1
    assert "::error::" in capsys.readouterr().err


# --- verify(): what the smoke jobs run against a BUILT installer -------
#
# stage() guards what goes into an installer; verify() guards what came out of
# one. All four platform smoke jobs are built on it, so its two failure messages
# are what a release engineer reads when a build looks wrong.


def _built(tmp_path) -> Path:
    """A directory shaped like an unpacked installer payload."""
    out = tmp_path / "unpacked"
    out.mkdir()
    (out / "gaia-tui.exe").write_bytes(TUI_BYTES)
    (out / "gaia-agent.exe").write_bytes(SIDECAR_BYTES)
    return out


def test_verify_passes_when_the_artifact_carries_the_pinned_bytes(lock_file, tmp_path):
    checked = fetch_payload.verify(lock_file(_lock()), "win32-x64", _built(tmp_path))

    assert {c["component"] for c in checked} == {"tui", "sidecar"}


def test_verify_catches_a_binary_that_packaging_replaced(lock_file, tmp_path):
    built = _built(tmp_path)
    built.joinpath("gaia-agent.exe").write_bytes(b"something else entirely")

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.verify(lock_file(_lock()), "win32-x64", built)

    message = str(excinfo.value)
    assert "INSIDE the built artifact" in message
    assert "gaia-agent.exe" in message, "the file must be named"
    assert _digest(SIDECAR_BYTES) in message, "the expected digest must be named"


def test_verify_names_what_the_artifact_actually_contains_when_one_is_missing(
    lock_file, tmp_path
):
    built = _built(tmp_path)
    built.joinpath("gaia-agent.exe").unlink()

    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.verify(lock_file(_lock()), "win32-x64", built)

    message = str(excinfo.value)
    assert "does not carry the sidecar binary" in message
    # Listing what IS there is the difference between "it failed" and knowing
    # whether packaging dropped the file or renamed it.
    assert "gaia-tui.exe" in message


def test_verify_says_so_when_the_directory_does_not_exist(lock_file, tmp_path):
    with pytest.raises(fetch_payload.PayloadError) as excinfo:
        fetch_payload.verify(lock_file(_lock()), "win32-x64", tmp_path / "nope")

    assert "directory does not exist" in str(excinfo.value)


def test_verify_never_reaches_the_network(lock_file, tmp_path, monkeypatch):
    """A smoke job checks bytes on disk; re-downloading would defeat the point."""

    def explode(*_a, **_k):
        raise AssertionError("verify() must not download anything")

    monkeypatch.setattr(fetch_payload, "_download", explode)
    fetch_payload.verify(lock_file(_lock()), "win32-x64", _built(tmp_path))


def test_verify_is_reachable_from_the_cli(lock_file, tmp_path, capsys):
    built = _built(tmp_path)
    built.joinpath("gaia-tui.exe").write_bytes(b"tampered")
    path = lock_file(_lock())

    code = fetch_payload.main(
        [
            "--verify",
            "--lock",
            str(path),
            "--platform",
            "win32-x64",
            "--dest",
            str(built),
        ]
    )

    assert code == 1
    assert "::error::" in capsys.readouterr().err
