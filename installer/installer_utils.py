#!/usr/bin/env python3
import sys
import re
from packaging import version


def check_version_compatibility(expected_version, actual_version):
    """
    Compare two version strings, checking if they are compatible.
    Compatible means they have the same major and minor version numbers.

    Args:
        expected_version (str): Expected version (e.g. "8.0.1", "v8.0.1")
        actual_version (str): Actual version output (may contain extra text)

    Returns:
        bool: True if versions are compatible, False otherwise
    """
    try:
        # Clean expected version (remove 'v' prefix)
        expected = expected_version.lstrip("v")

        # Extract version number from actual output using regex
        # Look for pattern like "8.0.1" in the string
        version_match = re.search(r"\d+\.\d+(?:\.\d+)?", actual_version)
        if not version_match:
            print(f"ERROR: No version number found in: {actual_version}")
            return False

        actual = version_match.group()

        # Parse and compare major.minor versions
        expected_ver = version.parse(expected)
        actual_ver = version.parse(actual)

        is_compatible = (
            expected_ver.major == actual_ver.major
            and expected_ver.minor == actual_ver.minor
        )

        print(
            f"Expected: {expected} (major.minor: {expected_ver.major}.{expected_ver.minor})"
        )
        print(f"Actual: {actual} (major.minor: {actual_ver.major}.{actual_ver.minor})")
        print(f"Compatible: {is_compatible}")

        return is_compatible

    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    if len(sys.argv) != 3:
        print("Usage: python installer_utils.py <expected_version> <actual_version>")
        sys.exit(1)

    expected = sys.argv[1]
    actual = sys.argv[2]

    is_compatible = check_version_compatibility(expected, actual)
    sys.exit(0 if is_compatible else 1)


if __name__ == "__main__":
    main()
