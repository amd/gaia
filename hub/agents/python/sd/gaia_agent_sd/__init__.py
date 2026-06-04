# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA SD (Stable Diffusion) agent — standalone hub package.

Registers the ``sd`` agent into the GAIA registry via the ``gaia.agent``
entry-point group. The agent module is imported lazily so registry discovery
stays cheap.
"""

# ``SDAgent`` / ``SDAgentConfig`` are re-exported lazily via ``__getattr__`` so
# importing this package at discovery time does not pull in the heavy agent
# module; they are therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {"SDAgent": "agent", "SDAgentConfig": "agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_sd.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the SD agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_sd.agent import SDAgent

        return class_factory(SDAgent)(**kwargs)

    return AgentRegistration(
        id="sd",
        name="Stable Diffusion",
        description="Image generation — LLM-enhanced prompts for Stable Diffusion",
        source="installed",
        conversation_starters=[
            "Generate an image of a mountain sunset",
            "Make a logo for a coffee shop",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:sd",
        category="creative",
        tags=["image", "stable-diffusion", "sd"],
        icon="image",
        tools_count=0,
    )
