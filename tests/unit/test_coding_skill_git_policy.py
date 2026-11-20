# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the coding skill's Git claims aligned with the executable policy."""

from pathlib import Path

from gaia.skills.binaries import (
    ALLOW,
    BINARY_POLICIES,
    CONFIRM,
    REFUSE,
    classify_invocation,
)

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
CONFIRM_GIT_INVOCATIONS = (
    ("add", "src/gaia/cli.py"),
    ("commit", "-m", "fix: thing"),
    ("checkout", "main"),
    ("switch", "main"),
    ("restore", "src/gaia/cli.py"),
    ("stash", "push"),
    ("stash", "save"),
    ("stash", "apply"),
    ("stash", "pop"),
)
ALLOW_GIT_INVOCATIONS = (
    ("stash", "list"),
    ("stash", "show"),
)
REFUSED_STASH_INVOCATIONS = (
    ("stash",),
    ("stash", "drop"),
    ("stash", "clear"),
)


def _section(text: str, start: str, end: str) -> str:
    """The text from a uniquely named instruction through the next boundary."""
    return text[text.index(start) : text.index(end, text.index(start))]


def _backticked_commands(text: str) -> set[str]:
    """Commands the skill explicitly presents as commands, not prose words."""
    return {span for span in text.split("`")[1::2] if span}


def _classify(argv: tuple[str, ...]) -> str:
    return classify_invocation(BINARY_POLICIES["git"], ["git", *argv]).outcome


def test_coding_skill_bumps_for_the_changed_signed_skill():
    assert "version: 0.2.2" in SKILL.read_text(encoding="utf-8")


def test_coding_skill_documents_every_refused_git_command():
    text = SKILL.read_text(encoding="utf-8")
    refusal = _section(
        text,
        "The direct Git policy refuses these commands entirely:",
        "\n\nPublishing, history rewriting",
    )
    documented = _backticked_commands(refusal)
    expected = REFUSED_GIT_SUBCOMMANDS | {
        "commit --amend",
        "reset --hard",
    }

    assert expected <= documented
    for command in sorted(REFUSED_GIT_SUBCOMMANDS):
        assert _classify((command,)) == REFUSE
    for argv in REFUSED_GIT_INVOCATIONS:
        assert _classify(argv) == REFUSE


def test_coding_skill_explains_continue_and_abort_boundaries():
    text = SKILL.read_text(encoding="utf-8").lower()
    for command in ("merge --continue", "rebase --continue", "abort"):
        assert command in text


def test_coding_skill_discloses_the_full_supported_git_surface():
    text = SKILL.read_text(encoding="utf-8")
    surface = _section(
        text,
        "The supported Git surface is reads (",
        "\n\n## Keep the diff to the change",
    )
    documented = _backticked_commands(surface)

    # The policy entry is the entire supported surface. Requiring backticked
    # tokens prevents ordinary prose such as "add" or "format" from masking a
    # deleted command list.
    assert set(BINARY_POLICIES["git"].subcommands) <= documented


def test_coding_skill_describes_confirmable_and_refused_stash_forms():
    text = SKILL.read_text(encoding="utf-8")
    stash = _section(
        text,
        "`stash list` and `stash show` are reads.",
        "\n\nThe direct Git policy refuses",
    )

    for argv in ALLOW_GIT_INVOCATIONS:
        assert _classify(argv) == ALLOW
        assert f"`{' '.join(argv)}`" in stash
    for argv in CONFIRM_GIT_INVOCATIONS:
        assert _classify(argv) == CONFIRM
    for argv in REFUSED_STASH_INVOCATIONS:
        assert _classify(argv) == REFUSE
        assert f"`{' '.join(argv)}`" in stash
