#!/usr/bin/env python3
# Requirement: `pip install black`
# 
# This script releases the approved sections of aigdat/gaia to the open source repo.
# 
# At the time of this writing, we are releasing the core functionality while keeping some
# internal tools and configurations private.
# 
# NOTE: This repo is the single source of truth (SSOT) for gaia.
# Please do not check code directly into the OSS repo!!! Only check code into this
# private repo, then use this script to sync it to the OSS repo. We don't have any tooling
# for syncing code in the other direction (public -> private).
# 
# Usage:
# 1. Make sure you already cloned the Gaia OSS repo on to your hard drive.
#    We'll use C:\work\gaia as an example clone.
# 2. Create a branch in the clone
# 3. NOTE: if aigdat/gaia moved any files already checked into the clone, you need
#    to `git mv` those files in the clone to the expected location before proceeding.
# 4. Run `python release.py -o C:\work\gaia` to sync changes from aigdat/gaia
#    into your Gaia OSS clone.
# 5. Use `git add` to add any new files. NOTE: be mindful when doing this! Make sure that
#    no internal/NDA files slipped through the filters.
# 6. Use `git commit -am MESSAGE` and `git push` to sync your updates to the server.
# 7. Launch a PR for your branch on the OSS repo.
# 
# Details of operation:
# - All source files under src/gaia are copied, except those that violate the
#   `exclude` list filter.
#   - NOTE: the `--internal` arg disables the exclude filter! Only use this when
#     syncing to an AMD-internal clone.
# - The script uses Git to identify tracked files in the source repository
# - Files are filtered based on an exclude list to prevent internal/NDA content from being copied
# - The script creates the necessary directory structure in the target repository
# - The script copies all files and then updates all markdown files with correct github links
# - The script prints out which files will be copied, which are excluded, and their destination paths

import os
import argparse
import subprocess
from pathlib import Path
import shutil
from typing import List

from util.docs import update_github_links


def copy_and_update(path, new_path):
    """
    Copy a Gaia file to its target location and update content if needed
    """
    # Create target directory if it doesn't exist
    new_path_dir = Path(new_path).parent
    os.makedirs(new_path_dir, exist_ok=True)

    # Copy the file
    shutil.copy2(path, new_path)


def main():
    parser = argparse.ArgumentParser(
        description="Release script for Gaia to OSS repo. Instructions: "
        "1. Clone the Gaia OSS repo to a LOCATION on your disk, "
        "2. Make a branch in the Gaia clone, "
        "3. `python release.py -o LOCATION`, "
        "4. Add/commit files as needed and make a pull request into the Gaia OSS repo"
    )

    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Location on disk of a Gaia repo clone that will "
        "be used to perform the pull request into Gaia OSS",
    )

    parser.add_argument(
        "--internal",
        help="Include artifacts that are safe to share within AMD",
        action="store_true",
    )

    args = parser.parse_args()

    gaia_location = Path(__file__).parent
    destination_location = args.output

    # Fixed exclude paths - using os.path.sep to ensure correct path separators
    # Also, a missing comma was added after "docs\\nimbys.md"
    exclude = [
        f".github{os.path.sep}workflows{os.path.sep}local_hybrid_tests.yml",
        f".github{os.path.sep}workflows{os.path.sep}test_installer_npu.yml",
        f"docs{os.path.sep}nimbys.md",
        f"nda",  # Added the NDA directory itself
        f"notebooks",
        "release.py",
    ]

    # If --internal flag is used, don't filter out any files
    if args.internal:
        exclude = []

    # Get the list of tracked files using Git index
    git_output = subprocess.check_output(
        ["git", "ls-files"], cwd=gaia_location, stderr=subprocess.DEVNULL
    )
    tracked_files = git_output.decode("utf-8").splitlines()

    # Construct absolute paths for tracked files
    file_paths = [
        os.path.abspath(os.path.join(gaia_location, filename))
        for filename in tracked_files
    ]

    # Filter files to exclude NDA content
    filtered_file_paths: List[str] = []
    for path in file_paths:
        # Get the relative path from gaia_location
        rel_path = os.path.relpath(path, gaia_location)
        
        # Check if the path should be excluded
        filter_out = any(
            rel_path == ex or rel_path.startswith(f"{ex}{os.path.sep}")
            for ex in exclude
        )
        
        if not filter_out:
            filtered_file_paths.append(path)
            
    print("The following source files will be copied:\n")
    for path in filtered_file_paths:
        print(path)

    print("\n\nThe following source files were NOT copied due to an exclude rule:\n")
    for path in file_paths:
        if path not in filtered_file_paths:
            print(path)

    # Copy the files
    print("\n\nThe new paths are:\n")
    for path in filtered_file_paths:
        new_path = path.replace(
            os.path.abspath(str(gaia_location)), destination_location
        )
        print(new_path)
        copy_and_update(path, new_path)

    # Then update GitHub links for all .md files
    update_github_links(str(destination_location))

    print("\ndone!")


if __name__ == "__main__":
    main()