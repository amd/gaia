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
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse


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
    - ``mail_provider``: which mailbox provider the agent operates on —
      ``"google"`` (Gmail, the default) or ``"microsoft"`` (personal
      Outlook.com / Hotmail / Live via MS Graph, #1275). Selects the live
      backend in ``resolve_mail_backend``. Case-insensitive.
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
    max_steps: int = 12
    streaming: bool = False
    debug: bool = False
    silent_mode: bool = False
    show_stats: bool = False
    output_dir: Optional[str] = None
    undo_window_seconds: int = 30
    db_path: Optional[str] = None
    mail_provider: str = "google"
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
