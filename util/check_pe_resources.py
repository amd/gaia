#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Assert a Windows .exe actually carries an icon and version resource.

Checking that a ``.syso`` file exists proves nothing: the Go linker silently
ignores a resource object whose ``GOOS_GOARCH`` filename suffix does not match
the target, and a malformed one links to nothing. The only claim worth making
is about the bytes that ship, so this walks the linked binary's PE resource
directory and reports what is genuinely in there.

Usage::

    python util/check_pe_resources.py bin/gaia-win-x64.exe
    python util/check_pe_resources.py --expect-version 0.23.0 bin/gaia-win-x64.exe

Exits non-zero, naming what is missing, if the icon or version resource is
absent. Reads the file directly -- no pefile dependency, no Windows required,
so the same check runs on the Linux runner that cross-compiles the binary.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# Resource type ids from winuser.h. RT_GROUP_ICON is the one Explorer reads to
# pick an icon for the file; RT_ICON alone is not enough.
RT_ICON = 3
RT_VERSION = 16
RT_GROUP_ICON = 14

RESOURCE_TYPE_NAMES = {
    RT_ICON: "RT_ICON",
    RT_VERSION: "RT_VERSION",
    RT_GROUP_ICON: "RT_GROUP_ICON",
}


class PEError(Exception):
    """The file is not a PE image we can read."""


def _section_containing(sections, rva):
    for name, virt_addr, virt_size, raw_size, raw_ptr in sections:
        if virt_addr <= rva < virt_addr + max(virt_size, raw_size):
            return name, virt_addr, raw_ptr
    return None


def _parse_pe(data: bytes):
    """Return (machine, sections, resource_rva, resource_size) for a PE image."""
    if data[:2] != b"MZ":
        raise PEError("not a PE image: missing the MZ DOS header")
    (pe_off,) = struct.unpack_from("<I", data, 0x3C)
    if data[pe_off : pe_off + 4] != b"PE\0\0":
        raise PEError(f"not a PE image: no PE signature at offset {pe_off:#x}")

    coff = pe_off + 4
    machine, num_sections, _, _, _, opt_size, _ = struct.unpack_from(
        "<HHIIIHH", data, coff
    )
    opt = coff + 20
    (magic,) = struct.unpack_from("<H", data, opt)
    if magic == 0x20B:  # PE32+
        data_dir = opt + 112
    elif magic == 0x10B:  # PE32
        data_dir = opt + 96
    else:
        raise PEError(f"unrecognized optional-header magic {magic:#x}")

    # Data directory entry 2 is the resource table.
    res_rva, res_size = struct.unpack_from("<II", data, data_dir + 2 * 8)

    sections = []
    sec_off = opt + opt_size
    for i in range(num_sections):
        base = sec_off + i * 40
        raw_name = data[base : base + 8].rstrip(b"\0").decode("ascii", "replace")
        virt_size, virt_addr, raw_size, raw_ptr = struct.unpack_from(
            "<IIII", data, base + 8
        )
        sections.append((raw_name, virt_addr, virt_size, raw_size, raw_ptr))

    return machine, sections, res_rva, res_size


def _walk_resource_types(data: bytes, sections, res_rva: int) -> dict[int, int]:
    """Map resource type id -> number of entries beneath it."""
    located = _section_containing(sections, res_rva)
    if located is None:
        raise PEError(f"resource RVA {res_rva:#x} falls outside every section")
    _, sec_rva, sec_ptr = located
    base = sec_ptr + (res_rva - sec_rva)

    # IMAGE_RESOURCE_DIRECTORY: 12 reserved/stamp bytes, then the two counts.
    named, ided = struct.unpack_from("<HH", data, base + 12)
    counts: dict[int, int] = {}
    for i in range(named + ided):
        entry = base + 16 + i * 8
        name_or_id, offset_to_data = struct.unpack_from("<II", data, entry)
        if name_or_id & 0x80000000:  # a string-named type, not one of ours
            continue
        if not offset_to_data & 0x80000000:  # a leaf where a subdirectory belongs
            continue
        sub = base + (offset_to_data & 0x7FFFFFFF)
        sub_named, sub_ided = struct.unpack_from("<HH", data, sub + 12)
        counts[name_or_id] = sub_named + sub_ided
    return counts


def _resource_text(data: bytes, sections, res_rva: int, res_size: int) -> str:
    """The UTF-16 text of the resource section only.

    Scoped to the section rather than the whole file on purpose: version strings
    are stored UTF-16LE inside RT_VERSION, and searching the entire binary would
    also match the same characters sitting in Go's string table -- which would
    let --expect-version pass on a binary whose resource says something else.
    """
    located = _section_containing(sections, res_rva)
    if located is None:
        return ""
    _, sec_rva, sec_ptr = located
    start = sec_ptr + (res_rva - sec_rva)
    return data[start : start + res_size].decode("utf-16-le", errors="ignore")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("exe", type=Path, help="the .exe to inspect")
    ap.add_argument(
        "--expect-version",
        help="also assert this string appears in the binary's version resource",
    )
    args = ap.parse_args()

    if not args.exe.is_file():
        print(f"::error::{args.exe} does not exist", file=sys.stderr)
        return 1

    data = args.exe.read_bytes()
    try:
        machine, sections, res_rva, res_size = _parse_pe(data)
    except (PEError, struct.error) as e:
        print(f"::error::{args.exe}: {e}", file=sys.stderr)
        return 1

    if res_rva == 0 or res_size == 0:
        print(
            f"::error::{args.exe} has NO resource directory at all. The Go linker embeds one "
            f"only when a matching resource_windows_<goarch>.syso sits in the main package "
            f"directory (tui/cmd/gaia/). Run tui/scripts/gen-winres.sh before building, and "
            f"check the .syso arch suffix matches GOARCH.",
            file=sys.stderr,
        )
        return 1

    try:
        counts = _walk_resource_types(data, sections, res_rva)
    except (PEError, struct.error) as e:
        print(
            f"::error::{args.exe}: unreadable resource directory: {e}", file=sys.stderr
        )
        return 1
    present = ", ".join(
        f"{RESOURCE_TYPE_NAMES.get(t, t)}={n}" for t, n in sorted(counts.items())
    )
    print(f"{args.exe.name}: machine={machine:#06x} resources: {present or '<none>'}")

    missing = []
    if counts.get(RT_GROUP_ICON, 0) < 1:
        missing.append("RT_GROUP_ICON (the icon Explorer draws)")
    if counts.get(RT_ICON, 0) < 1:
        missing.append("RT_ICON (the icon image data)")
    if counts.get(RT_VERSION, 0) < 1:
        missing.append("RT_VERSION (the Details-tab metadata)")
    if missing:
        print(
            f"::error::{args.exe} is missing {', '.join(missing)}. The binary linked a resource "
            f"directory but not the one gen-winres.sh produces -- check that the .syso was "
            f"regenerated for this GOARCH and that src/gaia/img/gaia.ico was readable.",
            file=sys.stderr,
        )
        return 1

    if args.expect_version:
        if args.expect_version not in _resource_text(data, sections, res_rva, res_size):
            print(
                f"::error::{args.exe} carries a version resource but it does not mention "
                f"{args.expect_version!r}. gen-winres.sh stamps --version into it, so this "
                f"means the resource was generated for a different version than the binary.",
                file=sys.stderr,
            )
            return 1
        print(f"  version resource mentions {args.expect_version}")

    print(f"  OK - icon and version resource present in {args.exe.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
