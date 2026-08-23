# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Packaging guard for the [mcp] extra's version range (issue #2940).

setup.py's ``extras_require["mcp"]`` block supports the mcp 2.x API. mcp 2.0.0
(released 2026-07-28) removed ``mcp.server.fastmcp`` (``FastMCP`` was renamed
to ``MCPServer`` and moved to ``mcp.server.mcpserver``), so the range must
exclude future incompatible major versions while admitting mcp 2.x.

This file asserts two things, independently:

* the pin is exactly ``mcp>=2.0.0,<3.0``;
* the comment directly above the pin documents support for mcp 2.x and the
  upper bound that protects against a future incompatible major release.

This is a static packaging assertion — it reads setup.py's source text and
never imports or installs anything, so it works in the CI unit-tests venv
that does not install [mcp]. Modelled on test_api_extras.py (#1617) and
test_base_keyring_dep.py (#1621).
"""

from __future__ import annotations

import re
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"

# Single source of truth for the expected range. Update this constant and
# setup.py's requirement/comment together when supporting another major.
EXPECTED_MCP_PIN = "mcp>=2.0.0,<3.0"
EXPECTED_CAP_COMMENT_SUBSTRING = "supports mcp 2.x"

_PORT_INSTRUCTION = (
    "mcp 2.x support requires the MCPServer API in "
    "src/gaia/mcp/agent_mcp_server.py, src/gaia/mcp/servers/agent_ui_mcp.py, "
    "and src/gaia/mcp/servers/tui_mcp.py."
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


def test_mcp_extra_supports_2_x() -> None:
    """setup.py's mcp extra must use the supported mcp 2.x range — see #2940.

    This checks the enforced version range directly (not the comment that
    claims to describe it), so a contradiction between the two can't hide
    behind a comment nobody re-checked.
    """
    mcp_reqs = _parse_extra("mcp")
    assert EXPECTED_MCP_PIN in mcp_reqs, (
        f'#2940: setup.py\'s "mcp" extras_require block does not pin '
        f'"{EXPECTED_MCP_PIN}". ' + _PORT_INSTRUCTION + "\n"
        f'Current "mcp" extra: {mcp_reqs}'
    )


def test_mcp_extra_support_comment_matches_pin() -> None:
    """The support comment above the mcp pin must match the pin — see #2940.

    setup.py documents the supported API and upper bound in a comment. This
    must fail if that comment or the enforced range drifts.
    """
    pin, comment = _pin_and_preceding_comment("mcp", "mcp>=")
    assert EXPECTED_CAP_COMMENT_SUBSTRING in comment.lower(), (
        "#2940: expected the comment directly above the mcp pin in setup.py "
        f'to say "supports mcp 2.x"; got: {comment!r}. '
        "If the comment was intentionally reworded, update this test to match "
        "it; otherwise restore the comment."
    )
    assert pin == EXPECTED_MCP_PIN, (
        f"#2940: setup.py's mcp extra comment documents mcp 2.x support but "
        f'the enforced pin is "{pin}", not "{EXPECTED_MCP_PIN}". ' + _PORT_INSTRUCTION
    )
