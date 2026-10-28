from pathlib import Path
from unittest.mock import patch

from gaia.agents.base import project_map
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.shell_session import ShellSession
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
