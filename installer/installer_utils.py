#!/usr/bin/env python3
import sys
import re
import subprocess
from packaging import version
import os
from datetime import datetime


def log_message(message):
    """Write message to gaia_install.log in current directory and print to console."""
    print(message)
    log_file = "gaia_install.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        # If we can't write to log, we have a bigger problem
        # but don't want to crash the installer
        pass


def check_version_compatibility(expected_version, actual_version):
    """
    Compare two version strings, checking if they are compatible.
    Compatible means they have the same major and minor version numbers.

    Args:
        expected_version (str): Expected version (e.g. "8.0.1", "v8.0.1")
        actual_version (str): Actual version output (may contain extra text)

    Returns:
        tuple: (bool, str) - (is_compatible, extracted_version)
    """
    try:
        # Clean expected version (remove 'v' prefix)
        expected = expected_version.lstrip("v")

        # Extract version number from actual output using regex
        # Look for pattern like "8.0.1" in the string
        version_match = re.search(r"\d+\.\d+(?:\.\d+)?", actual_version)
        if not version_match:
            log_message(f"ERROR: No version number found in: {actual_version}")
            return False, ""

        actual = version_match.group()

        # Parse and compare major.minor versions
        expected_ver = version.parse(expected)
        actual_ver = version.parse(actual)

        is_compatible = (
            expected_ver.major == actual_ver.major
            and expected_ver.minor == actual_ver.minor
        )

        log_message(
            f"Expected: {expected} (major.minor: {expected_ver.major}.{expected_ver.minor})"
        )
        log_message(
            f"Actual: {actual} (major.minor: {actual_ver.major}.{actual_ver.minor})"
        )
        log_message(f"Compatible: {is_compatible}")

        # Print the detected version for the NSI script to parse - use print() directly
        # to ensure clean output format for NSI parsing
        print(f"VERSION:{actual}")

        return is_compatible, actual

    except Exception as e:
        log_message(f"ERROR: {e}")
        return False, ""


def get_lemonade_version():
    """
    Get the version output from lemonade-server --version command.

    Returns:
        str: The complete output from lemonade-server --version
    """
    try:
        # Use shell=True and cmd /c to match the NSI script approach
        # This ensures proper PATH resolution on Windows
        result = subprocess.run(
            'cmd /c "lemonade-server --version 2>&1"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Combine stdout and stderr to get complete output
        full_output = result.stdout + result.stderr

        log_message(f"Lemonade version command output:")
        log_message(f"Return code: {result.returncode}")
        log_message(f"Full output: {repr(full_output)}")

        if result.returncode != 0:
            log_message(
                f"ERROR: lemonade-server --version failed with return code {result.returncode}"
            )
            # Add some debug info to help troubleshoot
            log_message("DEBUG: Checking if lemonade-server is in PATH...")
            where_result = subprocess.run(
                "where lemonade-server", shell=True, capture_output=True, text=True
            )
            log_message(f"'where lemonade-server' result: {where_result.returncode}")
            log_message(
                f"'where lemonade-server' output: {repr(where_result.stdout + where_result.stderr)}"
            )
            return None

        return full_output.strip()

    except subprocess.TimeoutExpired:
        log_message("ERROR: lemonade-server --version command timed out")
        return None
    except FileNotFoundError:
        log_message("ERROR: Failed to execute command (FileNotFoundError)")
        return None
    except Exception as e:
        log_message(f"ERROR: Failed to run lemonade-server --version: {e}")
        return None


def main():
    if len(sys.argv) != 2:
        log_message("Usage: python installer_utils.py <expected_version>")
        sys.exit(1)

    expected = sys.argv[1]
    log_message(f"Starting lemonade version check for expected version: {expected}")

    # Get the actual version from lemonade-server
    actual_version_output = get_lemonade_version()

    if actual_version_output is None:
        log_message("ERROR: Could not get lemonade version")
        sys.exit(1)

    is_compatible, detected_version = check_version_compatibility(
        expected, actual_version_output
    )

    if is_compatible:
        log_message("Lemonade version check: PASSED")
    else:
        log_message("Lemonade version check: FAILED")

    sys.exit(0 if is_compatible else 1)


if __name__ == "__main__":
    main()
