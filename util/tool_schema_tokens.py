# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Measure what the flagship agent's tool schemas cost on the wire.

Every model call re-sends the schema of every offered tool, so this text is
billed on each step of each turn. This script prints the per-tool and total
character counts for the two parts an author controls -- the ``description``
(the tool's docstring) and the parameter descriptions (its ``Args:`` block) --
plus a chars/4 token estimate.

Two tool sets are reported:

* **turn** (default) -- what the dynamic tool loader offers on a coding-style
  request: the flagship's CORE set plus the bundles such a turn pulls in. The
  bundle list is fixed (see ``CODING_TURN_BUNDLES``) rather than selected by
  the live embedder, so the number is reproducible without a backend.
* **registry** (``--all``) -- every tool the flagship can offer, which is what
  ``util/check_tool_descriptions.py`` holds to the budget.

Usage::

    python util/tool_schema_tokens.py            # coding-style turn
    python util/tool_schema_tokens.py --all      # every registered tool
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# A tool's docstring is one model-call payload; 4 chars/token is the usual
# English rule of thumb and keeps this script tokenizer-free.
CHARS_PER_TOKEN = 4

#: Bundles a coding-style request pulls in on top of the CORE set. Fixed so the
#: measurement is reproducible; the live loader picks these semantically.
CODING_TURN_BUNDLES = ("file_search", "file_discovery", "shell", "code_index")


def prepend_source_roots() -> None:
    """Import ``gaia`` and the hub agents from THIS checkout, not an install.

    Same reason as the root ``conftest.py``: the packages are editable-installed
    and on a machine with several worktrees that install points at whichever one
    ran ``pip install -e`` last.
    """
    roots = [REPO_ROOT / "src", *sorted(REPO_ROOT.glob("hub/agents/*/python"))]
    for root in reversed(roots):
        entry = str(root)
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)


def flagship_tool_schemas(turn_only: bool = False) -> List[dict]:
    """Return the flagship's OpenAI tool schemas, exactly as they ship.

    Builds the offline ``GaiaAgent`` skeleton (no Lemonade, no model load) and
    renders the real ``tools=`` payload. With *turn_only*, restrict it to the
    coding-style turn set described in the module docstring.
    """
    from gaia.eval.tool_cost import build_full_agent_skeleton

    agent = build_full_agent_skeleton(dynamic_tools=True)
    if not turn_only:
        return agent._build_openai_tool_schemas()

    from gaia_agent_chat.tool_bundles import FULL_BUNDLES, FULL_CORE_TOOLS

    members = set(FULL_CORE_TOOLS)
    by_name = {bundle.name: bundle for bundle in FULL_BUNDLES}
    for name in CODING_TURN_BUNDLES:
        members |= set(by_name[name].members)
    registry = agent._tools_registry
    return agent._build_openai_tool_schemas(
        filter_to=sorted(n for n in members if n in registry)
    )


def schema_sizes(schemas: List[dict]) -> List[Tuple[str, int, int]]:
    """Return ``(tool, description chars, parameter-description chars)`` rows."""
    rows: List[Tuple[str, int, int]] = []
    for schema in schemas:
        fn = schema["function"]
        params: Dict[str, dict] = fn["parameters"]["properties"]
        rows.append(
            (
                fn["name"],
                len(fn.get("description", "")),
                sum(len(p.get("description", "")) for p in params.values()),
            )
        )
    return sorted(rows, key=lambda row: -row[1])


def _tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def report(schemas: List[dict], label: str) -> None:
    """Print the per-tool breakdown and the totals for *schemas*."""
    rows = schema_sizes(schemas)
    print(f"\n{label} — {len(rows)} tools\n")
    print(f"{'desc':>7} {'~tok':>6} {'params':>7} {'~tok':>6}  tool")
    for name, desc, params in rows:
        print(f"{desc:>7} {_tokens(desc):>6} {params:>7} {_tokens(params):>6}  {name}")

    desc_total = sum(row[1] for row in rows)
    param_total = sum(row[2] for row in rows)
    print(
        f"\nTOTAL  descriptions {desc_total} chars (~{_tokens(desc_total)} tokens)"
        f"  |  parameters {param_total} chars (~{_tokens(param_total)} tokens)"
        f"  |  combined ~{_tokens(desc_total + param_total)} tokens"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--all",
        action="store_true",
        help="Measure every registered tool instead of the coding-style turn set",
    )
    args = parser.parse_args()

    prepend_source_roots()
    if args.all:
        report(flagship_tool_schemas(turn_only=False), "Full flagship registry")
    else:
        bundles = ", ".join(CODING_TURN_BUNDLES)
        report(
            flagship_tool_schemas(turn_only=True),
            f"Coding-style turn (CORE + {bundles})",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
