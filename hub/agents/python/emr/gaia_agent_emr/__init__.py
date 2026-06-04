# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA Medical Intake (EMR) agent — standalone hub package.

Installs the ``emr`` agent into the GAIA registry via the ``gaia.agent``
entry-point group (see ``pyproject.toml``). The framework's
``AgentRegistry._discover_installed_agents`` calls :func:`build_registration`
at discovery time; the agent module itself is imported lazily inside the
factory so discovery stays cheap.
"""

# ``MedicalIntakeAgent`` is re-exported lazily via ``__getattr__`` (below) so
# that importing this package at registry-discovery time does not pull in the
# heavy agent module; it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"


def __getattr__(name):
    # Lazy re-export so ``import gaia_agent_emr`` (e.g. at registry discovery)
    # does not pull in the heavy agent module + its SDK deps.
    if name == "MedicalIntakeAgent":
        from gaia_agent_emr.agent import MedicalIntakeAgent

        return MedicalIntakeAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the EMR agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_emr.agent import MedicalIntakeAgent

        return class_factory(MedicalIntakeAgent)(**kwargs)

    return AgentRegistration(
        id="emr",
        name="Medical Intake",
        description="Medical form intake and extraction (VLM)",
        source="installed",
        conversation_starters=[
            "Process the latest intake form",
            "Show me patient records",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:emr",
        category="productivity",
        tags=["medical", "vlm", "intake"],
        icon="stethoscope",
        tools_count=0,
        hidden=False,
    )
