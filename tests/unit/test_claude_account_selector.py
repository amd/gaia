# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every Claude workflow must pick its token through the shared account selector.

A workflow that reads ``secrets.CLAUDE_CODE_OAUTH_TOKEN`` directly keeps using the
primary account after claude-account-switch.yml moves CI to the secondary, and
nothing would say so: it just fails on the exhausted account.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

SELECTOR = (
    "(vars.CLAUDE_ACCOUNT == 'secondary' && secrets.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY"
    " || secrets.CLAUDE_CODE_OAUTH_TOKEN)"
)

# The switch job probes each account by name; reading the primary directly is its job.
EXEMPT = {"claude-account-switch.yml"}

_PRIMARY = re.compile(r"secrets\.CLAUDE_CODE_OAUTH_TOKEN(?!_SECONDARY)")


def _direct_reads(text: str) -> list[int]:
    """Line numbers that read the primary token outside the selector."""
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        remainder = line.replace(SELECTOR, "")
        if _PRIMARY.search(remainder):
            lines.append(number)
    return lines


def test_every_claude_workflow_reads_the_token_through_the_selector():
    offenders = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name in EXEMPT:
            continue
        lines = _direct_reads(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.name] = lines
    assert not offenders, (
        "These workflows read CLAUDE_CODE_OAUTH_TOKEN directly, so they keep using the "
        f"primary account after a switch: {offenders}. Use {SELECTOR} instead."
    )


def test_the_selector_is_actually_in_use():
    """Guards the test above: if the selector text changed, it would pass vacuously."""
    uses = sum(
        path.read_text(encoding="utf-8").count(SELECTOR)
        for path in WORKFLOWS.glob("*.yml")
    )
    assert uses > 0


def test_a_direct_read_is_caught():
    assert _direct_reads("token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}") == [1]
    assert _direct_reads("token: ${{ " + SELECTOR + " }}") == []
    assert (
        _direct_reads("token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY }}") == []
    )
