# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The scratch directory survives other chats and temp cleaners.

Every chat on one project gets the same scratch path (it is named in the
system prompt, which must stay cacheable), and an agent deletes it on cleanup.
In a long-lived sidecar one chat's cleanup removed the folder another chat was
still running in, and ``run_python`` then failed with "[WinError 267] The
directory name is invalid" for the rest of that chat.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.project_map import PROJECT_ROOT_ENV
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.turn_scope import TurnScopeGuard
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import (
    PathValidator,
    acquire_scratch_dir,
    ensure_scratch_dir,
    release_scratch_dir,
)


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)


class TestScratchHolders:
    def test_the_last_holder_is_the_one_that_may_delete(self, tmp_path):
        anchor = str(tmp_path / "proj")
        first = acquire_scratch_dir(anchor)
        second = acquire_scratch_dir(anchor)
        try:
            assert first == second
            assert release_scratch_dir(first) is False
            assert release_scratch_dir(second) is True
        finally:
            shutil.rmtree(first, ignore_errors=True)

    def test_a_deleted_dir_is_recreated_in_place(self, tmp_path):
        path = tmp_path / "gaia-scratch-x"
        path.mkdir()
        assert ensure_scratch_dir(path) is False
        path.rmdir()
        assert ensure_scratch_dir(path) is True
        assert path.is_dir()

    def test_a_file_in_its_place_is_refused(self, tmp_path):
        path = tmp_path / "gaia-scratch-x"
        path.write_text("not a dir", encoding="utf-8")
        with pytest.raises(OSError):
            ensure_scratch_dir(path)


pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402


@pytest.fixture
def make_agent():
    """Build ChatAgents and hand back each one's own ``run_python``."""
    saved = dict(_TOOL_REGISTRY)
    agents = []

    def build(allowed_path):
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(
                ChatAgentConfig(silent_mode=True, allowed_paths=[str(allowed_path)])
            )
        run_python = _TOOL_REGISTRY["run_python"]["function"]
        agents.append(agent)
        return agent, run_python

    try:
        yield build
    finally:
        for agent in agents:
            agent.__del__()
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def sidecar_cwd(tmp_path, monkeypatch):
    """A UI server whose cwd is not an allowed folder, so snippets use scratch."""
    server = tmp_path / "server"
    server.mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.chdir(server)
    monkeypatch.setattr(
        "gaia.agents.base.project_map.resolve_project_root", lambda explicit: None
    )
    return docs


def _cwd_of(result) -> Path:
    assert result["status"] == "success", result
    return Path(result["stdout"].strip()).resolve()


class TestRunPythonSurvivesADeletedScratchDir:
    def test_deleted_between_calls(self, make_agent, sidecar_cwd):
        agent, run_python = make_agent(sidecar_cwd)
        scratch = Path(agent.scratch_dir)
        assert _cwd_of(run_python(code="import os; print(os.getcwd())")) == (
            scratch.resolve()
        )

        shutil.rmtree(scratch)  # a temp cleaner, or another process's cleanup

        result = run_python(code="import os; print(os.getcwd())")
        assert _cwd_of(result) == scratch.resolve()
        assert scratch.is_dir()

    def test_two_chats_in_one_sidecar(self, make_agent, sidecar_cwd):
        first, _ = make_agent(sidecar_cwd)
        second, run_second = make_agent(sidecar_cwd)
        shared = Path(second.scratch_dir)
        assert Path(first.scratch_dir) == shared

        first.__del__()  # the first chat ends; the second is still running

        assert shared.is_dir()
        assert _cwd_of(run_second(code="import os; print(os.getcwd())")) == (
            shared.resolve()
        )

        second.__del__()
        assert not shared.exists()

    def test_two_chats_share_it_through_a_linked_temp_dir(
        self, make_agent, sidecar_cwd, tmp_path, monkeypatch
    ):
        """macOS ``/var`` -> ``/private/var``: the held and resolved paths differ."""
        real_temp = tmp_path / "real-temp"
        real_temp.mkdir()
        linked_temp = tmp_path / "linked-temp"
        try:
            linked_temp.symlink_to(real_temp, target_is_directory=True)
        except OSError:
            if sys.platform != "win32":
                raise
            import _winapi

            _winapi.CreateJunction(str(real_temp), str(linked_temp))
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(linked_temp))
        first, _ = make_agent(sidecar_cwd)
        second, _ = make_agent(sidecar_cwd)
        shared = Path(second.scratch_dir)

        first.__del__()

        assert shared.is_dir()
        second.__del__()
        assert not shared.exists()

    def test_a_deleted_project_runs_in_scratch_and_says_so(
        self, make_agent, tmp_path, monkeypatch
    ):
        project = tmp_path / "proj"
        project.mkdir()
        agent, run_python = make_agent(project)
        monkeypatch.setattr(
            "gaia.agents.base.project_map.resolve_project_root",
            lambda explicit: str(project),
        )
        project.rmdir()

        result = run_python(code="import os; print(os.getcwd())")

        assert _cwd_of(result) == Path(agent.scratch_dir).resolve()
        assert str(project) in result["note"]
        assert "no longer exists" in result["note"]

    def test_a_start_failure_is_transient_and_actionable(
        self, make_agent, sidecar_cwd, monkeypatch
    ):
        _, run_python = make_agent(sidecar_cwd)

        def fail(*_a, **_k):
            raise NotADirectoryError(267, "The directory name is invalid")

        monkeypatch.setattr("gaia_agent_chat.agent.run_python_job", fail)

        result = run_python(code="print(1)")

        assert result["status"] == "error"
        assert result["transient"] is True
        assert result["executed"] is False
        assert "working folder" in result["error"]
        assert "do not install packages" in result["error"].lower()


class TestTheBreakerIgnoresTransientFailures:
    def test_transient_failures_do_not_end_the_turn(self):
        guard = TurnScopeGuard(failure_limit=2)
        failure = {"status": "error", "error": "folder missing", "transient": True}
        for _ in range(4):
            assert guard.check("run_python", {"code": "print(1)"}) is None
            guard.record("run_python", {"code": "print(1)"}, failure)
        assert not guard.turn_should_end

    def test_ordinary_failures_still_count(self):
        guard = TurnScopeGuard(failure_limit=2)
        failure = {"status": "error", "error": "bad"}
        for code in ("a", "b"):
            guard.record("run_python", {"code": code}, failure)
        assert guard.check("run_python", {"code": "c"}) is not None


class _Shell(ShellToolsMixin):
    debug = False

    def __init__(self, validator):
        super().__init__()
        self.path_validator = validator
        self.console = None


class TestShellSessionFolder:
    def test_a_deleted_scratch_session_folder_is_recreated(self, tmp_path):
        scratch = tmp_path / "gaia-scratch-s"
        scratch.mkdir()
        validator = PathValidator(allowed_paths=[str(tmp_path)])
        validator.set_scratch_dir(str(scratch))
        shell = _Shell(validator)
        assert shell.shell_session.set_cwd(str(scratch))
        scratch.rmdir()

        assert shell._live_session_cwd() == str(scratch.resolve())
        assert scratch.is_dir()

    def test_another_deleted_folder_returns_to_the_start_and_says_so(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        gone = tmp_path / "build"
        gone.mkdir()
        shell = _Shell(PathValidator(allowed_paths=[str(tmp_path)]))
        start = shell.shell_session.cwd
        assert shell.shell_session.set_cwd(str(gone))
        gone.rmdir()

        assert shell._live_session_cwd() == start
        assert str(gone.resolve()) in shell._shell_cwd_note
