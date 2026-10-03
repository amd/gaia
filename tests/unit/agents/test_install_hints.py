# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards for gaia.agents.install_hints (#2240).

Every "agent X is not installed" message in the codebase used to recommend
`pip install gaia-agent-<id>` and `pip install "amd-gaia[agents]"` -- both
fail on a clean environment because the gaia-agent-* wheels aren't published
to PyPI yet. These tests pin the replacement contract: the generated message
must never recommend either broken command, and the source-install command it
does recommend must reference a directory that actually exists on disk.
"""

from pathlib import Path

import pytest

from gaia.agents.install_hints import (
    _AGENT_SOURCE_SUBDIRS,
    agent_import_error_message,
    agent_not_installed_message,
    source_install_command,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "hub" / "agents"


class TestSourceInstallCommand:
    def test_known_wheel_does_not_require_a_bare_uv_executable(self):
        """#2358: a stock `python -m venv` has neither a `uv` binary on PATH
        nor the `uv` Python module. Hard-coding `uv pip install` in the hint
        recreates the exact dead end #2240 was supposed to fix -- it just
        moves the broken command from `pip install gaia-agent-chat` to
        `uv pip install "... @ git+..."`. The command must be runnable with
        nothing more than the active interpreter's own pip (`python -m pip`),
        the same last-resort frontend `InitCommand._install_pip_extras`
        already falls back to for exactly this reason.
        """
        cmd = source_install_command("gaia-agent-chat")
        assert not cmd.startswith("uv "), (
            f"command requires a bare `uv` executable on PATH, which a stock "
            f"`python -m venv` does not have: {cmd!r}"
        )
        assert "-m pip install" in cmd, (
            f"command must be runnable via `python -m pip`, which every "
            f"stock venv provides even with no `uv` on PATH: {cmd!r}"
        )
        # The core operation (which wheel, from which subdirectory) must be
        # unchanged regardless of which frontend invokes pip. The git ref is
        # pinned to the installed core so a reinstall can't recreate the
        # version skew that produced the misdiagnosed "not installed"
        # ImportErrors -- the subdirectory must directly follow the tag.
        assert f"git+https://github.com/amd/gaia.git@v" in cmd
        assert "#subdirectory=hub/agents/chat/python" in cmd
        assert "gaia-agent-chat @" in cmd

    def test_unknown_wheel_raises(self):
        with pytest.raises(KeyError):
            source_install_command("gaia-agent-does-not-exist")

    @pytest.mark.parametrize("wheel", sorted(_AGENT_SOURCE_SUBDIRS))
    def test_every_registered_subdir_exists_on_disk(self, wheel):
        """Guards against the map drifting from hub/agents/<id>/python (#2240)."""
        subdir = _AGENT_SOURCE_SUBDIRS[wheel]
        assert (AGENTS_DIR / subdir / "python").is_dir(), (
            f"{wheel} maps to hub/agents/{subdir}/python, which doesn't "
            "exist -- update _AGENT_SOURCE_SUBDIRS in install_hints.py."
        )


class TestAgentNotInstalledMessage:
    def test_never_recommends_the_broken_pip_commands(self):
        """Regression guard: neither broken install path appears (#2240)."""
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "pip install gaia-agent-chat`" not in message
        assert "amd-gaia[agents]" not in message

    def test_includes_working_source_install_command(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert source_install_command("gaia-agent-chat") in message

    def test_references_tracking_issue(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "github.com/amd/gaia/issues/2240" in message

    def test_next_step_is_appended(self):
        message = agent_not_installed_message(
            "The chat agent is not installed",
            "gaia-agent-chat",
            next_step="Then re-run `gaia chat`.",
        )
        assert message.endswith("Then re-run `gaia chat`.")

    def test_no_next_step_has_no_trailing_space(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert not message.endswith(" ")


class TestAgentImportErrorMessage:
    def test_missing_top_level_module_name_reports_not_installed(self):
        # No CI environment can produce this for real -- gaia-agent-chat is
        # installed for the wheel's own tests. CPython assigns ImportError.name
        # exactly this way at raise time, so the constructed object is the
        # identical shape the real import machinery produces.
        error = ModuleNotFoundError("No module named 'gaia_agent_chat'")
        error.name = "gaia_agent_chat"
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is not installed" in message

    def test_version_skew_reports_failed_import_not_missing(self):
        """An ImportError raised *inside* the installed wheel must not be
        declared "not installed" (the old misdiagnosis) -- report the real
        error and both package versions. Produced via a real failed import of
        a symbol the core doesn't export, the exact shape of the 0.24.1 +
        gaia-agent-chat@main failure."""
        try:
            from gaia.config import _symbol_not_exported_by_core  # noqa: F401

            pytest.fail("import of an unexported symbol should fail")
        except ImportError as exc:
            error = exc
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is not installed" not in message
        assert "is installed, but it could not be imported" in message
        assert "_symbol_not_exported_by_core" in message
        assert "Detected versions:" in message
        assert "amd-gaia" in message
        assert source_install_command("gaia-agent-chat") in message

    def test_missing_transitive_dependency_reports_failed_import(self):
        # Real ModuleNotFoundError from importing a module that will never
        # exist -- a wheel missing a transitive dep raises exactly this.
        try:
            import _gaia_missing_transitive_dep  # noqa: F401

            pytest.fail("import of a missing module should fail")
        except ModuleNotFoundError as exc:
            error = exc
        assert error.name == "_gaia_missing_transitive_dep"
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is installed, but it could not be imported" in message
        assert "_gaia_missing_transitive_dep" in message
