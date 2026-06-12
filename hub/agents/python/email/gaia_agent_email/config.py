# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Configuration dataclass for the Email Triage Agent.

AC3 enforcement is **architectural** at this layer: there is NO field on
``EmailAgentConfig`` that can route email body content to a cloud LLM.
The lint gate at ``util/check_email_agent_local_only.py`` proves this
property statically.

Eval-mode injection seam: the eval harness passes
``gmail_backend=FakeGmailBackend(mbox_path)`` to bypass the live Gmail
API entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from gaia.agents.base.agent import default_max_steps
from gaia.connectors.api import connected_mailbox_providers


class ConfigurationError(ValueError):
    """Raised when the email agent's config is structurally invalid.

    Distinct from ``gaia.connectors.errors.ConfigurationError`` — this is
    a startup-time guard against AC3 bypass via ``base_url``.
    """


# Hosts that ``base_url`` is allowed to point at. Anything else fails at
# agent construction. The Lemonade host is derived from the
# ``LEMONADE_BASE_URL`` env var so users running Lemonade on a non-default
# port still pass the check.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _allowed_hosts() -> set[str]:
    out = set(_LOCAL_HOSTS)
    env = os.environ.get("LEMONADE_BASE_URL", "")
    if env:
        parsed = urlparse(env)
        host = parsed.hostname
        if not host:
            raise ConfigurationError(
                f"LEMONADE_BASE_URL={env!r} is not a valid URL: "
                "could not extract a hostname. Set it to a valid URL "
                "such as http://localhost:11434."
            )
        out.add(host)
    return out


@dataclass
class EmailAgentConfig:
    """Configuration for ``EmailTriageAgent``.

    Field semantics:

    - ``base_url``: where the agent dispatches its LLM calls. MUST be a
      local host (``localhost`` / ``127.0.0.1`` / ``::1``) or the host of
      the configured ``LEMONADE_BASE_URL``. Cloud-LLM hosts raise
      ``ConfigurationError`` at construction time. AC3 enforcement.
    - ``model_id``: the Lemonade model id to load. Defaults to the agent
      registry's resolved preference at run time.
    - ``max_steps``: bounded planning iteration count for the agent loop.
    - ``streaming``: emit incremental tokens to the console (CLI mode).
    - ``debug``: when True, the agent emits structured verbose logs for
      every triage decision and tool call (Phase A5 contract). Sensitive
      payloads (full prompt, full LLM response) are ONLY emitted when
      this is True — verbose mode is opt-in for benchmarking.
    - ``silent_mode``: suppress all console output (for JSON-only API
      usage).
    - ``output_dir``: where the agent dumps transcripts / artifacts.
    - ``undo_window_seconds``: how long after a soft-delete the user has
      to ``restore_message``. After this window ``restore_message``
      raises with a "use Trash to recover" message.
    - ``db_path``: where ``email_actions`` / ``email_drafts`` live.
      Defaults to ``~/.gaia/email/state.db``. Eval harness passes a
      ``tmp_path``-derived path so concurrent live + eval runs don't
      race on the same SQLite file.
    - ``mail_provider``: a FILTER over the connected mailboxes (#1603 Phase 2).
      ``None`` (the default) means "every connected mailbox" — a both-connected
      user triages Gmail and Outlook together. ``"google"`` / ``"microsoft"``
      restricts to that one provider (and only when it is connected). The
      plural ``resolve_mail_backends`` reads the connected set; the singular
      ``resolve_mail_backend`` stays connector-agnostic (``None`` → Gmail) for
      the eval seam. Case-insensitive.
    - ``calendar_provider``: which calendar provider the agent operates on —
      ``"google"`` (the default) or ``"microsoft"`` (personal Outlook.com
      calendar via MS Graph, #1276). Selects the live backend in
      ``resolve_calendar_backend``. Case-insensitive. When ``None`` (the
      default), tracks ``mail_provider`` so a Microsoft-only user who set
      ``mail_provider="microsoft"`` gets the Outlook calendar too without
      separately configuring it.
    - ``gmail_backend`` / ``outlook_backend`` / ``calendar_backend``: eval
      seam — when set, the agent's tools use the injected backend instead of
      constructing the live one. ``gmail_backend`` is honored for
      ``mail_provider="google"`` and ``outlook_backend`` for
      ``"microsoft"``; ``calendar_backend`` is honored for either calendar
      provider. An injected backend always wins over the live one.
    """

    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = field(default_factory=default_max_steps)
    streaming: bool = False
    debug: bool = False
    silent_mode: bool = False
    show_stats: bool = False
    output_dir: Optional[str] = None
    undo_window_seconds: int = 30
    db_path: Optional[str] = None
    mail_provider: Optional[str] = None
    calendar_provider: Optional[str] = None
    gmail_backend: Optional[Any] = None
    outlook_backend: Optional[Any] = None
    calendar_backend: Optional[Any] = None
    force_llm: bool = False

    def validate(self) -> None:
        """Run startup-time invariants. Called from the agent's __init__.

        Raises ``ConfigurationError`` on any failure — never silently
        downgrades.
        """
        if self.base_url:
            host = urlparse(self.base_url).hostname
            allowed = _allowed_hosts()
            if host is None or host not in allowed:
                raise ConfigurationError(
                    f"EmailAgentConfig.base_url host {host!r} is not in the "
                    f"allowed local-LLM allowlist {sorted(allowed)!r}. The "
                    "email agent processes email bodies LOCALLY only — no "
                    "cloud LLM endpoints are permitted (AC3). To use a "
                    "non-default Lemonade port, set LEMONADE_BASE_URL."
                )

    def resolved_db_path(self) -> str:
        """Return the SQLite path with ``$HOME`` expanded.

        When ``db_path`` is None, defaults to ``~/.gaia/email/state.db``.
        ``Path.home()`` resolution at call time ensures
        ``_autouse_isolate_home`` fixtures are honored in unit tests.
        """
        if self.db_path:
            return self.db_path
        from pathlib import Path

        return str(Path.home() / ".gaia" / "email" / "state.db")

    def resolve_mail_backend(self) -> Any:
        """Return the mailbox backend for the configured ``mail_provider``.

        Resolution order:
          1. An injected backend for the selected provider (eval/test seam) —
             always wins.
          2. The live backend bound to the provider's grant-checked token
             resolver.

        Both live backends satisfy the ``GmailBackend`` Protocol, so the
        agent's tools operate on Gmail and Outlook interchangeably. An unknown
        provider raises ``ConfigurationError`` (fail loudly — never silently
        default to one mailbox).

        Live backend imports are local to keep the module import graph free of
        the ``connectors`` dependency chain at ``config`` import time.
        """
        provider = (self.mail_provider or "google").strip().lower()
        if provider == "google":
            if self.gmail_backend is not None:
                return self.gmail_backend
            from gaia_agent_email.gmail_backend import (
                LiveGmailBackend,
                _get_gmail_token,
            )

            return LiveGmailBackend(_get_gmail_token)
        if provider == "microsoft":
            if self.outlook_backend is not None:
                return self.outlook_backend
            from gaia_agent_email.outlook_backend import (
                LiveOutlookBackend,
                _get_outlook_token,
            )

            return LiveOutlookBackend(_get_outlook_token)
        raise ConfigurationError(
            f"EmailAgentConfig.mail_provider {self.mail_provider!r} is not "
            "supported. Use 'google' (Gmail) or 'microsoft' (Outlook.com / "
            "Hotmail / Live)."
        )

    def _build_mail_backend(self, provider: str) -> Any:
        """Build (or return the injected) live backend for one provider.

        Honors the per-provider eval seam: ``gmail_backend`` for ``google`` and
        ``outlook_backend`` for ``microsoft``. An unknown provider raises
        ``ConfigurationError`` (fail loudly).
        """
        if provider == "google":
            if self.gmail_backend is not None:
                return self.gmail_backend
            from gaia_agent_email.gmail_backend import (
                LiveGmailBackend,
                _get_gmail_token,
            )

            return LiveGmailBackend(_get_gmail_token)
        if provider == "microsoft":
            if self.outlook_backend is not None:
                return self.outlook_backend
            from gaia_agent_email.outlook_backend import (
                LiveOutlookBackend,
                _get_outlook_token,
            )

            return LiveOutlookBackend(_get_outlook_token)
        raise ConfigurationError(
            f"Connected mailbox provider {provider!r} has no backend. "
            "Expected 'google' or 'microsoft'."
        )

    def resolve_mail_backends(self) -> List[Tuple[str, Any]]:
        """Return ``[(provider, backend), ...]`` for every admitted mailbox.

        ``mail_provider`` is a FILTER over the connected set (#1603 Phase 2):

          - ``None`` → every connected mailbox (multi-inbox scan).
          - ``"google"`` / ``"microsoft"`` → only that provider, and only when
            it is actually connected.

        Connector-derived: the connected set comes from
        ``connected_mailbox_providers()`` (the keyring), in registry order
        (google before microsoft). Fails loudly — an explicit filter naming an
        unconnected provider, or nothing connected at all, raises
        ``ConfigurationError`` rather than silently triaging one mailbox.

        The per-provider eval seam (``gmail_backend`` / ``outlook_backend``) is
        honored via ``_build_mail_backend``. An injected backend also marks its
        provider as available, so eval / unit tests that inject a fake do NOT
        need a live keyring connection. Distinct from the singular
        ``resolve_mail_backend``, which stays connector-agnostic for the
        single-backend eval path.
        """
        # Eval seam: when a fake backend is injected, it FULLY defines the
        # available set — the live keyring is not consulted at all, so an
        # injected-fake run stays hermetic regardless of the host's real OAuth
        # connections. Otherwise the available set is the connected mailboxes.
        injected = set()
        if self.gmail_backend is not None:
            injected.add("google")
        if self.outlook_backend is not None:
            injected.add("microsoft")
        available = injected if injected else set(connected_mailbox_providers())
        # Canonical registry order (google before microsoft) — deterministic
        # regardless of keyring vs injection ordering.
        connected = [p for p in ("google", "microsoft") if p in available]
        selected_filter = (self.mail_provider or "").strip().lower()
        if selected_filter:
            selected = [p for p in connected if p == selected_filter]
            if not selected:
                connected_desc = ", ".join(connected) if connected else "none"
                raise ConfigurationError(
                    f"Session selected mailbox {selected_filter!r} but it is "
                    f"not connected. Connected: {connected_desc}. Connect it in "
                    "Settings → Connectors, or clear the selection to use every "
                    "connected mailbox."
                )
        else:
            selected = list(connected)
        if not selected:
            raise ConfigurationError(
                "No mailbox connected — connect Google or Microsoft in "
                "Settings → Connectors before triaging."
            )
        return [(provider, self._build_mail_backend(provider)) for provider in selected]

    def resolve_calendar_backend(self) -> Any:
        """Return the calendar backend for the configured ``calendar_provider``.

        Resolution order (mirrors ``resolve_mail_backend``):
          1. An injected ``calendar_backend`` (eval/test seam) — always wins.
          2. The live backend bound to the provider's grant-checked token
             resolver.

        ``calendar_provider`` defaults to ``mail_provider`` when unset, so a
        Microsoft-only user is not forced to set it twice. Both live backends
        satisfy the ``CalendarBackend`` Protocol, so the agent's calendar tools
        operate on Google and Outlook calendars interchangeably. An unknown
        provider raises ``ConfigurationError`` (fail loudly — never silently
        default to one calendar).

        Live backend imports are local to keep the module import graph free of
        the ``connectors`` dependency chain at ``config`` import time.
        """
        if self.calendar_backend is not None:
            return self.calendar_backend
        # Default to the mail provider when calendar_provider is unset.
        provider = (
            (self.calendar_provider or self.mail_provider or "google").strip().lower()
        )
        if provider == "google":
            from gaia_agent_email.calendar_backend import (
                LiveCalendarBackend,
                _get_calendar_token,
            )

            return LiveCalendarBackend(_get_calendar_token)
        if provider == "microsoft":
            from gaia_agent_email.outlook_calendar_backend import (
                LiveOutlookCalendarBackend,
                _get_outlook_calendar_token,
            )

            return LiveOutlookCalendarBackend(_get_outlook_calendar_token)
        raise ConfigurationError(
            f"EmailAgentConfig.calendar_provider {self.calendar_provider!r} is "
            "not supported. Use 'google' or 'microsoft' (Outlook.com / Hotmail "
            "/ Live)."
        )


__all__ = ["ConfigurationError", "EmailAgentConfig"]
