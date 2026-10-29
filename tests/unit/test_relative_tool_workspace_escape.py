"""Adversarial: resolving relative paths against the chat workspace must not
let a path climb out of the chat's allowed folders (#5988)."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.security import PathValidator

SECRET = "TOPSECRET-outside-the-chat"


class _Config:
    def __init__(self, workspace):
        self.working_directory = str(workspace)


def _register(host, register):
    registered = {}

    def fake_tool(**_kwargs):
        def decorate(fn):
            registered[fn.__name__] = fn
            return fn

        return decorate

    with patch("gaia.agents.base.tools.tool", fake_tool):
        register(host)
    return registered


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.fixture
def chat(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET, encoding="utf-8")
    _junction(workspace / "link", outside)
    # The process cwd is the outside folder, as a sidecar's cwd is not the chat's.
    monkeypatch.chdir(outside)
    monkeypatch.setattr(
        PathValidator, "_prompt_user_for_access", lambda self, path: False
    )
    validator = PathValidator(allowed_paths=[str(workspace)])

    class Search(FileSearchToolsMixin):
        pass

    class FileSystem(FileSystemToolsMixin):
        pass

    search = Search()
    filesystem = FileSystem()
    for host in (search, filesystem):
        host.config = _Config(workspace)
        host.path_validator = validator
        host._fs_index = None
    filesystem._bookmarks = {}
    tools = {
        "search": _register(search, FileSearchToolsMixin.register_file_search_tools),
        "fs": _register(filesystem, FileSystemToolsMixin.register_filesystem_tools),
    }
    return tools, workspace, outside


def _escapes(outside: Path):
    forms = [
        "../outside/secret.txt",
        "link/secret.txt",
        "link/../../outside/secret.txt",
        str(outside / "secret.txt"),
    ]
    if sys.platform == "win32":
        drive = outside.drive
        forms.append("..\\outside\\secret.txt")
        # Drive-relative: Windows resolves it against the process cwd.
        forms.append(f"{drive}secret.txt")
        forms.append(f"\\\\localhost\\{drive[0]}$" + str(outside / "secret.txt")[2:])
    return forms


def test_reads_cannot_escape_the_chat_folder(chat):
    tools, _workspace, outside = chat
    readers = [
        tools["search"]["read_file"],
        tools["search"]["get_file_info"],
        tools["fs"]["file_info"],
    ]
    for form in _escapes(outside):
        for read in readers:
            result = read(form)
            assert SECRET not in str(result), (read.__name__, form, result)


def test_listings_cannot_escape_the_chat_folder(chat):
    tools, _workspace, outside = chat
    folders = ["..", "../outside", "link", str(outside)]
    for folder in folders:
        for browse in (
            tools["search"]["browse_directory"],
            tools["fs"]["browse_directory"],
        ):
            result = browse(folder)
            assert "secret.txt" not in str(result), (browse.__name__, folder, result)
        found = tools["fs"]["find_files"]("secret", scope=folder)
        assert "secret.txt" not in str(found), (folder, found)


def test_writes_cannot_escape_the_chat_folder(chat):
    tools, _workspace, outside = chat
    forms = ["../outside/w{}.txt", "link/w{}.txt", str(outside / "w{}.txt")]
    for i, form in enumerate(forms):
        tools["search"]["write_file"](form.format(i), "pwned")
        assert not (outside / f"w{i}.txt").exists(), form


def test_a_relative_path_inside_the_chat_still_works(chat):
    tools, workspace, _outside = chat
    (workspace / "notes.txt").write_text("inside", encoding="utf-8")
    assert "inside" in str(tools["search"]["read_file"]("notes.txt"))
    assert "notes.txt" in str(tools["fs"]["find_files"]("notes", scope="."))
