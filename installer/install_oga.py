# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import os
import sys
import zipfile
import requests
import subprocess
import shutil


def unzip_file(zip_path, extract_to):
    """Unzips the specified zip file to the given directory."""
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Install OGA NPU")
    parser.add_argument("folder_path", type=str, help="Install folder path")

    # Parse the arguments
    args = parser.parse_args()
    folder_path = args.folder_path

    # Define the path to the zip file
    zip_file_path = os.path.join(folder_path, "oga-npu.zip")

    # Unzip the file
    unzip_file(zip_file_path, folder_path)

    # Install all whl files in the amd_oga folder
    wheels_path = os.path.join(folder_path, "amd_oga", "wheels")
    for file in os.listdir(wheels_path):
        if file.endswith(".whl"):
            print(f"Installing {file}")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    os.path.join(wheels_path, file),
                ]
            )

    # Delete the zip file
    os.remove(zip_file_path)


if __name__ == "__main__":
    main()
