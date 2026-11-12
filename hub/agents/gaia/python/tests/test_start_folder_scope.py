# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The folder a terminal session was started in is a folder of that session.

The terminal app and ``gaia -p`` both send it as ``working_directory`` and as
the one entry of ``allowed_paths``. The prompt must then name it, not the home
folder, as where to look first, and a read there must not ask.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def started_in(monkeypatch, tmp_path):
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "home" / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("GAIA_ALLOWED_PATHS", raising=False)
    sidecar = tmp_path / "sidecar"
    start = tmp_path / "start"
    sidecar.mkdir()
    start.mkdir()
    (start / "note.txt").write_text("cobalt-finch-73", encoding="utf-8")
    # The daemon starts the agent in its own folder, never the user's.
    monkeypatch.chdir(sidecar)
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    agent = GaiaAgent(
        config=GaiaAgentConfig(
            silent_mode=True,
            working_directory=str(start),
            allowed_paths=[str(start)],
        )
    )
    try:
        yield start, agent
    finally:
        agent.close()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_prompt_names_the_start_folder_as_the_place_to_look(started_in):
    start, agent = started_in
    prompt = agent._compose_system_prompt()

    # Exactly the start folder: the home folder is not also offered.
    assert f"Your folders this session: {start.resolve()}. For" in prompt
    assert f"Working directory: {start.resolve()}" in prompt


def test_a_bare_name_is_read_from_the_start_folder_without_asking(started_in):
    start, agent = started_in

    def refuse_to_ask(*_args, **_kwargs):
        raise AssertionError("a read in the start folder asked for approval")

    agent.console.confirm_tool_execution = refuse_to_ask
    agent.path_validator._prompt_user_for_access = refuse_to_ask

    assert agent.path_validator.is_path_allowed(str(start / "note.txt"))
    result = agent._execute_tool("read_file", {"file_path": "note.txt"})

    assert "cobalt-finch-73" in str(result)


@pytest.mark.parametrize("escape", ["../outside.txt", "sub/../../outside.txt"])
def test_a_relative_name_cannot_climb_out_of_the_start_folder_unasked(
    started_in, escape
):
    start, agent = started_in
    (start.parent / "outside.txt").write_text("violet-otter-19", encoding="utf-8")
    asked = []

    def decline(*args, **_kwargs):
        asked.append(args)
        return False

    agent.path_validator._prompt_user_for_access = decline

    assert not agent.path_validator.is_path_allowed(
        str(start.parent / "outside.txt"), prompt_user=False
    )
    result = agent._execute_tool("read_file", {"file_path": escape})

    assert "violet-otter-19" not in str(result)
    assert asked, "a read outside the start folder did not ask first"
