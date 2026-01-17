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


def validate_release_notes(file_path: str, tag: str | None = None) -> list[str]:
    """
    Validate a release notes MDX file.

    Args:
        file_path: Path to the MDX file
        tag: Optional release tag to validate against (e.g., 'v0.16.0')

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

            # Check title matches tag if provided
            if tag:
                title_match = re.search(
                    r'title:\s*["\']?([^"\'\n]+)["\']?', frontmatter
                )
                if title_match and tag not in title_match.group(1):
                    errors.append(f"Title should contain '{tag}'")

    # Required sections
    required_sections = ["## Overview", "## What's New", "## Full Changelog"]
    for section in required_sections:
        if section not in content:
            errors.append(f"Missing required section: {section}")

    # Changelog link
    if "github.com/amd/gaia/compare/" not in content:
        errors.append("Missing changelog comparison link")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate release notes MDX files")
    parser.add_argument("file", nargs="+", help="Path(s) to release notes MDX file(s)")
    parser.add_argument(
        "--tag", "-t", help="Release tag to validate against (e.g., v0.16.0)"
    )
    args = parser.parse_args()

    all_errors = {}
    for file_path in args.file:
        print(f"Validating {file_path}...")
        errors = validate_release_notes(file_path, args.tag)
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
