# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA File I/O agent — standalone hub package.

Registers the ``fileio`` agent (read/write/edit files) into the GAIA registry
via the ``gaia.agent`` entry-point group. It is a building-block agent, hidden
from the UI selector by default. The agent module is imported lazily so
registry discovery stays cheap.
"""

# Re-exported lazily via ``__getattr__``; intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {"FileIOAgent": "agent", "FileIOAgentConfig": "agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_fileio.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the fileio agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_fileio.agent import FileIOAgent

        return class_factory(FileIOAgent)(**kwargs)

    return AgentRegistration(
        id="fileio",
        name="File I/O",
        description="Read, write, and edit files on the local filesystem",
        source="installed",
        conversation_starters=[
            "Read the contents of config.yaml",
            "Create a new file notes.md with my meeting notes",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        hidden=True,
        namespaced_agent_id="installed:fileio",
        category="productivity",
        tags=["files", "io", "edit"],
        icon="file",
        tools_count=0,
    )
