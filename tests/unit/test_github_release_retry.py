# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the release API rate-limit recovery helper."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import github_release_retry as release_retry  # noqa: E402


def test_identifies_primary_and_secondary_rate_limit_responses():
    assert release_retry._is_rate_limit_error(
        "API rate limit exceeded for installation"
    )
    assert release_retry._is_rate_limit_error(
        "You have exceeded a secondary rate limit"
    )
    assert not release_retry._is_rate_limit_error("HTTP 422: validation failed")


def test_retry_waits_then_returns(monkeypatch):
    calls = []
    sleeps = []

    def operation():
        calls.append("call")
        if len(calls) == 1:
            raise release_retry.RateLimited("API rate limit exceeded")
        return "done"

    monkeypatch.setattr(release_retry, "_rate_reset_delay", lambda: None)
    monkeypatch.setattr(release_retry.time, "sleep", sleeps.append)
    deadline = release_retry.time.monotonic() + 60
    assert release_retry._retry("release creation", operation, deadline) == "done"
    assert sleeps == [15]


def test_publish_recovers_a_partial_draft_and_does_not_reupload_assets(
    tmp_path, monkeypatch
):
    notes = tmp_path / "notes.md"
    first = tmp_path / "first.whl"
    second = tmp_path / "second.whl"
    for path in (notes, first, second):
        path.write_text(path.name, encoding="utf-8")

    events = []
    assets = {first.name}

    monkeypatch.setattr(release_retry, "_release_exists", lambda *_: True)
    monkeypatch.setattr(
        release_retry,
        "_release_json",
        lambda *_: {
            "assets": [{"name": name, "state": "uploaded"} for name in sorted(assets)]
        },
    )

    def upload(_repo, _tag, asset):
        events.append(Path(asset).name)
        assets.add(Path(asset).name)
        return "uploaded"

    monkeypatch.setattr(release_retry, "_upload_asset", upload)
    monkeypatch.setattr(
        release_retry,
        "_publish",
        lambda *_: events.append("publish") or "published",
    )

    release_retry.publish_release(
        repo="amd/gaia",
        tag="v0.25.4",
        title="v0.25.4",
        notes_file=str(notes),
        assets=[str(first), str(second)],
        prerelease=False,
    )

    assert events == [second.name, "publish"]


def test_publish_retries_an_asset_after_a_rate_limit(tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    asset = tmp_path / "package.whl"
    notes.write_text("notes", encoding="utf-8")
    asset.write_text("package", encoding="utf-8")

    uploads = []
    sleeps = []
    assets = set()
    monkeypatch.setattr(release_retry, "_release_exists", lambda *_: True)
    monkeypatch.setattr(
        release_retry,
        "_release_json",
        lambda *_: {
            "assets": [{"name": name, "state": "uploaded"} for name in sorted(assets)]
        },
    )
    monkeypatch.setattr(release_retry, "_rate_reset_delay", lambda: None)
    monkeypatch.setattr(release_retry.time, "sleep", sleeps.append)

    def upload(_repo, _tag, path):
        uploads.append(path)
        if len(uploads) == 1:
            raise release_retry.RateLimited("API rate limit exceeded")
        assets.add(Path(path).name)
        return "uploaded"

    monkeypatch.setattr(release_retry, "_upload_asset", upload)
    monkeypatch.setattr(release_retry, "_publish", lambda *_: "published")

    release_retry.publish_release(
        repo="amd/gaia",
        tag="v0.25.4",
        title="v0.25.4",
        notes_file=str(notes),
        assets=[str(asset)],
        prerelease=False,
    )

    assert uploads == [str(asset), str(asset)]
    assert sleeps == [15]


def test_publish_fails_loudly_when_the_wait_budget_is_exhausted(tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    notes.write_text("notes", encoding="utf-8")
    monkeypatch.setattr(release_retry, "_release_exists", lambda *_: True)
    monkeypatch.setattr(
        release_retry,
        "_release_json",
        lambda *_: (_ for _ in ()).throw(release_retry.RateLimited("limited")),
    )
    monkeypatch.setattr(release_retry, "_rate_reset_delay", lambda: 1)

    with pytest.raises(release_retry.ReleaseError, match="bounded retry deadline"):
        release_retry.publish_release(
            repo="amd/gaia",
            tag="v0.25.4",
            title="v0.25.4",
            notes_file=str(notes),
            assets=[],
            prerelease=False,
            max_wait_seconds=0,
        )


def test_release_mode_flags_are_preserved(monkeypatch):
    calls = []
    monkeypatch.setattr(
        release_retry,
        "_run",
        lambda args: calls.append(list(args)) or "ok",
    )

    release_retry._create_draft(
        "amd/gaia", "v0.25.4-rc1", "v0.25.4-rc1", "notes.md", True
    )
    release_retry._publish("amd/gaia", "v0.25.4-rc1", "notes.md", True)
    release_retry._create_draft("amd/gaia", "v0.25.4", "v0.25.4", "notes.md", False)
    release_retry._publish("amd/gaia", "v0.25.4", "notes.md", False)

    assert ["--prerelease", "--latest=false"] == calls[0][-2:]
    assert ["--prerelease", "--latest=false"] == calls[1][-2:]
    assert calls[2][-1] == "--latest"
    assert calls[3][-1] == "--latest"
