# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Email Triage agent — standalone hub package.

Registers the ``email`` agent into the GAIA registry via the ``gaia.agent``
entry-point group (#1102). ``EmailTriageAgent`` / ``EmailAgentConfig`` are
re-exported lazily (PEP 562) so that ``from gaia_agent_email.contract import
...`` — the dependency-light request/response contract used by the REST surface
(#1229) and the MCP stdio interface (#1104) — and registry discovery do NOT
drag the agent and its Gmail / connector backends into the importing process.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

__all__ = ["build_registration", "EmailTriageAgent", "EmailAgentConfig"]

# Single source of truth for the package version lives in ``version.py`` (the
# same module the REST and freeze servers read), so ``__version__`` has one home.
from gaia_agent_email.version import AGENT_VERSION as __version__

_LAZY = {
    "EmailTriageAgent": "agent",
    "EmailAgentConfig": "config",
}


def __getattr__(name: str):
    # PEP 562 lazy attribute access — keeps the heavy agent import off the path
    # of dependency-light consumers (the contract module) and registry discovery.
    if name in _LAZY:
        import importlib

        module = importlib.import_module(f"gaia_agent_email.{_LAZY[name]}")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_registration():
    """Return the :class:`AgentRegistration` for the ``email`` agent.

    Metadata is declared with literals (and connector requirements rebuilt from
    the dependency-light scope constants) so discovery stays cheap — the agent
    and its backends are imported only when an email agent is actually created.
    Values mirror ``EmailTriageAgent``'s class attributes exactly.
    """
    import dataclasses

    from gaia_agent_email.outlook_scopes import (
        OUTLOOK_CALENDAR_SCOPES,
        OUTLOOK_MAIL_SCOPES,
    )
    from gaia_agent_email.scopes import ALL_SCOPES

    from gaia.agents.registry import (
        AgentRegistration,
        _wrap_factory_with_namespaced_id,
    )
    from gaia.connectors.providers.base import ConnectorRequirement

    def email_factory(**kwargs):
        from gaia_agent_email.agent import EmailTriageAgent
        from gaia_agent_email.config import EmailAgentConfig

        valid_fields = {f.name for f in dataclasses.fields(EmailAgentConfig)}
        config = EmailAgentConfig(
            **{k: v for k, v in kwargs.items() if k in valid_fields}
        )
        return EmailTriageAgent(config=config)

    # Provider-superset connector list, mirrored from
    # EmailTriageAgent.REQUIRED_CONNECTORS so the AgentUI offers both the Google
    # and Microsoft tiles. Rebuilt from the light scope constants to avoid
    # importing the heavy agent module at discovery time.
    required_connections = [
        ConnectorRequirement(
            connector_id="google",
            scopes=ALL_SCOPES,
            reason=(
                "Read and organize Gmail messages, send drafts on your "
                "behalf, and respond to Google Calendar invites."
            ),
        ),
        ConnectorRequirement(
            connector_id="microsoft",
            scopes=OUTLOOK_MAIL_SCOPES + OUTLOOK_CALENDAR_SCOPES,
            reason=(
                "Read and organize your personal Outlook.com mailbox, send "
                "messages on your behalf, and read/respond to your Outlook "
                "calendar via Microsoft Graph."
            ),
        ),
    ]

    return AgentRegistration(
        id="email",
        name="Email Triage",
        description=(
            "Read, triage, organize, and reply to email through your "
            "connected Google account. All email content is processed "
            "locally on your machine."
        ),
        source="installed",
        conversation_starters=[
            "Run a pre-scan",
            "Triage my inbox",
            "Which of my sent emails are still waiting on a reply?",
            "Summarize my unread emails",
            "Draft a reply to my most recent message",
            "Show me today's calendar",
        ],
        factory=_wrap_factory_with_namespaced_id(email_factory, "installed:email"),
        agent_dir=None,
        models=[],
        required_connections=required_connections,
        namespaced_agent_id="installed:email",
        category="productivity",
        tags=["email", "gmail", "calendar", "triage"],
        icon="mail",
        tools_count=52,
    )
