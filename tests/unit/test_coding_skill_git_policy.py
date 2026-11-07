# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the coding skill's Git refusal inventory aligned with the policy."""

from pathlib import Path

from gaia.skills.binaries import BINARY_POLICIES, REFUSE, classify_invocation

SKILL = Path(__file__).resolve().parents[2] / "hub" / "skills" / "coding" / "SKILL.md"


REFUSED_GIT_SUBCOMMANDS = {
    "apply",
    "bisect",
    "cherry-pick",
    "clean",
    "clone",
    "config",
    "fetch",
    "format-patch",
    "gc",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "revert",
    "rm",
    "tag",
}
REFUSED_GIT_INVOCATIONS = (
    ("commit", "--amend"),
    ("reset", "--hard"),
)


def test_coding_skill_documents_every_refused_git_command():
    text = SKILL.read_text(encoding="utf-8").lower()
    for command in sorted(REFUSED_GIT_SUBCOMMANDS):
        assert command in text, command
    for argv in REFUSED_GIT_INVOCATIONS:
        assert " ".join(argv) in text


def test_refused_git_commands_have_no_approval_path():
    policy = BINARY_POLICIES["git"]
    for command in sorted(REFUSED_GIT_SUBCOMMANDS):
        assert command not in policy.subcommands
        assert classify_invocation(policy, ["git", command]).outcome == REFUSE
    for argv in REFUSED_GIT_INVOCATIONS:
        assert classify_invocation(policy, ["git", *argv]).outcome == REFUSE


def test_coding_skill_explains_continue_and_abort_boundaries():
    text = SKILL.read_text(encoding="utf-8").lower()
    for command in ("merge --continue", "rebase --continue", "abort"):
        assert command in text


def test_coding_skill_discloses_the_supported_git_surface():
    text = SKILL.read_text(encoding="utf-8").lower()
    for command in (
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "add",
        "commit",
        "checkout",
        "switch",
        "restore",
        "stash",
    ):
        assert command in text
