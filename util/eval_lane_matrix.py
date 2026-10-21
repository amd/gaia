# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Validate ``eval/ci_lanes.json`` and print the eval_flagship.yml lane matrix.

    python util/eval_lane_matrix.py >> "$GITHUB_OUTPUT"
    python util/eval_lane_matrix.py --categories "tool_selection memory" >> "$GITHUB_OUTPUT"

``--categories`` runs a subset (eval_flagship_gemma_stx.yml): the whole map is
still validated, every requested category must belong to a lane, and the
subset is regrouped by the environment it needs into at most three lanes,
``core``, ``memory`` and ``gaia``.

Standard library only: the lanes job runs before any venv exists. Each lane's
environment is derived from its categories rather than stored as a second
field, so moving a category between lanes cannot leave a flag pointing at the
wrong one:

- ``memory`` runs with the store live and its admin and MCP read tools on.
- A ``gaia_*`` lane runs the way ``test_gaia_agent_eval.yml`` runs those
  categories, and the way their baselines were captured: fixtures staged,
  fixture server and hub up, memory live with admin seeding, confirmation-gated
  tools auto-approved.
- Everything else runs with memory off, so one scenario cannot change the next
  one's score.

Values are emitted as literal env strings, not booleans: a GitHub expression of
the form ``flag and '0' or '1'`` reads '0' as falsy and falls through to '1'.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read the runner's screen/clipboard into a scorecard that uploads publicly.
SCREEN_READING = frozenset({"vision", "web_system"})

GAIA_PREFIX = "gaia_"


class LaneMapError(ValueError):
    """The lane map is unusable; the message is a GitHub ``::error::`` body."""


def lane_env(categories: list[str]) -> dict[str, str]:
    """The per-lane environment a lane's categories need."""
    gaia = [c for c in categories if c.startswith(GAIA_PREFIX)]
    if gaia and len(gaia) != len(categories):
        raise LaneMapError(
            f"Lane mixes gaia_* and other categories ({categories}). The gaia_* "
            "categories need fixtures, auto-approval and a live memory store that "
            "the others must not run with. Put them in separate lanes."
        )
    if "memory" in categories:
        return {
            "memory_disabled": "0",
            "memory_admin": "1",
            "memory_mcp": "1",
            "gaia_fixtures": "0",
        }
    if gaia:
        return {
            "memory_disabled": "0",
            "memory_admin": "1",
            "memory_mcp": "0",
            "gaia_fixtures": "1",
        }
    return {
        "memory_disabled": "1",
        "memory_admin": "0",
        "memory_mcp": "0",
        "gaia_fixtures": "0",
    }


def build_matrix(doc: dict, on_disk: set[str], lanes_file: str) -> list[dict]:
    """Validate the lane map against the scenario directories and build the matrix."""
    lanes = doc.get("lanes") or []
    excluded = doc.get("excluded") or {}
    if not lanes:
        raise LaneMapError(
            f"{lanes_file} defines no lanes, so no scenario would run and this "
            "gate would go green having measured nothing."
        )

    claimed = {c for lane in lanes for c in lane["categories"]}
    leaked = sorted(SCREEN_READING & claimed)
    if leaked:
        raise LaneMapError(
            f"{leaked} is assigned to a CI lane. These read this runner's "
            "clipboard, windows and screen, and every lane uploads its scorecard "
            f"and traces as a PUBLIC artifact. Move them back to `excluded` in "
            f"{lanes_file}."
        )

    orphans = sorted(on_disk - claimed - set(excluded))
    if orphans:
        raise LaneMapError(
            f"No lane runs {orphans}, and {lanes_file} gives no reason. These "
            "scenarios would stop being measured with every lane still green. Add "
            "each to a lane's `categories`, or to `excluded` with why it cannot "
            "run in CI."
        )

    missing = sorted(claimed - on_disk)
    if missing:
        raise LaneMapError(
            f"{lanes_file} names {missing}, which has no directory under "
            "eval/scenarios/. `gaia eval agent --category` on it measures nothing "
            "and reports success."
        )

    return [
        {
            "lane": lane["lane"],
            "categories": " ".join(lane["categories"]),
            **lane_env(lane["categories"]),
        }
        for lane in lanes
    ]


def subset_matrix(
    doc: dict, on_disk: set[str], lanes_file: str, categories: list[str]
) -> list[dict]:
    """A matrix for *categories* only, grouped by the environment each needs.

    ``memory`` gets its own lane rather than turning memory on for the plain
    categories beside it, and the gaia_* categories share one lane because they
    share fixtures. Order follows the request so the first lane is the one asked
    for first.
    """
    build_matrix(doc, on_disk, lanes_file)
    wanted = list(dict.fromkeys(c for c in categories if c))
    if not wanted:
        raise LaneMapError(
            "No categories were requested, so nothing would run and the eval "
            "would go green having measured nothing. Name at least one."
        )
    claimed = {c for lane in doc["lanes"] for c in lane["categories"]}
    excluded = doc.get("excluded") or {}
    unknown = [c for c in wanted if c not in claimed]
    if unknown:
        reasons = "; ".join(
            f"{c}: {excluded[c]}" if c in excluded else f"{c}: not in any lane"
            for c in unknown
        )
        raise LaneMapError(
            f"Cannot run {unknown} ({reasons}). Pick categories from the lanes in "
            f"{lanes_file}."
        )
    groups: dict[str, list[str]] = {}
    for category in wanted:
        if category == "memory":
            name = "memory"
        elif category.startswith(GAIA_PREFIX):
            name = "gaia"
        else:
            name = "core"
        groups.setdefault(name, []).append(category)
    return [
        {"lane": name, "categories": " ".join(cats), **lane_env(cats)}
        for name, cats in groups.items()
    ]


def categories_on_disk(scenarios: Path) -> set[str]:
    return {p.name for p in scenarios.iterdir() if p.is_dir() and any(p.glob("*.yaml"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--categories",
        default=None,
        help="Space-separated subset to run instead of every lane",
    )
    args = parser.parse_args(argv or [])
    lanes_file = os.environ.get("EVAL_LANES_FILE", "eval/ci_lanes.json")
    path = REPO_ROOT / lanes_file
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"::error::Could not read the lane map at {path}: {exc}. Nothing "
            "downstream knows which categories to run, so the eval would measure "
            "nothing and pass. Fix the JSON.",
            file=sys.stderr,
        )
        return 1
    on_disk = categories_on_disk(REPO_ROOT / "eval" / "scenarios")
    try:
        if args.categories is None:
            include = build_matrix(doc, on_disk, lanes_file)
        else:
            include = subset_matrix(doc, on_disk, lanes_file, args.categories.split())
    except LaneMapError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"matrix={json.dumps({'include': include})}")
    excluded = doc.get("excluded") or {}
    print(
        f"Lanes: {', '.join(l['lane'] for l in include)}; "
        f"excluded: {', '.join(sorted(excluded)) or 'none'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
