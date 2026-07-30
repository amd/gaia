# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``rss-digest`` starter skill.

Fetches through :class:`gaia.web.client.WebClient` rather than ``requests``
directly, so the skill inherits GAIA's SSRF guards (private/loopback addresses
refused, response size capped) instead of re-implementing them.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List
from xml.etree import ElementTree

from gaia.agents.base.tools import tool

# Atom uses a namespace; RSS 2.0 does not. Strip it so one parser handles both.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
# The root element is the first tag opening with a letter; `<?xml`, `<!--`, and
# `<!DOCTYPE` all sort before it. Everything earlier is the prolog.
_ROOT_START_RE = re.compile(rb"<[A-Za-z]")


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
    """Extract RSS ``<item>`` or Atom ``<entry>`` elements, in feed order."""
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
    """Fetch an RSS or Atom feed and return its entries as structured data.

    Args:
        url: The feed URL. Must be a public http(s) address.
        max_entries: Maximum entries to return, in feed order.

    Returns:
        ``{"feed_title", "entries", "count"}`` on success, or ``{"error"}``
        describing what went wrong. Never a partial or invented feed.
    """
    if max_entries < 1:
        return {"error": f"max_entries must be at least 1, got {max_entries}."}

    from gaia.web.client import WebClient

    client = WebClient()
    try:
        response = client.get(url)
        response.raise_for_status()
        payload = response.content
    except Exception as exc:  # noqa: BLE001 - reported to the model, not swallowed
        return {
            "error": f"Could not fetch {url}: {type(exc).__name__}: {exc}. "
            "Check the URL is a reachable public feed."
        }
    finally:
        client.close()

    return parse_feed(payload, source=url, max_entries=max_entries)


def parse_feed(payload: bytes, *, source: str, max_entries: int) -> dict:
    """Parse feed bytes into the ``fetch_rss`` result shape.

    Split out from the tool so the parsing rules are testable without network.
    """
    # Entity expansion is a DoS vector and stdlib ElementTree performs it. A
    # real RSS/Atom feed never needs a DTD, so refuse one outright. Only the
    # prolog is scanned: "<!DOCTYPE" inside an entry's embedded HTML is content.
    root_start = _ROOT_START_RE.search(payload)
    prolog = payload[: root_start.start()] if root_start else payload
    if _DOCTYPE_RE.search(prolog):
        return {
            "error": f"{source} declares a DTD (<!DOCTYPE>). Feeds do not need one "
            "and entity expansion is a denial-of-service vector, so it was refused."
        }

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        return {
            "error": f"{source} did not parse as RSS/Atom XML: {exc}. "
            "The URL may point at an HTML page rather than a feed."
        }

    entries = _parse_entries(root, max_entries)
    if not entries:
        return {
            "error": f"{source} parsed as XML but contains no RSS <item> or Atom "
            "<entry> elements. It may be an unsupported dialect (RSS 1.0/RDF) or "
            "an empty feed — reported rather than returned as an empty digest."
        }

    channel = root.find("channel")
    title_source = channel if channel is not None else root

    return {
        "feed_title": _text(title_source, "title") or source,
        "entries": entries,
        "count": len(entries),
    }
