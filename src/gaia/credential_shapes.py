# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a credential looks like in text, and how to mask it.

One copy of the token shapes and URL patterns that failure records, memory
and tool output all mask, so a shape added here is masked everywhere.
"""

import functools
import os
import re
from typing import Any, Callable, Optional, Tuple

REDACTED = "[redacted]"

#: Tokens a provider issues with a fixed prefix; each pattern spans the token.
#: Compile with ``re.IGNORECASE``. A prefix glued to a word or a ``-`` is part
#: of a name (``task-sk-…``), not a token.
TOKEN_SHAPES: Tuple[str, ...] = (
    r"(?<![\w-])sk-ant-[a-zA-Z0-9_-]{10,}",  # Anthropic API key
    r"(?<![\w-])sk-[a-zA-Z0-9_-]{20,}",  # OpenAI-style API key
    r"(?<![\w-])gh[pousr]_[a-zA-Z0-9]{20,}",  # GitHub token (ghp_/gho_/ghu_/ghs_/ghr_)
    r"(?<![\w-])github_pat_[a-zA-Z0-9_]{20,}",  # GitHub fine-grained PAT
    r"(?<![\w-])AKIA[0-9A-Z]{16}\b",  # AWS access key id
    r"(?<![\w-])AIza[0-9A-Za-z_-]{20,}",  # Google API key
    r"(?<![\w-])xox[baprs]-[a-zA-Z0-9-]{10,}",  # Slack token
    r"(?<![\w-])glpat-[a-zA-Z0-9_-]{20,}",  # GitLab PAT
)

#: The userinfo of a URL (``https://user:token@host``): between ``://`` and
#: the ``@`` before the host. scp-style ``git@host:repo`` and email addresses
#: have no ``://`` and are left alone.
URL_USERINFO = r"(?<=://)[^/\s@?#]+(?=@)"

#: A query parameter whose value is a credential: ``?access_token=…``.
_SECRET_QUERY_NAME = (
    r"([?&](?:access_token|private_token|token|api_key|apikey|key|secret"
    r"|password|passwd|auth|sig|signature)=)"
)
_URL_SECRET_QUERY = re.compile(_SECRET_QUERY_NAME + r"[^&\s#\"'<>]+", re.IGNORECASE)

_TOKENS = tuple(re.compile(p, re.IGNORECASE) for p in TOKEN_SHAPES)
_USERINFO = re.compile(URL_USERINFO)


def redact_credentials(text: str) -> str:
    """*text* with URL userinfo, secret query values and known tokens masked."""
    if not text:
        return text
    text = _USERINFO.sub(REDACTED, text)
    text = _URL_SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, text)
    for pattern in _TOKENS:
        text = pattern.sub(REDACTED, text)
    return text


#: What replaces a line that came from a credential file.
HIDDEN_LINE = "[credential file content hidden]"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: Where a ``file:line`` prefix can end: a match, a context line, ``grep -Z``.
_PREFIX_ENDS = ":-\x00"
_SEPARATORS = "/\\:"
#: How far into a line a file-name prefix is looked for.
_PREFIX_SPAN = 4096


@functools.lru_cache(maxsize=1)
def _credential_names() -> Tuple[frozenset, frozenset, Any, Any]:
    """The refused names and extensions, a pattern for either, and one for a prefix.

    The prefix pattern finds a name or extension right before ``:``, ``-``,
    NUL or a colour code: where a search's file-name prefix ends.
    """
    from gaia import security

    names = frozenset(n.lower() for n in security.SENSITIVE_FILE_NAMES)
    extensions = frozenset(e.lower() for e in security.SENSITIVE_EXTENSIONS)
    words = sorted(names | extensions, key=len, reverse=True)
    alternation = "|".join(re.escape(w) for w in words)
    needle = re.compile(alternation, re.IGNORECASE)
    prefix = re.compile(f"(?:{alternation})(?=[:\\-\x00\x1b])", re.IGNORECASE)
    return names, extensions, needle, prefix


def mask_credential_file_lines(text: str) -> str:
    """*text* with every line a search printed from a credential file hidden.

    ``grep -rn``, ``findstr /s`` and ``Select-String`` put the file name in
    front of each line (``./.env:3:KEY=...``). A line whose prefix names a
    credential file keeps the prefix and loses the rest, so a search of a
    folder holding ``.env`` still runs without printing what is in it.
    """
    if not text:
        return text
    names, extensions, needle, prefix = _credential_names()
    lowered = text.lower()
    # Substring checks first: far cheaper than the pattern over all of *text*.
    if not any(word in lowered for word in names | extensions):
        return text
    if not prefix.search(text):
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not prefix.search(line):
            continue
        prefix_end = _credential_prefix_end(line, names, extensions, needle)
        if prefix_end is not None:
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = line[:prefix_end] + HIDDEN_LINE + ending
    return "".join(lines)


def _credential_prefix_end(
    line: str, names: frozenset, extensions: frozenset, needle: Any
) -> Optional[int]:
    """Where a ``file:`` prefix naming a credential file ends in *line*, or None."""
    coloured = "\x1b" in line
    plain = _ANSI.sub("", line[: _PREFIX_SPAN * 4]) if coloured else line
    # Select-String marks a match line with '> ' and indents context lines.
    start = len(plain) - len(plain.lstrip(" >"))
    name_start = scanned = start
    for match in needle.finditer(plain, start, start + _PREFIX_SPAN):
        end = match.end()
        if end >= len(plain) or plain[end] not in _PREFIX_ENDS:
            continue
        # The name is what follows the last folder separator (or ':') so far.
        for separator in _SEPARATORS:
            found = plain.rfind(separator, scanned, match.start())
            if found >= name_start:
                name_start = found + 1
        scanned = match.start()
        name = plain[name_start:end].lower()
        if name in names or os.path.splitext(name)[1] in extensions:
            # Colour codes move the offsets, so a coloured line goes whole.
            return 0 if coloured else end + 1
    return None


def _map_strings(value: Any, mask: Callable[[str], str]) -> Any:
    """*value* with *mask* applied to every string in it, however deeply nested."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, mask) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, mask) for item in value]
    if isinstance(value, tuple):
        return tuple(_map_strings(item, mask) for item in value)
    return value


def _mask_command_output(text: str) -> str:
    return redact_credentials(mask_credential_file_lines(text))


def redact_tool_output(value: Any) -> Any:
    """*value* with tokens masked and credential-file lines hidden, everywhere in it."""
    return _map_strings(value, _mask_command_output)


#: ``user:password@`` in a URL. A bare ``user@`` (``git@``, ``postgres@``) is
#: a login name, not a secret.
_FILE_USERINFO = re.compile(r"(?<=://)([^/\s@?#:]+)(?::([^/\s@?#]*))?(?=@)")

#: A query value is a secret only at this length; ``?key=abc`` is a placeholder.
_MIN_QUERY_SECRET = 8

#: Placeholder passwords and values that documentation uses in place of a secret.
_PLACEHOLDER_WORDS = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "pwd",
        "secret",
        "token",
        "changeme",
        "example",
        "mypassword",
        "yourpassword",
    }
)
#: A stand-in only when the WHOLE value has this form; a secret may contain
#: ``$``, ``%`` or braces.
_PLACEHOLDER_SHAPE = re.compile(
    r"^(?:"
    r"\{\{?[^{}]*\}\}?"  # {name}, {{ name }}
    r"|\$\{(?:[A-Z_][A-Z0-9_]*|[a-z][A-Za-z0-9]*)\}"  # ${NAME}, ${apiKey}
    r"|\$[A-Z_][A-Z0-9_]*"  # $NAME
    r"|%\([^()]*\)[sd]"  # %(name)s
    r"|<[^<>]*>"  # <token>
    r"|(?:YOUR|MY)_[A-Z0-9_]+"  # YOUR_TOKEN, YOUR_TOKEN_HERE
    r"|[A-Z0-9_]+_HERE"  # TOKEN_HERE
    r"|x+|X+|\*+|\.+"
    r")$"
)

#: A query value in file text also ends where code closes the string or call.
_FILE_SECRET_QUERY = re.compile(_SECRET_QUERY_NAME + r"[^&\s#\"'`<>()]+", re.IGNORECASE)

#: A run of key-like characters a generated token has and a hyphenated name lacks.
_RANDOM_RUN = re.compile(r"[A-Za-z0-9_]{16,}")


def _placeholder(value: str) -> bool:
    """True when *value* is a template or a stand-in, not a real secret."""
    return (
        not value
        or value.lower() in _PLACEHOLDER_WORDS
        or bool(_PLACEHOLDER_SHAPE.match(value))
    )


def _looks_generated(text: str) -> bool:
    """True when *text* holds a long run of letters mixed with digits."""
    return any(
        any(c.isdigit() for c in run) and any(c.isalpha() for c in run)
        for run in _RANDOM_RUN.findall(text)
    )


def _mask_file_userinfo(match: "re.Match[str]") -> str:
    user, password = match.group(1), match.group(2)
    if password is not None and not _placeholder(password):
        return REDACTED
    if password is None and len(user) >= 20 and _looks_generated(user):
        return REDACTED  # a token used as the user name
    return match.group(0)


def _mask_file_query(match: "re.Match[str]") -> str:
    value = match.group(0)[len(match.group(1)) :]
    if len(value) < _MIN_QUERY_SECRET or _placeholder(value):
        return match.group(0)
    return match.group(1) + REDACTED


def _mask_file_token(match: "re.Match[str]") -> str:
    token = match.group(0)
    # Only the bare "sk-" prefix collides with ordinary hyphenated names.
    if token[:3].lower() == "sk-" and not _looks_generated(token):
        return token
    return REDACTED


def redact_file_text(text: str) -> str:
    """*text* read out of a file, with its real credentials masked.

    Stricter than :func:`redact_credentials`, because a file is full of
    things shaped like a secret that aren't: ``git@`` and ``postgres@`` URLs,
    ``?key={api_key}`` templates, ``sk-learn-...`` names. Masking those would
    hide the file's own text and block edits to it.
    """
    if not text:
        return text
    text = _FILE_USERINFO.sub(_mask_file_userinfo, text)
    text = _FILE_SECRET_QUERY.sub(_mask_file_query, text)
    for pattern in _TOKENS:
        text = pattern.sub(_mask_file_token, text)
    return text


def redact_file_content(value: Any) -> Any:
    """*value* with :func:`redact_file_text` applied everywhere; lines are never hidden.

    For text read out of a file: a line naming ``.env:`` there is the file's
    own content, not a search's file-name prefix.
    """
    return _map_strings(value, redact_file_text)


def holds_credentials(text: str) -> bool:
    """True when :func:`redact_file_text` would change *text*."""
    return bool(text) and redact_file_text(text) != text
