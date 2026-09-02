# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""
Email Tools — read-only mailbox access for the flagship agent.

Gives an agent the ability to list, search, and read mail from a connected
mailbox so a skill (``hub/skills/inbox-triage``) can do the judging. The tools
deliberately return facts, not verdicts: categorisation is the model's job,
driven by the skill, which is the whole point of moving email onto the flagship
rather than shipping a second agent with its own classifier.

Read-only by design. Nothing here archives, sends, or deletes; write verbs
arrive with the reversible-action ledger in a later phase. See
``docs/plans/email-triage-skill.mdx``.

Provider support: Outlook / Microsoft Graph. Gmail lands in a later phase behind
the same tool names — the tools return a provider-neutral shape so neither the
skill nor the model has to care which mailbox is connected.
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The agent identity the grant ledger records these tools under. Namespaced per
# gaia.connectors.grants: the flagship ships as a wheel-installed hub agent.
EMAIL_AGENT_ID = "installed:gaia"

MICROSOFT_CONNECTOR_ID = "microsoft"

# Phase 0 is read-only but requests Mail.ReadWrite rather than Mail.Read.
#
# check_agent_grant does exact string-subset matching with no scope-implication
# logic, so a user who already granted Mail.ReadWrite would be forced to
# re-consent for a strictly narrower scope. Requesting the broader scope reuses
# the existing grant. The trade is that the agent holds write scope during a
# phase that performs no writes; it narrows when the write tools land and the
# scope set becomes meaningful. Tracked in the plan doc, not silently accepted.
MAIL_SCOPES: tuple = ("https://graph.microsoft.com/Mail.ReadWrite",)

_MAX_LIMIT = 100


class EmailToolsMixin:
    """Read-only mailbox tools (Outlook / Microsoft Graph).

    Tool registration follows the GAIA pattern: ``register_email_tools()``.

    The mixin builds its backend lazily on first use, so composing it costs an
    agent nothing until a mail tool is actually called — an agent whose user
    never mentions email never touches the connectors layer.
    """

    _email_backend = None  # OutlookReadBackend, built lazily

    def _build_email_backend(self):
        """Construct the mailbox backend, or fail with an actionable error."""
        from gaia.agents.tools._email import MailboxError, OutlookReadBackend

        try:
            from gaia.connectors.api import get_access_token_sync
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise MailboxError(
                "The connectors framework is unavailable, so no mailbox can be "
                "reached. Reinstall GAIA with `uv pip install -e .` and retry."
            ) from exc

        def _token() -> str:
            return get_access_token_sync(
                provider=MICROSOFT_CONNECTOR_ID,
                scopes=list(MAIL_SCOPES),
                agent_id=EMAIL_AGENT_ID,
            )

        return OutlookReadBackend(_token)

    def _email(self):
        """The mailbox backend for this agent, built on first use."""
        if self._email_backend is None:
            self._email_backend = self._build_email_backend()
        return self._email_backend

    def register_email_tools(self) -> None:
        """Register read-only email tools."""
        from gaia.agents.base.tools import tool

        mixin = self

        def _fail(exc: Exception, action: str) -> str:
            """Render an exception as an actionable tool result.

            Errors are surfaced, never swallowed: the model needs to tell the
            user what to fix, and a tool that returns an empty list on failure
            reads as "your inbox is empty".
            """
            logger.warning("email tool failed during %s: %s", action, exc)
            return json.dumps(
                {"error": str(exc), "action": action, "success": False}, indent=2
            )

        def _clamp(limit: int) -> int:
            return max(1, min(int(limit), _MAX_LIMIT))

        @tool(atomic=True)
        def check_mailbox_access() -> str:
            """Check whether a mailbox is connected and readable.

            Call this first when the user asks about email and you are unsure a
            mailbox is set up, or when another email tool has just failed. It
            reports the connected address and the inbox unread count.

            Returns the mailbox address and folder counts, or an error naming
            what the user must do to connect one.
            """
            try:
                backend = mixin._email()
                address = backend.get_user_email()
                folders = backend.list_folders(limit=50)
                inbox = next(
                    (f for f in folders if (f["name"] or "").lower() == "inbox"), None
                )
                return json.dumps(
                    {
                        "success": True,
                        "provider": "outlook",
                        "address": address,
                        "inbox_unread": inbox["unread"] if inbox else None,
                        "inbox_total": inbox["total"] if inbox else None,
                        "folder_count": len(folders),
                    },
                    indent=2,
                )
            except Exception as exc:  # surfaced, not swallowed
                return _fail(exc, "check_mailbox_access")

        @tool(atomic=True)
        def list_inbox(limit: int = 25, unread_only: bool = False) -> str:
            """List recent email in the inbox, newest first.

            The tool to start any mail question with: triaging the inbox,
            finding which emails need a reply, seeing what arrived today, what
            is unread, what is waiting on the user, or what is important.

            Returns metadata and a short preview for each message — sender,
            subject, received time, unread and flagged state — but not full
            bodies. Use `read_email` when you need the body of one message.

            Args:
                limit: How many messages to return (1-100, default 25)
                unread_only: Only return messages that are still unread
            """
            try:
                messages = mixin._email().list_inbox(
                    limit=_clamp(limit), unread_only=bool(unread_only)
                )
                return json.dumps(
                    {"success": True, "count": len(messages), "messages": messages},
                    indent=2,
                )
            except Exception as exc:
                return _fail(exc, "list_inbox")

        @tool(atomic=True)
        def search_email(query: str, limit: int = 25) -> str:
            """Find email matching a keyword, from anyone, in any mail folder.

            Use to answer "did I get mail about X", to find a message from a
            named sender, or to look for a receipt, invoice, or thread the user
            half-remembers.

            Searches every folder, not just the inbox. Results come back in
            relevance order, NOT newest-first — do not describe them as "the
            most recent" unless you check the received timestamps yourself.

            Args:
                query: Keywords to search for (e.g. 'invoice from Acme')
                limit: How many messages to return (1-100, default 25)
            """
            try:
                messages = mixin._email().search(query, limit=_clamp(limit))
                return json.dumps(
                    {
                        "success": True,
                        "count": len(messages),
                        "order": "relevance",
                        "messages": messages,
                    },
                    indent=2,
                )
            except Exception as exc:
                return _fail(exc, "search_email")

        @tool(atomic=True)
        def read_email(message_id: str) -> str:
            """Read one message in full, including its body.

            Use after `list_inbox` or `search_email` has given you a message id.
            Fetching bodies is the expensive call — read the messages you
            actually need to judge, not every message in a listing.

            Args:
                message_id: The message id from a listing or search result
            """
            try:
                message = mixin._email().get_message(message_id)
                return json.dumps({"success": True, "message": message}, indent=2)
            except Exception as exc:
                return _fail(exc, "read_email")

        @tool(atomic=True)
        def list_mail_folders() -> str:
            """List mail folders with their unread and total message counts.

            Use to answer "how much mail is in X" or to find a folder's name
            before searching it.
            """
            try:
                folders = mixin._email().list_folders()
                return json.dumps(
                    {"success": True, "count": len(folders), "folders": folders},
                    indent=2,
                )
            except Exception as exc:
                return _fail(exc, "list_mail_folders")


__all__ = ["EmailToolsMixin", "EMAIL_AGENT_ID", "MAIL_SCOPES", "MICROSOFT_CONNECTOR_ID"]
