# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_pe_resources.py.

The checker walks a Windows PE resource directory by hand, and CI runs it on the
Linux runner that cross-compiles the .exe. So the fixtures here are synthetic PE
images built with struct.pack -- no Windows binary is read, and the suite passes
on every runner OS.
"""

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

from check_pe_resources import main  # noqa: E402

PE32 = 0x10B
PE32PLUS = 0x20B

RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16

ICON_AND_VERSION = {RT_ICON: 1, RT_GROUP_ICON: 1, RT_VERSION: 1}

PE_OFFSET = 0x80
TEXT_RVA = 0x1000
TEXT_RAW = 0x400
TEXT_RAW_SIZE = 0x200
RSRC_RVA = 0x2000
RSRC_RAW = TEXT_RAW + TEXT_RAW_SIZE
DATA_RVA = 0x3000
FILE_ALIGN = 0x200


# ---------------------------------------------------------------------------
# Synthetic PE builder
# ---------------------------------------------------------------------------


def _align(n: int, alignment: int) -> int:
    return ((n + alignment - 1) // alignment) * alignment


def _resource_dir(counts: dict[int, int], version_text: str | None) -> bytes:
    """A root IMAGE_RESOURCE_DIRECTORY with one subdirectory per type id."""
    type_ids = sorted(counts)
    header = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(type_ids))
    sub_start = len(header) + len(type_ids) * 8

    entries = bytearray()
    subs = bytearray()
    for type_id in type_ids:
        offset = sub_start + len(subs)
        entries += struct.pack("<II", type_id, offset | 0x80000000)
        subs += struct.pack("<IIHHHH", 0, 0, 0, 0, 0, counts[type_id])
        for i in range(counts[type_id]):
            subs += struct.pack("<II", i + 1, 0)  # leaf entries; never descended into

    blob = header + bytes(entries) + bytes(subs)
    if version_text is not None:
        # Version strings live UTF-16LE inside RT_VERSION; --expect-version
        # searches the decoded section, so it has to be encoded that way here.
        blob += version_text.encode("utf-16-le")
    return blob


def build_pe(
    *,
    resource_types: dict[int, int] | None = ICON_AND_VERSION,
    version_text: str | None = None,
    magic: int = PE32PLUS,
    resource_rva: int | None = None,
    resource_size_overrun: int = 0,
    trailing_section_text: str | None = None,
    machine: int = 0x8664,
    truncate_to: int | None = None,
) -> bytes:
    """A minimal but structurally valid PE image.

    ``resource_types`` maps a resource type id to the number of entries beneath
    it; ``None`` builds an image with no resource directory at all.
    ``resource_size_overrun`` inflates the data directory's (virtual) resource
    size past the .rsrc section's raw bytes, and ``trailing_section_text`` puts
    a UTF-16LE string in the section that follows -- together they reproduce a
    resource read spilling into the next section.
    """
    blob = (
        b"" if resource_types is None else _resource_dir(resource_types, version_text)
    )
    rsrc_raw_size = _align(max(len(blob), 1), FILE_ALIGN)

    if resource_types is None:
        res_rva, res_size = 0, 0
    else:
        res_rva = RSRC_RVA
        res_size = (
            rsrc_raw_size + resource_size_overrun
            if resource_size_overrun
            else len(blob)
        )
    if resource_rva is not None:
        res_rva = resource_rva

    opt_size = 240 if magic == PE32PLUS else 224
    data_dir = 112 if magic == PE32PLUS else 96
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, magic)
    struct.pack_into("<II", opt, data_dir + 2 * 8, res_rva, res_size)

    sections = [(b".text", 0x100, TEXT_RVA, TEXT_RAW_SIZE, TEXT_RAW)]
    if resource_types is not None:
        sections.append(
            (b".rsrc", max(len(blob), 1), RSRC_RVA, rsrc_raw_size, RSRC_RAW)
        )

    trailing = b""
    if trailing_section_text is not None:
        trailing = trailing_section_text.encode("utf-16-le")
        sections.append(
            (
                b".data",
                max(len(trailing), 1),
                DATA_RVA,
                _align(max(len(trailing), 1), FILE_ALIGN),
                RSRC_RAW + rsrc_raw_size,
            )
        )

    coff = struct.pack("<HHIIIHH", machine, len(sections), 0, 0, 0, opt_size, 0x22)

    dos = bytearray(PE_OFFSET)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, PE_OFFSET)

    image = bytearray(dos)
    image += b"PE\0\0" + coff + bytes(opt)
    for name, virt_size, virt_addr, raw_size, raw_ptr in sections:
        image += name.ljust(8, b"\0")
        image += struct.pack("<IIII", virt_size, virt_addr, raw_size, raw_ptr)
        image += bytes(16)

    assert len(image) <= TEXT_RAW, "headers overflowed the first section's raw offset"
    image += bytes(TEXT_RAW - len(image))
    image += bytes(TEXT_RAW_SIZE)
    if resource_types is not None:
        image += blob
        image += bytes(rsrc_raw_size - len(blob))
    if trailing:
        image += trailing
        image += bytes(_align(len(trailing), FILE_ALIGN) - len(trailing))

    return bytes(image) if truncate_to is None else bytes(image)[:truncate_to]


@pytest.fixture
def make_exe(tmp_path):
    """Write a synthetic PE to tmp_path and return its path."""
    counter = {"n": 0}

    def _make(*, raw: bytes | None = None, **kwargs) -> Path:
        counter["n"] += 1
        path = tmp_path / f"probe{counter['n']}.exe"
        path.write_bytes(build_pe(**kwargs) if raw is None else raw)
        return path

    return _make


def run_checker(monkeypatch, capsys, *argv):
    """Invoke the tool the way CI does and return (exit_code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["check_pe_resources.py", *[str(a) for a in argv]])
    code = main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_icon_and_version_present_exits_zero(make_exe, monkeypatch, capsys):
    code, out, err = run_checker(monkeypatch, capsys, make_exe())
    assert code == 0, err
    assert "RT_GROUP_ICON" in out
    assert "RT_VERSION" in out
    assert err == ""


def test_pe32_image_is_parsed_like_pe32_plus(make_exe, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(magic=PE32))
    assert code == 0, err


def test_expected_version_found_in_resource_section(make_exe, monkeypatch, capsys):
    exe = make_exe(version_text="1.2.3")
    code, out, err = run_checker(monkeypatch, capsys, exe, "--expect-version", "1.2.3")
    assert code == 0, err
    assert "1.2.3" in out


# ---------------------------------------------------------------------------
# Missing resources
# ---------------------------------------------------------------------------


def test_no_resource_directory_at_all_is_reported(make_exe, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(resource_types=None))
    assert code == 1
    assert "resource directory" in err.lower()
    assert "syso" in err  # points at the fix, not just the symptom


def test_empty_resource_directory_names_every_missing_type(
    make_exe, monkeypatch, capsys
):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(resource_types={}))
    assert code == 1
    assert "RT_GROUP_ICON" in err
    assert "RT_ICON" in err
    assert "RT_VERSION" in err


def test_missing_group_icon_is_named(make_exe, monkeypatch, capsys):
    exe = make_exe(resource_types={RT_ICON: 1, RT_VERSION: 1})
    code, _, err = run_checker(monkeypatch, capsys, exe)
    assert code == 1
    assert "RT_GROUP_ICON" in err
    assert "RT_VERSION" not in err


def test_missing_version_resource_is_named(make_exe, monkeypatch, capsys):
    exe = make_exe(resource_types={RT_ICON: 1, RT_GROUP_ICON: 1})
    code, _, err = run_checker(monkeypatch, capsys, exe)
    assert code == 1
    assert "RT_VERSION" in err
    assert "RT_GROUP_ICON" not in err


# ---------------------------------------------------------------------------
# Malformed input -- reported, never raised
# ---------------------------------------------------------------------------


def test_truncated_file_is_reported_not_raised(make_exe, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(truncate_to=40))
    assert code == 1
    assert "::error::" in err
    assert "Traceback" not in err


def test_truncated_resource_section_is_reported(make_exe, monkeypatch, capsys):
    # Headers survive, the .rsrc bytes they point at do not.
    code, _, err = run_checker(monkeypatch, capsys, make_exe(truncate_to=TEXT_RAW + 16))
    assert code == 1
    assert "resource directory" in err.lower()
    assert "Traceback" not in err


def test_resource_rva_outside_every_section_is_reported(make_exe, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(resource_rva=0x9000))
    assert code == 1
    assert "outside every section" in err


def test_non_pe_file_is_rejected(make_exe, monkeypatch, capsys):
    exe = make_exe(raw=b"#!/bin/sh\necho not an exe\n")
    code, _, err = run_checker(monkeypatch, capsys, exe)
    assert code == 1
    assert "not a PE image" in err


def test_mz_without_pe_signature_is_rejected(make_exe, monkeypatch, capsys):
    image = bytearray(build_pe())
    image[PE_OFFSET : PE_OFFSET + 4] = b"NOPE"
    code, _, err = run_checker(monkeypatch, capsys, make_exe(raw=bytes(image)))
    assert code == 1
    assert "PE signature" in err


def test_unrecognized_optional_header_magic_is_rejected(make_exe, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, make_exe(magic=0x107))
    assert code == 1
    assert "magic" in err


def test_missing_file_is_reported(tmp_path, monkeypatch, capsys):
    code, _, err = run_checker(monkeypatch, capsys, tmp_path / "absent.exe")
    assert code == 1
    assert "does not exist" in err


# ---------------------------------------------------------------------------
# --expect-version
# ---------------------------------------------------------------------------


def test_wrong_expected_version_fails(make_exe, monkeypatch, capsys):
    exe = make_exe(version_text="1.2.3")
    code, _, err = run_checker(monkeypatch, capsys, exe, "--expect-version", "9.9.9")
    assert code == 1
    assert "9.9.9" in err


def test_expected_version_only_matches_the_resource_section(
    make_exe, monkeypatch, capsys
):
    # The version sits in .text, not .rsrc -- a whole-file search would pass here.
    image = bytearray(build_pe())
    image[TEXT_RAW : TEXT_RAW + 10] = "9.9.9".encode("utf-16-le")
    code, _, err = run_checker(
        monkeypatch, capsys, make_exe(raw=bytes(image)), "--expect-version", "9.9.9"
    )
    assert code == 1
    assert "9.9.9" in err


def test_expected_version_does_not_spill_into_the_next_section(
    make_exe, monkeypatch, capsys
):
    # The data directory's resource size is virtual and can exceed .rsrc's raw
    # bytes; reading it unclamped would find "9.9.9" sitting in .data.
    exe = make_exe(
        version_text="1.2.3",
        resource_size_overrun=0x100,
        trailing_section_text="9.9.9",
    )
    code, _, err = run_checker(monkeypatch, capsys, exe, "--expect-version", "9.9.9")
    assert code == 1
    assert "9.9.9" in err

    # The real version, inside .rsrc, still matches.
    code, _, err = run_checker(monkeypatch, capsys, exe, "--expect-version", "1.2.3")
    assert code == 0, err
