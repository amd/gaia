# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the SDK's one reference agent package (tests/fixtures/agents/hello-world).

The template must stay a working agent and must keep the layout
``gaia agent init`` scaffolds. These tests mock the LLM and never contact a
live Lemonade server.
"""

from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia import cli_agent
from gaia.hub import manifest as hub_manifest

TEMPLATE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "agents" / "hello-world"
)
PACKAGE = "gaia_agent_hello_world"


@pytest.fixture
def template(monkeypatch):
    """Import the template package from its fixture directory."""
    monkeypatch.syspath_prepend(str(TEMPLATE_DIR))
    import gaia_agent_hello_world

    return gaia_agent_hello_world


_BUILD_OUTPUT = {"__pycache__", "build", "dist", "tests"}


def _package_files(root: Path):
    """Relative paths of an agent package's source files (tests and build output excluded)."""
    files = []
    for p in root.rglob("*"):
        parts = p.relative_to(root).parts
        if not p.is_file() or _BUILD_OUTPUT.intersection(parts):
            continue
        if any(part.endswith(".egg-info") for part in parts):
            continue
        files.append(p.relative_to(root).as_posix())
    return sorted(files)


def test_build_registration_metadata(template):
    """The entry-point registration advertises the agent to the registry."""
    reg = template.build_registration()

    assert reg.id == "hello-world"
    assert reg.name == "Hello World"
    assert reg.category == "examples"
    assert reg.tools_count == 0
    assert reg.namespaced_agent_id == "installed:hello-world"
    assert reg.models == ["Gemma-4-E4B-it-GGUF"]
    assert reg.conversation_starters


def test_factory_creates_agent_instance(template):
    """The registration factory builds a real HelloWorldAgent."""
    from gaia_agent_hello_world.agent import HelloWorldAgent

    reg = template.build_registration()
    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = reg.factory(skip_lemonade=True)

    assert isinstance(agent, HelloWorldAgent)


def test_system_prompt_and_no_tools(template):
    """A no-tool conversational agent exposes its prompt and registers nothing."""
    from gaia_agent_hello_world.agent import HelloWorldAgent

    from gaia.agents.base.tools import _TOOL_REGISTRY

    # The registry is process-global; clear it so the result doesn't depend on
    # which agents other tests imported first.
    _TOOL_REGISTRY.clear()
    with patch("gaia.agents.base.agent.AgentSDK", return_value=MagicMock()):
        agent = HelloWorldAgent(skip_lemonade=True)

    assert "Hello World agent" in agent.system_prompt
    assert agent.response_mode == "conversational"
    assert agent._tools_registry == {}


def test_lazy_class_export(template):
    """The class is importable via the package's lazy ``__getattr__``."""
    from gaia_agent_hello_world.agent import HelloWorldAgent

    assert template.HelloWorldAgent is HelloWorldAgent


def test_template_passes_the_publish_lint_gates():
    """The template clears the same ``gaia agent test --lint`` gates a published agent must."""
    cli_agent.cmd_test(
        Namespace(
            agent_action="test",
            path=str(TEMPLATE_DIR),
            lint=True,
            live=False,
            timeout=60,
        )
    )


def test_gaia_agent_init_scaffolds_the_template_layout(tmp_path):
    """``gaia agent init hello-world`` produces the same package shape as the template."""
    cli_agent.cmd_init(
        Namespace(
            agent_action="init",
            name="hello-world",
            language="python",
            output=str(tmp_path),
            force=False,
            layout="flat",
        )
    )
    scaffold = tmp_path / "hello-world"

    assert _package_files(scaffold) == _package_files(TEMPLATE_DIR)

    scaffolded = hub_manifest.parse(scaffold)
    kept = hub_manifest.parse(TEMPLATE_DIR)
    assert scaffolded.id == kept.id
    assert scaffolded.python.entry_module == kept.python.entry_module == PACKAGE
    assert scaffolded.python.entry_class == kept.python.entry_class

    entry_point = f'hello-world = "{PACKAGE}:build_registration"'
    assert entry_point in (scaffold / "pyproject.toml").read_text(encoding="utf-8")
    assert entry_point in (TEMPLATE_DIR / "pyproject.toml").read_text(encoding="utf-8")
