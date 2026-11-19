# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI for ``gaia project trust {list|add|decline|forget}`` (and alias ``gaia trust``).

Inspect and manage whether GAIA is allowed to read project instructions
(``CLAUDE.md``, ``AGENTS.md`` and ``.claude/skills``) into the model's prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from gaia.project_trust import (
    DECLINED,
    TRUSTED,
    ProjectTrustError,
    forget_folder_trust,
    list_folder_trust,
    remember_folder_trust,
    resolve_project_folder,
    trust_store_path,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3


def _configure_trust_subparsers(trust_sub: argparse._SubParsersAction) -> None:
    p_list = trust_sub.add_parser("list", help="List saved folder trust decisions")
    p_list.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON",
    )

    p_add = trust_sub.add_parser("add", help="Trust a folder ahead of time")
    p_add.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Folder or repository root (defaults to current directory)",
    )

    p_dec = trust_sub.add_parser("decline", help="Decline a folder ahead of time")
    p_dec.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Folder or repository root (defaults to current directory)",
    )

    p_for = trust_sub.add_parser("forget", help="Remove saved folder trust answers")
    p_for.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Folder whose answer to remove (defaults to current directory)",
    )
    p_for.add_argument(
        "--missing",
        action="store_true",
        dest="missing_only",
        help="Remove answers for folders that no longer exist on disk",
    )
    p_for.add_argument(
        "--all",
        action="store_true",
        dest="all_entries",
        help="Remove all saved answers",
    )
    p_for.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation when using --all",
    )


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia project trust`` and top-level alias ``gaia trust``."""
    project_parser = subparsers.add_parser(
        "project",
        help="Manage project-level configurations and folder trust",
    )
    project_sub = project_parser.add_subparsers(
        dest="project_action", help="Project action"
    )
    p_trust = project_sub.add_parser(
        "trust",
        help="List, change and forget saved folder trust answers",
    )
    p_trust_sub = p_trust.add_subparsers(dest="trust_action", help="Trust action")
    _configure_trust_subparsers(p_trust_sub)

    trust_parser = subparsers.add_parser(
        "trust",
        help="List, change and forget saved folder trust answers (alias for 'gaia project trust')",
    )
    trust_sub = trust_parser.add_subparsers(dest="trust_action", help="Trust action")
    _configure_trust_subparsers(trust_sub)


def handle(args: argparse.Namespace) -> int:
    """Dispatch ``gaia project trust`` and ``gaia trust`` commands."""
    action = getattr(args, "action", None)
    if action == "project":
        project_action = getattr(args, "project_action", None)
        if not project_action:
            sys.stderr.write(
                "gaia project: missing action. Try 'gaia project --help'.\n"
            )
            return EXIT_USAGE
        if project_action != "trust":
            sys.stderr.write(f"gaia project: unknown action {project_action!r}\n")
            return EXIT_USAGE

    trust_action = getattr(args, "trust_action", None)
    if not trust_action:
        cmd = "gaia project trust" if action == "project" else "gaia trust"
        sys.stderr.write(f"{cmd}: missing action. Try '{cmd} --help'.\n")
        return EXIT_USAGE

    try:
        if trust_action == "list":
            return _handle_list(args)
        if trust_action == "add":
            return _handle_add(args)
        if trust_action == "decline":
            return _handle_decline(args)
        if trust_action == "forget":
            return _handle_forget(args)
    except ProjectTrustError as e:
        sys.stderr.write(f"Error: {e}\n")
        return EXIT_ERROR
    except (FileNotFoundError, ValueError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return EXIT_NOT_FOUND

    sys.stderr.write(f"gaia project trust: unknown action {trust_action!r}\n")
    return EXIT_USAGE


def _handle_list(args: argparse.Namespace) -> int:
    entries = list_folder_trust()
    if getattr(args, "as_json", False):
        records = [
            {
                "folder": path,
                "decision": entry.get("decision"),
                "at": entry.get("at"),
                "exists": os.path.exists(path),
            }
            for path, entry in sorted(entries.items())
        ]
        sys.stdout.write(json.dumps(records, indent=2) + "\n")
        return EXIT_OK

    if not entries:
        sys.stdout.write(f"No saved project trust answers in {trust_store_path()}.\n")
        return EXIT_OK

    sys.stdout.write(f"{'DECISION':<10} {'SAVED AT':<21} PATH\n")
    for path, entry in sorted(entries.items()):
        decision = entry.get("decision") or "unknown"
        at = entry.get("at") or "-"
        missing = "" if os.path.exists(path) else " [missing]"
        sys.stdout.write(f"{decision:<10} {at:<21} {path}{missing}\n")
    return EXIT_OK


def _handle_add(args: argparse.Namespace) -> int:
    from gaia.skills.human_only import started_by_agent

    # Trusting puts a folder's guidance and skills in the prompt: the user's call, never an agent's.
    if started_by_agent():
        sys.stderr.write(
            "Refused 'gaia trust add': only you can trust a folder, from your own "
            "terminal. An agent or a process it started cannot.\n"
        )
        return EXIT_USAGE
    folder = getattr(args, "folder", None)
    resolved = resolve_project_folder(folder)
    remember_folder_trust(resolved, TRUSTED)
    sys.stdout.write(f"✅ Trusted folder {resolved}\n")
    sys.stdout.write(f"   Trust store: {trust_store_path()}\n")
    return EXIT_OK


def _handle_decline(args: argparse.Namespace) -> int:
    folder = getattr(args, "folder", None)
    resolved = resolve_project_folder(folder)
    remember_folder_trust(resolved, DECLINED)
    sys.stdout.write(f"❌ Declined folder {resolved}\n")
    sys.stdout.write(f"   Trust store: {trust_store_path()}\n")
    return EXIT_OK


def _handle_forget(args: argparse.Namespace) -> int:
    all_entries = getattr(args, "all_entries", False)
    missing_only = getattr(args, "missing_only", False)
    folder = getattr(args, "folder", None)
    yes = getattr(args, "yes", False)

    if all_entries:
        if folder or missing_only:
            sys.stderr.write(
                "Cannot combine --all with a folder argument or --missing.\n"
            )
            return EXIT_USAGE
        if not yes:
            if sys.stdin is not None and sys.stdin.isatty():
                reply = (
                    input("Forget all saved project trust answers? [y/N]: ")
                    .strip()
                    .lower()
                )
                if reply not in ("y", "yes"):
                    sys.stdout.write("Cancelled.\n")
                    return EXIT_OK
            else:
                sys.stderr.write(
                    "Use --yes to confirm clearing all answers in non-interactive environments.\n"
                )
                return EXIT_USAGE
        removed = forget_folder_trust(all_entries=True)
        sys.stdout.write(f"Forgot {len(removed)} saved folder answer(s).\n")
        sys.stdout.write(f"Trust store: {trust_store_path()}\n")
        return EXIT_OK

    if missing_only:
        if folder:
            sys.stderr.write("Cannot combine --missing with a specific folder.\n")
            return EXIT_USAGE
        removed = forget_folder_trust(missing_only=True)
        if not removed:
            sys.stdout.write("No missing folders found in trust store.\n")
        else:
            sys.stdout.write(f"Forgot {len(removed)} missing folder(s):\n")
            for p in removed:
                sys.stdout.write(f"  {p}\n")
        sys.stdout.write(f"Trust store: {trust_store_path()}\n")
        return EXIT_OK

    # A folder that is gone can still have a saved answer; forget it by its path.
    if folder and not os.path.exists(os.path.expanduser(folder)):
        resolved = os.path.expanduser(folder)
    else:
        resolved = resolve_project_folder(folder)
    removed = forget_folder_trust(folder=resolved)
    if not removed:
        sys.stdout.write(f"No saved answer for {resolved} in {trust_store_path()}.\n")
    else:
        sys.stdout.write(f"Forgot trust answer for {resolved}\n")
        sys.stdout.write(f"Trust store: {trust_store_path()}\n")
    return EXIT_OK
