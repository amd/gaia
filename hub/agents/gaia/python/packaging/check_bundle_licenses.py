# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Fail the build when a frozen agent binary carries copyleft code GAIA must not ship.

The binary is read the way PyInstaller's bootloader reads it: the CArchive
appended to the executable, plus the PYZ of Python modules inside it. Every
entry name is matched against :data:`FORBIDDEN`. A one-dir build also has its
``_internal`` folder walked, since its libraries live there and not in the
archive.

Usage::

    python check_bundle_licenses.py dist/gaia-agent.exe

Exit code 0 = clean, 1 = forbidden content found, 2 = not a PyInstaller binary.
Needs PyInstaller installed (run it from the freeze environment).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

#: ``(what it is, pattern over an archive entry name)``. Names use ``/`` or
#: ``\\`` separators for files and dots for PYZ module names.
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PyInstaller's build-time package (GPL-2.0; only the bootloader "
        "carries the distribution exception)",
        re.compile(r"^PyInstaller(?:[./\\]|$)"),
    ),
    (
        "GNU Readline (GPL-3.0)",
        re.compile(
            r"(?:^|[/\\])(?:libreadline[^/\\]*|readline(?:\.[^/\\]*\.(?:so|pyd))?)$"
        ),
    ),
    (
        "GNU dbm (GPL-3.0)",
        re.compile(
            r"(?:^|[/\\])(?:libgdbm[^/\\]*|_g?dbm(?:\.[^/\\]*\.(?:so|pyd))?)$"
            r"|^dbm\.(?:gnu|ndbm)$"
        ),
    ),
    (
        "PyMuPDF / MuPDF (AGPL-3.0)",
        re.compile(
            r"(?:^|[/\\.])(?:pymupdf|fitz)(?:[./\\]|$)|(?:^|[/\\])(?:lib)?_?mupdf",
            re.IGNORECASE,
        ),
    ),
)


#: LGPL shared libraries that still ship, each dynamically linked and replaceable
#: (LGPL's terms), pending a maintainer decision to remove them. Reported on every
#: run so they stay visible. Add an entry only with the decision recorded in REVIEW.md.
REVIEWED_LGPL: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "libquadmath (LGPL-2.1-or-later): GCC quad-precision runtime vendored by the "
        "numpy and faiss-cpu Linux wheels",
        re.compile(r"(?:^|[/\\])libquadmath[^/\\]*$"),
    ),
    (
        "libasound / ALSA (LGPL-2.1-or-later): vendored by the sherpa-onnx Linux wheel",
        re.compile(r"(?:^|[/\\])libasound[^/\\]*$"),
    ),
    (
        "libkeyutils (LGPL-2.1-or-later): pulled in by the build container's system "
        "OpenSSL/Kerberos",
        re.compile(r"(?:^|[/\\])libkeyutils[^/\\]*$"),
    ),
)


def reviewed_lgpl_entries(names: Iterable[str]) -> list[tuple[str, str]]:
    """``(entry name, what it is)`` for every reviewed LGPL library present."""
    found = []
    for name in names:
        for label, pattern in REVIEWED_LGPL:
            if pattern.search(name):
                found.append((name, label))
                break
    return sorted(found)

def forbidden_entries(names: Iterable[str]) -> list[tuple[str, str]]:
    """``(entry name, what it is)`` for every name matching :data:`FORBIDDEN`."""
    found = []
    for name in names:
        for label, pattern in FORBIDDEN:
            if pattern.search(name):
                found.append((name, label))
                break
    return sorted(found)


def bundle_entry_names(binary: Path) -> list[str]:
    """Every entry in *binary*'s CArchive and embedded PYZ, plus a one-dir tree."""
    from PyInstaller.archive.readers import CArchiveReader, NotAnArchiveError
    from PyInstaller.loader.pyimod01_archive import ArchiveReadError

    try:
        reader = CArchiveReader(str(binary))
    except (ArchiveReadError, NotAnArchiveError, OSError, ValueError) as e:
        raise ValueError(f"{binary} is not a PyInstaller binary: {e}") from e

    names = list(reader.toc)
    pyz = [n for n in reader.toc if n.endswith(".pyz")]
    if not pyz:
        raise ValueError(
            f"{binary} has no embedded PYZ, so its Python modules cannot be "
            "checked; is it a PyInstaller binary?"
        )
    for archive in pyz:
        names += list(reader.open_embedded_archive(archive).toc)

    internal = binary.parent / "_internal"
    if internal.is_dir():
        names += [p.relative_to(internal).as_posix() for p in internal.rglob("*")]
    return names


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if a frozen agent binary bundles forbidden copyleft code."
    )
    parser.add_argument("binary", type=Path, help="The frozen agent executable.")
    args = parser.parse_args(argv)

    try:
        names = bundle_entry_names(args.binary)
    except ValueError as e:
        print(f"check_bundle_licenses: {e}", file=sys.stderr)
        return 2

    found = forbidden_entries(names)
    if found:
        print(
            f"check_bundle_licenses: FAIL -- {args.binary} bundles code GAIA must "
            "not ship:",
            file=sys.stderr,
        )
        for name, label in found:
            print(f"  {name}  ({label})", file=sys.stderr)
        print(
            "Exclude it in packaging/freeze.py (EXCLUDES / STDLIB_EXCLUDED), or "
            "find the import that pulls it in from the build's xref-*.html.",
            file=sys.stderr,
        )
        return 1
    print(
        f"check_bundle_licenses: PASS -- {len(names)} entries checked in {args.binary}"
    )
    for name, label in reviewed_lgpl_entries(names):
        print(f"  reviewed LGPL exception: {name}  ({label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
