# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from importlib.metadata import version as get_package_version_metadata
import logging
import subprocess
import os

__version__ = "0.8.6"


def get_package_version() -> str:
    """Get the installed package version from importlib.metadata.

    Returns:
        str: The package version string
    """
    try:
        return get_package_version_metadata("gaia")
    except Exception as e:
        logging.warning(f"Failed to get package version: {e}")
        return ""


def get_git_hash(hash_length: int = 8) -> str:
    """Get the current git hash.
    Only used during build/installer process.

    Args:
        hash_length: Length of the hash to return

    Returns:
        str: The git hash or 'unknown' if git is not available
    """
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return git_hash[:hash_length]
    except Exception:
        logging.warning("Failed to get Git hash")
        return ""


def get_version_with_hash() -> str:
    """Get the full version string including git hash.
    Only used during build/installer process.

    Returns:
        str: Version string in format 'v{version}+{hash}'
    """
    git_hash = get_git_hash()
    if git_hash:
        return f"v{__version__}+{git_hash}"
    else:
        return f"v{__version__}"


def write_version_files() -> None:
    """Write version information to version.txt and installer/version.nsh files.
    Only used during build/installer process."""
    try:
        # Write version with hash to version.txt
        with open("version.txt", "w", encoding="utf-8") as f:
            f.write(get_version_with_hash())

        # Write version with hash to installer/version.nsh
        installer_dir = os.path.join("installer")
        os.makedirs(installer_dir, exist_ok=True)
        with open(
            os.path.join(installer_dir, "version.nsh"), "w", encoding="utf-8"
        ) as f:
            f.write(f'!define GAIA_VERSION "{get_version_with_hash()}"\n')

        print(
            "Version files created successfully: version.txt and installer/version.nsh"
        )
    except Exception as e:
        print(f"Failed to write version files: {str(e)}")
        raise


# For regular package usage - just version number
version = get_package_version()


def main():
    """Generate version files when the script is run directly.
    Only used during build/installer process."""
    write_version_files()


if __name__ == "__main__":
    main()
