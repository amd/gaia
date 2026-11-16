# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Find an MCP server's known secrets and clean them out of its output.

The secrets are exactly the values a server's launch config marks as secret
(``secrets_from_launch``); ``redact`` removes each of them in every encoding
it can arrive in, and nothing else.
"""

import bisect
import functools
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
# More than the longest one character of a secret can be written as within
# three decodes: an escape whose backslash is itself escaped, twice over.
LONGEST_FORM_PER_CHAR = 32
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
        reach = LONGEST_FORM_PER_CHAR * len(secret) + 16
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


def secrets_from_launch(
    tokens: Iterable[str], env: Optional[Dict[str, Any]] = None
) -> List[str]:
    """The known secret values in a server's launch argv and env, longest first.

    Exactly these, and nothing guessed from a value's shape:

    - the value of an env var whose name holds a secret word (``API_KEY``,
      ``SECRET_KEY_BASE``, ``PROXY_AUTHORIZATION``; the words are
      ``_SECRET_WORDS``, matched whole after splitting on ``_``, ``-``, ``.``
      and camelCase), unless its last word is a qualifier (``AUTH_MODE``,
      ``KEY_ID``) or its first a count (``MAX_TOKENS``);
    - the value after a flag named the same way, in ``--api-key v`` and
      ``--api-key=v`` forms (``--accessToken`` too; not ``--max-tokens``,
      ``--token-limit`` or ``--no-auth``);
    - ``KEY=VALUE`` where ``KEY`` is named the same way;
    - the userinfo of a URL (``user:pass@`` or ``TOKEN@``);
    - an Authorization or Bearer header value inside an argument.

    Values shorter than 4 characters, and booleans (true, false, yes, no, on,
    off), are not secrets: matching them would remove ordinary text.
    """
    tokens = list(tokens)
    env = env or {}
    found = {str(value) for name, value in env.items() if _is_secret_name(str(name))}
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
    for text in [*tokens, *(str(v) for v in env.values())]:
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


def merged_secret_spans(secrets: List[str], text: str) -> List[List[int]]:
    """Where *secrets* appear in *text*, merged and in order.

    Merged, so two secrets that overlap leave no piece of either.
    """
    spans = sorted(span for span in _secret_spans(secrets, text) if span[1] > span[0])
    merged: List[List[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def redact(text: str, secrets: List[str], limit: Optional[int] = None) -> str:
    """The first *limit* characters of *text* (all of it by default), with
    every one of *secrets* replaced by ``<redacted>``.

    A secret that starts before the limit is redacted whole, even where it
    runs past it.
    """
    limit = len(text) if limit is None else limit
    out, last = [], 0
    for start, end in merged_secret_spans(secrets, text):
        if start >= limit:
            break
        out += [text[last:start], "<redacted>"]
        last = end
    if last < limit:
        out.append(text[last:limit])
    return "".join(out)
