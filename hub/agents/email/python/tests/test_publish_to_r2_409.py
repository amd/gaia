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
    state = {"code": "version_exists", "remote": (LOCAL_SHA, 1024), "downloads": 0}

    def fake_post(url, headers=None, files=None, timeout=None):
        return _Resp409(state["code"])

    def fake_download(*a, **k):
        state["downloads"] += 1
        return state["remote"]

    monkeypatch.setattr(pub.requests, "post", fake_post)
    monkeypatch.setattr(pub, "_download_published", fake_download)
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


@pytest.mark.parametrize(
    "code",
    [
        "manifest_mismatch",
        "artifact_mismatch",
        "artifact_unverifiable",
        "id_conflict",
        "",
        None,  # body is not JSON
    ],
)
def test_any_other_409_fails_without_reconciling(hub, code):
    hub["code"] = code
    with pytest.raises(SystemExit, match="NOT 'already published'"):
        hub["publish"]()
    assert hub["downloads"] == 0, "a rejected release must not be reconciled"
