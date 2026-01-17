#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Validate MDX release notes files."""

import argparse
import io
import re
import sys
from pathlib import Path

# Fix Windows console encoding for emojis
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def validate_mdx(
    file_path: str, tag: str | None = None, release_notes: bool = False
) -> list[str]:
    """
    Validate an MDX file.

    Args:
        file_path: Path to the MDX file
        tag: Optional release tag to validate against (e.g., 'v0.16.0')
        release_notes: If True, validate release notes specific requirements

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    path = Path(file_path)

    if not path.exists():
        return [f"File not found: {file_path}"]

    content = path.read_text(encoding="utf-8")

    # Check for frontmatter
    if not content.startswith("---"):
        errors.append("Missing frontmatter (must start with ---)")
    else:
        # Extract frontmatter
        parts = content.split("---", 2)
        if len(parts) < 3:
            errors.append("Invalid frontmatter (missing closing ---)")
        else:
            frontmatter = parts[1]

            # Check required fields
            if "title:" not in frontmatter:
                errors.append("Missing 'title' in frontmatter")
            if "description:" not in frontmatter:
                errors.append("Missing 'description' in frontmatter")

            # Check title matches tag if provided (release notes only)
            if tag and release_notes:
                title_match = re.search(
                    r'title:\s*["\']?([^"\'\n]+)["\']?', frontmatter
                )
                if title_match and tag not in title_match.group(1):
                    errors.append(f"Title should contain '{tag}'")

    # Release notes specific checks
    if release_notes:
        required_sections = ["## Overview", "## What's New", "## Full Changelog"]
        for section in required_sections:
            if section not in content:
                errors.append(f"Missing required section: {section}")

        if "github.com/amd/gaia/compare/" not in content:
            errors.append("Missing changelog comparison link")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate MDX files")
    parser.add_argument("file", nargs="+", help="Path(s) to MDX file(s) to validate")
    parser.add_argument(
        "--tag", "-t", help="Release tag to validate against (e.g., v0.16.0)"
    )
    parser.add_argument(
        "--release-notes",
        "-r",
        action="store_true",
        help="Enable release notes specific validation",
    )
    args = parser.parse_args()

    all_errors = {}
    for file_path in args.file:
        print(f"Validating {file_path}...")
        errors = validate_mdx(file_path, args.tag, args.release_notes)
        if errors:
            all_errors[file_path] = errors

    if all_errors:
        for file_path, errors in all_errors.items():
            print(f"❌ {file_path}:")
            for error in errors:
                print(f"   - {error}")
        sys.exit(1)

    print(f"✅ {len(args.file)} file(s) validated successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
