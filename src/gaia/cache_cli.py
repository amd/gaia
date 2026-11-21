# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI for ``gaia cache {status|clear}``: GAIA's caches, their sizes, and the Context7 cache."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from gaia.logger import get_logger


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia cache`` and its actions."""
    cache_parser = subparsers.add_parser(
        "cache",
        help="Show GAIA's caches and their sizes, or clear the Context7 cache",
    )
    cache_subparsers = cache_parser.add_subparsers(
        dest="cache_action", help="Cache action to perform"
    )

    # Cache status command
    _ = cache_subparsers.add_parser("status", help="Show cache and rate limiter status")

    # Cache clear command
    cache_clear_parser = cache_subparsers.add_parser("clear", help="Clear cached data")
    cache_clear_parser.add_argument(
        "--context7", action="store_true", help="Clear Context7 cache"
    )
    cache_clear_parser.add_argument(
        "--all", action="store_true", help="Clear all caches"
    )


def _is_link(path) -> bool:
    """True for a symlink or a Windows junction, which ``is_symlink`` misses."""
    path = str(path)
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    if isjunction is not None:
        return isjunction(path)
    import stat as stat_mod

    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
    except OSError:  # gone; the size walk reports it as unreadable
        return False
    # Only junctions: OneDrive placeholders are reparse points too, and hold real data.
    return tag == getattr(stat_mod, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _cache_folder_usage(folder: Path) -> tuple:
    """Bytes, file count and unreadable entries under *folder*, without following links."""
    size = files = 0
    # Files and folders that couldn't be read, so the size never quietly comes up short.
    unreadable: list = []
    for dirpath, dirnames, filenames in os.walk(folder, onerror=unreadable.append):
        dirnames[:] = [d for d in dirnames if not _is_link(os.path.join(dirpath, d))]
        for name in filenames:
            try:
                stat = os.lstat(os.path.join(dirpath, name))
            except OSError as e:
                unreadable.append(e)
                continue
            size += stat.st_size
            files += 1
    return size, files, len(unreadable)


def _print_cache_folders() -> None:
    """Print each folder under GAIA's cache root, largest first, with its size."""
    from gaia.home import gaia_home
    from gaia.security import _format_size

    root = gaia_home() / "cache"
    print(f"\n=== GAIA caches ({root}) ===")
    if not root.is_dir():
        print("No caches yet.")
        return
    rows = []
    for entry in root.iterdir():
        if _is_link(entry):
            continue
        if entry.is_dir():
            rows.append((entry.name, *_cache_folder_usage(entry)))
        elif entry.is_file():
            try:
                rows.append((entry.name, entry.lstat().st_size, 1, 0))
            except OSError:  # removed or locked while listing
                rows.append((entry.name, 0, 0, 1))
    if not rows:
        print("No caches yet.")
        return
    rows.sort(key=lambda row: (-row[1], row[0]))
    width = max(len(row[0]) for row in rows + [("Total",)])

    def count(files: int) -> str:
        return f"{files:>7,} {'file' if files == 1 else 'files'}"

    for name, size, files, unreadable in rows:
        note = f"  ({unreadable} unreadable)" if unreadable else ""
        print(f"  {name:<{width}}  {_format_size(size):>10}  {count(files)}{note}")
    total_size = sum(row[1] for row in rows)
    total_files = sum(row[2] for row in rows)
    print(f"  {'Total':<{width}}  {_format_size(total_size):>10}  {count(total_files)}")


def handle(args):
    """Handle the cache management command.

    Args:
        args: Parsed command-line arguments
    """
    if not hasattr(args, "cache_action") or args.cache_action is None:
        print("❌ Error: No cache action specified")
        print("Available actions: status, clear")
        print("Run 'gaia cache --help' for more information")
        sys.exit(1)

    from gaia.mcp.context7_cache import Context7Cache, Context7RateLimiter
    from gaia.mcp.external_services import Context7Service

    try:
        if args.cache_action == "status":
            _print_cache_folders()

            is_available = Context7Service.check_availability()

            print("\n=== Context7 Service Status ===")
            if is_available:
                print("✓ Context7 is AVAILABLE (npx found, service working)")
            else:
                print(
                    "- Context7 (optional) is not available: npx not found or "
                    "the service failed"
                )
                print(
                    "  Library documentation search is off; nothing else is affected."
                )

            # Show cache and rate limiter status
            cache = Context7Cache()
            rate_limiter = Context7RateLimiter()

            status = rate_limiter.get_status()

            print("\n=== Context7 Cache Status ===")
            print(f"Cache directory: {cache.cache_dir}")
            # Use glob to count library ID files since _load_json is protected
            library_id_count = (
                len(list(cache.cache_dir.glob("library_*.json")))
                if hasattr(cache, "cache_dir")
                else 0
            )
            print(f"Library IDs cached: {library_id_count}")

            # Count documentation files
            doc_count = len(list(cache.docs_dir.glob("*.json")))
            print(f"Documentation entries: {doc_count}")

            print("\n=== Rate Limiter Status ===")
            print(
                f"Tokens available: {status['tokens_available']}/{status['max_tokens']}"
            )
            print(f"Circuit breaker: {'OPEN' if status['circuit_open'] else 'CLOSED'}")
            print(f"Consecutive failures: {status['consecutive_failures']}")

            if status["tokens_available"] < 5:
                print("\n⚠️  Warning: Low token count. Rate limiting may occur soon.")

            if not is_available:
                print("\n💡 To enable Context7:")
                print("   1. Install Node.js and npm")
                print("   2. Ensure 'npx' is in your PATH")
                print(
                    "   3. Optionally add CONTEXT7_API_KEY to .env for higher rate limits"
                )

        elif args.cache_action == "clear":
            # Clear caches
            if args.all or args.context7:
                cache = Context7Cache()
                cache.clear()
                print("✓ Context7 cache cleared")
            else:
                # Same refusal-must-not-report-success rule as `gaia kill`.
                print("❌ Specify --context7 or --all to clear caches")
                print("Run 'gaia cache clear --help' for more information")
                sys.exit(1)

    except Exception as e:
        cache_log = get_logger(__name__)
        cache_log.error(f"Error managing cache: {e}")
        print(f"❌ Error: {e}")
        sys.exit(1)
