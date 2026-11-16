# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How the publisher reads an HTTP 409 from the Agent Hub decides pass/fail.

Only ``version_exists`` means "already published". Every other 409 says the
Worker did NOT record the release, so treating it as success would ship a green
release the hub never saw. ``strict_immutable`` must also refuse a rebuild whose
bytes differ from the published ones.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "email_publish_to_r2_409", PACKAGING / "publish_to_r2.py"
)
assert _spec and _spec.loader
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)

ARTIFACT_BYTES = b"x" * 1024
LOCAL_SHA = hashlib.sha256(ARTIFACT_BYTES).hexdigest()


class _Resp409:
    status_code = 409

    def __init__(self, code: str | None) -> None:
        self._code = code
        self.text = f'{{"error": {{"code": "{code}"}}}}' if code else "conflict"

    def json(self) -> dict:
        if self._code is None:
            raise ValueError("not JSON")
        return {"error": {"code": self._code}}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """Answer the POST with a 409 and serve `remote` as the published bytes."""
    # `listed` None means the catalog lists whatever is stored.
    state = {
        "code": "version_exists",
        "remote": (LOCAL_SHA, 1024),
        "listed": None,
        "downloads": 0,
    }

    def fake_post(url, headers=None, files=None, timeout=None):
        return _Resp409(state["code"])

    def fake_download(*a, **k):
        state["downloads"] += 1
        return state["remote"]

    monkeypatch.setattr(pub.requests, "post", fake_post)

    def fake_listed(*a, **k):
        return state["listed"] or state["remote"][0]

    monkeypatch.setattr(pub, "_download_published", fake_download)
    monkeypatch.setattr(pub, "_listed_sha256", fake_listed)
    manifest = tmp_path / "gaia-agent.yaml"
    manifest.write_text("id: demo\nversion: 1.2.3\n", encoding="utf-8")
    artifact = tmp_path / "email-agent-win-x64.exe"
    artifact.write_bytes(ARTIFACT_BYTES)
    state["publish"] = lambda strict=False: pub.publish_one(
        base_url="https://hub.example",
        manifest_path=manifest,
        manifest={"id": "demo", "version": "1.2.3"},
        artifact_path=artifact,
        platform_key="win-x64",
        token="tok",
        strict_immutable=strict,
    )
    return state


@pytest.mark.parametrize("strict", [False, True])
def test_version_exists_with_identical_bytes_is_a_no_op(hub, strict):
    result = hub["publish"](strict)
    assert result["sha256"] == LOCAL_SHA
    assert result["_published"] is False


def test_version_exists_with_different_bytes_reports_the_published_ones(hub):
    hub["remote"] = ("f" * 64, 2048)
    result = hub["publish"]()
    assert (result["sha256"], result["size"]) == ("f" * 64, 2048)
    assert result["_published"] is False


def test_strict_immutable_refuses_different_published_bytes(hub):
    hub["remote"] = ("f" * 64, 2048)
    with pytest.raises(SystemExit, match="DIFFERENT sha256"):
        hub["publish"](strict=True)


@pytest.mark.parametrize("strict", [False, True])
def test_stored_bytes_that_differ_from_the_catalog_fail(hub, strict):
    # A re-run of a racing publish's loser: R2 holds its own bytes, but the
    # catalog lists the winner's sha256, so installs fail their checksum check.
    hub["listed"] = "e" * 64
    with pytest.raises(SystemExit, match="concurrent_publish_conflict"):
        hub["publish"](strict)


class _JsonResp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict:
        return self._body


def test_listed_sha256_reads_either_channel(monkeypatch):
    art = {"filename": "f.exe", "sha256": "a" * 64}
    catalog = {"versions": {}, "next_versions": {"1.2.3-rc1": {"artifacts": [art]}}}
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _JsonResp(200, catalog)

    monkeypatch.setattr(pub.requests, "get", fake_get)
    assert pub._listed_sha256("https://hub.example/", "demo", "1.2.3-rc1", "f.exe") == (
        "a" * 64
    )
    assert calls == ["https://hub.example/agents/demo/manifest.json"]


@pytest.mark.parametrize(
    "resp",
    [
        _JsonResp(404, {}),
        _JsonResp(200, {"versions": {"1.2.3": {"artifacts": []}}}),
    ],
)
def test_listed_sha256_fails_when_the_catalog_cannot_confirm(monkeypatch, resp):
    monkeypatch.setattr(pub.requests, "get", lambda *a, **k: resp)
    with pytest.raises(SystemExit):
        pub._listed_sha256("https://hub.example", "demo", "1.2.3", "f.exe")


@pytest.mark.parametrize(
    "code",
    [
        "manifest_mismatch",
        "artifact_mismatch",
        "artifact_unverifiable",
        "id_conflict",
        "concurrent_publish_conflict",
        "",
        None,  # body is not JSON
    ],
)
def test_any_other_409_fails_without_reconciling(hub, code):
    hub["code"] = code
    with pytest.raises(SystemExit, match="NOT 'already published'"):
        hub["publish"]()
    assert hub["downloads"] == 0, "a rejected release must not be reconciled"
