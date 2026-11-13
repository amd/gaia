# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a credential looks like in text, and how to mask it.

One copy of the token shapes and URL patterns that failure records, memory
and tool output all mask, so a shape added here is masked everywhere.
"""

import functools
import os
import re
from typing import Any, Optional, Tuple

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
_URL_SECRET_QUERY = re.compile(
    r"([?&](?:access_token|private_token|token|api_key|apikey|key|secret"
    r"|password|passwd|auth|sig|signature)=)[^&\s#\"'<>]+",
    re.IGNORECASE,
)

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


def redact_tool_output(value: Any) -> Any:
    """*value* with tokens masked and credential-file lines hidden, everywhere in it."""
    if isinstance(value, str):
        return redact_credentials(mask_credential_file_lines(value))
    if isinstance(value, dict):
        return {key: redact_tool_output(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_tool_output(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_tool_output(item) for item in value)
    return value
