# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``rss-digest`` starter skill.

Fetches through :class:`gaia.web.client.WebClient` rather than ``requests``
directly, so the skill inherits GAIA's SSRF guards (private/loopback addresses
refused, response size capped) instead of re-implementing them.
"""

from __future__ import annotations

from typing import Any, Dict, List
from xml.etree import ElementTree

from gaia.agents.base.tools import tool

# Atom uses a namespace; RSS 2.0 does not. Strip it so one parser handles both.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(element: Any, *names: str) -> str:
    """Return the first non-empty child text among ``names``."""
    for name in names:
        for candidate in (name, f"{_ATOM_NS}{name}"):
            child = element.find(candidate)
            if child is not None:
                if child.text and child.text.strip():
                    return child.text.strip()
                # Atom <link href="..."/> carries the value as an attribute.
                href = child.get("href")
                if href:
                    return href.strip()
    return ""


def _parse_entries(root: Any, max_entries: int) -> List[Dict[str, str]]:
    """Extract RSS ``<item>`` or Atom ``<entry>`` elements, newest first."""
    items = root.iter("item")
    entries = [
        {
            "title": _text(item, "title"),
            "link": _text(item, "link"),
            "published": _text(item, "pubDate", "published", "updated"),
            "summary": _text(item, "description", "summary", "content"),
        }
        for item in items
    ]
    if not entries:
        entries = [
            {
                "title": _text(entry, "title"),
                "link": _text(entry, "link"),
                "published": _text(entry, "published", "updated"),
                "summary": _text(entry, "summary", "content"),
            }
            for entry in root.iter(f"{_ATOM_NS}entry")
        ]
    return entries[:max_entries]


@tool
def fetch_rss(url: str, max_entries: int = 10) -> dict:
    """Fetch an RSS or Atom feed and return its newest entries as structured data.

    Args:
        url: The feed URL. Must be a public http(s) address.
        max_entries: Maximum entries to return, newest first.

    Returns:
        ``{"feed_title", "entries", "count"}`` on success, or ``{"error"}``
        describing what went wrong. Never a partial or invented feed.
    """
    if max_entries < 1:
        return {"error": f"max_entries must be at least 1, got {max_entries}."}

    from gaia.web.client import WebClient

    try:
        response = WebClient().get(url)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        return {
            "error": f"Could not fetch {url}: {type(exc).__name__}: {exc}. "
            "Check the URL is a reachable public feed."
        }

    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        return {
            "error": f"{url} did not parse as RSS/Atom XML: {exc}. "
            "The URL may point at an HTML page rather than a feed."
        }

    channel = root.find("channel")
    title_source = channel if channel is not None else root
    entries = _parse_entries(root, max_entries)

    return {
        "feed_title": _text(title_source, "title") or url,
        "entries": entries,
        "count": len(entries),
    }
