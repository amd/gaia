# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""File and document tools must expand a leading ``~`` (issue #4451).

``Path("~/x")`` is a relative path, so before the fix ``read_file`` answered
"File not found" for ``~/notes.txt`` and a write created a directory literally
named ``~`` in the working directory. Each test points ``HOME`` at a temp
directory, calls the tool with a ``~/...`` path, and asserts the file landed
under that home and that no ``~`` directory appeared in the working directory.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home directory, with the working directory somewhere else."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.chdir(cwd)
    return fake_home.resolve()


def _assert_no_literal_tilde_dir():
    assert not (Path.cwd() / "~").exists()


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


class _FileIOHost(Agent, FileIOToolsMixin):
    def __init__(self, home, **kwargs):
        self.path_validator = PathValidator(allowed_paths=[str(home)])
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_file_io_tools()


@pytest.fixture
def file_io(home):
    with patch("gaia.agents.base.agent.AgentSDK"):
        _FileIOHost(home, skip_lemonade=True, silent_mode=True)


class TestFileIOTools:
    def test_read_file(self, home, file_io):
        (home / "notes.txt").write_text("hello", encoding="utf-8")

        result = _tool("read_file")(file_path="~/notes.txt")

        assert result["status"] == "success"
        assert "hello" in result["content"]

    def test_write_file(self, home, file_io):
        result = _tool("write_file")(file_path="~/out.txt", content="x")

        assert result["status"] == "success"
        assert (home / "out.txt").read_text(encoding="utf-8") == "x"
        _assert_no_literal_tilde_dir()

    def test_edit_file(self, home, file_io):
        (home / "doc.txt").write_text("one two", encoding="utf-8")
        _tool("read_file")(file_path="~/doc.txt")

        result = _tool("edit_file")(
            file_path="~/doc.txt", old_content="two", new_content="2"
        )

        assert result["status"] == "success"
        assert (home / "doc.txt").read_text(encoding="utf-8") == "one 2"
        _assert_no_literal_tilde_dir()

    def test_write_python_file(self, home, file_io):
        result = _tool("write_python_file")(file_path="~/mod.py", content="x = 1\n")

        assert result["status"] == "success"
        assert (home / "mod.py").read_text(encoding="utf-8") == "x = 1\n"
        _assert_no_literal_tilde_dir()

    def test_edit_python_file(self, home, file_io):
        (home / "mod.py").write_text("x = 1\n", encoding="utf-8")
        _tool("read_file")(file_path="~/mod.py")

        result = _tool("edit_python_file")(
            file_path="~/mod.py", old_content="x = 1", new_content="x = 2"
        )

        assert result["status"] == "success"
        assert (home / "mod.py").read_text(encoding="utf-8") == "x = 2\n"
        _assert_no_literal_tilde_dir()

    def test_write_markdown_file(self, home, file_io):
        result = _tool("write_markdown_file")(file_path="~/n.md", content="# Hi\n")

        assert result["status"] == "success"
        assert (home / "n.md").read_text(encoding="utf-8") == "# Hi\n"
        _assert_no_literal_tilde_dir()

    def test_replace_function(self, home, file_io):
        (home / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _tool("read_file")(file_path="~/mod.py")

        result = _tool("replace_function")(
            file_path="~/mod.py",
            function_name="f",
            new_implementation="def f():\n    return 2\n",
        )

        assert result["status"] == "success"
        assert "return 2" in (home / "mod.py").read_text(encoding="utf-8")
        _assert_no_literal_tilde_dir()


@pytest.fixture
def file_search(home):
    FileSearchToolsMixin().register_file_search_tools()


class TestFileSearchTools:
    """``FileSearchToolsMixin`` has its own read_file / write_file / edit_file."""

    def test_read_file(self, home, file_search):
        (home / "notes.txt").write_text("hello", encoding="utf-8")

        result = _tool("read_file")(file_path="~/notes.txt")

        assert result["status"] == "success"
        assert "hello" in result["content"]

    def test_write_file(self, home, file_search):
        result = _tool("write_file")(file_path="~/out.txt", content="x")

        assert result["status"] == "success"
        assert (home / "out.txt").read_text(encoding="utf-8") == "x"
        _assert_no_literal_tilde_dir()

    def test_edit_file(self, home, file_search):
        (home / "doc.txt").write_text("one two", encoding="utf-8")
        _tool("read_file")(file_path="~/doc.txt")

        result = _tool("edit_file")(
            file_path="~/doc.txt", old_content="two", new_content="2"
        )

        assert result["status"] == "success"
        assert (home / "doc.txt").read_text(encoding="utf-8") == "one 2"
        _assert_no_literal_tilde_dir()


class _SandboxedSearchHost(FileSearchToolsMixin):
    """Scoped to the fake home, the way a session that was granted it is."""

    def __init__(self, home):
        self.path_validator = PathValidator(allowed_paths=[str(home)])


@pytest.fixture
def sandboxed_search(home):
    _SandboxedSearchHost(home).register_file_search_tools()


class TestFileSearchReadTools:
    """The read-side tools a data or code question starts with."""

    def test_analyze_data_file(self, home, sandboxed_search):
        (home / "sales.csv").write_text("region,revenue\nN,10\nS,5\n", "utf-8")

        result = _tool("analyze_data_file")(file_path="~/sales.csv")

        assert result.get("status") != "error", result
        assert result["row_count"] == 2

    def test_get_file_info(self, home, sandboxed_search):
        (home / "notes.txt").write_text("hello", encoding="utf-8")

        result = _tool("get_file_info")(file_path="~/notes.txt")

        assert result.get("status") != "error", result
        assert result["file_size_bytes"] == 5

    def test_browse_directory(self, home, sandboxed_search):
        (home / "repo").mkdir()
        (home / "repo" / "a.py").write_text("", encoding="utf-8")

        result = _tool("browse_directory")(directory_path="~/repo")

        assert result["status"] == "success", result
        assert [e["name"] for e in result["entries"]] == ["a.py"]

    def test_search_file_content(self, home, sandboxed_search):
        (home / "repo").mkdir()
        (home / "repo" / "a.py").write_text("def median():\n", encoding="utf-8")

        result = _tool("search_file_content")(pattern="median", directory="~/repo")

        assert result["status"] == "success", result
        assert result["total_matches"] == 1


class _RagHost(RAGToolsMixin):
    def __init__(self):
        self.rag = MagicMock()
        self.rag.index_document.return_value = {"success": True}
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()


@pytest.fixture
def rag(home):
    host = _RagHost()
    host.register_rag_tools()
    return host.rag


class TestRagTools:
    def test_index_document(self, home, rag):
        doc = home / "doc.txt"
        doc.write_text("hello", encoding="utf-8")

        result = _tool("index_document")(file_path="~/doc.txt")

        assert result["status"] == "success"
        rag.index_document.assert_called_once()
        assert rag.index_document.call_args.args == (str(doc),)

    def test_index_directory(self, home, rag):
        docs = home / "docs"
        docs.mkdir()
        (docs / "a.txt").write_text("a", encoding="utf-8")

        result = _tool("index_directory")(directory_path="~/docs")

        assert result["status"] == "success"
        assert result["indexed_count"] == 1
        rag.index_document.assert_called_once_with(str(docs / "a.txt"))
