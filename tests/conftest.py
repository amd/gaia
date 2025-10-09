# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT


def pytest_addoption(parser):
    parser.addoption(
        "--hybrid",
        action="store_true",
        default=False,
        help="Run with hybrid configuration (default: False)",
    )
