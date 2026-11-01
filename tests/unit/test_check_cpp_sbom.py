# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/check_cpp_sbom.py.

Each test builds a throwaway cpp/ tree (CMakeLists.txt, third_party/, vcpkg.json,
SBOM.spdx.json) under tmp_path so the check runs against known inputs.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_cpp_sbom  # noqa: E402

JSON_URL = "https://github.com/nlohmann/json/archive/refs/tags/v3.11.3.tar.gz"
JSON_SHA = "0d8ef5af7f9794e3263480193c491549b2ba6cc74bb018906202ada498a79406"
YAML_URL = "https://github.com/jbeder/yaml-cpp/archive/refs/tags/0.8.0.tar.gz"
YAML_SHA = "fbe74bbdcee21d656715688706da3c8becfd946d92cd44705cc6098bb23b3a16"
SQLITE_URL = "https://sqlite.org/2026/sqlite-amalgamation-3530400.zip"
SQLITE_SHA = "1e71ddf93849c6a6ecf58b827c0692073d2dd7ee40196158068f7b29f422e87d"

CMAKE = f"""
project(gaia-cpp VERSION 0.1.0 LANGUAGES C CXX)
FetchContent_Declare(
    json
    URL      {JSON_URL}
    URL_HASH SHA256={JSON_SHA}
)
# FetchContent_Declare(commented URL https://example.com/x.tar.gz)
FetchContent_Declare(
    yaml-cpp
    URL      {YAML_URL}
    URL_HASH SHA256={YAML_SHA}
)
"""


def _pkg(name, version, url, sha=None):
    pkg = {
        "name": name,
        "SPDXID": f"SPDXRef-Package-{name}",
        "versionInfo": version,
        "downloadLocation": url,
        "licenseDeclared": "MIT",
    }
    if sha:
        pkg["checksums"] = [{"algorithm": "SHA256", "checksumValue": sha}]
    return pkg


def _sbom(packages):
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "gaia-cpp-framework",
        "documentNamespace": "https://example.invalid/sbom",
        "creationInfo": {"creators": ["Organization: Test"]},
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-Package-gaia-cpp",
            }
        ],
    }


def _good_packages():
    return [
        _pkg("gaia-cpp", "0.1.0", "git+https://github.com/amd/gaia.git#cpp"),
        _pkg("nlohmann-json", "3.11.3", JSON_URL, JSON_SHA),
        _pkg("yaml-cpp", "0.8.0", YAML_URL, YAML_SHA),
        _pkg("sqlite", "3.53.4", SQLITE_URL, SQLITE_SHA),
        _pkg("openssl", "NOASSERTION", "NOASSERTION"),
    ]


@pytest.fixture
def cpp_tree(tmp_path):
    cpp = tmp_path / "cpp"
    (cpp / "third_party" / "sqlite").mkdir(parents=True)
    (cpp / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    (cpp / "third_party" / "sqlite" / "SQLITE_VERSION.txt").write_text(
        f"SQLITE_VERSION=3.53.4\nUPSTREAM_URL={SQLITE_URL}\n"
        f"UPSTREAM_SHA256={SQLITE_SHA}\nLICENSE=public-domain\n",
        encoding="utf-8",
    )
    (cpp / "vcpkg.json").write_text(
        json.dumps({"name": "gaia-cpp", "dependencies": ["openssl"]}), encoding="utf-8"
    )
    return cpp


def _write_sbom(cpp, doc):
    path = cpp / "SBOM.spdx.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _errors(cpp, doc):
    return check_cpp_sbom.check(cpp, _write_sbom(cpp, doc))


def test_matching_sbom_passes(cpp_tree, capsys):
    _write_sbom(cpp_tree, _sbom(_good_packages()))
    assert check_cpp_sbom.run_check(cpp_tree) == 0
    assert "[OK]" in capsys.readouterr().out


def test_new_fetchcontent_without_sbom_entry_fails(cpp_tree):
    cmake = cpp_tree / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8")
        + "FetchContent_Declare(\n  fmt\n  URL https://github.com/fmtlib/fmt/archive/"
        "refs/tags/10.2.1.tar.gz\n  URL_HASH SHA256=" + "a" * 64 + "\n)\n",
        encoding="utf-8",
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("fmtlib/fmt" in e and "no SBOM package" in e for e in errors), errors


def test_version_bump_without_sbom_update_fails(cpp_tree):
    cmake = cpp_tree / "CMakeLists.txt"
    new_url = JSON_URL.replace("3.11.3", "3.12.0")
    cmake.write_text(
        cmake.read_text(encoding="utf-8").replace(JSON_URL, new_url), encoding="utf-8"
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("v3.12.0" in e for e in errors), errors
    # The old entry is now stale, too.
    assert any("'nlohmann-json' matches no dependency" in e for e in errors), errors


def test_checksum_mismatch_fails(cpp_tree):
    packages = _good_packages()
    packages[1]["checksums"][0]["checksumValue"] = "b" * 64
    errors = _errors(cpp_tree, _sbom(packages))
    assert any("nlohmann-json: SBOM SHA256" in e for e in errors), errors


def test_checksum_compare_is_case_insensitive(cpp_tree):
    packages = _good_packages()
    packages[1]["checksums"][0]["checksumValue"] = JSON_SHA.upper()
    assert _errors(cpp_tree, _sbom(packages)) == []


def test_missing_checksum_fails(cpp_tree):
    packages = _good_packages()
    del packages[2]["checksums"]
    errors = _errors(cpp_tree, _sbom(packages))
    assert any("yaml-cpp: SBOM SHA256 None" in e for e in errors), errors


def test_version_mismatch_fails(cpp_tree):
    packages = _good_packages()
    packages[2]["versionInfo"] = "0.7.0"
    errors = _errors(cpp_tree, _sbom(packages))
    assert any("yaml-cpp: SBOM versionInfo '0.7.0'" in e for e in errors), errors


def test_stale_package_fails(cpp_tree):
    packages = _good_packages() + [
        _pkg("ghost", "1.0", "https://example.com/ghost.tar.gz", "c" * 64)
    ]
    errors = _errors(cpp_tree, _sbom(packages))
    assert any("'ghost' matches no dependency" in e for e in errors), errors


def test_vendored_upgrade_without_sbom_update_fails(cpp_tree):
    manifest = cpp_tree / "third_party" / "sqlite" / "SQLITE_VERSION.txt"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("3.53.4", "3.54.0"),
        encoding="utf-8",
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("sqlite: SBOM versionInfo '3.53.4'" in e for e in errors), errors


def test_new_vendored_tree_without_manifest_fails(cpp_tree):
    (cpp_tree / "third_party" / "zlib").mkdir()
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("zlib" in e and "*_VERSION.txt" in e for e in errors), errors


def test_vendored_manifest_missing_checksum_fails(cpp_tree):
    manifest = cpp_tree / "third_party" / "sqlite" / "SQLITE_VERSION.txt"
    manifest.write_text("SQLITE_VERSION=3.53.4\nUPSTREAM_URL=x\n", encoding="utf-8")
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("missing UPSTREAM_SHA256" in e for e in errors), errors


def test_new_vcpkg_dependency_fails(cpp_tree):
    (cpp_tree / "vcpkg.json").write_text(
        json.dumps({"dependencies": ["openssl", {"name": "zlib"}]}), encoding="utf-8"
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("'zlib' has no SBOM package" in e for e in errors), errors


def test_git_fetchcontent_is_rejected(cpp_tree):
    cmake = cpp_tree / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8")
        + "FetchContent_Declare(\n  gt\n  GIT_REPOSITORY https://x/y.git\n  GIT_TAG main\n)\n",
        encoding="utf-8",
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("FetchContent_Declare(gt) fetches from git" in e for e in errors), errors


def test_fetchcontent_without_hash_is_rejected(cpp_tree):
    cmake = cpp_tree / "CMakeLists.txt"
    cmake.write_text(
        cmake.read_text(encoding="utf-8")
        + "FetchContent_Declare(\n  nohash\n  URL https://x/y/refs/tags/v1.tar.gz\n)\n",
        encoding="utf-8",
    )
    errors = _errors(cpp_tree, _sbom(_good_packages()))
    assert any("nohash" in e and "URL_HASH" in e for e in errors), errors


def test_root_version_must_match_project(cpp_tree):
    packages = _good_packages()
    packages[0]["versionInfo"] = "0.0.9"
    errors = _errors(cpp_tree, _sbom(packages))
    assert any("project VERSION '0.1.0'" in e for e in errors), errors


def test_wrong_spdx_version_and_dangling_relationship_fail(cpp_tree):
    doc = _sbom(_good_packages())
    doc["spdxVersion"] = "SPDX-2.2"
    doc["relationships"].append(
        {
            "spdxElementId": "SPDXRef-Package-gaia-cpp",
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": "SPDXRef-Package-missing",
        }
    )
    errors = _errors(cpp_tree, doc)
    assert any("SPDX-2.3" in e for e in errors), errors
    assert any("SPDXRef-Package-missing" in e for e in errors), errors


def test_missing_and_invalid_sbom_fail(cpp_tree, capsys):
    assert check_cpp_sbom.run_check(cpp_tree) == 1
    assert "does not exist" in capsys.readouterr().err
    (cpp_tree / "SBOM.spdx.json").write_text("{not json", encoding="utf-8")
    assert check_cpp_sbom.run_check(cpp_tree) == 1
    assert "not valid JSON" in capsys.readouterr().err
