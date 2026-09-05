# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""run_shell_command shares one session, so state survives between calls (#3380).

Driven through the registered tools rather than the session object, because the
regression these guard is at the seam: a tool that builds a fresh subprocess per
call is indistinguishable from one that reuses a session until you ask it where
it is.
"""

import os

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


@pytest.fixture
def tools():
    """The registered shell tools, keyed by name, on one fresh host."""
    captured = {}
    import gaia.agents.base.tools as tools_module

    original = tools_module.tool

    def spy(**kwargs):
        def decorate(fn):
            captured[fn.__name__] = fn
            return original(**kwargs)(fn)

        return decorate

    tools_module.tool = spy
    try:
        host = _Host()
        # No rate limit: these tests run more than three commands in ten seconds.
        host.max_commands_per_10_seconds = 1000
        host.max_commands_per_minute = 1000
        host.register_shell_tools()
    finally:
        tools_module.tool = original
    captured["_host"] = host
    yield captured
    host.close_shell_session()


def test_cd_in_one_call_is_where_the_next_call_runs(tools, tmp_path):
    host = tools["_host"]
    (tmp_path / "workspace").mkdir()
    host.shell_session.set_cwd(str(tmp_path))

    tools["run_shell_command"](command="cd workspace")
    result = tools["run_shell_command"](command="pwd")

    assert result["status"] == "success", result
    assert result["session_cwd"].replace("\\", "/").endswith("/workspace")


def test_the_probe_reports_the_session_directory(tools, tmp_path):
    host = tools["_host"]
    host.shell_session.set_cwd(str(tmp_path))

    state = tools["get_shell_state"]()

    assert state["status"] == "success"
    assert state["cwd"] == str(tmp_path.resolve())
    assert state["environment"] == {}


def test_a_variable_set_through_the_tool_reaches_later_commands(tools):
    assert tools["set_shell_variable"](name="GAIA_TOOL_VAR", value="kept")[
        "status"
    ] == ("success")

    state = tools["get_shell_state"]()
    assert state["environment"]["GAIA_TOOL_VAR"] == "kept"

    echo = "echo %GAIA_TOOL_VAR%" if os.name == "nt" else "echo $GAIA_TOOL_VAR"
    result = tools["run_shell_command"](command=echo)
    assert "kept" in result["stdout"]


@pytest.mark.parametrize(
    "name", ["PATH", "PYTHONPATH", "LD_PRELOAD", "dyld_insert_libraries"]
)
def test_variables_that_choose_the_binary_are_refused(tools, name):
    """Setting PATH would let the agent pick which `ls` the whitelist approved."""
    result = tools["set_shell_variable"](name=name, value="/tmp/evil")

    assert result["status"] == "error"
    assert tools["get_shell_state"]()["environment"] == {}


def test_an_invalid_variable_name_is_refused(tools):
    result = tools["set_shell_variable"](name="not a name", value="x")

    assert result["status"] == "error"


def test_reset_returns_the_session_to_where_it_started(tools, tmp_path):
    host = tools["_host"]
    (tmp_path / "sub").mkdir()
    start = host.shell_session.cwd
    tools["set_shell_variable"](name="GAIA_TOOL_RESET", value="x")
    host.shell_session.set_cwd(str(tmp_path / "sub"))

    result = tools["reset_shell_session"]()

    assert result["status"] == "success"
    assert result["cwd"] == start
    assert tools["get_shell_state"]()["environment"] == {}


def test_working_directory_still_scopes_a_single_call(tools, tmp_path):
    """The existing argument keeps its meaning: this call only."""
    host = tools["_host"]
    before = host.shell_session.cwd

    result = tools["run_shell_command"](command="pwd", working_directory=str(tmp_path))

    assert result["status"] == "success", result
    assert host.shell_session.cwd == before


def test_a_forbidden_directory_change_is_not_absorbed(tools, tmp_path):
    """`cd` must not become a way around the path policy.

    Without this, the agent could step into a directory it may not read and then
    open a file by bare name — the per-argument check only sees paths.
    """
    host = tools["_host"]
    (tmp_path / "secrets").mkdir()
    host.shell_session.set_cwd(str(tmp_path))
    host._is_path_allowed = lambda path: "secrets" not in path.replace("\\", "/")
    host._shell_session = None  # rebuild the session with the guard attached

    host.shell_session.set_cwd(str(tmp_path))
    result = tools["run_shell_command"](command="cd secrets")

    assert host.shell_session.cwd == str(tmp_path.resolve())
    assert "warning" in result


def test_the_guardrails_still_refuse_a_blocked_command(tools):
    """Persistence is about state, not policy (#3380 acceptance criterion 6)."""
    result = tools["run_shell_command"](command="rm -rf /")

    assert result["status"] == "error"
    assert result["has_errors"] is True


def test_teardown_is_repeatable(tools):
    host = tools["_host"]
    tools["run_shell_command"](command="echo hello")

    host.close_shell_session()
    host.close_shell_session()

    # A closed session is replaced on next use rather than left broken.
    assert tools["run_shell_command"](command="echo again")["status"] == "success"
