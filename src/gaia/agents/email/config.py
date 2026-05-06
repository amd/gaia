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
        try:
            host = urlparse(env).hostname
            if host:
                out.add(host)
        except Exception:
            pass
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
    - ``gmail_backend`` / ``calendar_backend``: eval seam — when set,
      the agent's tools use these instead of constructing
      ``LiveGmailBackend(_get_gmail_token)``.
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
    gmail_backend: Optional[Any] = None
    calendar_backend: Optional[Any] = None

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


__all__ = ["ConfigurationError", "EmailAgentConfig"]
