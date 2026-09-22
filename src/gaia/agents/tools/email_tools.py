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

Provider support: Gmail and Outlook / Microsoft Graph, behind one set of tool
names. Both backends return the same provider-neutral shape, so neither the
skill nor the model has to care which mailbox is connected.

Selection is connector-derived and has no override: a mailbox is eligible only
if it is connected, carries a read scope, and this agent holds a grant for it.
With both eligible, Gmail wins on registry order, and the way to change that is
to revoke the grant — not to set an environment variable, which would bypass
the gate that makes any of this checkable.
"""

import json
import logging
from typing import Dict, List, Optional, Tuple

from gaia.agents.tools._email.scopes import (
    DECLARED_SCOPES,
    GOOGLE_CONNECTOR_ID,
    MICROSOFT_CONNECTOR_ID,
    READ_CAPABLE_SCOPES,
    resolve_request_scope,
)

logger = logging.getLogger(__name__)

# The agent identity the grant ledger records these tools under. Namespaced per
# gaia.connectors.grants: the flagship ships as a wheel-installed hub agent.
EMAIL_AGENT_ID = "installed:gaia"

# What a NEW consent asks for. The token request resolves separately against
# what the connection already carries — see _email/scopes.py.
MAIL_SCOPES: tuple = DECLARED_SCOPES[MICROSOFT_CONNECTOR_ID]
GMAIL_SCOPES: tuple = DECLARED_SCOPES[GOOGLE_CONNECTOR_ID]

# Registry order, matching connectors.api.connected_mailbox_providers().
MAILBOX_PROVIDERS: tuple = (GOOGLE_CONNECTOR_ID, MICROSOFT_CONNECTOR_ID)

_EMAIL_DOCS_URL = "https://amd-gaia.ai/docs/guides/email"

_MAX_LIMIT = 100

# ~12% of the 32K NPU window at the worst measured 3.0 chars/token, so a
# triage turn can read several messages. Caps one body, not a whole turn.
_MAX_BODY_CHARS = 12_000


def _classify_mailbox(provider: str) -> Tuple[Optional[str], str]:
    """``(resolved_scope, explanation)`` for one provider.

    A ``None`` scope means unusable, and the explanation names *that
    provider's own* state and remedy — "grant it" is the wrong advice for a
    mailbox that is not connected at all.
    """
    from gaia.connectors.api import get_connection
    from gaia.connectors.grants import list_agent_grants

    conn = get_connection(provider)
    if not conn:
        return None, f"not connected. Run `gaia connectors connect {provider}`"
    if conn.get("error") == "configuration":
        return None, (
            "connected, but its OAuth client credentials are no longer "
            f"configured. Reconnect with `gaia connectors connect {provider}`"
        )

    conn_scopes = list(conn.get("scopes") or [])
    capable = [s for s in READ_CAPABLE_SCOPES[provider] if s in conn_scopes]
    if not capable:
        # `--scopes` REPLACES a connection's scopes, so the remedy must name
        # granted ∪ needed. Naming only the gap would strip what it already had.
        union = sorted(set(conn_scopes) | {DECLARED_SCOPES[provider][0]})
        return None, (
            "connected, but the connection carries no mail read scope. "
            f"Reconnect with `gaia connectors connect {provider} --scopes "
            f"{' '.join(union)}` (--scopes replaces, so keep the whole list)"
        )

    granted = list_agent_grants(provider).get(EMAIL_AGENT_ID, [])
    scope = resolve_request_scope(
        provider, connection_scopes=conn_scopes, granted_scopes=granted
    )
    if scope is None:
        return None, (
            "connected and readable, but this agent holds no matching grant. "
            f"Run `gaia connectors grants grant {provider} {EMAIL_AGENT_ID} "
            f"--scopes {capable[0]}` (a ledger write — no browser needed)"
        )
    return scope, "ready"


def _no_mailbox_error(states: Dict[str, Tuple[Optional[str], str]]) -> str:
    lines = "\n".join(f"  - {p}: {why}" for p, (_, why) in states.items())
    return (
        "No readable mailbox is available, so the email tools cannot run.\n"
        f"{lines}\n"
        f"See {_EMAIL_DOCS_URL}"
    )


class EmailToolsMixin:
    """Read-only mailbox tools (Gmail / Outlook).

    Tool registration follows the GAIA pattern: ``register_email_tools()``.

    The mixin builds its backend lazily on first use, so composing it costs an
    agent nothing until a mail tool is actually called — an agent whose user
    never mentions email never touches the connectors layer.
    """

    _email_backend = None  # GmailReadBackend | OutlookReadBackend, built lazily
    _email_backend_is_owned = False
    _email_provider: Optional[str] = None
    _email_provider_source: Optional[str] = None
    _email_alternatives: Optional[List[str]] = None

    # Per-turn mail-reading ledger. Reset whenever ``_turn_seq`` moves on.
    _email_turn_token = None
    _email_turn_body_chars = 0
    _email_turn_reads = 0

    def _email_turn_budget_chars(self) -> int:
        """Chars all mail bodies read in ONE turn may occupy, combined.

        Reuses ``Agent._truncation_budget`` — the same per-tool-result cap
        already applied to any other large tool output — so mail reading
        inherits the real device profile instead of a new constant.
        """
        if hasattr(self, "_truncation_budget"):
            return self._truncation_budget()[0]
        from gaia.llm.lemonade_client import truncation_budget

        return truncation_budget(getattr(self, "device", None))[0]

    def _email_turn_reset_if_stale(self) -> None:
        token = getattr(self, "_turn_seq", None)
        if self._email_turn_token != token:
            self._email_turn_token = token
            self._email_turn_body_chars = 0
            self._email_turn_reads = 0

    def _resolve_mailbox(self) -> Tuple[str, str, str, List[str]]:
        """``(provider, scope, source, alternatives)``, or raise naming why not."""
        from gaia.agents.tools._email import MailboxError

        states = {p: _classify_mailbox(p) for p in MAILBOX_PROVIDERS}
        usable = [p for p, (scope, _) in states.items() if scope]

        if not usable:
            raise MailboxError(_no_mailbox_error(states))

        chosen = usable[0]
        source = "only-granted" if len(usable) == 1 else "precedence"
        return chosen, states[chosen][0], source, [p for p in usable if p != chosen]

    def _build_email_backend(self):
        """Construct the mailbox backend, or fail with an actionable error."""
        from gaia.agents.tools._email import MailboxError

        try:
            import gaia.connectors.api as connectors_api
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise MailboxError(
                "The connectors framework is unavailable, so no mailbox can be "
                "reached. Reinstall GAIA with `uv pip install -e .` and retry."
            ) from exc

        provider, scope, source, alternatives = self._resolve_mailbox()

        def _token() -> str:
            return connectors_api.get_access_token_sync(
                provider=provider, scopes=[scope], agent_id=EMAIL_AGENT_ID
            )

        if provider == GOOGLE_CONNECTOR_ID:
            from gaia.agents.tools._email.gmail import GmailReadBackend

            backend = GmailReadBackend(_token)
        else:
            from gaia.agents.tools._email.graph import OutlookReadBackend

            backend = OutlookReadBackend(_token)

        self._email_provider = provider
        self._email_provider_source = source
        self._email_alternatives = alternatives
        return backend

    def _email(self):
        """The mailbox backend for this agent, built on first use."""
        if self._email_backend is None:
            self._email_backend = self._build_email_backend()
            self._email_backend_is_owned = True
        return self._email_backend

    def _email_call(self, method: str, *args, **kwargs):
        """Run one backend read, re-resolving once if the mailbox rejects us.

        A connect or grant made mid-session leaves the memoized backend stale;
        re-selecting once is cheaper than telling the user to restart. The
        second failure is surfaced, not retried.
        """
        from gaia.agents.tools._email import MailboxAuthError

        try:
            return getattr(self._email(), method)(*args, **kwargs)
        except MailboxAuthError:
            if not self._email_backend_is_owned:
                raise
            logger.info("email: re-resolving the mailbox after an auth failure")
            self._email_backend = None
            self._email_backend_is_owned = False
            return getattr(self._email(), method)(*args, **kwargs)

    def register_email_tools(self) -> None:
        """Register read-only email tools."""
        from gaia.agents.base.tools import tool

        mixin = self

        def _fail(exc, action: str, *, refusal: bool = False, **extra) -> str:
            """Render an exception (or a refusal) as an actionable tool result.

            Errors are surfaced, never swallowed: the model needs to tell the
            user what to fix, and a tool that returns an empty list on failure
            reads as "your inbox is empty". ``extra`` carries structured fields
            for a refusal (e.g. ``turn_budget_exhausted``) alongside the error.
            """
            if refusal:
                logger.info("email: %s refused — %s", action, exc)
            else:
                logger.warning("email tool failed during %s: %s", action, exc)
            payload = {"error": str(exc), "action": action, "success": False}
            payload.update(extra)
            return json.dumps(payload, indent=2)

        def _clamp(limit: int) -> int:
            return max(1, min(int(limit), _MAX_LIMIT))

        @tool(atomic=True)
        def check_mailbox_access() -> str:
            """Check whether a mailbox is connected and readable.

            Call this first when the user asks about email and you are
            unsure a mailbox is set up, or after an email tool fails.

            `alternatives` non-empty means another usable mailbox lost on
            precedence; switching needs `gaia connectors grants revoke
            <provider> installed:gaia`.

            Returns the address and folder counts, or an error naming what
            to fix.
            """
            try:
                address = mixin._email_call("get_user_email")
                folders = mixin._email_call("list_folders", limit=50)
                inbox = next(
                    (f for f in folders if (f["name"] or "").lower() == "inbox"), None
                )
                return json.dumps(
                    {
                        "success": True,
                        "provider": mixin._email_provider,
                        "provider_source": mixin._email_provider_source,
                        "alternatives": mixin._email_alternatives or [],
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

            Start any mail question here: triage, what needs a reply, what
            arrived today, what is unread. Returns sender, subject, received
            time, unread and flagged state, and a short preview — not full
            bodies. Use read_email for one message's body.

            Args:
                limit: How many messages to return (1-100, default 25)
                unread_only: Only return messages that are still unread
            """
            try:
                messages = mixin._email_call(
                    "list_inbox", limit=_clamp(limit), unread_only=bool(unread_only)
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

            Answers "did I get mail about X", finds a named sender, or a
            receipt or thread the user half-remembers. Covers every folder,
            not just the inbox. Results come back in relevance order, NOT
            newest-first — check the timestamps before calling one "recent".

            Args:
                query: Keywords to search for (e.g. 'invoice from Acme')
                limit: How many messages to return (1-100, default 25)
            """
            try:
                messages = mixin._email_call("search", query, limit=_clamp(limit))
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

            Use after `list_inbox` or `search_email` gives you a message id.
            Bodies are expensive — read only what you must judge.

            A long body is truncated and says so, with its original
            length. When the turn's mail budget is spent you get
            `turn_budget_exhausted: true` instead: stop, don't retry,
            say so. Never report a truncated or refused read as
            complete.

            Args:
                message_id: The message id from a listing or search result
            """
            mixin._email_turn_reset_if_stale()
            budget = mixin._email_turn_budget_chars()
            used = mixin._email_turn_body_chars
            reads = mixin._email_turn_reads
            if used >= budget:
                return _fail(
                    f"This turn has already read {reads} message body(ies), "
                    f"filling this turn's mail-reading budget ({used} of "
                    f"{budget} chars) — further reads are refused so the "
                    "conversation does not silently overflow the context "
                    "window. Answer from the messages already read, tell the "
                    "user reading stopped here, and ask them to narrow the "
                    f"request or continue in a new turn. See {_EMAIL_DOCS_URL}",
                    "read_email",
                    refusal=True,
                    turn_budget_exhausted=True,
                    messages_read_this_turn=reads,
                    budget_chars=budget,
                    budget_used_chars=used,
                )
            try:
                message = mixin._email_call("get_message", message_id)
                bounded = _bound_body(message)
                mixin._email_turn_body_chars += len(bounded.get("body") or "")
                mixin._email_turn_reads += 1
                return json.dumps({"success": True, "message": bounded}, indent=2)
            except Exception as exc:
                return _fail(exc, "read_email")

        @tool(atomic=True)
        def list_mail_folders() -> str:
            """List mail folders with their unread and total message counts.

            Use to answer "how much mail is in X" or to find a folder's name
            before searching it.
            """
            try:
                folders = mixin._email_call("list_folders")
                return json.dumps(
                    {"success": True, "count": len(folders), "folders": folders},
                    indent=2,
                )
            except Exception as exc:
                return _fail(exc, "list_mail_folders")


def _bound_body(message: dict) -> dict:
    """Cap one message body, visibly. Provider-neutral so the shape holds."""
    body = message.get("body") or ""
    if len(body) <= _MAX_BODY_CHARS:
        return message
    bounded = dict(message)
    bounded["body"] = body[:_MAX_BODY_CHARS]
    bounded["body_truncated"] = True
    bounded["body_original_chars"] = len(body)
    return bounded


__all__ = [
    "EmailToolsMixin",
    "EMAIL_AGENT_ID",
    "GMAIL_SCOPES",
    "MAIL_SCOPES",
    "MAILBOX_PROVIDERS",
    "MICROSOFT_CONNECTOR_ID",
    "GOOGLE_CONNECTOR_ID",
]
