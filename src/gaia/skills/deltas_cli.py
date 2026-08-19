# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia skill deltas`` — inspect and control what an agent LEARNED (#2674).

The legibility surface for adaptive skills. A learned change the user cannot
see, explain, or switch off is a behaviour change they did not consent to, so
this command answers four questions and nothing else: what was learned, why,
what it costs, and how to undo it.

Lives outside :mod:`gaia.skills.cli` to keep that module from growing a seventh
concern; ``cli.py`` wires in the parser and one dispatch entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

EXIT_OK = 0
EXIT_USAGE = 2


def add_deltas_parser(sub: Any) -> None:
    """Register the ``deltas`` subcommand on ``gaia skill``'s subparsers."""
    p = sub.add_parser(
        "deltas",
        help="Show, approve, or revert what an agent LEARNED about a skill",
    )
    p.add_argument("name", help="Skill name (== its directory name)")
    p.add_argument(
        "--scope",
        default=None,
        help="Agent scope the changes belong to (default: every scope)",
    )
    p.add_argument(
        "--db", default=None, help="Memory database (default: ~/.gaia/memory.db)"
    )
    p.add_argument(
        "--pending",
        action="store_true",
        help="Show staged changes awaiting approval instead of active ones",
    )
    p.add_argument(
        "--diff",
        action="store_true",
        help="Unified diff of the shipped skill vs the one this agent runs",
    )
    p.add_argument("--approve", metavar="ID", help="Approve one staged change")
    p.add_argument(
        "--revert",
        metavar="ID",
        help="Archive one learned change (kept for inspection, never deleted)",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Archive EVERY learned change for this skill, back to the shipped one",
    )
    p.add_argument(
        "--drop-section",
        metavar="SLUG",
        dest="drop_section",
        help="Remove a section this agent never needs (the model cannot do this)",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON instead of text"
    )


def _as_delta(row: Dict[str, Any]):
    from gaia.agents.base.skill_deltas import SkillDelta

    return SkillDelta(
        id=row["id"],
        base_name=row["base_name"],
        scope=row["scope"],
        kind=row["kind"],
        anchor_section=row["anchor_section"],
        anchor_digest=row["anchor_digest"],
        payload=row["payload"],
        provenance=row["provenance"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
    )


def handle_deltas(args: argparse.Namespace, skill) -> int:
    """Run the ``deltas`` subcommand against an already-loaded *skill*."""
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.agents.base.skill_deltas import (
        KIND_DROP_SECTION,
        preview_diff,
        resolve_skill_body,
    )
    from gaia.skills.sections import find_section, parse_sections

    base_body = skill.body or ""
    store = MemoryStore(db_path=Path(args.db) if args.db else None)

    def rows(status: Optional[str] = None) -> List[Dict[str, Any]]:
        return store.search_deltas(base_name=args.name, scope=args.scope, status=status)

    # --- mutating actions -------------------------------------------------
    if args.approve:
        if store.approve_delta(args.approve):
            print(f"Approved {args.approve} — it applies from the next session.")
            return EXIT_OK
        sys.stderr.write(
            f"gaia skill deltas: {args.approve!r} is not a staged change on "
            f"{args.name!r}. Run with --pending to see what is awaiting "
            "approval.\n"
        )
        return EXIT_USAGE

    if args.revert:
        if store.archive_delta(args.revert):
            print(
                f"Reverted {args.revert} — archived, not deleted. "
                f"`gaia skill deltas {args.name} --pending` still lists it."
            )
            return EXIT_OK
        sys.stderr.write(
            f"gaia skill deltas: no learned change with id {args.revert!r} on "
            f"{args.name!r}.\n"
        )
        return EXIT_USAGE

    if args.reset:
        current = rows()
        for row in current:
            store.archive_delta(row["id"])
        print(
            f"{args.name!r} is back to the shipped skill — archived "
            f"{len(current)} learned change(s). Nothing was deleted."
        )
        return EXIT_OK

    if args.drop_section:
        sections = parse_sections(base_body)
        target = find_section(sections, args.drop_section)
        if target is None:
            sys.stderr.write(
                f"gaia skill deltas: {args.name!r} has no section "
                f"{args.drop_section!r}. Sections are: "
                f"{', '.join(s.slug for s in sections)}.\n"
            )
            return EXIT_USAGE
        if not args.scope:
            sys.stderr.write(
                "gaia skill deltas: --drop-section needs --scope, so the "
                "removal attaches to one agent rather than silently to none. "
                "The scope is the agent id, e.g. --scope GaiaAgent.\n"
            )
            return EXIT_USAGE
        delta_id = store.put_delta(
            base_name=args.name,
            scope=args.scope,
            kind=KIND_DROP_SECTION,
            anchor_section=args.drop_section,
            anchor_digest=target.digest,
            payload={},
            provenance={"source": "user_instruction", "via": "gaia skill deltas"},
            base_root=skill.root,
            base_version=skill.version,
        )
        store.approve_delta(delta_id)
        print(
            f"Removed section {args.drop_section!r} from {args.name!r} for "
            f"{args.scope} ({delta_id}). The shipped skill file is unchanged."
        )
        return EXIT_OK

    # --- read-only views --------------------------------------------------
    listed = rows(status="staged" if args.pending else "active")
    resolved = resolve_skill_body(base_body, [_as_delta(r) for r in rows("active")])

    if args.as_json:
        print(
            json.dumps(
                {
                    "skill": args.name,
                    "authored_tokens": resolved.base_tokens,
                    "effective_tokens": resolved.resolved_tokens,
                    "token_delta": resolved.token_delta,
                    "deltas": listed,
                    "notes": [
                        {
                            "delta": n.delta_id,
                            "section": n.section,
                            "outcome": n.outcome,
                            "detail": n.detail,
                        }
                        for n in resolved.notes
                    ],
                },
                indent=2,
                default=str,
            )
        )
        return EXIT_OK

    if args.diff:
        diff = preview_diff(base_body, [_as_delta(r) for r in listed])
        print(
            diff
            if diff.strip()
            else "(no difference — this agent runs the skill exactly as shipped)"
        )
        return EXIT_OK

    label = "staged, awaiting approval" if args.pending else "active"
    print(
        f"{skill.name}  {skill.version or '(unversioned)'}  "
        f"— learned changes ({label})"
    )
    if not listed:
        print("  none — this agent runs the skill exactly as shipped.")
        return EXIT_OK

    for row in listed:
        provenance = row["provenance"] or {}
        print(f"  {row['id']}")
        print(f"    change  : {row['kind']} on section '{row['anchor_section']}'")
        print(f"    scope   : {row['scope']}")
        print(f"    why     : {provenance.get('reason') or '(no reason recorded)'}")
        print(f"    source  : {provenance.get('source', 'unknown')}")
        print(f"    learned : {row['created_at']}")
        if row["approved_at"]:
            print(f"    approved: {row['approved_at']}")

    print()
    print(
        f"  effective skill: {resolved.resolved_tokens} tokens vs "
        f"{resolved.base_tokens} as shipped ({resolved.token_delta:+d})"
    )
    for note in resolved.notes:
        if note.outcome != "applied":
            print(f"  ! {note.outcome} on '{note.section}': {note.detail}")
    print()
    print(f"  see the diff : gaia skill deltas {args.name} --diff")
    print(f"  revert one   : gaia skill deltas {args.name} --revert <id>")
    print(f"  revert all   : gaia skill deltas {args.name} --reset")
    return EXIT_OK
