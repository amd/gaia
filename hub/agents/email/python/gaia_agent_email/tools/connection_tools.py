# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Connection-status tools mixin for ``EmailTriageAgent``.

Exposes the agent's LIVE mailbox connection state to the LLM so
conversational questions like "which mailbox are you connected to?" are
answered from real connector state instead of the capability text in the
system prompt.

Tools registered:

- ``list_connected_mailboxes()`` — reports the currently connected mailbox
  providers and their account emails, or an actionable empty-state when
  nothing is connected.

State is resolved LIVE per call (via ``available_mailbox_providers()`` +
``get_connection``), so disconnect → reconnect without a GAIA restart is
reflected on the next question. This is the read-only introspection surface
that complements the reactive fail-loud errors on mailbox operations.
"""

from __future__ import annotations

from typing import Optional

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

log = get_logger(__name__)


def _display_account_email(raw: object) -> Optional[str]:
    """Map the store's ``DEFAULT_ACCOUNT`` no-email sentinel (and empties) to
    ``None`` so the sentinel never leaks to the user as a literal "default"."""
    from gaia.connectors.store import DEFAULT_ACCOUNT

    email = raw or None
    return None if email == DEFAULT_ACCOUNT else email


class ConnectionToolsMixin:
    """Mixin that registers the live mailbox connection-status tool.

    State-free at construction time — reads ``self.config`` via a closure
    captured when ``_register_connection_tools()`` is called.
    """

    def _register_connection_tools(self) -> None:
        agent = self  # closure for live access to self.config

        @tool
        def list_connected_mailboxes() -> str:
            """Report which email mailboxes are connected right now.

            Call this for ANY question about connection state — "which mailbox
            are you connected to?", "what account is linked?", "am I connected
            to Gmail?", "which providers can you see?". NEVER answer such a
            question from your own description of your capabilities; the live
            connection state is only available through this tool.

            State is read live per call, so a disconnect or reconnect made in
            Settings → Connectors (no GAIA restart) is reflected immediately.

            Returns:
                JSON envelope. When at least one mailbox is connected:
                ``{"ok": true, "data": {"connected": true, "mailboxes":
                [{"provider": "google", "account_email":
                "user@gmail.com"}, ...]}}`` (``account_email`` may be null when
                the provider stored no address). When nothing is connected:
                ``{"ok": true, "data": {"connected": false, "mailboxes": [],
                "message": "No mailbox connected — connect Google or Microsoft
                in Settings → Connectors."}}``.
            """
            try:
                from gaia.connectors.api import get_connection

                providers = agent.config.available_mailbox_providers()
                if not providers:
                    return _envelope_ok(
                        {
                            "connected": False,
                            "mailboxes": [],
                            "message": (
                                "No mailbox connected — connect Google or "
                                "Microsoft in Settings → Connectors."
                            ),
                        }
                    )
                mailboxes = []
                for provider in providers:
                    conn = get_connection(provider) or {}
                    mailboxes.append(
                        {
                            "provider": provider,
                            "account_email": _display_account_email(
                                conn.get("account_email")
                            ),
                        }
                    )
                return _envelope_ok({"connected": True, "mailboxes": mailboxes})
            except Exception as exc:
                log.exception("list_connected_mailboxes failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
