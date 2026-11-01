# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Create and publish a GitHub release with bounded API-rate-limit retries.

The release workflow builds all assets before this script runs.  A draft release
is created first, assets are uploaded one at a time, and only then is the draft
published.  That makes a retry safe when GitHub accepts the release creation or
some uploads before returning an installation rate-limit error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence


class RateLimited(RuntimeError):
    """The GitHub API asked the caller to retry later."""


class ReleaseError(RuntimeError):
    """A non-retryable GitHub release failure."""


def _is_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "api rate limit exceeded",
            "rate limit exceeded",
            "secondary rate limit",
            "abuse detection",
            "retry-after",
        )
    )


def _run(args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return completed.stdout
    detail = (completed.stderr or completed.stdout).strip()
    if _is_rate_limit_error(detail):
        raise RateLimited(detail)
    raise ReleaseError(f"gh {' '.join(args)} failed: {detail}")


def _rate_reset_delay() -> int | None:
    """Return seconds until a depleted core API budget resets.

    Secondary limits do not consume the core hourly quota, so their response
    must use the bounded exponential backoff rather than sleeping until an
    unrelated core reset.
    """
    try:
        raw = _run(["api", "rate_limit", "--jq", ".resources.core"])
        core = json.loads(raw)
        if int(core["remaining"]) > 0:
            return None
        reset = int(core["reset"])
    except (KeyError, RateLimited, ReleaseError, TypeError, ValueError):
        return None
    return max(1, reset - int(time.time()))


def _retry_delay(attempt: int, deadline: float) -> int:
    reset = _rate_reset_delay()
    exponential = min(120, 15 * (2 ** min(attempt, 3)))
    delay = reset if reset is not None else exponential
    remaining = max(0, int(deadline - time.monotonic()))
    return min(delay, remaining)


def _retry(label: str, operation: Callable[[], str], deadline: float) -> str:
    attempt = 0
    while True:
        try:
            return operation()
        except RateLimited as exc:
            delay = _retry_delay(attempt, deadline)
            if delay <= 0:
                raise ReleaseError(
                    f"{label} remained rate-limited until the bounded retry "
                    f"deadline; last error: {exc}"
                ) from exc
            print(
                f"::warning::{label} hit GitHub's API limit; retrying in "
                f"{delay}s (attempt {attempt + 1})",
                file=sys.stderr,
            )
            time.sleep(delay)
            attempt += 1


def _release_exists(repo: str, tag: str) -> bool:
    try:
        _run(["release", "view", tag, "--repo", repo, "--json", "tagName"])
        return True
    except ReleaseError as exc:
        text = str(exc).lower()
        if "release not found" in text or "http 404" in text or "not found" in text:
            return False
        raise


def _release_json(repo: str, tag: str) -> dict:
    raw = _run(
        [
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "assets,isDraft,tagName",
        ]
    )
    return json.loads(raw)


def _create_draft(
    repo: str, tag: str, title: str, notes_file: str, prerelease: bool
) -> str:
    args = [
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--verify-tag",
        "--draft",
        "--title",
        title,
        "--notes-file",
        notes_file,
    ]
    if prerelease:
        args.extend(["--prerelease", "--latest=false"])
    else:
        args.append("--latest")
    return _run(args)


def _upload_asset(repo: str, tag: str, asset: str) -> str:
    # --clobber replaces a partial (state: starter) asset from an interrupted
    # upload, while remaining idempotent for a completed asset after re-check.
    return _run(["release", "upload", tag, asset, "--repo", repo, "--clobber"])


def _publish(repo: str, tag: str, notes_file: str, prerelease: bool) -> str:
    args = [
        "release",
        "edit",
        tag,
        "--repo",
        repo,
        "--draft=false",
        "--notes-file",
        notes_file,
    ]
    if prerelease:
        args.extend(["--prerelease", "--latest=false"])
    else:
        args.append("--latest")
    return _run(args)


def publish_release(
    *,
    repo: str,
    tag: str,
    title: str,
    notes_file: str,
    assets: Sequence[str],
    prerelease: bool,
    max_wait_seconds: int = 3600,
) -> None:
    """Publish a release, recovering safely from transient rate limits."""
    for asset in assets:
        if not Path(asset).is_file():
            raise ReleaseError(f"release asset does not exist: {asset}")
    if not Path(notes_file).is_file():
        raise ReleaseError(f"release notes file does not exist: {notes_file}")

    deadline = time.monotonic() + max_wait_seconds
    if not _retry("release lookup", lambda: _release_exists(repo, tag), deadline):
        # A rate-limit response during create can arrive after GitHub accepted
        # the draft. Re-check before every create attempt so retries never make
        # duplicate drafts for one tag.
        attempt = 0
        while not _retry(
            "release lookup", lambda: _release_exists(repo, tag), deadline
        ):
            if time.monotonic() >= deadline:
                raise ReleaseError("release creation exceeded retry deadline")
            try:
                _create_draft(repo, tag, title, notes_file, prerelease)
                break
            except RateLimited as exc:
                delay = _retry_delay(attempt, deadline)
                if delay <= 0:
                    raise ReleaseError(
                        f"release creation exceeded retry deadline: {exc}"
                    ) from exc
                print(
                    f"::warning::release creation rate-limited; retrying in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                attempt += 1
            except ReleaseError as exc:
                if "already exists" in str(exc).lower():
                    break
                raise

    uploaded: set[str] = set()
    while True:
        state = _retry("release lookup", lambda: _release_json(repo, tag), deadline)
        uploaded = {
            item["name"]
            for item in state.get("assets", [])
            if item.get("state") == "uploaded"
        }
        missing = [asset for asset in assets if Path(asset).name not in uploaded]
        if not missing:
            break
        for asset in missing:
            while Path(asset).name not in uploaded:
                if time.monotonic() >= deadline:
                    raise ReleaseError(
                        f"asset upload exceeded retry deadline: {Path(asset).name} "
                        "did not reach the uploaded state"
                    )
                try:
                    _upload_asset(repo, tag, asset)
                except RateLimited as exc:
                    delay = _retry_delay(0, deadline)
                    if delay <= 0:
                        raise ReleaseError(
                            f"asset upload exceeded retry deadline: {exc}"
                        ) from exc
                    print(
                        f"::warning::asset upload rate-limited; retrying in {delay}s",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    state = _retry(
                        "release lookup", lambda: _release_json(repo, tag), deadline
                    )
                    uploaded = {
                        item["name"]
                        for item in state.get("assets", [])
                        if item.get("state") == "uploaded"
                    }
                    continue
                except ReleaseError as exc:
                    if "already exists" not in str(exc).lower():
                        raise
                state = _retry(
                    "release lookup", lambda: _release_json(repo, tag), deadline
                )
                uploaded = {
                    item["name"]
                    for item in state.get("assets", [])
                    if item.get("state") == "uploaded"
                }

    _retry(
        "release publication",
        lambda: _publish(repo, tag, notes_file, prerelease),
        deadline,
    )
    print(f"Published GitHub release {tag} with {len(assets)} asset(s).")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes-file", required=True)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--max-wait-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        publish_release(
            repo=args.repo,
            tag=args.tag,
            title=args.title,
            notes_file=args.notes_file,
            assets=args.asset,
            prerelease=args.prerelease,
            max_wait_seconds=args.max_wait_seconds,
        )
    except ReleaseError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
