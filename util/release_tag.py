#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classify a release tag and stamp release-candidate versions into the tree.

A final release is tagged ``vX.Y.Z`` and publishes the version committed in
``src/gaia/version.py``, exactly as before. A release candidate is tagged
``vX.Y.Z-rcN`` on a commit whose ``version.py`` already says ``X.Y.Z``: the RC
version is derived from the tag at build time, so the final can later be tagged
on the very same commit without another version bump.

One RC, three spellings::

    tag      v0.25.0-rc1
    PyPI     0.25.0rc1      (PEP 440; pip ignores it without --pre)
    npm      0.25.0-rc.1    (SemVer; published under the `next` dist-tag)

Usage::

    python util/release_tag.py classify v0.25.0-rc1     # KEY=VALUE lines for $GITHUB_OUTPUT
    python util/release_tag.py stamp v0.25.0-rc1        # rewrite version.py + webui package files
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_PY = Path("src/gaia/version.py")
WEBUI_PACKAGE = Path("src/gaia/apps/webui/package.json")
WEBUI_LOCK = Path("src/gaia/apps/webui/package-lock.json")

RC_TAG_RE = re.compile(r"^v(?P<base>\d+\.\d+\.\d+)-rc(?P<rc>[1-9]\d*)$")
# Unchanged from before release candidates existed: any all-numeric v-tag
# (including the occasional four-part hotfix) is a final release.
FINAL_TAG_RE = re.compile(r"^v(?P<base>\d+(?:\.\d+)+)$")
VERSION_PY_RE = re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.MULTILINE)

TAG_HELP = "Use vX.Y.Z for a release or vX.Y.Z-rcN (N >= 1) for a release candidate."


@dataclass(frozen=True)
class ReleaseTag:
    tag: str
    is_rc: bool
    base_version: str
    rc_number: int | None

    @property
    def notes_tag(self) -> str:
        """The release-notes page this tag ships with: an RC reuses its final's."""
        return f"v{self.base_version}"

    @property
    def pep440_version(self) -> str:
        return f"{self.base_version}rc{self.rc_number}" if self.is_rc else self.base_version

    @property
    def npm_version(self) -> str:
        return f"{self.base_version}-rc.{self.rc_number}" if self.is_rc else self.base_version

    @property
    def npm_dist_tag(self) -> str:
        return "next" if self.is_rc else "latest"

    def outputs(self) -> dict[str, str]:
        return {
            "IS_RC": "true" if self.is_rc else "false",
            "BASE_VERSION": self.base_version,
            "PEP440_VERSION": self.pep440_version,
            "NPM_VERSION": self.npm_version,
            "NPM_DIST_TAG": self.npm_dist_tag,
            "NOTES_TAG": self.notes_tag,
        }


def parse_tag(tag: str) -> ReleaseTag:
    """Classify ``tag``; raise ValueError for anything that is neither form."""
    m = RC_TAG_RE.match(tag)
    if m:
        return ReleaseTag(tag, True, m.group("base"), int(m.group("rc")))
    m = FINAL_TAG_RE.match(tag)
    if m:
        return ReleaseTag(tag, False, m.group("base"), None)
    raise ValueError(f"'{tag}' is not a release tag. {TAG_HELP}")


def _read(path: Path) -> str:
    # newline="" keeps the file's own line endings on a Windows runner.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read_version_py(root: Path) -> str:
    m = VERSION_PY_RE.search(_read(root / VERSION_PY))
    if not m:
        raise ValueError(f"could not find __version__ in {root / VERSION_PY}")
    return m.group(2)


def _lock_root_re(name: str) -> re.Pattern[str]:
    # Same targeting as installer/version/bump-ui-version.mjs: the lockfile's two
    # root-package version fields each directly follow the package's own name.
    return re.compile(
        r'("name":\s*"' + re.escape(name) + r'",\s*\n\s*"version":\s*")([^"]+)(")'
    )


def stamp(tag: str, root: Path = REPO_ROOT) -> ReleaseTag:
    """Write an RC's versions into version.py and the webui package files.

    Refuses a final tag (finals publish what is committed) and refuses a tree
    whose committed version is not the RC's base -- that is the release-bump
    commit missing, the same mistake the final's version check catches.
    Idempotent: a tree already stamped for this RC is left as is.
    """
    rel = parse_tag(tag)
    if not rel.is_rc:
        raise ValueError(
            f"{tag} is a final release; finals publish the committed version and "
            "are never stamped."
        )

    py_path = root / VERSION_PY
    py_now = _read_version_py(root)
    if py_now not in (rel.base_version, rel.pep440_version):
        raise ValueError(
            f"{VERSION_PY} says {py_now}, but {tag} is a candidate for "
            f"{rel.base_version}. Land the {rel.base_version} release bump "
            "(version.py, release notes, docs.json) before tagging the RC."
        )

    pkg_path = root / WEBUI_PACKAGE
    pkg_text = _read(pkg_path)
    pkg = json.loads(pkg_text)
    if pkg.get("version") not in (rel.base_version, rel.npm_version):
        raise ValueError(
            f"{WEBUI_PACKAGE} says {pkg.get('version')}, but {tag} is a candidate "
            f"for {rel.base_version}. Run `node installer/version/bump-ui-version.mjs` "
            "and commit."
        )

    _write(
        py_path,
        VERSION_PY_RE.sub(
            lambda m: f"{m.group(1)}{rel.pep440_version}{m.group(3)}",
            _read(py_path),
            count=1,
        ),
    )

    pkg_new, n = re.subn(
        r'^(  "version":\s*")([^"]+)(")',
        lambda m: f"{m.group(1)}{rel.npm_version}{m.group(3)}",
        pkg_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1 or json.loads(pkg_new).get("version") != rel.npm_version:
        raise ValueError(f"could not rewrite the top-level version in {pkg_path}")
    _write(pkg_path, pkg_new)

    lock_path = root / WEBUI_LOCK
    lock_new, n = _lock_root_re(pkg["name"]).subn(
        lambda m: f"{m.group(1)}{rel.npm_version}{m.group(3)}",
        _read(lock_path),
    )
    if n != 2:
        raise ValueError(
            f"expected the root version twice in {lock_path}, found {n}. Regenerate "
            "the lockfile with `npm install` in src/gaia/apps/webui."
        )
    _write(lock_path, lock_new)
    return rel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("classify", help="print KEY=VALUE outputs for a tag").add_argument(
        "tag"
    )
    st = sub.add_parser("stamp", help="write an RC's versions into the tree")
    st.add_argument("tag")
    st.add_argument("--root", type=Path, default=REPO_ROOT)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "classify":
            for key, value in parse_tag(args.tag).outputs().items():
                print(f"{key}={value}")
        else:
            rel = stamp(args.tag, args.root)
            print(
                f"stamped {rel.tag}: version.py={rel.pep440_version} "
                f"package.json={rel.npm_version}",
                file=sys.stderr,
            )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
