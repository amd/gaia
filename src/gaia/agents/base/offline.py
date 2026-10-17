# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Internet off for one chat: the tool calls that would reach the internet.

A host sets ``Agent.internet_off``; every tool call is checked here before it
can ask for confirmation, so a toggle takes effect on the agent's next step.
Refused: the web tools, the in-app browser, connector (MCP) tools — GAIA can't
tell which of those stay on this PC — and any call whose arguments or shell
command reach a host other than this PC.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from gaia.agents.base.denied_effects import NETWORK, effects_of_call
from gaia.agents.base.verification import NOT_EXECUTED

#: Tools whose whole job is fetching from the internet.
WEB_TOOLS = frozenset(
    {
        "search_web",
        "fetch_page",
        "fetch_webpage",
        "download_file",
        "search_documentation",
    }
)

#: Commands that go online with no URL in sight: a remote, a package index, an API.
_ONLINE_COMMAND_RE = re.compile(
    r"(?:^|[;&|(]|\b(?:then|do)\s)\s*(?:sudo\s+)?"
    r"(?:git(?:\s+-{1,2}[\w-]+(?:[ =](?!-)\S+)?)*\s+"
    r"(?:clone|fetch|pull|push|ls-remote|submodule\s+update)\b"
    r"|(?:pip3?|uv\s+pip|python3?\s+-m\s+pip)\s+(?:install|download)\b"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|update|publish)\b"
    r"|(?:npx|uvx|pipx|bunx)\s"
    r"|uv\s+(?:add|sync|tool\s+install)\b|cargo\s+(?:install|fetch|update)\b"
    r"|go\s+(?:get|install|mod\s+download)\b|docker\s+(?:pull|push|login)\b"
    r"|(?:winget|choco|scoop|apt|apt-get|brew|dnf|yum)\s+(?:install|upgrade|update)\b"
    r"|gh\s|curl\b|wget\b|ssh\b|scp\b|iwr\b|irm\b|invoke-(?:webrequest|restmethod)\b)",
    re.IGNORECASE,
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

OFFLINE_MESSAGE = (
    "Internet is off for this chat, so {what} was not run. Answer from what is "
    "on this PC, and tell the user they can turn the internet back on under "
    "Environment in the session panel."
)


def _is_loopback(host: str) -> bool:
    host = host.lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _url_is_local(url: str) -> bool:
    host = urlparse(url.strip()).hostname
    return bool(host) and _is_loopback(host)


def _command_goes_online(command: str) -> bool:
    # A URL naming only this PC is fine; a bare `curl` to one is too.
    if _ONLINE_COMMAND_RE.search(command) is None:
        return False
    urls = re.findall(r"https?://([A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\])", command)
    return not urls or not all(_is_loopback(u.strip("[]")) for u in urls)


def offline_refusal(
    tool_name: str,
    tool_args: Optional[Mapping[str, Any]],
    registry_entry: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The refusal for a call that would reach the internet, or None.

    Args:
        tool_name: The tool being called.
        tool_args: Its arguments.
        registry_entry: The tool's registry entry; an MCP tool carries
            ``_mcp_server``.

    Returns:
        A not-executed tool error, or None when the call stays on this PC.
    """
    name = tool_name or ""
    args = dict(tool_args or {})
    what = None
    url = args.get("url")
    if name != "search_web" and isinstance(url, str) and _url_is_local(url):
        return None
    if name in WEB_TOOLS:
        what = f"the web tool {name}"
    elif name.startswith("browser_"):
        what = "the browser"
    elif registry_entry is not None and registry_entry.get("_mcp_server"):
        what = f"the {registry_entry['_mcp_server']} connector"
    else:
        hosts = sorted(
            e.target
            for e in effects_of_call(name, args)
            if e.kind == NETWORK and not _is_loopback(e.target)
        )
        command = args.get("command") or args.get("cmd")
        if hosts:
            what = f"{name} (it would contact {', '.join(hosts[:3])})"
        elif isinstance(command, str) and _command_goes_online(command):
            what = f"the command `{command.strip()[:80]}`, which goes online"
    if what is None:
        return None
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error_type": "internet_off",
        "error": OFFLINE_MESSAGE.format(what=what),
    }
