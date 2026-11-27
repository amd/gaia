# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_glibc_floor.py holds the Linux gaiacpp to the glibc floor."""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "util" / "check_glibc_floor.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_glibc_floor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cgf = _load()

# objdump -p of the v0.27.0-rc4 gaiacpp, linked on Ubuntu 26.04.
TOO_NEW = """
Dynamic Section:
  NEEDED               libm.so.6
  NEEDED               libc.so.6

Version definitions:
1 0x01 0x0d2e5d4e gaiacpp
    GLIBC_2.99

Version References:
  required from libm.so.6:
    0x06969194 0x00 07 GLIBC_2.43
    0x09691a75 0x00 05 GLIBC_2.2.5
  required from libc.so.6:
    0x069691b8 0x00 06 GLIBC_2.38
    0x0d696914 0x00 04 GLIBC_2.4
    0x09691a75 0x00 02 GLIBC_2.2.5
"""

WITHIN = """
Version References:
  required from libm.so.6:
    0x09691a75 0x00 05 GLIBC_2.2.5
    0x06969197 0x00 07 GLIBC_2.29
  required from libc.so.6:
    0x069691b4 0x00 06 GLIBC_2.34
    0x09691a75 0x00 02 GLIBC_2.2.5
"""

SHARED_CXX = WITHIN + """  required from libstdc++.so.6:
    0x02297f89 0x00 09 GLIBCXX_3.4.32
    0x056bafd3 0x00 08 CXXABI_1.3
"""


def test_reads_each_library_and_version():
    refs = cgf.version_references(TOO_NEW)
    assert ("libm.so.6", "GLIBC", (2, 43)) in refs
    assert ("libc.so.6", "GLIBC", (2, 38)) in refs
    # Version definitions are what a file provides, not what it needs.
    assert all(v != (2, 99) for _, _, v in refs)


def test_the_rc4_binary_fails_and_names_both_libraries():
    found = cgf.offenders(cgf.version_references(TOO_NEW), (2, 35), None)
    assert found[0].startswith("libm.so.6: GLIBC_2.43")
    assert any(line.startswith("libc.so.6: GLIBC_2.38") for line in found)


def test_a_floor_build_passes():
    assert cgf.offenders(cgf.version_references(WITHIN), (2, 35), None) == []


def test_exactly_the_floor_passes_and_one_above_fails():
    refs = [("libc.so.6", "GLIBC", (2, 35))]
    assert cgf.offenders(refs, (2, 35), None) == []
    refs = [("libc.so.6", "GLIBC", (2, 36))]
    assert cgf.offenders(refs, (2, 35), None)


def test_dt_relr_counts_as_glibc_2_36():
    text = "Version References:\n  required from libc.so.6:\n    0x0 0x00 03 GLIBC_ABI_DT_RELR\n"
    assert cgf.offenders(cgf.version_references(text), (2, 35), None)


def test_a_shared_cxx_runtime_fails_by_default():
    found = cgf.offenders(cgf.version_references(SHARED_CXX), (2, 35), None)
    assert any("GLIBCXX_3.4.32" in line for line in found)
    assert any("CXXABI_1.3" in line for line in found)


def test_glibcxx_is_held_to_its_own_floor_when_allowed():
    refs = cgf.version_references(SHARED_CXX)
    assert any("GLIBCXX" in line for line in cgf.offenders(refs, (2, 35), (3, 4, 30)))
    assert cgf.offenders(refs, (2, 35), (3, 4, 32)) == []


@pytest.fixture
def elf(tmp_path):
    path = tmp_path / "gaiacpp"
    path.write_bytes(cgf.ELF_MAGIC + b"\0" * 60)
    return path


def test_main_exit_codes(elf, monkeypatch, capsys):
    monkeypatch.setattr(cgf, "_objdump_p", lambda _p: TOO_NEW)
    assert cgf.main([str(elf), "--max", "2.35"]) == 1
    err = capsys.readouterr().err
    assert "Debian 12" in err and "Ubuntu 22.04" in err

    monkeypatch.setattr(cgf, "_objdump_p", lambda _p: WITHIN)
    assert cgf.main([str(elf), "--max", "2.35"]) == 0


def test_a_scan_that_reads_nothing_is_not_a_pass(elf, monkeypatch):
    monkeypatch.setattr(cgf, "_objdump_p", lambda _p: "Dynamic Section:\n")
    assert cgf.main([str(elf), "--max", "2.35"]) == 2


def test_a_non_elf_file_is_an_error(tmp_path):
    path = tmp_path / "gaiacpp"
    path.write_text("#!/bin/sh\n")
    assert cgf.main([str(path), "--max", "2.35"]) == 2
    assert cgf.main([str(tmp_path / "missing"), "--max", "2.35"]) == 2


def _build_job():
    text = (REPO_ROOT / ".github" / "workflows" / "build_agents.yml").read_text(
        encoding="utf-8"
    )
    return yaml.safe_load(text)["jobs"]["build"]


def test_the_linux_release_leg_builds_on_the_floor_and_gates_on_it():
    job = _build_job()
    legs = job["strategy"]["matrix"]["include"]
    linux = [leg for leg in legs if leg["lemonade_os"] == "linux"]
    assert len(linux) == 1
    leg = linux[0]
    # Floor runner, not ubuntu-latest, and the gate's floor is that runner's glibc.
    assert leg["os"] == "ubuntu-22.04"
    assert leg["glibc_floor"] == "2.35"
    names = [s.get("name", "") for s in job["steps"]]
    gate = next(
        i for i, n in enumerate(names) if n.startswith("Check gaiacpp runs on glibc")
    )
    build = names.index("Build (Release)")
    package = next(
        i for i, n in enumerate(names) if n.startswith("Package gaiacpp for Lemonade")
    )
    assert build < gate < package
    step = job["steps"][gate]
    assert "util/check_glibc_floor.py" in step["run"]
    assert "matrix.glibc_floor" in step["if"]
    assert not step.get("continue-on-error")


def test_caches_are_keyed_by_runner_image():
    # A static OpenSSL built on a newer image would carry its glibc into the floor build.
    for step in _build_job()["steps"]:
        if step.get("uses", "").startswith("actions/cache"):
            assert "matrix.os" in step["with"]["key"], step["name"]
