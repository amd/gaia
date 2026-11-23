# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Internet off for one chat: the tool calls that would reach the internet.

A host sets ``Agent.internet_off``; every tool call is checked here before it
can ask for confirmation, so a toggle takes effect on the agent's next step.
Refused: the web tools, the in-app browser, connector (MCP) tools — GAIA can't
tell which of those stay on this PC — the mailbox, calendar and Agent Hub tools, and any
call whose arguments or shell command reach a host other than this PC.

"This PC" means ``localhost`` exactly or a loopback IP literal (127.0.0.0/8,
``::1``). Every part of a shell command line is checked on its own, and a
command is only let through for a local address when it is a plain fetch
(curl, wget, Invoke-WebRequest) whose every destination is one.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

from gaia.agents.base.command_wrappers import OFFLINE_WRAPPERS as _WRAPPERS
from gaia.agents.base.denied_effects import (
    _ASSIGNMENT_RE,
    _SHELL_COMMAND_FLAGS,
    _SHELLS,
    NETWORK,
    _program,
    _split_segments,
    _tokens,
    effects_of_call,
)
from gaia.agents.base.tool_grants import pane_tool_name
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

#: Tools that always call an online service: mailbox, calendar or the Agent Hub.
ONLINE_SERVICE_TOOLS = frozenset(
    {
        "calendar/cancel_calendar_event",
        "calendar/check_calendar_availability",
        "calendar/create_calendar_event",
        "calendar/list_calendar_events",
        "calendar/update_calendar_event",
        "check_mailbox_access",
        "get_thread",
        "list_inbox",
        "list_mail_folders",
        "list_waiting_on_you",
        "read_email",
        "search_email",
        "send-email/create_draft",
        "send-email/discard_draft",
        "send-email/forward_message",
        "send-email/list_drafts",
        "send-email/reply_to_message",
        "send-email/send_draft",
        "send-email/send_now",
        "send-email/update_draft",
        "search_skill_hub",
        "install_skill",
    }
)

#: Commands that go online with no URL in sight: a remote, a package index, an API.
#: Kept in step with the ``online`` pattern in cpp/src/internet_refusal.cpp. Build tools
#: that resolve dependencies from a remote repository on nearly every run (mvn,
#: gradle) count whatever their arguments, unless ``_explicitly_offline`` lets one through.
_ONLINE_COMMAND_RE = re.compile(
    r"(?:^|[;&|(`]|\b(?:then|do)\s)\s*(?:sudo\s+)?"
    r"(?:git(?:\s+-{1,2}[\w-]+(?:[ =](?!-)\S+)?)*\s+"
    r"(?:clone|fetch|pull|push|ls-remote|submodule\s+update|remote\s+update"
    r"|lfs\s+(?:fetch|pull|push))\b"
    r"|(?:pip[0-9.]*|uv\s+pip|(?:python[0-9.]*|py)\s+-m\s+pip)\s+"
    r"(?:install|download|wheel|index|search)\b"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|ci|update|upgrade|up|publish|dlx|exec"
    r"|create|x|audit|outdated|view|info|search|login)\b"
    r"|(?:npm|pnpm|yarn|bun)\s+init\s+[^-\s]"
    r"|(?:npx|uvx|pipx|bunx|pnpx)\s"
    r"|uv\s+(?:add|sync|lock|tool\s+(?:install|upgrade)|python\s+install|run\s+--with"
    r"|self\s+update)\b"
    r"|(?:poetry|pdm)\s+(?:install|add|update|lock|sync|publish|self\s+update)\b"
    r"|(?:conda|mamba|micromamba)\s+(?:install|create|update|upgrade|search"
    r"|env\s+(?:create|update))\b"
    r"|cargo\s+(?:install|fetch|update|publish|search|add|login)\b"
    r"|go\s+(?:get|install|mod\s+(?:download|tidy))\b"
    r"|gem\s+(?:install|update|fetch|push|search|sources)\b"
    r"|bundler?\s+(?:install|update|add|outdated|lock)\b"
    r"|composer\s+(?:install|update|require|create-project)\b"
    r"|dotnet\s+(?:restore|add|new\s+install|tool\s+(?:install|update|restore)"
    r"|workload|nuget)\b"
    r"|nuget\s+(?:install|restore|update|push)\b"
    r"|(?:docker|podman)\s+(?:pull|push|login|search)\b"
    r"|(?:winget|choco|scoop|apt|apt-get|brew|dnf|yum|zypper|apk|snap|flatpak)\s+"
    r"(?:install|reinstall|upgrade|update|download|search|add|fetch|tap|source"
    r"|dist-upgrade|full-upgrade)\b"
    r"|pacman\s+-S|(?:install|update|find|save)-(?:module|package|script)\b"
    r"|start-bitstransfer\b|bitsadmin\b|certutil\b.*-urlcache|aria2c\b"
    r"|(?:huggingface-cli|hf)\s+(?:download|upload|login)\b|ollama\s+(?:pull|push)\b"
    r"|(?:mvn|mvnw|gradle|gradlew)(?:\s|$)"
    r"|gh\s|curl\b|wget\b|ssh\b|scp\b|iwr\b|irm\b|invoke-(?:webrequest|restmethod)\b)",
    re.IGNORECASE,
)

#: The quote, backslash and caret splices a shell drops from a word before running it.
_SPLICE_RE = re.compile(r"['\"\\^]")
_QUOTE_CARET_RE = re.compile(r"['\"^]")
#: What an explicitly offline command may not hold: quotes, expansions, a URL, a socket path.
_OFFLINE_UNSAFE_RE = re.compile(r"['\"$%!`]|://|/dev/(?:tcp|udp)/")
#: Build goals and tasks that publish, which an offline flag does not keep local.
_PUBLISHING_RE = re.compile(r"deploy|publish|upload|release|scm:|wagon:", re.IGNORECASE)

_LOCALHOST = "localhost"

#: A URL in free text: a scheme, then everything up to whitespace or a quote.
_TEXT_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"<>`()]+")

#: Programs that fetch only what their arguments name, so a call naming only
#: this PC stays on it. Every other online command is refused outright.
_FETCH_PROGRAMS = frozenset(
    {"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"}
)

#: Prefixes that run the rest of the line, beyond the shared ``_WRAPPERS``.
#: A prefix's numeric arguments (``timeout 5``, ``nice -n 10``) are skipped.
_TIMING_WRAPPERS = frozenset(
    {"timeout", "nice", "ionice", "stdbuf", "taskset", "chrt", "watch", "nohup"}
)
#: Run a command over arguments GAIA can't see (stdin, a file listing).
_ARGUMENT_FEEDERS = frozenset({"xargs", "parallel"})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})

#: Options whose value is not a destination (an output file, a header, a method).
_VALUE_OPTIONS = {
    "curl": (
        frozenset("AbcCdDeEFHmoPQrtTuwXyYz"),
        frozenset(
            {
                "--output",
                "--output-dir",
                "--data",
                "--data-raw",
                "--data-binary",
                "--data-urlencode",
                "--data-ascii",
                "--json",
                "--form",
                "--header",
                "--user-agent",
                "--user",
                "--request",
                "--write-out",
                "--max-time",
                "--connect-timeout",
                "--cookie",
                "--cookie-jar",
                "--continue-at",
                "--referer",
                "--range",
                "--retry",
                "--retry-delay",
                "--retry-max-time",
                "--upload-file",
                "--dump-header",
                "--cert",
                "--key",
                "--cacert",
                "--limit-rate",
                "--oauth2-bearer",
                "--unix-socket",
            }
        ),
    ),
    "wget": (
        frozenset("OoaPtTUwQlAR"),
        frozenset(
            {
                "--output-document",
                "--output-file",
                "--append-output",
                "--directory-prefix",
                "--tries",
                "--timeout",
                "--wait",
                "--header",
                "--user-agent",
                "--post-data",
                "--post-file",
                "--method",
                "--body-data",
                "--body-file",
                "--user",
                "--password",
                "--level",
            }
        ),
    ),
    "ps": (
        frozenset(),
        frozenset(
            {
                "-outfile",
                "-method",
                "-body",
                "-contenttype",
                "-timeoutsec",
                "-useragent",
                "-headers",
                "-infile",
                "-maximumredirection",
                "-sessionvariable",
            }
        ),
    ),
}
#: Options that send the request somewhere other than its URL, or read more
#: URLs from elsewhere: a proxy, a resolver override, a config or input file.
_REDIRECTING_OPTIONS = {
    "curl": (
        frozenset("xKU"),
        frozenset(
            {
                "--config",
                "--resolve",
                "--connect-to",
                "--doh-url",
                "--preproxy",
                "--socks4",
                "--socks4a",
                "--socks5",
                "--socks5-hostname",
                "--haproxy-protocol",
            }
        ),
    ),
    "wget": (
        frozenset("eiBF"),
        frozenset({"--execute", "--input-file", "--base", "--config", "--force-html"}),
    ),
    "ps": (frozenset(), frozenset()),
}

OFFLINE_MESSAGE = (
    "Internet is off for this chat, so {what} was not run. Answer from what is "
    "on this PC, and tell the user they can turn the internet back on under "
    "Environment in the session panel."
)


def _is_loopback(host: str) -> bool:
    """True only for ``localhost`` itself and loopback IP literals."""
    host = (host or "").lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if host == _LOCALHOST:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_of(target: str) -> Optional[str]:
    """The host a URL or bare ``host[:port][/path]`` names, or None if unclear."""
    target = target.strip()
    if not target or any(c.isspace() or c == "\\" for c in target):
        return None
    scheme, sep, rest = target.partition("://")
    if not sep:
        rest = target
    elif scheme.lower() == "file":
        return _LOCALHOST if rest.startswith("/") else None
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if authority.count("@") > 1:
        return None
    try:
        host = urlparse("//" + authority).hostname
    except ValueError:
        return None
    return host or None


def _target_is_local(target: str) -> bool:
    host = _host_of(target)
    return host is not None and _is_loopback(host)


def _url_is_local(url: str) -> bool:
    return "://" in url and _target_is_local(url)


def _fetch_stays_local(program: str, args: List[str]) -> bool:
    """A curl / wget / Invoke-WebRequest call whose every destination is this PC."""
    family = program if program in ("curl", "wget") else "ps"
    short_values, long_values = _VALUE_OPTIONS[family]
    short_bad, long_bad = _REDIRECTING_OPTIONS[family]
    targets: List[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        lowered = arg.lower()
        if family == "ps" and arg.startswith("-") and len(arg) > 1:
            name, colon, _ = lowered.partition(":")
            if name.startswith("-pr"):  # -Proxy / -ProxyCredential, abbreviated
                return False
            if name in long_values:
                skip_next = not colon
            elif colon:
                targets.append(arg.partition(":")[2])
            continue
        if arg.startswith("--"):
            name, eq, _ = lowered.partition("=")
            if name in long_bad or name.startswith("--proxy"):
                return False
            if name in long_values:
                skip_next = not eq
            elif eq:
                targets.append(arg.partition("=")[2])
            continue
        if arg.startswith("-") and len(arg) > 1:
            for idx, letter in enumerate(arg[1:]):
                if letter in short_bad:
                    return False
                if letter in short_values:
                    skip_next = idx == len(arg) - 2
                    break
            continue
        targets.append(arg)
    return bool(targets) and all(_target_is_local(t) for t in targets)


def _unwrap(tokens: List[str]) -> Tuple[List[str], bool]:
    """Drop ``sudo``/``env``/``timeout`` prefixes; report any ``VAR=value`` set."""
    assigned = False
    while tokens:
        program = _program(tokens[0])
        if _ASSIGNMENT_RE.match(tokens[0]):
            assigned = True
            tokens = tokens[1:]
        elif program in _WRAPPERS or program in _TIMING_WRAPPERS or program == "env":
            tokens = tokens[1:]
            while tokens and (
                tokens[0].startswith("-") or re.match(r"^\d[\w.,]*$", tokens[0])
            ):
                tokens = tokens[1:]
        else:
            break
    return tokens, assigned


def _bare(word: str) -> str:
    """A word as the shell looks it up: bash drops quotes and backslashes, cmd.exe drops ``^``."""
    return _SPLICE_RE.sub("", word)


def _mentions_online(words: List[str]) -> bool:
    # A full path, a quoted name or a Windows suffix still runs the program, and so
    # does a spliced one: c''onda, co"n"da, c\onda and co^nda are all conda.
    if not words:
        return False
    # Backslashes kept in one spelling, so C:\Tools\co^nda.exe still reads as a path.
    unquoted = [_QUOTE_CARET_RE.sub("", w) for w in words]
    for spelled in (words, unquoted, [_bare(w) for w in words]):
        joined = " ".join([_program(spelled[0]), *spelled[1:]])
        if _ONLINE_COMMAND_RE.search(joined) is not None:
            return True
    return False


def _explicitly_offline(segment: str) -> bool:
    """One command a documented flag keeps off the network.

    ``mvn -o``, ``mvn -v``, ``gradle --offline``, ``gradle --version``,
    ``npm ci/install --offline`` and ``pip install --no-index``; only those
    exact flags count. Still checked: anything quoted or expanded, a URL, a
    requirements file, a wrapper (mvnw and gradlew download a build tool
    first), and a goal that publishes. Kept in step with ``explicitlyOffline``
    in cpp/src/internet_refusal.cpp.
    """
    if _OFFLINE_UNSAFE_RE.search(segment):
        return False
    words = segment.replace("^", "").split()
    if not words:
        return False
    program = _program(words[0])
    rest = words[1:]
    version_only = len(rest) == 1 and rest[0] in ("-v", "--version")
    publishes = any(_PUBLISHING_RE.search(w) for w in rest)
    if program == "mvn":
        return version_only or (("-o" in rest or "--offline" in rest) and not publishes)
    if program == "gradle":
        return version_only or ("--offline" in rest and not publishes)
    if program == "npm":
        return (
            len(rest) > 1 and rest[0] in ("ci", "install", "i") and "--offline" in rest
        )
    if re.fullmatch(r"(?:python[0-9.]*|py)", program) and rest[:1] == ["-m"]:
        if len(rest) < 2 or rest[1].lower() != "pip":
            return False
        rest = rest[2:]
    elif not re.fullmatch(r"pip[0-9.]*", program):
        return False
    if rest[:1] != ["install"] or "--no-index" not in rest:
        return False
    # A requirements or constraints file can name a URL this check cannot see.
    return not any(
        w.startswith(("-r", "-c", "--requirement", "--constraint")) for w in rest
    )


def _segment_goes_online(segment: str, depth: int) -> bool:
    if depth == 0 and _explicitly_offline(segment):
        return False
    tokens, assigned = _unwrap(_tokens(segment))
    if not tokens:
        return False
    program, rest = _program(tokens[0]), tokens[1:]
    if program in _SHELLS or _program(_bare(tokens[0])) in _SHELLS:
        for idx, arg in enumerate(rest):
            if arg.lower() in _SHELL_COMMAND_FLAGS and idx + 1 < len(rest):
                return _command_goes_online(" ".join(rest[idx + 1 :]), depth + 1)
    if program in _ARGUMENT_FEEDERS:
        inner, _ = _unwrap([t for t in rest if not t.startswith("-")])
        return _mentions_online(inner)
    for idx, arg in enumerate(rest):
        if arg in _FIND_EXEC and _mentions_online(rest[idx + 1 :]):
            return True
    if _ONLINE_COMMAND_RE.search(segment) is None and not _mentions_online(tokens):
        return False
    if program not in _FETCH_PROGRAMS or assigned:
        return True
    # A substitution can hand the fetch a destination GAIA can't see.
    if any(mark in segment for mark in ("$(", "`", "<(", ">(", "${")):
        return True
    return not _fetch_stays_local(program, rest)


def _command_goes_online(command: str, depth: int = 0) -> bool:
    """True when any part of a shell command line would reach another host."""
    if depth > 3:
        return True
    return any(_segment_goes_online(s, depth) for s in _split_segments(command))


def _remote_hosts(args: Mapping[str, Any]) -> List[str]:
    """Hosts of every URL in the call's non-shell arguments that leaves this PC."""
    found: List[str] = []
    stack: List[Any] = [v for k, v in args.items() if k not in ("command", "cmd")]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            for url in _TEXT_URL_RE.findall(value):
                if not _url_is_local(url):
                    found.append(_host_of(url) or url[:60])
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif isinstance(value, Mapping):
            stack.extend(value.values())
    return found


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
    url_is_local = isinstance(url, str) and _url_is_local(url)
    if name in WEB_TOOLS:
        if name == "search_web" or not url_is_local:
            what = f"the web tool {name}"
    elif name in ONLINE_SERVICE_TOOLS:
        what = f"{name}, which calls an online service"
    elif pane_tool_name(name).startswith("browser_"):
        if not url_is_local:
            what = "the browser"
    elif name == "transcribe_media":
        hosts = _remote_hosts(args)
        if hosts:
            what = f"{name} (it would download from {hosts[0]})"
    elif registry_entry is not None and registry_entry.get("_mcp_server"):
        what = f"the {registry_entry['_mcp_server']} connector"
    else:
        effects = [e for e in effects_of_call(name, args) if e.kind == NETWORK]
        hosts = {e.target for e in effects if not _is_loopback(e.target)}
        if effects:
            # The effect scan reads only the host's leading characters; re-read
            # each URL whole so userinfo and look-alike hosts can't pass.
            hosts.update(_remote_hosts(args))
        command = args.get("command") or args.get("cmd")
        if hosts:
            what = f"{name} (it would contact {', '.join(sorted(hosts)[:3])})"
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
