#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Download the flagship `gaia-agent` sidecar and verify it against the committed lock.

The terminal hub spawns `gaia-agent` as a child process (``TransportSubprocess``
in ``tui/internal/catalog/catalog.go``), so an installer that ships only the TUI
installs a front end with nothing behind it. This stages the sidecar the
installers bundle.

The SHA-256 check is the security boundary, and it is the same contract
``hub/agents/gaia/npm/src/fetch.ts`` enforces: the expected digest comes from the
COMMITTED ``hub/agents/gaia/npm/binaries.lock.json``, never from the origin that
served the bytes. Verifying a download against a hash fetched from the same host
proves only that the host is self-consistent, which is trust-on-first-use, not
integrity. A placeholder hash in the lock is refused outright rather than quietly
downgraded to that weaker check -- there is no "use it anyway" path, and a
mismatch deletes the file and exits non-zero.

The lock also pins WHICH sidecar version ships. That is deliberately not the
manifest's ``latest_version``: three installer jobs resolving that independently
can bundle three different agent builds in one release, and nothing would record
which one shipped. The hub manifest is still read, but only to resolve the
artifact's download path.

The two hub lanes disagree about platform spelling: the terminal hub publishes
``gaia-win-x64.exe`` while the flagship publishes ``gaia-agent-win32-x64.exe``
(npm's ``process.platform`` naming). PLATFORM_KEYS is that translation, and it is
the only place it should live.

Usage::

    python installer/tui/fetch_sidecar.py --platform win-x64 --out dist/payload
    python installer/tui/fetch_sidecar.py --platform linux-x64 --out . --version 0.1.1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://hub.amd-gaia.ai"
SIDECAR_AGENT_ID = "gaia"

# terminal-hub platform key -> flagship (npm-style) platform key.
#
# win-arm64 and linux-arm64 are absent on purpose: the flagship has no build for
# them. Asking for one must fail loudly here rather than produce an installer
# that ships a UI with no agent behind it.
PLATFORM_KEYS = {
    "win-x64": "win32-x64",
    "darwin-arm64": "darwin-arm64",
    "darwin-x64": "darwin-x64",
    "linux-x64": "linux-x64",
}

CHUNK = 1 << 20

# The managed WAF in front of hub.amd-gaia.ai 403s urllib's default agent.
USER_AGENT = "gaia-installer-build/1.0"

# The committed sidecar pin, relative to the repo root.
BINARIES_LOCK = Path("hub/agents/gaia/npm/binaries.lock.json")

# Allowlist a real digest rather than blocklist known placeholders, so a lock
# left half-filled by a future generator cannot slip past as "not PENDING".
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
LOCK_GENERATOR = "hub/agents/gaia/python/packaging/gen_binaries_lock.py"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sidecar_lock() -> tuple[dict, Path]:
    """The committed sidecar pin: version plus the per-platform digests."""
    lock_path = _repo_root() / BINARIES_LOCK
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        sidecar = lock["components"]["sidecar"]
        if not isinstance(sidecar, dict):
            raise TypeError("components.sidecar is not an object")
        return sidecar, lock_path
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        raise SystemExit(
            f"could not read the sidecar pin from {lock_path}: {e}\n"
            f"It must contain components.sidecar with componentVersion and "
            f"platforms.<key>.sha256. Regenerate it with {LOCK_GENERATOR}."
        ) from e


def _expected_sha256(sidecar: dict, npm_key: str, lock_path: Path) -> str:
    """The committed digest for `npm_key`, or a hard stop if there is not one."""
    entry = (sidecar.get("platforms") or {}).get(npm_key)
    if not isinstance(entry, dict):
        raise SystemExit(
            f"{lock_path} has no components.sidecar.platforms.{npm_key} entry, so there "
            f"is no committed digest to verify the download against.\n"
            f"Regenerate the lock with {LOCK_GENERATOR}."
        )
    sha = str(entry.get("sha256", ""))
    if not SHA256_RE.match(sha):
        # The lock ships placeholders until a release fills them in, so this is
        # the expected state on a fresh branch -- and the build must stay red
        # until it is fixed. The same placeholder blocks `npx @amd-gaia/gaia`.
        raise SystemExit(
            f"{lock_path} carries no real sha256 for the sidecar on {npm_key} "
            f"(found {sha!r}), so the downloaded binary cannot be verified and this "
            f"installer must not bundle it.\n"
            f"Regenerate the lock from the published artifacts with {LOCK_GENERATOR}, "
            f"then re-run. Do NOT fall back to the digest served by the hub alongside "
            f"the download -- an origin verifying itself is not an integrity check."
        )
    return sha.lower()


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def _fetch_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(
            _request(url), timeout=60
        ) as r:  # noqa: S310 - fixed https host
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        raise SystemExit(
            f"could not read the flagship manifest at {url}: {e}\n"
            f"The Agent Hub must be reachable to stage the sidecar. Check network access, "
            f"or pass --base-url pointing at a mirror."
        ) from e


def _resolve_artifact(manifest: dict, resolved: str, filename: str) -> dict:
    """Return the manifest record for `filename`, which carries its download path."""
    versions = manifest.get("versions") or {}
    entry = versions.get(resolved)
    if entry is None:
        available = ", ".join(sorted(versions)) or "<none>"
        raise SystemExit(
            f"version {resolved} is not published for agent '{SIDECAR_AGENT_ID}'. "
            f"Published versions: {available}."
        )
    for artifact in entry.get("artifacts") or []:
        if artifact.get("filename") == filename:
            return artifact
    published = ", ".join(
        sorted(a.get("filename", "?") for a in entry.get("artifacts") or [])
    )
    raise SystemExit(
        f"the flagship agent {resolved} publishes no artifact named {filename}. "
        f"It ships: {published}. There is no build for this platform, so an installer "
        f"for it would ship the terminal hub with no agent behind it -- do not build one."
    )


def _download_and_verify(
    url: str, dest: Path, expected_sha: str, expected_size: int | None
) -> None:
    digest = hashlib.sha256()
    written = 0
    tmp = dest.with_suffix(dest.suffix + ".partial")
    try:
        with (
            urllib.request.urlopen(_request(url), timeout=300) as r,
            tmp.open("wb") as f,
        ):  # noqa: S310
            while chunk := r.read(CHUNK):
                digest.update(chunk)
                f.write(chunk)
                written += len(chunk)
    except (urllib.error.URLError, TimeoutError) as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"download of {url} failed: {e}") from e

    got = digest.hexdigest()
    if got != expected_sha:
        # Delete rather than leave a rejected artifact on disk where a later
        # step could pick it up. Same rule as fetch.ts: no unverified fallback.
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"SHA-256 mismatch for {url}\n"
            f"  expected ({BINARIES_LOCK.name}) : {expected_sha}\n"
            f"  downloaded                      : {got}\n"
            f"The file has been deleted. This is a hard stop: a binary that does not match "
            f"the committed lock must never be bundled into an installer. Re-run to retry a "
            f"corrupt transfer; if it reproduces, either the hub is serving something other "
            f"than what was released or the lock is stale -- resolve which, do not work "
            f"around it."
        )
    if expected_size is not None and written != expected_size:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"size mismatch for {url}: {BINARIES_LOCK.as_posix()} says {expected_size} "
            f"bytes, got {written}."
        )

    dest.unlink(missing_ok=True)
    tmp.rename(dest)
    if os.name != "nt":
        dest.chmod(0o755)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--platform",
        required=True,
        choices=sorted(PLATFORM_KEYS),
        help="terminal-hub platform key (win-arm64 and linux-arm64 have no flagship build)",
    )
    ap.add_argument(
        "--out", required=True, type=Path, help="directory to write the sidecar into"
    )
    ap.add_argument(
        "--version",
        help=f"flagship agent version (default: the pin in {BINARIES_LOCK.as_posix()})",
    )
    ap.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"hub origin (default: {DEFAULT_BASE_URL})",
    )
    ap.add_argument(
        "--name",
        help="filename to write (default: gaia-agent, plus .exe on Windows targets)",
    )
    args = ap.parse_args()

    npm_key = PLATFORM_KEYS[args.platform]
    is_windows = args.platform.startswith("win")
    filename = f"gaia-agent-{npm_key}" + (".exe" if is_windows else "")
    out_name = args.name or ("gaia-agent.exe" if is_windows else "gaia-agent")

    sidecar, lock_path = _sidecar_lock()
    pinned = str(sidecar.get("componentVersion") or "")
    version = args.version or pinned
    if not version:
        raise SystemExit(
            f"{lock_path} has no components.sidecar.componentVersion, so there is no "
            f"pinned sidecar version to fetch. Regenerate it with {LOCK_GENERATOR}."
        )
    if version != pinned:
        # The committed digest describes the pinned build and nothing else.
        raise SystemExit(
            f"--version {version} does not match the pin in {lock_path} ({pinned}), and "
            f"the committed sha256 only describes the pinned build. Bundling {version} "
            f"would mean verifying it against a digest for different bytes.\n"
            f"Bump the lock to {version} with {LOCK_GENERATOR}, or drop --version."
        )
    expected_sha = _expected_sha256(sidecar, npm_key, lock_path)
    expected_size = sidecar["platforms"][npm_key].get("size")
    if not isinstance(expected_size, int) or expected_size <= 0:
        expected_size = None

    base = args.base_url.rstrip("/")
    manifest = _fetch_json(f"{base}/agents/{SIDECAR_AGENT_ID}/manifest.json")
    artifact = _resolve_artifact(manifest, version, filename)

    args.out.mkdir(parents=True, exist_ok=True)
    dest = args.out / out_name
    url = f"{base}/{artifact['path'].lstrip('/')}"

    print(f"fetching {filename} (flagship {version}) -> {dest}")
    _download_and_verify(url, dest, expected_sha, expected_size)
    print(f"  verified sha256 {expected_sha} against {BINARIES_LOCK.as_posix()}")
    print(f"  wrote {dest} ({dest.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
