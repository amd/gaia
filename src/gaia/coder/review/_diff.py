# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Helpers for resolving a diff from either a PR URL or a local branch.

The review passes all accept a single ``pr_or_branch`` string as input.
Interpretation:

* If it looks like a PR URL (``https://github.com/<owner>/<repo>/pull/N``) or
  a short form (``<owner>/<repo>#N``) we shell to ``gh pr diff`` and
  ``gh pr view``.
* Otherwise it is treated as a local git ref or branch name and we compare
  against the configured base (``coder`` by default) via
  ``git diff <base>...<ref>``.

Module-level subprocess calls here are intentional: the review passes run
**before** the ``ReviewToolsMixin`` has an agent registry, so they cannot
route through the ``@tool``-registered ``run_cli_command``. The
``CLIToolsMixin`` denylist (§6.8 layer 1) is still consulted via
:func:`gaia.coder.tools.cli._check_denylist` so the same ban list applies.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)/?$"
)
_PR_SHORT_RE = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/]+)#(?P<num>\d+)$")


@dataclass
class DiffBundle:
    """Everything a review pass needs about one change-set.

    Assembled once by :func:`resolve_diff`; every pass reads from the same
    snapshot so a ``pr_or_branch`` with lots of files is only fetched once.
    """

    #: ``"pr"`` or ``"branch"`` — determines where ``pr_body`` comes from.
    source: str
    #: Free-form identifier (URL or ref) for logging / citations.
    identifier: str
    #: The unified diff as a single string.
    unified_diff: str
    #: Changed files, repo-relative.
    changed_files: List[str] = field(default_factory=list)
    #: PR title, populated for ``source == "pr"``.
    pr_title: str = ""
    #: PR body, populated for ``source == "pr"`` (empty string for branches).
    pr_body: str = ""
    #: The base ref used for the comparison.
    base_ref: str = "coder"


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run ``cmd`` synchronously and return ``(returncode, stdout, stderr)``.

    A thin wrapper so callers can stub a single seam in tests. The denylist
    check is applied for defence in depth even though these callers are
    internal.
    """
    # Late import so module import does not cost the whole CLI tree.
    from gaia.coder.tools.cli import _check_denylist

    _check_denylist(cmd)
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _is_pr_reference(pr_or_branch: str) -> bool:
    return bool(_PR_URL_RE.match(pr_or_branch) or _PR_SHORT_RE.match(pr_or_branch))


def resolve_diff(
    pr_or_branch: str,
    *,
    base_ref: str = "coder",
    repo_root: Optional[Path] = None,
) -> DiffBundle:
    """Return a :class:`DiffBundle` for ``pr_or_branch``.

    Args:
        pr_or_branch: A PR URL, a ``owner/repo#N`` shortform, or a local git
            ref / branch name.
        base_ref: The base to diff against when ``pr_or_branch`` is a local
            ref. Defaults to ``coder`` because self-fix PRs always target
            the ``coder`` branch per §5.1 and §7.4.
        repo_root: Override the directory where git commands run. Defaults
            to the current working directory.

    Raises:
        RuntimeError: if ``git`` / ``gh`` fail. The error message includes
            the failing command and stderr so the caller can act on it.
    """
    if _is_pr_reference(pr_or_branch):
        return _resolve_pr(pr_or_branch, repo_root=repo_root)
    return _resolve_branch(pr_or_branch, base_ref=base_ref, repo_root=repo_root)


def _resolve_pr(pr_ref: str, *, repo_root: Optional[Path]) -> DiffBundle:
    code, diff_out, diff_err = _run(["gh", "pr", "diff", pr_ref], cwd=repo_root)
    if code != 0:
        raise RuntimeError(
            f"gh pr diff {pr_ref!r} failed with code {code}: {diff_err.strip()!r}. "
            f"Is the gh CLI installed and authenticated?"
        )
    # `gh pr view --json title,body` gives us title + body without HTML parse.
    code, view_out, view_err = _run(
        ["gh", "pr", "view", pr_ref, "--json", "title,body,files"],
        cwd=repo_root,
    )
    if code != 0:
        raise RuntimeError(
            f"gh pr view {pr_ref!r} failed with code {code}: {view_err.strip()!r}."
        )
    import json

    meta = json.loads(view_out)
    files = [item["path"] for item in meta.get("files", [])]
    return DiffBundle(
        source="pr",
        identifier=pr_ref,
        unified_diff=diff_out,
        changed_files=files,
        pr_title=meta.get("title", ""),
        pr_body=meta.get("body", ""),
        base_ref="",
    )


def _resolve_branch(
    ref: str, *, base_ref: str, repo_root: Optional[Path]
) -> DiffBundle:
    code, diff_out, diff_err = _run(
        ["git", "diff", f"{base_ref}...{ref}"], cwd=repo_root
    )
    if code != 0:
        raise RuntimeError(
            f"git diff {base_ref}...{ref} failed with code {code}: "
            f"{diff_err.strip()!r}."
        )
    code, files_out, files_err = _run(
        ["git", "diff", "--name-only", f"{base_ref}...{ref}"], cwd=repo_root
    )
    if code != 0:
        raise RuntimeError(
            f"git diff --name-only failed with code {code}: {files_err.strip()!r}."
        )
    files = [line for line in files_out.splitlines() if line.strip()]
    return DiffBundle(
        source="branch",
        identifier=ref,
        unified_diff=diff_out,
        changed_files=files,
        pr_title="",
        pr_body="",
        base_ref=base_ref,
    )


def filter_by_extension(files: List[str], extensions: Tuple[str, ...]) -> List[str]:
    """Filter ``files`` by any of the given suffixes (case-insensitive)."""
    lowered = tuple(ext.lower() for ext in extensions)
    return [f for f in files if f.lower().endswith(lowered)]


__all__ = [
    "DiffBundle",
    "filter_by_extension",
    "resolve_diff",
]
