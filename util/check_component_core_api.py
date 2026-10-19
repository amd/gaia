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

Where "released" comes from
---------------------------
PyPI, every run. The versions ``amd-gaia`` publishes decide what is released,
and each published version's ``DAEMON_API_VERSION`` is read out of its wheel.
There is no hand-kept table of releases: one used to live here, and it went
stale for v0.23.1, v0.24.0 and v0.24.1 because nobody remembered to edit it.

If PyPI cannot be reached, or answers with something that is not a release
index, the check fails. It never guesses from this tree, because the tree is
ahead of every release — that skew is the whole point of the check.

There is no "predates the daemon" shortcut either: a minimum older than any
release with a wheel fails with "no unyanked wheel" rather than passing.

Usage:
    python util/check_component_core_api.py
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
import socket
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TUI_INSTANCE_GO = PROJECT_ROOT / "tui" / "internal" / "daemon" / "instance.go"
CORE_DAEMON_CONSTANTS = PROJECT_ROOT / "src" / "gaia" / "daemon" / "constants.py"
COMPONENTS_DIR = PROJECT_ROOT / "hub" / "components"

PYPI_PROJECT = "amd-gaia"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PROJECT}/json"
WHEEL_MEMBER = "gaia/daemon/constants.py"

_FETCH_ATTEMPTS = 3
_FETCH_BACKOFF_SECONDS = 2

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
    """Read *url*, retrying a dropped connection before calling PyPI unreachable.

    A DNS failure is a real signal and fails on the first attempt; a transport
    drop (``http.client.RemoteDisconnected`` is a ``ConnectionError``, not a
    ``URLError`` — see ``AbstractHTTPHandler.do_open``) is a blip worth a retry.
    """
    last_exc: Exception | None = None
    for attempt in range(_FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            # A status code is upstream's answer, not a blip - retrying is noise.
            raise CheckError(
                f"PyPI returned HTTP {exc.code} for {url}: {exc.reason}. "
                f"This check reads what each core release actually shipped; "
                f"a 404 here means that artifact is not published."
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.gaierror):
                raise CheckError(
                    f"could not resolve {urlparse(url).hostname} for {url}: "
                    f"{exc.reason}. This check needs DNS and network access "
                    f"to read what each core release actually shipped."
                ) from exc
            last_exc = exc
        except (ConnectionError, TimeoutError) as exc:
            last_exc = exc
        if attempt < _FETCH_ATTEMPTS - 1:
            time.sleep(_FETCH_BACKOFF_SECONDS * (attempt + 1))
    raise CheckError(
        f"could not reach PyPI at {url} after {_FETCH_ATTEMPTS} attempts: "
        f"{last_exc}. This check needs network access to read what each "
        f"core release actually shipped."
    ) from last_exc


def load_pypi_index() -> dict:
    """Read the amd-gaia release index from PyPI. Fails loudly, never returns empty."""
    raw = _fetch(PYPI_JSON_URL, timeout=60)
    try:
        index = json.loads(raw)
    except ValueError as exc:
        raise CheckError(
            f"PyPI returned something that is not JSON for {PYPI_JSON_URL}: {exc}"
        ) from exc
    releases = index.get("releases") if isinstance(index, dict) else None
    if not isinstance(releases, dict) or not releases:
        raise CheckError(
            f"PyPI's index at {PYPI_JSON_URL} lists no releases for {PYPI_PROJECT}. "
            f"Refusing to treat every version as unreleased — that would answer "
            f"from this tree, which is ahead of what shipped."
        )
    return index


def released_versions(index: dict) -> dict[tuple[int, int, int], str]:
    """Map each final release on PyPI to its index key.

    Pre-releases (``0.25.0rc1``) and releases with no installable file — never
    uploaded, or every file yanked — do not count: a user cannot get them with
    ``pip install amd-gaia>=X``.
    """
    published: dict[tuple[int, int, int], str] = {}
    for raw, files in index["releases"].items():
        # More than three parts would collide with its three-part prefix.
        if not re.fullmatch(r"\d+(\.\d+){0,2}", raw):
            continue
        if any(not f.get("yanked") for f in files or []):
            published[parse_version(raw)] = raw
    if not published:
        raise CheckError(
            f"PyPI lists no installable final release of {PYPI_PROJECT}. Check "
            f"{PYPI_JSON_URL} — the guard cannot tell what shipped without one."
        )
    return published


def fetch_released_daemon_api(version: str, index: dict) -> str | None:
    """Read DAEMON_API_VERSION out of a published wheel. None if it has no daemon."""
    files = index["releases"].get(version)
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


def resolve_daemon_api(
    min_gaia_version: str,
    *,
    tree_api: str,
    published: dict[tuple[int, int, int], str],
    fetcher,
    cutting: tuple[int, int, int] | None = None,
) -> tuple[str | None, str]:
    """Resolve the daemon API a given core version ships.

    Returns (api_version_or_None, provenance). None means that release has no
    daemon at all. ``published`` maps each released version to its PyPI key;
    ``fetcher`` reads a published wheel's DAEMON_API_VERSION by that key.
    """
    version = parse_version(min_gaia_version)

    if version in published:
        raw = published[version]
        return fetcher(raw), f"the published {PYPI_PROJECT}=={raw} wheel"

    latest = max(published)
    if version > latest or version == cutting:
        # Not on PyPI yet, so it can only be cut from this tree.
        return (
            tree_api,
            f"this tree ({CORE_DAEMON_CONSTANTS.relative_to(PROJECT_ROOT)}; "
            f"PyPI has no such release yet)",
        )

    raise CheckError(
        f"{min_gaia_version} is older than the newest published core release "
        f"({format_version(latest)}) but PyPI has no installable {min_gaia_version}: "
        f"a tag that never became a release, a yanked release, or a typo. Point "
        f"min_gaia_version at a release users can install — see {PYPI_JSON_URL}."
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
        min_gaia_version, tree_api=tree_api, **resolve_kwargs
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
    release_version: str | None = None,
    components_dir: Path = COMPONENTS_DIR,
    published: dict[tuple[int, int, int], str] | None = None,
    fetcher=None,
) -> CheckResult:
    """Check every hub component against what PyPI says has shipped.

    ``published`` and ``fetcher`` replace the PyPI reads; both default to the
    live index, loaded once.
    """
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

    index = None
    if published is None or fetcher is None:
        index = load_pypi_index()
    if published is None:
        published = released_versions(index)
    if fetcher is None:

        def fetcher(version: str) -> str | None:
            return fetch_released_daemon_api(version, index)

    cutting = parse_version(release_version) if release_version else None

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
            release_version=release_version,
            published=published,
            fetcher=fetcher,
            cutting=cutting,
        )
        result.problems.extend(component.problems)
        if component.resolution:
            result.resolutions.append(component.resolution)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that each hub component's min_gaia_version names a core "
        "release that can serve the daemon host API that component requires. "
        "Reads what each release shipped from PyPI, so it needs network access."
    )
    parser.add_argument(
        "--release-version",
        help="the core release this publish would ship under; a component whose "
        "min_gaia_version is newer than it cannot be published",
    )
    args = parser.parse_args(argv)

    try:
        result = run(release_version=args.release_version)
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
