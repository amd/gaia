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
PYTHON_AGENTS_DIR = REPO_ROOT / "hub" / "agents" / "python"


class TestSourceInstallCommand:
    def test_known_wheel_produces_git_subdirectory_install(self):
        cmd = source_install_command("gaia-agent-chat")
        assert cmd == (
            'uv pip install "gaia-agent-chat @ '
            "git+https://github.com/amd/gaia.git#subdirectory="
            'hub/agents/python/chat"'
        )

    def test_unknown_wheel_raises(self):
        with pytest.raises(KeyError):
            source_install_command("gaia-agent-does-not-exist")

    @pytest.mark.parametrize("wheel", sorted(_AGENT_SOURCE_SUBDIRS))
    def test_every_registered_subdir_exists_on_disk(self, wheel):
        """Guards against the map drifting from hub/agents/python/ (#2240)."""
        subdir = _AGENT_SOURCE_SUBDIRS[wheel]
        assert (PYTHON_AGENTS_DIR / subdir).is_dir(), (
            f"{wheel} maps to hub/agents/python/{subdir}, which doesn't "
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
