# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The frozen agent must not bundle GPL/AGPL code.

Three things kept it out: ``freeze.py`` excludes PyInstaller's own package
(numpy's bundled PyInstaller hook pulled it in), GNU Readline and GNU dbm
(reached by offering scripts the whole stdlib); PyMuPDF is no longer a script
library; and ``check_bundle_licenses.py`` reads the built archive and fails the
build if any of it is there anyway.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
WORKFLOW = (
    Path(__file__).resolve().parents[5]
    / ".github"
    / "workflows"
    / "build_flagship_sidecar.yml"
)


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


freeze = _load("gaia_freeze_licenses", "freeze.py")
check = _load("gaia_check_bundle_licenses", "check_bundle_licenses.py")

GPL_MODULES = [
    "PyInstaller",
    "altgraph",
    "pefile",
    "macholib",
    "readline",
    "_gdbm",
    "_dbm",
    "dbm.gnu",
    "dbm.ndbm",
]


# ---------------------------------------------------------------------------
# freeze.py: what is kept out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", GPL_MODULES)
def test_freeze_excludes_gpl_module(module):
    assert module in freeze.EXCLUDES
    assert freeze.is_excluded(module)


@pytest.mark.parametrize(
    "module",
    ["PyInstaller.utils.hooks", "PyInstaller.lib.modulegraph", "altgraph.Graph"],
)
def test_submodules_of_an_excluded_package_are_excluded(module):
    assert freeze.is_excluded(module)


@pytest.mark.parametrize(
    "module", ["dbm", "dbm.dumb", "shelve", "pdb", "cmd", "numpy", "PyInstallerX"]
)
def test_exclusion_is_by_module_not_by_prefix(module):
    """dbm and shelve stay usable for scripts, on dbm.dumb."""
    assert not freeze.is_excluded(module)


@pytest.mark.parametrize(
    "name",
    [
        "numpy._pyinstaller",
        "numpy._pyinstaller.hook-numpy",
        "somepkg.__pyinstaller.hook-somepkg",
    ],
)
def test_a_packages_own_pyinstaller_hook_is_not_collected(name):
    """numpy._pyinstaller.hook-numpy imports PyInstaller.utils.hooks."""
    assert freeze._wanted_submodule(name) is False


def test_excluded_modules_never_become_script_hidden_imports():
    pytest.importorskip("PyInstaller")
    hidden = freeze.script_hidden_imports()
    leaked = [m for m in hidden if freeze.is_excluded(m) or "_pyinstaller" in m]
    assert not leaked


def test_the_build_drops_hidden_imports_of_excluded_modules(tmp_path, monkeypatch):
    import sys
    import types

    captured: list[str] = []
    pyinstaller = types.ModuleType("PyInstaller")
    pyinstaller_main = types.ModuleType("PyInstaller.__main__")
    pyinstaller_main.run = captured.extend
    pyinstaller.__main__ = pyinstaller_main
    monkeypatch.setitem(sys.modules, "PyInstaller", pyinstaller)
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__", pyinstaller_main)
    monkeypatch.setattr(freeze, "HERE", tmp_path)
    monkeypatch.setattr(freeze, "_verify_collect_targets", lambda: None)
    monkeypatch.setattr(
        freeze, "script_hidden_imports", lambda: ["readline", "dbm.gnu", "csv"]
    )
    monkeypatch.setattr(freeze, "skill_code_hidden_imports", lambda: [])

    def fake_stage(dest):
        dest.mkdir(parents=True)
        return dest

    monkeypatch.setattr(freeze.tiktoken_bundle, "stage_cache", fake_stage)

    freeze.build(onefile=True)

    hidden = [captured[i + 1] for i, a in enumerate(captured) if a == "--hidden-import"]
    excluded = [
        captured[i + 1] for i, a in enumerate(captured) if a == "--exclude-module"
    ]
    assert "csv" in hidden
    assert "readline" not in hidden and "dbm.gnu" not in hidden
    assert set(GPL_MODULES) <= set(excluded)


def test_pymupdf_is_not_a_script_library():
    for name in ("pymupdf", "fitz"):
        assert name not in freeze.SCRIPT_LIBRARIES
        assert name not in freeze.COLLECT_DATA
        assert name not in freeze.COLLECT_ALL
    assert "pypdfium2" in freeze.SCRIPT_LIBRARIES


# ---------------------------------------------------------------------------
# check_bundle_licenses.py: what fails the build
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "PyInstaller",
        "PyInstaller.utils.hooks",
        "PyInstaller/loader/pyimod01_archive.py",
        "libreadline.so.8",
        "lib-dynload/readline.cpython-312-x86_64-linux-gnu.so",
        "readline",
        "libgdbm.so.6",
        "libgdbm_compat.so.4",
        "lib-dynload/_gdbm.cpython-312-x86_64-linux-gnu.so",
        "lib-dynload\\_dbm.cpython-312-darwin.so",
        "_gdbm",
        "dbm.gnu",
        "dbm.ndbm",
        "fitz",
        "fitz.utils",
        "pymupdf",
        "pymupdf\\mupdfcpp64.dll",
        "PyMuPDF/_mupdf.pyd",
        "libmupdf.so.26",
    ],
)
def test_forbidden_content_is_flagged(name):
    assert check.forbidden_entries([name]), name


@pytest.mark.parametrize(
    "name",
    [
        "pypdfium2_raw\\pdfium.dll",
        "pypdfium2",
        "dbm",
        "dbm.dumb",
        "shelve",
        "pdb",
        "rlcompleter",
        "_pyinstaller_hooks_contrib",
        "pyi_rth_inspect",
        "numpy",
        "docs/fitzgerald.txt",
        "pyreadline3",
        "base_library.zip",
    ],
)
def test_permitted_content_is_not_flagged(name):
    assert check.forbidden_entries([name]) == []


def test_each_finding_names_what_it_is():
    found = dict(check.forbidden_entries(["PyInstaller.compat", "libgdbm.so.6"]))
    assert "GPL-2.0" in found["PyInstaller.compat"]
    assert "GPL-3.0" in found["libgdbm.so.6"]


def test_a_file_that_is_not_a_frozen_binary_is_refused(tmp_path, capsys):
    pytest.importorskip("PyInstaller")
    fake = tmp_path / "gaia-agent.exe"
    fake.write_bytes(b"MZ" + b"\x00" * 4096)

    assert check.main([str(fake)]) == 2
    assert "not a PyInstaller binary" in capsys.readouterr().err


def test_a_flagged_binary_fails_and_lists_the_offenders(monkeypatch, capsys):
    monkeypatch.setattr(
        check, "bundle_entry_names", lambda _: ["csv", "PyInstaller.compat", "fitz"]
    )
    assert check.main(["gaia-agent"]) == 1
    err = capsys.readouterr().err
    assert "PyInstaller.compat" in err and "fitz" in err and "csv" not in err


def test_a_clean_binary_passes(monkeypatch, capsys):
    names = ["csv", "pypdfium2", *check.REQUIRED_NOTICES]
    monkeypatch.setattr(check, "bundle_entry_names", lambda _: names)
    assert check.main(["gaia-agent"]) == 0
    assert "PASS" in capsys.readouterr().out


def test_a_binary_without_notices_fails(monkeypatch, capsys):
    monkeypatch.setattr(check, "bundle_entry_names", lambda _: ["csv"])
    assert check.main(["gaia-agent"]) == 1
    assert "THIRD_PARTY_NOTICES.md" in capsys.readouterr().err


def test_the_release_build_runs_the_check_on_the_frozen_binary():
    text = WORKFLOW.read_text(encoding="utf-8")
    freeze_step = text.index('"${PACKAGING}/freeze.py" --onefile')
    check_step = text.index('"${PACKAGING}/check_bundle_licenses.py"')
    upload_step = text.index("- name: Upload platform artifact")
    assert freeze_step < check_step < upload_step


# --- reviewed LGPL runtime libraries: reported, never silently accepted ---------


@pytest.mark.parametrize(
    "name",
    [
        "libquadmath-2284e583-a9307bba.so.0.0.0",
        "numpy.libs/libquadmath-2284e583.so.0.0.0",
        "libasound-fb5348bf.so.2.0.0",
        "libkeyutils.so.1",
    ],
)
def test_reviewed_lgpl_libraries_are_reported(name):
    found = check.reviewed_lgpl_entries([name])
    assert [n for n, _ in found] == [name]
    assert "LGPL" in found[0][1]


def test_reviewed_lgpl_list_never_shadows_a_forbidden_library():
    names = ["libreadline.so.8", "libgdbm.so.6", "libquadmath.so.0"]
    assert [n for n, _ in check.forbidden_entries(names)] == [
        "libgdbm.so.6",
        "libreadline.so.8",
    ]
    assert [n for n, _ in check.reviewed_lgpl_entries(names)] == ["libquadmath.so.0"]


def test_permissive_libraries_are_neither_forbidden_nor_reported():
    names = [
        "libssl.so.3",
        "libz.so.1",
        "libgcc_s.so.1",
        "libstdc++.so.6",
        "libpython3.12.so.1.0",
    ]
    assert check.forbidden_entries(names) == []
    assert check.reviewed_lgpl_entries(names) == []


def test_a_binary_without_licence_notices_is_flagged():
    assert check.missing_notices(["numpy", "gaia_agent/gaia-agent.yaml"]) == list(
        check.REQUIRED_NOTICES
    )


def test_notices_are_found_under_either_path_separator():
    names = [
        "THIRD_PARTY_NOTICES.md",
        "licenses\\LGPL-2.1.txt",
        "licenses/GCC-exception-3.1.txt",
    ]
    assert check.missing_notices(names) == []


def test_the_freeze_bundles_every_required_notice():
    root = PACKAGING.parents[4]
    for source, _ in freeze.NOTICES:
        assert source.exists(), source
    for notice in check.REQUIRED_NOTICES:
        assert (root / notice).is_file(), notice
