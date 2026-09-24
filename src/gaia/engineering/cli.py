# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Explicit developer commands for pairing, sharing and recovering handoffs."""

from __future__ import annotations

import json
from pathlib import Path


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "engineering", help="Developer-only coding-app handoffs"
    )
    parser.add_argument(
        "--developer-mode",
        action="store_true",
        help="Explicitly enable engineering capabilities",
    )
    parser.add_argument(
        "--root", type=Path, help="Separate engineering profile directory"
    )
    commands = parser.add_subparsers(dest="engineering_action", required=True)
    commands.add_parser("setup", help="Prepare the official repository cache")
    connect = commands.add_parser(
        "connect", help="Write a private MCP configuration for a coding app"
    )
    connect.add_argument("backend", choices=["claude", "codex"])
    share = commands.add_parser(
        "share", help="Review and share an explicit text snapshot"
    )
    share.add_argument("backend", choices=["claude", "codex"])
    share.add_argument("--summary", required=True)
    share.add_argument("--context-file", type=Path, required=True)
    for action in ("status", "revoke", "approve-code", "open", "append"):
        child = commands.add_parser(action)
        child.add_argument("job_id")
        if action == "append":
            child.add_argument("--context-file", type=Path, required=True)


def _consent(backend: str, content: str) -> None:
    print(
        f"Share with {backend} and its configured provider. The native app retains its existing filesystem/network permissions. Shared data cannot be recalled."
    )
    print("Review this snapshot (common credentials will be redacted):\n" + content)
    if input("Type SHARE to approve this snapshot: ").strip() != "SHARE":
        raise PermissionError("Sharing declined; no data sent")


def run(args) -> None:
    from gaia.engineering.service import EngineeringService
    from gaia.engineering.store import clean_context

    service = EngineeringService(args.root, developer_mode=args.developer_mode)
    action = args.engineering_action
    if action == "setup":
        result = service.setup()
    elif action == "connect":
        result = service.connection(args.backend)
    elif action == "share":
        content = clean_context(args.context_file.read_text(encoding="utf-8"))
        summary = clean_context(args.summary)
        _consent(args.backend, summary + "\n" + content)
        result = service.share(args.backend, summary, content)
    elif action == "append":
        content = clean_context(args.context_file.read_text(encoding="utf-8"))
        _consent(service.status(args.job_id)["backend"], content)
        result = service.append(args.job_id, content)
    elif action == "approve-code":
        status = service.status(args.job_id)
        print(
            json.dumps(
                {"scope": status["summary"], "diagnosis": status["diagnosis"]}, indent=2
            )
        )
        if input("Type CODE to approve a worktree for this scope: ").strip() != "CODE":
            raise PermissionError("Code scope declined")
        result = service.approve_code(args.job_id, expected_revision=status["revision"])
    else:
        result = getattr(service, action)(args.job_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
