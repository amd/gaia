# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Strip identifying material out of anything derived from a session corpus.

Transcripts carry absolute paths, the operator's username, branch names, hostnames,
credentials pasted into a prompt, and — worst for a regex — the names of people in a
meeting transcript somebody pasted in.  Every string leaving the builder goes through
:func:`Scrubber.text`; :func:`Scrubber.value` walks nested structures.

The rules below are deliberately aggressive: over-redacting a shell command costs a
record's usefulness, under-redacting costs a leak.  What a regex fundamentally cannot
do is remove arbitrary proper nouns, so :func:`is_pasted_third_party` exists to drop
those records outright rather than pretend they were cleaned.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Tuple

# Ordered specific-before-generic: a GitHub PAT is also a long hex-ish blob, and a
# home directory is also an absolute path.  First match wins, same as the error
# taxonomy in ``harvest``.
_SECRET_RULES: List[Tuple[str, Pattern[str], str]] = [
    (
        "secret_github",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
        "<REDACTED:github>",
    ),
    (
        "secret_github",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "<REDACTED:github>",
    ),
    ("secret_openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<REDACTED:apikey>"),
    (
        "secret_anthropic",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        "<REDACTED:apikey>",
    ),
    ("secret_aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<REDACTED:aws>"),
    (
        "secret_slack",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "<REDACTED:slack>",
    ),
    (
        "secret_jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        "<REDACTED:jwt>",
    ),
    (
        "secret_bearer",
        re.compile(
            r"(?i)\b(bearer|token|api[_-]?key|password|passwd|secret)"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9_\-./+]{16,})[\"']?"
        ),
        r"\1=<REDACTED:credential>",
    ),
    (
        "secret_private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<REDACTED:privatekey>",
    ),
]

# Meeting-transcript speaker lines, and *only* those.
#
# A bare ``Surname, Firstname`` rule was tried and withdrawn: it fired 9,722
# times, almost all of it ordinary prose. "Multi-Agent Systems, Trust, & Outcome"
# became "Multi-Agent <PERSON>, <PERSON>, & Outcome", and an ISO working-group
# roster lost half its words. It damaged thousands of records to catch a threat
# that :func:`is_pasted_third_party` already handles by dropping the record
# whole. Names in web-fetched public documents are public attribution, not this
# corpus's private identity — the DATASHEET says so rather than the regex
# pretending otherwise.
_PERSON_RULES: List[Tuple[str, Pattern[str], str]] = [
    (
        "person_name",
        re.compile(
            r"^[ \t]*[A-Z][a-z]{1,20}(?:,)?\s+[A-Z][a-z]{1,20}\s+\d{1,2}:\d{2}\s*$",
            re.MULTILINE,
        ),
        "<PERSON> <TIME>",
    ),
    (
        "person_name",
        re.compile(r"\b[A-Z][a-z]{1,20},\s+[A-Z][a-z]{1,20}\s+\d{1,2}:\d{2}\b"),
        "<PERSON> <TIME>",
    ),
]

# The local part must start alphanumeric.  Without that, a Python decorator on a
# diff line (``-@pytest.mark.parametrize``) reads as an address whose local part
# is the leading ``-``, and every parametrized test in the corpus gets mangled.
_EMAIL_RULE = (
    "email",
    re.compile(
        r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,24}\b"
    ),
)

_HOST_RULES: List[Tuple[str, Pattern[str], str]] = [
    # ``www.amd.com`` is a public website, not a machine.  Redacting it would
    # break legitimate documentation links without hiding anything.
    (
        "hostname",
        re.compile(r"\b(?!www\.)[A-Za-z0-9-]+\.(?:amd|xilinx)\.com\b"),
        "<HOST>",
    ),
    ("hostname", re.compile(r"\b(?:XSJ|XHD|XCO|SJC)[A-Za-z0-9-]{3,}\b"), "<HOST>"),
]

_INTERNAL_RULES: List[Tuple[str, Pattern[str], str]] = [
    # AMD-internal tracker keys look like ABC-12345 in prose.  GitHub issue refs
    # (#1234) are public and deliberately kept — they are how a record is traced.
    (
        "internal_id",
        re.compile(r"\b(?!HTTP\b|UTF\b|SHA\b)[A-Z]{2,6}-\d{3,7}\b"),
        "<INTERNAL_ID>",
    ),
]

# Signatures of pasted third-party material.  Two independent hits are required
# before a record is dropped, so a single stray colon-number does not nuke a
# legitimate prompt.
_PASTE_SIGNATURES: List[Tuple[str, Pattern[str]]] = [
    ("speaker_timestamps", re.compile(r"(?m)^[A-Z][a-z]+.{0,40}\s\d{1,2}:\d{2}\s*$")),
    ("meeting_recording", re.compile(r"(?i)meeting recording|-\d{8}_\d{6}UTC")),
    ("transcript_header", re.compile(r"(?i)\b\d+h \d+m \d+s\b")),
    ("many_speaker_lines", re.compile(r"(?m)^\s*[A-Z][a-z]+,\s+[A-Z][a-z]+\s")),
]

#: Free-text human fields are capped here.  Machine output is handled by the rules
#: above; pasted prose is the surface a regex cannot fully clean, so bound it.
FREE_TEXT_CAP = 1500

#: A prompt longer than this that also trips two paste signatures is treated as
#: pasted third-party content and its record is dropped.
PASTE_LENGTH_THRESHOLD = 2000

#: Speaker-with-timestamp lines are the signature that names people. Enough of
#: them is conclusive on its own — a 1 KB excerpt of a meeting transcript names
#: just as many people as a 50 KB one.
PASTE_SPEAKER_LINES = 5


@dataclass
class ScrubStats:
    """How many times each rule fired, aggregated across a build."""

    counts: Dict[str, int] = field(default_factory=dict)

    def bump(self, rule: str, n: int = 1) -> None:
        if n:
            self.counts[rule] = self.counts.get(rule, 0) + n

    def merge(self, other: "ScrubStats") -> None:
        for rule, n in other.counts.items():
            self.bump(rule, n)


class Scrubber:
    """Replace identifying material in strings and nested structures.

    ``username`` and ``workspace_root`` are supplied by the caller rather than
    detected, because a scrubber that guesses which token is the username is a
    scrubber that misses it on the one machine that matters.
    """

    def __init__(
        self,
        username: str,
        workspace_root: Optional[str] = None,
        extra_names: Optional[List[str]] = None,
    ) -> None:
        if not username:
            raise ValueError(
                "Scrubber requires the corpus username; pass the account name whose "
                "transcripts are being processed (e.g. Scrubber(username='alice')). "
                "Without it, absolute paths cannot be anonymised."
            )
        self.username = username
        self.workspace_root = workspace_root or "Work"
        self._user_rules = self._build_user_rules(username, extra_names or [])

    @staticmethod
    def _build_user_rules(
        username: str, extra_names: List[str]
    ) -> List[Tuple[str, Pattern[str], str]]:
        rules: List[Tuple[str, Pattern[str], str]] = []
        for name in [username, *extra_names]:
            if not name:
                continue
            rules.append(
                ("username", re.compile(re.escape(name), re.IGNORECASE), "<USER>")
            )
        return rules

    # -- path handling -------------------------------------------------------

    def _paths(self, text: str, stats: ScrubStats) -> str:
        """Collapse absolute paths, keeping the repo-relative tail.

        The tail is what makes a record useful — ``src/gaia/cli.py`` is the
        addressable part — so a Windows or POSIX home prefix is replaced with a
        ``<WORKSPACE>`` marker rather than the whole path being discarded.
        """
        user = re.escape(self.username)

        def _sub(pattern: str, repl: str, rule: str, value: str) -> str:
            new, n = re.subn(pattern, repl, value, flags=re.IGNORECASE)
            stats.bump(rule, n)
            return new

        # C:\Users\<user>\Work\<anything>  and the /c/Users/<user>/... git-bash form.
        text = _sub(
            rf"[A-Za-z]:[\\/]+Users[\\/]+{user}[\\/]+",
            "<WORKSPACE>/",
            "abs_path_win",
            text,
        )
        text = _sub(rf"/[a-z]/Users/{user}/", "<WORKSPACE>/", "abs_path_posix", text)
        text = _sub(rf"/home/{user}/", "<WORKSPACE>/", "abs_path_posix", text)
        text = _sub(rf"/Users/{user}/", "<WORKSPACE>/", "abs_path_posix", text)
        # Slugified project-directory names: C--Users-<user>-Work-gaia
        text = _sub(rf"[A-Za-z]--Users-{user}-", "<WORKSPACE>-", "abs_path_slug", text)
        # Any surviving absolute path naming *some* account: a different user, a
        # different drive, or an ancestor directory that carries no name at all.
        # Node's module resolution alone emits ``/c/Users/node_modules`` while
        # walking up, which the user-specific rules above cannot match.
        text = _sub(
            r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]*", "<PATH>", "abs_path_win", text
        )
        text = _sub(r"/[a-z]/Users/[A-Za-z0-9._-]*", "<PATH>", "abs_path_posix", text)
        text = _sub(r"/home/[A-Za-z0-9._-]*", "<PATH>", "abs_path_posix", text)
        text = _sub(
            r"(?<![A-Za-z0-9])/Users/[A-Za-z0-9._-]*", "<PATH>", "abs_path_posix", text
        )
        text = _sub(r"[A-Za-z]--Users-[A-Za-z0-9._-]*", "<PATH>", "abs_path_slug", text)
        return text

    # -- public API ----------------------------------------------------------

    def text(self, value: str, stats: Optional[ScrubStats] = None) -> str:
        """Scrub one string. Order is secrets → identity → paths → names."""
        if not value:
            return value
        stats = stats if stats is not None else ScrubStats()
        for rule, pattern, repl in _SECRET_RULES:
            value, n = pattern.subn(repl, value)
            stats.bump(rule, n)
        value, n = _EMAIL_RULE[1].subn("<EMAIL>", value)
        stats.bump(_EMAIL_RULE[0], n)
        for rule, pattern, repl in _HOST_RULES:
            value, n = pattern.subn(repl, value)
            stats.bump(rule, n)
        value = self._paths(value, stats)
        for rule, pattern, repl in self._user_rules:
            value, n = pattern.subn(repl, value)
            stats.bump(rule, n)
        for rule, pattern, repl in _PERSON_RULES:
            value, n = pattern.subn(repl, value)
            stats.bump(rule, n)
        for rule, pattern, repl in _INTERNAL_RULES:
            value, n = pattern.subn(repl, value)
            stats.bump(rule, n)
        return value

    def value(self, obj: Any, stats: Optional[ScrubStats] = None) -> Any:
        """Recursively scrub every string inside a JSON-shaped structure."""
        stats = stats if stats is not None else ScrubStats()
        if isinstance(obj, str):
            return self.text(obj, stats)
        if isinstance(obj, dict):
            return {
                self.text(str(k), stats): self.value(v, stats) for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [self.value(v, stats) for v in obj]
        return obj

    def free_text(self, value: str, stats: Optional[ScrubStats] = None) -> str:
        """Scrub and cap a human-authored field."""
        cleaned = self.text(value or "", stats)
        if len(cleaned) > FREE_TEXT_CAP:
            stats = stats if stats is not None else ScrubStats()
            stats.bump("free_text_capped")
            return cleaned[:FREE_TEXT_CAP] + " …[capped]"
        return cleaned


def is_pasted_third_party(text: str) -> Optional[str]:
    """Return the reason a prompt looks like pasted third-party material, else None.

    Regexes cannot anonymise arbitrary proper nouns, so records built on pasted
    meeting transcripts and the like are dropped instead of cleaned.

    Two ways to trip it, because length alone is the wrong gate — a short excerpt
    of a meeting transcript names just as many people as a long one:

    * enough speaker-with-timestamp lines to be a transcript, at any length; or
    * two independent signatures in a prompt long enough not to be a passing
      mention, so an ordinary long instruction survives.
    """
    if not text:
        return None
    speaker_lines = len(_PASTE_SIGNATURES[0][1].findall(text))
    if speaker_lines >= PASTE_SPEAKER_LINES:
        return f"speaker_timestamps x{speaker_lines}"
    if len(text) < PASTE_LENGTH_THRESHOLD:
        return None
    hits = [name for name, pattern in _PASTE_SIGNATURES if pattern.search(text)]
    if len(hits) >= 2:
        return "+".join(sorted(hits))
    return None
