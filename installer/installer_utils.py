#!/usr/bin/env python3
import sys
from packaging import version


def check_version_compatibility(expected_version, actual_version):
    """
    Compare two version strings, checking if they are compatible.
    Compatible means they have the same major and minor version numbers.
    Patch version differences are ignored.

    Args:
        expected_version (str): The expected version string (e.g. "v6.2.0")
        actual_version (str): The actual version string to check (e.g. "Lemonade Server version is 6.2.0")

    Returns:
        bool: True if versions are compatible, False otherwise
    """
    try:
        # Remove 'v' prefix from expected version if present
        expected = expected_version.lstrip("v")
        print(f"- Cleaned expected version: {expected}")

        # Find first digit in actual_version
        for i, char in enumerate(actual_version):
            if char.isdigit():
                actual = actual_version[i:]
                break
        else:
            print(f"- ERROR: No version number found in: {actual_version}")
            return False

        print(f"- Cleaned actual version: {actual}")

        # Parse versions
        expected_ver = version.parse(expected)
        actual_ver = version.parse(actual)

        print(f"- Parsed expected version: {expected_ver}")
        print(f"- Parsed actual version: {actual_ver}")

        # Compare major and minor versions
        is_compatible = (
            expected_ver.major == actual_ver.major
            and expected_ver.minor == actual_ver.minor
        )

        print(f"- Version compatibility check result: {is_compatible}")
        return is_compatible

    except Exception as e:
        print(f"- ERROR: Error comparing versions: {str(e)}")
        return False


def main():
    """
    Main function to handle command line arguments.
    Takes two arguments: expected_version and actual_version.
    Returns 0 if versions are compatible, 1 otherwise.
    """
    if len(sys.argv) != 3:
        print("Usage: python installer_utils.py <expected_version> <actual_version>")
        sys.exit(1)

    expected = sys.argv[1]
    actual = sys.argv[2]

    print(f"- Starting version compatibility check")
    print(f"- Expected version: {expected}")
    print(f"- Actual version: {actual}")

    is_compatible = check_version_compatibility(expected, actual)
    # Return 0 for success (compatible), 1 for failure (incompatible)
    sys.exit(0 if is_compatible else 1)


if __name__ == "__main__":
    main()
