# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for gaia-coder File/CLI/Search mixins (§15.2).

Each tool has:
    - a happy-path test using ``tmp_path``
    - a failure-mode test
    - a registration test verifying the signature lands in ``_TOOL_REGISTRY``

See docs/plans/coder-agent.mdx §15.2 for signatures.
"""

from __future__ import annotations

import sys
import textwrap
import time

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.coder.tools.cli import CLIToolsMixin, ShellDeniedError
from gaia.coder.tools.file import FileToolsMixin
from gaia.coder.tools.search import SearchToolsMixin

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_snapshot():
    """Save and restore ``_TOOL_REGISTRY`` around a test."""
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


@pytest.fixture
def file_mixin(registry_snapshot):
    m = FileToolsMixin()
    m.register_file_tools()
    return m


@pytest.fixture
def cli_mixin(registry_snapshot):
    m = CLIToolsMixin()
    m.register_cli_tools()
    return m


@pytest.fixture
def search_mixin(registry_snapshot):
    m = SearchToolsMixin()
    m.register_search_tools()
    return m


def _get_tool(name: str):
    """Retrieve the callable registered under ``name``."""
    entry = _TOOL_REGISTRY.get(name)
    assert entry is not None, f"tool {name!r} not registered"
    return entry["function"]


# ---------------------------------------------------------------------------
# FileToolsMixin — read_file
# ---------------------------------------------------------------------------


def test_read_file_happy_path(tmp_path, file_mixin):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\nline3\n")
    read_file = _get_tool("read_file")
    assert read_file(str(f)) == "line1\nline2\nline3\n"


def test_read_file_line_range(tmp_path, file_mixin):
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n")
    read_file = _get_tool("read_file")
    assert read_file(str(f), start_line=2, end_line=4) == "b\nc\nd\n"


def test_read_file_missing_raises(tmp_path, file_mixin):
    read_file = _get_tool("read_file")
    with pytest.raises(FileNotFoundError):
        read_file(str(tmp_path / "nope.txt"))


# ---------------------------------------------------------------------------
# FileToolsMixin — write_file
# ---------------------------------------------------------------------------


def test_write_file_creates_parents(tmp_path, file_mixin):
    target = tmp_path / "a" / "b" / "c.txt"
    write_file = _get_tool("write_file")
    result = write_file(str(target), "hello")
    assert target.read_text() == "hello"
    assert result["written_bytes"] == 5
    assert result["path"] == str(target)


def test_write_file_overwrites(tmp_path, file_mixin):
    target = tmp_path / "over.txt"
    target.write_text("old")
    write_file = _get_tool("write_file")
    write_file(str(target), "new-content")
    assert target.read_text() == "new-content"


# ---------------------------------------------------------------------------
# FileToolsMixin — edit_file
# ---------------------------------------------------------------------------


def test_edit_file_happy_path(tmp_path, file_mixin):
    f = tmp_path / "ed.txt"
    f.write_text("alpha bravo charlie")
    edit_file = _get_tool("edit_file")
    result = edit_file(str(f), "bravo", "BRAVO")
    assert f.read_text() == "alpha BRAVO charlie"
    assert result["replacements"] == 1


def test_edit_file_not_found_raises(tmp_path, file_mixin):
    f = tmp_path / "ed.txt"
    f.write_text("alpha bravo")
    edit_file = _get_tool("edit_file")
    with pytest.raises(ValueError, match="old_string not found"):
        edit_file(str(f), "zulu", "ZULU")


def test_edit_file_non_unique_raises(tmp_path, file_mixin):
    f = tmp_path / "ed.txt"
    f.write_text("a a a")
    edit_file = _get_tool("edit_file")
    with pytest.raises(ValueError, match="old_string not unique"):
        edit_file(str(f), "a", "b")


def test_edit_file_replace_all(tmp_path, file_mixin):
    f = tmp_path / "ed.txt"
    f.write_text("a a a")
    edit_file = _get_tool("edit_file")
    result = edit_file(str(f), "a", "b", replace_all=True)
    assert f.read_text() == "b b b"
    assert result["replacements"] == 3


# ---------------------------------------------------------------------------
# FileToolsMixin — search_code
# ---------------------------------------------------------------------------


def test_search_code_happy_path(tmp_path, file_mixin):
    (tmp_path / "x.py").write_text("import os\nprint('hi')\n")
    (tmp_path / "y.py").write_text("import sys\nprint('bye')\n")
    search_code = _get_tool("search_code")
    hits = search_code(r"^import \w+", path=str(tmp_path))
    assert len(hits) == 2
    assert all("import" in h["line_text"] for h in hits)
    assert all("line_number" in h for h in hits)


def test_search_code_glob_filter(tmp_path, file_mixin):
    (tmp_path / "keep.py").write_text("needle\n")
    (tmp_path / "drop.txt").write_text("needle\n")
    search_code = _get_tool("search_code")
    hits = search_code("needle", path=str(tmp_path), glob="*.py")
    assert len(hits) == 1
    assert hits[0]["path"].endswith("keep.py")


def test_search_code_case_insensitive(tmp_path, file_mixin):
    (tmp_path / "a.txt").write_text("HELLO WORLD\n")
    search_code = _get_tool("search_code")
    assert len(search_code("hello", path=str(tmp_path), case_sensitive=False)) == 1
    assert search_code("hello", path=str(tmp_path), case_sensitive=True) == []


def test_search_code_max_matches(tmp_path, file_mixin):
    (tmp_path / "many.txt").write_text("needle\n" * 50)
    search_code = _get_tool("search_code")
    hits = search_code("needle", path=str(tmp_path), max_matches=10)
    assert len(hits) == 10


# ---------------------------------------------------------------------------
# FileToolsMixin — glob
# ---------------------------------------------------------------------------


def test_glob_happy_path(tmp_path, file_mixin):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    glob_ = _get_tool("glob")
    results = glob_("*.py", path=str(tmp_path))
    assert len(results) == 2
    assert all("/" in r or r.endswith(".py") for r in results)
    assert all(not r.endswith("c.txt") for r in results)


def test_glob_no_matches(tmp_path, file_mixin):
    glob_ = _get_tool("glob")
    assert glob_("*.nonexistent", path=str(tmp_path)) == []


# ---------------------------------------------------------------------------
# FileToolsMixin — generate_diff
# ---------------------------------------------------------------------------


def test_generate_diff_happy_path(tmp_path, file_mixin):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\ntwo\nthree\n")
    b.write_text("one\nTWO\nthree\n")
    generate_diff = _get_tool("generate_diff")
    diff = generate_diff(str(a), str(b))
    assert "-two" in diff
    assert "+TWO" in diff


def test_generate_diff_identical(tmp_path, file_mixin):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same\n")
    b.write_text("same\n")
    generate_diff = _get_tool("generate_diff")
    assert generate_diff(str(a), str(b)) == ""


# ---------------------------------------------------------------------------
# FileToolsMixin — registration
# ---------------------------------------------------------------------------


def test_file_tools_all_registered(file_mixin):
    expected = {
        "read_file",
        "write_file",
        "edit_file",
        "search_code",
        "glob",
        "generate_diff",
    }
    assert expected.issubset(_TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# CLIToolsMixin — run_cli_command
# ---------------------------------------------------------------------------


def test_run_cli_command_happy_path(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    result = run_cli_command([sys.executable, "-c", "print('ok')"])
    assert result["returncode"] == 0
    assert "ok" in result["stdout"]
    assert result["stderr"] == ""
    assert result["duration_ms"] >= 0
    assert result["pid"] is None  # not background


def test_run_cli_command_nonzero_exit(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    result = run_cli_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result["returncode"] == 3


def test_run_cli_command_denylist(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    with pytest.raises(ShellDeniedError):
        run_cli_command(["rm", "-rf", "/"])


def test_run_cli_command_denylist_sudo(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    with pytest.raises(ShellDeniedError):
        run_cli_command(["sudo", "ls"])


def test_run_cli_command_background(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    list_processes = _get_tool("list_processes")
    stop_process = _get_tool("stop_process")

    result = run_cli_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], background=True
    )
    pid = result["pid"]
    assert pid is not None
    try:
        procs = list_processes()
        assert any(p["pid"] == pid for p in procs)
    finally:
        stop_process(pid, force=True)


# ---------------------------------------------------------------------------
# CLIToolsMixin — stop_process
# ---------------------------------------------------------------------------


def test_stop_process_happy_path(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    stop_process = _get_tool("stop_process")
    list_processes = _get_tool("list_processes")

    result = run_cli_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], background=True
    )
    pid = result["pid"]
    stop_result = stop_process(pid, force=True)
    assert stop_result["stopped"] is True
    # Registry should be cleaned up
    assert all(p["pid"] != pid for p in list_processes())


def test_stop_process_unknown_pid(cli_mixin):
    stop_process = _get_tool("stop_process")
    with pytest.raises(ValueError, match="unknown pid"):
        stop_process(999999)


# ---------------------------------------------------------------------------
# CLIToolsMixin — list_processes
# ---------------------------------------------------------------------------


def test_list_processes_empty(cli_mixin):
    from gaia.coder.tools.cli import _PROCESS_REGISTRY

    _PROCESS_REGISTRY.clear()
    list_processes = _get_tool("list_processes")
    assert list_processes() == []


def test_list_processes_tracks_background(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    list_processes = _get_tool("list_processes")
    stop_process = _get_tool("stop_process")

    result = run_cli_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], background=True
    )
    pid = result["pid"]
    try:
        procs = list_processes()
        pids = [p["pid"] for p in procs]
        assert pid in pids
    finally:
        stop_process(pid, force=True)


# ---------------------------------------------------------------------------
# CLIToolsMixin — get_process_logs
# ---------------------------------------------------------------------------


def test_get_process_logs_happy_path(cli_mixin):
    run_cli_command = _get_tool("run_cli_command")
    get_process_logs = _get_tool("get_process_logs")
    stop_process = _get_tool("stop_process")

    result = run_cli_command(
        [
            sys.executable,
            "-c",
            "import time\n"
            "for i in range(5):\n"
            "    print('line', i, flush=True)\n"
            "    time.sleep(0.05)\n"
            "time.sleep(30)",
        ],
        background=True,
    )
    pid = result["pid"]
    try:
        time.sleep(0.8)
        logs = get_process_logs(pid, tail_lines=10)
        assert "line" in logs
    finally:
        stop_process(pid, force=True)


def test_get_process_logs_unknown_pid(cli_mixin):
    get_process_logs = _get_tool("get_process_logs")
    with pytest.raises(ValueError, match="unknown pid"):
        get_process_logs(999999)


# ---------------------------------------------------------------------------
# CLIToolsMixin — registration
# ---------------------------------------------------------------------------


def test_cli_tools_all_registered(cli_mixin):
    expected = {
        "run_cli_command",
        "stop_process",
        "list_processes",
        "get_process_logs",
    }
    assert expected.issubset(_TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# SearchToolsMixin — grep
# ---------------------------------------------------------------------------


def test_grep_happy_path(tmp_path, search_mixin):
    (tmp_path / "a.py").write_text("import os\n")
    grep = _get_tool("grep")
    hits = grep(r"import", path=str(tmp_path))
    assert len(hits) == 1
    assert hits[0]["line_text"].strip() == "import os"


def test_grep_no_matches(tmp_path, search_mixin):
    (tmp_path / "a.py").write_text("hello\n")
    grep = _get_tool("grep")
    assert grep("nonexistent_xyz123", path=str(tmp_path)) == []


# ---------------------------------------------------------------------------
# SearchToolsMixin — find_symbol
# ---------------------------------------------------------------------------


def test_find_symbol_function(tmp_path, search_mixin, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = textwrap.dedent("""
        def foo():
            pass

        def bar():
            pass
        """)
    (tmp_path / "m.py").write_text(src)
    find_symbol = _get_tool("find_symbol")
    hits = find_symbol("foo")
    assert any(h["kind"] == "function" and h["qualname"] == "foo" for h in hits)


def test_find_symbol_class_filter(tmp_path, search_mixin, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = textwrap.dedent("""
        class Foo:
            def method(self): pass

        def foo():
            pass
        """)
    (tmp_path / "m.py").write_text(src)
    find_symbol = _get_tool("find_symbol")
    hits = find_symbol("Foo", kind="class")
    assert len(hits) == 1
    assert hits[0]["kind"] == "class"
    assert hits[0]["qualname"] == "Foo"


def test_find_symbol_unsupported_language(tmp_path, search_mixin, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    # No .py files, only .js
    (tmp_path / "x.js").write_text("function foo() {}\n")
    find_symbol = _get_tool("find_symbol")
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        hits = find_symbol("foo")
    assert hits == []


# ---------------------------------------------------------------------------
# SearchToolsMixin — list_files
# ---------------------------------------------------------------------------


def test_list_files_recursive(tmp_path, search_mixin):
    (tmp_path / "a.py").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("")
    list_files = _get_tool("list_files")
    results = list_files(str(tmp_path), pattern="*.py", recursive=True)
    assert len(results) == 2


def test_list_files_excludes_directories(tmp_path, search_mixin):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "sub").mkdir()
    list_files = _get_tool("list_files")
    results = list_files(str(tmp_path), pattern="*", recursive=False)
    # Directory 'sub' should not appear
    from pathlib import Path as _P

    assert all(not _P(r).is_dir() for r in results)


# ---------------------------------------------------------------------------
# SearchToolsMixin — registration
# ---------------------------------------------------------------------------


def test_search_tools_all_registered(search_mixin):
    expected = {"grep", "find_symbol", "list_files", "semantic_search"}
    assert expected.issubset(_TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# SearchToolsMixin — semantic_search (delegates to gaia.code_index)
# ---------------------------------------------------------------------------


def test_semantic_search_warns_when_no_index(tmp_path, search_mixin, caplog):
    """No index built yet → returns [] and emits an actionable WARN.

    The §6.5 contract: never auto-index from a tool call (that's an EM-
    driven operation via `rag refresh`). Just point the EM at the right
    next step.
    """
    semantic_search = _get_tool("semantic_search")
    with caplog.at_level("WARNING", logger="gaia.coder.tools.search"):
        results = semantic_search("how do we verify webhook signatures", repo_path=str(tmp_path))
    assert results == []
    # Surface the build-an-index hint, not silent emptiness.
    assert any("no index found" in r.message for r in caplog.records), (
        "expected a 'no index found' WARN; got: " + repr([r.message for r in caplog.records])
    )


def test_semantic_search_returns_hits_from_indexed_repo(
    tmp_path, search_mixin, monkeypatch
):
    """When the SDK reports indexed=True, results round-trip into SemanticHit dicts."""
    from unittest.mock import MagicMock

    from gaia.code_index.sdk import CodeChunk, SearchResult

    fake_chunk = CodeChunk(
        content="def verify_hmac(body, sig): ...",
        file_path="src/webhook.py",
        language="python",
        start_line=42,
        end_line=58,
        symbol_name="verify_hmac",
        symbol_type="function",
    )
    fake_sdk = MagicMock()
    fake_sdk.get_status.return_value = {"indexed": True}
    fake_sdk.search.return_value = [SearchResult(chunk=fake_chunk, score=0.91, result_type="code")]

    # Patch the lazy-imported CodeIndexSDK constructor inside the helper.
    import gaia.coder.tools.search as search_mod

    def fake_ctor(_config):
        return fake_sdk

    monkeypatch.setattr(search_mod, "_semantic_search_impl", search_mod._semantic_search_impl)
    monkeypatch.setattr("gaia.code_index.CodeIndexSDK", fake_ctor)

    semantic_search = _get_tool("semantic_search")
    hits = semantic_search("verify webhook signature", repo_path=str(tmp_path))

    assert len(hits) == 1
    assert hits[0]["path"] == "src/webhook.py"
    assert hits[0]["start_line"] == 42
    assert hits[0]["symbol_name"] == "verify_hmac"
    assert 0.0 < hits[0]["score"] <= 1.0
    assert "verify_hmac" in hits[0]["snippet"]


def test_semantic_search_raises_when_rag_extra_missing(tmp_path, search_mixin, monkeypatch):
    """Missing faiss/numpy → actionable RuntimeError pointing at the [rag] extra."""
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("gaia.code_index"):
            raise ImportError("No module named 'gaia.code_index' (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    semantic_search = _get_tool("semantic_search")
    with pytest.raises(RuntimeError, match=r"requires the 'rag' extra"):
        semantic_search("anything", repo_path=str(tmp_path))
