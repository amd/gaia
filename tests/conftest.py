import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--hybrid",
        action="store_true",
        default=False,
        help="Run with hybrid configuration (default: False)",
    )
