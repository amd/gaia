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
        # unchanged regardless of which frontend invokes pip.
        assert (
            "gaia-agent-chat @ git+https://github.com/amd/gaia.git"
            "#subdirectory=hub/agents/chat/python" in cmd
        )

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
