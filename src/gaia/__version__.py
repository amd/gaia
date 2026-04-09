"""
GAIA version information.
"""

__version__ = "0.1.0"
VERSION_INFO = {
    "major": 0,
    "minor": 1,
    "patch": 0,
    "status": "alpha",
    "build_date": "2026-03-23",
}


def get_version() -> str:
    """Return the version string."""
    return __version__


def get_version_info() -> dict:
    """Return version information dictionary."""
    return VERSION_INFO
