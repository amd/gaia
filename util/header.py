# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import fnmatch
import os
import sys
from datetime import datetime, timezone

# Directories to ignore during file traversal
IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "out",
    "dist",
    "build",
    ".venv",
    "venv",
}

# Supported file extensions
SUPPORTED_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".mjs",
    ".cjs",
    ".yml",
    ".yaml",
}

# Copyright header templates
COPYRIGHT_TEMPLATE = (
    "{comment} Copyright(C) {years} Advanced Micro Devices, Inc. All rights reserved."
)
SPDX_TEMPLATE = "{comment} SPDX-License-Identifier: MIT"


def get_copyright_years():
    """Generate copyright year range as last_year-current_year based on UTC time."""
    current_year = datetime.now(timezone.utc).year
    last_year = current_year - 1
    return f"{last_year}-{current_year}"


def get_comment_style(file_path):
    """Return comment style based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".py", ".yml", ".yaml"]:
        return "#"
    elif ext in [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"]:
        return "//"
    return None


def find_files_with_header(root_dir, header):
    matching_files = []  # Will store tuples of (file_path, comment_marker)
    counter = 0
    total_files_checked = 0
    comment_markers = [
        "#",
        "//",
        "/*",
        ";",
        "--",
        "%",
        "REM",
        "@REM",
        "echo",
        "##",
        "###",
        "####",
        "#####",
        "######",  # Add markdown headers
    ]

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        print(f"\nChecking directory: {dirpath}")
        for filename in fnmatch.filter(filenames, "*"):
            total_files_checked += 1
            if total_files_checked % 100 == 0:
                print(f"Files checked: {total_files_checked}", end="\r", flush=True)

            file_path = os.path.join(dirpath, filename)
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    first_line = file.readline().strip()
                    if header in first_line:
                        # Extract comment marker from the start of the line
                        comment_marker = ""
                        # Sort markers by length (longest first) to catch '####' before '#'
                        sorted_markers = sorted(comment_markers, key=len, reverse=True)
                        for marker in sorted_markers:
                            if first_line.startswith(marker):
                                comment_marker = marker
                                break

                        matching_files.append(
                            {"path": file_path, "marker": comment_marker}
                        )
                        counter += 1
                        print(
                            f"\nFound ({counter}): {file_path} [marker: {comment_marker}]"
                        )
            except UnicodeDecodeError:
                try:
                    with open(file_path, "r", encoding="latin-1") as file:
                        first_line = file.readline().strip()
                        if header in first_line:
                            # Extract comment marker from the start of the line
                            comment_marker = ""
                            # Sort markers by length (longest first) to catch '####' before '#'
                            sorted_markers = sorted(
                                comment_markers, key=len, reverse=True
                            )
                            for marker in sorted_markers:
                                if first_line.startswith(marker):
                                    comment_marker = marker
                                    break

                            matching_files.append(
                                {"path": file_path, "marker": comment_marker}
                            )
                            counter += 1
                            print(
                                f"\nFound ({counter}): {file_path} [marker: {comment_marker}]"
                            )
                except Exception as e:
                    print(f"\nError reading {file_path}: {e}")
            except Exception as e:
                print(f"\nError reading {file_path}: {e}")

    print(f"\nTotal files checked: {total_files_checked}")
    print(f"Total files found: {counter}")
    return matching_files


def update_headers(files, check_mode=False):
    """Update copyright headers with current years and ensure SPDX is present."""
    files_modified = 0
    years = get_copyright_years()

    for file_info in files:
        file_path = file_info["path"]
        comment_marker = file_info["marker"]

        # If no comment marker found, default to '#'
        if not comment_marker:
            comment_marker = "#"

        new_copyright = COPYRIGHT_TEMPLATE.format(comment=comment_marker, years=years)
        new_spdx = SPDX_TEMPLATE.format(comment=comment_marker)

        try:
            # Read the file content
            with open(file_path, "r", encoding="utf-8") as file:
                lines = file.readlines()

            # Find copyright and SPDX lines in first 5 lines
            copyright_line_idx = None
            spdx_line_idx = None

            for i, line in enumerate(lines[:5]):
                if "Copyright(C)" in line:
                    copyright_line_idx = i
                if "SPDX-License-Identifier" in line:
                    spdx_line_idx = i

            # Check if update is needed
            if copyright_line_idx is not None:
                # Check if already up to date
                if (
                    lines[copyright_line_idx].strip() == new_copyright
                    and spdx_line_idx is not None
                    and lines[spdx_line_idx].strip() == new_spdx
                ):
                    continue  # Already up to date, skip

                # File needs update
                files_modified += 1
                print(f"{'Needs update' if check_mode else 'Updated'}: {file_path}")

                # Short circuit if in check mode
                if check_mode:
                    continue

                # Perform the update
                lines[copyright_line_idx] = new_copyright + "\n"

                # If SPDX exists right after copyright, replace it
                if spdx_line_idx == copyright_line_idx + 1:
                    lines[spdx_line_idx] = new_spdx + "\n"
                # If SPDX doesn't exist, insert it after copyright
                elif spdx_line_idx is None:
                    lines.insert(copyright_line_idx + 1, new_spdx + "\n")

                # Write back to file
                with open(file_path, "w", encoding="utf-8") as file:
                    file.writelines(lines)

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    print(
        f"\nTotal files {'needing update' if check_mode else 'modified'}: {files_modified}"
    )
    return files_modified


def find_files_without_header(root_dir):
    """Find all supported files that don't have a copyright header."""
    files_without_header = []
    counter = 0
    total_files_checked = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]

        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            ext = os.path.splitext(filename)[1].lower()

            if ext not in SUPPORTED_EXTENSIONS:
                continue

            total_files_checked += 1
            if total_files_checked % 100 == 0:
                print(f"Files checked: {total_files_checked}", end="\r", flush=True)

            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    # Check first 5 lines for copyright
                    has_copyright = False
                    for _ in range(5):
                        line = file.readline()
                        if not line:
                            break
                        if "Copyright(C)" in line:
                            has_copyright = True
                            break

                    if not has_copyright:
                        files_without_header.append(file_path)
                        counter += 1
                        print(f"\nMissing header ({counter}): {file_path}")

            except UnicodeDecodeError:
                # Skip binary files
                pass
            except Exception as e:
                print(f"\nError reading {file_path}: {e}")

    print(f"\nTotal supported files checked: {total_files_checked}")
    print(f"Files missing copyright header: {counter}")
    return files_without_header


def add_complete_header(files, check_mode=False):
    """Add complete copyright header to files that don't have one."""
    files_modified = 0
    years = get_copyright_years()

    for file_path in files:
        comment_style = get_comment_style(file_path)
        if not comment_style:
            print(f"Skipping (unsupported file type): {file_path}")
            continue

        files_modified += 1
        print(f"{'Needs header' if check_mode else 'Added header to'}: {file_path}")

        # Short circuit if in check mode
        if check_mode:
            continue

        copyright_line = (
            COPYRIGHT_TEMPLATE.format(comment=comment_style, years=years) + "\n"
        )
        spdx_line = SPDX_TEMPLATE.format(comment=comment_style) + "\n"

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                lines = file.readlines()

            # Check if first line is a shebang
            insert_at = 0
            if lines and lines[0].startswith("#!"):
                # Insert blank comment line after shebang
                lines.insert(1, comment_style + "\n")
                insert_at = 2

            # Insert header
            lines.insert(insert_at, copyright_line)
            lines.insert(insert_at + 1, spdx_line)

            # Add blank line after header if file has content and next line isn't blank
            if insert_at + 2 < len(lines) and lines[insert_at + 2].strip() != "":
                lines.insert(insert_at + 2, "\n")

            with open(file_path, "w", encoding="utf-8") as file:
                file.writelines(lines)

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    print(
        f"\nTotal files {'needing headers' if check_mode else 'modified'}: {files_modified}"
    )
    return files_modified


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manage copyright headers in source files"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: verify headers without modifying files (exit 1 if updates needed)",
    )
    args = parser.parse_args()

    check_mode = args.check

    # Get repo root (one level up from this script's location in util/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_directory = os.path.dirname(script_dir)

    years = get_copyright_years()

    mode_str = "Checking" if check_mode else "Processing"
    print(f"{mode_str} files with copyright year: {years}")
    print("=" * 80)

    # Find files needing headers and files with headers
    files_without = find_files_without_header(root_directory)
    files_with = find_files_with_header(root_directory, "Copyright(C)")

    # Process files
    files_added = add_complete_header(files_without, check_mode=check_mode)
    files_updated = update_headers(files_with, check_mode=check_mode)

    # Report results
    print(f"{'=' * 80}")
    if check_mode:
        if files_added > 0 or files_updated > 0:
            print(f"Files needing updates: {files_added + files_updated}")
            print(f"{'=' * 80}")
            print("❌ Copyright headers are missing or outdated")
            print("Please run: python util/header.py")
            sys.exit(1)
        else:
            print(f"{'=' * 80}")
            print("✅ All copyright headers are up to date")
            sys.exit(0)
    else:
        print(f"Headers added: {files_added}")
        print(f"Headers updated: {files_updated}")
        print(f"{'=' * 80}")
        print("Done!")
