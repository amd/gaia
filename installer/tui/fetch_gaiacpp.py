#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stage gaiacpp, the native GAIA agent, for the terminal installers.

The terminal UI runs gaiacpp when it sits beside gaia-tui and gaia-agent when
it does not, so each terminal installer ships the gaiacpp its release built for
its platform. The bytes come from one named source, never a guessed "latest":

``bundle --bundle DIR --tag TAG --version V --out OUT``
    build_agents.yml's release bundle (its ``lemonade/`` folder) from the
    caller's own run. util/gaiacpp_release_assets.py checks every archive
    against the SHA-256 the bundle's pin file records for TAG.

``release --tag TAG --version V --out OUT``
    The bare binaries attached to the published release TAG, each checked
    against that release's gaiacpp-SHA256SUMS.txt.

Both write the bare binaries and gaiacpp-SHA256SUMS.txt into OUT, and refuse a
binary that is not stamped ``gaiacpp V``.

``stage --dir OUT --platform P --version V --dest DIR``
    Checks P's binary against OUT's checksum list and its stamp again, then
    copies it into DIR as gaiacpp (gaiacpp.exe on Windows).

``has --platform P``
    Exits 0 when the release builds gaiacpp for P.

``stage`` and ``has`` exit 3 for a platform with no gaiacpp build: its
installer ships without one, and the terminal UI runs gaia-agent there.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "util"))
import gaiacpp_release_assets as gra  # noqa: E402
from fetch_agent_ui_setup import RELEASE_ASSET_URL, _download  # noqa: E402
from release_tag import parse_tag  # noqa: E402

# Every target the terminal hub is built for (tui_installers.yml).
TERMINAL_PLATFORMS = (
    "linux-x64",
    "linux-arm64",
    "darwin-x64",
    "darwin-arm64",
    "win-x64",
    "win-arm64",
)

# terminal-hub platform key -> the bare gaiacpp a release attaches for it, one
# per build_agents.yml leg. A platform absent here has no gaiacpp build.
PLATFORM_BINARIES = {
    "win-x64": "gaiacpp-windows-x64.exe",
    "linux-x64": "gaiacpp-linux-x64",
    "darwin-arm64": "gaiacpp-darwin-arm64",
}

EXIT_NO_GAIACPP_FOR_PLATFORM = 3

_SUMS_LINE = re.compile(r"([0-9a-f]{64}) [ *](\S+)")


class NoGaiacppForPlatform(Exception):
    """The release builds no gaiacpp for this platform."""


def installed_name(platform: str) -> str:
    """The name gaiacpp is installed under, beside gaia-tui."""
    return "gaiacpp.exe" if platform.startswith("win") else "gaiacpp"


def binary_for(platform: str) -> str:
    try:
        return PLATFORM_BINARIES[platform]
    except KeyError:
        raise NoGaiacppForPlatform(
            f"no gaiacpp is built for {platform}, so its installer ships without "
            "one and the terminal UI runs gaia-agent there"
        ) from None


def _check_tag(tag: str, version: str) -> None:
    try:
        rel = parse_tag(tag)
    except ValueError as e:
        raise SystemExit(f"gaiacpp: {e}") from e
    if rel.npm_version != version:
        raise SystemExit(
            f"gaiacpp: {tag} is release {rel.npm_version}, but these installers "
            f"are version {version}. Name the tag of the release being built."
        )


def read_sums(path: Path) -> dict[str, str]:
    """name -> sha256 from a sha256sum listing; anything ambiguous is refused."""
    if not path.is_file():
        raise SystemExit(f"gaiacpp: {path} is missing, so nothing can be verified")
    sums: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = _SUMS_LINE.fullmatch(line)
        if not m:
            raise SystemExit(
                f"gaiacpp: {path.name} line {number} is not '<sha256>  <name>': {line!r}"
            )
        if m.group(2) in sums:
            raise SystemExit(f"gaiacpp: {path.name} lists {m.group(2)} twice")
        sums[m.group(2)] = m.group(1)
    return sums


def verify(binary: Path, sums: dict[str, str], version: str) -> None:
    """Refuse a binary whose digest or version stamp is not this release's."""
    want = sums.get(binary.name)
    if want is None:
        raise SystemExit(f"gaiacpp: {gra.SUMS_NAME} has no line for {binary.name}")
    if not binary.is_file():
        raise SystemExit(f"gaiacpp: {binary} is missing")
    got = gra.sha256_hex(binary)
    if got != want:
        raise SystemExit(
            f"gaiacpp: SHA-256 mismatch for {binary.name}\n"
            f"  expected ({gra.SUMS_NAME}) : {want}\n"
            f"  got                              : {got}\n"
            "A binary that does not match its release's checksum is never packaged. "
            "Re-run to retry a corrupt transfer; if it reproduces, the release's "
            "files are not the ones its checksum list describes."
        )
    if not gra.is_stamped(binary, version):
        raise SystemExit(
            f"gaiacpp: {binary.name} is not stamped 'gaiacpp {version}'; it was "
            "built for another release"
        )


def from_bundle(bundle: Path, tag: str, version: str, out: Path) -> list[str]:
    _check_tag(tag, version)
    try:
        return gra.collect(bundle, tag, version, out)
    except gra.ReleaseAssetError as e:
        raise SystemExit(f"gaiacpp: the bundle at {bundle} is not {tag}'s: {e}") from e


def from_release(repo: str, tag: str, version: str, out: Path) -> list[str]:
    _check_tag(tag, version)

    def url(name: str) -> str:
        return RELEASE_ASSET_URL.format(repo=repo, tag=tag, name=name)

    try:
        listing = _download(url(gra.SUMS_NAME))
    except SystemExit as e:
        raise SystemExit(
            f"gaiacpp: {e}\n"
            f"{tag} carries no {gra.SUMS_NAME}: it predates gaiacpp, or is still "
            "uploading its files. Name a release that carries gaiacpp, or build "
            "the installers from publish.yml, which builds gaiacpp in the same run."
        ) from e
    out.mkdir(parents=True, exist_ok=True)
    sums_path = out / gra.SUMS_NAME
    sums_path.write_bytes(listing)
    sums = read_sums(sums_path)

    written = [gra.SUMS_NAME]
    for name in sorted(PLATFORM_BINARIES.values()):
        if name not in sums:
            raise SystemExit(
                f"gaiacpp: {tag}'s {gra.SUMS_NAME} has no line for {name}. Check "
                f"https://github.com/{repo}/releases/tag/{tag}"
            )
        path = out / name
        path.write_bytes(_download(url(name)))
        try:
            verify(path, sums, version)
        except SystemExit:
            path.unlink(missing_ok=True)
            raise
        path.chmod(0o755)
        written.append(name)
    return sorted(written)


def stage(source: Path, platform: str, version: str, dest: Path) -> Path:
    binary = source / binary_for(platform)
    verify(binary, read_sums(source / gra.SUMS_NAME), version)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / installed_name(platform)
    shutil.copyfile(binary, target)
    if os.name != "nt":
        target.chmod(0o755)
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("bundle", help="verify build_agents.yml's release bundle")
    bundle.add_argument("--bundle", required=True, type=Path)
    release = sub.add_parser("release", help="download a published release's gaiacpp")
    release.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "amd/gaia")
    )
    for p in (bundle, release):
        p.add_argument("--tag", required=True, help="release tag, e.g. v0.25.4-rc1")
        p.add_argument("--version", required=True, help="e.g. 0.25.4-rc.1")
        p.add_argument("--out", required=True, type=Path)

    stage_p = sub.add_parser("stage", help="copy one platform's gaiacpp into a payload")
    stage_p.add_argument("--dir", required=True, type=Path)
    stage_p.add_argument("--version", required=True)
    stage_p.add_argument("--dest", required=True, type=Path)
    has = sub.add_parser("has", help="exit 0 when gaiacpp is built for a platform")
    for p in (stage_p, has):
        p.add_argument("--platform", required=True, choices=TERMINAL_PLATFORMS)

    args = ap.parse_args(argv)
    try:
        if args.command == "bundle":
            written = from_bundle(args.bundle, args.tag, args.version, args.out)
        elif args.command == "release":
            written = from_release(args.repo, args.tag, args.version, args.out)
        elif args.command == "stage":
            target = stage(args.dir, args.platform, args.version, args.dest)
            print(f"[fetch_gaiacpp] staged {target} (gaiacpp {args.version})")
            return 0
        else:
            binary_for(args.platform)
            return 0
    except NoGaiacppForPlatform as e:
        print(f"[fetch_gaiacpp] {e}", file=sys.stderr)
        return EXIT_NO_GAIACPP_FOR_PLATFORM
    for name in written:
        print(f"[fetch_gaiacpp] verified {args.out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
