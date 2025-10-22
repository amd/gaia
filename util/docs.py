# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
from pathlib import Path


def update_github_links(folder_path):
    # Convert to Path object for better path handling
    folder = Path(folder_path)

    # Find all markdown files
    markdown_files = list(folder.rglob("*.md"))

    old_path = "aigdat/gaia"
    new_path = "amd/gaia"

    changes_count = 0
    updated_files = []

    for file_path in markdown_files:
        try:
            # Read the content
            with open(file_path, "r", encoding="utf-8") as file:
                content = file.read()

            # Check if the old path exists in the content
            if old_path in content:
                # Replace the path
                updated_content = content.replace(old_path, new_path)

                # Write back to file
                with open(file_path, "w", encoding="utf-8") as file:
                    file.write(updated_content)

                # Count occurrences of the old path
                file_changes = content.count(old_path)
                changes_count += file_changes
                updated_files.append((file_path, file_changes))
                print(f"Updated {file_path} ({file_changes} changes)")

        except Exception as e:
            print(f"Error processing {file_path}: {str(e)}")

    # Summary report
    print("\nSummary:")
    print(f"Total files updated: {len(updated_files)}")
    print(f"Total changes made: {changes_count}")
    if updated_files:
        print("\nUpdated files:")
        for file_path, count in updated_files:
            print(f"- {file_path} ({count} changes)")


if __name__ == "__main__":
    # Get folder path from user
    folder_path = input("Enter the folder path to process: ")

    if os.path.exists(folder_path):
        update_github_links(folder_path)
        print("Processing complete!")
    else:
        print("Invalid folder path!")
