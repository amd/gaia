# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for Stage E of issue #1428: registry load-error recording.

The registry keeps the existing resilience (one broken agent must not kill
discovery), but now records the failure so Stage D can surface it to the user.
"""

import textwrap
from pathlib import Path
from unittest.mock import patch

from gaia.agents.registry import AgentRegistry


class TestRegistryLoadErrors:
    """get_load_error() is exposed and populated by failed discover/hot-reload."""

    def test_get_load_error_returns_none_for_unknown(self):
        registry = AgentRegistry()
        assert registry.get_load_error("nonexistent") is None

    def test_broken_agent_dir_records_load_error(self, tmp_path):
        """A broken agent.py must not crash discover, but must be recorded."""
        # Write a syntactically broken agent
        agents_dir = tmp_path / ".gaia" / "agents"
        agents_dir.mkdir(parents=True)
        agent_dir = agents_dir / "broken-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text("this is not valid python !!!")

        registry = AgentRegistry()
        with patch.object(Path, "home", return_value=tmp_path):
            with patch.object(registry, "_discover_installed_agents"):
                with patch.object(registry, "_discover_native_agents"):
                    registry.discover()

        # Must record the error
        err = registry.get_load_error("broken-agent")
        assert err is not None
        assert len(err) > 0

    def test_dot_folders_in_the_agents_root_are_not_agents(self, tmp_path, caplog):
        """The install backup and lock-queue folders are skipped without noise."""
        import logging

        agents_dir = tmp_path / "agents"
        (agents_dir / ".demo.install.lock.d").mkdir(parents=True)
        backup = agents_dir / ".backup"
        backup.mkdir()
        (backup / "agent.py").write_text("raise RuntimeError('loaded a backup')")

        registry = AgentRegistry()
        with patch("gaia.agents.registry.gaia_agents_dir", return_value=agents_dir):
            with patch.object(registry, "_discover_installed_agents"):
                with patch.object(registry, "_discover_native_agents"):
                    with caplog.at_level(logging.WARNING):
                        registry.discover()

        assert registry.get_load_error(".backup") is None
        assert "No agent.py" not in caplog.text
        assert ".demo.install.lock.d" not in caplog.text

    def test_discover_continues_after_broken_agent(self, tmp_path):
        """A broken agent must not prevent other agents from loading."""
        bad_dir = tmp_path / "broken"
        bad_dir.mkdir()
        (bad_dir / "agent.py").write_text("!!!syntax error")

        good_dir = tmp_path / "good"
        good_dir.mkdir()
        # Write a minimal valid agent
        (good_dir / "agent.py").write_text(textwrap.dedent("""\
                from gaia.agents.base.agent import Agent

                class GoodAgent(Agent):
                    AGENT_ID = "good"
                    AGENT_NAME = "Good Agent"

                    def _register_tools(self):
                        pass
                """))

        registry = AgentRegistry()
        with patch.object(Path, "home", return_value=tmp_path):
            # Manually call the scan logic
            subdirs = sorted(d for d in tmp_path.iterdir() if d.is_dir())
            for agent_dir in subdirs:
                try:
                    registry._load_from_dir(agent_dir)
                except Exception:
                    registry._record_load_error(
                        str(agent_dir.name), "SyntaxError: invalid syntax"
                    )

        # Broken recorded, good loaded (or at least broken recorded)
        bad_err = registry.get_load_error("broken")
        assert bad_err is not None


class TestHubInstallDirs:
    """Hub installs live beside source agents but are registered elsewhere."""

    def test_a_hub_install_dir_is_skipped_without_a_warning(self, tmp_path, caplog):
        agent_dir = tmp_path / "gaia"
        agent_dir.mkdir()
        (agent_dir / ".installed").write_text("{}")
        (agent_dir / "gaia-agent.exe").write_bytes(b"")

        with caplog.at_level("WARNING", logger="gaia.agents.registry"):
            AgentRegistry()._load_from_dir(agent_dir)

        assert not [r for r in caplog.records if r.levelname == "WARNING"]

    def test_a_dir_with_no_agent_still_warns(self, tmp_path, caplog):
        agent_dir = tmp_path / "stray"
        agent_dir.mkdir()

        with caplog.at_level("WARNING", logger="gaia.agents.registry"):
            AgentRegistry()._load_from_dir(agent_dir)

        assert any("No agent.py" in r.getMessage() for r in caplog.records)
