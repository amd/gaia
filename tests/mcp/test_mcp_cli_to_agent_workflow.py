# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end MCP workflow: ``gaia connectors configure`` → config → agent tools.

This is the flow a user follows today (the connectors framework replaced
``gaia mcp add`` in #977):

1. ``gaia connectors configure <id> --set KEY=VALUE`` stores the secret in the
   keyring and writes ``~/.gaia/mcp_servers.json`` with a ``$keyring`` reference.
2. An MCP-enabled agent loads that file, spawns the server with the secret
   resolved into its environment, and registers its tools.
3. ``gaia connectors disconnect <id>`` removes the server for the next agent.

The server is the local fixture in ``tests/mcp/fixtures/`` — nothing is
downloaded, so this runs offline.
"""

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.mcp import MCPClientMixin

CONNECTOR_ID = "mcp-gaia-fixture"
TOOL_PREFIX = "mcp_gaia_fixture_"
SECRET_KEY = "GAIA_FIXTURE_TOKEN"
SECRET_VALUE = "fixture-secret-123"


class MCPTestAgent(Agent, MCPClientMixin):
    """Lemonade-free agent that loads MCP servers from the user's config."""

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        Agent.__init__(self, **kwargs)
        MCPClientMixin.__init__(self, auto_load_config=True)

    def _get_system_prompt(self) -> str:
        return "You are a test agent with access to MCP servers."

    def _register_tools(self) -> None:
        pass


def _connectors(*argv) -> tuple[int, str, str]:
    """Run ``gaia connectors <argv>`` in-process so it shares the test keyring."""
    from gaia.connectors import cli as connectors_cli

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = connectors_cli.main(["connectors", *argv])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def fixture_connector(
    isolated_home, fixture_server_config, in_memory_keyring, monkeypatch
):
    """Publish a catalog entry that launches the local fixture server."""
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.spec import ConfigField, ConnectorSpec

    spec = ConnectorSpec(
        id=CONNECTOR_ID,
        display_name="GAIA fixture",
        icon="T",
        category="dev-tools",
        tier=9,
        type="mcp_server",
        description="Local stdio MCP server used by the tests.",
        mcp_command=fixture_server_config["command"],
        mcp_args=tuple(fixture_server_config["args"]),
        mcp_env_keys=(SECRET_KEY,),
        config_schema=(
            ConfigField(key=SECRET_KEY, label="Token", kind="secret", secret=True),
        ),
    )
    monkeypatch.setitem(REGISTRY._specs, CONNECTOR_ID, spec)
    return spec


@pytest.fixture
def clean_tool_registry():
    """MCP tools land in the process-wide registry; drop them after each test."""
    before = set(_TOOL_REGISTRY)
    yield
    for name in set(_TOOL_REGISTRY) - before:
        del _TOOL_REGISTRY[name]


@pytest.fixture
def make_agent(clean_tool_registry):
    agents = []

    def _make():
        agent = MCPTestAgent()
        agents.append(agent)
        return agent

    yield _make
    for agent in agents:
        agent._mcp_manager.disconnect_all()


def _configure():
    rc, out, err = _connectors(
        "configure", CONNECTOR_ID, f"--set={SECRET_KEY}={SECRET_VALUE}"
    )
    assert rc == 0, f"configure failed: stdout={out!r} stderr={err!r}"


class TestConnectorsToAgentWorkflow:
    def test_configure_writes_keyring_reference_not_secret(
        self, fixture_connector, isolated_home
    ):
        _configure()

        config_path = isolated_home / ".gaia" / "mcp_servers.json"
        raw = config_path.read_text(encoding="utf-8")
        assert SECRET_VALUE not in raw

        entry = json.loads(raw)["mcpServers"][CONNECTOR_ID]
        assert entry["command"] == fixture_connector.mcp_command
        assert entry["args"] == list(fixture_connector.mcp_args)
        assert entry["env"][SECRET_KEY] == {
            "$keyring": f"gaia.connections:{CONNECTOR_ID}:{SECRET_KEY}"
        }

    def test_agent_loads_configured_server_and_calls_its_tools(
        self, fixture_connector, make_agent
    ):
        _configure()

        agent = make_agent()

        assert agent.list_mcp_servers() == [CONNECTOR_ID]
        registered = {n for n in _TOOL_REGISTRY if n.startswith(TOOL_PREFIX)}
        assert registered == {f"{TOOL_PREFIX}{t}" for t in ("echo", "add", "read_env")}
        for name in registered:
            assert name in agent.system_prompt

        echo = _TOOL_REGISTRY[f"{TOOL_PREFIX}echo"]["function"](text="hello")
        assert echo["status"] == "success"
        assert echo["data"]["content"][0]["text"] == "hello"

        # The keyring secret must reach the spawned server's environment.
        env = _TOOL_REGISTRY[f"{TOOL_PREFIX}read_env"]["function"](name=SECRET_KEY)
        assert env["status"] == "success"
        assert env["data"]["content"][0]["text"] == SECRET_VALUE

    def test_disconnect_removes_server_for_the_next_agent(
        self, fixture_connector, make_agent, isolated_home, in_memory_keyring
    ):
        _configure()
        assert make_agent().list_mcp_servers() == [CONNECTOR_ID]

        rc, out, err = _connectors("disconnect", CONNECTOR_ID)
        assert rc == 0, f"disconnect failed: stdout={out!r} stderr={err!r}"

        servers = json.loads(
            (isolated_home / ".gaia" / "mcp_servers.json").read_text(encoding="utf-8")
        )["mcpServers"]
        assert CONNECTOR_ID not in servers
        assert (
            in_memory_keyring.get_password(
                "gaia.connections", f"{CONNECTOR_ID}:{SECRET_KEY}"
            )
            is None
        )
        assert make_agent().list_mcp_servers() == []
