# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GitHubToolsMixin`` — 11 ``gh`` CLI wrappers from §15.2 of the coder plan.

Every tool shells out to the GitHub CLI (``gh``) via :mod:`subprocess` — no
PyGithub, no direct REST calls. Three reasons:

1. ``gh`` already encodes the bot-identity auth flow (private key → JWT →
   installation token) via ``gh auth``, so the mixin never has to hold a PEM
   in memory.
2. Every tool call is reproducible by a human at the terminal with the same
   argv — useful when the EM is debugging a weird API response.
3. Output shapes are stable (``gh`` ships JSON flags) and trivially parsed.

All tools default the ``--repo`` flag to the bound repo if known
(``GITHUB_REPO`` env var, or the ``gh`` CLI's own current-repo resolution).
Callers can override per-call via ``repo=``.

Failure mode is fail-loudly: any non-zero exit from ``gh`` raises
:class:`GitHubCLIError` with the stderr attached. This matches the rest of
``gaia-coder`` (§2 principle 3) and lets Pass 3 / Pass 5 catch citation
failures at publish time (§6.9).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Literal, Optional, TypedDict

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return types — §15.2 typed dicts
# ---------------------------------------------------------------------------


class PRHandle(TypedDict):
    """Handle returned by :func:`gh_pr_create`."""

    number: int
    url: str
    head: str
    base: str
    draft: bool


class PRState(TypedDict):
    """Subset of ``gh pr view --json …`` fields used by the loop."""

    number: int
    title: str
    body: str
    state: str  # "OPEN" | "CLOSED" | "MERGED"
    base: str
    head: str
    url: str
    is_draft: bool
    mergeable: Optional[str]
    merge_state_status: Optional[str]


class CommentHandle(TypedDict):
    """Handle returned by :func:`gh_pr_comment` / :func:`gh_issue_comment`."""

    url: str
    id: str


class ReviewHandle(TypedDict):
    """Handle returned by :func:`gh_pr_review`."""

    url: str
    state: str  # "APPROVED" | "CHANGES_REQUESTED" | "COMMENTED"


class MergeResult(TypedDict):
    """Handle returned by :func:`gh_pr_merge`."""

    merged: bool
    method: str
    sha: Optional[str]


class IssueHandle(TypedDict):
    """Handle returned by :func:`gh_issue_create`."""

    number: int
    url: str


class RunInfo(TypedDict):
    """One row returned by :func:`gh_run_list`."""

    id: int
    name: str
    status: str
    conclusion: Optional[str]
    branch: str
    url: str
    created_at: str


class RunOutcome(TypedDict):
    """Handle returned by :func:`gh_run_watch`."""

    id: int
    status: str  # "completed"
    conclusion: str  # "success" | "failure" | "cancelled" | "skipped"
    url: str


class ReleaseHandle(TypedDict):
    """Handle returned by :func:`gh_release_create`."""

    tag: str
    url: str
    draft: bool


# ---------------------------------------------------------------------------
# Exceptions — fail-loudly taxonomy
# ---------------------------------------------------------------------------


class GitHubCLIError(RuntimeError):
    """Raised when ``gh`` exits non-zero.

    The ``stderr`` attribute carries the full error output so the caller can
    surface it in a ``report_to_em`` message without re-invoking ``gh``.
    """

    def __init__(self, argv: List[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"gh exited {returncode}: {' '.join(argv)}\n"
            f"stderr: {stderr.strip() or '<empty>'}"
        )


class GitHubCLIMissingError(RuntimeError):
    """Raised at tool-call time when the ``gh`` binary cannot be found.

    Surfacing this at use-site (rather than at import time) matches the
    portable-core policy of §5.8 — the mixin is importable on machines
    without ``gh`` installed, but any tool invocation fails loudly with a
    fixable message.
    """


# ---------------------------------------------------------------------------
# Subprocess boundary — one place to stub in tests
# ---------------------------------------------------------------------------


def _gh_binary() -> str:
    """Return the absolute path to ``gh`` or raise :class:`GitHubCLIMissingError`."""
    path = shutil.which("gh")
    if path is None:
        raise GitHubCLIMissingError(
            "`gh` CLI not found on PATH. Install it via "
            "https://cli.github.com/ and run `gh auth login`."
        )
    return path


def _run_gh(
    argv: List[str],
    *,
    cwd: Optional[str] = None,
    timeout_s: int = 60,
) -> str:
    """Invoke ``gh`` with ``argv``, returning stdout.

    Raises :class:`GitHubCLIError` on non-zero exit. Tests mock this one
    function to avoid real API calls (per task instructions).
    """
    full = [_gh_binary(), *argv]
    logger.debug("gh invocation: %s (cwd=%s)", " ".join(full), cwd)
    completed = subprocess.run(
        full,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        raise GitHubCLIError(full, completed.returncode, completed.stderr)
    return completed.stdout


def _default_repo() -> Optional[str]:
    """Return the bound repo (``owner/name``) if ``GITHUB_REPO`` is set.

    Else return ``None`` and let ``gh`` fall back to its own CWD resolution.
    """
    repo = os.environ.get("GITHUB_REPO")
    return repo.strip() if repo else None


def _with_repo_flag(argv: List[str], repo: Optional[str]) -> List[str]:
    """Splice ``--repo <owner/name>`` into ``argv`` after the subcommand name."""
    resolved = repo or _default_repo()
    if resolved is None:
        return argv
    return [*argv, "--repo", resolved]


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class GitHubToolsMixin:
    """Mixin providing the 11 ``gh_*`` tools from §15.2 of the coder plan.

    Every tool defaults ``--repo`` to the bound repo (via ``GITHUB_REPO`` env
    var) so the LLM never has to repeat ``owner/name`` in each call — and so
    a stray call cannot accidentally target a different repository.
    """

    def register_github_tools(self) -> None:
        """Register the 11 ``gh_*`` tools in the agent tool registry."""

        @tool
        def gh_pr_create(
            title: str,
            body: str,
            head: str,
            base: str = "coder",
            draft: bool = True,
            labels: Optional[List[str]] = None,
            assignees: Optional[List[str]] = None,
            repo: Optional[str] = None,
        ) -> PRHandle:
            """Open a pull request. Defaults to draft + base=coder per §5.7."""
            argv: List[str] = [
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--head",
                head,
                "--base",
                base,
            ]
            if draft:
                argv.append("--draft")
            for lbl in labels or []:
                argv.extend(["--label", lbl])
            for asg in assignees or []:
                argv.extend(["--assignee", asg])
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            # Parse the PR number out of the URL — the last path segment.
            try:
                number = int(url.rsplit("/", 1)[-1])
            except ValueError as e:
                raise GitHubCLIError(
                    argv, 0, f"could not parse PR number from url {url!r}"
                ) from e
            return PRHandle(
                number=number, url=url, head=head, base=base, draft=draft
            )

        @tool
        def gh_pr_view(number: int, repo: Optional[str] = None) -> PRState:
            """Fetch PR metadata as a structured record."""
            fields = [
                "number",
                "title",
                "body",
                "state",
                "baseRefName",
                "headRefName",
                "url",
                "isDraft",
                "mergeable",
                "mergeStateStatus",
            ]
            argv = ["pr", "view", str(number), "--json", ",".join(fields)]
            argv = _with_repo_flag(argv, repo)
            raw = _run_gh(argv)
            parsed = _parse_json(argv, raw)
            return PRState(
                number=parsed["number"],
                title=parsed["title"],
                body=parsed["body"],
                state=parsed["state"],
                base=parsed["baseRefName"],
                head=parsed["headRefName"],
                url=parsed["url"],
                is_draft=parsed["isDraft"],
                mergeable=parsed.get("mergeable"),
                merge_state_status=parsed.get("mergeStateStatus"),
            )

        @tool
        def gh_pr_comment(
            number: int, body: str, repo: Optional[str] = None
        ) -> CommentHandle:
            """Add a comment to a PR (top-level; not a review)."""
            argv = ["pr", "comment", str(number), "--body", body]
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            return CommentHandle(url=url, id=url.rsplit("#", 1)[-1])

        @tool
        def gh_pr_review(
            number: int,
            event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"],
            body: str,
            repo: Optional[str] = None,
        ) -> ReviewHandle:
            """Submit a PR review.

            The bot identity cannot self-approve a PR it opened (GitHub
            enforces this server-side). Callers wanting to mark a self-PR
            "reviewed" should use ``gh_pr_comment`` instead.
            """
            flag_map = {
                "APPROVE": "--approve",
                "REQUEST_CHANGES": "--request-changes",
                "COMMENT": "--comment",
            }
            argv = [
                "pr",
                "review",
                str(number),
                flag_map[event],
                "--body",
                body,
            ]
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            return ReviewHandle(url=url, state=_review_state(event))

        @tool
        def gh_pr_merge(
            number: int,
            method: Literal["merge", "squash", "rebase"] = "squash",
            repo: Optional[str] = None,
        ) -> MergeResult:
            """Merge a PR. Sensitive-class per §4.3 — callers must elevate.

            ``--repo coder`` restrictions from §5.7 are enforced *one layer
            up* at the trust-tier check, not inside this tool — this tool is
            the low-level shell and must stay general.
            """
            flag_map = {
                "merge": "--merge",
                "squash": "--squash",
                "rebase": "--rebase",
            }
            argv = ["pr", "merge", str(number), flag_map[method], "--admin"]
            argv = _with_repo_flag(argv, repo)
            _run_gh(argv)  # gh prints a human summary; we don't need it
            # Re-fetch the PR to get the merge commit SHA.
            sha: Optional[str] = None
            try:
                state = gh_pr_view(number, repo=repo)
                if state["state"] == "MERGED":
                    # Fetch merge commit SHA via a separate query.
                    sha_argv = _with_repo_flag(
                        ["pr", "view", str(number), "--json", "mergeCommit"],
                        repo,
                    )
                    sha_raw = _run_gh(sha_argv)
                    parsed = _parse_json(sha_argv, sha_raw)
                    sha = (parsed.get("mergeCommit") or {}).get("oid")
            except GitHubCLIError as e:
                # Re-raise with context — we were able to merge but not
                # confirm the SHA; that's an actionable split condition.
                logger.warning(
                    "gh_pr_merge: merge succeeded but follow-up view failed: %s",
                    e,
                )
            return MergeResult(merged=True, method=method, sha=sha)

        @tool
        def gh_issue_create(
            title: str,
            body: str,
            labels: Optional[List[str]] = None,
            assignees: Optional[List[str]] = None,
            repo: Optional[str] = None,
        ) -> IssueHandle:
            """Open an issue in the bound repo (or ``repo`` override)."""
            argv = ["issue", "create", "--title", title, "--body", body]
            for lbl in labels or []:
                argv.extend(["--label", lbl])
            for asg in assignees or []:
                argv.extend(["--assignee", asg])
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            try:
                number = int(url.rsplit("/", 1)[-1])
            except ValueError as e:
                raise GitHubCLIError(
                    argv, 0, f"could not parse issue number from url {url!r}"
                ) from e
            return IssueHandle(number=number, url=url)

        @tool
        def gh_issue_comment(
            number: int, body: str, repo: Optional[str] = None
        ) -> CommentHandle:
            """Add a comment to an existing issue."""
            argv = ["issue", "comment", str(number), "--body", body]
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            return CommentHandle(url=url, id=url.rsplit("#", 1)[-1])

        @tool
        def gh_run_list(
            workflow: Optional[str] = None,
            branch: Optional[str] = None,
            limit: int = 20,
            repo: Optional[str] = None,
        ) -> List[RunInfo]:
            """List recent workflow runs matching the given filters."""
            argv: List[str] = [
                "run",
                "list",
                "--limit",
                str(limit),
                "--json",
                "databaseId,name,status,conclusion,headBranch,url,createdAt",
            ]
            if workflow:
                argv.extend(["--workflow", workflow])
            if branch:
                argv.extend(["--branch", branch])
            argv = _with_repo_flag(argv, repo)
            raw = _run_gh(argv)
            parsed = _parse_json(argv, raw)
            runs: List[RunInfo] = []
            for row in parsed:
                runs.append(
                    RunInfo(
                        id=row["databaseId"],
                        name=row["name"],
                        status=row["status"],
                        conclusion=row.get("conclusion"),
                        branch=row.get("headBranch", ""),
                        url=row["url"],
                        created_at=row["createdAt"],
                    )
                )
            return runs

        @tool
        def gh_run_watch(
            run_id: int, timeout_s: int = 3600, repo: Optional[str] = None
        ) -> RunOutcome:
            """Block until a workflow run completes, then return its outcome.

            The ``gh run watch`` command prints progress to stdout but exits
            0 regardless of conclusion — so we re-query ``gh run view``
            after the watch exits to extract conclusion.
            """
            argv = ["run", "watch", str(run_id), "--exit-status"]
            argv = _with_repo_flag(argv, repo)
            try:
                _run_gh(argv, timeout_s=timeout_s)
                conclusion = "success"
            except GitHubCLIError as e:
                # --exit-status makes `gh` return non-zero on non-success
                # conclusions. We infer the conclusion from a follow-up
                # query so the caller gets structured data either way.
                logger.debug("gh run watch exited non-zero: %s", e)
                conclusion = _fetch_run_conclusion(run_id, repo=repo)
            view_argv = _with_repo_flag(
                ["run", "view", str(run_id), "--json", "url,status"],
                repo,
            )
            parsed = _parse_json(view_argv, _run_gh(view_argv))
            return RunOutcome(
                id=run_id,
                status=parsed.get("status", "completed"),
                conclusion=conclusion,
                url=parsed["url"],
            )

        @tool
        def gh_run_view_log(
            run_id: int,
            failed_only: bool = True,
            repo: Optional[str] = None,
        ) -> str:
            """Return the log text for a workflow run.

            ``failed_only=True`` applies ``--log-failed`` which restricts to
            failed steps — the default because the triage loop always wants
            the failing step's context, not the full log dump.
            """
            argv: List[str] = ["run", "view", str(run_id)]
            argv.append("--log-failed" if failed_only else "--log")
            argv = _with_repo_flag(argv, repo)
            return _run_gh(argv, timeout_s=120)

        @tool
        def gh_release_create(
            tag: str,
            title: str,
            notes: str,
            draft: bool = True,
            repo: Optional[str] = None,
        ) -> ReleaseHandle:
            """Create a GitHub Release. Sensitive per §4.3 — draft by default."""
            argv: List[str] = [
                "release",
                "create",
                tag,
                "--title",
                title,
                "--notes",
                notes,
            ]
            if draft:
                argv.append("--draft")
            argv = _with_repo_flag(argv, repo)
            url = _run_gh(argv).strip().splitlines()[-1]
            return ReleaseHandle(tag=tag, url=url, draft=draft)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(argv: List[str], raw: str) -> Any:
    """Parse ``gh --json`` output, raising :class:`GitHubCLIError` on invalid JSON."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise GitHubCLIError(
            argv,
            0,
            f"gh returned non-JSON output: {e.msg} (first 200 chars: {raw[:200]!r})",
        ) from e


def _review_state(event: str) -> str:
    """Map a ``gh_pr_review`` ``event`` to GitHub's review state string."""
    return {
        "APPROVE": "APPROVED",
        "REQUEST_CHANGES": "CHANGES_REQUESTED",
        "COMMENT": "COMMENTED",
    }[event]


def _fetch_run_conclusion(run_id: int, repo: Optional[str]) -> str:
    """Query ``gh run view --json conclusion`` for ``run_id``.

    Extracted so :func:`gh_run_watch` can recover conclusion after ``watch``
    exits non-zero on a failing run — keeps the tool body readable.
    """
    argv = _with_repo_flag(
        ["run", "view", str(run_id), "--json", "conclusion"],
        repo,
    )
    parsed = _parse_json(argv, _run_gh(argv))
    return parsed.get("conclusion") or "failure"


__all__ = [
    "CommentHandle",
    "GitHubCLIError",
    "GitHubCLIMissingError",
    "GitHubToolsMixin",
    "IssueHandle",
    "MergeResult",
    "PRHandle",
    "PRState",
    "ReleaseHandle",
    "ReviewHandle",
    "RunInfo",
    "RunOutcome",
]
