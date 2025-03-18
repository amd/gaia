# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import fnmatch

def find_files_with_header(root_dir, header):
    matching_files = []  # Will store tuples of (file_path, comment_marker)
    counter = 0
    total_files_checked = 0
    ignore_dirs = {'.git', '__pycache__', '.pytest_cache'}
    comment_markers = [
        '#', '//', '/*', ';', '--', '%', 'REM', '@REM', 'echo',
        '##', '###', '####', '#####', '######'  # Add markdown headers
    ]

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs]

        print(f"\nChecking directory: {dirpath}")
        for filename in fnmatch.filter(filenames, '*'):
            total_files_checked += 1
            if total_files_checked % 100 == 0:
                print(f"Files checked: {total_files_checked}", end='\r', flush=True)

            file_path = os.path.join(dirpath, filename)
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    first_line = file.readline().strip()
                    if header in first_line:
                        # Extract comment marker from the start of the line
                        comment_marker = ''
                        # Sort markers by length (longest first) to catch '####' before '#'
                        sorted_markers = sorted(comment_markers, key=len, reverse=True)
                        for marker in sorted_markers:
                            if first_line.startswith(marker):
                                comment_marker = marker
                                break

                        matching_files.append({'path': file_path, 'marker': comment_marker})
                        counter += 1
                        print(f"\nFound ({counter}): {file_path} [marker: {comment_marker}]")
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='latin-1') as file:
                        first_line = file.readline().strip()
                        if header in first_line:
                            # Extract comment marker from the start of the line
                            comment_marker = ''
                            # Sort markers by length (longest first) to catch '####' before '#'
                            sorted_markers = sorted(comment_markers, key=len, reverse=True)
                            for marker in sorted_markers:
                                if first_line.startswith(marker):
                                    comment_marker = marker
                                    break

                            matching_files.append({'path': file_path, 'marker': comment_marker})
                            counter += 1
                            print(f"\nFound ({counter}): {file_path} [marker: {comment_marker}]")
                except Exception as e:
                    print(f"\nError reading {file_path}: {e}")
            except Exception as e:
                print(f"\nError reading {file_path}: {e}")

    print(f"\nTotal files checked: {total_files_checked}")
    print(f"Total files found: {counter}")
    return matching_files

def add_spdx_header(files):
    files_modified = 0
    files_skipped = 0

    for file_info in files:
        file_path = file_info['path']
        comment_marker = file_info['marker']

        # If no comment marker found, default to '#'
        if not comment_marker:
            comment_marker = '#'

        spdx_header = f"{comment_marker} SPDX-License-Identifier: MIT"

        try:
            # Read the file content
            with open(file_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            # Check first few lines for existing SPDX header
            has_spdx = False
            for line in lines[:5]:
                if "SPDX-License-Identifier" in line:
                    has_spdx = True
                    files_skipped += 1
                    print(f"Skipping (already has SPDX): {file_path}")
                    break

            # Insert SPDX header after the first line if not found
            if not has_spdx and len(lines) > 0:
                lines.insert(1, spdx_header + '\n')

                # Write back to file
                with open(file_path, 'w', encoding='utf-8') as file:
                    file.writelines(lines)

                files_modified += 1
                print(f"Added SPDX header to: {file_path}")

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    print(f"\nTotal files modified: {files_modified}")
    print(f"Files skipped (already had SPDX): {files_skipped}")
    return files_modified

if __name__ == "__main__":
    gaia_root = os.getenv('GAIA_ROOT', '.')
    root_directory = os.path.join(gaia_root)
    header_to_find = "Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved."

    files = find_files_with_header(root_directory, header_to_find)

    if files:
        print("\nAdding SPDX headers...")
        add_spdx_header(files)

    print("Done")
