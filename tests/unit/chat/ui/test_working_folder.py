# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GAIA_WORKING_DIR`` / ``--working-dir``: the folder a chat works in by default.

The desktop app starts its backend in the home folder, which is too broad to
grant, so every chat without a picked folder fell back to GAIA's own documents
folder with no way to choose another default.
"""

import os
from pathlib import Path

import pytest

from gaia.ui import start_screen
from gaia.ui._chat_helpers import (
    WorkingDirEnvError,
    _compute_allowed_paths,
    _managed_documents_dir,
    _session_working_directory,
    default_working_dir,
    env_working_dir,
)


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("GAIA_WORKING_DIR", raising=False)
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)


def test_unset_or_blank_means_the_start_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert env_working_dir() is None
    monkeypatch.setenv("GAIA_WORKING_DIR", "   ")
    assert env_working_dir() is None
    assert default_working_dir() == tmp_path.resolve()


def test_gaia_working_dir_replaces_an_unsafe_cwd(project, monkeypatch):
    monkeypatch.chdir(Path.home())
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    assert _compute_allowed_paths([]) == sorted(
        {str(_managed_documents_dir()), str(project.resolve())}
    )
    assert _session_working_directory() == str(project.resolve())


def test_gaia_working_dir_replaces_a_safe_cwd_too(project, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    allowed = _compute_allowed_paths([])
    assert str(project.resolve()) in allowed
    assert str(cwd.resolve()) not in allowed


def test_a_folder_the_chat_picked_beats_the_env_default(project, tmp_path, monkeypatch):
    picked = tmp_path / "picked"
    picked.mkdir()
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    assert _session_working_directory([str(picked)]) == str(picked.resolve())


def test_attached_documents_still_narrow_the_scope(project, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    doc = tmp_path / "elsewhere" / "a.txt"
    allowed = _compute_allowed_paths([str(doc)])
    assert str(project.resolve()) not in allowed


@pytest.mark.parametrize("which", ["home", "missing", "file", "root"])
def test_gaia_working_dir_must_be_a_narrow_existing_folder(
    tmp_path, monkeypatch, which
):
    if which == "file":
        bad = tmp_path / "a.txt"
        bad.write_text("x")
    else:
        bad = {
            "home": Path.home(),
            "missing": tmp_path / "nope",
            "root": Path(Path.cwd().anchor),
        }[which]
    monkeypatch.setenv("GAIA_WORKING_DIR", str(bad))
    with pytest.raises(WorkingDirEnvError, match="GAIA_WORKING_DIR"):
        env_working_dir()
    # Never quietly granted: every scope path raises instead.
    with pytest.raises(WorkingDirEnvError):
        _compute_allowed_paths([])
    with pytest.raises(WorkingDirEnvError):
        _session_working_directory()


def test_a_home_subfolder_reached_through_dots_is_still_home(monkeypatch):
    monkeypatch.setenv("GAIA_WORKING_DIR", str(Path.home() / "x" / ".." / "."))
    with pytest.raises(WorkingDirEnvError, match="home"):
        env_working_dir()


def test_a_bad_gaia_working_dir_stops_server_startup(tmp_path, monkeypatch):
    from gaia.ui.server import create_app

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_WORKING_DIR", str(tmp_path / "nope"))
    with pytest.raises(WorkingDirEnvError):
        create_app(db_path=":memory:")


def test_start_screen_shows_the_env_folder(project, monkeypatch):
    monkeypatch.chdir(Path.home())
    monkeypatch.setenv("GAIA_WORKING_DIR", str(project))
    shown = start_screen.working_folder(choosable=True)
    assert shown["path"] == str(project.resolve())
    assert shown["gaia_folder"] is False


def test_start_screen_survives_an_env_folder_deleted_after_startup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GAIA_WORKING_DIR", str(tmp_path / "gone"))
    shown = start_screen.working_folder(choosable=True)
    assert shown["path"] == str(_managed_documents_dir())
    assert shown["gaia_folder"] is True


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
    assert os.environ["GAIA_WORKING_DIR"] == "D:/proj"


def test_launching_the_ui_without_a_working_dir_leaves_the_env(monkeypatch):
    import gaia.cli as cli
    import gaia.ui.server as server

    monkeypatch.setenv("GAIA_WORKING_DIR", "kept")
    monkeypatch.setattr(cli, "_ensure_webui_built", lambda log=None: None)

    def stop(**_kwargs):
        raise SystemExit(0)

    monkeypatch.setattr(server, "create_app", stop)
    with pytest.raises(SystemExit):
        cli._launch_agent_ui()
    assert os.environ["GAIA_WORKING_DIR"] == "kept"


def test_the_server_runner_reports_a_bad_working_dir(tmp_path, monkeypatch, capsys):
    import sys

    import gaia.ui.server as server

    monkeypatch.setenv("GAIA_WORKING_DIR", "")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(
        sys, "argv", ["server", "--working-dir", str(tmp_path / "nope")]
    )
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 64
    assert "GAIA_WORKING_DIR" in capsys.readouterr().err


def test_the_session_panel_names_a_bad_working_dir(tmp_path, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from gaia.ui.database import ChatDatabase
    from gaia.ui.routers import environment

    db = ChatDatabase(":memory:")
    session = db.get_session(db.create_session()["id"])
    monkeypatch.setenv("GAIA_WORKING_DIR", str(tmp_path / "gone"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(environment._describe(db, session))
    assert exc.value.status_code == 500
    assert "GAIA_WORKING_DIR" in exc.value.detail
