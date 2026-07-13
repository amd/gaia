# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Connectors Demo agent — standalone hub package.

Registers the ``connectors-demo`` agent into the GAIA registry via the
``gaia.agent`` entry-point group. Public names are re-exported lazily so
registry discovery stays cheap.
"""

__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {
    "ConnectorsDemoAgent": "agent",
    "ConnectorsDemoAgentConfig": "agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_connectors_demo.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the connectors-demo agent."""
    import dataclasses

    from gaia_agent_connectors_demo.agent import (
        ConnectorsDemoAgent,
        ConnectorsDemoAgentConfig,
    )

    from gaia.agents.registry import (
        AgentRegistration,
        _wrap_factory_with_namespaced_id,
    )

    def _factory(**kwargs):
        valid_fields = {f.name for f in dataclasses.fields(ConnectorsDemoAgentConfig)}
        config = ConnectorsDemoAgentConfig(
            **{k: v for k, v in kwargs.items() if k in valid_fields}
        )
        return ConnectorsDemoAgent(config=config)

    # Stamp the namespaced id onto the instance so the per-agent connector
    # activation filter (#1005) can match this agent's grants — the registry's
    # create_agent does not inject it for entry-point agents.
    factory = _wrap_factory_with_namespaced_id(_factory, "installed:connectors-demo")

    return AgentRegistration(
        id="connectors-demo",
        name="Connectors Demo",
        description=(
            "Demonstrates the connectors framework — pulls real "
            "data from your connected Google account and GitHub PAT."
        ),
        source="installed",
        conversation_starters=[
            "What's in my inbox?",
            "What's on my calendar today?",
            "List my recent Drive files",
            "List my GitHub repositories",
        ],
        factory=factory,
        agent_dir=None,
        models=[],
        required_connections=list(ConnectorsDemoAgent.REQUIRED_CONNECTORS),
        namespaced_agent_id="installed:connectors-demo",
        category="productivity",
        tags=["google", "gmail", "github", "calendar"],
        icon="plug",
        tools_count=4,
    )
