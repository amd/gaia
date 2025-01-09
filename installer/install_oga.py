# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import os
import sys
import zipfile
import subprocess


def unzip_file(zip_path, extract_to):
    """Unzips the specified zip file to the given directory."""
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Install OGA")
    parser.add_argument("--folder_path", type=str, help="Install folder path")
    parser.add_argument(
        "--zip_file_name",
        type=str,
        default="oga-npu.zip",
        help="Name of the zip file to install",
    )
    parser.add_argument(
        "--wheels_path",
        type=str,
        default="amd_oga/wheels",
        help="Path to the wheels directory",
    )
    parser.add_argument(
        "--keep_zip",
        action="store_true",
        help="Keep the zip file after installation (default: delete)",
    )

    # Parse the arguments
    args = parser.parse_args()
    folder_path = args.folder_path

    # Parse the arguments
    args = parser.parse_args()
    folder_path = args.folder_path
    zip_file_name = args.zip_file_name
    wheels_path = args.wheels_path

    # Define the path to the zip file
    zip_file_path = os.path.join(folder_path, zip_file_name)

    # Unzip the file
    unzip_file(zip_file_path, folder_path)

    # Install all whl files in the specified wheels folder
    wheels_full_path = os.path.join(folder_path, wheels_path)
    for file in os.listdir(wheels_full_path):
        if file.endswith(".whl"):
            print(f"Installing {file}")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    os.path.join(wheels_full_path, file),
                ]
            )

    # Delete the zip file unless --keep_zip is specified
    if not args.keep_zip:
        os.remove(zip_file_path)


if __name__ == "__main__":
    main()
