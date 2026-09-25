# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every leaf `gaia <subcommand>` must appear, full path, in docs/reference/cli.mdx.

#4250: nested commands like `gaia mcp serve` and `gaia eval code` existed in
the parser with zero mention in the CLI reference. Matching on bare
`add_parser()` names (`list`, `get`, `set`, `start`, `stop`, ...) would produce
dozens of false positives from nested parsers whose parent section already
covers them, so this walks the parser to the leaf and checks the full
`gaia <parent> <child>` path — the same form `cli.mdx` already writes commands
in (`gaia config get`, `gaia mcp start`, `gaia slack setup`, ...).

Commands still missing when this test was written are pre-existing debt,
listed in ``_ALLOWLISTED_UNDOCUMENTED`` so this test can catch *new* drift
without requiring every gap to be closed in one PR. Shrink the allowlist as
each is documented; nothing may be added to it going forward.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_DOC = REPO_ROOT / "docs" / "reference" / "cli.mdx"

# Pre-existing gaps (#4250 fixed `mcp serve` / `eval code`; these remain).
_ALLOWLISTED_UNDOCUMENTED = {
    ("connectors", "activations", "activate"),
    ("connectors", "activations", "deactivate"),
    ("connectors", "activations", "list"),
    ("connectors", "disconnect"),
    ("connectors", "grants", "list"),
    ("connectors", "grants", "revoke"),
    ("connectors", "list"),
    ("connectors", "status"),
    ("connectors", "test"),
    ("eval", "sessions"),
    ("schedule", "remove"),
    ("schedule", "show"),
    ("slack", "connect"),
    ("slack", "decline"),
}


def _iter_leaf_commands(
    parser: argparse.ArgumentParser,
    prefix: tuple[str, ...] = (),
    seen: set[int] | None = None,
) -> Iterator[tuple[str, ...]]:
    """Yield only the leaf command paths — the ones a user actually runs."""
    if seen is None:
        seen = set()

    subparsers_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if subparsers_action is None:
        if prefix:
            yield prefix
        return

    for name, child in subparsers_action.choices.items():
        if id(child) in seen:
            continue
        seen.add(id(child))
        yield from _iter_leaf_commands(child, prefix + (name,), seen)


def _discover_leaf_commands() -> list[tuple[str, ...]]:
    from gaia.cli import build_parser  # deferred — runs cli.py module side-effects

    return sorted(set(_iter_leaf_commands(build_parser())))


def test_every_leaf_command_is_documented():
    cli_text = CLI_DOC.read_text(encoding="utf-8")
    leaves = _discover_leaf_commands()
    assert len(leaves) > 50, "discovery walk found suspiciously few commands"

    undocumented = [
        path
        for path in leaves
        if path not in _ALLOWLISTED_UNDOCUMENTED
        and f"gaia {' '.join(path)}" not in cli_text
    ]
    assert not undocumented, (
        "New undocumented subcommand(s) — add a section to docs/reference/cli.mdx "
        f"(or, if truly out of scope, to the allowlist with a reason): {undocumented}"
    )


def test_allowlist_has_no_stale_entries():
    """An allowlisted command that got documented (or removed) must be dropped."""
    cli_text = CLI_DOC.read_text(encoding="utf-8")
    leaves = set(_discover_leaf_commands())
    stale = [
        path
        for path in _ALLOWLISTED_UNDOCUMENTED
        if path not in leaves or f"gaia {' '.join(path)}" in cli_text
    ]
    assert not stale, f"Allowlist entries no longer belong there: {stale}"
