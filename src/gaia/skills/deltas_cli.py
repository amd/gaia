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

#: Everything printed here is model-authored. Escape sequences in it could
#: repaint the diff the user is reading to decide whether to approve it, so
#: strip control characters on the way out — tabs and newlines excepted.
_KEEP = {"\n", "\t"}


def _sanitized(text: str) -> str:
    """Model-authored text, safe to print to a terminal.

    Escapes wide as ``\\uXXXX`` so a zero-width space cannot be misread as a
    space followed by digits.
    """
    return "".join(
        (
            ch
            if ch in _KEEP or ch.isprintable()
            else (f"\\x{ord(ch):02x}" if ord(ch) <= 0xFF else f"\\u{ord(ch):04x}")
        )
        for ch in text
    )


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
    view = p.add_mutually_exclusive_group()
    view.add_argument(
        "--pending",
        action="store_true",
        help="Show staged changes awaiting approval instead of active ones",
    )
    view.add_argument(
        "--archived",
        action="store_true",
        help="Show changes reverted with --revert or --reset",
    )
    p.add_argument(
        "--diff",
        action="store_true",
        help="Unified diff of the shipped skill vs the one this agent runs",
    )
    # One mutation per invocation. Without this argparse accepts
    # `--approve X --revert Y`, runs the first, and exits 0 having silently
    # dropped the second.
    action = p.add_mutually_exclusive_group()
    action.add_argument("--approve", metavar="ID", help="Approve one staged change")
    action.add_argument(
        "--revert",
        metavar="ID",
        help="Archive one learned change (listed by --archived, never deleted)",
    )
    action.add_argument(
        "--reset",
        action="store_true",
        help="Archive EVERY learned change for this skill, back to the shipped one",
    )
    action.add_argument(
        "--drop-section",
        metavar="SLUG",
        dest="drop_section",
        help="Remove a section this agent never needs (the model cannot do this)",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit JSON instead of text"
    )


def handle_deltas(args: argparse.Namespace, skill) -> int:
    """Run the ``deltas`` subcommand against an already-loaded *skill*."""
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.agents.base.skill_deltas import (
        KIND_DROP_SECTION,
        STATUS_ACTIVE,
        STATUS_ARCHIVED,
        STATUS_STAGED,
        DeltaRefused,
        SkillDelta,
        approve_delta,
        preview_diff,
        resolve_skill_body,
        supersession_key,
    )
    from gaia.skills.sections import find_section, parse_sections

    _as_delta = SkillDelta.from_row

    base_body = skill.body or ""
    store = MemoryStore(db_path=Path(args.db) if args.db else None)

    def rows(status: Optional[str] = None) -> List[Dict[str, Any]]:
        # limit=None: this command IS the legibility surface, so a silent
        # ceiling here would hide the very rows the user came to audit — and
        # --reset would report a completion it did not perform.
        return store.search_deltas(
            base_name=args.name, scope=args.scope, status=status, limit=None
        )

    # A delta anchors to the exact section text it was written against. If that
    # text has moved on — the skill was updated, or this command resolved a
    # different copy of it than the agent runs — resolution flags the delta
    # `stale` and keeps the authored section. Approving it would then hand back
    # a receipt for a change that can never apply, so check the anchor here.
    def stale_anchor(row: Dict[str, Any]) -> Optional[str]:
        section = find_section(parse_sections(base_body), row["anchor_section"])
        if section is not None and section.digest == row["anchor_digest"]:
            return None
        moved = "is no longer in" if section is None else "has changed in"
        return (
            f"gaia skill deltas: {row['id']} was learned against a "
            f"{row['anchor_section']!r} section that {moved} "
            f"{args.name} {skill.version or '(unversioned)'} as resolved here "
            f"(from the {skill.root or 'unknown'} root). Approving it would "
            "change nothing. Ask the agent to re-learn the correction against "
            "the current text.\n"
        )

    # Before the mutating actions, so --reset cannot cross scopes unannounced.
    # Archived rows are excluded: a scope the user already retired is not a
    # combination anyone is running.
    all_live = rows()
    merged_scopes = sorted(
        {r["scope"] for r in all_live if r["status"] != STATUS_ARCHIVED}
    )
    if not args.scope and len(merged_scopes) > 1:
        sys.stderr.write(
            f"gaia skill deltas: {args.name!r} has changes from "
            f"{len(merged_scopes)} agent scopes ({', '.join(merged_scopes)}). "
            "No single agent runs this combination — pass --scope <agent> for "
            "one agent's view.\n"
        )

    # --- mutating actions -------------------------------------------------
    if args.approve:
        # Only when the id resolves — an unknown one deserves the "not a staged
        # change" error below, not a stale-anchor one it did not cause.
        target = next((r for r in all_live if r["id"] == args.approve), None)
        if target is not None:
            stale = stale_anchor(target)
            if stale:
                sys.stderr.write(stale)
                return EXIT_USAGE
        # Retires the live correction this one replaces, which is deferred to
        # here: until now the replacement had no consent behind it.
        try:
            approved = approve_delta(
                store,
                args.approve,
                base_body,
                base_name=args.name,
                scope=args.scope,
            )
        except DeltaRefused as exc:
            sys.stderr.write(f"gaia skill deltas: {exc}\n")
            return EXIT_USAGE
        if approved is not None:
            print(f"Approved {args.approve} — it applies from the next launch.")
            return EXIT_OK
        sys.stderr.write(
            f"gaia skill deltas: {args.approve!r} is not a staged change "
            f"awaiting approval on {args.name!r}. It may already be approved, "
            "or superseded by a newer one. Run with --pending to see what is "
            "actually awaiting approval.\n"
        )
        return EXIT_USAGE

    if args.revert:
        # base_name/scope bound: an id belonging to another skill must not be
        # archived under the name the user typed and reported as that skill's.
        if store.archive_delta(args.revert, base_name=args.name, scope=args.scope):
            print(
                f"Reverted {args.revert} — archived, not deleted, and it stops "
                f"applying from the next launch. `gaia skill deltas "
                f"{args.name} --archived` lists it."
            )
            return EXIT_OK
        sys.stderr.write(
            f"gaia skill deltas: no live learned change with id "
            f"{args.revert!r} on {args.name!r}"
            f"{f' in scope {args.scope!r}' if args.scope else ''}. It may "
            "belong to another skill, or already be reverted — "
            f"`gaia skill deltas {args.name} --archived` lists what is.\n"
        )
        return EXIT_USAGE

    if args.reset:
        # Count what this run actually retired: an unfiltered read re-counts
        # rows archived by an earlier --reset, so the tally would overstate.
        # Anything not already archived is in scope, so a status added later
        # cannot silently escape a reset.
        archived = [
            row["id"]
            for row in all_live
            if row["status"] != STATUS_ARCHIVED
            and store.archive_delta(row["id"], base_name=args.name, scope=args.scope)
        ]
        print(
            f"{args.name!r} is back to the shipped skill from the next launch "
            f"— archived {len(archived)} learned change(s). Nothing was "
            f"deleted; `gaia skill deltas {args.name} --archived` lists them."
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
        # The user typed this command, so consent is on record already.
        try:
            approved = approve_delta(
                store, delta_id, base_body, base_name=args.name, scope=args.scope
            )
        except DeltaRefused as exc:
            sys.stderr.write(f"gaia skill deltas: {exc}\n")
            return EXIT_USAGE
        if approved is None:
            sys.stderr.write(
                f"gaia skill deltas: stored {delta_id} but could not activate "
                "it. Approve it explicitly with "
                f"`gaia skill deltas {args.name} --approve {delta_id}`.\n"
            )
            return EXIT_USAGE
        # Name the copy this resolved: a removal anchors to one exact section
        # text, so if the agent runs a different copy of the skill it will not
        # apply, and the version+root is what tells the user which they got.
        print(
            f"Removed section {args.drop_section!r} from {args.name} "
            f"{skill.version or '(unversioned)'} (the "
            f"{skill.root or 'unknown'} copy) for {args.scope} ({delta_id}). "
            "The shipped skill file is unchanged."
        )
        return EXIT_OK

    # --- read-only views --------------------------------------------------
    if args.pending:
        view_status = STATUS_STAGED
    elif args.archived:
        view_status = STATUS_ARCHIVED
    else:
        view_status = STATUS_ACTIVE
    active = rows(status=STATUS_ACTIVE)
    listed = active if view_status == STATUS_ACTIVE else rows(status=view_status)
    resolved = resolve_skill_body(base_body, [_as_delta(r) for r in active])

    # Under --pending the question is "what would approving give me", so the
    # already-active deltas belong in the diff too — minus the ones approval
    # would retire. Showing staged-only renders a body no agent would ever run,
    # and keeping the superseded actives lets the outgoing text win the preview
    # of the change that replaces it. Either way the user consents to the wrong
    # thing, which is the one failure a consent view may not have.
    if args.pending:
        retired = {supersession_key(_as_delta(r)) for r in listed}
        survivors = [r for r in active if supersession_key(_as_delta(r)) not in retired]
        diff_deltas = survivors + listed
    else:
        diff_deltas = listed

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
        diff = preview_diff(base_body, [_as_delta(r) for r in diff_deltas])
        print(
            _sanitized(diff)
            if diff.strip()
            else "(no difference — this agent runs the skill exactly as shipped)"
        )
        return EXIT_OK

    if args.pending:
        label = "staged, awaiting approval"
    elif args.archived:
        label = "archived — no longer applied"
    else:
        label = "active"
    print(
        f"{skill.name}  {skill.version or '(unversioned)'}  "
        f"— learned changes ({label})"
    )

    # Surface the queue on the view users actually type. Staging is the whole
    # consent mechanism, so it must not take a flag to discover it exists.
    def _pending_hint() -> None:
        if args.pending:
            return
        waiting = len(rows(status=STATUS_STAGED))
        if waiting:
            print(
                f"  ({waiting} change(s) awaiting your approval — "
                f"`gaia skill deltas {args.name} --pending --diff`)"
            )

    if not listed:
        if args.archived:
            print("  none — nothing has been reverted for this skill.")
        else:
            print("  none — this agent runs the skill exactly as shipped.")
        _pending_hint()
        return EXIT_OK

    for row in listed:
        provenance = row["provenance"] if isinstance(row["provenance"], dict) else {}
        reason = provenance.get("reason") or "(no reason recorded)"
        print(f"  {row['id']}")
        # anchor_section is a slug from the SKILL.md, which for a hub-installed
        # skill is third-party text.
        section = _sanitized(str(row["anchor_section"]))
        print(f"    change  : {row['kind']} on section '{section}'")
        print(f"    scope   : {row['scope']}")
        print(f"    why     : {_sanitized(str(reason))}")
        print(f"    source  : {_sanitized(str(provenance.get('source', 'unknown')))}")
        print(f"    learned : {row['created_at']}")
        if row["approved_at"]:
            print(f"    approved: {row['approved_at']}")

    print()
    _pending_hint()
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
