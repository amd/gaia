# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Full access means full access: any command, any file.

Each case runs the same call in both modes. Under full access it must work;
by default it must still be refused or asked about, exactly as before.
"""

import os
import shutil
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import PathValidator

_WINDOWS = os.name == "nt"


class _Agent(Agent, FileIOToolsMixin, ShellToolsMixin):
    def __init__(self, scope, full_access):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        console = SilentConsole()
        console.full_access = full_access
        console.auto_approve_gated_tools = full_access
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )
        ShellToolsMixin.__init__(self)

    def _get_system_prompt(self) -> str:
        return "full access"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        self.register_shell_tools()


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """A project, a stand-in system folder and a credential file outside it."""
    monkeypatch.setattr(
        "gaia.security._system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    project = tmp_path / "project"
    project.mkdir()
    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setattr(
        "gaia.security.BLOCKED_DIRECTORIES", {str(system.resolve())}, raising=True
    )
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    env_file = secrets / ".env"
    env_file.write_text("API_KEY=hunter2\n", encoding="utf-8")
    return {"project": project, "system": system, "env": env_file}


@pytest.fixture
def audit(monkeypatch):
    records = []
    monkeypatch.setattr(
        "gaia.security.audit_shell_command", lambda **kw: records.append(kw)
    )
    return records


def _shell(agent, command, cwd=None, **extra):
    args = {"command": command, **extra}
    if cwd is not None:
        args["working_directory"] = str(cwd)
    return agent._execute_tool("run_shell_command", args)


def _local_program(folder: Path) -> str:
    """A program in *folder* that no allowlist names, and how to call it."""
    if _WINDOWS:
        (folder / "hello-tool.cmd").write_text("@echo local tool ran\r\n")
        return ".\\hello-tool.cmd"
    script = folder / "hello-tool"
    script.write_text("#!/bin/sh\necho local tool ran\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return "./hello-tool"


def _delete(name: str) -> str:
    return f"rm {name}" if not _WINDOWS or shutil.which("rm") else f"del {name}"


def _read(path) -> str:
    return f'type "{path}"' if _WINDOWS else f"cat '{path}'"


class TestShell:
    def test_an_unlisted_program_runs(self, layout, audit):
        program = _local_program(layout["project"])

        on = _shell(_Agent(layout["project"], True), program, layout["project"])
        off = _shell(_Agent(layout["project"], False), program, layout["project"])

        assert on["status"] == "success", on
        assert "local tool ran" in on["stdout"]
        assert off["status"] != "success"

    def test_rm_runs(self, layout, audit):
        doomed = layout["project"] / "doomed.txt"
        doomed.write_text("x", encoding="utf-8")

        off = _shell(_Agent(layout["project"], False), _delete("doomed.txt"))
        assert doomed.exists() and off["status"] != "success"

        on = _shell(
            _Agent(layout["project"], True), _delete("doomed.txt"), layout["project"]
        )
        assert on["status"] == "success", on
        assert not doomed.exists()

    def test_a_redirect_and_a_pipe_run(self, layout, audit):
        agent = _Agent(layout["project"], True)

        written = _shell(agent, "echo hello> out.txt", layout["project"])
        piped = _shell(agent, "echo b | sort", layout["project"])

        assert written["status"] == "success", written
        assert "hello" in (layout["project"] / "out.txt").read_text()
        assert piped["status"] == "success", piped
        assert "b" in piped["stdout"]

    def test_a_redirect_is_not_run_unapproved_by_default(self, layout, audit):
        off = _shell(_Agent(layout["project"], False), "echo hello> out.txt")
        assert off["status"] != "success"
        assert not (layout["project"] / "out.txt").exists()

    def test_a_write_into_a_system_folder_runs(self, layout, audit):
        target = layout["system"] / "made.txt"
        line = f'echo made> "{target}"' if _WINDOWS else f"echo made > '{target}'"

        off = _shell(_Agent(layout["project"], False), line)
        assert off["status"] != "success" and not target.exists()

        on = _shell(_Agent(layout["project"], True), line, layout["project"])
        assert on["status"] == "success", on
        assert target.read_text().strip() == "made"

    def test_a_credential_file_can_be_read(self, layout, audit):
        off = _shell(_Agent(layout["project"], False), _read(layout["env"]))
        assert off["status"] != "success"
        assert "hunter2" not in str(off)

        on = _shell(_Agent(layout["project"], True), _read(layout["env"]))
        assert on["status"] == "success", on
        assert "hunter2" in on["stdout"]

    def test_every_line_is_audited_whole(self, layout, audit):
        _shell(_Agent(layout["project"], True), "echo one && echo two")

        assert [r["mode"] for r in audit] == ["bypass"]
        assert audit[0]["command"] == "echo one && echo two"

    def test_a_cd_carries_over_to_the_next_call(self, layout, audit):
        agent = _Agent(layout["project"], True)
        (layout["system"] / "marker.txt").write_text("here", encoding="utf-8")

        moved = _shell(agent, f'cd "{layout["system"]}"')
        listed = _shell(agent, "dir /b" if _WINDOWS else "ls")

        assert moved["status"] == "success", moved
        assert "marker.txt" in listed["stdout"]

    def test_the_timeout_still_applies(self, layout, audit):
        line = f'"{sys.executable}" -c "import time; time.sleep(30)"'

        result = _shell(_Agent(layout["project"], True), line, timeout=2)

        assert result.get("timed_out") is True, result

    def test_a_failing_line_reports_its_exit_code(self, layout, audit):
        line = f'"{sys.executable}" -c "import sys; sys.exit(3)"'

        result = _shell(_Agent(layout["project"], True), line)

        assert result["status"] == "error"
        assert result["return_code"] == 3

    def test_a_skill_trust_command_is_not_refused(self, layout):
        line = "gaia skill trust some-skill"
        on = _Agent(layout["project"], True).policy_refusal_for_call(
            "run_shell_command", {"command": line}
        )
        off = _Agent(layout["project"], False).policy_refusal_for_call(
            "run_shell_command", {"command": line}
        )
        assert on is None
        assert off is not None

    def test_the_agent_child_marker_is_not_set(self, layout, audit):
        line = (
            f'"{sys.executable}" -c '
            "\"import os; print(os.environ.get('GAIA_AGENT_CHILD'))\""
        )
        result = _shell(_Agent(layout["project"], True), line)
        assert result["stdout"].strip() == "None", result


class TestFileTools:
    def test_a_write_into_a_system_folder(self, layout):
        target = layout["system"] / "conf.txt"
        args = {"file_path": str(target), "content": "x"}

        off = _Agent(layout["project"], False)._execute_tool("write_file", args)
        assert off["status"] == "error" and not target.exists()

        on = _Agent(layout["project"], True)._execute_tool("write_file", args)
        assert on["status"] == "success", on
        assert target.read_text(encoding="utf-8") == "x"

    def test_a_credential_file_can_be_read(self, layout):
        args = {"file_path": str(layout["env"])}

        off = _Agent(layout["project"], False)._execute_tool("read_file", args)
        assert off["status"] == "error"
        assert "hunter2" not in str(off)

        on = _Agent(layout["project"], True)._execute_tool("read_file", args)
        assert on["status"] == "success", on
        assert "hunter2" in str(on)

    def test_a_credential_file_can_be_written(self, layout):
        agent = _Agent(layout["project"], True)
        # Overwriting a file unread is refused in every mode, so read it first.
        agent._execute_tool("read_file", {"file_path": str(layout["env"])})
        args = {"file_path": str(layout["env"]), "content": "API_KEY=new\n"}

        on = agent._execute_tool("write_file", args)

        assert on["status"] == "success", on
        assert "new" in layout["env"].read_text(encoding="utf-8")

    def test_a_token_in_a_file_is_not_masked(self, layout):
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        config = layout["project"] / "remote.txt"
        config.write_text(f"url = https://user:{token}@example.com/r.git\n")
        args = {"file_path": str(config)}

        off = _Agent(layout["project"], False)._execute_tool("read_file", args)
        on = _Agent(layout["project"], True)._execute_tool("read_file", args)

        assert token not in str(off)
        assert token in str(on)

    def test_the_toggle_is_read_live(self, layout):
        agent = _Agent(layout["project"], False)
        assert agent.path_validator.validate_read(str(layout["env"]))[0] is False
        agent.console.full_access = True
        assert agent.path_validator.validate_read(str(layout["env"]))[0] is True
        agent.console.full_access = False
        assert agent.path_validator.validate_read(str(layout["env"]))[0] is False


class TestPython:
    def test_python_may_read_a_credential_file(self, layout):
        code = f"print(open({str(layout['env'])!r}).read())"

        on = _Agent(layout["project"], True)._policy_refusal(
            "run_python", {"code": code}
        )
        off = _Agent(layout["project"], False)._policy_refusal(
            "run_python", {"code": code}
        )

        assert on is None
        assert off is not None


class TestMultiLine:
    def test_every_line_runs(self, layout, audit):
        agent = _Agent(layout["project"], True)

        result = _shell(agent, "echo one\necho two> two.txt", layout["project"])

        assert result["status"] == "success", result
        assert "one" in result["stdout"]
        assert (layout["project"] / "two.txt").read_text().strip() == "two"

    def test_crlf_lines_run_and_the_last_exit_code_stands(self, layout, audit):
        line = f'"{sys.executable}" -c "import sys; sys.exit(4)"'
        agent = _Agent(layout["project"], True)

        result = _shell(agent, f"echo a\r\necho b\r\n{line}", layout["project"])

        assert result["return_code"] == 4, result
        assert result["stdout"].split()[:2] == ["a", "b"]

    def test_a_cd_on_an_earlier_line_carries_over(self, layout, audit):
        agent = _Agent(layout["project"], True)
        (layout["system"] / "marker.txt").write_text("here", encoding="utf-8")

        _shell(agent, f'cd "{layout["system"]}"\necho moved')
        listed = _shell(agent, "dir /b" if _WINDOWS else "ls")

        assert "marker.txt" in listed["stdout"]


class TestUndoCopyFailure:
    def test_a_delete_runs_when_its_undo_copy_fails(self, layout, audit, monkeypatch):
        from gaia.agents.base.file_snapshots import SnapshotError

        doomed = layout["project"] / "doomed.txt"
        doomed.write_text("x", encoding="utf-8")

        def fail(self, steps, step_cwds):
            raise SnapshotError("could not copy doomed.txt")

        monkeypatch.setattr(ShellToolsMixin, "_snapshot_shell_deletes", fail)
        agent = _Agent(layout["project"], True)

        result = _shell(agent, _delete("doomed.txt"), layout["project"])

        assert result["status"] == "success", result
        assert not doomed.exists()
        assert "Undo can't restore" in result["note"]


class TestTurningFullAccessOff:
    """Every check comes back the moment full access is turned off."""

    def test_the_shell_folder_full_access_reached_is_judged_again(self, layout, audit):
        agent = _Agent(layout["project"], True)
        moved = _shell(agent, f'cd "{layout["system"]}"')
        assert moved["status"] == "success", moved
        assert os.path.normcase(agent.shell_session.cwd) == os.path.normcase(
            str(layout["system"].resolve())
        )

        agent.console.full_access = False
        agent.console.auto_approve_gated_tools = False
        fresh = _Agent(layout["project"], False)

        for command in ("dir /b" if _WINDOWS else "ls", "echo hi"):
            toggled = agent.policy_refusal_for_call(
                "run_shell_command", {"command": command}
            )
            explicit = fresh.policy_refusal_for_call(
                "run_shell_command",
                {"command": command, "working_directory": str(layout["system"])},
            )
            assert toggled is not None, command
            assert toggled.get("tier") == explicit.get("tier"), (toggled, explicit)
            ran = _shell(agent, command)
            assert ran["status"] != "success", ran

    def test_the_rate_limit_comes_back(self, layout, audit):
        agent = _Agent(layout["project"], True)
        _shell(agent, "echo on", layout["project"])
        assert len(agent.shell_command_times) == 0

        agent.console.full_access = False
        _shell(agent, "echo off", layout["project"])
        assert len(agent.shell_command_times) == 1

    def test_masking_comes_back(self, layout):
        token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        config = layout["project"] / "remote.txt"
        config.write_text(f"url = https://user:{token}@example.com/r.git\n")
        agent = _Agent(layout["project"], True)
        assert token in str(
            agent._execute_tool("read_file", {"file_path": str(config)})
        )

        agent.console.full_access = False
        assert token not in str(
            agent._execute_tool("read_file", {"file_path": str(config)})
        )

    def test_python_refusals_come_back(self, layout):
        code = f"print(open({str(layout['env'])!r}).read())"
        agent = _Agent(layout["project"], True)
        assert agent._policy_refusal("run_python", {"code": code}) is None

        agent.console.full_access = False
        assert agent._policy_refusal("run_python", {"code": code}) is not None

    def test_the_agent_child_marker_and_guarded_folders_come_back(self, layout):
        from gaia_agent.chat_agent import _guarded_folders

        from gaia.agents.tools.shell_tools import _full_access_launch, _segment_env
        from gaia.skills.human_only import AGENT_CHILD_ENV

        agent = _Agent(layout["project"], True)
        assert AGENT_CHILD_ENV not in _full_access_launch("echo", None)[1]["env"]
        assert _guarded_folders(agent._full_access_on()) == ""

        agent.console.full_access = False
        assert agent._full_access_on() is False
        # The ordinary runner marks every child it starts.
        assert _segment_env({})[AGENT_CHILD_ENV] == "1"
        assert _guarded_folders(agent._full_access_on()) != ""

    def test_withheld_folders_come_back(self, layout):
        agent = _Agent(layout["project"], True)
        agent.path_validator.withhold_path(str(layout["system"]))
        assert agent.path_validator.withheld_root(str(layout["system"])) is None

        agent.console.full_access = False
        assert agent.path_validator.withheld_root(str(layout["system"])) is not None
        assert agent.path_validator.is_path_allowed(str(layout["system"])) is False

    def test_a_mock_console_is_not_full_access(self, layout):
        from unittest.mock import MagicMock

        agent = _Agent(layout["project"], False)
        agent.console = MagicMock()
        assert agent._full_access_on() is False
        assert agent.path_validator.validate_read(str(layout["env"]))[0] is False


class TestCodeIndex:
    def test_the_index_reads_outside_its_repo_only_under_full_access(self, tmp_path):
        from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        state = {"on": False}
        sdk = CodeIndexSDK(CodeIndexConfig(repo_path=str(repo)))
        sdk.follow_full_access(lambda: state["on"])

        assert sdk._read_file_safe(str(outside)) is None
        state["on"] = True
        assert "x = 1" in sdk._read_file_safe(str(outside))
