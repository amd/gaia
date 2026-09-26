# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where and how ChatAgent's SessionManager stores session transcripts."""

import json
from pathlib import Path

import pytest
from gaia_agent_chat.session import SessionCorruptError, SessionManager

from gaia import config as gaia_config


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia-home"
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_DIR", home)
    return home


@pytest.fixture
def work_cwd(tmp_path, monkeypatch):
    cwd = tmp_path / "some-repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return cwd


def test_default_dir_is_gaia_home_not_cwd(gaia_home, work_cwd):
    manager = SessionManager(auto_cleanup=False)
    session = manager.create_session("session_1")
    assert manager.save_session(session)

    assert manager.session_dir == gaia_home / "sessions"
    assert (gaia_home / "sessions" / "session_1.json").is_file()
    assert list(work_cwd.iterdir()) == []


@pytest.mark.usefixtures("work_cwd")
@pytest.mark.parametrize(
    "bad_id", ["../x", "..", ".", "a/b", "a\\b", "", "x" * 129, "id with space"]
)
def test_unsafe_session_id_rejected(gaia_home, bad_id):
    manager = SessionManager(auto_cleanup=False)
    with pytest.raises(ValueError, match="session_id"):
        manager.load_session(bad_id)
    with pytest.raises(ValueError, match="session_id"):
        manager.create_session(bad_id)
    assert not (gaia_home / "x.json").exists()


@pytest.mark.usefixtures("gaia_home")
def test_ui_style_ids_accepted():
    manager = SessionManager(auto_cleanup=False)
    for sid in ("session_20260924_101010", "3f2b9c1e-8a7d-4f00-9b1a-0c2d3e4f5a6b"):
        assert manager.save_session(manager.create_session(sid))
        assert manager.load_session(sid).session_id == sid


@pytest.mark.usefixtures("gaia_home")
def test_missing_session_returns_none():
    manager = SessionManager(auto_cleanup=False)
    assert manager.load_session("never_saved") is None


@pytest.mark.usefixtures("gaia_home")
def test_truncated_session_is_loud_and_not_overwritten():
    manager = SessionManager(auto_cleanup=False)
    session = manager.create_session("session_keep")
    session.chat_history = [{"role": "user", "content": "important"}]
    assert manager.save_session(session)

    path = manager.session_dir / "session_keep.json"
    truncated = path.read_text(encoding="utf-8")[:40]
    path.write_text(truncated, encoding="utf-8")

    with pytest.raises(SessionCorruptError, match=str(path)):
        manager.load_session("session_keep")
    assert path.read_text(encoding="utf-8") == truncated


@pytest.mark.usefixtures("gaia_home")
def test_save_is_atomic_and_leaves_no_temp_files():
    manager = SessionManager(auto_cleanup=False)
    session = manager.create_session("session_atomic")
    assert manager.save_session(session)
    assert manager.save_session(session)

    files = sorted(p.name for p in manager.session_dir.iterdir())
    assert files == ["session_atomic.json"]
    data = json.loads((manager.session_dir / "session_atomic.json").read_text())
    assert data["session_id"] == "session_atomic"


@pytest.mark.usefixtures("gaia_home")
def test_failed_write_keeps_previous_file(monkeypatch):
    manager = SessionManager(auto_cleanup=False)
    session = manager.create_session("session_prev")
    session.metadata = {"v": 1}
    assert manager.save_session(session)
    path = manager.session_dir / "session_prev.json"
    before = path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("gaia_agent_chat.session.json.dump", boom)
    session.metadata = {"v": 2}
    assert manager.save_session(session) is False

    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in Path(manager.session_dir).iterdir()] == [
        "session_prev.json"
    ]


@pytest.fixture
def isolated_tool_registry():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.mark.usefixtures("isolated_tool_registry")
def test_agent_refuses_to_replace_corrupt_ui_session(gaia_home, work_cwd, monkeypatch):
    from unittest.mock import patch

    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    monkeypatch.setenv("HOME", str(work_cwd.parent))
    sessions = gaia_home / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "ui-session-1.json"
    path.write_text('{"session_id": "ui-session-1", "chat_hist', encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
        pytest.raises(SessionCorruptError),
    ):
        ChatAgent(
            ChatAgentConfig(
                prompt_profile="doc", silent_mode=True, ui_session_id="ui-session-1"
            )
        )

    assert path.read_text(encoding="utf-8") == before
