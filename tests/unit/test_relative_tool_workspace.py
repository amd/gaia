from pathlib import Path
from unittest.mock import patch

from gaia.agents.base import project_map
from gaia.agents.tools.browser_tools import BrowserToolsMixin
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_monitor_tools import FileToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.shell_session import ShellSession
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.agents.tools.workspace_paths import _resolve_target, _workspace_default


class _Config:
    def __init__(self, workspace):
        self.working_directory = str(workspace)


class _Host:
    def __init__(self, workspace):
        self.config = _Config(workspace)


def test_every_relative_resolver_uses_the_session_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)
    host = _Host(workspace)

    assert _workspace_default(host) == str(workspace.resolve())
    assert (
        _resolve_target("helper.py", None, _workspace_default(host))
        == (workspace / "helper.py").resolve()
    )
    assert (
        _resolve_target("./nested/helper.py", None, _workspace_default(host))
        == (workspace / "nested/helper.py").resolve()
    )


def test_project_map_names_the_same_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    line = project_map.working_directory_line(str(workspace))

    assert str(workspace.resolve()) in line
    assert str(sidecar.resolve()) not in line


def test_shell_session_starts_in_the_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    session = ShellSession(start_cwd=str(workspace))

    assert session.cwd == str(workspace.resolve())
    assert session.cwd != str(sidecar.resolve())


class _AllowedScope:
    def __init__(self, *roots, env_scope=None):
        self.allowed_paths = [str(root) for root in roots]
        self.env_scope = env_scope


def test_search_does_not_escape_allowed_paths_for_workspace(tmp_path):
    allowed = tmp_path / "allowed"
    workspace = tmp_path / "outside"
    allowed.mkdir()
    workspace.mkdir()

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.path_validator = _AllowedScope(allowed)
    host.config = _Config(workspace)

    roots = host._search_roots()

    assert roots == [allowed.resolve()]


def test_search_keeps_a_more_specific_allowed_root_first(tmp_path):
    allowed = tmp_path / "allowed"
    workspace = allowed / "chat"
    subproject = workspace / "project"
    subproject.mkdir(parents=True)

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.path_validator = _AllowedScope(allowed, subproject)
    host.config = _Config(workspace)

    assert host._search_roots()[0] == subproject.resolve()


def test_empty_allowed_scope_stays_empty_with_a_workspace(tmp_path):
    workspace = tmp_path / "chat"
    workspace.mkdir()

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.path_validator = _AllowedScope(env_scope=[])
    host.config = _Config(workspace)

    assert host._search_roots() == []


def test_search_prefers_workspace_when_it_is_allowed(tmp_path):
    allowed = tmp_path / "allowed"
    workspace = allowed / "chat"
    workspace.mkdir(parents=True)

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.path_validator = _AllowedScope(allowed)
    host.config = _Config(workspace)

    roots = host._search_roots()

    assert roots[0] == workspace.resolve()
    assert allowed.resolve() in roots


def test_browse_without_workspace_defaults_to_home(tmp_path, monkeypatch):
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    class Host(FileSystemToolsMixin):
        config = None

        def _validate_path(self, path):
            return Path(path).resolve()

    registered = {}

    def fake_tool(**_kwargs):
        def decorate(fn):
            registered[fn.__name__] = fn
            return fn

        return decorate

    with patch("gaia.agents.base.tools.tool", fake_tool):
        host = Host()
        host._bookmarks = {}
        host._fs_index = None
        host._path_validator = None
        host.register_filesystem_tools()

    assert "browse_directory" in registered
    result = registered["browse_directory"]()
    assert str(Path.home().resolve()) in result
    assert str(sidecar) not in result


def _register_tools(host, register):
    registered = {}

    def fake_tool(**_kwargs):
        def decorate(fn):
            registered[fn.__name__] = fn
            return fn

        return decorate

    with patch("gaia.agents.base.tools.tool", fake_tool):
        register(host)
    return registered


def test_file_search_tools_resolve_browse_and_file_info_in_chat_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    (workspace / "helper.py").write_text("chat copy\n", encoding="utf-8")
    (sidecar / "helper.py").write_text("sidecar copy\n", encoding="utf-8")
    monkeypatch.chdir(sidecar)

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    registered = _register_tools(host, FileSearchToolsMixin.register_file_search_tools)

    listing = registered["browse_directory"](".")
    info = registered["get_file_info"]("helper.py")

    assert Path(listing["current_path"]) == workspace.resolve()
    assert Path(info["file_path"]) == (workspace / "helper.py").resolve()


def test_filesystem_tools_resolve_tree_and_file_info_in_chat_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    (workspace / "helper.py").write_text("chat copy\n", encoding="utf-8")
    (sidecar / "helper.py").write_text("sidecar copy\n", encoding="utf-8")
    monkeypatch.chdir(sidecar)

    class Host(FileSystemToolsMixin):
        config = _Config(workspace)

    host = Host()
    host._bookmarks = {}
    host._fs_index = None
    registered = _register_tools(host, FileSystemToolsMixin.register_filesystem_tools)

    tree = registered["tree"](".")
    info = registered["file_info"]("helper.py")

    assert str(workspace.resolve()) in tree
    assert str((workspace / "helper.py").resolve()) in info


def test_shell_uses_workspace_only_when_it_is_allowed(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    allowed = tmp_path / "allowed"
    workspace.mkdir()
    sidecar.mkdir()
    allowed.mkdir()
    monkeypatch.chdir(sidecar)

    class Validator:
        def __init__(self, allowed_path):
            self.allowed_path = allowed_path

        def is_path_allowed(self, path, prompt_user=True):
            return Path(path).resolve() == self.allowed_path.resolve()

    class Host(ShellToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host.path_validator = Validator(allowed)
    assert Path(host.shell_session.cwd) == sidecar.resolve()
    host.reset_shell_session()
    assert Path(host.shell_session.cwd) == sidecar.resolve()

    host.close_shell_session()
    host.path_validator = Validator(workspace)
    assert Path(host.shell_session.cwd) == workspace.resolve()


def test_find_files_resolves_explicit_scope_against_chat_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    (workspace / "src").mkdir(parents=True)
    (sidecar / "src").mkdir(parents=True)
    (workspace / "src" / "chathelper.py").write_text("chat\n", encoding="utf-8")
    (sidecar / "src" / "decoyhelper.py").write_text("decoy\n", encoding="utf-8")
    monkeypatch.chdir(sidecar)

    class Host(FileSystemToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host._fs_index = None
    registered = _register_tools(host, FileSystemToolsMixin.register_filesystem_tools)

    result = registered["find_files"]("helper", scope="src")

    assert "chathelper.py" in result
    assert "decoyhelper.py" not in result


def test_find_files_filters_indexed_scope_against_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    (workspace / "src").mkdir(parents=True)
    (sidecar / "src").mkdir(parents=True)
    chat_file = workspace / "src" / "chathelper.py"
    decoy_file = sidecar / "src" / "decoyhelper.py"
    chat_file.write_text("chat\n", encoding="utf-8")
    decoy_file.write_text("decoy\n", encoding="utf-8")
    monkeypatch.chdir(sidecar)

    class Index:
        def query_files(self, **_kwargs):
            return [
                {"path": str(decoy_file), "size": 1, "modified_at": "now"},
                {"path": str(chat_file), "size": 1, "modified_at": "now"},
            ]

    class Host(FileSystemToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host._fs_index = Index()
    registered = _register_tools(host, FileSystemToolsMixin.register_filesystem_tools)

    result = registered["find_files"]("helper", scope="src")

    assert "chathelper.py" in result
    assert "decoyhelper.py" not in result


def test_shell_relative_working_directory_uses_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    class Host(ShellToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)

    assert host._call_cwd(".") == (str(workspace.resolve()), True)


def test_watch_directory_relative_path_uses_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    (workspace / "report.pdf").write_bytes(b"%PDF")
    (sidecar / "decoy.pdf").write_bytes(b"%PDF")
    monkeypatch.chdir(sidecar)

    class Validator:
        def validate_read(self, path):
            return True, ""

    class Host(FileToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host.path_validator = Validator()
    host.watch_directories = []
    host.indexed_files = set()
    host.rag = type(
        "Rag", (), {"index_document": lambda _self, _path: {"success": True}}
    )()
    watched = []
    host._watch_directory = watched.append
    host._auto_save_session = lambda: None
    registered = _register_tools(host, FileToolsMixin.register_file_tools)

    result = registered["add_watch_directory"](".")

    assert result["status"] == "success"
    assert watched == [str(workspace.resolve())]
    assert str(workspace / "report.pdf") in host.indexed_files
    assert str(sidecar / "decoy.pdf") not in host.indexed_files


def test_search_code_relative_directory_uses_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    (workspace / "answer.py").write_text("needle = 'chat'\n", encoding="utf-8")
    (sidecar / "decoy.py").write_text("needle = 'sidecar'\n", encoding="utf-8")
    monkeypatch.chdir(sidecar)

    class Validator:
        def validate_read(self, _path, **_kwargs):
            return True, ""

    class Host(FileIOToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host.path_validator = Validator()
    registered = {}

    def capture(function=None, **_kwargs):
        if function is not None:
            registered[function.__name__] = function
            return function

        def decorate(inner):
            registered[inner.__name__] = inner
            return inner

        return decorate

    with patch("gaia.agents.tools.file_io_tools.tool", capture):
        host.register_file_io_tools()

    result = registered["search_code"](pattern="needle")

    assert "answer.py" in str(result)
    assert "decoy.py" not in str(result)


def test_download_relative_save_to_uses_chat_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    class WebClient:
        def download(self, **kwargs):
            self.kwargs = kwargs
            return {
                "filename": "report.txt",
                "path": str(Path(kwargs["save_dir"]) / "report.txt"),
                "size": 1,
                "content_type": "text/plain",
            }

    class Host(BrowserToolsMixin):
        pass

    host = Host()
    host.config = _Config(workspace)
    host._path_validator = None
    host._web_client = WebClient()
    registered = _register_tools(host, BrowserToolsMixin.register_browser_tools)

    result = registered["download_file"]("https://example.test/report", save_to="out")

    assert "Saved to" in result
    assert host._web_client.kwargs["save_dir"] == str(workspace / "out")
