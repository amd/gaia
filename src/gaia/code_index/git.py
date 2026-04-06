# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GitIndexer — extracts git commit history and pull-request data for the code index.

Uses subprocess with shell=False (no shell injection risk).
"""

import json
import logging
import subprocess
from typing import List, Optional

from .sdk import CodeIndexConfig, CommitChunk, PRChunk

log = logging.getLogger(__name__)

# Separator used in git log --format to split fields (unit separator character)
_GIT_SEP = "\x1f"

# git log format: sha, subject, author, ISO date, then a list of changed files
_GIT_FORMAT = f"%H{_GIT_SEP}%s{_GIT_SEP}%an{_GIT_SEP}%aI"


class GitIndexer:
    """Indexes git commit history and GitHub pull requests."""

    def __init__(self, config: CodeIndexConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_commits(self) -> List[CommitChunk]:
        """Return up to config.max_commits as CommitChunk objects.

        Returns an empty list if git history indexing is disabled or if
        git is not available / the directory is not a repository.
        """
        if not self.config.index_git_history:
            return []

        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    self.config.repo_path,
                    "log",
                    f"--max-count={self.config.max_commits}",
                    f"--format={_GIT_FORMAT}",
                    "--name-only",
                ],
                capture_output=True,
                text=True,
                shell=False,
                check=False,
            )
        except FileNotFoundError:
            log.warning("git not found — skipping commit indexing")
            return []

        if result.returncode != 0:
            log.warning(
                f"git log failed (rc={result.returncode}): {result.stderr.strip()}"
            )
            return []

        return self._parse_git_log(result.stdout)

    def get_pull_requests(self) -> List[PRChunk]:
        """Return closed/merged pull requests as PRChunk objects.

        Returns an empty list if PR indexing is disabled or if the
        gh CLI is not available / not authenticated.
        """
        if not self.config.index_prs:
            return []

        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "all",
                    "--limit",
                    "200",
                    "--json",
                    "number,title,body,state,author,url,labels",
                ],
                capture_output=True,
                text=True,
                shell=False,
                check=False,
                cwd=self.config.repo_path,
            )
        except FileNotFoundError:
            log.warning("gh CLI not found — skipping PR indexing")
            return []

        if result.returncode != 0:
            log.warning(
                f"gh pr list failed (rc={result.returncode}): {result.stderr.strip()}"
            )
            return []

        return self._parse_pr_json(result.stdout)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_git_log(self, output: str) -> List[CommitChunk]:
        """Parse multi-record git log output into CommitChunk objects.

        git log with --name-only produces records of the form:
            <hash><sep><msg><sep><author><sep><date>
            (blank line)
            file1
            file2
            (blank line)
            <next record>
        """
        chunks: List[CommitChunk] = []
        current_header: Optional[str] = None
        current_files: List[str] = []

        def _flush():
            if current_header is None:
                return
            parts = current_header.split(_GIT_SEP)
            if len(parts) < 4:
                return
            sha, message, author, date = parts[0], parts[1], parts[2], parts[3]
            content = f"commit {sha}\nauthor {author}\ndate {date}\n\n{message}"
            if current_files:
                content += "\n\nfiles changed:\n" + "\n".join(current_files)
            chunks.append(
                CommitChunk(
                    commit_hash=sha,
                    diff_summary=message,
                    author=author,
                    date=date,
                    files_changed=list(current_files),
                    content=content,
                )
            )

        for line in output.splitlines():
            if _GIT_SEP in line:
                # New commit header — flush the previous one
                _flush()
                current_header = line.strip()
                current_files = []
            elif line.strip():
                current_files.append(line.strip())
            # blank lines are separators — ignore

        _flush()
        return chunks

    def _parse_pr_json(self, output: str) -> List[PRChunk]:
        """Parse gh pr list JSON output into PRChunk objects."""
        try:
            prs = json.loads(output)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"Failed to parse gh pr list output: {e}")
            return []

        if not isinstance(prs, list):
            return []

        chunks: List[PRChunk] = []
        for pr in prs:
            number = pr.get("number", 0)
            title = pr.get("title", "")
            body = pr.get("body", "") or ""
            state = pr.get("state", "unknown").lower()
            author = (pr.get("author") or {}).get("login", "unknown")
            url = pr.get("url", "")
            labels = [lbl.get("name", "") for lbl in pr.get("labels", [])]

            content = (
                f"PR #{number}: {title}\nstate: {state}\nauthor: {author}\n\n{body}"
            )

            chunks.append(
                PRChunk(
                    pr_number=number,
                    title=title,
                    body=body,
                    state=state,
                    author=author,
                    url=url,
                    labels=labels,
                    files_changed=[],
                    content=content,
                )
            )

        return chunks
