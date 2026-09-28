# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia telegram start` refuses to run a bot anyone can talk to.

The adapter raises; this pins that the CLI turns that into an actionable
message and a non-zero exit instead of a traceback.
"""

import os
import subprocess
import sys

import pytest

from gaia.cli import build_parser


def _run_cli(*args):
    """Drive the real console entry point, not an internal function."""
    # The refusal carries a ❌, which the default Windows console encoding
    # cannot round-trip; pin UTF-8 on both ends.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", "telegram", "start", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Under the macOS lane's `--timeout=60 -x`, a longer timeout here would
        # abort the run instead of failing as a clean TimeoutExpired.
        timeout=30,
        env=env,
    )


def test_parser_still_accepts_an_omitted_allowlist():
    """argparse must not preempt the adapter's explanatory refusal."""
    args = build_parser().parse_args(["telegram", "start", "--token", "t"])
    assert args.allowed_users is None


@pytest.mark.parametrize("allowlist_args", [[], ["--allowed-users", ""]])
def test_start_without_an_allowlist_exits_nonzero_with_the_remedy(allowlist_args):
    result = _run_cli("--token", "123456789:FAKE", *allowlist_args)

    assert result.returncode == 2
    assert "no allowed-users configured" in result.stderr
    assert "--allowed-users 11111111,22222222" in result.stderr  # what to do
    assert "guides/telegram-adapter" in result.stderr  # where to look
    assert "Traceback" not in result.stderr


def test_non_numeric_allowlist_is_rejected():
    result = _run_cli("--token", "123456789:FAKE", "--allowed-users", "alice")

    assert result.returncode == 2
    assert "expected comma-separated integers" in result.stderr
