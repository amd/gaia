# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Fail when cpp/SBOM.spdx.json drifts from the dependencies the C++ build pins.

Every source of third-party code in the C++ framework must have a matching
SPDX package, and the SBOM must not list anything the build no longer uses:

- each ``FetchContent_Declare`` in a cpp/ CMakeLists.txt or *.cmake module
  (examples and build trees excluded), matched by its URL, with the same SHA256
  and the version taken from the tag in that URL;
- each vendored tree under cpp/third_party/<dir>/, described by its
  ``*_VERSION.txt`` (``UPSTREAM_URL``, ``UPSTREAM_SHA256``, ``<X>_VERSION``);
- each dependency named in cpp/vcpkg.json, matched by package name;
- the root package's ``versionInfo`` equals ``project(gaia-cpp VERSION ...)``.

Run from anywhere: ``python util/check_cpp_sbom.py``. Exit 0 when in sync.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
CPP_DIR = REPO_ROOT / "cpp"
ROOT_PACKAGE = "gaia-cpp"

# CMake command names are case-insensitive; keyword arguments are not.
_DECLARE_RE = re.compile(r"FetchContent_Declare\s*\((.*?)\)", re.DOTALL | re.IGNORECASE)
_BRACKET_COMMENT_RE = re.compile(r"#\[(=*)\[.*?\]\1\]", re.DOTALL)
_SHA256_RE = re.compile(r"SHA256=([0-9a-fA-F]{64})")
_PROJECT_RE = re.compile(
    r"^\s*project\s*\(\s*gaia-cpp\s+VERSION\s+([0-9][0-9.]*)", re.MULTILINE
)
_TAG_RE = re.compile(r"/refs/tags/v?([^/]+?)\.(?:tar\.gz|zip)$")
# These CMake commands can fetch third-party code without producing a pin the
# checker can compare with the SBOM. FetchContent_Populate(name) without direct
# arguments remains allowed because it consumes a prior FetchContent_Declare.
_UNTRACKED_DEPENDENCY_RE = re.compile(
    r"""
    (?P<external>\bExternalProject_Add\s*\()
    |(?P<populate>\bFetchContent_Populate\s*\(\s*[^()\s]+\s+(?=[^)]*\S))
    |(?P<cpm>\bCPMAddPackage\s*\()
    |(?P<download>\bfile\s*\(\s*DOWNLOAD\b)
    """,
    re.DOTALL | re.IGNORECASE | re.VERBOSE,
)
_UNTRACKED_COMMAND_NAMES = {
    "external": "ExternalProject_Add",
    "populate": "FetchContent_Populate with direct arguments",
    "cpm": "CPMAddPackage",
    "download": "file(DOWNLOAD)",
}


@dataclass(frozen=True)
class Dependency:
    origin: str  # where the pin lives, for error messages
    url: str
    sha256: str
    version: str


def _strip_comments(cmake: str) -> str:
    cmake = _BRACKET_COMMENT_RE.sub("", cmake)
    return "\n".join(line.split("#", 1)[0] for line in cmake.splitlines())


def _value_after(args: List[str], keyword: str) -> Optional[str]:
    if keyword in args:
        i = args.index(keyword)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def fetchcontent_dependencies(cmake_text: str, origin: str) -> List[Dependency]:
    """Every FetchContent_Declare in a CMakeLists, as (url, sha256, version)."""
    deps: List[Dependency] = []
    errors: List[str] = []
    cmake_text = _strip_comments(cmake_text)
    for match in _UNTRACKED_DEPENDENCY_RE.finditer(cmake_text):
        name = _UNTRACKED_COMMAND_NAMES[match.lastgroup or "external"]
        errors.append(
            f"{origin} uses {name}; declare an archive with "
            "FetchContent_Declare(URL + URL_HASH SHA256=...) so the SBOM can track it"
        )
    for block in _DECLARE_RE.findall(cmake_text):
        args = [a.strip('"') for a in block.split()]
        name = args[0] if args else "<unnamed>"
        where = f"{origin} FetchContent_Declare({name})"
        if "GIT_REPOSITORY" in args:
            errors.append(
                f"{where} fetches from git; pin a release archive with URL + "
                "URL_HASH SHA256 so the SBOM can record a checksum"
            )
            continue
        url = _value_after(args, "URL")
        sha = _SHA256_RE.fullmatch(_value_after(args, "URL_HASH") or "")
        if not url or not sha:
            errors.append(f"{where} needs both URL and URL_HASH SHA256=<hex>")
            continue
        tag = _TAG_RE.search(url)
        if not tag:
            errors.append(
                f"{where} URL {url} is not a .../refs/tags/<version> "
                "archive, so its version cannot be checked against the SBOM"
            )
            continue
        deps.append(Dependency(where, url, sha.group(1).lower(), tag.group(1)))
    if errors:
        raise ValueError("\n".join(errors))
    return deps


# Consumer examples pin gaia itself, and build trees hold fetched sources.
_SKIP_DIRS = {"examples", "third_party", "_deps"}


def cmake_sources(cpp_dir: Path) -> List[Path]:
    """Every CMakeLists.txt and *.cmake module the C++ build can include."""
    found = []
    for path in sorted(cpp_dir.rglob("*")):
        rel = path.relative_to(cpp_dir).parts
        if any(p in _SKIP_DIRS or p == "build" or p.startswith("build-") for p in rel[:-1]):
            continue
        if path.name == "CMakeLists.txt" or path.suffix == ".cmake" or path.name.endswith(".cmake.in"):
            found.append(path)
    return found


def vendored_dependencies(third_party: Path) -> List[Dependency]:
    """Each cpp/third_party/<dir>/ described by exactly one *_VERSION.txt."""
    deps: List[Dependency] = []
    if not third_party.is_dir():
        return deps
    for sub in sorted(p for p in third_party.iterdir() if p.is_dir()):
        manifests = sorted(sub.glob("*_VERSION.txt"))
        if len(manifests) != 1:
            raise ValueError(
                f"cpp/third_party/{sub.name}: expected exactly one *_VERSION.txt "
                f"recording UPSTREAM_URL, "
                f"UPSTREAM_SHA256 and the version, found {len(manifests)}"
            )
        fields: Dict[str, str] = {}
        for line in manifests[0].read_text(encoding="utf-8-sig").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip()
        prefix = manifests[0].name[: -len("_VERSION.txt")]
        missing = [
            k
            for k in ("UPSTREAM_URL", "UPSTREAM_SHA256", f"{prefix}_VERSION")
            if not fields.get(k)
        ]
        if missing:
            raise ValueError(f"{manifests[0]}: missing {', '.join(missing)}")
        deps.append(
            Dependency(
                f"vendored {sub.name}",
                fields["UPSTREAM_URL"],
                fields["UPSTREAM_SHA256"].lower(),
                fields[f"{prefix}_VERSION"],
            )
        )
    return deps


def vcpkg_dependencies(vcpkg_json: Path) -> List[str]:
    if not vcpkg_json.exists():
        return []
    data = json.loads(vcpkg_json.read_text(encoding="utf-8-sig"))
    names = []
    for dep in data.get("dependencies", []):
        names.append(dep if isinstance(dep, str) else dep["name"])
    return names


def _sha256_of(pkg: dict) -> Optional[str]:
    for c in pkg.get("checksums", []):
        if c.get("algorithm") == "SHA256":
            return str(c.get("checksumValue", "")).lower()
    return None


def check(cpp_dir: Path, sbom_path: Path) -> List[str]:
    """Return a list of drift errors (empty when the SBOM matches the build)."""
    errors: List[str] = []
    try:
        sbom = json.loads(sbom_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return [f"{sbom_path} does not exist"]
    except json.JSONDecodeError as exc:
        return [f"{sbom_path} is not valid JSON: {exc}"]

    if sbom.get("spdxVersion") != "SPDX-2.3":
        errors.append(f"spdxVersion must be SPDX-2.3, got {sbom.get('spdxVersion')!r}")
    for field in ("dataLicense", "SPDXID", "name", "documentNamespace"):
        if not sbom.get(field):
            errors.append(f"document is missing required field {field!r}")
    if not sbom.get("creationInfo", {}).get("creators"):
        errors.append("creationInfo.creators is required")

    packages = sbom.get("packages", [])
    ids = [p.get("SPDXID") for p in packages]
    if len(ids) != len(set(ids)):
        errors.append("duplicate SPDXID in packages")
    for pkg in packages:
        for field in ("name", "SPDXID", "downloadLocation", "licenseDeclared"):
            if not pkg.get(field):
                errors.append(f"package {pkg.get('name')!r} is missing {field!r}")
    known_ids = set(ids) | {"SPDXRef-DOCUMENT"}
    for rel in sbom.get("relationships", []):
        for end in ("spdxElementId", "relatedSpdxElement"):
            if rel.get(end) not in known_ids:
                errors.append(f"relationship references unknown {end} {rel.get(end)!r}")

    by_url = {p.get("downloadLocation"): p for p in packages}
    by_name = {p.get("name"): p for p in packages}
    accounted = set()

    cmake_path = cpp_dir / "CMakeLists.txt"
    cmake_text = cmake_path.read_text(encoding="utf-8-sig")
    root = by_name.get(ROOT_PACKAGE)
    project = _PROJECT_RE.search(cmake_text)
    if root is None:
        errors.append(f"no root package named {ROOT_PACKAGE!r}")
    else:
        accounted.add(ROOT_PACKAGE)
        if not project:
            errors.append(f"{cmake_path}: could not read project(gaia-cpp VERSION ...)")
        elif root.get("versionInfo") != project.group(1):
            errors.append(
                f"{ROOT_PACKAGE} versionInfo {root.get('versionInfo')!r} != "
                f"project VERSION {project.group(1)!r}"
            )

    try:
        pinned = []
        for source in cmake_sources(cpp_dir):
            pinned += fetchcontent_dependencies(
                source.read_text(encoding="utf-8-sig"),
                f"cpp/{source.relative_to(cpp_dir).as_posix()}",
            )
        pinned += vendored_dependencies(cpp_dir / "third_party")
    except ValueError as exc:
        errors.append(str(exc))
        pinned = []

    for dep in pinned:
        pkg = by_url.get(dep.url)
        if pkg is None:
            errors.append(
                f"{dep.origin}: {dep.url} has no SBOM package "
                f"(add one with downloadLocation set to that URL)"
            )
            continue
        accounted.add(pkg.get("name"))
        if _sha256_of(pkg) != dep.sha256:
            errors.append(
                f"{pkg.get('name')}: SBOM SHA256 {_sha256_of(pkg)} != pinned {dep.sha256}"
            )
        if pkg.get("versionInfo") != dep.version:
            errors.append(
                f"{pkg.get('name')}: SBOM versionInfo {pkg.get('versionInfo')!r} "
                f"!= pinned {dep.version!r}"
            )

    for name in vcpkg_dependencies(cpp_dir / "vcpkg.json"):
        if name not in by_name:
            errors.append(f"cpp/vcpkg.json dependency {name!r} has no SBOM package")
        accounted.add(name)

    for pkg in packages:
        if pkg.get("name") not in accounted:
            errors.append(
                f"SBOM package {pkg.get('name')!r} matches no dependency the build "
                "pins; remove it or fix its downloadLocation"
            )
    return errors


def run_check(cpp_dir: Path = CPP_DIR, sbom_path: Optional[Path] = None) -> int:
    sbom_path = sbom_path or cpp_dir / "SBOM.spdx.json"
    errors = check(cpp_dir, sbom_path)
    if errors:
        print(f"[!] {sbom_path} is out of sync with the C++ build:", file=sys.stderr)
        for err in errors:
            print(f"    - {err}", file=sys.stderr)
        print(
            "    Update cpp/SBOM.spdx.json alongside the dependency change.",
            file=sys.stderr,
        )
        return 1
    print(f"[OK] {sbom_path.name} matches every pinned C++ dependency")
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
