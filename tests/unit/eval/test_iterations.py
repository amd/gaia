# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for CLI-level --iterations validation."""

import subprocess
import sys


class TestIterationsCLIValidation:
    """Tests for CLI-level iterations validation."""

    def test_iterations_zero_rejected(self):
        """--iterations 0 should print error and exit."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--iterations", "0"],
            capture_output=True,
            text=True,
        )
        assert "must be >= 1" in result.stderr

    def test_fix_with_iterations_rejected(self):
        """--fix combined with --iterations > 1 should print error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gaia.cli",
                "eval",
                "agent",
                "--fix",
                "--iterations",
                "3",
            ],
            capture_output=True,
            text=True,
        )
        assert "incompatible" in result.stderr

    def test_iterations_negative_rejected(self):
        """--iterations -1 should be rejected."""
        result = subprocess.run(
            [sys.executable, "-m", "gaia.cli", "eval", "agent", "--iterations", "-1"],
            capture_output=True,
            text=True,
        )
        assert "must be >= 1" in result.stderr
