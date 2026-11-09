# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for cpp/packaging/package_lemonade.py: the archive and its pin file."""

import hashlib
import importlib.util
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "cpp" / "packaging" / "package_lemonade.py"


@pytest.fixture(scope="module")
def pkg():
    spec = importlib.util.spec_from_file_location("package_lemonade", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_tree(tmp_path: Path, exe: str) -> Path:
    build = tmp_path / "build"
    build.mkdir()
    (build / exe).write_bytes(b"\x7fELF fake gaiacpp")
    return build


def _pins(version: str, assets: dict) -> dict:
    return {
        "gaiacpp": {"version": version},
        "checksums": {"github": {"amd/gaia": {version: assets}}},
    }


def test_asset_name_uses_zip_only_on_windows(pkg):
    assert pkg.asset_name("linux", "x64") == "gaiacpp-linux-x64.tar.gz"
    assert pkg.asset_name("darwin", "arm64") == "gaiacpp-darwin-arm64.tar.gz"
    assert pkg.asset_name("windows", "x64") == "gaiacpp-windows-x64.zip"


def test_linux_archive_has_executable_at_root_and_pinned_digest(pkg, tmp_path):
    build = _build_tree(tmp_path, "gaiacpp")
    out = tmp_path / "out"

    rc = pkg.main(
        [
            "--build-dir",
            str(build),
            "--os",
            "linux",
            "--arch",
            "x64",
            "--version",
            "v1.2.3",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    archive = out / "gaiacpp-linux-x64.tar.gz"
    with tarfile.open(archive) as t:
        assert sorted(t.getnames()) == [
            "LICENSE.md",
            "THIRD_PARTY_NOTICES.md",
            "gaiacpp",
        ]
        assert t.getmember("gaiacpp").mode == 0o755
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    pins = json.loads((out / "agent_versions.gaia.json").read_text("utf-8"))
    assert pins == _pins("v1.2.3", {"gaiacpp-linux-x64.tar.gz": digest})


def test_windows_archive_is_a_zip_with_the_exe(pkg, tmp_path):
    build = _build_tree(tmp_path, "gaiacpp.exe")
    out = tmp_path / "out"

    pkg.main(
        [
            "--build-dir",
            str(build),
            "--os",
            "windows",
            "--arch",
            "x64",
            "--version",
            "v1.2.3",
            "--out",
            str(out),
        ]
    )

    with zipfile.ZipFile(out / "gaiacpp-windows-x64.zip") as z:
        assert sorted(z.namelist()) == [
            "LICENSE.md",
            "THIRD_PARTY_NOTICES.md",
            "gaiacpp.exe",
        ]


def test_missing_binary_is_an_error_naming_the_build_command(pkg, tmp_path):
    empty = tmp_path / "build"
    empty.mkdir()

    with pytest.raises(SystemExit) as err:
        pkg.main(
            [
                "--build-dir",
                str(empty),
                "--os",
                "linux",
                "--arch",
                "x64",
                "--version",
                "v1.2.3",
                "--out",
                str(tmp_path / "out"),
            ]
        )

    assert "--target gaiacpp" in str(err.value)


def test_packaging_without_its_flags_is_a_usage_error(pkg, tmp_path, capsys):
    with pytest.raises(SystemExit) as err:
        pkg.main(["--out", str(tmp_path)])

    assert err.value.code == 2
    assert "--build-dir" in capsys.readouterr().err


def test_merge_keeps_every_platform(pkg):
    merged = pkg.merge_pins(
        [
            _pins("v1.2.3", {"gaiacpp-linux-x64.tar.gz": "sha256:aa"}),
            _pins("v1.2.3", {"gaiacpp-windows-x64.zip": "sha256:bb"}),
            _pins("v1.2.3", {"gaiacpp-darwin-arm64.tar.gz": "sha256:cc"}),
        ]
    )

    assert merged == _pins(
        "v1.2.3",
        {
            "gaiacpp-linux-x64.tar.gz": "sha256:aa",
            "gaiacpp-windows-x64.zip": "sha256:bb",
            "gaiacpp-darwin-arm64.tar.gz": "sha256:cc",
        },
    )


def test_merge_refuses_two_versions(pkg):
    with pytest.raises(ValueError, match="two versions: v1.2.3 and v1.2.4"):
        pkg.merge_pins(
            [
                _pins("v1.2.3", {"gaiacpp-linux-x64.tar.gz": "sha256:aa"}),
                _pins("v1.2.4", {"gaiacpp-windows-x64.zip": "sha256:bb"}),
            ]
        )


def test_merge_refuses_two_checksums_for_one_asset(pkg):
    with pytest.raises(ValueError, match="gaiacpp-linux-x64.tar.gz has two checksums"):
        pkg.merge_pins(
            [
                _pins("v1.2.3", {"gaiacpp-linux-x64.tar.gz": "sha256:aa"}),
                _pins("v1.2.3", {"gaiacpp-linux-x64.tar.gz": "sha256:bb"}),
            ]
        )


def test_merge_refuses_nothing_and_a_file_without_a_version(pkg):
    with pytest.raises(ValueError, match="no pin files"):
        pkg.merge_pins([])
    with pytest.raises(ValueError, match="no gaiacpp.version"):
        pkg.merge_pins([{"checksums": {}}])


def test_merge_mode_writes_one_pin_file(pkg, tmp_path):
    sources = []
    for name, asset in (
        ("linux", "gaiacpp-linux-x64.tar.gz"),
        ("windows", "gaiacpp-windows-x64.zip"),
    ):
        leg = tmp_path / name
        leg.mkdir()
        path = leg / "agent_versions.gaia.json"
        path.write_text(json.dumps(_pins("v1.2.3", {asset: f"sha256:{name}"})), "utf-8")
        sources.append(str(path))
    out = tmp_path / "bundle" / "lemonade"

    rc = pkg.main(["--out", str(out), "--merge", *sources])

    assert rc == 0
    merged = json.loads((out / "agent_versions.gaia.json").read_text("utf-8"))
    assert merged == _pins(
        "v1.2.3",
        {
            "gaiacpp-linux-x64.tar.gz": "sha256:linux",
            "gaiacpp-windows-x64.zip": "sha256:windows",
        },
    )


def test_merge_mode_names_a_missing_pin_file(pkg, tmp_path):
    missing = tmp_path / "nope" / "agent_versions.gaia.json"

    with pytest.raises(SystemExit) as err:
        pkg.main(["--out", str(tmp_path / "out"), "--merge", str(missing)])

    assert str(missing) in str(err.value)
