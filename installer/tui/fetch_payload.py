# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Stage the flagship GAIA agent's two binaries for one platform, hash-gated.

The installer bundles ``gaia-tui`` (the Go terminal UI) and ``gaia-agent`` (the
frozen Python sidecar). Both are published to the Agent Hub and pinned by
SHA-256 in ``@amd-gaia/gaia``'s ``binaries.lock.json``. This script resolves one
platform's pair from that lock, downloads them, and verifies each digest BEFORE
anything is packaged. A mismatch aborts — an installer that ships an unverified
binary is worse than no installer.

Used by ``.github/workflows/build-flagship-installers.yml`` on every platform
leg, and callable by hand for a local build.

    python installer/tui/fetch_payload.py \
        --lock binaries.lock.json --platform win32-x64 --dest payload/win32-x64

``--verify`` inverts it: instead of downloading, it checks binaries already on
disk against the same lock. That is what turns a smoke test on a BUILT
installer into a real integrity check — unpack the artifact, point this at the
result, and a packaging step that altered a binary fails loudly.

    python installer/tui/fetch_payload.py --verify \
        --lock binaries.lock.json --platform linux-x64 --dest squashfs-root/usr/bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The lock's two component lanes. The installed name is what the binary is
# called on the user's disk; the hub filename encodes the platform and is not
# what anyone should ever see.
COMPONENTS = ("tui", "sidecar")

PLACEHOLDER = "PENDING-replace-with-real-sha256"


class PayloadError(RuntimeError):
    """A staging failure that must stop the build."""


def _load_lock(path: Path) -> dict:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PayloadError(
            f"binaries.lock.json not found at {path}. Fetch it from the published "
            f"package (`npm pack @amd-gaia/gaia` and read package/binaries.lock.json), "
            f"or pass --lock pointing at a copy."
        ) from e
    except json.JSONDecodeError as e:
        raise PayloadError(f"{path} is not valid JSON: {e}") from e

    schema = str(lock.get("schemaVersion", ""))
    if not schema.startswith("3."):
        raise PayloadError(
            f"{path} declares schemaVersion {schema!r}; this script reads the "
            f"two-lane 3.x layout ({{components: {{tui, sidecar}}}}). Regenerate the "
            f"lock or update installer/tui/fetch_payload.py to the new shape."
        )
    return lock


def _entry(lock: dict, component: str, platform: str) -> tuple[str, dict]:
    comp = lock.get("components", {}).get(component)
    if comp is None:
        raise PayloadError(
            f"The lock has no '{component}' component. Present: "
            f"{sorted(lock.get('components', {}))}. See "
            f".github/workflows/release_agent_gaia.yml for how the lock is generated."
        )
    entry = comp.get("platforms", {}).get(platform)
    if entry is None:
        raise PayloadError(
            f"The lock publishes no '{component}' build for {platform}. Published: "
            f"{sorted(comp.get('platforms', {}))}. Drop this platform from the "
            f"installer matrix or publish the missing binary first."
        )
    base = str(comp.get("baseUrl", "")).rstrip("/")
    if not base:
        raise PayloadError(f"The lock's '{component}' component declares no baseUrl.")
    return base, entry


# The hub sits behind Cloudflare, whose managed rules 403 the default
# ``Python-urllib/3.x`` agent. An explicit one is required, not cosmetic.
USER_AGENT = "gaia-flagship-installer-build/1.0 (+https://github.com/amd/gaia)"


def _download(url: str, dest: Path) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:  # noqa: S310
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise PayloadError(
            f"{url} returned HTTP {e.code}. Check the version is published: "
            f"GET https://hub.amd-gaia.ai/agents/gaia/manifest.json lists what the "
            f"hub actually serves."
        ) from e
    except urllib.error.URLError as e:
        raise PayloadError(
            f"Could not reach {url}: {e.reason}. The hub must be reachable to build "
            f"the installer — the binaries are bundled, not fetched at install time."
        ) from e
    dest.write_bytes(data)
    return data


def _require(entry: dict, field: str, component: str, platform: str) -> str:
    """Read a required lock field, or fail the way every other path here does.

    A bare ``KeyError`` would escape ``main``'s ``PayloadError`` handler and
    print a traceback instead of an actionable ``::error::`` line.
    """
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise PayloadError(
            f"The lock's '{component}/{platform}' entry has no usable {field!r} "
            f"(got {value!r}). It was written by gen_binaries_lock.py in "
            f".github/workflows/release_agent_gaia.yml — a lock missing this field "
            f"cannot describe what to download, so regenerate it rather than "
            f"guessing here."
        )
    return value


def _safe_executable(name: str, component: str, platform: str) -> str:
    """Reject an ``executable`` that would write outside ``--dest``.

    Everything else in this file assumes the lock is hostile until its digests
    check out; this field is used as a path before any digest exists, so it gets
    the same treatment.
    """
    if name != Path(name).name or name in {".", ".."}:
        raise PayloadError(
            f"The lock's '{component}/{platform}' executable name {name!r} is a "
            f"path, not a filename. It is joined onto the staging directory, so a "
            f"lock carrying a path here could write outside it. Refusing; fix the "
            f"lock."
        )
    return name


def verify(lock_path: Path, platform: str, directory: Path) -> list[dict]:
    """Check binaries ALREADY on disk against the lock.

    ``stage`` guards what goes into an installer; this guards what came out of
    one. A smoke test unpacks a built ``.deb``/``.AppImage``/``.dmg`` and points
    this at the result, which turns "the installer was built" into "the
    installer carries the exact bytes the lock pins".
    """
    lock = _load_lock(lock_path)
    checked: list[dict] = []

    for component in COMPONENTS:
        _, entry = _entry(lock, component, platform)
        executable = _safe_executable(
            _require(entry, "executable", component, platform), component, platform
        )
        expected = _require(entry, "sha256", component, platform)
        target = directory / executable

        if not target.is_file():
            present = (
                sorted(p.name for p in directory.iterdir())
                if directory.is_dir()
                else []
            )
            raise PayloadError(
                f"{target} is missing, so the built artifact does not carry the "
                f"{component} binary for {platform}. Present in {directory}: "
                f"{present or '(directory does not exist)'}."
            )

        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise PayloadError(
                f"SHA-256 mismatch for {component}/{platform} INSIDE the built "
                f"artifact.\n"
                f"  file:     {target}\n"
                f"  expected: {expected}  (binaries.lock.json)\n"
                f"  actual:   {actual}\n"
                f"Packaging altered or replaced the verified binary — the installer "
                f"would ship bytes nobody checked. Do not release it."
            )

        print(f"[verify]   ok  {target}  sha256 {actual}", flush=True)
        checked.append({"component": component, "path": str(target), "sha256": actual})

    return checked


def stage(lock_path: Path, platform: str, dest: Path) -> list[dict]:
    lock = _load_lock(lock_path)
    version = lock.get("agentVersion")
    if not version:
        raise PayloadError(f"{lock_path} declares no agentVersion.")

    dest.mkdir(parents=True, exist_ok=True)
    staged: list[dict] = []

    for component in COMPONENTS:
        base, entry = _entry(lock, component, platform)
        filename = _require(entry, "filename", component, platform)
        executable = _safe_executable(
            _require(entry, "executable", component, platform), component, platform
        )
        expected = _require(entry, "sha256", component, platform)
        if expected == PLACEHOLDER or not expected:
            raise PayloadError(
                f"The lock pins '{component}/{platform}' to the placeholder hash "
                f"{expected!r}, which means that lane was never published. Build "
                f"against a released lock (npm pack @amd-gaia/gaia@latest), not the "
                f"in-repo copy."
            )

        url = f"{base}/{filename}"
        target = dest / executable
        print(f"[payload] {component}/{platform} <- {url}", flush=True)
        data = _download(url, target)

        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            target.unlink(missing_ok=True)
            raise PayloadError(
                f"SHA-256 mismatch for {component}/{platform}.\n"
                f"  url:      {url}\n"
                f"  expected: {expected}  (binaries.lock.json)\n"
                f"  actual:   {actual}\n"
                f"The bytes the hub served are not the bytes the lock pins. Do NOT "
                f"package this. Re-run to rule out a truncated download; if it "
                f"persists, treat it as a hub integrity incident and stop."
            )

        size = entry.get("size")
        if size and len(data) != size:
            raise PayloadError(
                f"{component}/{platform} is {len(data)} bytes but the lock records "
                f"{size}. The digest matched, so the lock's own size field is wrong — "
                f"regenerate it rather than shipping a lock nobody can validate."
            )

        target.chmod(0o755)
        print(
            f"[payload]   ok  {executable}  {len(data)} bytes  sha256 {actual}",
            flush=True,
        )
        staged.append(
            {
                "component": component,
                "executable": executable,
                "sha256": actual,
                "size": len(data),
                "url": url,
            }
        )

    (dest / "payload.json").write_text(
        json.dumps(
            {"agentVersion": version, "platform": platform, "files": staged}, indent=2
        ),
        encoding="utf-8",
    )
    return staged


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--lock", required=True, type=Path, help="path to binaries.lock.json"
    )
    p.add_argument(
        "--platform",
        required=True,
        help="lock platform key, e.g. win32-x64 / darwin-arm64 / darwin-x64 / linux-x64",
    )
    p.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="directory to stage into, or (with --verify) the directory to check",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="check binaries already in --dest instead of downloading them; used by "
        "the installer smoke tests to prove a BUILT artifact carries the pinned bytes",
    )
    args = p.parse_args(argv)

    try:
        if args.verify:
            verify(args.lock, args.platform, args.dest)
        else:
            stage(args.lock, args.platform, args.dest)
    except PayloadError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
