# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Code agent — standalone hub package.

Registers the ``code`` agent into the GAIA registry via the ``gaia.agent``
entry-point group. Public names are re-exported lazily so registry discovery
stays cheap.
"""

# Re-exported lazily via ``__getattr__``; intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {
    "CodeAgent": "agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_code.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the code agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_code.agent import CodeAgent

        return class_factory(CodeAgent)(**kwargs)

    return AgentRegistration(
        id="code",
        name="Code",
        description="Autonomous code generation — plan, write, lint, fix, and test",
        source="installed",
        conversation_starters=[
            "Create a Python CLI that fetches weather for a city",
            "Build a Next.js todo app with a SQLite backend",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:code",
        category="development",
        tags=["code", "python", "typescript", "nextjs"],
        icon="code",
        tools_count=0,
    )
