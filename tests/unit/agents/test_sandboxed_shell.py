# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A disposable sandbox may declare itself, and then the shell gets out of the way.

The command allowlist tries to be the whole containment story and cannot be:
measured against 28,064 real agent shell commands, 94% use an operator the
tool refuses. In a container or a throwaway benchmark workspace that filter
adds nothing the sandbox does not already provide, and it costs the agent most
of its expressiveness.

Two properties matter and both are tested here:

* **off by default** — an agent on a developer's machine is unaffected;
* **paths are still contained** — the switch lifts the *command* filter only.
  Escaping the workspace must fail in both modes.
"""

import os
import tempfile

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.shell_tools import (
    SANDBOX_ENV_VAR,
    ShellToolsMixin,
    sandboxed_shell_enabled,
)
from gaia.security import PathValidator


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("line\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def run(workspace):
    class _Host(ShellToolsMixin):
        def __init__(self):
            self.path_validator = PathValidator([str(workspace)])

    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _Host().register_shell_tools()
    yield _TOOL_REGISTRY["run_shell_command"]["function"]
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _refused(result):
    return result.get("status") == "error"


#: Shapes the corpus says real agent work is made of, all refused by default.
REAL_WORK = [
    "echo hi && echo there",
    "sed -n 1p f.txt",
    "awk '{print}' f.txt",
    "mkdir -p sub",
    "echo one > out.txt",
]


class TestOffByDefault:
    def test_the_switch_is_off_when_unset(self, monkeypatch):
        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        assert not sandboxed_shell_enabled()

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
    def test_only_a_truthy_value_enables_it(self, monkeypatch, value):
        monkeypatch.setenv(SANDBOX_ENV_VAR, value)
        assert not sandboxed_shell_enabled()

    @pytest.mark.parametrize("command", REAL_WORK)
    def test_the_filter_still_applies_by_default(self, run, monkeypatch, command):
        monkeypatch.delenv(SANDBOX_ENV_VAR, raising=False)
        assert _refused(run(command=command, timeout=20))


class TestSandboxDeclared:
    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_truthy_values_enable_it(self, monkeypatch, value):
        monkeypatch.setenv(SANDBOX_ENV_VAR, value)
        assert sandboxed_shell_enabled()

    @pytest.mark.parametrize("command", REAL_WORK)
    def test_real_work_runs(self, run, monkeypatch, command):
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        assert not _refused(run(command=command, timeout=20))

    def test_a_chain_actually_executes_both_halves(self, run, monkeypatch):
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        out = run(command="echo first && echo second", timeout=20)
        assert "first" in out.get("stdout", "")
        assert "second" in out.get("stdout", "")

    def test_a_redirect_actually_writes(self, run, monkeypatch, workspace):
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        run(command="echo written > out.txt", timeout=20)
        assert (workspace / "out.txt").read_text(encoding="utf-8").strip() == "written"

    def test_an_unparseable_command_is_still_refused(self, run, monkeypatch):
        # Lifting the filter is not the same as accepting anything at all.
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        assert _refused(run(command='echo "unterminated', timeout=20))


class TestContainmentIsNotRelaxed:
    """The switch lifts the command filter. It does not widen the workspace."""

    OUTSIDE = (
        "C:/Windows/System32/drivers/etc/hosts" if os.name == "nt" else "/etc/passwd"
    )

    @pytest.mark.parametrize("enabled", ["0", "1"])
    def test_reading_outside_the_workspace_is_blocked_in_both_modes(
        self, run, monkeypatch, enabled
    ):
        monkeypatch.setenv(SANDBOX_ENV_VAR, enabled)
        assert _refused(run(command=f"cat {self.OUTSIDE}", timeout=20))

    def test_writing_outside_the_workspace_is_blocked_when_sandboxed(
        self, run, monkeypatch
    ):
        monkeypatch.setenv(SANDBOX_ENV_VAR, "1")
        # A fresh directory per run: a fixed name in the shared temp dir
        # survives previous runs, so "the file exists" would report an escape
        # that already happened rather than the one under test.
        target = os.path.join(tempfile.mkdtemp(), "escape_probe.txt")
        assert _refused(run(command=f"echo x > {target}", timeout=20))
        assert not os.path.exists(target), "escaped the workspace"

    @pytest.mark.parametrize("enabled", ["0", "1"])
    def test_a_native_separator_path_cannot_evade_the_check(
        self, run, monkeypatch, enabled
    ):
        """The operand must be validated as the shell will see it.

        Argument scanning reads tokens from ``shlex.split``, which runs in
        POSIX mode and treats a backslash as an escape — so a Windows path
        tokenises with its separators removed and stops looking like a path,
        while the untokenised string still reaches the shell and reads the
        file. Both modes must refuse it.
        """
        monkeypatch.setenv(SANDBOX_ENV_VAR, enabled)
        outside = tempfile.mkdtemp()
        canary = os.path.join(outside, "canary.txt")
        with open(canary, "w", encoding="utf-8") as handle:
            handle.write("CANARY\n")
        result = run(command=f"cat {canary}", timeout=20)
        assert "CANARY" not in (
            result.get("stdout") or ""
        ), "read outside the workspace"
