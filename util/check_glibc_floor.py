# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Fail the build if a native Linux binary needs a newer glibc than the floor.

A Linux binary asks for the glibc symbol versions of the system it was linked
on. gaiacpp linked on the runner's newest Ubuntu needed GLIBC_2.43 and would
not start on Debian 12 or Ubuntu 22.04 (``version `GLIBC_2.43' not found``).
This reads the binary's "Version References" (``objdump -p``) and names every
version above the floor, per library.

The C++ runtime is linked statically, so by default any libstdc++ (GLIBCXX_,
CXXABI_) or libgcc_s (GCC_) requirement is a failure: it means the static link
silently did not happen. Pass ``--max-glibcxx`` to allow one up to a version.

Usage (needs ``objdump`` from binutils)::

    python util/check_glibc_floor.py cpp/build/gaiacpp --max 2.35

Exit code 0 = within the floor, 1 = too new, 2 = the scan could not run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ELF_MAGIC = b"\x7fELF"

#: The glibc of each distro the floor was chosen for, so a failure names who
#: would be locked out.
DISTRO_GLIBC = {
    "RHEL / Rocky 9": (2, 34),
    "Ubuntu 22.04": (2, 35),
    "Debian 12": (2, 36),
    "Ubuntu 24.04": (2, 39),
}

#: Non-numeric glibc versions, as the release that introduced them. A binary
#: linked with ``-z pack-relative-relocs`` needs DT_RELR support (glibc 2.36).
NAMED_VERSIONS = {"ABI_DT_RELR": (2, 36)}

_REQUIRED_FROM = re.compile(r"^\s+required from (\S+):\s*$")
# A "Version References" row: hash, flags, index, name.
_VERSION_ROW = re.compile(r"^\s+0x[0-9a-f]+\s+0x[0-9a-f]+\s+\d+\s+(\S+)\s*$")
_PREFIXED = re.compile(r"^(GLIBC|GLIBCXX|CXXABI|GCC)_(.+)$")


def parse_version(text: str) -> tuple[int, ...]:
    if text in NAMED_VERSIONS:
        return NAMED_VERSIONS[text]
    return tuple(int(part) for part in text.split("."))


def fmt(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def version_references(objdump_p: str) -> list[tuple[str, str, tuple[int, ...]]]:
    """``(library, family, version)`` for each version an ``objdump -p`` listing needs.

    *family* is ``GLIBC``, ``GLIBCXX``, ``CXXABI`` or ``GCC``. Only the
    "Version References" section counts: "Version definitions" are what a
    library provides, not what it needs.
    """
    refs: list[tuple[str, str, tuple[int, ...]]] = []
    in_refs = False
    library = ""
    for line in objdump_p.splitlines():
        if line.startswith("Version References:"):
            in_refs = True
            continue
        if not in_refs:
            continue
        if line and not line[0].isspace():
            in_refs = False
            continue
        required = _REQUIRED_FROM.match(line)
        if required:
            library = required.group(1)
            continue
        row = _VERSION_ROW.match(line)
        if not row:
            continue
        prefixed = _PREFIXED.match(row.group(1))
        if not prefixed:
            continue
        family, raw = prefixed.groups()
        try:
            version = parse_version(raw)
        except ValueError:
            # GLIBC_PRIVATE and the like: no release number, never shipped to apps.
            version = (999,)
        refs.append((library, family, version))
    return refs


def offenders(
    refs: list[tuple[str, str, tuple[int, ...]]],
    max_glibc: tuple[int, ...],
    max_glibcxx: tuple[int, ...] | None,
) -> list[str]:
    """One line per requirement above the floor, newest first."""
    found = []
    for library, family, version in sorted(refs, key=lambda r: r[2], reverse=True):
        if family == "GLIBC":
            if version > max_glibc:
                found.append(
                    f"{library}: GLIBC_{fmt(version)} (floor {fmt(max_glibc)})"
                )
        elif family == "GLIBCXX" and max_glibcxx is not None:
            if version > max_glibcxx:
                found.append(
                    f"{library}: GLIBCXX_{fmt(version)} (floor {fmt(max_glibcxx)})"
                )
        elif max_glibcxx is None:
            found.append(
                f"{library}: {family}_{fmt(version)} (the C++ runtime should be "
                "linked statically; check GAIA_STATIC_CXX_RUNTIME)"
            )
    return found


def _objdump_p(path: Path) -> str:
    try:
        result = subprocess.run(
            ["objdump", "-p", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"objdump -p {path} did not finish in 120 s") from exc
    if result.returncode != 0:
        raise RuntimeError(f"objdump -p {path} failed: {result.stderr.strip()}")
    return result.stdout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if a Linux binary needs a newer glibc than the floor."
    )
    parser.add_argument("binary", type=Path, help="A dynamically linked ELF binary.")
    parser.add_argument(
        "--max", required=True, help="Newest glibc the binary may need, e.g. 2.35."
    )
    parser.add_argument(
        "--max-glibcxx",
        help="Newest GLIBCXX the binary may need. Omit to require a static C++ runtime.",
    )
    args = parser.parse_args(argv)
    max_glibc = parse_version(args.max)
    max_glibcxx = parse_version(args.max_glibcxx) if args.max_glibcxx else None

    try:
        with args.binary.open("rb") as fh:
            if fh.read(len(ELF_MAGIC)) != ELF_MAGIC:
                raise RuntimeError("not an ELF file")
        refs = version_references(_objdump_p(args.binary))
    except (OSError, RuntimeError) as exc:
        print(
            f"check_glibc_floor: could not scan {args.binary}: {exc}", file=sys.stderr
        )
        return 2

    glibc = [v for _, family, v in refs if family == "GLIBC"]
    if not glibc:
        print(
            f"check_glibc_floor: {args.binary} references no glibc version, so the "
            "scan read nothing; refusing to call that a pass",
            file=sys.stderr,
        )
        return 2

    libraries = sorted({lib for lib, _, _ in refs})
    print(
        f"check_glibc_floor: {args.binary} needs glibc {fmt(max(glibc))} at most "
        f"(floor {args.max}) from {', '.join(libraries)}"
    )
    found = offenders(refs, max_glibc, max_glibcxx)
    if not found:
        print("check_glibc_floor: PASS")
        return 0

    worst = max(glibc)
    locked_out = (
        [d for d, v in DISTRO_GLIBC.items() if v < worst] if worst > max_glibc else []
    )
    print(
        f"check_glibc_floor: FAIL -- {args.binary} would not start on "
        f"{', '.join(locked_out) or 'systems older than its build host'}:",
        file=sys.stderr,
    )
    for line in found:
        print(f"  {line}", file=sys.stderr)
    print(
        "It was linked on a newer system than the floor. Build it on the floor "
        "runner (the linux-x64 leg of .github/workflows/build_agents.yml), and "
        "clear any cache restored from a newer image.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
