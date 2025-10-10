# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import os

import requests


def download_lfs_file(token, file, output_filename):
    """Downloads a file from LFS"""
    # Set up the headers for the request
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    response = requests.get(
        f"https://api.github.com/repos/aigdat/ryzenai-sw-ea/contents/{file}",
        headers=headers,
    )

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the JSON response to get the download URL
        content = response.json()
        download_url = content.get("download_url")

        if download_url:
            # Download the file from the download URL
            file_response = requests.get(download_url)

            # Write the content to a file
            with open(output_filename, "wb") as file:
                file.write(file_response.content)
        else:
            print("Download URL not found in the response.")
    else:
        print(
            f"Failed to fetch the content from GitHub API. Status code: {response.status_code}"
        )
        print(response.json())


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Download LFS file")
    parser.add_argument("file", type=str, help="Path to the folder containing LFS file")
    parser.add_argument(
        "output_folder_path", type=str, help="Output folder where to save the LFS file"
    )
    parser.add_argument(
        "output_file_name", type=str, help="New name of the downloaded file"
    )
    parser.add_argument("token", type=str, help="LFS token")

    # Parse the arguments
    args = parser.parse_args()

    # Define the path to the zip file
    output_path = os.path.join(args.output_folder_path, args.output_file_name)

    # Download LFS file
    download_lfs_file(token=args.token, file=args.file, output_filename=output_path)

    # Check if the zip file exists
    if not os.path.isfile(output_path):
        print(f"Error: {output_path} does not exist.")
        exit(1)


if __name__ == "__main__":
    main()
