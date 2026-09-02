#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installer files each platform produces, and the hub key each publishes under.

One table, because these names were previously spelled by hand in the build
scripts, the smoke tests and the publish step at once -- and the publisher uploads
serially into immutable paths, so a rename that reaches only two of the three
half-publishes a version that can never be completed.

``tests/unit/test_installer_artifacts.py`` asserts the three build scripts still
agree with this table; ``publish-args`` asserts every named file exists before the
first upload.

Usage::

    python installer/tui/artifacts.py names --version 0.23.0 --platform win-x64
    python installer/tui/artifacts.py publish-args --version 0.23.0 --dir dist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fetch_sidecar import platform_has_sidecar

# terminal-hub platform key -> [(filename template, hub publish key), ...]
#
# The publish keys are deliberately NOT the six raw-binary ones. The hub
# installer picks an artifact with `_filename_matches_platform`
# (src/gaia/hub/installer.py), which is an `endswith` on the platform key -- so a
# setup published under "win-x64" would be a candidate wherever the raw
# gaia-win-x64.exe is expected.
ARTIFACTS: dict[str, list[tuple[str, str]]] = {
    "win-x64": [("gaia-{version}-win-x64-setup.exe", "win-x64-setup")],
    "darwin-arm64": [("gaia-{version}-darwin-arm64.pkg", "darwin-arm64-pkg")],
    "darwin-x64": [("gaia-{version}-darwin-x64.pkg", "darwin-x64-pkg")],
    "linux-x64": [
        ("gaia_{version}_amd64.deb", "linux-x64-deb"),
        ("gaia-{version}.x86_64.rpm", "linux-x64-rpm"),
    ],
}


def artifacts_for(platform: str, version: str) -> list[tuple[str, str]]:
    return [(t.format(version=version), key) for t, key in ARTIFACTS[platform]]


def _cmd_names(args: argparse.Namespace) -> int:
    platforms = [args.platform] if args.platform else sorted(ARTIFACTS)
    printed = 0
    for platform in platforms:
        for filename, _ in artifacts_for(platform, args.version):
            if args.ext and not filename.endswith(f".{args.ext}"):
                continue
            print(filename)
            printed += 1
    if printed == 0:
        raise SystemExit(
            f"no installer artifact matches platform={args.platform or '<all>'} "
            f"ext={args.ext or '<any>'}. Known platforms: {', '.join(sorted(ARTIFACTS))}."
        )
    return 0


def _cmd_publish_args(args: argparse.Namespace) -> int:
    """Emit `<path>=<hub key>` for every artifact this release must publish.

    Platforms the flagship publishes no sidecar for are skipped with a reason --
    no sidecar means no installer was built, which is the same answer the npm
    installer gives. Everything else must be on disk BEFORE the first upload.
    """
    lines: list[str] = []
    missing: list[str] = []
    for platform in sorted(ARTIFACTS):
        if not platform_has_sidecar(platform):
            print(
                f"[artifacts] skipping {platform}: the flagship agent publishes no "
                f"sidecar for it, so no installer was built.",
                file=sys.stderr,
            )
            continue
        for filename, key in artifacts_for(platform, args.version):
            path = args.dir / filename
            if not path.is_file():
                missing.append(path.as_posix())
            lines.append(f"{path.as_posix()}={key}")

    if missing:
        listing = "\n".join(f"    {p}" for p in missing)
        raise SystemExit(
            "error: these installer artifacts were named for publishing but are not on "
            f"disk:\n{listing}\n"
            "  Publishing is serial into immutable paths, so uploading the ones that DO "
            "exist would\n"
            "  leave a version that can never be completed. Nothing has been published.\n"
            f"  Fix: check the tui-installers job for {args.dir}, or update "
            "installer/tui/artifacts.py if a filename changed."
        )

    print("\n".join(lines))
    return 0


def main_argv(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    names = sub.add_parser("names", help="print installer filenames, one per line")
    names.add_argument("--version", required=True)
    names.add_argument("--platform", choices=sorted(ARTIFACTS))
    names.add_argument("--ext", help="keep only files with this extension, e.g. deb")
    names.set_defaults(func=_cmd_names)

    publish = sub.add_parser(
        "publish-args", help="print <path>=<hub key> for publish_to_r2.py --artifact"
    )
    publish.add_argument("--version", required=True)
    publish.add_argument("--dir", required=True, type=Path)
    publish.set_defaults(func=_cmd_publish_args)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main_argv())
