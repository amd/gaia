# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP **stdio** server for the Email Triage Agent (#1104).

Exposes the email agent's triage / draft / send capability over MCP — the same
surface the REST API (#1229) exposes over HTTP — so a desktop MCP client
(VSCode, Copilot, Claude Desktop) can drive it over stdin/stdout.

Parity by construction
----------------------
The ``triage_email`` tool calls the *same* FastAPI-free ``EmailTriageService``
that backs ``POST /v1/email/triage``, validating the *same* frozen #1262
contract (``gaia_agent_email.contract``). Identical service + identical
contract ⇒ identical structured output. ``tests/mcp/test_email_mcp_stdio_parity``
locks this in by comparing both surfaces byte-for-byte.

Send-confirmation gate (#1264)
------------------------------
``send_email`` mirrors the REST 403 gate: it refuses to send without a valid,
payload-bound, single-use confirmation token minted by ``draft_reply``. This
server holds its **own** ``ConfirmationStore`` instance — the gate is a
per-surface boundary, so MCP tokens never mix with REST tokens. A blocked send
returns a structured error (``{"sent": false, "error": ...}``); it never raises
a send through.

The triage path is deterministic (heuristic categorizer + rule-based summary),
so the wrapped agent constructs with ``skip_lemonade=True`` — no Lemonade, no
network, no Google credentials needed to triage. Only ``send_email`` touches a
mail backend, and only on the authorized path.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from gaia.agents.base.console import SilentConsole
from gaia.agents.base.mcp_agent import MCPAgent
from gaia_agent_email.contract import (
    BatchTriageRequest,
    DraftReply,
    EmailAddress,
    EmailTriageRequest,
)
from gaia_agent_email.api_routes import (  # read-only reuse of the REST surface
    ConfirmationStore,
    EmailTriageService,
    _format_address,
    _payload_fingerprint,
)
from gaia.connectors.api import connected_mailbox_providers
from gaia.logger import get_logger
from gaia_agent_email.version import AGENT_VERSION

logger = get_logger(__name__)

# Opt-in test seam: swap the live Gmail send for an in-process fake so the
# parity test never touches real mail. Production never sets this, and the send
# path then fails loudly if Google is not connected (no silent fallback).
_FAKE_SEND_ENV = "GAIA_EMAIL_MCP_FAKE_SEND"


# ---------------------------------------------------------------------------
# Tool schemas (mirror the REST surface's request bodies)
# ---------------------------------------------------------------------------

# triage_email accepts a contract EmailTriageRequest. ``payload`` is the
# discriminated single|thread union; ``schema_version`` and ``context`` are
# optional. ``context`` is validated by EmailTriageRequest.model_validate — no
# parse-code change here (#1541).
_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "payload": {
            "type": "object",
            "description": (
                "The #1262 contract input: a single email "
                "(kind='single', with 'message') or a full thread "
                "(kind='thread', with 'thread_id' + 'messages')."
            ),
        },
        "schema_version": {
            "type": "string",
            "description": "Contract version. Defaults to the frozen current revision.",
        },
        "context": {
            "type": "object",
            "description": (
                "Optional context that biases categorization/summary (#1541): "
                "people (list), projects (list), tone (string), self_email "
                "(string). Absent → behavior unchanged."
            ),
            "properties": {
                "people": {"type": "array", "items": {"type": "string"}},
                "projects": {"type": "array", "items": {"type": "string"}},
                "tone": {"type": "string"},
                "self_email": {"type": "string"},
            },
        },
    },
    "required": ["payload"],
}

# triage_email_batch accepts BatchTriageRequest. ``items`` is the list of
# SingleEmailInput / ThreadInput objects; ``schema_version`` and ``context``
# are optional. Discriminator on ``kind`` is enforced by pydantic.
_BATCH_TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": (
                "1..100 email or thread inputs to triage. Each item must be a "
                "SingleEmailInput (kind='single', with 'principal' + 'message') "
                "or a ThreadInput (kind='thread', with 'principal' + 'thread_id' "
                "+ 'messages'). Order is preserved in the response."
            ),
            "minItems": 1,
            "maxItems": 100,
            "items": {"type": "object"},
        },
        "schema_version": {
            "type": "string",
            "description": "Contract version. Defaults to the frozen current revision.",
        },
        "context": {
            "type": "object",
            "description": (
                "Optional context applied to ALL items (#1541): "
                "people (list), projects (list), tone (string), self_email "
                "(string). Absent → behavior unchanged."
            ),
            "properties": {
                "people": {"type": "array", "items": {"type": "string"}},
                "projects": {"type": "array", "items": {"type": "string"}},
                "tone": {"type": "string"},
                "self_email": {"type": "string"},
            },
        },
    },
    "required": ["items"],
}

# draft_reply / send_email mirror EmailDraftRequest / EmailSendRequest. ``to``
# is a list of {name?, email} address objects.
_TO_PROP = {
    "type": "array",
    "description": "Recipient address objects: [{'email': 'a@b.com', 'name': 'A'}].",
}

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "to": _TO_PROP,
        "subject": {"type": "string", "description": "Proposed subject line."},
        "body": {"type": "string", "description": "Proposed reply body."},
    },
    "required": ["to", "subject", "body"],
}

_SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "to": _TO_PROP,
        "subject": {"type": "string", "description": "Subject line."},
        "body": {"type": "string", "description": "Reply body."},
        "confirmation_token": {
            "type": "string",
            "description": (
                "Token from draft_reply authorizing exactly this "
                "(to, subject, body). A send without a valid token is rejected."
            ),
        },
    },
    "required": ["to", "subject", "body"],
}


def _coerce_addresses(raw: Any) -> List[EmailAddress]:
    """Validate a list of address dicts into contract ``EmailAddress`` objects.

    Raises ``ValueError`` (loudly) on a malformed address — never coerces
    garbage into a send.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("'to' must be a non-empty list of address objects")
    return [EmailAddress.model_validate(a) for a in raw]


class _FakeSendBackend:
    """In-process stand-in for the Gmail send backend (test seam only).

    Returns a Gmail-API-shaped ``{"id": ...}`` like ``LiveGmailBackend`` so the
    send tool's success path is exercised end-to-end without real mail.
    """

    def __init__(self) -> None:
        self._seq = 0

    def send_message(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        # Signature mirrors LiveGmailBackend.send_message; body/headers are part
        # of that contract but unused by the in-process fake.
        # pylint: disable=unused-argument
        self._seq += 1
        return {"id": f"mcp_fake_{self._seq}", "to": to, "subject": subject}


class EmailTriageMCPAgent(MCPAgent):
    """Deterministic MCP wrapper exposing the email triage / draft / send surface.

    Construct with ``skip_lemonade=True``: triage is rule-based, so no LLM
    backend is contacted. The send gate uses an instance-local
    ``ConfirmationStore`` so MCP confirmation state never mixes with the REST
    surface's store.
    """

    AGENT_ID = "email-mcp"
    AGENT_NAME = "Email Triage MCP"

    def __init__(self, **agent_params: Any):
        # Triage needs no LLM; skip Lemonade so the server starts offline.
        agent_params.setdefault("skip_lemonade", True)
        agent_params.setdefault("silent_mode", True)
        # Instance-local — NOT the REST module-level store. Per-surface gate.
        self._confirmation_store = ConfirmationStore()
        self._service = EmailTriageService()
        super().__init__(**agent_params)

    # -- base Agent contract (no agent-loop tools; MCP tools are separate) ---

    def _get_system_prompt(self) -> str:
        return "GAIA Email Triage MCP server."

    def _create_console(self):
        return SilentConsole()

    def _register_tools(self) -> None:
        # No @tool agent-loop tools — this wrapper exposes capability through
        # the MCP tool interface (get_mcp_tool_definitions / execute_mcp_tool).
        pass

    # -- MCP interface ------------------------------------------------------

    def get_mcp_server_info(self) -> Dict[str, Any]:
        return {"name": "GAIA Email Triage", "version": AGENT_VERSION}

    def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "triage_email",
                "description": (
                    "Triage a single email or a full thread. Returns the "
                    "structured #1262 result: category, spam/phishing signals, "
                    "a plain-text summary, action items, and an optional draft "
                    "reply. Analyzes only the payload — reads/sends no mail."
                ),
                "inputSchema": _TRIAGE_SCHEMA,
            },
            {
                "name": "triage_email_batch",
                "description": (
                    "Triage a batch of emails or threads in a single call "
                    "(#1887). Accepts 1..100 EmailInput items (each a "
                    "SingleEmailInput or ThreadInput) and returns a "
                    "BatchTriageResponse with one BatchItemResult per item, "
                    "order-preserved. Per-item failures set "
                    "BatchItemResult.error; remaining items are still "
                    "processed. HTTP-200 with all items errored is valid — "
                    "inspect each result. Reads/sends no mail."
                ),
                "inputSchema": _BATCH_TRIAGE_SCHEMA,
            },
            {
                "name": "draft_reply",
                "description": (
                    "Propose a reply and mint a single-use confirmation token "
                    "bound to its exact (to, subject, body). Echo the token to "
                    "send_email to authorize sending that payload."
                ),
                "inputSchema": _DRAFT_SCHEMA,
            },
            {
                "name": "send_email",
                "description": (
                    "Send a reply — gated on confirmation (#1264). Rejected "
                    "unless a valid confirmation_token for this exact payload "
                    "(from draft_reply) is supplied. Never auto-confirms."
                ),
                "inputSchema": _SEND_SCHEMA,
            },
        ]

    def execute_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        if tool_name == "triage_email":
            return self._triage(arguments)
        if tool_name == "triage_email_batch":
            return self._triage_batch(arguments)
        if tool_name == "draft_reply":
            return self._draft(arguments)
        if tool_name == "send_email":
            return self._send(arguments)
        raise ValueError(f"Unknown tool: {tool_name}")

    # -- tool bodies --------------------------------------------------------

    def _triage(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Build the request via the contract so an invalid payload fails loudly
        # with the SAME validation the REST endpoint applies (extra='forbid').
        request = EmailTriageRequest.model_validate(arguments)
        response = self._service.triage_request(request)
        # JSON mode so enums serialize to their string values — the exact wire
        # form the REST endpoint returns. This is what guarantees parity.
        return response.model_dump(mode="json")

    def _triage_batch(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        # Same validation + JSON-mode serialization pattern as _triage — parity
        # with the REST /triage/batch endpoint is guaranteed by using the same
        # contract models and the same service method.
        request = BatchTriageRequest.model_validate(arguments)
        response = self._service.triage_batch(request)
        return response.model_dump(mode="json")

    def _draft(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        to = _coerce_addresses(arguments.get("to"))
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")
        fingerprint = _payload_fingerprint(to, subject, body)
        token = self._confirmation_store.issue(fingerprint)
        draft = DraftReply(to=to, subject=subject, body=body)
        return {
            "draft": draft.model_dump(mode="json"),
            "confirmation_token": token,
        }

    def _send(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        to = _coerce_addresses(arguments.get("to"))
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")
        token = arguments.get("confirmation_token") or ""

        # Gate FIRST — before resolving any backend — so a missing/invalid
        # token is rejected regardless of backend health (mirrors REST).
        fingerprint = _payload_fingerprint(to, subject, body)
        if not self._confirmation_store.consume(token, fingerprint):
            return {
                "sent": False,
                "error": (
                    "Send rejected: missing or invalid confirmation token for "
                    "this message. Call draft_reply to obtain a token bound to "
                    "this exact (to, subject, body), then echo it in "
                    "'confirmation_token'. Emails are never sent without "
                    "explicit confirmation."
                ),
            }

        backend = self._resolve_send_backend()
        to_header = ", ".join(_format_address(a) for a in to)
        result = backend.send_message(to=to_header, subject=subject, body=body)
        sent_id = result.get("id") or ""
        # Graph sendMail returns 202 with no body → sent=True, empty id is success.
        if not sent_id and not result.get("sent"):
            return {
                "sent": False,
                "error": "Email backend did not return a message id for the send.",
            }
        logger.info("email MCP send: id=%s to=%s", sent_id, to_header)
        return {
            "sent": True,
            "sent_id": sent_id,
            "to": [a.model_dump(mode="json") for a in to],
            "subject": subject,
        }

    def _resolve_send_backend(self):
        """Resolve the send backend AFTER the gate — connector-derived (#1603).

        The opt-in ``GAIA_EMAIL_MCP_FAKE_SEND`` seam short-circuits BEFORE the
        provider count check so parity tests run without a live mailbox. Then:

          - 0 connected → RuntimeError (actionable: go connect a mailbox)
          - 2+ connected → RuntimeError (actionable: ambiguous, use the agent UI)
          - exactly 1    → build the matching live backend

        Never silently no-ops a send.
        """
        if os.environ.get(_FAKE_SEND_ENV):
            return _FakeSendBackend()

        providers = connected_mailbox_providers()
        if not providers:
            raise RuntimeError(
                "No mailbox connected — connect Google or Microsoft in "
                "Settings → Connectors before sending via MCP."
            )
        if len(providers) > 1:
            raise RuntimeError(
                f"Multiple mailboxes connected ({', '.join(providers)}); "
                "the MCP send can't choose. Send from the agent UI (sends "
                "from the message's mailbox), or draft with a provider binding."
            )
        provider = providers[0]
        if provider == "google":
            from gaia_agent_email.gmail_backend import LiveGmailBackend, _get_gmail_token

            return LiveGmailBackend(_get_gmail_token)
        if provider == "microsoft":
            from gaia_agent_email.outlook_backend import (
                LiveOutlookBackend,
                _get_outlook_token,
            )

            return LiveOutlookBackend(_get_outlook_token)
        raise RuntimeError(
            f"Connected mailbox provider '{provider}' has no send backend. "
            "Expected 'google' or 'microsoft'."
        )


def start_email_mcp(
    transport: str = "stdio",
    port: int = None,
    host: str = None,
    verbose: bool = False,
) -> None:
    """Start the Email Triage MCP server.

    Args:
        transport: ``"stdio"`` (default — desktop MCP clients) or
            ``"streamable-http"``.
        port / host: HTTP bind settings (ignored for stdio).
        verbose: Enable verbose logging.
    """
    # Local import so ``setup.py`` consumers that only need the agent class
    # don't pull FastMCP at import time.
    from gaia.mcp.agent_mcp_server import AgentMCPServer

    server = AgentMCPServer(
        agent_class=EmailTriageMCPAgent,
        name="GAIA Email Triage MCP",
        port=port,
        host=host,
        verbose=verbose,
        transport=transport,
        # Typed registration so a standard MCP client gets precise per-tool
        # schemas (and structuredContent back), not the legacy **kwargs glue.
        register_typed_tools=True,
    )
    server.start()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="GAIA Email Triage MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio — what desktop MCP clients launch).",
    )
    parser.add_argument("--port", type=int, default=None, help="HTTP port (HTTP only).")
    parser.add_argument("--host", default=None, help="HTTP host (HTTP only).")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args()

    start_email_mcp(
        transport=args.transport,
        port=args.port,
        host=args.host,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
