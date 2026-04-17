#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Check documentation for hardcoded version strings that should match
the canonical versions defined in src/gaia/version.py.

Currently checks:
  - LEMONADE_VERSION: Lemonade Server version references in docs and README files

Usage:
    python util/check_doc_versions.py            # Check all docs
    python util/check_doc_versions.py --verbose  # Show skipped files and scan counts

Exit codes:
    0 - All version references are consistent
    1 - Mismatched version references found
"""

import argparse
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Project root (relative to this script's location in util/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Source of truth
VERSION_FILE = PROJECT_ROOT / "src" / "gaia" / "version.py"

# Directories and file patterns to scan
SCAN_PATHS = [
    (PROJECT_ROOT / "docs", "**/*.mdx"),
    (PROJECT_ROOT / "docs", "**/*.md"),
    (PROJECT_ROOT / "cpp", "**/*.md"),
    (PROJECT_ROOT / "cpp", "**/*.mdx"),
]

# Files to exclude from scanning (relative to PROJECT_ROOT)
EXCLUDE_PATTERNS = [
    "docs/releases/*",  # Release notes are historical, versions are intentional
    "docs/plans/*",  # Planning docs may reference old versions
]


@dataclass
class VersionMismatch:
    """A single version mismatch found in a file."""

    file: Path
    line_num: int
    line_text: str
    found_version: str
    expected_version: str
    pattern_desc: str


@dataclass
class CheckConfig:
    """Configuration for a version check."""

    name: str
    expected_version: str
    # Regex patterns that capture the version string
    # Each pattern should have a named group (?P<version>...) for the version
    patterns: list[tuple[str, str]] = field(default_factory=list)


def read_lemonade_version() -> str:
    """Read LEMONADE_VERSION from src/gaia/version.py."""
    if not VERSION_FILE.exists():
        print(f"[ERROR] Version file not found: {VERSION_FILE}")
        sys.exit(1)

    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'LEMONADE_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        print(f"[ERROR] LEMONADE_VERSION not found in {VERSION_FILE}")
        sys.exit(1)

    return match.group(1)


def should_exclude(file_path: Path) -> bool:
    """Check if a file should be excluded from scanning."""
    rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
    for pattern in EXCLUDE_PATTERNS:
        # Simple glob-style matching
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if rel_path.startswith(prefix + "/"):
                return True
        elif rel_path == pattern:
            return True
    return False


def build_lemonade_patterns(version: str) -> list[tuple[str, str]]:
    """
    Build regex patterns to find Lemonade version references.

    Each tuple is (compiled_pattern_str, human_description).
    Patterns use a named group (?P<version>...) to capture the version found.
    """
    # We want to find ANY semver-like version in these contexts,
    # then check if it matches the expected version.
    semver = r"\d+\.\d+\.\d+"

    return [
        # URLs: releases/download/v<VERSION>/
        (
            rf"lemonade(?:-sdk)?/lemonade/releases/download/v(?P<version>{semver})/",
            "Lemonade GitHub release URL",
        ),
        # Filenames: lemonade-server-minimal.msi or lemonade-server_<VERSION>_amd64.deb
        (
            rf"lemonade(?:-server)?_(?P<version>{semver})_amd64\.deb",
            "Lemonade .deb filename",
        ),
        # Release page links: releases/tag/v<VERSION>
        (
            rf"lemonade(?:-sdk)?/lemonade/releases/tag/v(?P<version>{semver})",
            "Lemonade release page link",
        ),
        # Explicit version callouts in text: (v<VERSION>) or v<VERSION>
        # Match patterns like "Lemonade ... v9.3.0" or "(v9.3.0)"
        (
            rf"[Ll]emonade[^|\n]{{0,60}}v(?P<version>{semver})",
            "Lemonade version reference in text",
        ),
        # Table cells: | 9.3.0 | (preceded by Lemonade on same line)
        (
            rf"[Ll]emonade.*?\|\s*(?P<version>{semver})\s*\|",
            "Lemonade version in table",
        ),
    ]


def scan_file(
    file_path: Path,
    patterns: list[tuple[re.Pattern, str]],
    expected_version: str,
) -> list[VersionMismatch]:
    """Scan a single file for version mismatches.

    Deduplicates so each (file, line, version) is reported only once,
    even if multiple patterns match the same version on the same line.
    """
    mismatches = []
    seen: set[tuple[int, str]] = set()  # (line_num, found_version)

    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return mismatches

    for line_num, line in enumerate(content.splitlines(), start=1):
        for compiled_pattern, desc in patterns:
            for match in compiled_pattern.finditer(line):
                found = match.group("version")
                if found != expected_version:
                    key = (line_num, found)
                    if key not in seen:
                        seen.add(key)
                        mismatches.append(
                            VersionMismatch(
                                file=file_path,
                                line_num=line_num,
                                line_text=line.strip(),
                                found_version=found,
                                expected_version=expected_version,
                                pattern_desc=desc,
                            )
                        )

    return mismatches


def scan_all(
    check: CheckConfig, verbose: bool = False
) -> list[VersionMismatch]:
    """Scan all documentation files for version mismatches."""
    # Compile patterns
    compiled = [
        (re.compile(pat), desc) for pat, desc in check.patterns
    ]

    all_mismatches: list[VersionMismatch] = []
    files_scanned = 0

    for base_dir, glob_pattern in SCAN_PATHS:
        if not base_dir.exists():
            continue
        for file_path in sorted(base_dir.glob(glob_pattern)):
            if should_exclude(file_path):
                if verbose:
                    rel = file_path.relative_to(PROJECT_ROOT)
                    print(f"  [SKIP] {rel} (excluded)")
                continue

            files_scanned += 1
            mismatches = scan_file(file_path, compiled, check.expected_version)
            all_mismatches.extend(mismatches)

    if verbose:
        print(f"  Scanned {files_scanned} files")

    return all_mismatches


def run_check(verbose: bool = False) -> int:
    """
    Run all version consistency checks.

    Returns:
        0 if all checks pass, 1 if mismatches found
    """
    print("=" * 60)
    print("  Documentation Version Consistency Check")
    print("=" * 60)

    # Read canonical versions
    lemonade_version = read_lemonade_version()
    print(f"\n[SOURCE] src/gaia/version.py")
    print(f"  LEMONADE_VERSION = {lemonade_version}")

    # Build check config
    checks = [
        CheckConfig(
            name="Lemonade Server",
            expected_version=lemonade_version,
            patterns=build_lemonade_patterns(lemonade_version),
        ),
    ]

    total_mismatches: list[VersionMismatch] = []

    for check in checks:
        print(f"\n[CHECK] {check.name} (expected: {check.expected_version})")
        print("-" * 40)

        mismatches = scan_all(check, verbose=verbose)
        total_mismatches.extend(mismatches)

        if mismatches:
            # Group by file for readability
            by_file: dict[Path, list[VersionMismatch]] = {}
            for m in mismatches:
                by_file.setdefault(m.file, []).append(m)

            for file_path, file_mismatches in by_file.items():
                rel = file_path.relative_to(PROJECT_ROOT)
                print(f"\n  [MISMATCH] {rel}")
                for m in file_mismatches:
                    print(
                        f"    Line {m.line_num}: found v{m.found_version}, "
                        f"expected v{m.expected_version}"
                    )
                    print(f"      ({m.pattern_desc})")
                    # Show truncated line context
                    display_line = m.line_text
                    if len(display_line) > 100:
                        display_line = display_line[:100] + "..."
                    print(f"      > {display_line}")
        else:
            print("  [OK] All references match")

    # Summary
    print("\n" + "=" * 60)
    if total_mismatches:
        unique_files = len({m.file for m in total_mismatches})
        print(
            f"[FAILED] {len(total_mismatches)} version mismatch(es) "
            f"in {unique_files} file(s)"
        )
        print()
        print("[TIP] Update the hardcoded versions to match src/gaia/version.py")
        print(
            "[TIP] If a version is intentionally different (e.g., release notes),\n"
            "      add the file path to EXCLUDE_PATTERNS in this script."
        )
        print("=" * 60)
        return 1
    else:
        print("[OK] All documentation version references are consistent!")
        print("=" * 60)
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Check documentation for version consistency with src/gaia/version.py"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output including skipped files",
    )
    args = parser.parse_args()

    exit_code = run_check(verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
