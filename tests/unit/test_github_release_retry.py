# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the release API rate-limit recovery helper."""

from __future__ import annotations

import sys
from collections import deque
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


def test_release_lookup_checks_the_requested_repository_and_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(
        release_retry,
        "_run",
        lambda args: calls.append(list(args)) or '{"tagName": "v0.25.4"}',
    )

    assert release_retry._release_exists("amd/gaia", "v0.25.4") is True
    assert calls == [
        ["release", "view", "v0.25.4", "--repo", "amd/gaia", "--json", "tagName"]
    ]


@pytest.mark.parametrize(
    "message",
    ["release not found", "HTTP 404: Not Found", "Not Found"],
)
def test_release_lookup_recognizes_a_missing_release(monkeypatch, message):
    def run(_args):
        raise release_retry.ReleaseError(message)

    monkeypatch.setattr(release_retry, "_run", run)

    assert release_retry._release_exists("amd/gaia", "v0.25.4") is False


@pytest.mark.parametrize(
    "error",
    [
        release_retry.RateLimited("API rate limit exceeded"),
        release_retry.RateLimited("You have exceeded a secondary rate limit"),
        release_retry.ReleaseError("HTTP 403: Resource not accessible by integration"),
        release_retry.ReleaseError("could not resolve host: api.github.com"),
    ],
)
def test_release_lookup_does_not_treat_other_failures_as_missing(monkeypatch, error):
    def run(_args):
        raise error

    monkeypatch.setattr(release_retry, "_run", run)

    with pytest.raises(type(error)) as raised:
        release_retry._release_exists("amd/gaia", "v0.25.4")

    assert raised.value is error


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


@pytest.fixture
def publish_script(tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    asset = tmp_path / "package.whl"
    notes.write_text("Release notes", encoding="utf-8")
    asset.write_bytes(b"release artifact")
    repo = "amd/gaia"
    tag = "v0.25.4-rc1"
    clock = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(release_retry.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(release_retry.time, "sleep", sleep)
    monkeypatch.setattr(release_retry, "_rate_reset_delay", lambda: None)

    def publish(steps, *, prerelease=False, max_wait_seconds=60, expected_sleeps=()):
        mode = ["--prerelease", "--latest=false"] if prerelease else ["--latest"]
        view = ["release", "view", tag, "--repo", repo, "--json"]
        commands = {
            "lookup": [*view, "tagName"],
            "state": [*view, "assets,isDraft,tagName"],
            "create": [
                "release",
                "create",
                tag,
                "--repo",
                repo,
                "--verify-tag",
                "--draft",
                "--title",
                "Release title",
                "--notes-file",
                str(notes),
                *mode,
            ],
            "upload": [
                "release",
                "upload",
                tag,
                str(asset),
                "--repo",
                repo,
                "--clobber",
            ],
            "publish": [
                "release",
                "edit",
                tag,
                "--repo",
                repo,
                "--draft=false",
                "--notes-file",
                str(notes),
                *mode,
            ],
        }
        pending = deque(steps)

        def run(args):
            assert pending, f"Unexpected gh call: {args}"
            operation, result = pending.popleft()
            assert list(args) == commands[operation]
            if isinstance(result, Exception):
                raise result
            return result

        # Keep lookup, creation, uploads and retries real; only gh is scripted.
        monkeypatch.setattr(release_retry, "_run", run)
        try:
            release_retry.publish_release(
                repo=repo,
                tag=tag,
                title="Release title",
                notes_file=str(notes),
                assets=[str(asset)],
                prerelease=prerelease,
                max_wait_seconds=max_wait_seconds,
            )
        finally:
            assert not pending, f"Expected more gh calls: {list(pending)}"
            assert sleeps == list(expected_sleeps)

    return publish


_EMPTY_RELEASE = '{"assets": [], "isDraft": true}'
_UPLOADED_RELEASE = (
    '{"assets": [{"name": "package.whl", "state": "uploaded"}], "isDraft": true}'
)
_UPLOAD_AND_PUBLISH = [
    ("state", _EMPTY_RELEASE),
    ("upload", ""),
    ("state", _UPLOADED_RELEASE),
    ("state", _UPLOADED_RELEASE),
    ("publish", ""),
]


@pytest.mark.parametrize("prerelease", [False, True])
def test_publish_creates_a_missing_release_before_uploading(publish_script, prerelease):
    missing = release_retry.ReleaseError("release not found")
    publish_script(
        [
            ("lookup", missing),
            ("lookup", missing),
            ("create", ""),
            *_UPLOAD_AND_PUBLISH,
        ],
        prerelease=prerelease,
    )


@pytest.mark.parametrize("failures", [1, 2])
def test_publish_rechecks_after_create_is_rate_limited(publish_script, failures):
    missing = release_retry.ReleaseError("release not found")
    steps = [("lookup", missing)]
    for _ in range(failures):
        steps.extend(
            [
                ("lookup", missing),
                ("create", release_retry.RateLimited("secondary rate limit")),
            ]
        )
    publish_script(
        [*steps, ("lookup", missing), ("create", ""), *_UPLOAD_AND_PUBLISH],
        expected_sleeps=[15, 30][:failures],
    )


def test_publish_does_not_recreate_a_rate_limited_but_accepted_draft(publish_script):
    missing = release_retry.ReleaseError("release not found")
    publish_script(
        [
            ("lookup", missing),
            ("lookup", missing),
            ("create", release_retry.RateLimited("secondary rate limit")),
            ("lookup", '{"tagName": "v0.25.4-rc1"}'),
            *_UPLOAD_AND_PUBLISH,
        ],
        expected_sleeps=[15],
    )


def test_publish_handles_an_already_existing_draft_after_create_race(publish_script):
    missing = release_retry.ReleaseError("release not found")
    publish_script(
        [
            ("lookup", missing),
            ("lookup", missing),
            ("create", release_retry.ReleaseError("HTTP 422: already exists")),
            *_UPLOAD_AND_PUBLISH,
        ]
    )


def test_publish_retries_lookup_before_creating_a_draft(publish_script):
    missing = release_retry.ReleaseError("release not found")
    publish_script(
        [
            ("lookup", release_retry.RateLimited("API rate limit exceeded")),
            ("lookup", missing),
            ("lookup", missing),
            ("create", ""),
            *_UPLOAD_AND_PUBLISH,
        ],
        expected_sleeps=[15],
    )


def test_publish_does_not_upload_or_publish_after_fatal_creation_error(publish_script):
    missing = release_retry.ReleaseError("release not found")
    error = release_retry.ReleaseError(
        "HTTP 403: Resource not accessible by integration"
    )
    with pytest.raises(release_retry.ReleaseError) as raised:
        publish_script([("lookup", missing), ("lookup", missing), ("create", error)])

    assert raised.value is error


def test_publish_does_not_create_after_fatal_lookup_error(publish_script):
    error = release_retry.ReleaseError("could not resolve host: api.github.com")
    with pytest.raises(release_retry.ReleaseError) as raised:
        publish_script([("lookup", error)])

    assert raised.value is error


def test_publish_creation_stops_at_the_wait_deadline(publish_script):
    missing = release_retry.ReleaseError("release not found")
    limited = release_retry.RateLimited("secondary rate limit")
    with pytest.raises(
        release_retry.ReleaseError, match="creation exceeded retry deadline"
    ):
        publish_script(
            [
                ("lookup", missing),
                ("lookup", missing),
                ("create", limited),
                ("lookup", missing),
                ("create", limited),
                ("lookup", missing),
            ],
            max_wait_seconds=20,
            expected_sleeps=[15, 5],
        )


def test_publish_creation_fails_if_the_call_exhausts_the_deadline(
    publish_script, monkeypatch
):
    missing = release_retry.ReleaseError("release not found")
    limited = release_retry.RateLimited("secondary rate limit")
    times = iter([100.0, 100.0, 160.0])
    monkeypatch.setattr(release_retry.time, "monotonic", lambda: next(times))

    with pytest.raises(
        release_retry.ReleaseError, match="creation exceeded retry deadline"
    ) as raised:
        publish_script([("lookup", missing), ("lookup", missing), ("create", limited)])

    assert raised.value.__cause__ is limited


def test_publish_does_not_create_with_no_wait_budget(publish_script):
    missing = release_retry.ReleaseError("release not found")
    with pytest.raises(
        release_retry.ReleaseError, match="creation exceeded retry deadline"
    ):
        publish_script([("lookup", missing), ("lookup", missing)], max_wait_seconds=0)
