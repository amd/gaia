# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Packaging guard for the [mcp] extra's version pin (issue #2885).

setup.py's ``extras_require["mcp"]`` block carries a comment saying the mcp
dependency is "Capped below 2.0" because mcp 2.0.0 (released 2026-07-28)
breaks ``MCPClient.connect()`` — the custom-agent harness went red with no
code change on GAIA's side. But the pin itself reads ``mcp>=1.1.0,<3.0``,
which does NOT enforce the cap the comment describes: the comment and the
enforced version range contradict each other, so ``pip install`` can still
resolve an mcp 2.x that breaks the client.

This file asserts two things, independently:

* the pin is exactly ``mcp>=1.1.0,<2.0`` (the enforced range, not the
  comment's claim about it);
* the comment directly above the pin and the pin itself never contradict
  each other — if the comment still says the dependency is "Capped below
  2.0", the enforced range must actually be ``<2.0``.

This is a static packaging assertion — it reads setup.py's source text and
never imports or installs anything, so it works in the CI unit-tests venv
that does not install [mcp]. Modelled on test_api_extras.py (#1617) and
test_base_keyring_dep.py (#1621).
"""

from __future__ import annotations

import re
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"

_PORT_INSTRUCTION = (
    "mcp 2.0.0 broke MCPClient.connect() — before widening the cap past "
    "<2.0, port the MCP client in src/gaia/mcp/agent_mcp_server.py and "
    "src/gaia/mcp/servers/agent_ui_mcp.py to the mcp 2.x API."
)


def _parse_extra(name: str) -> list[str]:
    """Extract the requirement strings from a named extras_require block.

    Walks the file line by line so brackets that appear inside ``# comments``
    don't confuse a naive non-greedy regex match.
    """
    lines = SETUP_PY.read_text(encoding="utf-8").splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(rf'"{re.escape(name)}"\s*:\s*\[', stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, f'Could not find "{name}" extra in setup.py extras_require'
    return re.findall(r'"([^"]+)"', "\n".join(body))


def _pin_and_preceding_comment(name: str, pin_prefix: str) -> tuple[str, str]:
    """Return ``(pin_requirement_string, comment_text_directly_above_it)``.

    Walks setup.py line by line to find the requirement string starting with
    ``pin_prefix`` inside the ``extras_require[name]`` block, then walks
    backward collecting the contiguous run of ``# ...`` comment lines
    immediately above it (stopping at the first non-comment line). Mirrors
    ``_parse_extra``'s line-walking style, but keeps the comment text that
    ``_parse_extra`` deliberately discards.
    """
    lines = SETUP_PY.read_text(encoding="utf-8").splitlines()
    in_block = False
    block_start = -1
    pin_line_idx = None
    pin_value = None
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not in_block:
            if re.match(rf'"{re.escape(name)}"\s*:\s*\[', stripped):
                in_block = True
                block_start = i
            continue
        if stripped.startswith("]"):
            break
        match = re.match(rf'"({re.escape(pin_prefix)}[^"]*)"', stripped)
        if match:
            pin_line_idx = i
            pin_value = match.group(1)
            break
    assert in_block, f'Could not find "{name}" extra in setup.py extras_require'
    assert pin_line_idx is not None, (
        f'No requirement starting with "{pin_prefix}" found in the "{name}" '
        "extras_require block (setup.py)."
    )

    comment_lines: list[str] = []
    j = pin_line_idx - 1
    while j > block_start:
        stripped = lines[j].strip()
        if not stripped.startswith("#"):
            break
        comment_lines.insert(0, stripped.lstrip("#").strip())
        j -= 1

    return pin_value, " ".join(comment_lines)


def test_mcp_extra_pin_is_capped_below_2_0() -> None:
    """setup.py's mcp extra must pin ``mcp>=1.1.0,<2.0`` exactly — see #2885.

    This checks the enforced version range directly (not the comment that
    claims to describe it), so a contradiction between the two can't hide
    behind a comment nobody re-checked.
    """
    mcp_reqs = _parse_extra("mcp")
    assert "mcp>=1.1.0,<2.0" in mcp_reqs, (
        '#2885: setup.py\'s "mcp" extras_require block does not pin '
        '"mcp>=1.1.0,<2.0". ' + _PORT_INSTRUCTION + "\n"
        f'Current "mcp" extra: {mcp_reqs}'
    )


def test_mcp_extra_capped_comment_matches_pin() -> None:
    """The "Capped below 2.0" comment above the mcp pin must match the pin — see #2885.

    setup.py documents the cap with a comment ("Capped below 2.0: mcp 2.0.0
    ... breaks MCPClient.connect()"), but the pin itself can drift out of
    sync with what the comment claims (it currently reads
    ``mcp>=1.1.0,<3.0``). This must fail whenever the comment still claims
    the dependency is capped below 2.0 but the pin's upper bound is anything
    other than ``<2.0`` — the contradiction must not silently pass.
    """
    pin, comment = _pin_and_preceding_comment("mcp", "mcp>=")
    assert "capped below 2.0" in comment.lower(), (
        '#2885: expected the comment directly above the mcp pin in setup.py '
        'to still say "Capped below 2.0" (it documents why mcp is capped, '
        f"following the mcp 2.0.0 MCPClient.connect() break); got: {comment!r}. "
        "If the comment was intentionally reworded, update this test to match "
        "it; otherwise restore the comment."
    )
    assert pin == "mcp>=1.1.0,<2.0", (
        '#2885: setup.py\'s mcp extra comment says "Capped below 2.0" but the '
        f'enforced pin is "{pin}", not "mcp>=1.1.0,<2.0" — the comment and the '
        "enforced version range contradict each other. " + _PORT_INSTRUCTION
    )
