# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The Linux agent must start on the oldest distro it supports (#5526).

``check_glibc.py`` is the build gate for that and ``rthook_linux_ca.py`` keeps
HTTPS working when the binary runs on a different distro than it was built on.
Both are scripts PyInstaller and the workflow run, so they are loaded by path.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
WORKFLOW = (
    Path(__file__).resolve().parents[5]
    / ".github"
    / "workflows"
    / "build_flagship_sidecar.yml"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_glibc = _load("check_glibc")
rthook = _load("rthook_linux_ca")

# Trimmed from `objdump -p` on a real libpython: definitions name GLIBC too, and
# must not count as requirements.
OBJDUMP = """
libpython3.12.so.1.0:     file format elf64-x86-64

Version definitions:
1 0x01 0x0a5c8f61 libpython3.12.so.1.0
2 0x00 0x09691f73 GLIBC_2.99

Version References:
  required from libm.so.6:
    0x09691a75 0x00 07 GLIBC_2.2.5
    0x06969187 0x00 12 GLIBC_2.27
  required from libc.so.6:
    0x069691b4 0x00 11 GLIBC_2.34
    0x06969197 0x00 10 GLIBC_2.17
  required from libgcc_s.so.1:
    0x0b792650 0x00 13 GCC_3.0
"""


def test_reads_only_the_versions_a_file_requires():
    assert check_glibc.glibc_requirements(OBJDUMP) == {
        (2, 2, 5),
        (2, 27),
        (2, 34),
        (2, 17),
    }


def test_a_section_after_the_references_is_not_read():
    text = OBJDUMP + "\nDynamic Section:\n    0x06969187 0x00 12 GLIBC_2.40\n"
    assert max(check_glibc.glibc_requirements(text)) == (2, 34)


def test_versions_compare_numerically_not_as_text():
    # "2.4" > "2.34" as strings; a text compare would pass a 2.40 requirement.
    assert check_glibc.parse_version("2.40") > check_glibc.parse_version("2.34")
    assert check_glibc.parse_version("2.2.5") < check_glibc.parse_version("2.17")


def _scan_result(monkeypatch, newest):
    monkeypatch.setattr(check_glibc, "scan", lambda binary: newest)


def test_passes_at_the_floor(monkeypatch, capsys):
    _scan_result(monkeypatch, {"<bootloader>": (2, 17), "libpython3.12.so": (2, 34)})
    assert check_glibc.main(["gaia-agent", "--max", "2.34"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_fails_and_names_each_file_and_who_is_locked_out(monkeypatch, capsys):
    _scan_result(
        monkeypatch,
        {
            "<bootloader>": (2, 17),
            "libpython3.12.so.1.0": (2, 38),
            "numpy/_core/x.so": (2, 35),
        },
    )
    assert check_glibc.main(["gaia-agent", "--max", "2.34"]) == 1
    err = capsys.readouterr().err
    assert "GLIBC_2.38  libpython3.12.so.1.0" in err
    assert "GLIBC_2.35  numpy/_core/x.so" in err
    for distro in ("RHEL / Rocky 9", "Ubuntu 22.04", "Debian 12"):
        assert distro in err


def test_an_empty_scan_is_not_a_pass(monkeypatch):
    _scan_result(monkeypatch, {})
    assert check_glibc.main(["gaia-agent", "--max", "2.34"]) == 2


def test_an_archive_without_libpython_is_not_scanned(monkeypatch, tmp_path):
    binary = tmp_path / "gaia-agent"
    binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(check_glibc, "bundled_elf_files", lambda b, into: [])
    with pytest.raises(RuntimeError, match="no libpython3"):
        check_glibc.scan(binary)


def test_the_workflow_floor_reaches_every_supported_distro():
    freeze = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["freeze"]
    linux = next(
        row
        for row in freeze["strategy"]["matrix"]["include"]
        if row["platform"] == "linux-x64"
    )
    floor = check_glibc.parse_version(linux["glibc_floor"])
    assert floor <= min(check_glibc.DISTRO_GLIBC.values())
    steps = "\n".join(step.get("run", "") for step in freeze["steps"])
    assert re.search(r"check_glibc\.py\".*\n.*--max", steps)


def _paths(cafile=None, capath=None):
    return SimpleNamespace(cafile=cafile, capath=capath)


def test_ca_hook_points_a_foreign_openssl_at_the_host_bundle():
    # RHEL-built OpenSSL on Ubuntu: its /etc/pki defaults do not exist.
    found = rthook._os_ca_bundle(
        {}, _paths(), exists=lambda p: p == "/etc/ssl/certs/ca-certificates.crt"
    )
    assert found == "/etc/ssl/certs/ca-certificates.crt"


@pytest.mark.parametrize(
    "environ, paths",
    [
        ({"SSL_CERT_FILE": "/mine.pem"}, _paths()),
        ({"SSL_CERT_DIR": "/mine"}, _paths()),
        ({}, _paths(cafile="/etc/pki/tls/cert.pem")),
        ({}, _paths(capath="/usr/lib/ssl/certs")),
    ],
)
def test_ca_hook_leaves_a_working_or_user_set_store_alone(environ, paths):
    assert rthook._os_ca_bundle(environ, paths, exists=lambda p: True) is None


def test_ca_hook_sets_nothing_when_the_host_has_no_bundle():
    assert rthook._os_ca_bundle({}, _paths(), exists=lambda p: False) is None
