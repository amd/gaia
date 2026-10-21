# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Fail the build if the frozen Linux agent needs a newer glibc than the floor.

A PyInstaller binary runs on the user's glibc, and every library packed inside
it -- the Python runtime, each extension module, each wheel's vendored .so --
asks for the glibc symbol versions of the system it was built against. One
library built on a newer system and the whole agent refuses to start with
``version `GLIBC_2.38' not found`` (#5526). This scans the bootloader and every
ELF file in the one-file archive and names each one that asks for too much.

Usage (from the freeze venv, which has PyInstaller; needs ``objdump``)::

    python check_glibc.py dist/gaia-agent --max 2.34

Exit code 0 = within the floor, 1 = too new, 2 = the scan could not run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
_VERSION_REF = re.compile(r"^\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+\d+\s+GLIBC_([\d.]+)\s*$")

#: The newest glibc of the oldest distro each floor was chosen for, so the
#: failure message names who would be locked out.
DISTRO_GLIBC = {
    "RHEL / Rocky 9": (2, 34),
    "Ubuntu 22.04": (2, 35),
    "Debian 12": (2, 36),
}


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def glibc_requirements(objdump_p: str) -> set[tuple[int, ...]]:
    """The ``GLIBC_x.y`` versions an ``objdump -p`` listing says it needs.

    Only the "Version References" section counts: those are the versions this
    file requires. "Version definitions" are what a library provides.
    """
    needed: set[tuple[int, ...]] = set()
    in_refs = False
    for line in objdump_p.splitlines():
        if line.startswith("Version References:"):
            in_refs = True
            continue
        if in_refs:
            if line and not line[0].isspace():
                in_refs = False
                continue
            match = _VERSION_REF.match(line)
            if match:
                needed.add(parse_version(match.group(1)))
    return needed


def _objdump(path: Path) -> str:
    result = subprocess.run(
        ["objdump", "-p", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"objdump -p {path} failed: {result.stderr.strip()}")
    return result.stdout


def bundled_elf_files(binary: Path, into: Path) -> list[tuple[str, Path]]:
    """Every ELF file packed in a PyInstaller one-file *binary*, written to *into*."""
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(binary))
    found: list[tuple[str, Path]] = []
    for index, name in enumerate(sorted(reader.toc)):
        data = reader.extract(name)
        if not isinstance(data, bytes) or not data.startswith(ELF_MAGIC):
            continue
        path = into / f"{index:05d}.so"
        path.write_bytes(data)
        found.append((name, path))
    return found


def scan(binary: Path) -> dict[str, tuple[int, ...]]:
    """Newest glibc version each ELF file needs, keyed by its name in the binary."""
    newest: dict[str, tuple[int, ...]] = {}
    with tempfile.TemporaryDirectory(prefix="gaia-glibc-") as tmp:
        files = [("<bootloader>", binary)] + bundled_elf_files(binary, Path(tmp))
        if not any(Path(name).name.startswith("libpython3") for name, _ in files):
            raise RuntimeError(
                f"no libpython3 found inside {binary}, so this is not the "
                "PyInstaller one-file binary the check expects"
            )
        for name, path in files:
            needed = glibc_requirements(_objdump(path))
            if needed:
                newest[name] = max(needed)
    return newest


def _fmt(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if the frozen agent needs a newer glibc than the floor."
    )
    parser.add_argument("binary", type=Path, help="The frozen one-file gaia-agent.")
    parser.add_argument(
        "--max", required=True, help="Newest glibc the binary may need, e.g. 2.34."
    )
    args = parser.parse_args(argv)
    floor = parse_version(args.max)

    try:
        newest = scan(args.binary)
    except (OSError, RuntimeError) as exc:
        print(f"check_glibc: could not scan {args.binary}: {exc}", file=sys.stderr)
        return 2
    if not newest:
        print(
            f"check_glibc: no file in {args.binary} references glibc, so the "
            "scan read nothing; refusing to call that a pass",
            file=sys.stderr,
        )
        return 2

    overall = max(newest.values())
    print(
        f"check_glibc: {len(newest)} ELF files need glibc; the newest any needs "
        f"is {_fmt(overall)} (floor {args.max})"
    )
    too_new = sorted(
        (version, name) for name, version in newest.items() if version > floor
    )
    if not too_new:
        print("check_glibc: PASS")
        return 0

    locked_out = [d for d, v in DISTRO_GLIBC.items() if v < overall]
    print(
        f"check_glibc: FAIL -- {len(too_new)} file(s) need a glibc newer than "
        f"{args.max}, so the agent would not start on "
        f"{', '.join(locked_out) or 'systems older than glibc ' + _fmt(overall)}:",
        file=sys.stderr,
    )
    for version, name in reversed(too_new):
        print(f"  GLIBC_{_fmt(version)}  {name}", file=sys.stderr)
    print(
        "Each was built on a newer system than the floor. Build the Linux agent "
        "in the floor container (the linux-x64 row of "
        ".github/workflows/build_flagship_sidecar.yml), and if a dependency "
        "only publishes wheels for newer glibc, pin a release that does not.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
