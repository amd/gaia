# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Blender agent — standalone hub package.

Installs the ``blender`` agent into the GAIA registry via the ``gaia.agent``
entry-point group (see ``pyproject.toml``). The framework's
``AgentRegistry._discover_installed_agents`` calls :func:`build_registration`
at discovery time; the agent module itself is imported lazily inside the
factory so discovery stays cheap.
"""

# ``BlenderAgent`` is re-exported lazily via ``__getattr__`` (below) so that
# importing this package at registry-discovery time does not pull in the heavy
# agent module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_blender`` (e.g. at registry
    # discovery) does not pull in the heavy agent module + its SDK deps.
    if name == "BlenderAgent":
        from gaia_agent_blender.agent import BlenderAgent

        return BlenderAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the blender agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_blender.agent import BlenderAgent

        return class_factory(BlenderAgent)(**kwargs)

    return AgentRegistration(
        id="blender",
        name="Blender",
        description="3D scene automation for Blender via MCP",
        source="installed",
        conversation_starters=[
            "Create a red cube",
            "Render the current scene",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:blender",
        category="creative",
        tags=["blender", "3d", "mcp"],
        icon="box",
        tools_count=0,
    )
