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

from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from gaia.agents import install_hints
from gaia.agents.install_hints import (
    _AGENT_SOURCE_SUBDIRS,
    agent_not_installed_message,
    source_install_command,
)
from gaia.version import __version__ as CORE_VERSION

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
            f"@v{CORE_VERSION}#subdirectory=hub/agents/chat/python" in cmd
        )

    def test_url_is_pinned_to_the_core_release_tag(self):
        """#4586: an unpinned URL pulls `main`, whose agent can need core
        symbols the installed release lacks -- following the hint recreated
        the very import failure it was printed for."""
        cmd = source_install_command("gaia-agent-chat")
        assert f"gaia.git@v{CORE_VERSION}#" in cmd
        assert "gaia.git#" not in cmd

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


@pytest.fixture
def wheel_absent(monkeypatch):
    def _missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(install_hints, "_dist_version", _missing)


@pytest.fixture
def wheel_installed(monkeypatch):
    monkeypatch.setattr(install_hints, "_dist_version", lambda name: "0.1.0")


@pytest.mark.usefixtures("wheel_absent")
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


@pytest.mark.usefixtures("wheel_installed")
class TestInstalledButUnimportable:
    """#4586: an installed wheel that fails to import (version skew with the
    core) must not be reported as "not installed"."""

    def _message(self, **kwargs):
        return agent_not_installed_message(
            "The chat agent is not installed",
            "gaia-agent-chat",
            error=ImportError("cannot import name 'gaia_home' from 'gaia.config'"),
            **kwargs,
        )

    def test_does_not_claim_the_wheel_is_missing(self):
        assert "is not installed" not in self._message()

    def test_reports_the_real_import_error(self):
        assert "cannot import name 'gaia_home'" in self._message()

    def test_reports_both_versions(self):
        message = self._message()
        assert "`gaia-agent-chat` 0.1.0" in message
        assert f"`amd-gaia` {CORE_VERSION}" in message

    def test_recommends_a_pinned_force_reinstall(self):
        message = self._message()
        assert source_install_command("gaia-agent-chat", force_reinstall=True) in (
            message
        )
        assert "--force-reinstall" in message
        assert f"@v{CORE_VERSION}#" in message

    def test_next_step_is_appended(self):
        message = self._message(next_step="Then re-run `gaia chat`.")
        assert message.endswith("Then re-run `gaia chat`.")
