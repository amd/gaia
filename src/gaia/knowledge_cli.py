# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI for ``gaia knowledge {search|extract|usage}``: web research via Tavily."""

from __future__ import annotations

import argparse
import json
import sys


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia knowledge`` and its actions."""
    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Web research via Tavily (search|extract|usage), with caching and a credit budget",
    )
    knowledge_subparsers = knowledge_parser.add_subparsers(
        dest="knowledge_action", help="knowledge action to perform"
    )

    k_search = knowledge_subparsers.add_parser("search", help="Run a web search")
    k_search.add_argument("query", help="Search query")
    k_search.add_argument(
        "--max-results", type=int, default=5, help="Max results (default: 5)"
    )
    k_search.add_argument(
        "--depth",
        choices=("basic", "advanced"),
        default="basic",
        help="Search depth — advanced costs more credits (default: basic)",
    )
    k_search.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Credit cap for this session; omit for unlimited",
    )
    k_search.add_argument(
        "--no-block",
        action="store_true",
        help="Warn instead of blocking when the budget cap is exceeded",
    )

    k_extract = knowledge_subparsers.add_parser(
        "extract", help="Extract clean content from one or more URLs (requires Tavily)"
    )
    k_extract.add_argument("urls", nargs="+", help="One or more URLs to extract")
    k_extract.add_argument(
        "--depth",
        choices=("basic", "advanced"),
        default="basic",
        help="Extract depth (default: basic)",
    )
    k_extract.add_argument(
        "--budget", type=int, default=None, help="Credit cap for this session"
    )
    k_extract.add_argument(
        "--no-block",
        action="store_true",
        help="Warn instead of blocking when the budget cap is exceeded",
    )

    knowledge_subparsers.add_parser("usage", help="Show cached credit-usage totals")
    knowledge_parser.set_defaults(action="knowledge")


def _print_usage(client, show_session=True):
    """Print a one-line credit-usage summary for a Tavily client."""
    usage = client.usage()
    cap = usage["cap"]
    cap_str = "unlimited" if cap is None else str(cap)
    # A bare `usage` run spends nothing, so its session figure would always be 0.
    session = f", {usage['session_credits']} this session" if show_session else ""
    print(
        f"\n💳 Credits used: {usage['total_credits']} total{session} (cap: {cap_str})"
    )


def handle(args):
    """Handle `gaia knowledge` — Tavily web research with caching + budget.

    Args:
        args: Parsed command-line arguments
    """
    action = getattr(args, "knowledge_action", None)
    if action is None:
        print("❌ Error: No knowledge action specified")
        print("Available actions: search, extract, usage")
        print("Run 'gaia knowledge --help' for more information")
        return

    import requests

    from gaia.web.client import SearchStatusError, SearchUnavailableError
    from gaia.web.tavily import (
        BudgetConfig,
        TavilyBudgetExceeded,
        TavilyClient,
        TavilyConfigError,
    )

    budget = BudgetConfig(
        cap=getattr(args, "budget", None),
        block=not getattr(args, "no_block", False),
    )
    client = TavilyClient(budget=budget)
    try:
        if action == "search":
            result = client.search(
                args.query, search_depth=args.depth, max_results=args.max_results
            )
            source = result.get("source", "tavily")
            print(f"\n=== Results for {args.query!r} (source: {source}) ===")
            for i, r in enumerate(result.get("results", []), 1):
                print(f"{i}. {r.get('title', '')}")
                print(f"   {r.get('url', '')}")
                content = r.get("content") or r.get("snippet") or ""
                if content:
                    print(f"   {content[:200]}")
            _print_usage(client)
        elif action == "extract":
            result = client.extract(args.urls, extract_depth=args.depth)
            print(json.dumps(result, indent=2))
            _print_usage(client)
        elif action == "usage":
            _print_usage(client, show_session=False)
    except TavilyBudgetExceeded as e:
        print(f"🛑 {e}")
        sys.exit(1)
    except (TavilyConfigError, SearchUnavailableError, SearchStatusError) as e:
        print(f"❌ {e}")
        sys.exit(1)
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,  # dropped mid-response
    ) as e:
        service = "Tavily" if client.configured else "DuckDuckGo"
        print(
            f"❌ Web {action} failed: {service} could not be reached ({e}). "
            "Check your internet connection or proxy settings and try again."
        )
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        # The service answered with an error, so the user's network is fine.
        service = "Tavily" if client.configured else "DuckDuckGo"
        status = getattr(e.response, "status_code", None) or "an error"
        hint = (
            "It rejected the request; check the query or URL and try again."
            if isinstance(status, int) and status < 500
            else "It may be down for a moment; try again later."
        )
        print(f"❌ Web {action} failed: {service} answered with {status}. {hint}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        # Redirect loops, a malformed URL and the like: still an error, not a crash.
        service = "Tavily" if client.configured else "DuckDuckGo"
        print(
            f"❌ Web {action} failed: the {service} request could not complete ({e})."
        )
        sys.exit(1)
    finally:
        client.close()
