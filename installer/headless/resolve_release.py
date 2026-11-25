#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the GitHub release the installer journeys test, wait for its assets,
and say which installers it carries.

    python resolve_release.py --tag v0.25.1-rc2 --wait-minutes 0 --out release.json
    python resolve_release.py --tag "" ...   # newest pre-release, or a newer final

Writes ``release.json`` and, under Actions, the same keys to $GITHUB_OUTPUT.

Set GAIA_GITHUB_FIXTURE to a checked-in response to read releases from disk
instead of the API. PR CI uses tests/fixtures/github/releases.json; tag and
dispatch runs leave it unset and fetch live.

An empty --tag picks the newest release by version (a final sorts after its own
candidates) whose assets are all attached, and names any newer one it skipped.

Asset names come from the same places the release pipeline uses
(installer/tui/artifacts.py for the terminal packages; electron-builder's
artifactName for the desktop app), spelled with the release's asset version:
tag v0.25.1-rc2 -> 0.25.1-rc.2.

Waiting exists for the tag trigger: the tag is pushed before publish.yml has
attached anything, and a final's approval gate in it is human. A release still missing assets when the wait ends fails with
the list of what is missing -- testing a half-attached release would report
installers as broken that simply are not there yet.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

API = "https://api.github.com/repos/amd/gaia"
FIXTURE_ENV = "GAIA_GITHUB_FIXTURE"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
# The first release candidate publish.yml attaches the terminal .pkg/.deb/.rpm
# to. Older candidates carry none, and their packages are rebuilt in the run.
TUI_PACKAGES_ON_RC_SINCE = "v0.25.1-rc4"


def parse_tag(tag: str) -> Tuple[int, int, int, Optional[int]]:
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError(f"not a release tag: {tag!r} (expected vX.Y.Z or vX.Y.Z-rcN)")
    rc = int(m.group(4)) if m.group(4) else None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), rc


def sort_key(tag: str) -> Tuple[int, int, int, int]:
    major, minor, patch, rc = parse_tag(tag)
    # A final sorts after every candidate for it.
    return major, minor, patch, rc if rc is not None else 1 << 30


def asset_version(tag: str) -> str:
    """v0.25.1-rc2 -> 0.25.1-rc.2 (npm/electron-builder form); v0.25.1 -> 0.25.1."""
    major, minor, patch, rc = parse_tag(tag)
    base = f"{major}.{minor}.{patch}"
    return f"{base}-rc.{rc}" if rc is not None else base


def pep440(tag: str) -> str:
    major, minor, patch, rc = parse_tag(tag)
    base = f"{major}.{minor}.{patch}"
    return f"{base}rc{rc}" if rc is not None else base


def core_version(tag: str) -> str:
    major, minor, patch, _ = parse_tag(tag)
    return f"{major}.{minor}.{patch}"


def _tui_binary(platform: str, published: Iterable[str]) -> str:
    """A raw terminal binary's name: gaia-cli-<platform>, or gaia-<platform>
    on a release from before the binary became gaia-cli."""
    legacy = f"gaia-{platform}"
    if legacy in published and f"gaia-cli-{platform}" not in published:
        return legacy
    return f"gaia-cli-{platform}"


def expected_assets(tag: str, published: Iterable[str] = ()) -> Dict[str, str]:
    """Installer id -> the asset name the release should carry for it.

    ``published`` is what the release carries, when known: it decides which of
    their two names the raw terminal binaries go by.
    """
    v = asset_version(tag)
    published = set(published)
    names = {
        "win-setup": f"gaia-{v}-win-x64-setup.exe",
        "agent-ui-exe": f"gaia-agent-ui-{v}-x64-setup.exe",
        "agent-ui-dmg": f"gaia-agent-ui-{v}-arm64.dmg",
        "agent-ui-appimage": f"gaia-agent-ui-{v}-x86_64.AppImage",
        "agent-ui-deb": f"gaia-agent-ui-{v}-amd64.deb",
        "tui-bin-linux-x64": _tui_binary("linux-amd64", published),
        "tui-bin-darwin-arm64": _tui_binary("darwin-arm64", published),
        "tui-bin-darwin-x64": _tui_binary("darwin-amd64", published),
        "tui-bin-win-x64": _tui_binary("windows-amd64.exe", published),
        "wheel": f"amd_gaia-{pep440(tag)}-py3-none-any.whl",
        # Attached to release candidates (their own freeze); a final takes
        # the published flagship from the hub instead.
        "sidecar-linux-x64": "gaia-agent-linux-x64",
        "sidecar-darwin-arm64": "gaia-agent-darwin-arm64",
        "sidecar-darwin-x64": "gaia-agent-darwin-x64",
        # installer/tui/artifacts.py's names, for finals and (since
        # TUI_PACKAGES_ON_RC_SINCE) candidates alike.
        "tui-pkg-darwin-arm64": f"gaia-{v}-darwin-arm64.pkg",
        "tui-pkg-darwin-x64": f"gaia-{v}-darwin-x64.pkg",
        "tui-deb": f"gaia_{v}_amd64.deb",
        "tui-rpm": f"gaia-{v}.x86_64.rpm",
    }
    return names


def required(tag: str) -> List[str]:
    """What a release of this kind must carry before the journeys can run."""
    _, _, _, rc = parse_tag(tag)
    ids = [
        "win-setup",
        "agent-ui-exe",
        "agent-ui-dmg",
        "agent-ui-appimage",
        "agent-ui-deb",
        "tui-bin-linux-x64",
        "tui-bin-darwin-arm64",
        "tui-bin-darwin-x64",
        "tui-bin-win-x64",
    ]
    packages = ["tui-pkg-darwin-arm64", "tui-pkg-darwin-x64", "tui-deb", "tui-rpm"]
    if rc is None:
        ids += packages
    else:
        ids += ["sidecar-linux-x64", "sidecar-darwin-arm64", "sidecar-darwin-x64"]
        if sort_key(tag) >= sort_key(TUI_PACKAGES_ON_RC_SINCE):
            ids += packages
    return ids


_RETRY_STATUSES = (403, 429)
_RETRY_LIMIT_MAX = 5
_RETRY_WAIT_CAP_S = 30.0
_RETRY_WAIT_BUDGET_S = 30.0


def _fixture_releases() -> Optional[List[Dict[str, Any]]]:
    """The checked-in releases fixture, or None when GAIA_GITHUB_FIXTURE is unset."""
    raw_path = os.environ.get(FIXTURE_ENV, "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"error: could not read {FIXTURE_ENV}={path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"error: {FIXTURE_ENV}={path} is not valid JSON: {exc}"
        ) from exc

    releases = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(releases, list) or not all(
        isinstance(release, dict) for release in releases
    ):
        raise SystemExit(
            f"error: {FIXTURE_ENV}={path} must be a JSON object with a 'releases' array"
        )
    for release in releases:
        if not isinstance(release.get("tag_name"), str):
            raise SystemExit(
                f"error: {FIXTURE_ENV}={path} has a release without a string tag_name"
            )
        assets = release.get("assets", [])
        if not isinstance(assets, list):
            raise SystemExit(
                f"error: {FIXTURE_ENV}={path} has a release whose assets is not a list"
            )
        for asset in assets:
            if not (
                isinstance(asset, dict)
                and isinstance(asset.get("name"), str)
                and isinstance(asset.get("browser_download_url"), str)
            ):
                raise SystemExit(
                    f"error: {FIXTURE_ENV}={path} has an asset without a string "
                    "name and browser_download_url"
                )
    return releases


def _retry_limit() -> int:
    """GITHUB_FETCH_RETRIES, default 0; invalid or out-of-range values fail."""
    raw = os.environ.get("GITHUB_FETCH_RETRIES", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(
            f"error: GITHUB_FETCH_RETRIES must be an integer from 0 to "
            f"{_RETRY_LIMIT_MAX}, got {raw!r}"
        ) from None
    if not 0 <= value <= _RETRY_LIMIT_MAX:
        raise SystemExit(
            f"error: GITHUB_FETCH_RETRIES must be from 0 to {_RETRY_LIMIT_MAX}, "
            f"got {value}"
        )
    return value


def _clamp_wait(seconds: float) -> float:
    return max(0.0, min(seconds, _RETRY_WAIT_CAP_S))


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """How long to wait before the next attempt, never more than 30 s."""
    headers = getattr(exc, "headers", None)
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        text = str(retry_after).strip()
        try:
            return _clamp_wait(float(text))
        except ValueError:
            pass
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            return _clamp_wait(when.timestamp() - time.time())
    reset = headers.get("X-RateLimit-Reset") if headers else None
    if reset:
        try:
            return _clamp_wait(float(reset) - time.time())
        except (TypeError, ValueError):
            pass
    return _clamp_wait(0.5 * 2 ** (attempt - 1))


def _get(url: str) -> Any:
    retries = _retry_limit()
    attempt = 1
    waited = 0.0
    while True:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_STATUSES or attempt > retries:
                if attempt > 1:
                    if exc.code in _RETRY_STATUSES:
                        hint = (
                            "GH_TOKEN/GITHUB_TOKEN is set, so this is a GitHub "
                            "API rate limit or an exhausted workflow-token "
                            "hourly quota"
                            if token
                            else "set GH_TOKEN or GITHUB_TOKEN"
                        )
                    else:
                        hint = "this status is not retried"
                    raise RuntimeError(
                        f"{url} returned HTTP {exc.code} after {attempt} attempt(s) "
                        f"with GITHUB_FETCH_RETRIES={retries}; {hint}. Nothing was tested."
                    ) from exc
                raise
            delay = _retry_delay(exc, attempt)
            remaining = _RETRY_WAIT_BUDGET_S - waited
            # A wait that lands exactly on the budget is allowed; one that would
            # take the total past it is not, so the cumulative wait never
            # exceeds 30 s.
            if delay > remaining:
                raise RuntimeError(
                    f"{url} returned HTTP {exc.code} after {attempt} attempt(s); "
                    f"the next retry would wait {delay:.1f}s and take the total "
                    f"past the {_RETRY_WAIT_BUDGET_S:.0f}s wait budget "
                    f"(GITHUB_FETCH_RETRIES={retries}). Nothing was tested."
                ) from exc
            print(
                f"{url}: HTTP {exc.code}, retrying in {delay:.1f}s "
                f"(attempt {attempt} of {retries})",
                flush=True,
            )
            time.sleep(delay)
            waited += delay
            attempt += 1


def _rate_limit(exc: BaseException) -> Optional[Tuple[float, str]]:
    """(seconds until GitHub allows the next read, what it said) when ``exc`` is
    a GitHub API rate limit, else None.

    A 403 only counts when GitHub marks it as a limit (Retry-After, an exhausted
    X-RateLimit-Remaining, or a "rate limit" message), so a real permission
    error is never waited on.
    """
    http = exc if isinstance(exc, urllib.error.HTTPError) else exc.__cause__
    if not isinstance(http, urllib.error.HTTPError) or http.code not in _RETRY_STATUSES:
        return None
    headers = http.headers or {}
    try:
        body = http.read().decode("utf-8", "replace") if http.fp else ""
    except OSError:
        body = ""
    message = ""
    try:
        message = str(json.loads(body).get("message", "")) if body else ""
    except (ValueError, AttributeError):
        message = body[:200]
    remaining = headers.get("X-RateLimit-Remaining")
    retry_after = headers.get("Retry-After")
    detail = (
        f"HTTP {http.code}: {message or 'no message'} "
        f"(X-RateLimit-Remaining={remaining}, "
        f"X-RateLimit-Resource={headers.get('X-RateLimit-Resource')}, "
        f"Retry-After={retry_after})"
    )
    limited = (
        http.code == 429
        or retry_after is not None
        or str(remaining).strip() == "0"
        or "rate limit" in message.lower()
    )
    if not limited:
        print(f"::error::not a GitHub rate limit, failing: {detail}", flush=True)
        return None
    wait = 60.0
    if retry_after:
        try:
            wait = float(str(retry_after).strip())
        except ValueError:
            try:
                wait = (
                    email.utils.parsedate_to_datetime(str(retry_after)).timestamp()
                    - time.time()
                )
            except (TypeError, ValueError):
                pass
    elif str(remaining).strip() == "0" and headers.get("X-RateLimit-Reset"):
        try:
            wait = float(headers["X-RateLimit-Reset"]) - time.time() + 1
        except (TypeError, ValueError):
            pass
    return max(1.0, wait), detail


def _wait_out_rate_limit(
    exc: BaseException, what: str, deadline: float, minutes: float
) -> bool:
    """Sleep until GitHub allows the next read and return True, or return False
    when that is past ``deadline``. Re-raises ``exc`` when it is not a rate limit.
    """
    limit = _rate_limit(exc)
    if limit is None:
        raise exc
    wait, detail = limit
    if time.monotonic() + wait > deadline:
        print(
            f"::error::{what}: the GitHub API is rate-limiting this job's "
            f"token ({detail}) and allows the next read in {wait:.0f}s, "
            f"after the {minutes:g} min wait ends. Re-run this "
            f"workflow once the limit resets. Nothing was tested.",
            flush=True,
        )
        return False
    print(
        f"{what}: GitHub API rate limit ({detail}); "
        f"waiting {wait:.0f}s before checking again",
        flush=True,
    )
    time.sleep(wait)
    return True


def list_releases() -> List[Dict[str, Any]]:
    fixture = _fixture_releases()
    if fixture is not None:
        print(
            f"::notice::using checked-in release fixture "
            f"{os.environ[FIXTURE_ENV]} ({len(fixture)} releases)",
            flush=True,
        )
        out = fixture
    else:
        out = []
        for page in range(1, 4):
            batch = _get(f"{API}/releases?per_page=100&page={page}")
            out += batch
            if len(batch) < 100:
                break
    return [
        r for r in out if not r.get("draft") and TAG_RE.match(r.get("tag_name", ""))
    ]


def pick_default(releases: List[Dict[str, Any]]) -> str:
    """The newest release, by version, that carries everything the journeys need.

    "Newest by version" is the newest pre-release unless a final is newer, since a
    final sorts after its own candidates. A release still being attached is
    skipped by name rather than failing a PR that did not ask for it.
    """
    if not releases:
        raise SystemExit("error: amd/gaia has no published release to test.")
    for rel in sorted(releases, key=lambda r: sort_key(r["tag_name"]), reverse=True):
        tag = rel["tag_name"]
        have = {a["name"] for a in rel.get("assets", [])}
        names = expected_assets(tag, have)
        missing = [names[i] for i in required(tag) if names[i] not in have]
        if not missing:
            return tag
        print(
            f"::notice::skipping {tag}: its release is missing {len(missing)} asset(s) "
            f"(e.g. {missing[0]}); testing an older complete release instead. "
            f"Pass release_tag to test {tag} once it is complete.",
            flush=True,
        )
    raise SystemExit("error: no amd/gaia release carries a complete set of installers.")


def previous_stable(
    releases: List[Dict[str, Any]], tag: str
) -> Optional[Dict[str, Any]]:
    """The newest final release older than ``tag``'s version."""
    major, minor, patch, _ = parse_tag(tag)
    older = [
        r
        for r in releases
        if not r.get("prerelease")
        and sort_key(r["tag_name"])[:3] < (major, minor, patch)
    ]
    return max(older, key=lambda r: sort_key(r["tag_name"])) if older else None


def release_assets(tag: str) -> Optional[Dict[str, str]]:
    """Asset name -> download URL, or None when the release does not exist yet."""
    fixture = _fixture_releases()
    if fixture is not None:
        for rel in fixture:
            if rel.get("tag_name") == tag:
                return {
                    asset["name"]: asset["browser_download_url"]
                    for asset in rel.get("assets", [])
                }
        return None
    try:
        rel = _get(f"{API}/releases/tags/{tag}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tag", default="", help="vX.Y.Z or vX.Y.Z-rcN; empty picks one")
    ap.add_argument("--wait-minutes", type=float, default=0)
    ap.add_argument(
        "--rate-limit-minutes",
        type=float,
        default=0,
        help="how long a GitHub API rate limit may be waited out; never less "
        "than --wait-minutes",
    )
    ap.add_argument("--poll-seconds", type=float, default=60)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    _retry_limit()
    start = time.monotonic()
    deadline = start + args.wait_minutes * 60
    limit_minutes = max(args.wait_minutes, args.rate_limit_minutes)
    limit_deadline = start + limit_minutes * 60

    releases = None
    while releases is None:
        try:
            releases = list_releases()
        except (urllib.error.HTTPError, RuntimeError) as exc:
            if not _wait_out_rate_limit(
                exc, "the release list", limit_deadline, limit_minutes
            ):
                return 1
    tag = args.tag.strip() or pick_default(releases)
    parse_tag(tag)

    while True:
        try:
            assets = release_assets(tag)
        except (urllib.error.HTTPError, RuntimeError) as exc:
            if not _wait_out_rate_limit(exc, tag, limit_deadline, limit_minutes):
                return 1
            continue
        names = expected_assets(tag, assets or ())
        missing = [
            names[i] for i in required(tag) if assets is None or names[i] not in assets
        ]
        if not missing:
            break
        if time.monotonic() >= deadline:
            where = (
                "has no GitHub release yet" if assets is None else "is missing assets"
            )
            listing = "\n".join(f"    {m}" for m in missing)
            print(
                f"::error::{tag} {where} after waiting {args.wait_minutes:g} min. Missing:\n{listing}\n"
                f"Check the publish.yml run for {tag}; "
                f"re-run this workflow once it has attached everything. Nothing was tested.",
                flush=True,
            )
            return 1
        print(
            f"{tag}: waiting for {len(missing)} asset(s), e.g. {missing[0]}", flush=True
        )
        time.sleep(args.poll_seconds)

    assert assets is not None
    prev = previous_stable(releases, tag)
    prev_tag = prev["tag_name"] if prev else ""
    prev_assets = {
        a["name"]: a["browser_download_url"] for a in (prev or {}).get("assets", [])
    }
    prev_names = expected_assets(prev_tag, prev_assets) if prev_tag else {}

    info: Dict[str, Any] = {
        "tag": tag,
        "version": asset_version(tag),
        "pep440": pep440(tag),
        # gaia-cli stamps the tag itself: `gaia-cli version` -> "gaia v0.25.1-rc2 (...)".
        "tui_version": tag[1:],
        "core_version": core_version(tag),
        "prerelease": parse_tag(tag)[3] is not None,
        "assets": {i: n for i, n in names.items() if n in assets},
        "missing": {i: n for i, n in names.items() if n not in assets},
        "urls": {n: assets[n] for n in names.values() if n in assets},
        "prev_tag": prev_tag,
        "prev_version": asset_version(prev_tag) if prev_tag else "",
        "prev_pep440": pep440(prev_tag) if prev_tag else "",
        "prev_tui_version": prev_tag[1:],
        "prev_assets": {i: n for i, n in prev_names.items() if n in prev_assets},
        "prev_urls": {
            n: prev_assets[n] for n in prev_names.values() if n in prev_assets
        },
    }
    Path(args.out).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            for key in (
                "tag",
                "version",
                "pep440",
                "core_version",
                "prev_tag",
                "prev_version",
                "prev_pep440",
            ):
                fh.write(f"{key}={info[key]}\n")
            fh.write(f"prerelease={'true' if info['prerelease'] else 'false'}\n")
            fh.write(
                f"tui_packages_published={'true' if 'tui-deb' in info['assets'] else 'false'}\n"
            )
    print(
        json.dumps(
            {k: info[k] for k in ("tag", "version", "prev_tag", "missing")}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
