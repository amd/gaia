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
_VERSION = r"GLIBC_([\d.]+|ABI_DT_RELR)"
# A "Version References" row of `objdump -p`: hash, flags, index, name.
_VERSION_REF = re.compile(rf"^\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+\d+\s+{_VERSION}\s*$")
# An undefined symbol in `objdump -T`; hidden versions print in parentheses.
_UNDEFINED_SYMBOL = re.compile(rf"\*UND\*\s+[0-9a-f]+\s+\(?{_VERSION}\)?\s+(\S+)\s*$")

#: Non-numeric glibc versions, as the release that introduced them. A library
#: linked with ``-z pack-relative-relocs`` needs DT_RELR support (glibc 2.36).
NAMED_VERSIONS = {"ABI_DT_RELR": (2, 36)}

#: The glibc of the oldest release of each distro the floor was chosen for, so
#: the failure message names who would be locked out.
DISTRO_GLIBC = {
    "RHEL / Rocky 9": (2, 34),
    "Ubuntu 22.04": (2, 35),
    "Debian 12": (2, 36),
}

#: Symbols newer than the floor that are still safe, because RHEL 9's glibc 2.34
#: backports them under their upstream version and every other target is new
#: enough to have them. RHEL 9's own libgcc_s unwinds through _dl_find_object,
#: so the libgcc_s built in the floor container needs it. Keep each entry within
#: the oldest non-RHEL target: the distro-smoke job on Rocky 9 proves the rest.
RHEL9_BACKPORTS = {((2, 35), "_dl_find_object")}


def parse_version(text: str) -> tuple[int, ...]:
    if text in NAMED_VERSIONS:
        return NAMED_VERSIONS[text]
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


def glibc_symbols(objdump_t: str) -> set[tuple[tuple[int, ...], str]]:
    """``(version, symbol)`` for each glibc symbol an ``objdump -T`` listing imports."""
    found = set()
    for line in objdump_t.splitlines():
        match = _UNDEFINED_SYMBOL.search(line)
        if match:
            found.add((parse_version(match.group(1)), match.group(2)))
    return found


def too_new(
    needed: set[tuple[int, ...]],
    symbols: set[tuple[tuple[int, ...], str]],
    floor: tuple[int, ...],
) -> list[tuple[tuple[int, ...], str]]:
    """What a file needs above *floor*, minus :data:`RHEL9_BACKPORTS`.

    A version need no imported symbol accounts for (``ABI_DT_RELR`` has none)
    is reported as ``"<no symbol>"``, never waved through.
    """
    over = {(v, s) for v, s in symbols if v > floor}
    explained = {v for v, _ in over}
    over |= {(v, "<no symbol>") for v in needed if v > floor and v not in explained}
    return sorted(over - RHEL9_BACKPORTS, reverse=True)


def _objdump(flag: str, path: Path) -> str:
    result = subprocess.run(
        ["objdump", flag, str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"objdump {flag} {path} failed: {result.stderr.strip()}")
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


def scan(binary: Path, floor: tuple[int, ...]):
    """``(newest, offenders)`` for every ELF file in *binary*.

    *newest* maps each file that needs glibc to the newest version it needs;
    *offenders* maps each file that needs more than *floor* to what it needs.
    """
    newest: dict[str, tuple[int, ...]] = {}
    offenders: dict[str, list[tuple[tuple[int, ...], str]]] = {}
    with tempfile.TemporaryDirectory(prefix="gaia-glibc-") as tmp:
        files = [("<bootloader>", binary)] + bundled_elf_files(binary, Path(tmp))
        if not any(Path(name).name.startswith("libpython3") for name, _ in files):
            raise RuntimeError(
                f"no libpython3 found inside {binary}, so this is not the "
                "PyInstaller one-file binary the check expects"
            )
        for name, path in files:
            needed = glibc_requirements(_objdump("-p", path))
            if not needed:
                continue
            newest[name] = max(needed)
            if newest[name] > floor:
                excess = too_new(needed, glibc_symbols(_objdump("-T", path)), floor)
                if excess:
                    offenders[name] = excess
    return newest, offenders


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
        newest, offenders = scan(args.binary, floor)
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

    print(
        f"check_glibc: {len(newest)} ELF files need glibc; the newest any needs "
        f"is {_fmt(max(newest.values()))} (floor {args.max}; allowed above it: "
        + ", ".join(f"{s}@GLIBC_{_fmt(v)}" for v, s in sorted(RHEL9_BACKPORTS))
        + ")"
    )
    if not offenders:
        print("check_glibc: PASS")
        return 0

    worst = max(v for needs in offenders.values() for v, _ in needs)
    locked_out = [d for d, v in DISTRO_GLIBC.items() if v < worst]
    print(
        f"check_glibc: FAIL -- {len(offenders)} file(s) need a glibc newer than "
        f"{args.max}, so the agent would not start on "
        f"{', '.join(locked_out) or 'systems older than glibc ' + _fmt(worst)}:",
        file=sys.stderr,
    )
    for name, needs in sorted(
        offenders.items(), key=lambda item: item[1][0], reverse=True
    ):
        listed = ", ".join(f"{s}@GLIBC_{_fmt(v)}" for v, s in needs)
        print(f"  {name}: {listed}", file=sys.stderr)
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
