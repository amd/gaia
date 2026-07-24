# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``gaia email`` CLI entry point.

One-shot mode (``--query``) prints the agent's response and exits.
Interactive mode (``--interactive``) reads queries from stdin in a loop
until EOF / Ctrl-C / ``/quit``.

Verbose / debug flags are wired into ``EmailAgentConfig.debug`` so the
structured-logging contract from Phase A5 fires for every triage decision.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from gaia_agent_email.agent import EmailTriageAgent
from gaia_agent_email.config import EmailAgentConfig
from gaia.logger import get_logger

log = get_logger(__name__)


async def main(args: Any) -> int:
    """Async main — invoked by ``gaia.cli.handle_email_command``.

    Returns the process exit code (0 on success, 1 on error).

    Note: ``--spec`` is handled upstream in ``gaia.cli.handle_email_command``
    (it short-circuits before this coroutine is reached), so there is no
    spec branch here — see ``spec_html.write_and_open_spec``.
    """
    # Wire verbose/debug to the agent's logger before constructing.
    if getattr(args, "verbose", False) or getattr(args, "debug", False):
        logging.getLogger("gaia_agent_email").setLevel(logging.INFO)
    if getattr(args, "debug", False):
        logging.getLogger("gaia_agent_email").setLevel(logging.DEBUG)

    config = EmailAgentConfig(
        debug=bool(getattr(args, "debug", False) or getattr(args, "verbose", False)),
        streaming=False,
        silent_mode=False,
        show_stats=bool(getattr(args, "show_stats", False)),
    )
    try:
        agent = EmailTriageAgent(config=config)
    except Exception as exc:
        log.exception("email-agent failed to start")
        print(f"❌ Email agent could not start: {exc}", file=sys.stderr)
        return 1

    try:
        if getattr(args, "query", None):
            return await _one_shot(agent, args.query, args)
        if getattr(args, "interactive", False):
            return await _interactive(agent, args)
        # No query and not interactive — print a helpful usage hint.
        print(
            "Usage: gaia email -q '<your question>' OR gaia email -i\n"
            "Examples:\n"
            "  gaia email -q 'Triage my inbox'\n"
            "  gaia email -q 'Summarize my unread emails from this week'\n"
            "  gaia email -i\n"
        )
        return 0
    finally:
        try:
            agent.close_db()
        except Exception as exc:
            log.warning("close_db failed during shutdown: %s", exc)


def _extract_answer(result: Any) -> str:
    """Extract the human-readable answer from an agent result dict."""
    if isinstance(result, dict):
        return result.get("answer") or result.get("response") or str(result)
    return str(result)


async def _one_shot(agent: EmailTriageAgent, query: str, args: Any = None) -> int:
    """Run a single query and print the result."""
    try:
        result = agent.process_query(
            query, trace=getattr(args, "trace", False) if args else False
        )
        print(_extract_answer(result))
        return 0
    except Exception as exc:
        log.exception("email-agent one-shot failed")
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1


async def _interactive(agent: EmailTriageAgent, args: Any = None) -> int:
    """REPL loop — reads queries from stdin until EOF or ``/quit``."""
    print("Email Triage Agent — interactive. Enter a query, or '/quit' to exit.\n")
    while True:
        try:
            query = input("email> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not query:
            continue
        if query in ("/quit", "/exit", "/q"):
            return 0
        try:
            result = agent.process_query(
                query, trace=getattr(args, "trace", False) if args else False
            )
            print(_extract_answer(result))
        except Exception as exc:
            log.exception("email-agent interactive query failed")
            print(f"❌ Error: {exc}", file=sys.stderr)
