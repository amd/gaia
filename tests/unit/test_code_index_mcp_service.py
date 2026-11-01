# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool logic behind ``gaia mcp code-index`` (#869), with the SDK faked.

The MCP transport is covered by ``tests/mcp/test_code_index_mcp.py``; these
tests pin argument validation, the no-index error, and rebuild bookkeeping.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from gaia.code_index.sdk import CodeChunk, CodeIndexConfig, IndexResult, SearchResult
from gaia.mcp.servers.code_index_mcp import (
    MAX_TOP_K,
    SNIPPET_CHARS,
    CodeIndexService,
)


class FakeSDK:
    instances: list = []

    @staticmethod
    def configure(sdk) -> None:
        """Tests replace this to shape the next SDK the service builds."""

    def __init__(self, config: CodeIndexConfig):
        self.config = config
        self.indexed = False
        self.searches = []
        self.progress = {"embedded": 0, "to_embed": 0}
        self.build_started = threading.Event()
        self.release_build = None
        self.fail_build = None
        self.dropped = 0
        FakeSDK.instances.append(self)
        FakeSDK.configure(self)

    def is_indexed(self):
        return self.indexed

    def get_status(self):
        return {"indexed": self.indexed, "repo_path": self.config.repo_path}

    def search(self, query, scope, top_k):
        self.searches.append((query, scope, top_k))
        chunk = CodeChunk(
            content="x" * (SNIPPET_CHARS + 100),
            file_path="src/auth.py",
            language="python",
            start_line=10,
            end_line=40,
            symbol_name="refresh_token",
            symbol_type="function",
        )
        return [SearchResult(chunk=chunk, score=0.876543, result_type="code")]

    def index_repository(self):
        self.progress = {"embedded": 3, "to_embed": 9}
        self.build_started.set()
        if self.release_build is not None:
            self.release_build.wait(10)
        if self.fail_build is not None:
            raise self.fail_build
        self.indexed = True
        return IndexResult(
            files_indexed=4,
            chunks_created=9,
            duration_seconds=1.23456,
            chunks_dropped=self.dropped,
        )


@pytest.fixture(autouse=True)
def fresh_instances():
    FakeSDK.instances = []


@pytest.fixture
def repo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _service(repo, **overrides) -> CodeIndexService:
    return CodeIndexService(
        CodeIndexConfig(repo_path=str(repo), **overrides), sdk_factory=FakeSDK
    )


def _indexed(repo) -> CodeIndexService:
    service = _service(repo)
    FakeSDK.instances[-1].indexed = True
    return service


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def test_home_directory_is_refused_before_any_sdk_exists(repo):
    with pytest.raises(ValueError, match="home directory"):
        _service(Path.home())
    assert FakeSDK.instances == []


def test_home_directory_spelled_with_a_tilde_is_refused(repo):
    with pytest.raises(ValueError, match="home directory"):
        _service(Path("~"))


def test_repo_path_is_made_absolute(repo, monkeypatch):
    monkeypatch.chdir(repo.parent)
    service = _service(Path("repo"))
    assert service.repo_path == str(repo.resolve())
    assert FakeSDK.instances[0].config.repo_path == str(repo.resolve())


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------


def test_search_without_an_index_says_to_rebuild(repo):
    service = _service(repo)
    with pytest.raises(RuntimeError, match="Call rebuild_index first"):
        service.search_code("auth", "all", 10)
    assert FakeSDK.instances[0].searches == []


def test_search_returns_located_snippets(repo):
    service = _indexed(repo)

    [hit] = service.search_code("where are tokens refreshed", "code", 5)

    assert FakeSDK.instances[0].searches == [("where are tokens refreshed", "code", 5)]
    assert hit == {
        "file_path": "src/auth.py",
        "start_line": 10,
        "end_line": 40,
        "language": "python",
        "symbol_name": "refresh_token",
        "symbol_type": "function",
        "score": 0.8765,
        "content": "x" * SNIPPET_CHARS,
    }


@pytest.mark.parametrize(
    "query,scope,top_k,match",
    [
        ("", "all", 10, "query"),
        ("   \n", "all", 10, "query"),
        ("auth", "docs", 10, "scope"),
        ("auth", "", 10, "scope"),
        ("auth", "all", 0, "top_k"),
        ("auth", "all", -1, "top_k"),
        ("auth", "all", MAX_TOP_K + 1, "top_k"),
    ],
)
def test_bad_search_arguments_are_rejected(repo, query, scope, top_k, match):
    service = _indexed(repo)
    with pytest.raises(ValueError, match=match):
        service.search_code(query, scope, top_k)
    assert FakeSDK.instances[0].searches == []


@pytest.mark.parametrize("top_k", [1, MAX_TOP_K])
def test_top_k_bounds_are_inclusive(repo, top_k):
    assert _indexed(repo).search_code("auth", "all", top_k)


# ---------------------------------------------------------------------------
# rebuild_index / get_index_status
# ---------------------------------------------------------------------------


def test_rebuild_reports_counts_and_makes_search_work(repo):
    service = _service(repo)

    report = service.rebuild_index()

    assert report == {
        "repo_path": str(repo.resolve()),
        "embedding_model": CodeIndexConfig(repo_path="x").embedding_model,
        "files_indexed": 4,
        "chunks_created": 9,
        "duration_seconds": 1.23,
    }
    assert service.search_code("auth", "all", 3)
    assert service.get_index_status()["indexed"] is True


def test_rebuild_overrides_reach_the_sdk_and_stick(repo):
    service = _service(repo)
    base = FakeSDK.instances[0].config

    service.rebuild_index(max_files=7, model=" nomic-embed ")
    service.rebuild_index()

    assert FakeSDK.instances[1].config == replace(
        base, max_files=7, embedding_model="nomic-embed"
    )
    assert FakeSDK.instances[2].config == FakeSDK.instances[1].config


def test_dropped_chunks_are_reported_not_hidden(repo, monkeypatch):
    service = _service(repo)
    monkeypatch.setattr(
        FakeSDK, "configure", staticmethod(lambda sdk: setattr(sdk, "dropped", 2))
    )

    report = service.rebuild_index()

    assert report["chunks_dropped"] == 2
    assert "rebuild_index again" in report["warning"]


@pytest.mark.parametrize(
    "kwargs,match",
    [({"max_files": 0}, "max_files"), ({"model": "  "}, "model")],
)
def test_bad_rebuild_arguments_build_nothing(repo, kwargs, match):
    service = _service(repo)
    with pytest.raises(ValueError, match=match):
        service.rebuild_index(**kwargs)
    assert len(FakeSDK.instances) == 1


def test_a_failed_rebuild_keeps_the_old_index_and_can_be_retried(repo, monkeypatch):
    service = _indexed(repo)
    failure = RuntimeError("Lemonade Server not reachable")
    monkeypatch.setattr(
        FakeSDK,
        "configure",
        staticmethod(lambda sdk: setattr(sdk, "fail_build", failure)),
    )

    with pytest.raises(RuntimeError, match="Lemonade"):
        service.rebuild_index(model="other-model")
    monkeypatch.undo()

    service.search_code("auth", "all", 1)
    assert FakeSDK.instances[0].searches, "search must still use the old index"
    assert service.get_index_status()["rebuild_running"] is False
    service.rebuild_index()


def test_a_second_rebuild_while_one_runs_is_refused(repo, monkeypatch):
    service = _indexed(repo)
    gate = threading.Event()
    monkeypatch.setattr(
        FakeSDK,
        "configure",
        staticmethod(lambda sdk: setattr(sdk, "release_build", gate)),
    )
    worker = threading.Thread(target=service.rebuild_index)
    try:
        worker.start()
        assert _wait_for_build(service)

        status = service.get_index_status()
        assert status["rebuild_running"] is True
        assert status["progress"] == {"embedded": 3, "to_embed": 9}
        with pytest.raises(RuntimeError, match="already running"):
            service.rebuild_index()
        # Searches keep answering from the previous index meanwhile.
        assert service.search_code("auth", "all", 1)
    finally:
        gate.set()
        worker.join(10)

    assert service.get_index_status()["rebuild_running"] is False


def _wait_for_build(service) -> bool:
    for _ in range(200):
        if len(FakeSDK.instances) > 1 and FakeSDK.instances[-1].build_started.wait(
            0.05
        ):
            return True
    return False
