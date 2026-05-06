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

import argparse
import logging
import sys
from typing import Any

from gaia.agents.email.agent import EmailTriageAgent
from gaia.agents.email.config import EmailAgentConfig
from gaia.logger import get_logger

log = get_logger(__name__)


async def main(args: Any) -> int:
    """Async main — invoked by ``gaia.cli.handle_email_command``.

    Returns the process exit code (0 on success, 1 on error).
    """
    # Wire verbose/debug to the agent's logger before constructing.
    if getattr(args, "verbose", False) or getattr(args, "debug", False):
        logging.getLogger("gaia.agents.email").setLevel(logging.INFO)
    if getattr(args, "debug", False):
        logging.getLogger("gaia.agents.email").setLevel(logging.DEBUG)

    config = EmailAgentConfig(
        debug=bool(getattr(args, "debug", False) or getattr(args, "verbose", False)),
        streaming=False,
        silent_mode=False,
    )
    try:
        agent = EmailTriageAgent(config=config)
    except Exception as exc:
        print(f"❌ Email agent could not start: {exc}", file=sys.stderr)
        return 1

    try:
        if getattr(args, "query", None):
            return await _one_shot(agent, args.query)
        if getattr(args, "interactive", False):
            return await _interactive(agent)
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
        except Exception:
            pass


async def _one_shot(agent: EmailTriageAgent, query: str) -> int:
    """Run a single query and print the result."""
    try:
        # process_query is async on the base Agent class.
        result = await agent.process_query(query)
        if isinstance(result, dict):
            answer = result.get("answer") or result.get("response") or str(result)
        else:
            answer = str(result)
        print(answer)
        return 0
    except Exception as exc:
        log.exception("email-agent one-shot failed")
        print(f"❌ Error: {exc}", file=sys.stderr)
        return 1


async def _interactive(agent: EmailTriageAgent) -> int:
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
            result = await agent.process_query(query)
            if isinstance(result, dict):
                answer = result.get("answer") or result.get("response") or str(result)
            else:
                answer = str(result)
            print(answer)
        except Exception as exc:
            log.exception("email-agent interactive query failed")
            print(f"❌ Error: {exc}", file=sys.stderr)


# Standalone entry point — also wired in setup.py if desired in a follow-up.
def cli() -> int:  # pragma: no cover — wrapper around argparse
    parser = argparse.ArgumentParser(prog="gaia email")
    parser.add_argument("-q", "--query", default=None)
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    import asyncio

    return asyncio.run(main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
