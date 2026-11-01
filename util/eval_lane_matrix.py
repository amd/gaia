# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Validate ``eval/ci_lanes.json`` and print the eval_flagship.yml lane matrix.

    python util/eval_lane_matrix.py >> "$GITHUB_OUTPUT"

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

``EVAL_ONE_CATEGORY_PER_LANE=1`` gives each selected category its own lane
(eval_flagship_gemma_stx.yml, whose small pool runs lanes on separate machines).

A dispatch can narrow the run with ``EVAL_SELECT_CATEGORIES`` and
``EVAL_SELECT_SCENARIOS`` (comma-separated; blank = all). The matrix then holds
only the lanes that run something, each lane only its selected categories, and
``scenarios`` names the ids per category. An unknown or unrunnable id fails here,
on a hosted runner, before any lane takes a lab machine. A tag run sets neither,
so its matrix is exactly the full one.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read the runner's screen/clipboard into a scorecard that uploads publicly.
SCREEN_READING = frozenset({"vision", "web_system"})

GAIA_PREFIX = "gaia_"

#: Tags a gaia_* lane passes to ``--exclude-tag`` in eval_flagship.yml's run
#: step. A test keeps the two in step.
GAIA_LANE_EXCLUDE_TAGS = ("live", "local_blocked_win_shim")

#: The outcomes job (`gaia eval tasks`) is not a scenario category, but a
#: targeted dispatch can still ask for it by this name.
OUTCOMES = "outcomes"


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


def categories_on_disk(scenarios: Path) -> set[str]:
    return {p.name for p in scenarios.iterdir() if p.is_dir() and any(p.glob("*.yaml"))}


def _top_level(text: str, key: str) -> str | None:
    match = re.search(rf"^{key}:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def scenario_index(scenarios: Path) -> dict[str, dict]:
    """``{id: {"category", "tags"}}`` for every scenario YAML, without PyYAML.

    The lanes job runs before any venv exists. Scenario files keep ``id``,
    ``category`` and a flow-style ``tags`` list at top level; a test checks this
    reading against ``yaml.safe_load`` for every file.
    """
    index = {}
    for path in sorted(scenarios.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        sid = _unquote(_top_level(text, "id") or "")
        if not sid:
            continue
        raw_tags = _top_level(text, "tags") or ""
        tags = []
        if raw_tags.startswith("[") and raw_tags.endswith("]"):
            tags = [_unquote(t) for t in raw_tags[1:-1].split(",") if t.strip()]
        index[sid] = {
            "category": _unquote(_top_level(text, "category") or path.parent.name),
            "tags": tags,
        }
    return index


def parse_list(text: str | None) -> list[str]:
    """Split a dispatch input on commas or whitespace, keeping order, no repeats."""
    out = []
    for part in re.split(r"[,\s]+", text or ""):
        if part and part not in out:
            out.append(part)
    return out


def select(
    include: list[dict],
    index: dict[str, dict],
    categories: list[str],
    scenarios: list[str],
    excluded: dict,
) -> dict:
    """Narrow a full matrix to a dispatch's selection, or refuse it.

    Returns ``include`` (lanes that run something), ``targeted``, ``outcomes``
    (whether the outcomes job runs) and ``description`` (one line for humans).
    Every refusal names the valid ids, so a typo costs one re-dispatch.
    """
    if not categories and not scenarios:
        return {
            "include": include,
            "targeted": False,
            "outcomes": True,
            "description": "full run: every lane, category and scenario",
        }

    lane_of = {c: lane["lane"] for lane in include for c in lane["categories"].split()}
    problems = []

    bad_category = False
    for category in categories:
        if category == OUTCOMES or category in lane_of:
            continue
        if category in excluded:
            problems.append(
                f"category '{category}' never runs in CI: {excluded[category]}"
            )
        else:
            problems.append(f"'{category}' is not a category")
            bad_category = True
    if bad_category:
        problems.append("Valid categories: " + ", ".join(sorted(lane_of) + [OUTCOMES]))

    picked: dict[str, list[str]] = {}
    unknown = []
    for sid in scenarios:
        meta = index.get(sid)
        if meta is None:
            unknown.append(sid)
            continue
        category = meta["category"]
        if category not in lane_of:
            why = excluded.get(category, "no lane runs it")
            problems.append(
                f"scenario '{sid}' is in '{category}', which never runs in CI: {why}"
            )
            continue
        if categories and category not in categories:
            problems.append(
                f"scenario '{sid}' is in '{category}', which the categories input "
                "leaves out. Add that category, or clear the categories input"
            )
            continue
        if category.startswith(GAIA_PREFIX):
            hit = sorted(set(meta["tags"]) & set(GAIA_LANE_EXCLUDE_TAGS))
            if hit:
                problems.append(
                    f"scenario '{sid}' carries {hit}, which the gaia_* lanes "
                    "exclude, so it would never run"
                )
                continue
        picked.setdefault(category, []).append(sid)
    if unknown:
        problems.append(f"not scenario ids: {', '.join(unknown)}")
        runnable = sorted(
            sid
            for sid, meta in index.items()
            if meta["category"] in lane_of
            and (not categories or meta["category"] in categories)
        )
        problems.append("Valid scenario ids: " + ", ".join(runnable))
    if problems:
        raise LaneMapError(
            "The dispatch selection cannot run as asked. " + " | ".join(problems)
        )

    wanted = list(picked) if scenarios else [c for c in categories if c != OUTCOMES]
    lanes = []
    for lane in include:
        mine = [c for c in lane["categories"].split() if c in wanted]
        if not mine:
            continue
        entry = {**lane, "categories": " ".join(mine), **lane_env(mine)}
        if scenarios:
            entry["scenarios"] = " ".join(f"{c}={','.join(picked[c])}" for c in mine)
        lanes.append(entry)

    outcomes = OUTCOMES in categories
    if scenarios:
        count = sum(len(ids) for ids in picked.values())
        what = f"{count} scenario(s) in {len(picked)} category(ies)"
    else:
        what = f"every scenario in {', '.join(wanted) or 'no scenario category'}"
    lane_names = ", ".join(lane["lane"] for lane in lanes) or "none"
    return {
        "include": lanes,
        "targeted": True,
        "outcomes": outcomes,
        "description": (
            f"TARGETED run: {what}; lanes {lane_names}; outcomes job "
            + ("runs" if outcomes else "skipped")
        ),
    }


def one_category_per_lane(include: list[dict]) -> list[dict]:
    """Split every lane into one lane per category, named after the category.

    For a small pool where lanes run in parallel across machines: two slow
    categories that share a lane in ``ci_lanes.json`` would otherwise run one
    after the other on one machine. The environment is re-derived per category,
    and a targeted lane's ``scenarios`` follow their category.
    """
    split = []
    for lane in include:
        picked = {}
        for pair in (lane.get("scenarios") or "").split():
            category, ids = pair.split("=", 1)
            picked[category] = ids
        for category in lane["categories"].split():
            entry = {
                **lane,
                "lane": category,
                "categories": category,
                **lane_env([category]),
            }
            if "scenarios" in lane:
                entry["scenarios"] = f"{category}={picked[category]}"
            split.append(entry)
    return split


def main() -> int:
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
    scenarios_dir = REPO_ROOT / "eval" / "scenarios"
    excluded = doc.get("excluded") or {}
    try:
        include = build_matrix(doc, categories_on_disk(scenarios_dir), lanes_file)
        chosen = select(
            include,
            scenario_index(scenarios_dir),
            parse_list(os.environ.get("EVAL_SELECT_CATEGORIES")),
            parse_list(os.environ.get("EVAL_SELECT_SCENARIOS")),
            excluded,
        )
    except LaneMapError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if os.environ.get("EVAL_ONE_CATEGORY_PER_LANE") == "1":
        before = ", ".join(lane["lane"] for lane in chosen["include"]) or "none"
        chosen["include"] = one_category_per_lane(chosen["include"])
        after = ", ".join(lane["lane"] for lane in chosen["include"]) or "none"
        chosen["description"] = chosen["description"].replace(
            f"lanes {before};", f"lanes {after} (one per category);"
        )
    print(f"matrix={json.dumps({'include': chosen['include']})}")
    print(f"has_lanes={'true' if chosen['include'] else 'false'}")
    print(f"targeted={'true' if chosen['targeted'] else 'false'}")
    print(f"outcomes={'true' if chosen['outcomes'] else 'false'}")
    print(f"selection={chosen['description']}")
    lane_names = ", ".join(lane["lane"] for lane in chosen["include"]) or "none"
    print(
        f"Lanes: {lane_names}; excluded: {', '.join(sorted(excluded)) or 'none'}. "
        f"{chosen['description']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
