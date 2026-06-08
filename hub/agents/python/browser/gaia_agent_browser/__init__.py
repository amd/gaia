# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Browser agent — standalone hub package.

Registers the ``web`` agent (web research) into the GAIA registry via the
``gaia.agent`` entry-point group. Public names are re-exported lazily so
registry discovery stays cheap.
"""

__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {
    "BrowserAgent": "agent",
    "BrowserAgentConfig": "agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_browser.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the ``web`` (browser) agent."""
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

        from gaia_agent_browser.agent import BrowserAgent, BrowserAgentConfig

        valid_fields = {f.name for f in dataclasses.fields(BrowserAgentConfig)}
        config = BrowserAgentConfig(
            **{k: v for k, v in kwargs.items() if k in valid_fields}
        )
        return BrowserAgent(config=config)

    factory = _wrap_factory_with_namespaced_id(_factory, "installed:web")

    return AgentRegistration(
        id="web",
        name="Browser Agent",
        description="Web research — search, fetch pages, and download files",
        source="installed",
        conversation_starters=[
            "Search the web for...",
            "What's the latest on...",
            "Fetch this URL for me",
        ],
        factory=factory,
        agent_dir=None,
        models=[],
        required_connections=[],
        namespaced_agent_id="installed:web",
        category="research",
        tags=["web", "search", "browser", "download"],
        icon="globe",
        tools_count=10,
        model_tiers=tiers,
    )
