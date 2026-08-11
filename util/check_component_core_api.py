#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Check that each hub component's ``min_gaia_version`` names a core release that
can actually serve the daemon host API that component requires.

Why this exists
---------------
The terminal hub (``tui/``) refuses to attach to a daemon whose host API MAJOR
differs, or whose MINOR is below the one that added the ``/daemon/v1/agents``
control plane and the ``/v1/<agent>/*`` relay. Those two numbers live in
``tui/internal/daemon/instance.go`` as ``RequiredAPIMajor`` /
``RequiredAgentsMinor`` and are enforced by ``Instance.CheckAgentsFloor()``.

``hub/components/terminal-hub/gaia-agent.yaml`` separately hardcodes
``min_gaia_version``. Nothing tied the two together, so the manifest could — and
did — promise compatibility with a core release that predates the control plane.
A user installing the declared minimum would get a binary that fails preflight
on every check.

Comparing the manifest against ``src/gaia/daemon/constants.py`` in THIS tree
would not have caught it: the tree already has the new API. The break is between
the tree and a *published* artifact, so the check has to reason about what each
core release actually shipped.

Two modes, deliberately
-----------------------
Offline (the default) answers from the pinned ``RELEASED_DAEMON_API`` table
below, so unit tests and PR CI stay deterministic and network-free.

``--verify-released`` audits that table against PyPI — every pinned row is
re-read out of the published wheel, and ``LATEST_CORE_RELEASE`` is checked
against the versions PyPI actually serves. That audit runs over the table
itself, NOT only over whatever version a manifest happens to name: a component
pinned to an unreleased version has nothing to fetch, so a manifest-driven check
would quietly do no work in exactly the release where the table went stale.

Usage:
    python util/check_component_core_api.py
    python util/check_component_core_api.py --verify-released
    python util/check_component_core_api.py --release-version 0.23.0

Exit codes:
    0 - every component's declared minimum core can serve it
    1 - a component declares a minimum core that cannot serve it
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TUI_INSTANCE_GO = PROJECT_ROOT / "tui" / "internal" / "daemon" / "instance.go"
CORE_DAEMON_CONSTANTS = PROJECT_ROOT / "src" / "gaia" / "daemon" / "constants.py"
COMPONENTS_DIR = PROJECT_ROOT / "hub" / "components"

PYPI_PROJECT = "amd-gaia"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PROJECT}/json"
WHEEL_MEMBER = "gaia/daemon/constants.py"

# What each published core release actually shipped as DAEMON_API_VERSION.
#
# Verified by installing each amd-gaia wheel from PyPI and reading
# gaia/daemon/constants.py: 0.21.2 and every release before it have no
# gaia/daemon package at all, and 0.22.0 — the first release with a daemon —
# shipped "1". Repo main has since moved to "1.1".
#
# Maintenance: after cutting a core release, add its row and bump
# LATEST_CORE_RELEASE. Forgetting makes the offline mode fall back to trusting
# this tree for a version that has since shipped something else — the exact
# drift this guard exists to catch — which is why --verify-released audits both
# against PyPI before any publish.
FIRST_CORE_RELEASE_WITH_DAEMON = (0, 22, 0)
RELEASED_DAEMON_API: dict[tuple[int, ...], str] = {
    (0, 22, 0): "1",
}
LATEST_CORE_RELEASE = (0, 22, 0)

# Which daemon host API each hub component needs. "tui" reads the floor from the
# Go constants the terminal hub enforces at runtime, so the guard cannot drift
# from the binary. None means the component never attaches to the daemon control
# plane — agent-ui is an Electron front end that talks to the gaia.ui.server
# FastAPI backend.
COMPONENT_HOST_API_SOURCE: dict[str, str | None] = {
    "terminal-hub": "tui",
    "agent-ui": None,
}


class CheckError(Exception):
    """A guard input could not be resolved. Never swallowed — always fatal."""


@dataclass(frozen=True)
class HostAPIFloor:
    """The daemon host API a component requires: MAJOR must match, MINOR is a floor."""

    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def satisfied_by(self, api: tuple[int, int]) -> bool:
        """Mirror Instance.CheckAgentsFloor(): exact MAJOR, MINOR at or above."""
        return api[0] == self.major and api[1] >= self.minor


@dataclass
class ComponentCheck:
    """One component's verdict, plus where its answer came from."""

    problems: list[str] = field(default_factory=list)
    resolution: str = ""


@dataclass
class CheckResult:
    problems: list[str] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)


def parse_api_version(raw: str) -> tuple[int, int]:
    """Parse a DAEMON_API_VERSION string. A bare "1" means 1.0."""
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", raw.strip())
    if not match:
        raise CheckError(
            f"cannot parse daemon API version {raw!r}: expected MAJOR or MAJOR.MINOR"
        )
    return int(match.group(1)), int(match.group(2) or 0)


def parse_version(raw: str) -> tuple[int, int, int]:
    """Parse a release version like "0.22.0" into a comparable 3-tuple.

    Padded so "0.22" and "0.22.0" compare equal rather than the former sorting
    below the daemon-era floor and being reported as pre-daemon.
    """
    if not re.fullmatch(r"\d+(\.\d+)*", raw.strip()):
        raise CheckError(f"cannot parse version {raw!r}: expected dotted numerals")
    parts = tuple(int(part) for part in raw.strip().split("."))
    return (parts + (0, 0, 0))[:3]


def format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def read_go_const(source: str, name: str, path: Path) -> int:
    match = re.search(rf"^\s*{name}\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    if not match:
        raise CheckError(
            f"{path}: could not find the constant `{name}`. The terminal hub's "
            f"host API floor is read from here — if it was renamed or moved, "
            f"update util/check_component_core_api.py to match. Do not delete "
            f"the check."
        )
    return int(match.group(1))


def read_tui_host_api_floor(instance_go: Path = TUI_INSTANCE_GO) -> HostAPIFloor:
    """Read the floor the terminal hub enforces at runtime, from its own source."""
    if not instance_go.is_file():
        raise CheckError(
            f"{instance_go}: not found — cannot read the terminal hub's host API floor"
        )
    source = instance_go.read_text(encoding="utf-8")
    return HostAPIFloor(
        major=read_go_const(source, "RequiredAPIMajor", instance_go),
        minor=read_go_const(source, "RequiredAgentsMinor", instance_go),
    )


def read_tree_daemon_api(constants_py: Path = CORE_DAEMON_CONSTANTS) -> str:
    """Read DAEMON_API_VERSION from this tree — what the NEXT release will ship."""
    if not constants_py.is_file():
        raise CheckError(
            f"{constants_py}: not found — cannot read this tree's DAEMON_API_VERSION"
        )
    return _extract_daemon_api(
        constants_py.read_text(encoding="utf-8"), str(constants_py)
    )


def _extract_daemon_api(source: str, where: str) -> str:
    match = re.search(r'^DAEMON_API_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise CheckError(
            f"{where}: could not find DAEMON_API_VERSION. Update "
            f"util/check_component_core_api.py to match. Do not delete the check."
        )
    return match.group(1)


def _fetch(url: str, timeout: int) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CheckError(
            f"could not reach PyPI at {url}: {exc}. --verify-released needs network "
            f"access to read what each core release actually shipped."
        ) from exc


def _pypi_index() -> dict:
    return json.loads(_fetch(PYPI_JSON_URL, timeout=60))


def released_versions(index: dict | None = None) -> set[tuple[int, int, int]]:
    """Every version published to PyPI — ground truth for "is this released yet"."""
    index = _pypi_index() if index is None else index
    return {
        parse_version(raw)
        for raw, files in index.get("releases", {}).items()
        if re.fullmatch(r"\d+(\.\d+)*", raw) and files
    }


def fetch_released_daemon_api(version: str) -> str | None:
    """Read DAEMON_API_VERSION out of a published wheel. None if it has no daemon.

    Ground truth for what a release shipped, as opposed to what this tree has.
    """
    files = _pypi_index().get("releases", {}).get(version)
    if not files:
        raise CheckError(f"PyPI has no release {version} for {PYPI_PROJECT}")
    wheels = sorted(
        (
            f
            for f in files
            if f.get("packagetype") == "bdist_wheel" and not f.get("yanked")
        ),
        key=lambda f: f.get("filename", ""),
    )
    if not wheels:
        raise CheckError(
            f"PyPI release {version} of {PYPI_PROJECT} has no unyanked wheel to inspect"
        )
    with zipfile.ZipFile(io.BytesIO(_fetch(wheels[0]["url"], timeout=300))) as archive:
        if WHEEL_MEMBER not in archive.namelist():
            return None
        source = archive.read(WHEEL_MEMBER).decode("utf-8")
    return _extract_daemon_api(source, f"the {version} wheel")


def audit_released_table(
    *,
    cutting: tuple[int, int, int] | None = None,
    published: set[tuple[int, int, int]] | None = None,
    fetcher=fetch_released_daemon_api,
    released_api: dict[tuple[int, ...], str] | None = None,
    latest_release: tuple[int, ...] = LATEST_CORE_RELEASE,
) -> None:
    """Prove the pinned table still matches what PyPI serves.

    Runs over the table itself rather than over whatever a manifest names, so a
    component pinned to an unreleased version cannot make this a no-op.
    """
    table = RELEASED_DAEMON_API if released_api is None else released_api
    published = released_versions() if published is None else published

    ahead = sorted(v for v in published if v > latest_release and v != cutting)
    if ahead:
        raise CheckError(
            f"LATEST_CORE_RELEASE is stale: it says {format_version(latest_release)} "
            f"but PyPI has {', '.join(format_version(v) for v in ahead)}. Bump it and "
            f"add the matching RELEASED_DAEMON_API row(s) — until then this guard "
            f"resolves those versions from this tree instead of from what shipped."
        )

    for version, pinned in sorted(table.items()):
        fetched = fetcher(format_version(version))
        if pinned != fetched:
            raise CheckError(
                f"RELEASED_DAEMON_API is wrong for {format_version(version)}: it pins "
                f"{pinned!r} but the published wheel ships {fetched!r}. Correct the table."
            )


def resolve_daemon_api(
    min_gaia_version: str,
    *,
    tree_api: str,
    verify_released: bool = False,
    released_api: dict[tuple[int, ...], str] | None = None,
    latest_release: tuple[int, ...] = LATEST_CORE_RELEASE,
    fetcher=fetch_released_daemon_api,
    published: set[tuple[int, int, int]] | None = None,
) -> tuple[str | None, str]:
    """Resolve the daemon API a given core version ships.

    Returns (api_version_or_None, provenance). None means that release has no
    daemon at all.
    """
    table = RELEASED_DAEMON_API if released_api is None else released_api
    version = parse_version(min_gaia_version)

    if verify_released:
        # Let PyPI, not a hand-maintained constant, decide what is released.
        if published is None:
            published = released_versions()
        if version not in published:
            return tree_api, "this tree (PyPI has no such release yet)"
        return (
            fetcher(min_gaia_version),
            f"the published {PYPI_PROJECT}=={min_gaia_version} wheel",
        )

    if version > latest_release:
        # Not released yet, so it can only be cut from this tree.
        return (
            tree_api,
            f"this tree ({CORE_DAEMON_CONSTANTS.relative_to(PROJECT_ROOT)})",
        )

    if version < FIRST_CORE_RELEASE_WITH_DAEMON:
        return None, f"the published {min_gaia_version} release (predates the daemon)"

    if version in table:
        return table[version], f"the published {min_gaia_version} release"

    raise CheckError(
        f"{min_gaia_version} is at or below the newest core release on record "
        f"({format_version(latest_release)}) but has no row in RELEASED_DAEMON_API. "
        f"Add one — read DAEMON_API_VERSION out of that release's wheel, or re-run "
        f"with --verify-released. Do not guess from this tree; the tree is ahead of "
        f"what shipped, which is exactly the skew this guard catches."
    )


def resolve_floor(
    component_id: str, source: str | None, tui_floor: HostAPIFloor
) -> HostAPIFloor | None:
    if source == "tui":
        return tui_floor
    if source is None:
        return None
    raise CheckError(
        f"{component_id}: unknown host API source {source!r} in "
        f"COMPONENT_HOST_API_SOURCE. Add a resolver for it — do not leave the "
        f"component unchecked."
    )


def check_component(
    component_id: str,
    manifest: dict,
    *,
    floor: HostAPIFloor | None,
    tree_api: str,
    verify_released: bool = False,
    release_version: str | None = None,
    **resolve_kwargs,
) -> ComponentCheck:
    """Return the problems with one component's declared minimum core version."""
    result = ComponentCheck()
    min_gaia_version = manifest.get("min_gaia_version")
    if not min_gaia_version:
        result.problems.append(f"{component_id}: manifest has no min_gaia_version")
        return result

    if release_version is not None and parse_version(min_gaia_version) > parse_version(
        release_version
    ):
        result.problems.append(
            f"{component_id}: cannot publish under {release_version} — it declares "
            f"min_gaia_version {min_gaia_version}, so the release it would ship "
            f"alongside is older than the core it needs. Publish it with "
            f"{min_gaia_version} or later."
        )

    if floor is None:
        result.resolution = (
            f"{component_id}: min_gaia_version {min_gaia_version}, no daemon host "
            f"API requirement"
        )
        return result

    api, provenance = resolve_daemon_api(
        min_gaia_version,
        tree_api=tree_api,
        verify_released=verify_released,
        **resolve_kwargs,
    )
    result.resolution = (
        f"{component_id}: min_gaia_version {min_gaia_version} ships daemon host API "
        f"{api or 'none'} (per {provenance}), needs v{floor}+"
    )

    if api is None:
        result.problems.append(
            f"{component_id}: needs daemon host API v{floor}+, but core "
            f"{min_gaia_version} ships no daemon at all (per {provenance}). "
            f"Raise min_gaia_version to a release that has one."
        )
        return result

    if not floor.satisfied_by(parse_api_version(api)):
        result.problems.append(
            f"{component_id}: needs daemon host API v{floor}+, but core "
            f"{min_gaia_version} ships v{api} (per {provenance}). Every user who "
            f"installs the declared minimum would get a build that cannot talk to "
            f"their daemon. Raise min_gaia_version to the first release shipping "
            f"v{floor}+."
        )
    return result


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise CheckError(f"{path}: manifest did not parse to a mapping")
    return data


def run(
    *,
    verify_released: bool = False,
    release_version: str | None = None,
    components_dir: Path = COMPONENTS_DIR,
    **resolve_kwargs,
) -> CheckResult:
    """Check every hub component."""
    tree_api = read_tree_daemon_api()
    tui_floor = read_tui_host_api_floor()

    on_disk = {p.parent.name for p in components_dir.glob("*/gaia-agent.yaml")}
    unclassified = on_disk - set(COMPONENT_HOST_API_SOURCE)
    if unclassified:
        raise CheckError(
            f"unclassified hub component(s): {', '.join(sorted(unclassified))}. Add a "
            f"COMPONENT_HOST_API_SOURCE entry — 'tui' if it attaches to the daemon "
            f"control plane, None if it does not."
        )

    if verify_released:
        audit_released_table(
            cutting=parse_version(release_version) if release_version else None,
            **resolve_kwargs,
        )

    result = CheckResult()
    for component_id, source in sorted(COMPONENT_HOST_API_SOURCE.items()):
        manifest_path = components_dir / component_id / "gaia-agent.yaml"
        if not manifest_path.is_file():
            raise CheckError(f"{manifest_path}: not found")
        component = check_component(
            component_id,
            load_manifest(manifest_path),
            floor=resolve_floor(component_id, source, tui_floor),
            tree_api=tree_api,
            verify_released=verify_released,
            release_version=release_version,
            **resolve_kwargs,
        )
        result.problems.extend(component.problems)
        if component.resolution:
            result.resolutions.append(component.resolution)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that each hub component's min_gaia_version names a core "
        "release that can serve the daemon host API that component requires."
    )
    parser.add_argument(
        "--verify-released",
        action="store_true",
        help="audit the pinned release table against PyPI (needs network access)",
    )
    parser.add_argument(
        "--release-version",
        help="the core release this publish would ship under; a component whose "
        "min_gaia_version is newer than it cannot be published",
    )
    args = parser.parse_args(argv)

    try:
        result = run(
            verify_released=args.verify_released,
            release_version=args.release_version,
        )
    except CheckError as exc:
        print(f"::error::{exc}")
        return 1

    for resolution in result.resolutions:
        print(f"  {resolution}")
    if result.problems:
        for problem in result.problems:
            print(f"::error::{problem}")
        return 1
    print("OK: every component's declared minimum core can serve it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
