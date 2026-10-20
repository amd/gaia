# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a chat works: ``GAIA_WORKING_DIR`` and a folder picked per chat.

The desktop app starts its backend in the home folder, which is too broad to
grant, so every chat fell back to GAIA's own documents folder with no way to
choose another.
"""

from pathlib import Path

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.ui import _chat_helpers as helpers
from gaia.ui._chat_helpers import (
    WorkingDirEnvError,
    _compute_allowed_paths,
    _managed_documents_dir,
    _project_root,
    env_working_dir,
)
from gaia.ui.database import ChatDatabase


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_gaia_working_dir_replaces_an_unsafe_cwd(project, monkeypatch):
    monkeypatch.chdir(Path.home())
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    assert _compute_allowed_paths([]) == sorted(
        {str(_managed_documents_dir()), str(project.resolve())}
    )


@pytest.mark.parametrize("which", ["home", "missing"])
def test_gaia_working_dir_must_be_a_narrow_existing_folder(
    tmp_path, monkeypatch, which
):
    bad = Path.home() if which == "home" else tmp_path / "nope"
    monkeypatch.setenv("GAIA_WORKING_DIR", str(bad))
    with pytest.raises(WorkingDirEnvError, match="GAIA_WORKING_DIR"):
        env_working_dir()


def test_a_bad_gaia_working_dir_stops_server_startup(tmp_path, monkeypatch):
    from gaia.ui.server import create_app

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_WORKING_DIR", str(tmp_path / "nope"))
    with pytest.raises(WorkingDirEnvError):
        create_app(db_path=":memory:")


def test_a_picked_working_folder_is_granted_beside_documents(project, tmp_path):
    doc = tmp_path / "elsewhere" / "a.txt"
    allowed = _compute_allowed_paths([str(doc)], str(project))
    assert set(allowed) == {
        str(doc.resolve()),
        str(project.resolve()),
        str(_managed_documents_dir()),
    }


def test_a_picked_working_folder_replaces_the_default(project, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    allowed = _compute_allowed_paths([], str(project))
    assert str(cwd.resolve()) not in allowed
    assert str(project.resolve()) in allowed


def test_project_root_is_the_picked_folder_then_the_env_one(project, monkeypatch):
    monkeypatch.delenv("GAIA_WORKING_DIR", raising=False)
    assert _project_root(None) is None
    assert _project_root("/picked") == "/picked"
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    assert _project_root(None) == str(project.resolve())


def test_the_working_folder_is_saved_with_the_chat(project):
    db = ChatDatabase(":memory:")
    sid = db.create_session()["id"]
    assert helpers._session_working_folder(db, sid) is None
    db.set_session_working_folder(sid, str(project))
    db.set_session_environment(sid, folders=["/x"], withheld=[], internet_off=True)
    env = db.get_session_environment(sid)
    assert env["working_folder"] == str(project)
    assert env["folders"] == ["/x"] and env["internet_off"] is True


class _Shell(ShellToolsMixin):
    def __init__(self, project_root):
        self.config = type("C", (), {"project_root": project_root})()

    def _path_allowed(self, _path):
        return True


def test_shell_commands_start_in_the_configured_project(project):
    assert Path(_Shell(str(project)).shell_session.cwd) == project.resolve()
    assert Path(_Shell(None).shell_session.cwd) == Path.cwd()


@pytest.mark.parametrize(
    "argv", [["--working-dir", "D"], ["chat", "--ui", "--working-dir", "D"]]
)
def test_the_cli_takes_a_working_dir(argv):
    from gaia.cli import build_parser

    assert build_parser().parse_args(argv).working_dir == "D"


def test_launching_the_ui_with_a_working_dir_sets_the_env(monkeypatch):
    import gaia.cli as cli
    import gaia.ui.server as server

    # Set first so monkeypatch restores the variable the launch writes.
    monkeypatch.setenv("GAIA_WORKING_DIR", "")
    monkeypatch.setattr(cli, "_ensure_webui_built", lambda log=None: None)

    def stop(**_kwargs):
        raise SystemExit(0)

    monkeypatch.setattr(server, "create_app", stop)
    with pytest.raises(SystemExit):
        cli._launch_agent_ui(working_dir="D:/proj")
    import os

    assert os.environ["GAIA_WORKING_DIR"] == "D:/proj"
