# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Docker agent — standalone hub package.

Registers the ``docker`` agent into the GAIA registry via the ``gaia.agent``
entry-point group. The agent module is imported lazily so registry discovery
stays cheap.
"""

# ``DockerAgent`` / ``DEFAULT_MODEL`` are re-exported lazily via ``__getattr__``;
# they are intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {"DockerAgent": "agent", "DEFAULT_MODEL": "agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_docker.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the docker agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_docker.agent import DockerAgent

        return class_factory(DockerAgent)(**kwargs)

    return AgentRegistration(
        id="docker",
        name="Docker",
        description="Container management — build, run, and inspect Docker containers",
        source="installed",
        conversation_starters=[
            "List my running containers",
            "Build an image from this Dockerfile",
        ],
        factory=factory,
        agent_dir=None,
        models=["Qwen3.5-35B-A3B-GGUF"],
        namespaced_agent_id="installed:docker",
        category="infrastructure",
        tags=["docker", "containers", "devops"],
        icon="container",
        tools_count=0,
    )
