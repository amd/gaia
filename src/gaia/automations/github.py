# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GitHub reads automations use: a token, and a branch's CI verdict."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

API = "https://api.github.com"


def github_token() -> Optional[str]:
    """``GITHUB_TOKEN``, else the gh CLI's login, else ``None`` (anonymous)."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            [gh, "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,  # a nonzero exit means "not logged in", handled below
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Could not ask gh for its token, watching anonymously: %s", e)
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


#: A run that ended these ways says nothing about the branch's health.
_NOT_A_VERDICT = frozenset(
    {"cancelled", "skipped", "neutral", "stale", "action_required"}
)


def ci_failures(repo: str, branch: str, token: Optional[str]) -> List[str]:
    """Workflows whose latest finished run on *branch* failed, sorted.

    Only pushes to the branch in *repo* itself: a fork's PR from its own
    ``main`` is not this repo's main. Runs are grouped by workflow, not by run
    name, which a workflow can set per run. Cancelled and skipped runs are not
    a verdict, so a push that cancels a run never reads as a break or a fix.
    """
    import httpx

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GAIA-discord-watch",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(
        f"{API}/repos/{repo.strip().strip('/')}/actions/runs",
        params={
            "branch": branch,
            "event": "push",
            "status": "completed",
            "per_page": "100",
        },
        headers=headers,
        timeout=30,
    )
    if response.status_code in (403, 429):
        raise RuntimeError(
            f"GitHub rate-limited the CI check (HTTP {response.status_code}). Sign "
            "in to the gh CLI so GAIA checks with your account's higher limit."
        )
    response.raise_for_status()
    full_name = repo.strip().strip("/").lower()
    latest: Dict[Any, Dict[str, Any]] = {}
    for run in response.json().get("workflow_runs", []):
        if run.get("conclusion") in _NOT_A_VERDICT:
            continue
        head = (run.get("head_repository") or {}).get("full_name") or full_name
        if str(head).lower() != full_name:
            continue
        key = run.get("workflow_id") or run.get("path") or run.get("name")
        if key not in latest or run.get("created_at", "") > latest[key].get(
            "created_at", ""
        ):
            latest[key] = run
    return sorted(
        {
            str(run.get("name") or run.get("path") or "workflow")
            for run in latest.values()
            if run.get("conclusion") != "success"
        }
    )
