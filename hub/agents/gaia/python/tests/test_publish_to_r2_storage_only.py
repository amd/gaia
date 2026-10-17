# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A release candidate's sidecar is stored on the hub without entering its catalog.

The hub's ``latest_version`` is the highest SemVer it has been told about, and
``0.3.0-rc.1`` outranks ``0.2.0``. POSTing an RC to /publish would therefore make
it every hub user's default. ``--storage-only`` PUTs the bytes straight to R2,
where the Worker serves them to the npm package's lock, and never calls
/publish. These tests pin that it never POSTs, never accepts a final version,
and never replaces bytes that are already stored.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_publish_to_r2_storage_only", PACKAGING / "publish_to_r2.py"
)
assert _spec and _spec.loader
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)

R2_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")


def _manifest(tmp_path: Path, version: str) -> Path:
    p = tmp_path / "gaia-agent.yaml"
    p.write_text(f"id: gaia\nname: GAIA\nversion: {version}\n", encoding="utf-8")
    return p


def _artifact(tmp_path: Path, name: str, size: int = 1024, fill: bytes = b"x") -> Path:
    p = tmp_path / name
    p.write_bytes(fill * size)
    return p


@pytest.fixture
def hub(monkeypatch):
    """A fake R2 + Worker: records PUTs, serves HEAD/GET from what is stored."""
    state: dict = {"stored": {}, "puts": [], "head_status": None}

    def fake_post(*a, **kw):
        raise AssertionError("--storage-only must never POST /publish")

    def fake_head(url, timeout=None, allow_redirects=None):
        if state["head_status"] is not None:
            status = state["head_status"]
        else:
            status = 200 if url in state["stored"] else 404
        return type("H", (), {"status_code": status})()

    def fake_download(base_url, agent_id, version, filename):
        url = f"{base_url.rstrip('/')}/agents/{agent_id}/{version}/{filename}"
        data = state["stored"][url]
        return hashlib.sha256(data).hexdigest(), len(data)

    class _S3:
        def put_object(self, **kw):
            state["puts"].append({**kw, "Body": kw["Body"].read()})

    class _Config:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    monkeypatch.setattr(pub.requests, "post", fake_post)
    monkeypatch.setattr(pub.requests, "head", fake_head)
    monkeypatch.setattr(pub, "_download_published", fake_download)
    sysmod = __import__("sys").modules
    monkeypatch.setitem(
        sysmod, "boto3", type("m", (), {"client": staticmethod(lambda *a, **k: _S3())})
    )
    monkeypatch.setitem(sysmod, "botocore", type("m", (), {}))
    monkeypatch.setitem(sysmod, "botocore.config", type("m", (), {"Config": _Config}))
    for name in R2_ENV:
        monkeypatch.setenv(name, "acct" if name.endswith("ACCOUNT_ID") else "v")
    # No publish token: storage-only must not need one.
    monkeypatch.delenv("AGENT_HUB_PUBLISH_TOKEN", raising=False)
    return state


def _run(manifest: Path, *artifacts: Path, extra: tuple[str, ...] = ()) -> int:
    argv = ["--base-url", "https://hub.example", "--manifest", str(manifest)]
    for a in artifacts:
        argv += ["--artifact", str(a)]
    return pub.main([*argv, "--storage-only", *extra])


def test_every_artifact_goes_to_r2_and_nothing_is_posted(tmp_path, hub):
    small = _artifact(tmp_path, "gaia-agent-win32-x64.exe")
    big = _artifact(tmp_path, "gaia-agent-linux-x64", size=2048)

    assert _run(_manifest(tmp_path, "0.3.0-rc.1"), small, big) == 0

    by_key = {p["Key"]: p for p in hub["puts"]}
    assert set(by_key) == {
        "agents/gaia/0.3.0-rc.1/gaia-agent-win32-x64.exe",
        "agents/gaia/0.3.0-rc.1/gaia-agent-linux-x64",
    }
    for put in by_key.values():
        # R2 records a verifiable whole-object SHA-256 only from this header,
        # base64 of the raw digest -- hex here would be rejected.
        digest = hashlib.sha256(put["Body"]).digest()
        assert put["ChecksumSHA256"] == base64.b64encode(digest).decode("ascii")
        assert put["Bucket"] == "gaia-hub"


def test_the_summary_feeds_the_lock_like_a_normal_publish(tmp_path, hub):
    art = _artifact(tmp_path, "gaia-agent-win32-x64.exe")
    out = tmp_path / "summary.json"

    _run(_manifest(tmp_path, "0.3.0-rc.1"), art, extra=("--summary-out", str(out)))

    (record,) = json.loads(out.read_text(encoding="utf-8"))
    assert record == {
        "component": "sidecar",
        "platform": "win32-x64",
        "filename": "gaia-agent-win32-x64.exe",
        "executable": "gaia-agent.exe",
        "sha256": hashlib.sha256(art.read_bytes()).hexdigest(),
        "size": 1024,
    }


@pytest.mark.parametrize(
    "version", ["0.3.0", "1.0.0", "0.3.0+build.5", "0.3.0-", "v0.3.0-rc.1", "0.3-rc.1"]
)
def test_a_final_or_malformed_version_is_refused_before_anything_is_stored(
    tmp_path, hub, version
):
    art = _artifact(tmp_path, "gaia-agent-linux-x64")

    with pytest.raises(SystemExit) as e:
        _run(_manifest(tmp_path, version), art)

    assert "pre-release" in str(e.value)
    assert hub["puts"] == []


@pytest.mark.parametrize("version", ["0.3.0-rc.1", "0.3.0-rc.12", "1.0.0-beta"])
def test_prerelease_versions_are_accepted(version):
    assert pub.PRERELEASE_RE.match(version)


def test_doc_flags_are_refused_since_nothing_would_render_them(tmp_path, hub):
    art = _artifact(tmp_path, "gaia-agent-linux-x64")
    readme = tmp_path / "README.md"
    readme.write_text("# hi", encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        _run(_manifest(tmp_path, "0.3.0-rc.1"), art, extra=("--readme", str(readme)))

    assert "--readme" in str(e.value)
    assert hub["puts"] == []


def test_identical_bytes_already_stored_are_a_no_op(tmp_path, hub):
    art = _artifact(tmp_path, "gaia-agent-linux-x64")
    url = "https://hub.example/agents/gaia/0.3.0-rc.1/gaia-agent-linux-x64"
    hub["stored"][url] = art.read_bytes()

    assert _run(_manifest(tmp_path, "0.3.0-rc.1"), art) == 0
    assert hub["puts"] == []


def test_different_bytes_already_stored_are_never_replaced(tmp_path, hub):
    """A re-run's rebuild differs (PyInstaller is not reproducible); never overwrite."""
    art = _artifact(tmp_path, "gaia-agent-linux-x64")
    url = "https://hub.example/agents/gaia/0.3.0-rc.1/gaia-agent-linux-x64"
    hub["stored"][url] = b"other bytes"

    with pytest.raises(SystemExit) as e:
        _run(_manifest(tmp_path, "0.3.0-rc.1"), art)

    assert "DIFFERENT sha256" in str(e.value)
    assert "rc.2" in str(e.value)
    assert hub["puts"] == []


@pytest.mark.parametrize("status", [403, 500, 503])
def test_an_unknown_head_status_does_not_upload_blind(tmp_path, hub, status):
    hub["head_status"] = status
    art = _artifact(tmp_path, "gaia-agent-linux-x64")

    with pytest.raises(SystemExit) as e:
        _run(_manifest(tmp_path, "0.3.0-rc.1"), art)

    assert f"HTTP {status}" in str(e.value)
    assert hub["puts"] == []


def test_a_small_artifact_needs_r2_credentials_in_storage_only_mode(
    tmp_path, monkeypatch
):
    """Normally a small file rides the Worker; here it cannot, so the preflight must say so."""
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {}))
    small = _artifact(tmp_path, "gaia-agent-win32-x64.exe")

    pub._preflight_oversized([str(small)])  # normal mode: fine without R2
    with pytest.raises(SystemExit) as e:
        pub._preflight_oversized([str(small)], all_direct=True)

    msg = str(e.value)
    assert small.name in msg
    assert "--storage-only" in msg
    assert "Nothing has been published" in msg
