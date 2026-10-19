# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stdio transport for MCP protocol communication via subprocess."""

import bisect
import collections
import functools
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil

from gaia.env import child_env
from gaia.logger import get_logger
from gaia.tool_cancellation import ToolCancelled, tool_cancelled
from gaia.utils.terminal import windows_hidden_popen_kwargs

from .base import MCPTransport

logger = get_logger(__name__)

# Operator characters shlex isolates into their own token when unquoted. A
# token made only of these was shell syntax; quoted uses stay inside a larger
# token and are inert under shell=False, so they are left alone.
_SHELL_OPERATOR_CHARS = frozenset(";|&<>()")

# How often a blocked read wakes to check for cancellation.
_POLL_INTERVAL_S = 0.25

# What counts as a known secret in a server's launch config. Server output
# is cleaned of exactly these values; nothing else is guessed at.
#
# A name holds a secret when any of its words, matched whole, is one of
# these: API_KEY, API_KEY_2, SECRET_KEY_BASE, API_TOKENS, PROXY_AUTHORIZATION,
# --accessToken.
_SECRET_WORDS = frozenset(
    {
        "key",
        "keys",
        "apikey",
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwords",
        "passwd",
        "pwd",
        "passphrase",
        "credential",
        "credentials",
        "auth",
        "authorization",
        "authtoken",
        "apitoken",
        "pass",
        "pat",
        "pgpassword",
    }
)
# ... unless its last word says the value is something else about the secret:
# AUTH_MODE, KEY_ID, TOKEN_FILE, --token-limit, TOKENIZERS_PARALLELISM.
_QUALIFIER_WORDS = frozenset(
    {
        "id",
        "mode",
        "limit",
        "file",
        "path",
        "type",
        "url",
        "name",
        "size",
        "count",
        "length",
        "region",
        "parallelism",
        "layout",
        "sort",
        "partition",
        "cache",
        "enabled",
        "ttl",
        "endpoint",
        "provider",
        "uri",
        "timeout",
        "required",
        "method",
        "scope",
        "prefix",
        "through",
        "attempt",
        "attempts",
        "age",
        "days",
        "retry",
        "retries",
        "expiry",
        "expires",
        "lifetime",
        "budget",
    }
)
# ... or it is a database's key: SORT_KEY, PARTITION_KEY, PRIMARY_KEY.
_KEY_KINDS = frozenset(
    {"sort", "partition", "primary", "cache", "foreign", "composite"}
)
# ... or its first word makes it a count: MAX_TOKENS, --max-tokens.
_COUNT_WORDS = frozenset({"max", "min", "num", "first"})
# Words that name a secret whatever comes before them.
_STRONG_WORDS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "passwords",
        "passwd",
        "pwd",
        "passphrase",
        "pgpassword",
        "credential",
        "credentials",
        "apikey",
        "apitoken",
        "authtoken",
        "authorization",
    }
)
# A switch's value says on or off; it is never the secret.
_BOOLEANS = frozenset({"true", "false", "yes", "no", "on", "off", "0", "1"})
# user:pass@ or TOKEN@ in a URL; the value of an Authorization or Bearer header.
_URL_USERINFO = re.compile(r"[a-zA-Z][\w+.-]*://([^/@\s]+)@")
_AUTH_HEADER = re.compile(
    r"(?:authorization\s*[:=]\s*(?:bearer|basic|token)?\s*|\bbearer\s+)([^\s\"',;]+)",
    re.IGNORECASE,
)


def _words(name: str) -> List[str]:
    """``--accessToken``, ``APIToken``, ``API_KEY2`` -> access token, api token, api key 2."""
    spaced = name.lstrip("-")
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", spaced)  # APIToken
    spaced = re.sub(r"([a-z])([A-Z])", r"\1-\2", spaced)  # accessToken
    spaced = re.sub(r"([A-Za-z])([0-9])", r"\1-\2", spaced)  # KEY2
    spaced = re.sub(r"([0-9])([A-Za-z])", r"\1-\2", spaced)  # 2FA
    return [word for word in re.split(r"[-_.]+", spaced.lower()) if word]


def _names_a_secret(name: str) -> bool:
    words = _words(name)
    if not words or words[-1] in _QUALIFIER_WORDS:
        return False
    # MAX_TOKENS and FIRST_PASS are counts; FIRST_PASSWORD is still a password.
    if words[0] in _COUNT_WORDS and not any(word in _STRONG_WORDS for word in words):
        return False
    # A switch: --no-auth, --disable-auth, DISABLE_AUTH, NO_AUTH. NO_PROXY_TOKEN
    # is a token for NO_PROXY, so for a name "no" must be followed by one word.
    if words[0] in ("disable", "enable") or (
        words[0] == "no" and (name.startswith("-") or len(words) == 2)
    ):
        return False
    # A database's key, not a credential: SORT_KEY, PARTITION_KEY, PRIMARY_KEY.
    if len(words) >= 2 and words[-1] in ("key", "keys") and words[-2] in _KEY_KINDS:
        return False
    return any(word in _SECRET_WORDS for word in words)


def _is_secret_flag(flag: str) -> bool:
    """``--api-key``, ``--accessToken``: a flag whose name holds a secret."""
    return flag.startswith("-") and _names_a_secret(flag)


def _is_secret_name(name: str) -> bool:
    """An env or KEY=VALUE name holding a secret: API_KEY, AUTHORIZATION."""
    return _names_a_secret(name)


# Finding a known secret in text a server wrote. Rather than describing every
# encoding in a pattern, the text is decoded into views that each map back to
# the original, and the secret is looked up in each as a plain string.
#
# Escapes an encoder can write a character in (JSON, Python's repr and
# ascii(), Go, C), matched as runs of one kind so a long run is one step.
_SHORT_ESCAPES = {
    "\\": "\\",
    "/": "/",
    "'": "'",
    '"': '"',
    "t": "\t",
    "n": "\n",
    "r": "\r",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "0": "\0",
    "e": "\x1b",
    "a": "\x07",
}


def _escape_runs(short: str, byte: str, digit: str) -> "re.Pattern[str]":
    """Runs of one kind of escape: short ones, or hex ones of one width.

    The backslash leads the pattern, outside any group, so the search for a
    match is a fast scan for it.
    """
    return re.compile(
        r"\\(?:"
        rf"({short}(?:\\{short})*)"
        rf"|(x{byte}(?:\\x{byte})*)"
        rf"|(u{digit}{{4}}(?:\\u{digit}{{4}})*)"
        rf"|(U{digit}{{8}}(?:\\U{digit}{{8}})*))"
    )


_HEX_DIGIT = "[0-9a-fA-F]"
# Readings of a backslash. Every escape, a doubled backslash included:
_ALL_ESCAPES = _escape_runs(r"[\\/'\"tnrbfv0ea]", _HEX_DIGIT * 2, _HEX_DIGIT)
# a backslash before another escape as a literal backslash ("\\u4e2d" is an
# escaped backslash then "u4e2d", or a backslash then an escape):
_LITERAL_BACKSLASH = _escape_runs(r"[/'\"tnrbfv0ea]", _HEX_DIGIT * 2, _HEX_DIGIT)
# and hex escapes only, any other backslash being the secret's own: of any
# character, or as Python's backslashreplace writes them, lowercase and for
# non-ASCII characters only.
_HEX_ONLY = _escape_runs("(?!)", _HEX_DIGIT * 2, _HEX_DIGIT)
_BACKSLASHREPLACED = _escape_runs("(?!)", "[89a-f][0-9a-f]", "[0-9a-f]")
# The two readings of a backslash run differ only where an even run is
# followed by what could start an escape: an odd one decodes the same.
# Two backslashes lead the pattern so the search for it is a fast scan.
_EVEN_BACKSLASHES_BEFORE_ESCAPE = re.compile(
    r"\\\\(?<=(?<!\\)\\\\)(?:\\\\)*[/'\"tnrbfv0eaxuU]"
)
_SHORT_ESCAPE = re.compile(r"\\[\\/'\"tnrbfv0ea]")
_HEX_ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8})")
_NON_ASCII_ESCAPE = re.compile(
    r"\\(?:x[89a-f][0-9a-f]|u(?!00[0-7])[0-9a-f]{4}|U[0-9a-f]{8})"
)
_DECODE_PASSES = 3
_MAX_VIEWS = 16
# Characters a code page or an encoder mangles: non-ASCII, "?", U+FFFD. A
# secret's run of them matches any run, so it still matches when its
# non-ASCII characters arrive replaced, as mojibake, or as bytes a UTF-8
# decode collapsed.
_MANGLED_CHAR = r"[?\x80-\U0010ffff]"
_MANGLED_RUN = re.compile(_MANGLED_CHAR + "+")
# A run of k secret characters arrives as at most this many times k.
_RUN_SCALE = 4


def _escape_segments(match: "re.Match[str]", non_ascii_only: bool) -> list:
    """``(start, end, text, width)`` for a run of escapes, in order.

    Each character of *text* comes from *width* characters of the run; a
    width of 0 maps the text to the whole segment. *text* None keeps the
    segment as written.
    """
    start, end = match.span()
    run = match[0]
    if match[1] is not None:
        return [(start, end, "".join(map(_SHORT_ESCAPES.__getitem__, run[1::2])), 2)]
    width = 4 if match[2] is not None else 6 if match[3] is not None else 10
    codes = [int(run[i + 2 : i + width], 16) for i in range(0, len(run), width)]
    if all(
        (code < 0xD800 or 0xDFFF < code <= 0x10FFFF)
        and (code >= 0x80 or not non_ascii_only)
        for code in codes
    ):
        return [(start, end, "".join(map(chr, codes)), width)]
    # Surrogate pairs, codes out of range, or ASCII left alone: one at a time.
    segments = []
    i = 0
    while i < len(codes):
        at, code = start + i * width, codes[i]
        if (
            0xD800 <= code <= 0xDBFF
            and i + 1 < len(codes)
            and 0xDC00 <= codes[i + 1] <= 0xDFFF
        ):
            pair = 0x10000 + ((code - 0xD800) << 10) + (codes[i + 1] - 0xDC00)
            segments.append((at, at + 2 * width, chr(pair), 0))
            i += 2
            continue
        keep = code > 0x10FFFF or (non_ascii_only and code < 0x80)
        segments.append((at, at + width, None if keep else chr(code), width))
        i += 1
    return segments


def _run_decoder(non_ascii_only: bool):
    """A ``re.sub`` replacement decoding a run of escapes."""
    # A run decodes the same wherever it is, and adversarial text repeats a few.
    cache: Dict[str, str] = {}
    lookup = cache.get

    def decode(match: "re.Match[str]") -> str:
        written = match[0]
        decoded = lookup(written)
        if decoded is None:
            decoded = "".join(
                match.string[start:end] if new is None else new
                for start, end, new, _width in _escape_segments(match, non_ascii_only)
            )
            if len(cache) < 4096:
                cache[written] = decoded
        return decoded

    return decode


# Each reading: its pattern, the replacement, the mapped segments, and a check
# that skips it where it would only repeat the first reading's view.
_READINGS = tuple(
    (
        pattern,
        _run_decoder(non_ascii),
        functools.partial(_escape_segments, non_ascii_only=non_ascii),
        needed,
    )
    for pattern, non_ascii, needed in (
        (_ALL_ESCAPES, False, lambda _text: True),
        (
            _LITERAL_BACKSLASH,
            False,
            lambda text: _EVEN_BACKSLASHES_BEFORE_ESCAPE.search(text) is not None,
        ),
        (
            _HEX_ONLY,
            False,
            lambda text: _SHORT_ESCAPE.search(text) is not None
            and _HEX_ESCAPE.search(text) is not None,
        ),
        (
            _BACKSLASHREPLACED,
            True,
            lambda text: _NON_ASCII_ESCAPE.search(text) is not None,
        ),
    )
)
_ALL, _LITERAL, _HEX, _NON_ASCII = _READINGS
# The readings tried at each depth. A backslash backslashreplace wrote is only
# in the text as it arrived, and a raw one at most one layer in: deeper
# layers escape their own.
_READINGS_AT_DEPTH = (
    (_ALL, _LITERAL, _NON_ASCII),
    (_ALL, _LITERAL, _HEX),
    (_ALL,),
)


class _View:
    """A rewrite of its parent's text, mapped back to it.

    The root view is the text searched, at *offset* in the original. The map
    is built only when a hit needs it.
    """

    __slots__ = ("text", "parent", "reading", "offset", "_pieces", "_starts")

    def __init__(self, text: str, parent: "Optional[_View]", reading=None, offset=0):
        self.text = text
        self.parent = parent
        self.reading = reading
        self.offset = offset
        self._pieces: Optional[list] = None
        self._starts: List[int] = []

    @classmethod
    def rewrite(cls, parent: "_View", reading) -> "Optional[_View]":
        """*parent* with every match of *reading* rewritten, or None if unchanged."""
        pattern, replace = reading[:2]
        text = pattern.sub(replace, parent.text)
        return None if text == parent.text else cls(text, parent, reading)

    def piece_at(self, index: int) -> tuple:
        """``(start, end, source_start, source_end, width)`` holding *index*."""
        if self._pieces is None:
            self._map()
        return self._pieces[bisect.bisect_right(self._starts, index) - 1]

    def _map(self) -> None:
        source = self.parent.text
        pattern, _replace, segments_of = self.reading[:3]
        pieces: list = []
        at = position = 0
        for match in pattern.finditer(source):
            for start, end, new, width in segments_of(match):
                if new is None:
                    continue
                if start > position:
                    pieces.append((at, at + start - position, position, start, 1))
                    at += start - position
                pieces.append((at, at + len(new), start, end, width))
                at += len(new)
                position = end
        pieces.append((at, at + len(source) - position, position, len(source), 1))
        if at + len(source) - position != len(self.text):
            raise AssertionError("decoded view and its map disagree")
        self._pieces = pieces
        self._starts = [piece[0] for piece in pieces]

    def to_source(self, start: int, end: int) -> Tuple[int, int]:
        """A span of this view as a span of its parent's text."""
        first, last = self.piece_at(start), self.piece_at(end - 1)
        if first[4]:
            start = first[2] + (start - first[0]) * first[4]
        else:
            start = first[2]
        end = last[2] + (end - last[0]) * last[4] if last[4] else last[3]
        return start, end

    def to_original(self, start: int, end: int) -> Tuple[int, int]:
        view = self
        while view.parent is not None:
            start, end = view.to_source(start, end)
            view = view.parent
        return start + view.offset, end + view.offset


def _decoded_views(root: _View) -> List[_View]:
    """*root* and its decodings, each reading of each view, three passes deep."""
    views = [root]
    seen = {root.text}
    frontier = [root]
    for depth in range(_DECODE_PASSES):
        decoded = []
        for view in frontier:
            if "\\" not in view.text:
                continue
            for reading in _READINGS_AT_DEPTH[depth]:
                if len(views) >= _MAX_VIEWS:
                    return views
                if not reading[3](view.text):
                    continue
                child = _View.rewrite(view, reading)
                if child is None or child.text in seen:
                    continue
                seen.add(child.text)
                views.append(child)
                decoded.append(child)
        frontier = decoded
    return views


@functools.lru_cache(maxsize=256)
def _loose_pattern(secret: str) -> "Optional[re.Pattern[str]]":
    """*secret* with each run of mangleable characters as a run of any.

    Each run may be up to ``_RUN_SCALE`` times as long; one at either end is
    matched up to that length next to the rest. None when the secret has no
    such run, or too few ASCII letters and digits for a loose match not to
    catch ordinary text.
    """
    parts = _MANGLED_RUN.split(secret)
    runs = _MANGLED_RUN.findall(secret)
    if not runs or len(re.findall(r"[A-Za-z0-9]", "".join(parts))) < 4:
        return None
    pattern = re.escape(parts[0])
    for run, part in zip(runs, parts[1:]):
        pattern += f"{_MANGLED_CHAR}{{1,{_RUN_SCALE * len(run)}}}" + re.escape(part)
    # A lookahead, so overlapping matches are all found.
    return re.compile(f"(?=({pattern}))")


@functools.lru_cache(maxsize=256)
def _period(text: str) -> int:
    """The shortest shift that maps *text* onto itself."""
    return next(
        (shift for shift in range(1, len(text)) if text[shift:] == text[:-shift]),
        len(text),
    )


def _repeat_length(text: str, start: int, period: int) -> int:
    """How far *text* goes on repeating itself every *period* from *start*."""
    longest = len(text) - start
    known, step = 0, 64
    while known < longest:
        step = min(step, longest - known)
        at = start + known
        if text[at : at + step] != text[at - period : at - period + step]:
            break
        known += step
        step *= 2
    else:
        return known
    # The mismatch is within the last step: halve it down.
    while step > 1:
        step //= 2
        at = start + known
        if text[at : at + step] == text[at - period : at - period + step]:
            known += step
    return known


def _occurrences(haystack: str, needle: str):
    """Spans of *haystack* that are *needle*, overlapping ones as one span.

    A needle repeating through the text is jumped over in one step rather
    than found once per character.
    """
    period = _period(needle)
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        if period < len(needle):
            end += _repeat_length(haystack, end, period) // period * period
        yield start, end
        start = haystack.find(needle, end - len(needle) + 1)


def _anchor_finders(char: str) -> list:
    """Finders of *char* raw, and of the tail of any escape of it, whatever
    wrote the backslash: each returns the next position from a start, or -1.

    Separate, so each search is a fast scan for a literal.
    """
    code = ord(char)
    tails = [rf"[xX]{code:02x}", rf"u00{code:02x}", rf"U000000{code:02x}"]
    tails += [re.escape(key) for key, value in _SHORT_ESCAPES.items() if value == char]
    hexed = "|".join(tails).replace(f"{code:02x}", f"(?i:{code:02x})")
    finders = [lambda text, start: text.find(char, start)]
    for written_backslash in (r"\\", "5[cC]"):
        pattern = re.compile(rf"{written_backslash}(?:{hexed})")
        finders.append(
            lambda text, start, pattern=pattern: (
                -1 if (match := pattern.search(text, start)) is None else match.start()
            )
        )
    return finders


def _anchor_count(text: str, char: str) -> int:
    """At least how often the anchors :func:`_anchor_finders` finds for *char* are in *text*."""
    code = ord(char)
    count = text.count(char) + text.count(f"{code:02x}")
    if f"{code:02x}" != f"{code:02X}":
        count += text.count(f"{code:02X}")
    return count + sum(
        text.count(key) for key, value in _SHORT_ESCAPES.items() if value == char
    )


def _candidate_windows(text: str, secrets: List[str]) -> List[Tuple[int, int]]:
    """Spans of *text* that could hold an encoded secret, in order, merged.

    Each lies around an occurrence of the secret's rarest ASCII character, raw
    or escaped, as far as the longest form the secret can be written in, and
    never starts inside a run of backslashes.
    """
    windows = []
    for secret in secrets:
        ascii_chars = {char for char in secret if ord(char) < 128}
        if not ascii_chars:
            return [(0, len(text))]
        reach = _LONGEST_FORM_PER_CHAR * len(secret) + 16
        anchor = min(sorted(ascii_chars), key=lambda char: _anchor_count(text, char))
        finders = _anchor_finders(anchor)
        found = [find(text, 0) for find in finders]
        while True:
            pending = [at for at in found if at != -1]
            if not pending:
                break
            at = min(pending)
            # Also covers every anchor in the next *reach* characters, so the
            # search can skip them.
            windows.append((max(0, at - reach), min(len(text), at + 2 * reach)))
            found = [
                find(text, at + reach) if -1 < next_at < at + reach else next_at
                for find, next_at in zip(finders, found)
            ]
    windows.sort()
    merged: List[List[int]] = []
    for start, end in windows:
        while start > 0 and text[start - 1] == "\\":
            start -= 1
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _secret_spans(secrets: List[str], text: str) -> List[Tuple[int, int]]:
    """Every span of *text* where one of *secrets* appears, in any form."""
    spans = [span for secret in secrets for span in _occurrences(text, secret)]
    loose = [_loose_pattern(secret) for secret in secrets]
    loose = [pattern for pattern in loose if pattern is not None]
    # Not only when the text has non-ASCII: escaped UTF-8 bytes decode to mojibake.
    if "\\" not in text and not loose:
        return spans
    for start, end in _candidate_windows(text, secrets):
        window = text[start:end]
        if "\\" not in window and not (loose and _MANGLED_RUN.search(window)):
            continue
        for view in _decoded_views(_View(window, None, offset=start)):
            if view.parent is not None:
                for secret in secrets:
                    for start_in_view, end_in_view in _occurrences(view.text, secret):
                        spans.append(view.to_original(start_in_view, end_in_view))
            for pattern in loose:
                spans += [
                    view.to_original(*match.span(1))
                    for match in pattern.finditer(view.text)
                ]
    return spans


# How long connect() watches for an immediate crash, and in how many steps.
_STARTUP_CHECK_S = 0.1
_STARTUP_CHECK_SLICES = 5

# How often a running server's child processes are recorded. A launcher that
# starts a child and exits sooner than this can leave that child behind.
_CHILD_SCAN_S = 0.5
_CHILD_SCAN_MAX_S = 8.0

# The longest stderr line kept, and how much of it is read and cleaned so a
# secret at the cut is still matched whole. The rest of a longer line is read
# and dropped.
_STDERR_LINE_CHARS = 8192
# More than the longest one character of a secret can be written as within
# three decodes: an escape whose backslash is itself escaped, twice over.
_LONGEST_FORM_PER_CHAR = 32

# Lines of server stderr kept for crash diagnostics.
_STDERR_TAIL_LINES = 200

# A tool call ends this much before the agent's own per-tool limit, so the
# error the user sees names the MCP server instead of a generic tool timeout.
_TOOL_CALL_MARGIN_S = 5

# Lines read from the server and not yet consumed. A full queue stops the
# reader, which back-pressures the server instead of growing without bound.
_STDOUT_QUEUE_LINES = 10_000


def tool_call_timeout() -> float:
    """Seconds a ``tools/call`` may wait: the agent's per-tool limit, less a margin."""
    # Imported here: gaia.agents imports gaia.mcp, not the other way round.
    from gaia.agents.base.agent import tool_execution_timeout

    return max(tool_execution_timeout() - _TOOL_CALL_MARGIN_S, 1.0)


# JSON-RPC "method not found": the reply to a request this client can't serve.
_METHOD_NOT_FOUND = -32601

_EOF = object()

_FIX_HINT = (
    'Split the program from its arguments instead: {"command": "npx", '
    '"args": ["-y", "@scope/server"]}. See docs/spec/mcp-client.mdx.'
)


def _split_command_string(command: str) -> List[str]:
    """Split a single-string server command into an argv list.

    Args:
        command: Full command line, e.g. ``"npx -y @scope/server"``.

    Returns:
        List[str]: argv tokens, program first, with ``~`` and environment
        variables expanded the way the shell used to expand them.

    Raises:
        ValueError: If the string is blank, unparseable, or carries shell
            syntax that would no longer be interpreted.
    """
    # Windows: double the backslashes so POSIX quoting rules apply without
    # eating path separators. Matches what CommandLineToArgvW would produce.
    raw = command.replace("\\", "\\\\") if os.name == "nt" else command

    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError(
            f"MCP server command could not be parsed ({exc}). "
            f"Check for unbalanced quotes. {_FIX_HINT}"
        ) from exc

    if not tokens:
        raise ValueError(
            "MCP server command is empty. Set 'command' to the "
            f'program to run, e.g. "npx". {_FIX_HINT}'
        )

    for token in tokens:
        # A backtick never forms its own token, so quoting state is already
        # lost here — reject it anywhere in the string rather than guess.
        if set(token) <= _SHELL_OPERATOR_CHARS or "`" in token:
            raise ValueError(
                "MCP server command contains shell syntax (; | & < > ( ) or a "
                "backtick). GAIA launches MCP servers without a shell, so this "
                "would be passed through as literal text rather than interpreted. "
                f"{_FIX_HINT}"
            )

    # The shell used to expand these; do it here so existing configs using
    # ~ or $VAR/%VAR% keep resolving instead of reaching the child literally.
    return [os.path.expandvars(os.path.expanduser(token)) for token in tokens]


def _program_token(command: str) -> str:
    """The first token of a command line, tokenized as ``_split_command_string`` does.

    No validation or expansion: this only names the program in an error.
    """
    raw = command.replace("\\", "\\\\") if os.name == "nt" else command
    lexer = shlex.shlex(raw, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return next(iter(lexer), "")
    except ValueError:
        return ""


class StdioTransport(MCPTransport):
    """Stdio-based transport using subprocess for MCP servers.

    This transport launches MCP servers as subprocesses and communicates
    via stdin/stdout using JSON-RPC messages.

    Servers are always launched with ``shell=False``. Two input shapes are
    accepted:

    1. Legacy: a single command string (e.g. ``"npx -y server"``), tokenized
       here into an argv list rather than handed to a shell.
    2. Modern: ``command`` plus an ``args`` list (matches the Anthropic format).

    Args:
        command: Base command to start the MCP server (e.g., "npx" or "python")
        args: Optional list of arguments (e.g., ["-y", "@modelcontextprotocol/server-github"])
        env: Extra variables for the server, overlaid on the child environment
        timeout: Seconds to wait for each reply (default: 30). A server that
            stays silent that long raises ``TimeoutError``.
        debug: Enable debug logging (default: False)
        name: The server's configured name, used in errors
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        debug: bool = False,
        name: Optional[str] = None,
    ):
        self.name = name
        self._label: Optional[str] = None
        self.command = command
        self.args = args
        self.env = env
        self.timeout = timeout
        self.debug = debug
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        # One request in flight at a time: replies are read off a shared pipe.
        self._request_lock = threading.Lock()
        self._stdout_lines: Optional[queue.Queue] = None
        self._stderr_tail: Optional[collections.deque] = None
        self._stderr_reader: Optional[threading.Thread] = None
        #: Descendants seen so far, by PID, so a dead parent's are still found.
        self._children: Dict[int, "psutil.Process"] = {}
        self._started_at = 0.0
        #: The server process as psutil sees it; None when it can't be
        #: confirmed as the one this connect() started.
        self._ps_server: Optional["psutil.Process"] = None

    @property
    def label(self) -> str:
        """The server's configured name and executable, for errors.

        The executable is shown only once it resolves to a real file, by its
        file name; anything else from the command shows as ``?``.
        """
        if self._label is None:
            program = self.command if self.args else _program_token(self.command)
            executable = "?"
            if program and (os.path.isfile(program) or shutil.which(program)):
                executable = os.path.basename(program.rstrip("/\\")) or "?"
            self._label = f"{self.name} ({executable})" if self.name else executable
        return self._label

    def _secrets(self) -> List[str]:
        """The known secret values in this server's launch config, longest first.

        ``redact`` finds each one in any encoding (``_secret_spans``). A
        boolean (true, false, yes, no, on, off) is never a secret.

        Exactly these, and nothing guessed from a value's shape:

        - the value of an env var whose name holds a secret word
          (``API_KEY``, ``SECRET_KEY_BASE``, ``PROXY_AUTHORIZATION``; the
          words are ``_SECRET_WORDS``, matched whole after splitting on
          ``_``, ``-``, ``.`` and camelCase), unless its last word is a
          qualifier (``AUTH_MODE``, ``KEY_ID``) or its first a count
          (``MAX_TOKENS``);
        - the value after a flag named the same way, in ``--api-key v`` and
          ``--api-key=v`` forms (``--accessToken`` too; not ``--max-tokens``,
          ``--token-limit`` or ``--no-auth``);
        - ``KEY=VALUE`` where ``KEY`` is named the same way;
        - the userinfo of a URL (``user:pass@`` or ``TOKEN@``);
        - an Authorization or Bearer header value inside an argument.

        Values shorter than 4 characters are not matched: they would remove
        ordinary text. A secret echoed back as a JSON number is out of scope.
        """
        try:
            tokens = _split_command_string(self.command)
        except ValueError:
            tokens = self.command.split()
        tokens = [*tokens, *(self.args or [])]
        found = {
            str(value)
            for name, value in (self.env or {}).items()
            if _is_secret_name(str(name))
        }
        for i, token in enumerate(tokens):
            name, eq, value = token.partition("=")
            # A flag is judged by whole words; a bare KEY by the name rule.
            secret_key = (
                _is_secret_flag(name) if name.startswith("-") else _is_secret_name(name)
            )
            if eq and value and secret_key:
                found.add(value)  # --token=VALUE, API_KEY=VALUE
            if not eq and _is_secret_flag(token) and i + 1 < len(tokens):
                found.add(tokens[i + 1])  # --api-key VALUE
        for text in [*tokens, *(str(v) for v in (self.env or {}).values())]:
            for match in _URL_USERINFO.finditer(text):
                userinfo = match.group(1)
                found.add(userinfo)
                found.update(userinfo.split(":", 1))
            found.update(match.group(1) for match in _AUTH_HEADER.finditer(text))
        return sorted(
            (v for v in found if len(v) >= 4 and v.lower() not in _BOOLEANS),
            key=len,
            reverse=True,
        )

    def redact(self, text: str) -> str:
        """*text* from the server with this server's known secrets removed.

        The contract: GAIA's own messages never include a server's command,
        arguments or env; text a server sends back is cleaned of exactly the
        known secret values ``_secrets`` lists, in every form they can arrive
        in, and of nothing else. Debug logs of raw traffic (``debug=True``)
        can still hold other server output.
        """
        return self._clean(text, len(text))

    def _secret_spans(self, text: str) -> List[List[int]]:
        """Where this server's secrets appear in *text*, merged and in order.

        Merged, so two secrets that overlap leave no piece of either.
        """
        spans = sorted(
            span for span in _secret_spans(self._secrets(), text) if span[1] > span[0]
        )
        merged: List[List[int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        return merged

    def _clean(self, text: str, limit: int) -> str:
        """The first *limit* characters of *text*, with every secret redacted.

        A secret that starts before the limit is redacted whole, even where it
        runs past it.
        """
        out, last = [], 0
        for start, end in self._secret_spans(text):
            if start >= limit:
                break
            out += [text[last:start], "<redacted>"]
            last = end
        if last < limit:
            out.append(text[last:limit])
        return "".join(out)

    @property
    def _stderr_window(self) -> int:
        """Characters of a stderr line read at once: the kept part, plus room
        for the longest form any secret can be written in to end past it."""
        longest = max((len(secret) for secret in self._secrets()), default=0)
        return _STDERR_LINE_CHARS + max(4096, _LONGEST_FORM_PER_CHAR * longest)

    def redact_value(self, value: Any) -> Any:
        """*value* (a decoded reply) with every string in it redacted, keys too.

        Cleaned before it is serialized, so a secret is matched as decoded
        text rather than through JSON escaping.
        """
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {
                (self.redact(k) if isinstance(k, str) else k): self.redact_value(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact_value(v) for v in value]
        return value

    def _start_readers(self) -> None:
        """Drain stdout into a queue and stderr into a tail buffer.

        Reads on a pipe can't time out, so they happen on daemon threads and
        ``send_request`` waits on the queue instead. Draining stderr also stops
        a chatty server from filling the pipe and blocking mid-write.
        """
        self._stdout_lines = queue.Queue(maxsize=_STDOUT_QUEUE_LINES)
        self._stderr_tail = collections.deque(maxlen=_STDERR_TAIL_LINES)
        threading.Thread(
            target=self._pump_stdout,
            args=(self._process.stdout, self._stdout_lines),
            name=f"mcp-stdout:{self._process.pid}",
            daemon=True,
        ).start()
        self._stderr_reader = threading.Thread(
            target=self._pump_stderr,
            args=(self._process.stderr, self._stderr_tail),
            name=f"mcp-stderr:{self._process.pid}",
            daemon=True,
        )
        self._stderr_reader.start()
        threading.Thread(
            target=self._watch_children,
            args=(self._process,),
            name=f"mcp-children:{self._process.pid}",
            daemon=True,
        ).start()

    def _watch_children(self, proc: subprocess.Popen) -> None:
        # Once the parent exits, POSIX re-parents its children and they can no
        # longer be found from it, so they are recorded while it runs.
        # A scan walks the whole process table, so it backs off once the
        # server's tree has settled and speeds up again when it changes.
        server = self._ps_server
        interval = _CHILD_SCAN_S
        while self._process is proc and server is not None and server.is_running():
            known = len(self._children)
            self._track_children()
            changed = len(self._children) != known
            interval = (
                _CHILD_SCAN_S if changed else min(interval * 2, _CHILD_SCAN_MAX_S)
            )
            time.sleep(interval)

    @staticmethod
    def _discard_rest_of_line(stream) -> None:
        """Read through the next newline without keeping any of it."""
        while True:
            part = stream.readline(65536)
            if not isinstance(part, str) or not part or part.endswith("\n"):
                return

    @staticmethod
    def _pump_stdout(stream, lines: queue.Queue) -> None:
        try:
            while True:
                line = stream.readline()
                if not isinstance(line, str) or not line:
                    break
                lines.put(line)
        except (OSError, ValueError) as e:
            logger.debug("MCP server stdout closed: %s", e)
        finally:
            lines.put(_EOF)

    def _pump_stderr(self, stream, tail: collections.deque) -> None:
        try:
            while True:
                # Bounded: a server writing endlessly without a newline must
                # not grow GAIA's memory with it.
                window = self._stderr_window
                line = stream.readline(window)
                if not isinstance(line, str) or not line:
                    return
                ended = line.endswith("\n")
                if not ended and len(line) >= window:
                    self._discard_rest_of_line(stream)
                body = line[:-1] if ended else line
                # The original is cut, not the cleaned text: a secret across
                # the cut is still matched whole, within the window.
                kept = self._clean(body, _STDERR_LINE_CHARS)
                if len(body) > _STDERR_LINE_CHARS or not (ended or len(line) < window):
                    kept += " [line cut]"
                tail.append(kept + "\n")
        except (OSError, ValueError) as e:
            logger.debug("MCP server stderr closed: %s", e)

    def _build_argv(self) -> List[str]:
        """Resolve this transport's command into an argv list for ``shell=False``.

        Returns:
            List[str]: Program path (resolved via PATH when possible) plus args.

        Raises:
            ValueError: If a single-string command cannot be tokenized safely.
        """
        if self.args:
            program, extra = self.command, list(self.args)
        else:
            tokens = _split_command_string(self.command)
            program, extra = tokens[0], tokens[1:]

        # Resolve via PATH (handles Windows .cmd/.bat extensions). Keep this —
        # Popen needs the .cmd suffix to launch npx-style shims with shell=False.
        resolved = shutil.which(program)
        return [resolved or program] + extra

    def connect(self) -> bool:
        """Launch the MCP server subprocess.

        Returns:
            bool: True if process started successfully

        Raises:
            ValueError: If the configured command cannot be tokenized safely.
            RuntimeError: If the process dies immediately after launch.
        """
        if self._process is not None:
            logger.warning("Transport already connected")
            return True

        # Built outside the try so a bad command surfaces as an actionable
        # ValueError instead of a bare "failed to start" False.
        argv = self._build_argv()

        try:
            if self.debug:
                logger.debug("Starting MCP server '%s'", self.label)

            # Never None: an inherited environment would carry GAIA's own
            # credentials into the server.
            merged_env = child_env(self.env)

            self._started_at = time.time()
            self._process = subprocess.Popen(
                argv,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                env=merged_env,
                # close_fds=False lets Python use posix_spawn instead of
                # fork+exec.  fork() in a process that has background C
                # threads (e.g. after loading native extensions via
                # LemonadeManager) crashes the child with SIGSEGV on macOS.
                close_fds=False,
                **windows_hidden_popen_kwargs(),
            )

            logger.debug(f"MCP server process started (PID: {self._process.pid})")

            self._ps_server = self._own_process(self._process.pid)
            self._start_readers()

            # Brief pause to catch immediate crashes (e.g. missing binary, SIGSEGV
            # on load), recording children as it goes: a launcher that dies in
            # this window must still have them stopped.
            for _ in range(_STARTUP_CHECK_SLICES):
                self._track_children()
                time.sleep(_STARTUP_CHECK_S / _STARTUP_CHECK_SLICES)
            if self._process.poll() is not None:
                error = self._process_died_error()
                self._process = None
                raise error

            return True

        except RuntimeError:
            # Propagate crash diagnostics (from early death detection) to
            # caller, and "can't start new thread" from the readers.
            self._abandon_launch()
            raise
        except Exception as e:
            # The exception's text can name the program; its type cannot.
            logger.error(
                "Failed to start MCP server '%s': %s (errno %s)",
                self.label,
                type(e).__name__,
                getattr(e, "errno", None),
            )
            self._abandon_launch()
            return False

    def _abandon_launch(self) -> None:
        """Stop a server whose launch failed after Popen, so none runs unread."""
        proc, self._process = self._process, None
        self._stop_children()
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning(
                "Could not stop MCP server '%s' after a failed start: %s",
                self.label,
                e,
            )

    def disconnect(self) -> None:
        """Terminate the MCP server subprocess."""
        if self._process is None:
            return

        try:
            logger.debug(f"Terminating MCP server process (PID: {self._process.pid})")

            # If process already died, capture stderr for diagnostics
            if self._process.poll() is not None:
                stderr = self._read_stderr()
                if stderr:
                    logger.debug(
                        "Server stderr before disconnect:\n%s", self.redact(stderr)
                    )

            # npx and uvx run the real server as a child, which terminate()
            # alone would leave running, holding our pipes open.
            self._stop_children()
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Process did not terminate, killing...")
                self._process.kill()
                self._process.wait()

            logger.debug("MCP server process terminated")

        except Exception as e:
            logger.error(
                "Error disconnecting MCP server '%s': %s", self.label, type(e).__name__
            )

        finally:
            self._process = None

    def _own_process(self, pid: Any) -> Optional["psutil.Process"]:
        """*pid* as a psutil process, if it is the one this connect() started."""
        if not isinstance(pid, int):
            return None
        try:
            candidate = psutil.Process(pid)
            # A PID that already existed before the launch is someone else's.
            if candidate.create_time() >= self._started_at - 1:
                return candidate
        except psutil.Error as e:
            logger.debug("MCP server PID %s not visible to psutil: %s", pid, e)
        return None

    def _track_children(self) -> None:
        """Remember the processes the server has started so far.

        ``npx`` and ``uvx`` run the real server as a child. Remembering them
        lets disconnect() stop them even after the parent has exited, when
        its tree can no longer be walked.
        """
        server = self._ps_server
        if server is None:
            return
        try:
            children = server.children(recursive=True)
        except psutil.Error as e:
            logger.debug("Could not list MCP server '%s' children: %s", self.label, e)
            return
        for child in children:
            self._children.setdefault(child.pid, child)

    def _stop_children(self) -> None:
        """Stop every process the server started, before stopping the server."""
        # Only processes seen as the server's own descendants: finding orphans
        # by parent PID could hit another program once the OS reuses that PID.
        self._track_children()
        survivors = []
        for child in list(self._children.values()):
            try:
                # is_running() also checks create time, so a reused PID is spared.
                if child.is_running():
                    child.terminate()
                    survivors.append(child)
            except psutil.Error as e:
                logger.debug("MCP server child %s already gone: %s", child.pid, e)
        _, alive = psutil.wait_procs(survivors, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.Error as e:
                logger.warning(
                    "Could not stop child %s of MCP server '%s': %s",
                    child.pid,
                    self.label,
                    e,
                )
        self._children.clear()

    @staticmethod
    def _format_exit_code(exit_code: int) -> str:
        """Format an exit code with signal name if applicable.

        Negative exit codes on Unix indicate termination by signal.
        E.g. -11 means SIGSEGV, -9 means SIGKILL.

        Args:
            exit_code: Process exit code

        Returns:
            str: Formatted exit code, e.g. "-11 (SIGSEGV)" or "1"
        """
        if exit_code is None:
            return "None"
        text = str(exit_code)
        if exit_code < 0:
            try:
                sig = signal.Signals(-exit_code)
                text += f" ({sig.name})"
            except ValueError:
                pass
        return text

    def _read_stderr(self) -> str:
        """Read available stderr from the process without blocking.

        Only reads when the process has exited (poll() is not None) to
        prevent blocking on a live process's stderr pipe.

        Returns:
            str: Stderr content (truncated to last 2000 chars), or empty string
        """
        if self._process is None or self._process.stderr is None:
            return ""
        # Only read stderr from a dead process to avoid blocking
        if self._process.poll() is None:
            return ""
        if self._stderr_tail is not None:
            # The process is gone, so its stderr reader reaches EOF promptly.
            if self._stderr_reader is not None:
                self._stderr_reader.join(timeout=1)
            stderr = "".join(self._stderr_tail)
            return self.redact(stderr.strip())[-2000:] if stderr else ""
        try:
            stderr = self._process.stderr.read()
        except (OSError, ValueError) as e:
            logger.debug("Could not read the MCP server's stderr: %s", e)
            return ""
        return self.redact(stderr.strip())[-2000:] if stderr else ""

    def _process_died_error(self) -> RuntimeError:
        """Build a RuntimeError with exit code, signal name, and stderr from a dead process."""
        exit_code = self._process.returncode if self._process else None
        stderr = self._read_stderr()
        formatted_code = self._format_exit_code(exit_code)
        msg = f"MCP server '{self.label}' process died (exit code: {formatted_code})"
        if stderr:
            msg += f"\nServer stderr:\n{self.redact(stderr)}"
        if exit_code is not None and exit_code < 0:
            msg += (
                "\nHint: The server crashed with a signal. "
                "Try: uvx cache clean && uvx <server-command>"
            )
        return RuntimeError(msg)

    def send_request(
        self,
        method: str,
        params: Dict[str, Any] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a JSON-RPC request via stdin and wait for its reply.

        Notifications and replies to earlier, timed-out requests are skipped;
        a request from the server gets a "method not found" error back so it
        doesn't wait on us. The wait ends early once the agent abandons the
        tool call (``tool_cancelled()``), and a request given up on is
        cancelled on the server with ``notifications/cancelled``.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary
            timeout: Seconds to wait for the reply. Defaults to
                ``self.timeout``, or ``tool_call_timeout()`` for ``tools/call``
                when that is longer.

        Returns:
            dict: JSON-RPC response

        Raises:
            RuntimeError: If not connected, the process died, or the server
                rejected the request without naming it
            TimeoutError: If the server doesn't reply in time
            ToolCancelled: If the agent stopped waiting for this tool call
            ValueError: If the server writes something other than JSON-RPC
        """
        # One handle for the whole request: disconnect() may clear the attribute.
        proc = self._process
        if proc is None:
            raise RuntimeError("Transport not connected")

        # Check if process is still alive
        if proc.poll() is not None:
            raise self._process_died_error()

        if timeout is not None:
            wait_s = timeout
        elif method == "tools/call":
            wait_s = max(self.timeout, tool_call_timeout())
        else:
            wait_s = self.timeout
        # One deadline covers waiting for the lock and for the reply.
        deadline = time.monotonic() + wait_s

        self._take_request_lock(method, wait_s, deadline)
        try:
            request_id = self._request_id
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }

            if self.debug:
                logger.debug(f"Sending request: {json.dumps(request, indent=2)}")

            self._write(proc, request, deadline)
            try:
                response = self._await_reply(proc, request_id, method, wait_s, deadline)
            except (TimeoutError, ToolCancelled) as e:
                self._cancel_on_server(proc, request_id, str(e))
                raise

            if self.debug:
                logger.debug(
                    "Received response: %s",
                    json.dumps(self.redact_value(response), indent=2),
                )

            return response
        finally:
            self._request_lock.release()
            self._track_children()

    def _take_request_lock(self, method: str, wait_s: float, deadline: float) -> None:
        """Wait for the request in flight to finish, honouring Stop and the deadline."""
        while not self._request_lock.acquire(timeout=_POLL_INTERVAL_S):
            if tool_cancelled():
                raise ToolCancelled(
                    f"Stopped waiting to send '{method}' to MCP server "
                    f"'{self.label}': the tool call was cancelled."
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"MCP server '{self.label}' was still busy with another "
                    f"request after {wait_s:g}s, so '{method}' was never sent."
                )

    def _write(
        self,
        proc: subprocess.Popen,
        message: Dict[str, Any],
        deadline: Optional[float] = None,
        honour_cancel: bool = True,
    ) -> None:
        """Write one message; a server that stops reading can't block us for good.

        The write runs on a helper thread because a pipe write can't time out.
        One that doesn't finish by *deadline* has left a partial message on
        the pipe, so the server is stopped: nothing after it could be framed.
        """
        payload = json.dumps(message) + "\n"
        outcome: Dict[str, BaseException] = {}

        def _send() -> None:
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except BaseException as e:  # noqa: BLE001 — re-raised below
                outcome["error"] = e

        writer = threading.Thread(target=_send, name="mcp-stdin", daemon=True)
        writer.start()
        end = time.monotonic() + self.timeout if deadline is None else deadline
        while writer.is_alive():
            writer.join(_POLL_INTERVAL_S)
            cancelled = honour_cancel and tool_cancelled()
            if writer.is_alive() and (cancelled or time.monotonic() >= end):
                why = "cancelled" if cancelled else "timed out"
                self.disconnect()
                raise (ToolCancelled if why == "cancelled" else TimeoutError)(
                    f"Writing to MCP server '{self.label}' {why}: it stopped "
                    "reading its input, so it was stopped. Reconnect it."
                )
        error = outcome.get("error")
        if error is None:
            return
        if isinstance(error, (BrokenPipeError, OSError, ValueError)):
            # Process likely died - include stderr in the error
            if proc.poll() is not None:
                raise self._process_died_error() from error
            raise RuntimeError(
                f"MCP server '{self.label}' closed its input unexpectedly"
            ) from error
        raise error

    def _cancel_on_server(
        self, proc: subprocess.Popen, request_id: int, reason: str
    ) -> None:
        """Tell the server to drop a request nobody is waiting for any more."""
        try:
            self._write(
                proc,
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": request_id, "reason": reason[:200]},
                },
                deadline=time.monotonic() + 2,
                honour_cancel=False,
            )
        except (RuntimeError, TimeoutError) as e:
            # The caller is already raising the reason it gave up.
            logger.warning(
                "Could not cancel request %s on MCP server '%s': %s",
                request_id,
                self.label,
                e,
            )

    def _await_reply(
        self,
        proc: subprocess.Popen,
        request_id: int,
        method: str,
        wait_s: float,
        deadline: float,
    ) -> Dict[str, Any]:
        while True:
            if tool_cancelled():
                raise ToolCancelled(
                    f"Stopped waiting for MCP server '{self.label}' to answer "
                    f"'{method}': the tool call was cancelled."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                hint = (
                    " A first run of npx -y or uvx downloads the server first; "
                    "set a longer 'timeout' (seconds) in its config if it needs one."
                    if method == "initialize"
                    else ""
                )
                raise TimeoutError(
                    f"MCP server '{self.label}' did not answer '{method}' within "
                    f"{wait_s:g}s. It may be hung or overloaded; check that it "
                    "starts and answers when run on its own from a terminal, "
                    f"then reconnect it.{hint}"
                )
            try:
                line = self._stdout_lines.get(timeout=min(remaining, _POLL_INTERVAL_S))
            except queue.Empty:
                # A child still holding the pipe open hides EOF; poll as well.
                if proc.poll() is not None and self._stdout_lines.empty():
                    raise self._closed_error(proc, method)
                continue

            if line is _EOF:
                raise self._closed_error(proc, method)
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"MCP server '{self.label}' wrote non-JSON to stdout while "
                    f"answering '{method}': {self.redact(line)[:200]!r}. Servers must log to "
                    "stderr, never stdout."
                ) from e
            if not isinstance(message, dict):
                raise ValueError(
                    f"MCP server '{self.label}' sent a JSON-RPC message that is "
                    f"not an object: {self.redact(line)[:200]!r}"
                )

            if "method" in message:
                self._handle_server_message(proc, message)
                continue
            if message.get("id") is None and "error" in message:
                # A parse error has no id to match; it answers what we just sent.
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise RuntimeError(
                    f"MCP server '{self.label}' rejected '{method}': "
                    f"{self.redact_value(detail)}"
                )
            if message.get("id") != request_id:
                logger.debug(
                    "MCP server '%s': dropping reply to id %r while waiting on %r",
                    self.label,
                    self.redact_value(message.get("id")),
                    request_id,
                )
                continue
            return message

    def _handle_server_message(
        self, proc: subprocess.Popen, message: Dict[str, Any]
    ) -> None:
        """Answer a server request; log a notification."""
        if "id" not in message:
            logger.debug(
                "MCP server '%s' notification: %s",
                self.label,
                self.redact_value(message["method"]),
            )
            return
        if message["method"] == "ping":
            self._write(proc, {"jsonrpc": "2.0", "id": message["id"], "result": {}})
            return
        self._write(
            proc,
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {
                    "code": _METHOD_NOT_FOUND,
                    "message": "GAIA's MCP client does not support "
                    f"'{message['method']}'",
                },
            },
        )

    def _closed_error(self, proc: subprocess.Popen, method: str) -> RuntimeError:
        """The error for a server whose stdout reached EOF mid-request."""
        if self._process is not proc:
            return RuntimeError(
                f"MCP server '{self.label}' was disconnected while answering "
                f"'{method}'."
            )
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return RuntimeError(
                f"MCP server '{self.label}' closed its output but is still "
                "running; reconnect it."
            )
        return self._process_died_error()

    def is_connected(self) -> bool:
        """Check if the subprocess is running.

        Returns:
            bool: True if process is alive
        """
        return self._process is not None and self._process.poll() is None
