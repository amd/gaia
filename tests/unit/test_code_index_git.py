# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for gaia.code_index.git module.

All subprocess calls are mocked — no real git or gh CLI required.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from gaia.code_index.git import GitIndexer
    from gaia.code_index.sdk import CodeIndexConfig, CommitChunk, PRChunk

    GIT_AVAILABLE = True
except ImportError as e:
    GIT_AVAILABLE = False
    IMPORT_ERROR = str(e)


def skip_if_unavailable():
    if not GIT_AVAILABLE:
        pytest.skip(f"code_index.git not available: {IMPORT_ERROR}")


def make_indexer(tmp_path: Path, **kwargs) -> "GitIndexer":
    defaults = dict(
        repo_path=str(tmp_path),
        cache_dir=str(tmp_path / ".cache"),
        index_git_history=True,
        index_prs=False,
        max_commits=100,
    )
    defaults.update(kwargs)
    return GitIndexer(CodeIndexConfig(**defaults))


# ---------------------------------------------------------------------------
# Test: GitIndexer instantiation
# ---------------------------------------------------------------------------


class TestGitIndexerInit:
    def test_creates_without_error(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)
        assert indexer is not None

    def test_stores_config(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)
        assert indexer.config.repo_path == str(tmp_path)


# ---------------------------------------------------------------------------
# Test: get_commits (mocked subprocess)
# ---------------------------------------------------------------------------

# Matches real `git log --format=<header> --name-only` output:
# header line, blank line, changed files, blank line, next commit
_GIT_LOG_OUTPUT = (
    "abc123\x1fAdd new feature\x1fAlice\x1f2024-01-15T10:00:00\n"
    "\n"
    "src/foo.py\n"
    "src/bar.py\n"
    "\n"
    "def456\x1fFix bug in parser\x1fBob\x1f2024-01-14T09:00:00\n"
    "\n"
    "tests/test_foo.py\n"
    "\n"
)


class TestGetCommits:
    def test_returns_list_of_commit_chunks(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _GIT_LOG_OUTPUT
            mock_run.return_value = mock_result

            commits = indexer.get_commits()

        assert isinstance(commits, list)
        assert len(commits) == 2
        assert all(isinstance(c, CommitChunk) for c in commits)

    def test_commit_fields_populated(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _GIT_LOG_OUTPUT
            mock_run.return_value = mock_result

            commits = indexer.get_commits()

        first = commits[0]
        assert first.commit_hash == "abc123"
        assert (
            "new feature" in first.diff_summary.lower()
            or "feature" in first.diff_summary.lower()
        )
        assert first.author == "Alice"
        assert len(first.files_changed) == 2

    def test_git_failure_returns_empty(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 128
            mock_result.stdout = ""
            mock_result.stderr = "fatal: not a git repository"
            mock_run.return_value = mock_result

            commits = indexer.get_commits()

        assert commits == []

    def test_respects_max_commits(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path, max_commits=1)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _GIT_LOG_OUTPUT
            mock_run.return_value = mock_result

            commits = indexer.get_commits()

        # Verify git was called with --max-count or -n
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
        cmd_str = " ".join(str(x) for x in cmd)
        assert "1" in cmd_str  # max_commits=1 passed to git

    def test_no_shell_true(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            indexer.get_commits()

        # shell=False is mandatory (security)
        call_kwargs = mock_run.call_args[1] if mock_run.call_args else {}
        assert call_kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# Test: get_pull_requests (mocked gh CLI)
# ---------------------------------------------------------------------------

_GH_PR_OUTPUT = json.dumps(
    [
        {
            "number": 42,
            "title": "Add streaming support",
            "body": "This PR adds SSE streaming to the chat endpoint.",
            "state": "merged",
            "author": {"login": "alice"},
            "url": "https://github.com/amd/gaia/pull/42",
            "labels": [{"name": "enhancement"}],
        },
        {
            "number": 41,
            "title": "Fix null pointer in parser",
            "body": "Fixes crash when content is empty.",
            "state": "closed",
            "author": {"login": "bob"},
            "url": "https://github.com/amd/gaia/pull/41",
            "labels": [],
        },
    ]
)


class TestGetPullRequests:
    def test_returns_list_of_pr_chunks(self, tmp_path):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
            index_git_history=False,
            index_prs=True,
        )
        indexer = GitIndexer(config)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _GH_PR_OUTPUT
            mock_run.return_value = mock_result

            prs = indexer.get_pull_requests()

        assert isinstance(prs, list)
        assert len(prs) == 2
        assert all(isinstance(pr, PRChunk) for pr in prs)

    def test_pr_fields_populated(self, tmp_path):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
            index_git_history=False,
            index_prs=True,
        )
        indexer = GitIndexer(config)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = _GH_PR_OUTPUT
            mock_run.return_value = mock_result

            prs = indexer.get_pull_requests()

        first = prs[0]
        assert first.pr_number == 42
        assert "streaming" in first.title.lower()
        assert first.state == "merged"

    def test_gh_failure_returns_empty(self, tmp_path):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
            index_git_history=False,
            index_prs=True,
        )
        indexer = GitIndexer(config)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            mock_result.stderr = "gh: command not found"
            mock_run.return_value = mock_result

            prs = indexer.get_pull_requests()

        assert prs == []

    def test_no_shell_true_gh(self, tmp_path):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
            index_git_history=False,
            index_prs=True,
        )
        indexer = GitIndexer(config)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "[]"
            mock_run.return_value = mock_result

            indexer.get_pull_requests()

        call_kwargs = mock_run.call_args[1] if mock_run.call_args else {}
        assert call_kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# Test: index_prs=False skips PR fetch
# ---------------------------------------------------------------------------


class TestIndexingFlags:
    def test_index_prs_false_skips_prs(self, tmp_path):
        skip_if_unavailable()
        indexer = make_indexer(tmp_path, index_prs=False)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "[]"
            mock_run.return_value = mock_result

            prs = indexer.get_pull_requests()

        # When index_prs=False, should return empty without calling gh
        assert prs == []
        mock_run.assert_not_called()

    def test_index_git_history_false_skips_commits(self, tmp_path):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path=str(tmp_path),
            cache_dir=str(tmp_path / ".cache"),
            index_git_history=False,
            index_prs=False,
        )
        indexer = GitIndexer(config)

        with patch("subprocess.run") as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            commits = indexer.get_commits()

        assert commits == []
        mock_run.assert_not_called()
