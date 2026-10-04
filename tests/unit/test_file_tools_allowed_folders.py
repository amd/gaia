# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every tool that takes a path checks the same allowed-folders rule.

Each tool gets a folder outside the session's scope, reached three ways — by
its own path, through ``..``, and through a symlink inside the scope — and must
send it through the validator's approval prompt and refuse when the user says
no. A path inside the scope must still work.
"""

import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.audio_tools import AudioToolsMixin
from gaia.agents.tools.code_index_tools import (
    _CODE_INDEX_AVAILABLE,
    CodeIndexToolsMixin,
)
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_monitor_tools import FileToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.agents.tools.screenshot_tools import ScreenshotToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Prompt:
    """The host's approval dialog: records what it was asked, answers *answer*."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def __call__(self, path):
        self.asked.append(Path(path))
        return self.answer


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """An allowed folder, an outside folder, and links from one to the other.

    pytest's tmp_path is in the system temp dir, which is never askable, so the
    temp rule points at a dedicated folder instead.
    """
    fake_temp = tmp_path / "systemp"
    fake_temp.mkdir()
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(fake_temp)})
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (allowed / "notes.txt").write_text("needle inside\n", encoding="utf-8")
    (outside / "secret.txt").write_text("needle outside\n", encoding="utf-8")
    (allowed / "project").mkdir()
    (outside / "project").mkdir()
    links = True
    try:
        (allowed / "linked_dir").symlink_to(outside, target_is_directory=True)
        (allowed / "linked.txt").symlink_to(outside / "secret.txt")
    except OSError:  # Windows without symlink privilege
        links = False
    return SimpleNamespace(
        allowed=allowed.resolve(), outside=outside.resolve(), links=links
    )


def _outside_forms(tree):
    """The outside folder, reached directly, through ``..`` and through a link."""
    forms = {
        "direct": str(tree.outside),
        "dotdot": str(tree.allowed / ".." / "outside"),
    }
    if tree.links:
        forms["symlink"] = str(tree.allowed / "linked_dir")
    return forms


OUTSIDE_FORMS = ["direct", "dotdot", "symlink"]


def _outside(tree, form):
    forms = _outside_forms(tree)
    if form not in forms:
        pytest.skip("symlinks unavailable on this platform")
    return forms[form]


@pytest.fixture
def validator(tree):
    v = PathValidator(allowed_paths=[str(tree.allowed)])
    v.set_access_prompt(_Prompt(False))
    return v


def _asked(validator):
    return validator._access_prompt.asked


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _refused(result):
    text = result if isinstance(result, str) else json.dumps(result)
    return "not in allowed paths" in text


# ── PathValidator itself ────────────────────────────────────────────────────


class TestValidatorExpandsHome:
    def test_tilde_is_judged_as_the_home_folder(self, tree, monkeypatch):
        """Not as a folder named ``~`` inside an allowed working directory."""
        monkeypatch.setenv("HOME", str(tree.allowed))
        monkeypatch.setenv("USERPROFILE", str(tree.allowed))
        monkeypatch.chdir(tree.outside)
        v = PathValidator(allowed_paths=[str(tree.outside)])

        assert not v.is_path_allowed("~/notes.txt", prompt_user=False)

    def test_tilde_inside_the_scope_is_allowed(self, tree, monkeypatch):
        monkeypatch.setenv("HOME", str(tree.allowed))
        monkeypatch.setenv("USERPROFILE", str(tree.allowed))
        v = PathValidator(allowed_paths=[str(tree.allowed)])

        assert v.is_path_allowed("~/notes.txt", prompt_user=False)


# ── FileSearchToolsMixin ────────────────────────────────────────────────────


class _SearchHost(FileSearchToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


@pytest.fixture
def search_tools(validator):
    _SearchHost(validator).register_file_search_tools()
    return validator


class TestBrowseDirectory:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_is_asked_then_refused(self, tree, search_tools, form):
        result = _tool("browse_directory")(directory_path=_outside(tree, form))

        assert result["status"] == "error"
        assert _refused(result)
        assert "entries" not in result
        assert _asked(search_tools) == [tree.outside]

    def test_inside_lists_the_folder(self, tree, search_tools):
        result = _tool("browse_directory")(directory_path=str(tree.allowed))

        assert result["status"] == "success"
        assert "notes.txt" in [e["name"] for e in result["entries"]]

    def test_approval_lets_the_listing_through(self, tree, search_tools):
        search_tools.set_access_prompt(_Prompt(True))

        result = _tool("browse_directory")(directory_path=str(tree.outside))

        assert result["status"] == "success"
        assert [e["name"] for e in result["entries"]] == ["project", "secret.txt"]


class TestSearchDirectory:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_root_is_refused(self, tree, search_tools, form):
        result = _tool("search_directory")(
            directory_name="project", search_root=_outside(tree, form)
        )

        assert result["status"] == "error"
        assert _refused(result)
        assert _asked(search_tools) == [tree.outside]

    def test_default_stays_in_the_allowed_folders(self, tree, search_tools):
        result = _tool("search_directory")(directory_name="project")

        assert result["status"] == "success"
        assert result["directories"] == [str(tree.allowed / "project")]


class TestSearchFileContent:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_folder_is_refused(self, tree, search_tools, form):
        result = _tool("search_file_content")(
            pattern="needle", directory=_outside(tree, form)
        )

        assert result["status"] == "error"
        assert _refused(result)

    def test_a_link_out_of_the_folder_is_not_grepped(self, tree, search_tools):
        result = _tool("search_file_content")(
            pattern="needle", directory=str(tree.allowed)
        )

        assert result["status"] == "success"
        assert [m["content"] for m in result["matches"]] == ["needle inside"]
        assert _asked(search_tools) == []


class TestListRecentFiles:
    @pytest.fixture
    def home(self, tree, monkeypatch):
        home = tree.allowed.parent
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        docs = home / "Documents"
        docs.mkdir()
        (docs / "report.txt").write_text("q3", encoding="utf-8")
        return docs.resolve()

    def test_folder_outside_scope_is_asked_then_refused(self, home, search_tools):
        result = _tool("list_recent_files")(location="documents")

        assert result["status"] == "error"
        assert _refused(result)
        assert _asked(search_tools) == [home]

    def test_folder_in_scope_is_listed(self, home, search_tools):
        search_tools.add_allowed_path(str(home))

        result = _tool("list_recent_files")(location="documents")

        assert result["status"] == "success"
        assert [f["file_name"] for f in result["files"]] == ["report.txt"]


# ── FileSystemToolsMixin ────────────────────────────────────────────────────


class _FsHost(FileSystemToolsMixin):
    """Binds only ``path_validator`` — the name most hosts use."""

    def __init__(self, validator):
        self.path_validator = validator
        self._bookmarks = {}


@pytest.fixture
def fs_tools(validator):
    _FsHost(validator).register_filesystem_tools()
    return validator


class TestFileSystemTools:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_browse_outside_is_refused(self, tree, fs_tools, form):
        result = _tool("browse_directory")(path=_outside(tree, form))

        assert _refused(result)
        assert "secret.txt" not in result

    def test_browse_inside_lists_the_folder(self, tree, fs_tools):
        assert "notes.txt" in _tool("browse_directory")(path=str(tree.allowed))

    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_find_files_scope_outside_is_refused(self, tree, fs_tools, form):
        result = _tool("find_files")(
            query="secret", search_type="name", scope=_outside(tree, form)
        )

        assert _refused(result)
        assert "secret.txt" not in result

    def test_find_files_scope_inside_searches(self, tree, fs_tools):
        result = _tool("find_files")(
            query="notes", search_type="name", scope=str(tree.allowed)
        )

        assert "notes.txt" in result


# ── FileIOToolsMixin ────────────────────────────────────────────────────────


class _IOHost(FileIOToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


@pytest.fixture
def io_tools(validator):
    _IOHost(validator).register_file_io_tools()
    return validator


class TestSearchCode:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_folder_is_refused(self, tree, io_tools, form):
        result = _tool("search_code")(
            directory=_outside(tree, form), pattern="needle", file_extension=".txt"
        )

        assert result["status"] == "error"
        assert _refused(result)

    def test_a_link_out_of_the_folder_is_not_grepped(self, tree, io_tools):
        result = _tool("search_code")(
            directory=str(tree.allowed), pattern="needle", file_extension=".txt"
        )

        assert result["status"] == "success"
        assert [r["file"] for r in result["results"]] == ["notes.txt"]


# ── RAGToolsMixin ───────────────────────────────────────────────────────────


class _RagHost(RAGToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator
        self.rag = MagicMock()
        self.rag.index_document.return_value = {"success": True}
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()


@pytest.fixture
def rag_host(validator):
    host = _RagHost(validator)
    host.register_rag_tools()
    return host


class TestRagTools:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_index_document_outside_is_asked_then_refused(self, tree, rag_host, form):
        path = os.path.join(_outside(tree, form), "secret.txt")

        result = _tool("index_document")(file_path=path)

        assert result["status"] == "error"
        assert _refused(result)
        assert _asked(rag_host.path_validator) == [tree.outside / "secret.txt"]
        rag_host.rag.index_document.assert_not_called()

    def test_index_document_refuses_a_secret_inside_the_scope(self, tree, rag_host):
        (tree.allowed / ".env").write_text("TOKEN=x", encoding="utf-8")

        result = _tool("index_document")(file_path=str(tree.allowed / ".env"))

        assert result["status"] == "error"
        rag_host.rag.index_document.assert_not_called()

    def test_index_document_inside_indexes(self, tree, rag_host):
        result = _tool("index_document")(file_path=str(tree.allowed / "notes.txt"))

        assert result["status"] == "success"
        rag_host.rag.index_document.assert_called_once_with(
            str(tree.allowed / "notes.txt")
        )

    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_index_directory_outside_is_refused(self, tree, rag_host, form):
        result = _tool("index_directory")(directory_path=_outside(tree, form))

        assert result["status"] == "error"
        assert _refused(result)
        rag_host.rag.index_document.assert_not_called()

    def test_index_directory_skips_a_link_out_of_the_folder(self, tree, rag_host):
        result = _tool("index_directory")(directory_path=str(tree.allowed))

        assert result["status"] == "success"
        rag_host.rag.index_document.assert_called_once_with(
            str(tree.allowed / "notes.txt")
        )

    def test_dump_document_refuses_an_output_outside(self, tree, rag_host):
        doc = str(tree.allowed / "notes.txt")
        rag_host.rag.indexed_files = [doc]
        rag_host.rag.file_metadata = {doc: {"full_text": "hello"}}
        target = tree.outside / "dump.md"

        result = _tool("dump_document")(file_name="notes.txt", output_path=str(target))

        assert result["status"] == "error"
        assert not target.exists()

    def test_dump_document_writes_an_output_inside(self, tree, rag_host):
        doc = str(tree.allowed / "notes.txt")
        rag_host.rag.indexed_files = [doc]
        rag_host.rag.file_metadata = {doc: {"full_text": "hello"}}
        target = tree.allowed / "dump.md"

        result = _tool("dump_document")(file_name="notes.txt", output_path=str(target))

        assert result["status"] == "success"
        assert "hello" in target.read_text(encoding="utf-8")


# ── Folder watching, code index ─────────────────────────────────────────────


class _WatchHost(FileToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator
        self.watch_directories = []
        self._watch_directory = MagicMock()
        self._auto_save_session = MagicMock()
        self.rag = MagicMock()
        self.indexed_files = set()


class TestAddWatchDirectory:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_is_refused(self, tree, validator, form):
        host = _WatchHost(validator)
        host.register_file_tools()

        result = _tool("add_watch_directory")(directory=_outside(tree, form))

        assert result["status"] == "error"
        assert _refused(result)
        host._watch_directory.assert_not_called()

    def test_inside_is_watched(self, tree, validator):
        host = _WatchHost(validator)
        host.register_file_tools()

        result = _tool("add_watch_directory")(directory=str(tree.allowed))

        assert result["status"] == "success"
        host._watch_directory.assert_called_once_with(str(tree.allowed))


class _CodeIndexHost(CodeIndexToolsMixin):
    def __init__(self, validator, ceiling):
        self.path_validator = validator
        self._init_code_index_state(repo_path=ceiling, ceiling_paths=[ceiling])
        self.register_code_index_tools()


@pytest.mark.skipif(
    not _CODE_INDEX_AVAILABLE, reason="code index dependencies not installed"
)
class TestIndexCodebase:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_outside_is_refused_even_under_the_ceiling(self, tree, validator, form):
        _CodeIndexHost(validator, str(tree.allowed.parent))

        result = json.loads(_tool("index_codebase")(repo_path=_outside(tree, form)))

        assert _refused(result)

    def test_inside_is_indexed(self, tree, validator):
        host = _CodeIndexHost(validator, str(tree.allowed.parent))
        sdk = MagicMock()
        sdk.index_repository.return_value = SimpleNamespace(
            files_indexed=1, chunks_created=2
        )
        host._get_code_index_sdk = lambda: sdk

        result = json.loads(_tool("index_codebase")(repo_path=str(tree.allowed)))

        assert result["status"] == "ok"


# ── Files a tool saves: screenshots, transcripts ────────────────────────────


@pytest.fixture
def fake_mss(monkeypatch):
    """A capture backend that writes a tiny file instead of grabbing the screen."""
    tools_mod = types.ModuleType("mss.tools")
    tools_mod.to_png = lambda rgb, size, output: Path(output).write_bytes(b"png")
    grab = SimpleNamespace(rgb=b"", size=(2, 1))
    shot = MagicMock(monitors=[{}], grab=MagicMock(return_value=grab))
    shot.__enter__ = MagicMock(return_value=shot)
    shot.__exit__ = MagicMock(return_value=False)
    mss_mod = types.ModuleType("mss")
    mss_mod.mss = MagicMock(return_value=shot)
    mss_mod.tools = tools_mod
    monkeypatch.setitem(sys.modules, "mss", mss_mod)
    monkeypatch.setitem(sys.modules, "mss.tools", tools_mod)


class _ShotHost(ScreenshotToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


class TestTakeScreenshot:
    def test_output_outside_is_refused(self, tree, validator, fake_mss):
        target = tree.outside / "shot.png"

        result = _ShotHost(validator)._take_screenshot(str(target))

        assert result["status"] == "error"
        assert _refused(result)
        assert not target.exists()

    def test_output_inside_is_saved(self, tree, validator, fake_mss):
        target = tree.allowed / "shot.png"

        result = _ShotHost(validator)._take_screenshot(str(target))

        assert result["status"] == "success"
        assert target.exists()


class _AudioHost(AudioToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


class TestAudioTools:
    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_transcribe_outside_is_refused(self, tree, validator, form):
        media = os.path.join(_outside(tree, form), "secret.txt")

        result = _AudioHost(validator)._transcribe_media(media)

        assert result["status"] == "error"
        assert _refused(result)

    def test_transcribe_inside_gets_past_the_check(self, tree, validator):
        result = _AudioHost(validator)._transcribe_media(
            str(tree.allowed / "missing.mp3")
        )

        assert "No such file" in result["error"]

    def test_transcribe_refuses_an_output_outside(self, tree, validator):
        result = _AudioHost(validator)._transcribe_media(
            str(tree.allowed / "missing.mp3"),
            output_path=str(tree.outside / "t.txt"),
        )

        assert _refused(result)

    @pytest.mark.parametrize("form", OUTSIDE_FORMS)
    def test_refine_outside_is_refused(self, tree, validator, form):
        transcript = os.path.join(_outside(tree, form), "secret.txt")

        result = _AudioHost(validator)._refine_transcript(transcript)

        assert result["status"] == "error"
        assert _refused(result)

    def test_refine_inside_gets_past_the_check(self, tree, validator):
        result = _AudioHost(validator)._refine_transcript(
            str(tree.allowed / "missing.txt")
        )

        assert "No transcript at" in result["error"]
