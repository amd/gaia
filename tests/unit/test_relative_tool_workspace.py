from pathlib import Path

from gaia.agents.tools.workspace_paths import _resolve_target, _workspace_default
from gaia.agents.tools.shell_session import ShellSession
from gaia.agents.base import project_map


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
    assert _resolve_target("helper.py", None, _workspace_default(host)) == (
        workspace / "helper.py"
    ).resolve()
    assert _resolve_target("./nested/helper.py", None, _workspace_default(host)) == (
        workspace / "nested/helper.py"
    ).resolve()


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
