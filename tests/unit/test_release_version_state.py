# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/release_version_state.py: what the nightly patch-release check makes of main."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "util" / "release_version_state.py"
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_version_state as state  # noqa: E402

# The repo after v0.25.4 shipped and v0.26.0 was cut (#6915).
TAGS = ["v0.25.3", "v0.25.4-rc18", "v0.25.4", "v0.26.0-rc1", "v0.26.0-rc7"]


def test_a_next_minor_with_rcs_after_a_final_is_valid():
    assert state.classify("v0.25.4", "0.26.0", TAGS) == "next-line"


def test_a_next_major_with_rcs_is_valid():
    assert state.classify("v0.25.4", "1.0.0", TAGS + ["v1.0.0-rc1"]) == "next-line"


@pytest.mark.parametrize(
    "main, expected",
    [
        ("0.25.4", "steady"),
        ("0.25.5", "staged-not-tagged"),
    ],
)
def test_the_patch_states_are_unchanged(main, expected):
    assert state.classify("v0.25.4", main, TAGS) == expected


@pytest.mark.parametrize(
    "main, tags",
    [
        # A next minor nobody has cut an RC for.
        ("0.26.0", ["v0.25.4"]),
        # An RC of another version does not vouch for this one.
        ("0.26.0", ["v0.25.4", "v0.26.1-rc1", "v0.26.0-rc1-hotfix"]),
        # Skipping a minor, or landing past .0, is never a cut line.
        ("0.27.0", TAGS + ["v0.27.0-rc1"]),
        ("0.26.1", TAGS + ["v0.26.1-rc1"]),
        # Going backwards.
        ("0.25.3", TAGS),
        # Not a final X.Y.Z.
        ("0.26.0.dev1", TAGS),
        ("0.26.0rc1", TAGS),
    ],
)
def test_anything_else_is_a_mismatch(main, tags):
    assert state.classify("v0.25.4", main, tags) == "mismatch"


def test_an_unparsable_release_tag_fails_loudly():
    with pytest.raises(ValueError, match="v0.25.4.1"):
        state.classify("v0.25.4.1", "0.25.4", TAGS)


def test_the_cli_reads_this_checkouts_tags(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("init", "-q")
    git(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    for tag in ("v0.25.4", "v0.26.0-rc1"):
        git("tag", tag)

    def run(main):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "v0.25.4", main],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    assert run("0.26.0") == "next-line"
    assert run("0.27.0") == "mismatch"
