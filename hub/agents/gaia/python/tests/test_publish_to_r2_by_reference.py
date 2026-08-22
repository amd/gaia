# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which lane a gaia sidecar takes to the hub, and what actually goes on the wire.

Cloudflare caps a Worker request body at 100 MB on Free/Pro, and the Linux
sidecar is past that (measured 120,677,152 bytes with the RAG deps compiled in),
so it cannot be published through the Worker at all — it 413s at the edge. The
publisher routes it straight into R2 and sends only its coordinates.

These tests assert the *shape* of both calls, not merely that they happened. A
mock that records "put_object was invoked" would still pass if the upload went
multipart, or if the checksum were omitted, or if the hex digest were sent where
base64 belongs — and every one of those makes the Worker refuse the publish,
during a release, after the binaries have been built.

The gaia publisher is a separate file from the email one with a different
signature (component-tagged records feeding the two-lane binaries.lock.json), so
the email copy's tests do not cover it. util/verify_publish_pipeline.py drives
both end-to-end against a local Worker; these are the fast half.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_publish_to_r2", PACKAGING / "publish_to_r2.py"
)
assert _spec and _spec.loader
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)

MANIFEST_YAML = """\
id: gaia
name: GAIA
version: 1.2.3
description: "Flagship agent"
author: AMD
license: MIT
language: python
category: conversation
"""

R2_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CLOUDFLARE_ACCOUNT_ID")


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    p = tmp_path / "gaia-agent.yaml"
    p.write_text(MANIFEST_YAML, encoding="utf-8")
    return p


@pytest.fixture
def r2_env(monkeypatch):
    for name in R2_ENV:
        monkeypatch.setenv(name, "acct" if name.endswith("ACCOUNT_ID") else "v")


def _artifact(tmp_path: Path, size: int, name: str = "gaia-agent-linux-x64") -> Path:
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    return p


class _Resp:
    """Stands in for the Worker, echoing the sha it would have recorded.

    The publisher re-checks that value against its own digest, so returning a
    placeholder would mask a real mismatch rather than exercise the check.
    """

    status_code = 201

    def __init__(self, sha: str) -> None:
        self._sha = sha

    def json(self) -> dict:
        return {"published": {"artifact": {"sha256": self._sha}}}


@pytest.fixture
def captured(monkeypatch):
    """Capture the outgoing POST and any R2 upload, without performing either."""
    seen: dict = {
        "post": None,
        "put": None,
        "head": None,
        "client_kwargs": None,
        "already_published": False,
    }

    def fake_post(url, headers=None, files=None, timeout=None):
        # requests encodes each value as (filename, body[, content_type]); read
        # the file handle now, before the caller's context manager closes it.
        flat = {}
        for k, v in (files or {}).items():
            body = v[1]
            flat[k] = body.read() if hasattr(body, "read") else body
        seen["post"] = {"url": url, "files": flat}
        # By reference the Worker verifies R2's stored digest and returns it;
        # inline it hashes the bytes it received. Model both.
        sha = flat.get("artifact_ref_sha256")
        if sha is None:
            sha = hashlib.sha256(flat["artifact"]).hexdigest()
        return _Resp(sha)

    class _S3:
        def put_object(self, **kw):
            seen["put"] = kw

    def fake_client(*a, **kw):
        seen["client_kwargs"] = kw
        return _S3()

    def fake_head(url, timeout=None, allow_redirects=None):
        seen["head"] = url
        return type(
            "H", (), {"status_code": seen["already_published"] and 200 or 404}
        )()

    monkeypatch.setattr(pub.requests, "head", fake_head)
    monkeypatch.setattr(pub.requests, "post", fake_post)
    monkeypatch.setattr(pub, "_download_published", lambda *a, **k: ("", 0))

    # Stub boto3 AND botocore.config: neither is installed in the unit env, and
    # the client is constructed with a botocore Config object whose settings are
    # the thing under test.
    class _Config:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    sysmod = __import__("sys").modules
    monkeypatch.setitem(
        sysmod, "boto3", type("m", (), {"client": staticmethod(fake_client)})
    )
    monkeypatch.setitem(sysmod, "botocore", type("m", (), {}))
    monkeypatch.setitem(sysmod, "botocore.config", type("m", (), {"Config": _Config}))
    return seen


def _publish(manifest: Path, artifact: Path) -> dict:
    return pub.publish_one(
        base_url="https://hub.example",
        manifest_path=manifest,
        manifest={"id": "gaia", "version": "1.2.3"},
        artifact_path=artifact,
        component="sidecar",
        platform_key="linux-x64",
        token="tok",
        docs={},
    )


def test_a_small_artifact_still_rides_inline(manifest, tmp_path, captured, r2_env):
    """The existing path must not change — Windows and macOS ship through it."""
    _publish(manifest, _artifact(tmp_path, 1024))

    assert captured["put"] is None, "a small artifact must not touch the S3 API"
    files = captured["post"]["files"]
    assert "artifact" in files
    assert not any(k.startswith("artifact_ref_") for k in files)


def test_an_oversized_artifact_goes_to_r2_and_is_published_by_reference(
    manifest, tmp_path, captured, r2_env
):
    size = pub.DIRECT_UPLOAD_THRESHOLD + 1
    art = _artifact(tmp_path, size)
    sha = hashlib.sha256(art.read_bytes()).hexdigest()

    _publish(manifest, art)

    put = captured["put"]
    assert put is not None, "an oversized artifact must be uploaded to R2"
    assert put["Key"] == f"agents/gaia/1.2.3/{art.name}"
    # Base64 of the raw digest. Sending hex here is accepted by boto3 and then
    # rejected by R2, which is a failure that only shows up mid-release.
    assert put["ChecksumSHA256"] == base64.b64encode(bytes.fromhex(sha)).decode()

    files = captured["post"]["files"]
    assert "artifact" not in files, "the bytes must not also travel through the Worker"
    assert files["artifact_ref_filename"] == art.name
    assert files["artifact_ref_sha256"] == sha
    assert files["artifact_ref_size"] == str(size)


def test_the_summary_record_still_carries_the_component(
    manifest, tmp_path, captured, r2_env
):
    """gen_binaries_lock.py routes the sidecar/tui lanes on this field.

    The by-reference lane must not change the record's shape, or an oversized
    Linux sidecar publishes fine and then vanishes from the lock.
    """
    rec = _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    assert rec["component"] == "sidecar"
    assert rec["platform"] == "linux-x64"
    assert rec["executable"] == "gaia-agent"
    assert rec["size"] == pub.DIRECT_UPLOAD_THRESHOLD + 1


def test_a_windows_artifact_keeps_the_exe_suffix_on_the_executable(
    manifest, tmp_path, captured, r2_env
):
    rec = _publish(manifest, _artifact(tmp_path, 1024, "gaia-agent-win32-x64.exe"))

    assert rec["executable"] == "gaia-agent.exe"


def test_put_object_is_used_so_the_upload_stays_single_part(
    manifest, tmp_path, captured, r2_env
):
    """R2 records a whole-object SHA-256 only for single-part uploads.

    `upload_file`/`upload_fileobj` switch to multipart above their threshold and
    the checksum is lost, after which the Worker refuses the publish as
    unverifiable. Pinning the call keeps that from regressing quietly.
    """
    _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    assert set(captured["put"]) >= {"Bucket", "Key", "Body", "ChecksumSHA256"}


@pytest.mark.parametrize("missing", R2_ENV)
def test_missing_r2_credentials_fail_loudly(
    manifest, tmp_path, captured, monkeypatch, r2_env, missing
):
    """Never fall back to the Worker — that path 413s and wastes a release."""
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(SystemExit) as e:
        _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    msg = str(e.value)
    assert "R2_ACCESS_KEY_ID" in msg and "100 MB" in msg
    assert (
        captured["post"] is None
    ), "nothing may be published when the upload cannot run"


def test_the_threshold_sits_below_cloudflares_real_cap():
    """Switch lanes before the cap, not at it, so growth never 413s a release."""
    assert pub.DIRECT_UPLOAD_THRESHOLD < 100 * 1024 * 1024


def test_an_already_published_object_is_never_overwritten(
    manifest, tmp_path, captured, r2_env
):
    """The failure this guards is silent, which is what makes it dangerous.

    The Worker's by-reference immutability check keys on the agent manifest, so
    it fires only after the PUT. Upload first and a re-publish with different
    bytes replaces the stored artifact, the catalog keeps the OLD hash, and the
    409 handler then re-downloads the bytes it just wrote — agreeing with itself
    and exiting green while install-time verification is broken for everyone.
    """
    captured["already_published"] = True

    _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    assert captured["head"] is not None, "must check before uploading"
    assert captured["put"] is None, "published bytes must never be overwritten"


def test_the_check_runs_before_the_upload_not_after(
    manifest, tmp_path, captured, r2_env
):
    """A first publish still uploads — the guard must not block the normal path."""
    captured["already_published"] = False

    _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    assert captured["head"] is not None
    assert captured["put"] is not None


def test_r2_client_disables_boto3_automatic_checksums(
    manifest, tmp_path, captured, r2_env
):
    """boto3 >= 1.36 breaks R2 uploads unless the new defaults are turned off.

    It adds a CRC32 checksum to every PutObject and sends it as an aws-chunked
    trailer, which R2 rejects — reported as `Unauthorized`, which reads like a
    credentials problem and sends you looking in the wrong place entirely. This
    cost one real release, so pin the config rather than the symptom.
    """
    _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    cfg = captured["client_kwargs"]["config"]
    assert cfg.request_checksum_calculation == "when_required"
    assert cfg.response_checksum_validation == "when_required"
    # The explicit digest must survive — the Worker refuses an object R2 has no
    # SHA-256 for, so suppressing checksums entirely would break verification.
    assert "ChecksumSHA256" in captured["put"]


def test_the_r2_endpoint_is_account_scoped(manifest, tmp_path, captured, monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")

    _publish(manifest, _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD + 1))

    assert (
        captured["client_kwargs"]["endpoint_url"]
        == "https://acct123.r2.cloudflarestorage.com"
    )


# --- the pre-flight: nothing may be stored before the oversized lane is proven --


def test_the_preflight_is_silent_when_every_artifact_fits(tmp_path, monkeypatch):
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    small = _artifact(tmp_path, 1024)

    pub._preflight_oversized([str(small)])


def test_the_preflight_names_every_oversized_file_when_credentials_are_absent(
    tmp_path, monkeypatch
):
    """It must name them: a release run cannot guess which leg is too big."""
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {}))
    small = _artifact(tmp_path, 1024, "gaia-agent-win32-x64.exe")
    big = _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD, "gaia-agent-linux-x64")

    with pytest.raises(SystemExit) as e:
        pub._preflight_oversized([str(small), str(big)])

    msg = str(e.value)
    assert big.name in msg
    assert "R2_ACCESS_KEY_ID" in msg
    assert "Nothing has been published" in msg


def test_the_preflight_fails_when_boto3_is_missing_even_with_credentials(
    tmp_path, monkeypatch, r2_env
):
    """Credentials alone do not make the upload possible.

    boto3 is imported lazily by the upload itself, so without this the missing
    dependency surfaces mid-publish — after the smaller platforms are already
    stored immutably under a version that can never be completed.
    """
    real_import = __import__("builtins").__import__

    def blocked(name, *a, **kw):
        if name.split(".")[0] in ("boto3", "botocore"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(__import__("builtins"), "__import__", blocked)
    big = _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD, "gaia-agent-linux-x64")

    with pytest.raises(SystemExit) as e:
        pub._preflight_oversized([str(big)])

    msg = str(e.value)
    assert "boto3" in msg
    assert big.name in msg
    assert "Nothing has been published" in msg


def test_the_threshold_is_a_floor_not_a_ceiling(tmp_path, monkeypatch):
    """An artifact exactly AT the threshold takes the direct lane, not the Worker."""
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {}))
    exact = _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD)

    with pytest.raises(SystemExit):
        pub._preflight_oversized([str(exact)])


def test_the_preflight_rejects_a_missing_artifact_before_anything_is_stored(
    tmp_path, monkeypatch
):
    """A typo'd path must not cost the platforms that were listed before it."""
    for name in R2_ENV:
        monkeypatch.delenv(name, raising=False)
    present = _artifact(tmp_path, 1024)

    with pytest.raises(SystemExit) as e:
        pub._preflight_oversized([str(present), str(tmp_path / "gaia-agent-nope")])

    msg = str(e.value)
    assert "gaia-agent-nope" in msg
    assert "Nothing has been published" in msg


def test_the_oversized_artifact_is_published_first(
    manifest, tmp_path, monkeypatch, r2_env
):
    """The pre-flight proves the R2 credentials exist, not that R2 takes them.

    An expired or read-only token still fails at the PUT, so the artifact that
    needs it must go first — otherwise the smaller platforms are already stored
    immutably under a version that can never be completed.
    """
    small = _artifact(tmp_path, 1024, "gaia-agent-darwin-arm64")
    big = _artifact(tmp_path, pub.DIRECT_UPLOAD_THRESHOLD, "gaia-agent-linux-x64")
    order: list[str] = []

    def record(base_url, manifest_path, manifest_data, artifact_path, *a, **kw):
        order.append(artifact_path.name)
        return {"component": "sidecar", "platform": "x", "filename": "x"}

    monkeypatch.setattr(pub, "publish_one", record)
    monkeypatch.setenv("AGENT_HUB_PUBLISH_TOKEN", "tok")
    monkeypatch.setitem(__import__("sys").modules, "boto3", type("m", (), {}))

    # Deliberately listed small-first, which is what `for f in bins/*` produces.
    pub.main(
        [
            "--base-url",
            "https://hub.example",
            "--manifest",
            str(manifest),
            "--artifact",
            f"{small}=sidecar:darwin-arm64",
            "--artifact",
            f"{big}=sidecar:linux-x64",
        ]
    )

    assert order == [big.name, small.name], "the oversized artifact must go first"
