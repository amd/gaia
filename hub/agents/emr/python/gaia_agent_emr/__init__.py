# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA EMR (Medical Intake) agent — standalone hub package.

Registers the ``emr`` agent into the GAIA registry via the ``gaia.agent``
entry-point group. The agent module is imported lazily so registry discovery
stays cheap.

NOTE: This is a demonstration/proof-of-concept application. Not intended for
production use with real patient data.
"""

# ``MedicalIntakeAgent`` is re-exported lazily via ``__getattr__`` so importing
# this package at discovery time does not pull in the heavy agent module (VLM,
# database, file watcher); it is therefore intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {"MedicalIntakeAgent": "agent"}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_emr.{_LAZY[name]}")
        return getattr(module, name)
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
        description="Medical intake — VLM extraction of patient forms into a records database",
        source="installed",
        conversation_starters=[
            "How many patients were processed today?",
            "Find patient John Smith",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:emr",
        category="healthcare",
        tags=["emr", "medical", "vlm", "intake"],
        icon="stethoscope",
        tools_count=0,
    )
