# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for gaia.code_index config and response models.
"""

import pytest

try:
    from gaia.code_index.sdk import (
        CodeChunk,
        CodeIndexConfig,
        CommitChunk,
        IndexResult,
        PRChunk,
        SearchResult,
    )

    CODE_INDEX_AVAILABLE = True
except ImportError as e:
    CODE_INDEX_AVAILABLE = False
    IMPORT_ERROR = str(e)


def skip_if_unavailable():
    if not CODE_INDEX_AVAILABLE:
        pytest.skip(f"code_index not available: {IMPORT_ERROR}")


class TestCodeIndexConfig:
    """Tests for CodeIndexConfig dataclass."""

    def test_default_values(self):
        skip_if_unavailable()
        config = CodeIndexConfig(repo_path="/tmp/repo")
        assert config.repo_path == "/tmp/repo"
        assert config.max_files == 5000
        assert config.max_file_size_mb == 1
        assert config.chunk_overlap == 50
        assert config.embedding_model == "nomic-embed-text-v2-moe-GGUF"
        assert config.cache_dir == "~/.gaia/code_index"
        assert config.index_git_history is True
        assert config.index_prs is False
        assert config.max_commits == 1000

    def test_custom_values(self):
        skip_if_unavailable()
        config = CodeIndexConfig(
            repo_path="/my/repo",
            max_files=100,
            max_file_size_mb=2,
            index_git_history=False,
            index_prs=True,
            max_commits=50,
        )
        assert config.max_files == 100
        assert config.max_file_size_mb == 2
        assert config.index_git_history is False
        assert config.index_prs is True
        assert config.max_commits == 50

    def test_repo_path_required(self):
        skip_if_unavailable()
        # repo_path has no default — must be provided
        with pytest.raises(TypeError):
            CodeIndexConfig()


class TestCodeChunk:
    """Tests for CodeChunk dataclass."""

    def test_required_fields(self):
        skip_if_unavailable()
        chunk = CodeChunk(
            content="def foo(): pass",
            file_path="src/foo.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        assert chunk.content == "def foo(): pass"
        assert chunk.file_path == "src/foo.py"
        assert chunk.language == "python"
        assert chunk.start_line == 1
        assert chunk.end_line == 1

    def test_optional_fields_default_none(self):
        skip_if_unavailable()
        chunk = CodeChunk(
            content="x = 1",
            file_path="src/x.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        assert chunk.symbol_name is None
        assert chunk.symbol_type is None
        assert chunk.imports == []

    def test_with_symbol_metadata(self):
        skip_if_unavailable()
        chunk = CodeChunk(
            content="def bar(): pass",
            file_path="src/bar.py",
            language="python",
            start_line=5,
            end_line=10,
            symbol_name="bar",
            symbol_type="function",
            imports=["os", "sys"],
        )
        assert chunk.symbol_name == "bar"
        assert chunk.symbol_type == "function"
        assert chunk.imports == ["os", "sys"]


class TestCommitChunk:
    """Tests for CommitChunk dataclass."""

    def test_required_fields(self):
        skip_if_unavailable()
        chunk = CommitChunk(
            content="fix: resolve issue with indexing",
            commit_hash="abc123",
            author="developer",
            date="2026-04-01",
            files_changed=["src/foo.py"],
            diff_summary="1 file changed, 5 insertions",
        )
        assert chunk.commit_hash == "abc123"
        assert chunk.author == "developer"
        assert chunk.files_changed == ["src/foo.py"]

    def test_files_changed_default_empty(self):
        skip_if_unavailable()
        chunk = CommitChunk(
            content="initial commit",
            commit_hash="def456",
            author="dev",
            date="2026-01-01",
            files_changed=[],
            diff_summary="",
        )
        assert chunk.files_changed == []


class TestPRChunk:
    """Tests for PRChunk dataclass."""

    def test_required_fields(self):
        skip_if_unavailable()
        chunk = PRChunk(
            content="feat: add new feature\n\nThis PR adds...",
            pr_number=42,
            title="feat: add new feature",
            state="open",
            author="contributor",
            labels=["enhancement"],
            files_changed=["src/feature.py"],
            url="https://github.com/amd/gaia/pull/42",
        )
        assert chunk.pr_number == 42
        assert chunk.state == "open"
        assert chunk.labels == ["enhancement"]

    def test_closed_pr(self):
        skip_if_unavailable()
        chunk = PRChunk(
            content="old PR content",
            pr_number=1,
            title="old feature",
            state="closed",
            author="dev",
            labels=[],
            files_changed=[],
            url="https://github.com/amd/gaia/pull/1",
        )
        assert chunk.state == "closed"


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_with_code_chunk(self):
        skip_if_unavailable()
        chunk = CodeChunk(
            content="def foo(): pass",
            file_path="foo.py",
            language="python",
            start_line=1,
            end_line=1,
        )
        result = SearchResult(chunk=chunk, score=0.95, result_type="code")
        assert result.score == 0.95
        assert result.result_type == "code"
        assert isinstance(result.chunk, CodeChunk)

    def test_with_commit_chunk(self):
        skip_if_unavailable()
        chunk = CommitChunk(
            content="fix: bug",
            commit_hash="abc",
            author="dev",
            date="2026-01-01",
            files_changed=[],
            diff_summary="",
        )
        result = SearchResult(chunk=chunk, score=0.7, result_type="commit")
        assert result.result_type == "commit"

    def test_with_pr_chunk(self):
        skip_if_unavailable()
        chunk = PRChunk(
            content="PR body",
            pr_number=1,
            title="fix",
            state="merged",
            author="dev",
            labels=[],
            files_changed=[],
            url="https://github.com/amd/gaia/pull/1",
        )
        result = SearchResult(chunk=chunk, score=0.8, result_type="pr")
        assert result.result_type == "pr"


class TestIndexResult:
    """Tests for IndexResult dataclass."""

    def test_default_values(self):
        skip_if_unavailable()
        result = IndexResult(
            files_indexed=10,
            chunks_created=50,
            commits_indexed=0,
            prs_indexed=0,
            duration_seconds=1.5,
        )
        assert result.files_indexed == 10
        assert result.chunks_created == 50
        assert result.commits_indexed == 0
        assert result.prs_indexed == 0
        assert result.duration_seconds == 1.5

    def test_full_index_result(self):
        skip_if_unavailable()
        result = IndexResult(
            files_indexed=100,
            chunks_created=500,
            commits_indexed=200,
            prs_indexed=30,
            duration_seconds=45.2,
        )
        assert result.files_indexed == 100
        assert result.commits_indexed == 200
        assert result.prs_indexed == 30
