# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Jira agent — standalone hub package.

Registers the ``jira`` agent into the GAIA registry via the ``gaia.agent``
entry-point group. Public names are re-exported lazily so registry discovery
stays cheap.
"""

# Re-exported lazily via ``__getattr__``; intentionally absent from ``__all__``.
__all__ = ["build_registration"]

__version__ = "0.1.0"

_LAZY = {
    "JiraAgent": "agent",
    "JiraIssue": "agent",
    "JiraSearchResult": "agent",
    "generate_jql_from_templates": "jql_templates",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_jira.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the jira agent."""
    from gaia.agents.registry import AgentRegistration, class_factory

    def factory(**kwargs):
        from gaia_agent_jira.agent import JiraAgent

        return class_factory(JiraAgent)(**kwargs)

    return AgentRegistration(
        id="jira",
        name="Jira",
        description="Jira issue management — search, create, and update issues",
        source="installed",
        conversation_starters=[
            "Show my open issues",
            "Create a bug for the login crash",
        ],
        factory=factory,
        agent_dir=None,
        models=["Gemma-4-E4B-it-GGUF"],
        namespaced_agent_id="installed:jira",
        category="productivity",
        tags=["jira", "issues", "atlassian"],
        icon="kanban",
        tools_count=0,
    )
