# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Analyst agent — standalone hub package.

Registers the ``data`` agent (structured-data analysis) into the GAIA registry
via the ``gaia.agent`` entry-point group. Public names are re-exported lazily
so registry discovery stays cheap.
"""

__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {
    "AnalystAgent": "agent",
    "AnalystAgentConfig": "agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_analyst.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the ``data`` (analyst) agent."""
    import dataclasses

    from gaia.agents.registry import (
        AgentRegistration,
        _select_tier_model,
        _wrap_factory_with_namespaced_id,
        build_model_tiers,
    )

    tiers = build_model_tiers("Full (~35B)")

    def _factory(**kwargs):
        tier = kwargs.pop("model_tier", None)
        if tier:
            preset = _select_tier_model(tiers, tier)
            if preset:
                kwargs.setdefault("model_id", preset)

        from gaia_agent_analyst.agent import AnalystAgent, AnalystAgentConfig

        valid_fields = {f.name for f in dataclasses.fields(AnalystAgentConfig)}
        config = AnalystAgentConfig(
            **{k: v for k, v in kwargs.items() if k in valid_fields}
        )
        return AnalystAgent(config=config)

    factory = _wrap_factory_with_namespaced_id(_factory, "installed:data")

    return AgentRegistration(
        id="data",
        name="Analyst Agent",
        description="Data analysis — CSV, Excel, structured queries and tables",
        source="installed",
        conversation_starters=[
            "Analyze my spending data",
            "What are the trends in this CSV?",
            "Who is the top performer?",
        ],
        factory=factory,
        agent_dir=None,
        models=[],
        required_connections=[],
        namespaced_agent_id="installed:data",
        category="productivity",
        tags=["data", "csv", "excel", "analysis"],
        icon="table",
        tools_count=10,
        model_tiers=tiers,
    )
