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


# ── Tools that resolve against the chat folder since #6065 ──────────────────


@pytest.fixture
def remaining(tmp_path, monkeypatch):
    """The chat folder, an outside folder holding a secret, and a junction out."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    workspace = tmp_path / "chat"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text(f"x = '{SECRET}'\n", encoding="utf-8")
    _junction(workspace / "link", outside)
    monkeypatch.chdir(outside)
    monkeypatch.setattr(
        PathValidator, "_prompt_user_for_access", lambda self, path: False
    )
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield workspace, outside, PathValidator(allowed_paths=[str(workspace)])
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _registered(name):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY[name]["function"]


_FOLDER_ESCAPES = ["..", "../outside", "link", "link/../../outside"]


def test_search_code_cannot_escape_the_chat_folder(remaining):
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin

    workspace, outside, validator = remaining
    host = FileIOToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator
    host.register_file_io_tools()
    search_code = _registered("search_code")

    for folder in _FOLDER_ESCAPES + [str(outside)]:
        result = search_code(pattern="TOPSECRET", directory=folder)
        assert SECRET not in str(result), (folder, result)
        assert "secret.py" not in str(result), (folder, result)


def test_update_gaia_md_writes_the_chat_folder_and_cannot_escape(remaining):
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin

    workspace, outside, validator = remaining
    host = FileIOToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator
    host.register_file_io_tools()
    update = _registered("update_gaia_md")

    for i, folder in enumerate(_FOLDER_ESCAPES + [str(outside)]):
        result = update(project_root=folder, project_name=f"escape{i}")
        assert result["status"] == "error", (folder, result)
        assert not (outside / "GAIA.md").exists(), folder

    # The default "." is the chat folder, not the process folder (outside).
    result = update(project_name="inside")
    assert result["status"] == "success", result
    assert (workspace / "GAIA.md").is_file()
    assert not (outside / "GAIA.md").exists()


def test_watch_directory_cannot_escape_the_chat_folder(remaining):
    from gaia.agents.tools.file_monitor_tools import FileToolsMixin

    workspace, outside, validator = remaining
    host = FileToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator
    host.watch_directories = []
    host.indexed_files = set()
    host._watch_directory = lambda _path: pytest.fail("watched an outside folder")
    host._auto_save_session = lambda: None
    host.register_file_tools()
    add = _registered("add_watch_directory")

    for folder in _FOLDER_ESCAPES + [str(outside)]:
        result = add(folder)
        assert result["status"] == "error", (folder, result)
    assert host.watch_directories == []


def test_shell_working_directory_cannot_escape_the_chat_folder(remaining):
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    workspace, outside, validator = remaining
    host = ShellToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator
    host.register_shell_tools()
    run = _registered("run_shell_command")
    listing = "dir" if sys.platform == "win32" else "ls"

    for folder in _FOLDER_ESCAPES + [str(outside)]:
        result = run(command=listing, working_directory=folder)
        assert result["status"] == "error", (folder, result)
        assert "secret.py" not in str(result.get("stdout", "")), folder


def test_screenshot_output_cannot_escape_the_chat_folder(remaining, monkeypatch):
    from gaia.agents.tools.screenshot_tools import ScreenshotToolsMixin

    workspace, outside, validator = remaining
    host = ScreenshotToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator
    captured = []

    class _Grab:
        size = (1, 1)
        rgb = b"\0\0\0"

    class _Mss:
        monitors = [None]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def grab(self, _monitor):
            return _Grab()

    fake_tools = type(sys)("mss.tools")
    fake_tools.to_png = lambda _rgb, _size, output: (
        captured.append(output),
        Path(output).write_bytes(b"png"),
    )
    fake_mss = type(sys)("mss")
    fake_mss.mss = _Mss
    fake_mss.tools = fake_tools
    monkeypatch.setitem(sys.modules, "mss", fake_mss)
    monkeypatch.setitem(sys.modules, "mss.tools", fake_tools)

    for form in ["../outside/s.png", "link/s.png", str(outside / "s.png")]:
        result = host._take_screenshot(form)
        assert result["status"] == "error", (form, result)
    assert not (outside / "s.png").exists()
    assert captured == []

    result = host._take_screenshot("shot.png")
    assert result["status"] == "success", result
    assert Path(result["file_path"]) == (workspace / "shot.png").resolve()


def _windows_folder_escapes(outside: Path):
    """Windows spellings of an outside folder a relative resolver could mishandle."""
    if sys.platform != "win32":
        return []
    tail = str(outside)[2:]
    return [
        r"..\outside",
        "../outside\\",
        r"link\..\..\outside",
        r"..\outside.",  # Win32 drops a trailing dot
        tail,  # root-relative: no drive, starts at the drive root
        f"{outside.drive}{tail}".upper(),
        rf"\\localhost\{outside.drive[0]}${tail}",
        rf"\\?\{outside}",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path spellings")
def test_windows_spellings_cannot_escape_through_the_resolver(remaining):
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin
    from gaia.agents.tools.file_monitor_tools import FileToolsMixin

    workspace, outside, validator = remaining
    io_host = FileIOToolsMixin()
    io_host.config = _Config(workspace)
    io_host.path_validator = validator
    io_host.register_file_io_tools()
    search_code = _registered("search_code")
    update = _registered("update_gaia_md")

    watch_host = FileToolsMixin()
    watch_host.config = _Config(workspace)
    watch_host.path_validator = validator
    watch_host.watch_directories = []
    watch_host.indexed_files = set()
    watch_host._watch_directory = lambda _p: pytest.fail("watched an outside folder")
    watch_host._auto_save_session = lambda: None
    watch_host.register_file_tools()
    add = _registered("add_watch_directory")

    for folder in _windows_folder_escapes(outside):
        assert SECRET not in str(search_code(pattern="TOPSECRET", directory=folder))
        assert update(project_root=folder)["status"] == "error", folder
        assert add(folder)["status"] == "error", folder
    assert not (outside / "GAIA.md").exists()
    assert watch_host.watch_directories == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-relative paths")
def test_drive_relative_path_stays_in_the_chat_folder(remaining):
    """``C:x`` normally means "x in the process folder" — here, the outside one."""
    from gaia.agents.tools.workspace_paths import _workspace_target

    workspace, outside, _validator = remaining
    host = type("Host", (), {})()
    host.config = _Config(workspace)
    resolved = _workspace_target(host, f"{outside.drive}secret.py")
    assert resolved.parent == workspace.resolve()
    assert resolved.parent != outside.resolve()


def test_download_save_to_cannot_escape_the_chat_folder(remaining):
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    workspace, outside, validator = remaining
    written = []

    class WebClient:
        def download(self, url, save_dir, filename, on_path_resolved):
            dest = Path(save_dir).resolve() / "got.txt"
            on_path_resolved(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("pwned", encoding="utf-8")
            written.append(dest)
            return {
                "filename": "got.txt",
                "path": str(dest),
                "size": 5,
                "content_type": "text/plain",
            }

    host = BrowserToolsMixin()
    host.config = _Config(workspace)
    host._path_validator = validator
    host._web_client = WebClient()
    host.register_browser_tools()
    download = _registered("download_file")

    for folder in _FOLDER_ESCAPES + [str(outside)] + _windows_folder_escapes(outside):
        result = download("https://example.test/f", save_to=folder)
        assert result.startswith("Error"), (folder, result)
    assert written == []
    assert not (outside / "got.txt").exists()

    assert "Saved to" in download("https://example.test/f", save_to="inbox")
    assert written == [(workspace / "inbox" / "got.txt").resolve()]


def test_refine_transcript_cannot_read_or_write_outside(remaining):
    from gaia.agents.tools.audio_tools import AudioToolsMixin

    workspace, outside, validator = remaining
    (outside / "raw.txt").write_text(SECRET, encoding="utf-8")
    (workspace / "raw.txt").write_text("inside", encoding="utf-8")
    host = AudioToolsMixin()
    host.config = _Config(workspace)
    host.path_validator = validator

    for form in ["../outside/raw.txt", "link/raw.txt", str(outside / "raw.txt")]:
        result = host._refine_transcript(form)
        assert result["status"] == "error", (form, result)
        assert SECRET not in str(result)

    for form in ["../outside/out.md", "link/out.md", str(outside / "out.md")]:
        result = host._refine_transcript("raw.txt", output_path=form)
        assert result["status"] == "error", (form, result)
    assert not (outside / "out.md").exists()
