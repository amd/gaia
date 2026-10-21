# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``triage_logs``: the tool the ``log-triage`` skill provides, and its engine.

A small model handed a log folder reads the first page, counts the loudest
message and reports routine noise as the incident. This module does the
counting deterministically, in one streaming pass per file:

1. **Formats.** Each file's format is learned from a sample (head, middle and
   tail): JSON lines, syslog (RFC 3164 and 5424), Apache/Nginx access and
   error logs, glog, ``key=value``, and any line that starts with a timestamp
   (ISO 8601, ``YYYY-MM-DD HH:MM:SS,mmm``, ``dd/Mon/yyyy:...``, ctime, epoch
   seconds or milliseconds). Indented and stack-trace lines join the entry
   above them. Lines that fit nothing are counted and sampled, never dropped
   silently.
2. **Templates.** Variable parts (numbers, hex, UUIDs, IPs, paths, URLs, mixed
   ids) are masked, then templates that differ in one high-cardinality word
   are merged (Drain-like, bounded by a template cap).
3. **Classification.** Per template: count, severity, hosts, entities, span,
   presence, peak against its own median, first and last occurrence with
   ``file:line``. A template steady across most of the range is BASELINE
   whatever its level; one that is short-lived, new, spiking, escalating or
   hits many hosts at once is an ANOMALY.
4. **Bursts.** Buckets where errors and anomaly lines rise far above their
   median, merged into windows with exact first and last timestamps and the
   templates that occur only there.
5. **Entities.** Ids (request, session, block, job, host ...) taken only from
   anomaly templates, inside the anomaly's own window, with a check that
   warns when the flagged share looks like the baseline.

Memory is bounded by caps on templates, hosts, entities and time buckets;
whatever a cap cut off is reported in ``warnings``. Only the standard library
is used, so the tool runs anywhere the agent does.
"""

from __future__ import annotations

import csv
import fnmatch
import gzip
import io
import json
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from gaia.agents.base.tools import tool
from gaia.logger import get_logger
from gaia.skills import context as skill_context
from gaia.skills.errors import SkillPermissionError

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Limits. Every one of them is reported when it bites.
# ---------------------------------------------------------------------------

MAX_FILES = 500
MAX_TEMPLATES = 20_000
MAX_HOSTS_PER_TEMPLATE = 2_000
MAX_HOSTS_TOTAL = 20_000
MAX_CELL_HOSTS = 32
MAX_ENTITIES_PER_TEMPLATE = 20_000
MAX_ENTITIES_TOTAL = 200_000
MAX_ENTITY_BUCKET_PAIRS = 3_000_000
MAX_ENTITY_KINDS = 300
#: Entity lists up to this long ride inline in the compact result.
OUT_ENTITY_LIST = 150
MAX_LINE_CHARS = 8_192
MAX_MESSAGE_CHARS = 1_500
TARGET_BUCKETS = 720
MAX_BUCKETS = 3_000
DEFAULT_TIME_BUDGET_S = 540.0
SAMPLE_HEAD_LINES = 400
SAMPLE_MID_LINES = 200
SAMPLE_TAIL_BYTES = 64 * 1024
MERGE_MIN_VARIANTS = 8

# Classification thresholds (percent of the analysed time range unless noted).
BASELINE_MIN_SPAN_PCT = 60.0
BASELINE_MIN_QUARTERS = 3
SHORT_SPAN_PCT = 20.0
CLUSTER_WINDOW_FRAC = 0.10
CLUSTER_SHARE = 0.80
SPIKE_FACTOR = 6.0
SPIKE_MIN_LINES = 10
ESCALATION_FACTOR = 4.0
ESCALATION_MIN_COUNT = 20
NEW_TEMPLATE_MIN_FIRST_PCT = 10.0
NEW_TEMPLATE_EXPECTED_BEFORE = 5.0
BLAST_MIN_HOSTS = 3
BLAST_HOST_SHARE = 0.30
BURST_MIN_LINES = 8
#: A bucket needs this many lines to start a burst; the burst needs BURST_MIN_LINES.
BURST_MIN_BUCKET = 3
BURST_MEDIAN_FACTOR = 3.0
BURST_SIGMAS = 6.0
ENTITY_SHARE_WARN = 0.30
#: Below this share of parsed lines a file's format was probably misread.
MIN_PARSED_SHARE = 0.95
ENTITY_TEMPLATE_SHARE_DROP = 0.50

# Output caps: the compact result must fit a small model's context.
OUT_TEMPLATE_CHARS = 160
OUT_HOSTS = 5
OUT_FILES = 20
OUT_BURSTS = 5
OUT_TIMELINE = 15
OUT_BASELINE = 8
OUT_ENTITIES = 25
OUT_WARNINGS = 12
OUT_WARNING_CHARS = 300
OUT_SAMPLES = 2
OUT_SAMPLE_CHARS = 160
DEFAULT_TOP_N = 10
MAX_TOP_N = 50

#: Extensions that are never logs; skipped when a folder is walked.
NON_LOG_SUFFIXES = frozenset(
    {
        ".zip", ".7z", ".rar", ".tar", ".bz2", ".xz", ".zst", ".png", ".jpg",
        ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".docx", ".xlsx", ".pptx",
        ".exe", ".dll", ".so", ".dylib", ".pyc", ".class", ".jar", ".db",
        ".sqlite", ".parquet", ".pkl", ".npy", ".mp3", ".mp4", ".wav",
    }
)  # fmt: skip

OUTPUT_MARKER = "gaia.triage_logs"
BOM = "﻿"

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

LEVELS: Dict[str, int] = {
    "TRACE": 0, "DEBUG": 0, "DBG": 0, "FINE": 0, "FINER": 0, "FINEST": 0,
    "VERBOSE": 0, "INFO": 1, "INFORMATION": 1, "INFORMATIONAL": 1, "INF": 1,
    "NOTICE": 1, "CONFIG": 1, "WARN": 2, "WARNING": 2, "WRN": 2, "ERROR": 3,
    "ERR": 3, "SEVERE": 3, "FAILURE": 3, "CRITICAL": 4, "CRIT": 4, "FATAL": 4,
    "ALERT": 4, "EMERG": 4, "EMERGENCY": 4, "PANIC": 4,
}  # fmt: skip
SEVERITY_NAMES = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")

_KEYWORD_SEV = (
    (4, re.compile(r"\b(?:fatal|panic\w*|corrupt\w*|crash\w*|abort\w*|segfault|"
                   r"out of memory|outofmemory\w*|oom|kernel bug|machine check)\b", re.I)),
    (3, re.compile(r"\b(?:errors?|err|fail\w*|exception|timed? ?out|timeout|refused|"
                   r"denied|unreachable|traceback)\b", re.I)),
    (2, re.compile(r"\b(?:warn\w*|retry\w*|retrying|slow|degraded|deprecated)\b", re.I)),
)  # fmt: skip


def level_severity(level: Any) -> Optional[int]:
    """Map a level name or number to 0 (debug) .. 4 (fatal); None if unknown."""
    if level is None:
        return None
    if isinstance(level, bool):
        return None
    if isinstance(level, (int, float)):
        n = int(level)
        if n >= 10:  # bunyan/pino (10..60) and Python logging (10..50)
            return max(0, min(4, n // 10 - 1)) if n < 60 else 4
        return None
    text = str(level).strip().strip("[]()<>:|,").upper()
    if not text:
        return None
    if text in LEVELS:
        return LEVELS[text]
    stripped = text.rstrip("0123456789")  # apache trace1..trace8
    if stripped in LEVELS and stripped != text:
        return LEVELS[stripped]
    if text.isdigit():
        return level_severity(int(text))
    return None


def keyword_severity(message: str) -> int:
    """Severity from words in the message, for lines that carry no level."""
    for sev, pattern in _KEYWORD_SEV:
        if pattern.search(message):
            return sev
    return 1


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ],
        1,
    )
}
_EPOCH_MIN = 946_684_800  # 2000-01-01
_EPOCH_MAX = 4_102_444_800  # 2100-01-01

_FRAC = r"(?:[.,:](?P<f>\d{1,9}))?"
_TZ_ATTACHED = r"(?P<tz>Z|[+-]\d{2}:\d{2}|[+-]\d{4}|[+-]\d{2}(?![\d:]))?"
_TZ_SPACED = r"(?:\s(?P<tz2>[+-]\d{4}|UTC|GMT|Z)(?![\w]))?"

_TS_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "iso",
        re.compile(
            r"(?P<y>(?:19|20)\d{2})[-/.](?P<mo>\d{1,2})[-/.](?P<d>\d{1,2})"
            r"(?:[T ]+|[-_:])(?P<h>\d{1,2})[:.](?P<mi>\d{2})[:.](?P<s>\d{2})"
            + _FRAC
            + _TZ_ATTACHED
            + _TZ_SPACED
        ),
    ),
    (
        "compact",
        re.compile(
            r"(?P<y>(?:19|20)\d{2})(?P<mo>[01]\d)(?P<d>[0-3]\d)[T ](?P<h>[0-2]\d):?"
            r"(?P<mi>[0-5]\d):?(?P<s>[0-6]\d)(?![\d])" + _FRAC + _TZ_ATTACHED
        ),
    ),
    (
        "clf",
        re.compile(
            r"(?P<d>\d{1,2})/(?P<mon>[A-Za-z]{3})/(?P<y>\d{4}):(?P<h>\d{2}):(?P<mi>\d{2})"
            r":(?P<s>\d{2})" + _FRAC + r"(?:\s(?P<tz>[+-]\d{4}))?"
        ),
    ),
    (
        "dmy",
        re.compile(
            r"(?P<d>\d{1,2})[- ](?P<mon>[A-Za-z]{3})[- ](?P<y>\d{4})[ :T](?P<h>\d{2})"
            r":(?P<mi>\d{2}):(?P<s>\d{2})" + _FRAC + _TZ_ATTACHED + _TZ_SPACED
        ),
    ),
    (
        "ctime",
        re.compile(
            r"(?:[A-Za-z]{3},?\s+)?(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2})"
            r":(?P<mi>\d{2}):(?P<s>\d{2})"
            + _FRAC
            + r"\s+(?P<y>\d{4})(?!\d)"
            + _TZ_SPACED
        ),
    ),
    (
        "mdy_name",
        re.compile(
            r"(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{4})\s+(?P<h>\d{1,2}):"
            r"(?P<mi>\d{2}):(?P<s>\d{2})" + _FRAC + r"(?:\s?(?P<ampm>[AaPp][Mm]))?"
        ),
    ),
    (
        "us",
        re.compile(
            r"(?P<mo>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})[ ,]+(?P<h>\d{1,2}):(?P<mi>\d{2})"
            r"(?::(?P<s>\d{2}))?" + _FRAC + r"(?:\s?(?P<ampm>[AaPp][Mm]))?"
        ),
    ),
    (
        "syslog",
        re.compile(
            r"(?P<mon>[A-Za-z]{3})\s+(?P<d>\d{1,2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
            + _FRAC
        ),
    ),
    (
        "epoch",
        re.compile(
            r"(?P<ep>[12]\d{9}(?:\.\d{1,9})?|[12]\d{12}(?:\.\d+)?|[12]\d{15}|[12]\d{18})(?![\d])"
        ),
    ),
)
_TS_BY_NAME = dict(_TS_PATTERNS)
#: Formats that carry no year; the caller supplies one.
YEARLESS = frozenset({"syslog"})


def _shift_years(epoch: float, years: int) -> float:
    """``epoch`` moved by whole calendar years (Feb 29 lands on Feb 28)."""
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    try:
        moved = dt.replace(year=dt.year + years)
    except ValueError:
        moved = dt.replace(year=dt.year + years, day=28)
    return moved.timestamp()


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 1970-01-01 for a proleptic Gregorian date (H. Hinnant)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _tz_offset(tz: Optional[str]) -> Optional[int]:
    """Seconds east of UTC for ``Z``/``UTC``/``+02:00``/``-0500``/``+05``."""
    if not tz:
        return None
    tz = tz.strip()
    if tz in ("Z", "UTC", "GMT"):
        return 0
    sign = -1 if tz[0] == "-" else 1
    digits = tz[1:].replace(":", "")
    if len(digits) == 2:
        hh, mm = int(digits), 0
    elif len(digits) == 4:
        hh, mm = int(digits[:2]), int(digits[2:])
    else:
        return None
    if hh > 14 or mm > 59:
        return None
    return sign * (hh * 3600 + mm * 60)


@dataclass
class TsMatch:
    """A parsed timestamp: epoch seconds (UTC when the text had an offset)."""

    epoch: float
    end: int
    kind: str
    has_tz: bool
    offset: Optional[int]


def _build(m: "re.Match[str]", kind: str, year_hint: int) -> Optional[TsMatch]:
    g = m.groupdict()
    if kind == "epoch":
        raw = g["ep"]
        if "." in raw:
            whole, _, _ = raw.partition(".")
        else:
            whole = raw
        digits = len(whole)
        value = float(raw)
        if digits == 13:
            value /= 1e3
        elif digits == 16:
            value /= 1e6
        elif digits == 19:
            value /= 1e9
        if not _EPOCH_MIN <= value <= _EPOCH_MAX:
            return None
        return TsMatch(value, m.end(), kind, True, 0)
    try:
        if g.get("mon"):
            mo = _MONTHS.get(g["mon"].lower())
            if mo is None:
                return None
        else:
            mo = int(g["mo"])
        d = int(g["d"])
        y = int(g["y"]) if g.get("y") else year_hint
        h = int(g["h"])
        mi = int(g["mi"])
        s = int(g["s"]) if g.get("s") else 0
    except (TypeError, ValueError):
        return None
    if kind == "us" and mo > 12 and d <= 12:
        mo, d = d, mo
    ampm = g.get("ampm")
    if ampm:
        if h > 12:
            return None
        h = h % 12 + (12 if ampm.lower() == "pm" else 0)
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
        return None
    epoch = float(_days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s)
    frac = g.get("f")
    if frac:
        frac = frac[:6]
        epoch += int(frac) / (10 ** len(frac))
    tz = g.get("tz") or g.get("tz2")
    offset = _tz_offset(tz)
    if offset is not None:
        epoch -= offset
    return TsMatch(epoch, m.end(), kind, offset is not None, offset)


def parse_timestamp(
    text: str,
    pos: int = 0,
    *,
    prefer: Optional[str] = None,
    year_hint: Optional[int] = None,
    kinds: Optional[Sequence[str]] = None,
) -> Optional[TsMatch]:
    """Parse a timestamp that starts exactly at ``text[pos]``.

    Args:
        text: The line or value.
        pos: Where the timestamp must start.
        prefer: Format name to try first (the one the file's sample used).
        year_hint: Year for formats that omit it (syslog).
        kinds: Restrict to these format names.

    Returns:
        A :class:`TsMatch`, or None when nothing parses.
    """
    year = year_hint if year_hint is not None else datetime.now(timezone.utc).year
    order: List[Tuple[str, "re.Pattern[str]"]] = []
    if prefer and prefer in _TS_BY_NAME:
        order.append((prefer, _TS_BY_NAME[prefer]))
    for kind, pattern in _TS_PATTERNS:
        if kind != prefer and (kinds is None or kind in kinds):
            order.append((kind, pattern))
    for kind, pattern in order:
        m = pattern.match(text, pos)
        if m:
            built = _build(m, kind, year)
            if built is not None:
                return built
    return None


def parse_timestamp_value(
    value: Any, year_hint: Optional[int] = None
) -> Optional[TsMatch]:
    """A timestamp held in a JSON/kv field: a string, or epoch s/ms/us/ns."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        for scale in (1.0, 1e3, 1e6, 1e9):
            if _EPOCH_MIN <= v / scale <= _EPOCH_MAX:
                return TsMatch(v / scale, 0, "epoch", True, 0)
        return None
    text = str(value).strip()
    if not text:
        return None
    return parse_timestamp(text, 0, year_hint=year_hint)


def format_ts(epoch: Optional[float], utc_suffix: bool = False) -> Optional[str]:
    """ISO text for an epoch value; milliseconds only when present."""
    if epoch is None:
        return None
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    text = dt.strftime("%Y-%m-%d %H:%M:%S")
    ms = int(round((epoch - math.floor(epoch)) * 1000))
    if ms >= 1000:
        ms = 999
    if ms:
        text += f".{ms:03d}"
    return text + ("Z" if utc_suffix else "")


# ---------------------------------------------------------------------------
# Templates (masking)
# ---------------------------------------------------------------------------

_MASKS: Tuple[Tuple[Optional[str], "re.Pattern[str]", str], ...] = (
    ("://", re.compile(r"\b[a-zA-Z][\w+.-]*://[^\s\"'<>]+"), "<URL>"),
    ("@", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "<EMAIL>"),
    (
        "-",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (
        ":",
        re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}(?![\w:])"),
        "<IP>",
    ),
    (
        ".",
        re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?(?![\w.])"),
        "<IP>",
    ),
    ("\\", re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]*"), "<PATH>"),
    ("/", re.compile(r"(?<![\w/<>.:])(?:/[\w.@%+~-]+){2,}/?"), "<PATH>"),
    (None, re.compile(r"\b0[xX][0-9a-fA-F]+\b"), "<*>"),
    # Decimals and scientific notation as one value: 0.25, -1.5e-05.
    (".", re.compile(r"(?<![\w.])[-+]?\d+\.\d+(?:[eE][-+]?\d+)?(?![\w.])"), "<*>"),
    # An id after a separator, any length, digits only or mixed: req-12345678,
    # req-a3f9c2d1, sess-1a3f4b and node-07 all mask alike.
    (
        None,
        re.compile(r"(?<=[-_:#=])(?=[A-Za-z]*\d)[A-Za-z0-9]+(?![A-Za-z0-9])"),
        "<*>",
    ),
    (None, re.compile(r"\b(?=[a-fA-F]*\d)[0-9a-fA-F]{8,}\b"), "<*>"),
    (None, re.compile(r"\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{8,}\b"), "<*>"),
    (None, re.compile(r"\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"), "<*>"),
)
_PLACEHOLDER = re.compile(r"^<[A-Z*]+>$")


def mask_message(message: str) -> str:
    """Replace the variable parts of a message so equal events share a key."""
    text = message[:MAX_MESSAGE_CHARS]
    for hint, pattern, token in _MASKS:
        if hint is not None and hint not in text:
            continue
        text = pattern.sub(token, text)
    return " ".join(text.split())


_EXC_RE = re.compile(
    r"(?:Caused by:\s*)?((?:[A-Za-z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error|Throwable|Fault))\b"
)


def exception_class(text: str) -> Optional[str]:
    """The first ``SomethingException``/``SomethingError`` class name in text."""
    m = _EXC_RE.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

_ENTITY_KEYS = (
    "request_id|req_id|requestid|trace_id|traceid|span_id|session_id|sessionid|"
    "user_id|userid|user|username|job_id|jobid|task_id|taskid|order_id|block_id|"
    "blk_id|container_id|pod|upstream|upstream_addr|client_id|account_id|tenant|"
    "tenant_id|device_id|txn_id|transaction_id|correlation_id|correlationid|node_id|"
    "instance_id|host_id|shard|partition|session|sess|token|trace|request|req|txn|"
    "transaction|job|task|order|block|blk|container|device|account|correlation"
)
_KEY_SET = frozenset(_ENTITY_KEYS.split("|"))
_KV_CANDIDATE = re.compile(
    r"(?<![\w.])([A-Za-z][\w.]{0,30}?)\s*[=:]\s*\"?([^\s\",;}\])]+)"
)
_UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_IP_PORT_RE = re.compile(r"(?<![\w.:])(\d{1,3}(?:\.\d{1,3}){3}:\d{1,5})(?![\w.:])")
_IP_RE = re.compile(r"(?<![\w.:])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.:])")
#: word + separator + id with a digit: blk_-1234, job-17, req-a3f9c2d1, sess:x9k2.
_PREFIXED_ID_RE = re.compile(
    r"(?<![\w.-])([A-Za-z]{1,16})([-_:#])(-?)([A-Za-z0-9]*\d[A-Za-z0-9]*)(?![\w-])"
)
_HEX_ID_RE = re.compile(
    r"(?<![\w.:-])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)([0-9a-f]{8,})(?![\w.:-])"
)
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
_SHAPE_RE = {"N": r"\d+", "H": r"[0-9a-fA-F]+", "A": r"[A-Za-z0-9]+"}
#: Regex text reported for the fixed id kinds.
FIXED_KIND_REGEX = {
    "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "ip:port": r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}",
    "ip": r"\d{1,3}(?:\.\d{1,3}){3}",
    "hex": r"[0-9a-f]{8,}",
}


def _id_shape(token: str) -> str:
    if token.isdigit():
        return "N"
    if all(c in _HEX_CHARS for c in token):
        return "H"
    return "A"


def auto_entities(text: str, *, kv_only: bool = False) -> List[Tuple[str, str]]:
    """Every id-shaped token in ``text`` with the kind of id it is.

    Kinds are learned from the token's shape, not a fixed vocabulary:
    ``blk_N`` / ``req-H`` / ``sess:A`` (a word, a separator and an id with a
    digit, keyed by the word), ``request_id=`` (an id-named field), ``uuid``,
    ``hex``, ``ip:port`` and ``ip``. Which kinds name the anomalous entities
    is decided after the whole log is read (:func:`build_entities`).
    """
    found: List[Tuple[str, str]] = []
    seen: set = set()

    def add(value: str, kind: str) -> None:
        value = value.rstrip(".,;:")[:120]
        if value and value not in seen:
            seen.add(value)
            found.append((value, kind))

    if "=" in text or ":" in text:
        for m in _KV_CANDIDATE.finditer(text):
            key = m.group(1).lower()
            if (
                key in _KEY_SET
                or key.endswith("_id")
                or key.endswith("id")
                and len(key) > 3
            ):
                add(m.group(2), f"{key}=")
    if kv_only:
        return found
    if text.count("-") >= 4:
        for m in _UUID_RE.finditer(text):
            add(m.group(1).lower(), "uuid")
    if "." in text:
        if ":" in text:
            for m in _IP_PORT_RE.finditer(text):
                add(m.group(1), "ip:port")
        for m in _IP_RE.finditer(text):
            add(m.group(1), "ip")
    if "-" in text or "_" in text or ":" in text or "#" in text:
        for m in _PREFIXED_ID_RE.finditer(text):
            prefix, sep, neg, ident = m.groups()
            add(m.group(0), f"{prefix.lower()}{sep}{_id_shape(ident)}")
    for m in _HEX_ID_RE.finditer(text):
        add(m.group(1), "hex")
    return found


def kind_regex(kind: str, examples: Sequence[str] = ()) -> str:
    """A regex for an id kind, to pass back as ``entity_regex``."""
    if kind in FIXED_KIND_REGEX:
        return f"({FIXED_KIND_REGEX[kind]})"
    if kind.endswith("="):
        return rf"{re.escape(kind[:-1])}\s*[=:]\s*\"?([^\s\",;}}\])]+)"
    if kind == "entity_regex":
        return ""
    prefix, sep, shape = kind[:-2], kind[-2], kind[-1]
    sample = next((e for e in examples if e.lower().startswith(prefix)), "")
    word = sample[: len(prefix)] if sample else prefix
    negative = any(e[len(prefix) + 1 : len(prefix) + 2] == "-" for e in examples)
    return f"({re.escape(word)}{sep}{'-?' if negative else ''}{_SHAPE_RE[shape]})"


_ID_KEY_RE = re.compile(r"^(?:" + _ENTITY_KEYS + r")$|(?:_id|Id|ID)$", re.I)


def _regex_entities(patterns: Sequence["re.Pattern[str]"], text: str) -> List[str]:
    found: List[str] = []
    for pattern in patterns:
        for m in pattern.finditer(text):
            if "entity" in pattern.groupindex:
                value = m.group("entity")
            elif pattern.groups:
                value = next((g for g in m.groups() if g), None)
            else:
                value = m.group(0)
            if value:
                value = value.strip().rstrip(".,;:")
                if value and value not in found:
                    found.append(value)
    return found


# ---------------------------------------------------------------------------
# Line records and per-format parsers
# ---------------------------------------------------------------------------


class Rec:
    """One parsed log entry (the header line of a possibly multi-line event)."""

    __slots__ = (
        "ts",
        "has_tz",
        "offset",
        "sev",
        "level",
        "host",
        "msg",
        "prefix",
        "ents",
        "exc",
        "ts_kind",
    )

    def __init__(self) -> None:
        self.ts: Optional[float] = None
        self.has_tz = False
        self.offset: Optional[int] = None
        self.sev: Optional[int] = None
        self.level: Optional[str] = None
        self.host: Optional[str] = None
        self.msg = ""
        self.prefix = ""
        self.ents: List[Tuple[str, str]] = []
        self.exc: Optional[str] = None
        self.ts_kind: Optional[str] = None


_CONT_RE = re.compile(
    r"^(?:\s|at\s|Caused by:|\.\.\.\s*\d+\s+(?:more|common)|Traceback \(|During handling|"
    r"The above exception|(?:[A-Za-z_$][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error|Throwable)(?::|$)|"
    r"[}\])]|\^)"
)
_HOST_SHAPE = re.compile(
    r"^(?:(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9-]+)*|\d{1,3}(?:\.\d{1,3}){3})$"
)
_KV_RE = re.compile(r"([A-Za-z_@][\w.@-]*)=(\"(?:[^\"\\]|\\.)*\"|\S*)")
_TS_KEYS = (
    "@timestamp", "timestamp", "time", "ts", "datetime", "date", "eventtime",
    "event_time", "asctime", "logtime", "log_time", "t", "created", "created_at",
    "timegenerated", "timecreated", "time_local",
)  # fmt: skip
_LEVEL_KEYS = (
    "level", "severity", "lvl", "loglevel", "log_level", "levelname", "level_name",
    "log.level", "severitytext", "severity_text", "@level", "priority", "status_level",
)  # fmt: skip
_MSG_KEYS = (
    "message",
    "msg",
    "log",
    "text",
    "event",
    "body",
    "description",
    "@message",
)
_HOST_KEYS = (
    "host", "hostname", "host.name", "host.hostname", "node", "nodename", "node_name",
    "instance", "pod", "pod_name", "server", "machine", "computer", "source_host",
)  # fmt: skip
_SERVICE_KEYS = (
    "service", "service.name", "service_name", "app", "application", "component",
    "logger", "logger_name", "loggername", "module", "source",
)  # fmt: skip
_EXC_KEYS = (
    "exception", "exc_info", "error", "err", "stack", "stack_trace", "stacktrace",
    "exception.type", "error.type", "exc", "error.kind", "exception.class", "throwable",
)  # fmt: skip
_RESERVED_KEYS = frozenset(_TS_KEYS + _LEVEL_KEYS + _MSG_KEYS + _HOST_KEYS)


def _flatten(obj: Dict[str, Any], prefix: str = "", depth: int = 0) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, value in obj.items():
        name = f"{prefix}{key}".lower() if prefix else str(key).lower()
        if isinstance(value, dict) and depth < 2:
            flat.update(_flatten(value, name + ".", depth + 1))
            leaf_view = _flatten(value, "", depth + 1)
            for leaf, leaf_value in leaf_view.items():
                flat.setdefault(f"{key}.{leaf}".lower(), leaf_value)
        else:
            flat[name] = value
    for name in list(flat):
        if "." in name:
            leaf = name.rsplit(".", 1)[1]
            flat.setdefault(leaf, flat[name])
    return flat


def _first(flat: Dict[str, Any], keys: Sequence[str]) -> Tuple[Optional[str], Any]:
    for key in keys:
        if key in flat and flat[key] not in (None, ""):
            return key, flat[key]
    return None, None


def _exc_from_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for k in ("type", "class", "name", "kind"):
            if value.get(k):
                return str(value[k])[:120]
        return None
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value[:3])
    text = str(value)
    found = exception_class(text)
    if found:
        return found
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if (
        first_line
        and len(first_line) <= 80
        and re.match(r"^[A-Za-z_][\w.$]*$", first_line)
    ):
        return first_line
    return None


class FileContext:
    """Per-file parsing state, learned from the sample."""

    def __init__(self, idx: int, path: Path, year_hint: int) -> None:
        self.idx = idx
        self.path = path
        self.name = path.name
        self.stem = path.name.split(".")[0] or path.name
        self.year = year_hint
        self.year_bumps = 0
        self.prefer_ts: Optional[str] = None
        self.level_pos: Optional[int] = None
        self.host_pos: Optional[int] = None
        self.formats: List[str] = []
        self.last_ts: Optional[float] = None
        self.last_raw: Optional[float] = None
        # A yearless log whose first line lies after the file's own mtime was
        # written last year (a December log copied in January).
        self.year_back = 0


def _apply_mapping(flat: Dict[str, Any], ctx: FileContext, rec: Rec) -> Rec:
    """Fill a record from a JSON object or a ``key=value`` line."""
    _, ts_value = _first(flat, _TS_KEYS)
    if ts_value is not None:
        tm = parse_timestamp_value(ts_value, ctx.year)
        if tm is not None:
            rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
                tm.epoch,
                tm.has_tz,
                tm.offset,
                tm.kind,
            )
    _, level = _first(flat, _LEVEL_KEYS)
    if level is not None:
        rec.level = str(level).upper()[:16]
        rec.sev = level_severity(level)
    _, host = _first(flat, _HOST_KEYS)
    _, service = _first(flat, _SERVICE_KEYS)
    rec.host = (
        str(host)[:120]
        if host is not None
        else (str(service)[:120] if service is not None else None)
    )
    if service is not None:
        rec.prefix = f"[{str(service)[:60]}] "
    _, msg = _first(flat, _MSG_KEYS)
    if msg is not None and not isinstance(msg, (dict, list)):
        rec.msg = str(msg)
    else:
        rest = [k for k in flat if k not in _RESERVED_KEYS and "." not in k][:12]
        rec.msg = "fields: " + ",".join(sorted(rest))
    for key in _EXC_KEYS:
        if key in flat and flat[key] not in (None, "", False):
            exc = _exc_from_value(flat[key])
            if exc:
                rec.exc = exc
                break
    for key, value in flat.items():
        if (
            key in _RESERVED_KEYS
            or isinstance(value, (dict, list, bool))
            or value in (None, "")
        ):
            continue
        leaf = key.rsplit(".", 1)[-1]
        if _ID_KEY_RE.search(leaf) and key not in _SERVICE_KEYS:
            text = str(value)[:120]
            if all(text != v for v, _ in rec.ents):
                rec.ents.append((text, f"{leaf}="))
    return rec


def _parse_json(line: str, ctx: FileContext) -> Optional[Rec]:
    s = line.lstrip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return _apply_mapping(_flatten(obj), ctx, Rec())


def _parse_logfmt(line: str, ctx: FileContext) -> Optional[Rec]:
    pairs = _KV_RE.findall(line)
    if len(pairs) < 3:
        return None
    covered = sum(len(k) + len(v) + 1 for k, v in pairs)
    if covered < 0.6 * len(line.strip()):
        return None
    flat: Dict[str, Any] = {}
    for key, value in pairs:
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1].replace('\\"', '"')
        flat.setdefault(key.lower(), value)
    if not any(k in flat for k in _TS_KEYS):
        return None
    return _apply_mapping(flat, ctx, Rec())


_SYSLOG5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>\d{1,2} (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<proc>\S+) "
    r"(?P<msgid>\S+) (?P<sd>-|(?:\[(?:[^\]\\]|\\.)*\])+)\s?(?P<msg>.*)$"
)
_SYSLOG_TAG_RE = re.compile(
    r"^(?P<tag>[\w./@()-]+?)(?:\[(?P<pid>\d+)\])?: ?(?P<msg>.*)$"
)
_LEADING_LEVEL_RE = re.compile(r"^[\[<(]?([A-Za-z]{3,11})[\]>)]?[:\s]")


def _pri_severity(pri: str) -> Optional[int]:
    try:
        sev = int(pri) & 7
    except ValueError:
        return None
    return 4 if sev <= 2 else 3 if sev == 3 else 2 if sev == 4 else 1 if sev <= 6 else 0


def _leading_level(rec: Rec, msg: str) -> None:
    m = _LEADING_LEVEL_RE.match(msg)
    if m:
        sev = level_severity(m.group(1))
        if sev is not None:
            rec.level, rec.sev = m.group(1).upper(), sev


def _parse_syslog5424(line: str, ctx: FileContext) -> Optional[Rec]:
    m = _SYSLOG5424_RE.match(line)
    if not m:
        return None
    tm = parse_timestamp(m.group("ts"), 0, kinds=("iso",))
    rec = Rec()
    if tm is not None:
        rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
            tm.epoch,
            tm.has_tz,
            tm.offset,
            tm.kind,
        )
    elif m.group("ts") != "-":
        return None
    rec.host = m.group("host") if m.group("host") != "-" else None
    rec.sev = _pri_severity(m.group("pri"))
    rec.level = SEVERITY_NAMES[rec.sev] if rec.sev is not None else None
    msg = m.group("msg").lstrip(BOM)
    _leading_level(rec, msg)
    app = m.group("app")
    rec.prefix = f"{app}: " if app != "-" else ""
    rec.msg = msg
    return rec


def _parse_syslog3164(line: str, ctx: FileContext) -> Optional[Rec]:
    pos = 0
    pri = None
    if line.startswith("<"):
        close = line.find(">", 1, 5)
        if close == -1 or not line[1:close].isdigit():
            return None
        pri = line[1:close]
        pos = close + 1
    tm = parse_timestamp(
        line, pos, prefer="syslog", year_hint=ctx.year, kinds=("syslog", "iso")
    )
    if tm is None or tm.end >= len(line) or line[tm.end] != " ":
        return None
    rest = line[tm.end + 1 :]
    host, _, rest = rest.partition(" ")
    if not host or not rest:
        return None
    tag = _SYSLOG_TAG_RE.match(rest)
    if not tag:
        return None
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
        tm.epoch,
        tm.has_tz,
        tm.offset,
        tm.kind,
    )
    rec.host = host
    if pri is not None:
        rec.sev = _pri_severity(pri)
        rec.level = SEVERITY_NAMES[rec.sev] if rec.sev is not None else None
    msg = tag.group("msg")
    _leading_level(rec, msg)
    rec.prefix = f"{tag.group('tag')}: "
    rec.msg = msg
    return rec


_ACCESS_RE = re.compile(
    r"^(?P<client>\S+) (?P<ident>\S+) (?P<user>\S+(?: \S+)*?) \[(?P<ts>[^\]]+)\] "
    r"\"(?P<req>[^\"]*)\" (?P<status>\d{3}) (?P<size>\S+)(?P<rest>.*)$"
)
_PATH_SEG_ID = re.compile(r"^(?:[\w.-]*\d[\w.-]*|[\w-]{25,})$")


def _path_template(path: str) -> str:
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 200:
        path = path[:200]
    segments = path.split("/")
    out = []
    for seg in segments:
        if not seg:
            out.append(seg)
        elif _PATH_SEG_ID.match(seg):
            out.append("<*>")
        else:
            out.append(seg)
    return "/".join(out) or "/"


def _parse_access(line: str, ctx: FileContext) -> Optional[Rec]:
    m = _ACCESS_RE.match(line)
    if not m:
        return None
    tm = parse_timestamp(m.group("ts"), 0, kinds=("clf", "iso"))
    if tm is None:
        return None
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
        tm.epoch,
        tm.has_tz,
        tm.offset,
        tm.kind,
    )
    rec.host = m.group("client")
    status = int(m.group("status"))
    rec.sev = 3 if status >= 500 else 2 if status >= 400 else 1
    rec.level = f"HTTP {status // 100}xx"
    parts = m.group("req").split()
    if len(parts) >= 2:
        method, target = parts[0][:12], parts[1]
        rec.msg = f"{method} {_path_template(target)} {status}"
    else:
        rec.msg = f"<malformed request> {status}"
    rec.prefix = ""
    rec.ents = auto_entities(m.group("rest"), kv_only=True)
    rec.exc = None
    # The masked path is the template; masking must not touch it again.
    rec.msg = "\x00" + rec.msg
    return rec


_APACHE_ERR_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\] \[(?:(?P<mod>[\w-]+):)?(?P<lvl>\w+)\] "
    r"(?:\[pid (?P<pid>\d+)[^\]]*\] )?(?:\[client (?P<client>[^\]]+)\] )?(?P<msg>.*)$"
)


def _parse_apache_error(line: str, ctx: FileContext) -> Optional[Rec]:
    m = _APACHE_ERR_RE.match(line)
    if not m:
        return None
    tm = parse_timestamp(m.group("ts"), 0, kinds=("ctime", "iso"))
    sev = level_severity(m.group("lvl"))
    if tm is None or sev is None:
        return None
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
        tm.epoch,
        tm.has_tz,
        tm.offset,
        tm.kind,
    )
    rec.sev, rec.level = sev, m.group("lvl").upper()
    rec.prefix = f"[{m.group('mod')}] " if m.group("mod") else ""
    rec.msg = m.group("msg")
    return rec


_GLOG_RE = re.compile(
    r"^(?P<l>[IWEF])(?P<mo>\d{2})(?P<d>\d{2}) (?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<f>\d+))?\s+(?P<tid>\d+) (?P<src>[^\]\s]+)\] (?P<msg>.*)$"
)


def _parse_glog(line: str, ctx: FileContext) -> Optional[Rec]:
    m = _GLOG_RE.match(line)
    if not m:
        return None
    built = _build(m, "glog", ctx.year)
    if built is None:
        return None
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = built.epoch, False, None, "syslog"
    rec.sev = {"I": 1, "W": 2, "E": 3, "F": 4}[m.group("l")]
    rec.level = SEVERITY_NAMES[rec.sev]
    rec.prefix = m.group("src").split(":")[0] + ": "
    rec.msg = m.group("msg")
    return rec


def _strip_token(token: str) -> str:
    return token.strip("[]()<>{}:|,;")


def _parse_generic(line: str, ctx: FileContext) -> Optional[Rec]:
    pos = 0
    bracket = False
    if line.startswith("["):
        pos, bracket = 1, True
    elif line.startswith("<"):
        close = line.find(">", 1, 5)
        if close != -1 and line[1:close].isdigit():
            pos = close + 1
    tm = parse_timestamp(line, pos, prefer=ctx.prefer_ts, year_hint=ctx.year)
    if tm is None:
        return None
    end = tm.end
    if bracket:
        close = line.find("]", end, end + 40)
        if close == -1:
            return None
        end = close + 1
    if end < len(line) and line[end] not in " \t|,;]-:":
        return None
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
        tm.epoch,
        tm.has_tz,
        tm.offset,
        tm.kind,
    )
    rest = line[end:].lstrip(" \t|,;]-:")
    tokens = rest.split(" ")
    drop = set()
    if ctx.level_pos is not None and ctx.level_pos < len(tokens):
        sev = level_severity(_strip_token(tokens[ctx.level_pos]))
        if sev is not None:
            rec.sev, rec.level = sev, _strip_token(tokens[ctx.level_pos]).upper()
            drop.add(ctx.level_pos)
    if rec.sev is None:
        for i, token in enumerate(tokens[:5]):
            sev = level_severity(_strip_token(token))
            if sev is not None and _strip_token(token).isupper():
                rec.sev, rec.level = sev, _strip_token(token).upper()
                drop.add(i)
                break
    if ctx.host_pos is not None and ctx.host_pos < len(tokens):
        candidate = _strip_token(tokens[ctx.host_pos])
        if _HOST_SHAPE.match(candidate) and level_severity(candidate) is None:
            rec.host = candidate
            drop.add(ctx.host_pos)
    msg = " ".join(t for i, t in enumerate(tokens) if i not in drop)
    if msg.startswith("{"):
        try:
            obj = json.loads(msg)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            inner = _apply_mapping(_flatten(obj), ctx, Rec())
            rec.msg = inner.msg
            rec.prefix = inner.prefix
            rec.ents = inner.ents
            rec.exc = inner.exc
            if rec.sev is None and inner.sev is not None:
                rec.sev, rec.level = inner.sev, inner.level
            if rec.host is None:
                rec.host = inner.host
            return rec
    rec.msg = msg
    return rec


PARSERS: Dict[str, Callable[[str, FileContext], Optional[Rec]]] = {
    "json": _parse_json,
    "syslog5424": _parse_syslog5424,
    "access": _parse_access,
    "apache_error": _parse_apache_error,
    "glog": _parse_glog,
    "syslog": _parse_syslog3164,
    "logfmt": _parse_logfmt,
    "timestamped": _parse_generic,
}


class CustomParser:
    """Line parser built from the caller's ``timestamp_regex``."""

    def __init__(self, timestamp_regex: str) -> None:
        self.pattern = re.compile(timestamp_regex)
        names = set(self.pattern.groupindex)
        if (
            "ts" not in names
            and not {"year", "month", "day", "hour", "minute"} <= names
        ):
            if self.pattern.groups < 1:
                raise ValueError(
                    "timestamp_regex needs a named group (?P<ts>...), or groups "
                    "year/month/day/hour/minute[/second], or one capture group."
                )

    def __call__(self, line: str, ctx: FileContext) -> Optional[Rec]:
        m = self.pattern.search(line)
        if not m:
            return None
        g = m.groupdict()
        tm: Optional[TsMatch] = None
        if "year" in g and g.get("year"):
            try:
                y = int(g["year"])
                if y < 100:
                    y += 2000
                mon = g["month"]
                mo = int(mon) if mon.isdigit() else _MONTHS.get(mon[:3].lower(), 0)
                epoch = float(
                    _days_from_civil(y, mo, int(g["day"])) * 86400
                    + int(g["hour"]) * 3600
                    + int(g["minute"]) * 60
                    + int(g.get("second") or 0)
                )
                if g.get("fraction"):
                    epoch += float("0." + g["fraction"])
                tm = TsMatch(epoch, m.end(), "custom", False, None)
            except (TypeError, ValueError, KeyError):
                tm = None
        else:
            text = g.get("ts") if "ts" in g else m.group(1)
            if text:
                tm = parse_timestamp_value(text.strip(), ctx.year)
                if tm is None and text.strip().replace(".", "", 1).isdigit():
                    tm = parse_timestamp_value(float(text.strip()), ctx.year)
        if tm is None:
            return None
        rec = Rec()
        rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
            tm.epoch,
            tm.has_tz,
            tm.offset,
            "custom",
        )
        span_start, span_end = m.span("ts") if "ts" in g else m.span()
        rest = (line[:span_start] + " " + line[span_end:]).strip(" \t|,;[]-:")
        tokens = rest.split(" ")
        drop = None
        for i, token in enumerate(tokens[:5]):
            sev = level_severity(_strip_token(token))
            if sev is not None and _strip_token(token).isupper():
                rec.sev, rec.level, drop = sev, _strip_token(token).upper(), i
                break
        rec.msg = " ".join(t for i, t in enumerate(tokens) if i != drop)
        return rec


def _compile_optional(pattern: Optional[str], what: str) -> Optional["re.Pattern[str]"]:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{what} is not a valid regular expression: {exc}") from exc


def _group_value(m: "re.Match[str]", name: str) -> Optional[str]:
    if name in m.re.groupindex:
        return m.group(name)
    if m.re.groups:
        return next((g for g in m.groups() if g), None)
    return m.group(0)


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------


class _OwningGzip(gzip.GzipFile):
    """A GzipFile that also closes the file it reads from."""

    def close(self) -> None:
        inner = self.fileobj
        try:
            super().close()
        finally:
            if inner is not None:
                inner.close()


def _open_text(path: Path) -> io.TextIOBase:
    """Open a log for streaming text: gzip, UTF-16 and BOMs handled.

    Every read goes through :func:`gaia.skills.context.open_read`, so the
    agent's allowed folders and secret-file refusals apply to each file.
    """
    raw: Any = skill_context.open_read(path, "rb")
    if path.suffix.lower() == ".gz":
        raw = _OwningGzip(fileobj=raw, mode="rb")
    head = raw.peek(4)[:4] if hasattr(raw, "peek") else b""
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif head.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    return io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline=None)


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() == ".gz":
        skill_context.check_read(path)
        return False
    try:
        with skill_context.open_read(path, "rb") as f:
            chunk = f.read(8192)
    except OSError:
        return False
    if chunk.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    return b"\x00" in chunk


def _sample_lines(path: Path) -> List[str]:
    """Head, middle and tail lines of a file, without reading all of it."""
    lines: List[str] = []
    with _open_text(path) as f:
        for _ in range(SAMPLE_HEAD_LINES):
            line = f.readline()
            if not line:
                return [ln.rstrip("\r\n") for ln in lines]
            lines.append(line)
    if path.suffix.lower() == ".gz":
        return [ln.rstrip("\r\n") for ln in lines]
    size = path.stat().st_size
    if size > 4 * SAMPLE_TAIL_BYTES:
        with skill_context.open_read(path, "rb") as f:
            for offset in (size // 2, max(0, size - SAMPLE_TAIL_BYTES)):
                f.seek(offset)
                chunk = f.read(
                    SAMPLE_TAIL_BYTES if offset != size // 2 else SAMPLE_TAIL_BYTES // 2
                )
                text = chunk.decode("utf-8", errors="replace").splitlines()[1:]
                lines.extend(
                    text[:SAMPLE_MID_LINES]
                    if offset == size // 2
                    else text[-SAMPLE_MID_LINES:]
                )
    return [ln.rstrip("\r\n") for ln in lines]


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _year_hint(path: Path) -> int:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).year
    except (OSError, ValueError, OverflowError):
        return datetime.now(timezone.utc).year


def detect_format(
    sample: Sequence[str], ctx: FileContext, custom: Optional[CustomParser] = None
) -> Dict[str, Any]:
    """Pick the parser(s) for a file from its sample and learn its columns.

    Returns:
        ``{"format", "match_rate", "secondary": [...]}``; sets ``ctx.formats``,
        ``ctx.prefer_ts``, ``ctx.level_pos`` and ``ctx.host_pos``.
    """
    headers = [ln for ln in sample if ln.strip() and not _CONT_RE.match(ln)]
    if custom is not None:
        ok = sum(1 for ln in headers if custom(ln, ctx) is not None)
        rate = ok / len(headers) if headers else 0.0
        ctx.formats = ["custom"]
        return {"format": "custom", "match_rate": round(rate, 3), "secondary": []}
    if not headers:
        ctx.formats = ["plain"]
        return {"format": "plain", "match_rate": 0.0, "secondary": []}

    # Learn which timestamp layout the timestamped lines use first.
    kinds = Counter()
    for ln in headers[:200]:
        pos = 1 if ln.startswith("[") else 0
        tm = parse_timestamp(ln, pos, year_hint=ctx.year)
        if tm:
            kinds[tm.kind] += 1
    if kinds:
        ctx.prefer_ts = kinds.most_common(1)[0][0]
    _learn_columns(headers, ctx)

    rates: Dict[str, float] = {}
    for name, parser in PARSERS.items():
        ok = 0
        for ln in headers:
            try:
                if parser(ln, ctx) is not None:
                    ok += 1
            except (ValueError, IndexError):
                continue
        rates[name] = ok / len(headers)
    order = list(PARSERS)
    best = max(order, key=lambda n: (round(rates[n], 2), -order.index(n)))
    if rates[best] < 0.2:
        ctx.formats = ["plain"]
        return {"format": "plain", "match_rate": round(rates[best], 3), "secondary": []}
    secondary = [n for n in order if n != best and rates[n] >= 0.02]
    ctx.formats = [best] + secondary
    return {"format": best, "match_rate": round(rates[best], 3), "secondary": secondary}


def _learn_columns(headers: Sequence[str], ctx: FileContext) -> None:
    """Find the level and host columns of timestamp-led lines."""
    rows: List[List[str]] = []
    for ln in headers[:300]:
        pos = 1 if ln.startswith("[") else 0
        tm = parse_timestamp(ln, pos, prefer=ctx.prefer_ts, year_hint=ctx.year)
        if not tm:
            continue
        end = tm.end
        if pos == 1:
            close = ln.find("]", end, end + 40)
            end = close + 1 if close != -1 else end
        rows.append(ln[end:].lstrip(" \t|,;]-:").split(" ")[:6])
    if len(rows) < 3:
        return
    n = len(rows)
    level_hits = Counter()
    for toks in rows:
        for i, tok in enumerate(toks[:5]):
            if level_severity(_strip_token(tok)) is not None:
                level_hits[i] += 1
                break
    if level_hits:
        pos, hits = level_hits.most_common(1)[0]
        if hits >= 0.6 * n:
            ctx.level_pos = pos
    limit = ctx.level_pos if ctx.level_pos is not None else 2
    for i in range(min(limit, 3)):
        shaped = [
            _strip_token(t[i])
            for t in rows
            if len(t) > i
            and not t[i].startswith("[")
            and _HOST_SHAPE.match(_strip_token(t[i]))
            and level_severity(_strip_token(t[i])) is None
        ]
        if len(shaped) >= 0.9 * n and len(set(shaped)) <= max(1, 0.5 * n):
            ctx.host_pos = i
            break


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class _Tpl:
    """Running statistics for one template."""

    __slots__ = (
        "key", "count", "sev", "levels", "hosts", "hosts_capped", "ents", "ents_capped",
        "first", "first_loc", "last", "last_loc", "cells", "file_first", "no_ts", "sample",
        "tid", "cls", "reasons", "m",
    )  # fmt: skip

    def __init__(self, key: str) -> None:
        self.key = key
        self.count = 0
        self.sev = 0
        self.levels: Counter = Counter()
        self.hosts: Dict[str, int] = {}
        self.hosts_capped = False
        self.ents: Dict[str, set] = {}
        self.ents_capped = False
        self.first: Optional[float] = None
        self.first_loc: Optional[Tuple[int, int]] = None
        self.last: Optional[float] = None
        self.last_loc: Optional[Tuple[int, int]] = None
        # bucket -> [count, min_ts, max_ts, min_loc, hosts set]
        self.cells: Dict[int, list] = {}
        self.file_first: Dict[int, Tuple[float, int]] = {}
        self.no_ts = 0
        self.sample = ""
        self.tid = ""
        self.cls = "intermittent"
        self.reasons: List[str] = []
        self.m: Dict[str, Any] = {}


@dataclass
class FileReport:
    """Coverage for one input file."""

    path: str
    name: str
    format: str = "unknown"
    match_rate: float = 0.0
    secondary: List[str] = field(default_factory=list)
    lines: int = 0
    blank: int = 0
    entries: int = 0
    continuation: int = 0
    unparsed: int = 0
    no_timestamp: int = 0
    unparsed_samples: List[Tuple[int, str]] = field(default_factory=list)
    first: Optional[float] = None
    last: Optional[float] = None
    tz_aware: int = 0
    tz_naive: int = 0
    offsets: Counter = field(default_factory=Counter)
    truncated_at_line: Optional[int] = None
    error: Optional[str] = None
    year_inferred: bool = False
    out_of_order: int = 0


@dataclass
class TriageOptions:
    """Knobs for :func:`triage`. Regex overrides apply to every file."""

    glob: Optional[str] = None
    timestamp_regex: Optional[str] = None
    level_regex: Optional[str] = None
    host_regex: Optional[str] = None
    entity_regex: Optional[str] = None
    bucket: Optional[str] = None
    top_n: int = DEFAULT_TOP_N
    time_budget_s: float = DEFAULT_TIME_BUDGET_S
    max_templates: int = MAX_TEMPLATES


_BUCKET_LADDER = (
    1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600,
    43200, 86400, 172800, 604800,
)  # fmt: skip
_BUCKET_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)?\s*$",
    re.I,
)
_UNIT_S = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_bucket(text: Optional[str]) -> Optional[float]:
    """``"5min"`` / ``"30s"`` / ``"1h"`` / ``"300"`` -> seconds; None if empty."""
    if text is None or str(text).strip() == "":
        return None
    m = _BUCKET_RE.match(str(text))
    if not m:
        raise ValueError(
            f"bucket '{text}' is not a duration; use e.g. '30s', '5min', '1h' or '1d'."
        )
    value = float(m.group(1))
    unit = (m.group(2) or "s").lower()
    key = "ms" if unit == "ms" else unit[0]
    seconds = value * _UNIT_S[key]
    if seconds <= 0:
        raise ValueError("bucket must be longer than zero.")
    return seconds


def choose_bucket(span_s: float) -> float:
    """Smallest standard bucket that gives at most ~TARGET_BUCKETS buckets."""
    for b in _BUCKET_LADDER:
        if span_s / b <= TARGET_BUCKETS:
            return float(b)
    return float(_BUCKET_LADDER[-1])


def _median_with_zeros(values: Sequence[int], total: int) -> float:
    """Median of ``values`` padded with zeros up to ``total`` slots."""
    if total <= 0:
        return 0.0
    zeros = total - len(values)
    ordered = sorted(values)

    def at(k: int) -> int:
        return 0 if k < zeros else ordered[k - zeros]

    mid = total // 2
    if total % 2:
        return float(at(mid))
    return (at(mid - 1) + at(mid)) / 2.0


class Analyzer:
    """Streams files into template statistics, then classifies them."""

    def __init__(self, options: TriageOptions) -> None:
        self.opt = options
        self.custom = (
            CustomParser(options.timestamp_regex) if options.timestamp_regex else None
        )
        self.level_re = _compile_optional(options.level_regex, "level_regex")
        self.host_re = _compile_optional(options.host_regex, "host_regex")
        entity_re = _compile_optional(options.entity_regex, "entity_regex")
        self.entity_patterns: Tuple["re.Pattern[str]", ...] = (
            (entity_re,) if entity_re is not None else ()
        )
        self.custom_entities = entity_re is not None
        self.bucket_s: float = parse_bucket(options.bucket) or 60.0
        self.bucket_fixed = options.bucket not in (None, "")
        self.origin = 0.0
        self.tpls: Dict[str, _Tpl] = {}
        self.overflow: Optional[_Tpl] = None
        self.hosts: Dict[str, list] = {}
        self.hosts_capped = False
        # entity -> first (file, ts) per file, for the cross-file clock check;
        # None when there is only one file.
        self.all_ents: Dict[str, Optional[list]] = {}
        self.multi_file = False
        # entity -> kind (see auto_entities); kind -> a few example values.
        self.ent_kind: Dict[str, str] = {}
        self.kinds: Dict[str, List[str]] = {}
        self.kinds_capped = False
        self.ents_capped = False
        self.ent_pairs = 0
        self.ent_pairs_capped = False
        self.files: List[FileReport] = []
        self.warnings: List[str] = []
        self.level_totals: Counter = Counter()
        self.entries = 0
        self.started = time.monotonic()
        self.out_of_time = False
        self.gmin: Optional[float] = None
        self.gmax: Optional[float] = None

    # -- streaming ---------------------------------------------------------

    def _bucket(self, ts: float) -> int:
        return int((ts - self.origin) // self.bucket_s)

    def prepare(
        self, paths: Sequence[Path]
    ) -> List[Tuple[Path, FileContext, Dict[str, Any]]]:
        """Sample every file: format, columns, and the overall time range."""
        plans = []
        self.multi_file = len(paths) > 1
        lo: Optional[float] = None
        hi: Optional[float] = None
        for idx, path in enumerate(paths):
            ctx = FileContext(idx, path, _year_hint(path))
            report = FileReport(path=str(path), name=path.name)
            self.files.append(report)
            try:
                sample = _sample_lines(path)
            except (OSError, EOFError, gzip.BadGzipFile) as exc:
                report.error = f"could not read: {exc}"
                plans.append((path, ctx, {"format": "unreadable"}))
                continue
            info = detect_format(sample, ctx, self.custom)
            report.format, report.match_rate = info["format"], info["match_rate"]
            report.secondary = info["secondary"]
            recs = [
                r
                for r in (self._parse_line(ln, ctx) for ln in sample)
                if r is not None and r.ts is not None
            ]
            mtime = _mtime(path)
            yearless = [r.ts for r in recs if r.ts_kind in YEARLESS]
            if yearless and mtime is not None and yearless[0] > mtime + 2 * 86400:
                ctx.year_back = 1
            for rec in recs:
                ts = (
                    _shift_years(rec.ts, -ctx.year_back)
                    if rec.ts_kind in YEARLESS and ctx.year_back
                    else rec.ts
                )
                lo = ts if lo is None else min(lo, ts)
                hi = ts if hi is None else max(hi, ts)
            plans.append((path, ctx, info))
        if not self.bucket_fixed:
            span = (hi - lo) if lo is not None and hi is not None else 3600.0
            self.bucket_s = choose_bucket(max(span, 1.0))
        if lo is not None:
            self.origin = math.floor(lo / self.bucket_s) * self.bucket_s
        return plans

    def _parse_line(self, line: str, ctx: FileContext) -> Optional[Rec]:
        rec: Optional[Rec] = None
        if self.custom is not None:
            rec = self.custom(line, ctx)
        else:
            for name in ctx.formats:
                if name == "plain":
                    break
                rec = PARSERS[name](line, ctx)
                if rec is not None:
                    break
        if rec is None:
            return None
        if self.level_re is not None:
            m = self.level_re.search(line)
            if m:
                value = _group_value(m, "level")
                sev = level_severity(value)
                if sev is not None:
                    rec.sev, rec.level = sev, str(value).upper()
        if self.host_re is not None:
            m = self.host_re.search(line)
            if m:
                value = _group_value(m, "host")
                if value:
                    rec.host = value
        return rec

    def run_file(self, path: Path, ctx: FileContext) -> None:
        """Stream one file into the statistics."""
        report = self.files[ctx.idx]
        if report.error:
            return
        plain = ctx.formats == ["plain"]
        pending: Optional[Tuple[Rec, int, str]] = None
        try:
            with _open_text(path) as f:
                for n, raw in enumerate(f, 1):
                    report.lines = n
                    if (
                        n % 4096 == 0
                        and time.monotonic() - self.started > self.opt.time_budget_s
                    ):
                        report.truncated_at_line = n
                        self.out_of_time = True
                        break
                    line = raw.rstrip("\r\n")
                    if len(line) > MAX_LINE_CHARS:
                        line = line[:MAX_LINE_CHARS]
                    if not line.strip():
                        report.blank += 1
                        continue
                    if n == 1:
                        line = line.lstrip(BOM)
                    rec = None if plain else self._parse_line(line, ctx)
                    if rec is None and pending is not None and _CONT_RE.match(line):
                        report.continuation += 1
                        prev = pending[0]  # pylint: disable=unsubscriptable-object
                        if prev.exc is None:
                            exc = exception_class(line)
                            if exc:
                                prev.exc = exc
                        continue
                    if rec is None:
                        if plain:
                            rec = Rec()
                            rec.msg = line
                        else:
                            report.unparsed += 1
                            if len(report.unparsed_samples) < OUT_SAMPLES:
                                report.unparsed_samples.append(
                                    (n, line[:OUT_SAMPLE_CHARS])
                                )
                            continue
                    if pending is not None:
                        self._commit(*pending, ctx, report)
                    pending = (rec, n, line)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            report.error = f"stopped reading at line {report.lines}: {exc}"
        if pending is not None:
            self._commit(*pending, ctx, report)
        if ctx.year_bumps:
            report.year_inferred = True

    def _commit(
        self, rec: Rec, n: int, line: str, ctx: FileContext, report: FileReport
    ) -> None:
        if rec.ts is not None and rec.ts_kind in YEARLESS:
            # Syslog has no year: assume the file's year, roll over at New Year.
            if ctx.last_raw is not None and rec.ts < ctx.last_raw - 180 * 86400:
                ctx.year_bumps += 1
            ctx.last_raw = rec.ts
            shift = ctx.year_bumps - ctx.year_back
            if shift:
                rec.ts = _shift_years(rec.ts, shift)
            report.year_inferred = True
        report.entries += 1
        self.entries += 1
        if rec.sev is None:
            rec.sev = keyword_severity(rec.msg + (" " + rec.exc if rec.exc else ""))
            if rec.level is None:
                rec.level = SEVERITY_NAMES[rec.sev] if rec.sev != 1 else "-"
        level = rec.level or SEVERITY_NAMES[rec.sev]
        self.level_totals[level] += 1
        host = rec.host or ctx.stem
        msg = rec.msg
        if msg.startswith("\x00"):
            key = rec.prefix + msg[1:]
        else:
            key = rec.prefix + mask_message(msg)
        if rec.exc and rec.exc not in msg:
            key += " | " + rec.exc
        if not key.strip():
            key = "<empty message>"
        tpl = self.tpls.get(key)
        if tpl is None:
            if len(self.tpls) >= self.opt.max_templates:
                if self.overflow is None:
                    self.overflow = _Tpl("<templates beyond the limit>")
                tpl = self.overflow
            else:
                tpl = _Tpl(key)
                tpl.sample = msg.lstrip("\x00")[:OUT_TEMPLATE_CHARS]
                self.tpls[key] = tpl
        tpl.count += 1
        if rec.sev > tpl.sev:
            tpl.sev = rec.sev
        tpl.levels[level] += 1
        if host in tpl.hosts:
            tpl.hosts[host] += 1
        elif len(tpl.hosts) < MAX_HOSTS_PER_TEMPLATE:
            tpl.hosts[host] = 1
        else:
            tpl.hosts_capped = True
        loc = (ctx.idx, n)
        ts = rec.ts
        bucket = None
        if ts is None:
            tpl.no_ts += 1
            report.no_timestamp += 1
        else:
            if rec.has_tz:
                report.tz_aware += 1
                if rec.offset is not None:
                    report.offsets[rec.offset] += 1
            else:
                report.tz_naive += 1
            if ctx.last_ts is not None and ts < ctx.last_ts - 1.0:
                report.out_of_order += 1
            ctx.last_ts = ts
            report.first = ts if report.first is None else min(report.first, ts)
            report.last = ts if report.last is None else max(report.last, ts)
            self.gmin = ts if self.gmin is None else min(self.gmin, ts)
            self.gmax = ts if self.gmax is None else max(self.gmax, ts)
            if tpl.first is None or ts < tpl.first:
                tpl.first, tpl.first_loc = ts, loc
            if tpl.last is None or ts >= tpl.last:
                tpl.last, tpl.last_loc = ts, loc
            ff = tpl.file_first.get(ctx.idx)
            if ff is None or ts < ff[0]:
                if ff is not None or len(tpl.file_first) < 64:
                    tpl.file_first[ctx.idx] = (ts, n)
            bucket = self._bucket(ts)
            cell = tpl.cells.get(bucket)
            if cell is None:
                tpl.cells[bucket] = [1, ts, ts, loc, {host}]
            else:
                cell[0] += 1
                if ts < cell[1]:
                    cell[1], cell[3] = ts, loc
                if ts > cell[2]:
                    cell[2] = ts
                if len(cell[4]) < MAX_CELL_HOSTS:
                    cell[4].add(host)
            hs = self.hosts.get(host)
            if hs is None:
                if len(self.hosts) < MAX_HOSTS_TOTAL:
                    self.hosts[host] = [1, ts, ts]
                else:
                    self.hosts_capped = True
            else:
                hs[0] += 1
                hs[1] = min(hs[1], ts)
                hs[2] = max(hs[2], ts)
        if ts is None and host not in self.hosts and len(self.hosts) < MAX_HOSTS_TOTAL:
            self.hosts[host] = [1, None, None]
        # Entities.
        if self.custom_entities:
            ents = [
                (e, "entity_regex") for e in _regex_entities(self.entity_patterns, line)
            ]
        else:
            ents = list(rec.ents)
            if not msg.startswith("\x00"):
                known = {v for v, _ in ents}
                ents.extend(p for p in auto_entities(msg) if p[0] not in known)
        if ents:
            mark = bucket if bucket is not None else -(10**9)
            for e, kind in ents:
                e = e[:120]
                seen = self.all_ents.get(e, False)
                if seen is False:
                    if kind not in self.kinds and len(self.kinds) >= MAX_ENTITY_KINDS:
                        self.kinds_capped = True
                        continue
                    if len(self.all_ents) < MAX_ENTITIES_TOTAL:
                        self.all_ents[e] = (
                            [(ctx.idx, ts)]
                            if self.multi_file and ts is not None
                            else None
                        )
                        self.ent_kind[e] = kind
                        examples = self.kinds.setdefault(kind, [])
                        if len(examples) < 5:
                            examples.append(e)
                    else:
                        self.ents_capped = True
                elif seen is not None and ts is not None and len(seen) < 4:
                    if all(f != ctx.idx for f, _ in seen):
                        seen.append((ctx.idx, ts))
                bset = tpl.ents.get(e)
                if bset is None:
                    if len(tpl.ents) >= MAX_ENTITIES_PER_TEMPLATE:
                        tpl.ents_capped = True
                        continue
                    bset = tpl.ents[e] = set()
                if mark not in bset:
                    if self.ent_pairs < MAX_ENTITY_BUCKET_PAIRS:
                        bset.add(mark)
                        self.ent_pairs += 1
                    else:
                        self.ent_pairs_capped = True

    # -- post-processing ---------------------------------------------------

    def finalize(self) -> None:
        """Rebucket if the sample misjudged the range, then merge templates."""
        if self.gmin is None or self.gmax is None:
            return
        lo = self._bucket(self.gmin)
        hi = self._bucket(self.gmax)
        factor = 1
        if not self.bucket_fixed and (hi - lo + 1) > MAX_BUCKETS:
            target = choose_bucket(max(self.gmax - self.gmin, 1.0))
            factor = max(1, int(math.ceil(target / self.bucket_s)))
        elif self.bucket_fixed and (hi - lo + 1) > 20 * MAX_BUCKETS:
            factor = int(math.ceil((hi - lo + 1) / (20 * MAX_BUCKETS)))
            self.warnings.append(
                f"bucket {self.opt.bucket} gave {hi - lo + 1} buckets; widened {factor}x "
                f"to {self._fmt_duration(self.bucket_s * factor)}."
            )
        self._rebase(lo, factor)
        self._merge_templates()

    def _rebase(self, lo: int, factor: int) -> None:
        """Renumber buckets from 0 at the earliest line, coarsening by ``factor``."""

        def remap(idx: int) -> int:
            return (idx - lo) // factor

        for tpl in self._all_templates():
            new_cells: Dict[int, list] = {}
            for idx, cell in tpl.cells.items():
                j = remap(idx)
                cur = new_cells.get(j)
                if cur is None:
                    new_cells[j] = cell
                else:
                    _merge_cell(cur, cell)
            tpl.cells = new_cells
            for e, bset in tpl.ents.items():
                tpl.ents[e] = {remap(b) if b > -(10**9) else b for b in bset}
        self.origin = self.origin + lo * self.bucket_s
        self.bucket_s *= factor

    def _all_templates(self) -> Iterable[_Tpl]:
        yield from self.tpls.values()
        if self.overflow is not None:
            yield self.overflow

    def _merge_templates(self) -> None:
        """Merge templates that differ only in one high-cardinality word."""
        keys = list(self.tpls)
        groups: Dict[str, List[str]] = {}
        for key in keys:
            tokens = key.split(" ")
            if len(tokens) < 3 or len(tokens) > 40:
                continue
            sev_class = "S" if self.tpls[key].sev >= 3 else "s"
            for i, tok in enumerate(tokens):
                if _PLACEHOLDER.match(tok):
                    continue
                wild = " ".join(tokens[:i] + ["<*>"] + tokens[i + 1 :])
                groups.setdefault(f"{sev_class}{len(tokens)}|{i}|{wild}", []).append(
                    key
                )
        parent = {k: k for k in keys}

        def find(k: str) -> str:
            while parent[k] != k:
                parent[k] = parent[parent[k]]
                k = parent[k]
            return k

        merged_any = False
        for members in groups.values():
            if len(members) >= MERGE_MIN_VARIANTS:
                root = find(members[0])
                for other in members[1:]:
                    r = find(other)
                    if r != root:
                        parent[r] = root
                        merged_any = True
        if not merged_any:
            return
        clusters: Dict[str, List[str]] = {}
        for key in keys:
            clusters.setdefault(find(key), []).append(key)
        new: Dict[str, _Tpl] = {}
        for members in clusters.values():
            if len(members) == 1:
                new[members[0]] = self.tpls[members[0]]
                continue
            token_lists = [m.split(" ") for m in members]
            merged_tokens = [
                toks[0] if all(t == toks[0] for t in toks) else "<*>"
                for toks in zip(*token_lists)
            ]
            merged_key = " ".join(merged_tokens)
            base = self.tpls[members[0]]
            for other in members[1:]:
                _merge_tpl(base, self.tpls[other])
            base.key = merged_key
            if merged_key in new:
                _merge_tpl(new[merged_key], base)
            else:
                new[merged_key] = base
        self.tpls = new

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        if seconds >= 86400 and seconds % 86400 == 0:
            return f"{int(seconds // 86400)}d"
        if seconds >= 3600 and seconds % 3600 == 0:
            return f"{int(seconds // 3600)}h"
        if seconds >= 60 and seconds % 60 == 0:
            return f"{int(seconds // 60)}min"
        return f"{seconds:g}s"


def _merge_cell(into: list, other: list) -> None:
    into[0] += other[0]
    if other[1] < into[1]:
        into[1], into[3] = other[1], other[3]
    into[2] = max(into[2], other[2])
    for h in other[4]:
        if len(into[4]) >= MAX_CELL_HOSTS:
            break
        into[4].add(h)


def _merge_tpl(into: _Tpl, other: _Tpl) -> None:
    into.count += other.count
    into.sev = max(into.sev, other.sev)
    into.levels.update(other.levels)
    for h, c in other.hosts.items():
        if h in into.hosts:
            into.hosts[h] += c
        elif len(into.hosts) < MAX_HOSTS_PER_TEMPLATE:
            into.hosts[h] = c
        else:
            into.hosts_capped = True
    into.hosts_capped |= other.hosts_capped
    for e, bset in other.ents.items():
        if e in into.ents:
            into.ents[e] |= bset
        elif len(into.ents) < MAX_ENTITIES_PER_TEMPLATE:
            into.ents[e] = set(bset)
        else:
            into.ents_capped = True
    into.ents_capped |= other.ents_capped
    if other.first is not None and (into.first is None or other.first < into.first):
        into.first, into.first_loc = other.first, other.first_loc
    if other.last is not None and (into.last is None or other.last >= into.last):
        into.last, into.last_loc = other.last, other.last_loc
    for idx, cell in other.cells.items():
        cur = into.cells.get(idx)
        if cur is None:
            into.cells[idx] = cell
        else:
            _merge_cell(cur, cell)
    for fidx, ff in other.file_first.items():
        cur = into.file_first.get(fidx)
        if cur is None or ff[0] < cur[0]:
            into.file_first[fidx] = ff
    into.no_ts += other.no_ts


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _template_metrics(tpl: _Tpl, nb: int, span_s: float, gmin: float) -> Dict[str, Any]:
    cells = sorted(tpl.cells.items())
    counts = [c[0] for _, c in cells]
    timed = sum(counts)
    m: Dict[str, Any] = {"timed": timed}
    if not cells:
        return m
    first_idx, last_idx = cells[0][0], cells[-1][0]
    active = last_idx - first_idx + 1
    peak_idx, peak_cell = max(cells, key=lambda kv: (kv[1][0], -kv[0]))
    m["presence_pct"] = 100.0 * len(cells) / nb
    m["quarters"] = len({min(3, idx * 4 // nb) for idx, _ in cells})
    m["span_pct"] = 100.0 * (tpl.last - tpl.first) / span_s if span_s > 0 else 0.0
    m["first_pct"] = 100.0 * (tpl.first - gmin) / span_s if span_s > 0 else 0.0
    m["median_all"] = _median_with_zeros(counts, nb)
    m["median_active"] = _median_with_zeros(counts, active)
    m["peak"] = peak_cell[0]
    m["peak_idx"] = peak_idx
    m["peak_loc"] = peak_cell[3]
    m["peak_ts"] = peak_cell[1]
    m["active"] = active
    # Densest window of CLUSTER_WINDOW_FRAC of the range.
    w = max(1, int(math.ceil(nb * CLUSTER_WINDOW_FRAC)))
    best = run = 0
    j = 0
    dense = (first_idx, first_idx)
    for i in range(len(cells)):
        run += cells[i][1][0]
        while cells[i][0] - cells[j][0] >= w:
            run -= cells[j][1][0]
            j += 1
        if run > best:
            best, dense = run, (cells[j][0], cells[i][0])
    m["concentration"] = best / timed if timed else 0.0
    m["dense"] = dense
    if active >= 6:
        third = active / 3.0
        early = sum(
            c
            for idx, c in zip((i for i, _ in cells), counts)
            if idx < first_idx + third
        )
        late = sum(
            c for idx, c in zip((i for i, _ in cells), counts) if idx > last_idx - third
        )
        m["early"], m["late"] = early, late
    return m


def classify(an: Analyzer) -> Dict[str, Any]:
    """Label every template baseline / anomaly / intermittent, with reasons."""
    tpls = [t for t in an.tpls.values()]
    timed_mode = an.gmin is not None and an.gmax is not None
    total_hosts = len(an.hosts)
    if timed_mode:
        span_s = max(an.gmax - an.gmin, 0.0)
        nb = max(1, int((an.gmax - an.origin) // an.bucket_s) + 1)
    else:
        span_s, nb = 0.0, 1
    blast_hosts = max(BLAST_MIN_HOSTS, int(math.ceil(BLAST_HOST_SHARE * total_hosts)))
    for n, tpl in enumerate(
        sorted(tpls, key=lambda t: (t.first if t.first is not None else 0, t.key)), 1
    ):
        tpl.tid = f"T{n}"
    for tpl in tpls:
        m = (
            _template_metrics(tpl, nb, span_s, an.gmin or 0.0)
            if timed_mode
            else {"timed": 0}
        )
        tpl.m = m
        reasons: List[str] = []
        hosts = len(tpl.hosts)
        if timed_mode and m.get("timed"):
            span_pct = m["span_pct"]
            clustered = (
                span_pct <= SHORT_SPAN_PCT or m["concentration"] >= CLUSTER_SHARE
            )
            spread = span_s > 0 and nb >= 4
            if spread and clustered and tpl.sev >= 3:
                reasons.append(
                    "severe, short window" if tpl.count > 1 else "severe, seen once"
                )
            if (
                spread
                and clustered
                and tpl.sev == 2
                and (tpl.count >= 5 or hosts >= BLAST_MIN_HOSTS)
            ):
                reasons.append("warnings clustered in a short window")
            if (
                spread
                and clustered
                and hosts >= blast_hosts
                and total_hosts >= BLAST_MIN_HOSTS
            ):
                reasons.append(f"hit {hosts} of {total_hosts} hosts in a short window")
            if (
                spread
                and tpl.sev >= 2
                and tpl.count >= 5
                and m["first_pct"] >= NEW_TEMPLATE_MIN_FIRST_PCT
            ):
                active_s = max(tpl.last - tpl.first, an.bucket_s)
                expected_before = tpl.count / active_s * (tpl.first - an.gmin)
                if expected_before >= NEW_TEMPLATE_EXPECTED_BEFORE:
                    reasons.append(
                        f"new: first seen {m['first_pct']:.0f}% into the range"
                    )
            threshold = max(
                SPIKE_MIN_LINES, SPIKE_FACTOR * max(1.0, m["median_active"])
            )
            if spread and tpl.sev >= 2 and m["peak"] >= threshold and m["active"] > 1:
                reasons.append(
                    f"spike: peak {m['peak']}/bucket vs median {m['median_active']:g}"
                )
            if (
                spread
                and tpl.sev >= 2
                and tpl.count >= ESCALATION_MIN_COUNT
                and "late" in m
                and m["late"] >= 10
                and m["late"] >= ESCALATION_FACTOR * max(1, m["early"])
            ):
                reasons.append(
                    f"escalating: {m['early']} -> {m['late']} lines (first vs last third)"
                )
            if not spread and tpl.sev >= 3:
                reasons.append("severe")
        elif not timed_mode:
            if tpl.sev >= 3 and tpl.count <= max(3, 0.001 * an.entries):
                reasons.append("rare severe template (no timestamps to rank by time)")
        tpl.reasons = reasons
        if reasons:
            tpl.cls = "anomaly"
        elif (
            timed_mode
            and m.get("timed")
            and (
                m["span_pct"] >= BASELINE_MIN_SPAN_PCT
                and m["quarters"] >= BASELINE_MIN_QUARTERS
                and tpl.count >= 4
            )
        ):
            tpl.cls = "baseline"
        elif not timed_mode and tpl.count >= 20:
            tpl.cls = "baseline"
        else:
            tpl.cls = "intermittent"
    return {"nb": nb, "span_s": span_s, "total_hosts": total_hosts}


def _anomaly_window(tpl: _Tpl) -> Optional[set]:
    """Buckets where a template is anomalous; None means all of it."""
    m = tpl.m
    if not m.get("timed"):
        return None
    whole = [r for r in tpl.reasons if not r.startswith(("spike", "escalating"))]
    window: set = set()
    if whole:
        if any(r.startswith("new") for r in whole) or m["span_pct"] <= SHORT_SPAN_PCT:
            return None
        # Clustered, with stragglers elsewhere: the cluster is the anomaly.
        lo, hi = m["dense"]
        window |= {idx for idx in tpl.cells if lo <= idx <= hi}
    if any(r.startswith("spike") for r in tpl.reasons):
        hot = max(SPIKE_MIN_LINES / 2.0, 3.0 * max(1.0, m["median_active"]))
        window |= {idx for idx, c in tpl.cells.items() if c[0] >= hot}
    if any(r.startswith("escalating") for r in tpl.reasons):
        first_idx = min(tpl.cells)
        last_idx = max(tpl.cells)
        cut = last_idx - (last_idx - first_idx + 1) / 3.0
        window |= {idx for idx in tpl.cells if idx > cut}
    return window


def find_bursts(an: Analyzer, nb: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Buckets where error and anomaly lines rise far above their median.

    Baseline templates are left out of the series: a platform that logs a
    steady stream of FATAL lines would otherwise raise the median until a real
    burst disappears into it. A baseline template that spikes is an anomaly,
    so it still counts.
    """
    contrib = [
        t
        for t in an.tpls.values()
        if t.cells and (t.cls == "anomaly" or (t.sev >= 3 and t.cls != "baseline"))
    ]
    series = [0] * nb
    for t in contrib:
        for idx, cell in t.cells.items():
            if 0 <= idx < nb:
                series[idx] += cell[0]
    ordered = sorted(series)
    med = _median_with_zeros(ordered, nb)
    mad = _median_with_zeros(sorted(abs(x - med) for x in series), nb)
    sigma = max(1.4826 * mad, math.sqrt(med))
    threshold = max(
        BURST_MIN_BUCKET, BURST_MEDIAN_FACTOR * med, med + BURST_SIGMAS * sigma
    )
    stats = {"median": med, "threshold": round(threshold, 1)}
    hot = [i for i, v in enumerate(series) if v >= threshold]
    # Hysteresis: a hot bucket starts a burst; neighbours above a lower bar
    # (its leading and trailing edge) belong to it.
    shoulder = max(1.0, BURST_MEDIAN_FACTOR / 1.5 * med, med + BURST_SIGMAS / 2 * sigma)
    windows: List[List[int]] = []
    for i in hot:
        if windows and i - windows[-1][1] <= 2:
            windows[-1][1] = i
        else:
            windows.append([i, i])
    for w in windows:
        while w[0] > 0 and series[w[0] - 1] >= shoulder:
            w[0] -= 1
        while w[1] < nb - 1 and series[w[1] + 1] >= shoulder:
            w[1] += 1
    merged: List[List[int]] = []
    for w in windows:
        if merged and w[0] - merged[-1][1] <= 2:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(w)
    windows = merged
    bursts = []
    for b0, b1 in windows:
        lines = sum(series[b0 : b1 + 1])
        if lines < BURST_MIN_LINES:
            continue
        wlen = b1 - b0 + 1
        rows = []
        for t in contrib:
            w_count = sum(c[0] for idx, c in t.cells.items() if b0 <= idx <= b1)
            if not w_count:
                continue
            share = w_count / max(1, sum(c[0] for c in t.cells.values()))
            expected = t.m.get("median_all", 0.0) * wlen
            elevated = w_count >= 3 and w_count >= 3 * expected + 3
            rows.append((t, w_count, share, elevated))
        unique = [r for r in rows if r[2] >= 0.5 and r[3]]
        elevated = [r for r in rows if r[3]]
        basis = unique or elevated or rows
        lo_ts = hi_ts = None
        lo_loc = None
        hosts: set = set()
        for t, _, share, _ in basis:
            reach = 1 if share >= 0.5 else 0
            for idx, cell in t.cells.items():
                if b0 - reach <= idx <= b1 + reach:
                    if lo_ts is None or cell[1] < lo_ts:
                        lo_ts, lo_loc = cell[1], cell[3]
                    hi_ts = cell[2] if hi_ts is None else max(hi_ts, cell[2])
                    hosts |= cell[4]
        rows.sort(key=lambda r: (-r[3], -r[0].sev, -r[2], -r[1]))
        bursts.append(
            {
                "b0": b0,
                "b1": b1,
                "start": lo_ts,
                "end": hi_ts,
                "start_loc": lo_loc,
                "lines": lines,
                "excess": round(lines - med * wlen),
                "hosts": sorted(hosts),
                "templates": rows,
            }
        )
    hot_all = {i for b in bursts for i in range(b["b0"], b["b1"] + 1)}
    for b in bursts:
        b["quiet"] = compare_with_quiet(an, b["b0"], b["b1"], hot_all, nb)
    bursts.sort(key=lambda b: -b["excess"])
    return bursts, stats


def compare_with_quiet(
    an: Analyzer, b0: int, b1: int, hot: set, nb: int
) -> Optional[Dict[str, Any]]:
    """Template counts in a burst against an equally long quiet window.

    The quiet window is the nearest stretch before the burst (after it when
    the burst is at the start) that overlaps no burst. What rose is the
    incident's signature; what fell silent (heartbeats, a host's routine
    traffic) is often the clearer clue.
    """
    length = b1 - b0 + 1
    window = None
    for q0 in list(range(b0 - length - 1, -1, -1)) + list(
        range(b1 + 2, nb - length + 1)
    ):
        q1 = q0 + length - 1
        if not any(i in hot for i in range(q0 - 1, q1 + 2)):
            window = (q0, q1)
            break
    if window is None:
        return None
    q0, q1 = window
    rose, fell = [], []
    for t in an.tpls.values():
        b = q = 0
        for idx, cell in t.cells.items():
            if b0 <= idx <= b1:
                b += cell[0]
            elif q0 <= idx <= q1:
                q += cell[0]
        if b >= 3 and b >= 2 * q + 3:
            rose.append((b - q, t, b, q))
        elif q >= 5 and b * 4 <= q:
            fell.append((q - b, t, b, q))
    rose.sort(key=lambda r: (-r[1].sev, -r[0]))
    fell.sort(key=lambda r: -r[0])
    start = an.origin + q0 * an.bucket_s
    return {
        "start": start,
        "end": start + length * an.bucket_s,
        "rose": [(t, b, q) for _, t, b, q in rose],
        "fell": [(t, b, q) for _, t, b, q in fell],
    }


def clock_check(an: Analyzer) -> List[Dict[str, Any]]:
    """Steady offsets between files, measured on ids that appear in both.

    The same request, transaction or job id logged by two services should
    carry nearly the same time. A consistent lag on many shared ids means a
    skewed clock, a time zone written without an offset, or a pipeline delay.
    Times are never shifted; the offsets are reported for the reader.
    """
    if not an.multi_file:
        return []
    diffs: Dict[Tuple[int, int], List[float]] = {}
    for seen in an.all_ents.values():
        if not seen or len(seen) < 2:
            continue
        ordered = sorted(seen)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                (fa, ta), (fb, tb) = ordered[i], ordered[j]
                bucket = diffs.setdefault((fa, fb), [])
                if len(bucket) < 5000:
                    bucket.append(tb - ta)
    out = []
    for (fa, fb), d in diffs.items():
        if len(d) < 5:
            continue
        d.sort()
        med = d[len(d) // 2]
        spread = d[(3 * len(d)) // 4] - d[len(d) // 4]
        if abs(med) < 2.0 or spread > max(5.0, abs(med) / 2):
            continue
        a, b = an.files[fa].name, an.files[fb].name
        if med < 0:
            a, b, med = b, a, -med
        out.append(
            {
                "file": b,
                "later_than": a,
                "median_offset_s": round(med, 1),
                "spread_s": round(spread, 1),
                "shared_ids": len(d),
            }
        )
    out.sort(key=lambda r: -r["median_offset_s"])
    return out[:5]


def _template_hits(tpl: _Tpl) -> List[str]:
    window = _anomaly_window(tpl)
    return [e for e, bset in tpl.ents.items() if window is None or bset & window]


def build_entities(an: Analyzer, anomalies: List[_Tpl]) -> Dict[str, Any]:
    """The anomalous-entity list: ids from anomaly templates, in their windows.

    Without ``entity_regex`` every id-shaped token was collected by kind
    (:func:`auto_entities`). A kind names the anomalous entities when it
    occurs in the anomaly templates and separates them from routine traffic:
    either most of its values are untouched by anomalies, or the flagged ones
    seldom appear in routine templates. Kinds that fail (client IPs, ports,
    ids every request carries) are reported as skipped, with the reason.

    If the list still covers most entities that routine templates also carry,
    the broadest anomaly templates are left out one at a time and the list is
    rebuilt from the narrower ones, saying which and why.
    """
    anomaly_ids = {id(t) for t in anomalies}
    routine: set = set()
    for t in an.tpls.values():
        if id(t) not in anomaly_ids:
            routine.update(t.ents)
    hits_by_tpl = {t.tid: _template_hits(t) for t in anomalies}
    tpl_by_id = {t.tid: t for t in anomalies}
    kind_total: Counter = Counter(an.ent_kind.values())

    kind_flagged: Dict[str, set] = {}
    kind_weight: Counter = Counter()
    for tid, hits in hits_by_tpl.items():
        kinds_here = set()
        for e in hits:
            k = an.ent_kind.get(e)
            if k is None:
                continue
            kind_flagged.setdefault(k, set()).add(e)
            kinds_here.add(k)
        for k in kinds_here:
            kind_weight[k] += tpl_by_id[tid].count

    def describe(k: str) -> Dict[str, Any]:
        flagged_k = kind_flagged.get(k, set())
        return {
            "kind": k,
            "regex": kind_regex(k, an.kinds.get(k, [])),
            "total": kind_total[k],
            "flagged": len(flagged_k),
            "examples": sorted(flagged_k)[:3] or an.kinds.get(k, [])[:3],
        }

    used: List[str] = []
    skipped: List[Dict[str, Any]] = []
    separating: List[str] = []
    exclusive: Dict[str, float] = {}
    for k in kind_flagged:
        flagged_k = kind_flagged[k]
        share_k = len(flagged_k) / max(1, kind_total[k])
        exclusive[k] = 1.0 - sum(1 for e in flagged_k if e in routine) / len(flagged_k)
        if an.custom_entities or share_k <= 0.5 or exclusive[k] > 0.5:
            separating.append(k)
        else:
            skipped.append(
                {
                    **describe(k),
                    "why": f"anomalies touch {share_k:.0%} of its values and most of them "
                    "also appear in routine templates, so it does not single anything out",
                }
            )
    # The primary kind: in the most anomaly lines, then the one whose flagged
    # values occur only in anomalies, then the rarer share, then cardinality.
    separating.sort(
        key=lambda k: (
            -kind_weight[k],
            -round(exclusive[k], 1),
            len(kind_flagged[k]) / max(1, kind_total[k]),
            -kind_total[k],
            k,
        )
    )
    if separating:
        primary = separating[0]
        used.append(primary)
        for k in separating[1:]:
            if kind_weight[k] >= 0.5 * kind_weight[primary] and exclusive[k] > 0.5:
                used.append(k)
            else:
                why = (
                    f"in fewer anomaly lines than {primary}"
                    if kind_weight[k] < 0.5 * kind_weight[primary]
                    else "its flagged values also appear in routine templates; "
                    f"{primary} names the anomalous entities more precisely"
                )
                skipped.append({**describe(k), "why": why})
    elif kind_flagged:
        used.append(max(kind_flagged, key=lambda k: (kind_weight[k], k)))
    used_set = set(used)
    total = sum(kind_total[k] for k in used)

    def collect(tids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        flagged: Dict[str, Dict[str, Any]] = {}
        for tid in tids:
            for e in hits_by_tpl[tid]:
                if an.ent_kind.get(e) not in used_set:
                    continue
                entry = flagged.setdefault(
                    e, {"templates": [], "kind": an.ent_kind.get(e)}
                )
                if tid not in entry["templates"] and len(entry["templates"]) < 5:
                    entry["templates"].append(tid)
        return flagged

    active = [tid for tid, hits in hits_by_tpl.items() if hits]
    flagged = collect(active)
    narrowed: List[str] = []

    def routine_heavy(fl: Dict[str, Any]) -> bool:
        if not fl or total < 20 or len(fl) / total <= ENTITY_SHARE_WARN:
            return False
        return sum(1 for e in fl if e in routine) >= 0.5 * len(fl)

    while routine_heavy(flagged) and len(active) > 1:
        broadest = max(
            active,
            key=lambda tid: (sum(1 for e in hits_by_tpl[tid] if e in flagged), tid),
        )
        active.remove(broadest)
        narrowed.append(broadest)
        flagged = collect(active)
    per_template = {
        tid: sum(1 for e in flagged if tid in flagged[e]["templates"]) for tid in active
    }
    per_template = {k: v for k, v in per_template.items() if v}
    share = len(flagged) / total if total else 0.0
    warnings = []
    note = None
    if narrowed:
        note = (
            f"left out {', '.join(narrowed)}: with them the list held most of the {total} "
            "entities, and those also appear in routine templates, so they describe the "
            "baseline. The list comes from the narrower anomaly templates."
        )
        warnings.append("entity list narrowed: " + note)
    if routine_heavy(flagged):
        warnings.append(
            f"{len(flagged)} of {total} entities ({share:.0%}) are flagged and most also "
            "appear in routine templates. Real anomalies are usually a small minority; "
            "check the anomaly templates or pass entity_regex."
        )
    if an.ents_capped or an.ent_pairs_capped or any(t.ents_capped for t in anomalies):
        warnings.append(
            "entity tracking hit its memory cap; the entity list may be incomplete."
        )
    if an.kinds_capped:
        warnings.append(
            f"more than {MAX_ENTITY_KINDS} id shapes; rarer ones were not tracked."
        )
    return {
        "mode": "entity_regex" if an.custom_entities else "auto-detected",
        "kinds_used": [describe(k) for k in used],
        "kinds_skipped": skipped,
        "total": total,
        "all_ids": len(an.all_ents),
        "flagged": flagged,
        "per_template": per_template,
        "dropped_templates": narrowed,
        "narrowed": note,
        "share": share,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def resolve_inputs(
    paths: Sequence[str], glob_pattern: Optional[str]
) -> Tuple[List[Path], List[Dict[str, str]]]:
    """Expand folders and globs into a sorted, de-duplicated file list.

    Folders are listed with :func:`gaia.skills.context.list_readable` and
    files checked with :func:`gaia.skills.context.check_read`, so nothing
    outside the agent's allowed folders is ever opened.

    Returns:
        ``(files, skipped)`` where ``skipped`` names each file left out and why.
    """
    files: List[Path] = []
    skipped: List[Dict[str, str]] = []
    seen = set()
    patterns = [g.strip() for g in re.split(r"[;,]", glob_pattern or "") if g.strip()]

    def wanted(path: Path, root: Optional[Path]) -> bool:
        if not patterns:
            return True
        rel = path.relative_to(root).as_posix() if root else path.name
        return any(
            fnmatch.fnmatch(path.name.lower(), p.lower())
            or fnmatch.fnmatch(rel.lower(), p.lower())
            for p in patterns
        )

    def add(path: Path, explicit: bool) -> None:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            return
        seen.add(key)
        if not explicit and path.suffix.lower() in NON_LOG_SUFFIXES:
            skipped.append(
                {"file": str(path), "reason": f"{path.suffix} is not a log format"}
            )
            return
        try:
            if _is_binary(path):
                skipped.append({"file": str(path), "reason": "binary content"})
                return
            if _is_own_output(path):
                skipped.append(
                    {"file": str(path), "reason": "an earlier triage_logs output"}
                )
                return
        except SkillPermissionError as exc:
            skipped.append({"file": str(path), "reason": str(exc)})
            return
        files.append(path)

    def listed(root: Path, recursive: bool) -> List[Path]:
        try:
            return skill_context.list_readable(root, recursive=recursive)
        except SkillPermissionError as exc:
            skipped.append({"file": str(root), "reason": str(exc)})
            return []

    for raw in paths:
        p = Path(os.path.expanduser(str(raw).strip().strip('"')))
        if p.is_dir():
            for fp in listed(p, True):
                if wanted(fp, p):
                    add(fp, explicit=False)
        elif p.is_file():
            add(p, explicit=True)
        else:
            parent = p.parent if str(p.parent) not in ("", ".") else Path(".")
            if any(ch in p.name for ch in "*?[") and parent.is_dir():
                for fp in listed(parent, False):
                    if fnmatch.fnmatch(fp.name.lower(), p.name.lower()):
                        add(fp, explicit=False)
            else:
                skipped.append({"file": str(p), "reason": "not found"})
    return files, skipped


def _is_own_output(path: Path) -> bool:
    if path.suffix.lower() not in (".json", ".csv"):
        return False
    try:
        with skill_context.open_read(path, "rb") as f:
            head = f.read(200).decode("utf-8", errors="replace")
    except OSError:
        return False
    return OUTPUT_MARKER in head


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def _short(text: str, limit: int = OUT_TEMPLATE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def triage(
    paths: Sequence[str], options: Optional[TriageOptions] = None
) -> "TriageResult":
    """Run the whole triage over ``paths`` (files and/or folders).

    Must run inside a skill tool call (or a :func:`gaia.skills.context.bind`
    block): every file is opened through the skill context.

    Raises:
        ValueError: an option (regex, bucket) is invalid.
    """
    options = options or TriageOptions()
    an = Analyzer(options)
    files, skipped = resolve_inputs(paths, options.glob)
    not_processed: List[str] = []
    if len(files) > MAX_FILES:
        not_processed = [str(p) for p in files[MAX_FILES:]]
        files = files[:MAX_FILES]
    plans = an.prepare(files)
    for path, ctx, _info in plans:
        if an.out_of_time:
            not_processed.append(str(path))
            continue
        an.run_file(path, ctx)
    an.finalize()
    meta = classify(an)
    return TriageResult(an, meta, skipped, not_processed)


class TriageResult:
    """Everything :func:`triage` found, plus the compact and full views."""

    def __init__(
        self,
        an: Analyzer,
        meta: Dict[str, Any],
        skipped: List[Dict[str, str]],
        not_processed: List[str],
    ) -> None:
        self.an = an
        self.meta = meta
        self.skipped = skipped
        self.not_processed = not_processed
        tpls = list(an.tpls.values())
        self.anomalies = sorted(
            (t for t in tpls if t.cls == "anomaly"),
            key=lambda t: (-t.sev, -len(t.hosts), t.count, t.first or 0),
        )
        self.baseline = sorted(
            (t for t in tpls if t.cls == "baseline"), key=lambda t: -t.count
        )
        self.intermittent = [t for t in tpls if t.cls == "intermittent"]
        nb = meta["nb"]
        self.bursts, self.burst_stats = (
            find_bursts(an, nb) if an.gmin is not None else ([], {})
        )
        self.entities = build_entities(an, self.anomalies)
        self.clock = clock_check(an)
        all_tz = [f for f in an.files if f.tz_aware or f.tz_naive]
        self.utc = bool(all_tz) and all(f.tz_aware and not f.tz_naive for f in all_tz)
        self.warnings = self._warnings()
        self.checks = self._checks()

    # -- helpers -----------------------------------------------------------

    def ts(self, epoch: Optional[float]) -> Optional[str]:
        return format_ts(epoch, self.utc)

    def loc(self, loc: Optional[Tuple[int, int]]) -> Optional[Dict[str, Any]]:
        if loc is None:
            return None
        return {"file": self.an.files[loc[0]].name, "line": loc[1]}

    def _warnings(self) -> List[str]:
        an = self.an
        out: List[str] = list(an.warnings)
        for f in an.files:
            if f.error:
                out.append(f"{f.name}: {f.error}")
            parsed_share = f.entries / max(1, f.entries + f.unparsed)
            if (
                f.entries + f.unparsed
                and parsed_share < MIN_PARSED_SHARE
                and f.format != "plain"
            ):
                out.append(
                    f"{f.name}: {f.unparsed} of {f.entries + f.unparsed} lines did not parse "
                    f"as {f.format}; pass timestamp_regex if the format was misread."
                )
            if f.format == "plain":
                out.append(
                    f"{f.name}: no timestamp format recognised; lines were templated "
                    "without time ranking. Pass timestamp_regex to fix."
                )
            if f.truncated_at_line:
                out.append(
                    f"{f.name}: time budget ran out at line {f.truncated_at_line}; the rest "
                    "was not analysed."
                )
            if f.year_inferred:
                out.append(
                    f"{f.name}: timestamps carry no year; assumed {datetime.fromtimestamp(f.first or 0, tz=timezone.utc).year}."
                )
            if f.out_of_order > max(10, 0.01 * f.entries):
                out.append(
                    f"{f.name}: {f.out_of_order} lines go back in time; clocks or merges may be off."
                )
        aware = [f for f in an.files if f.tz_aware]
        naive = [f for f in an.files if f.tz_naive]
        if aware and naive:
            out.append(
                f"{len(aware)} file(s) carry UTC offsets (converted to UTC) and {len(naive)} do not "
                f"(taken as written, e.g. {naive[0].name}). If those are local time, cross-file order "
                "is off by the zone difference."
            )
        offsets = {o for f in aware for o in f.offsets}
        if len(offsets) > 1:
            out.append(
                "files use several UTC offsets ("
                + ", ".join(sorted(_fmt_offset(o) for o in offsets))
                + "); all timestamps are reported in UTC."
            )
        out.extend(self._skew_warnings())
        for c in self.clock:
            hours = c["median_offset_s"] / 3600.0
            zone = (
                " (close to whole hours: a time zone written without an offset?)"
                if (hours >= 0.5 and abs(hours * 2 - round(hours * 2)) < 0.1)
                else ""
            )
            out.append(
                f"ids shared by {c['later_than']} and {c['file']} appear {c['median_offset_s']} s later in "
                f"{c['file']} (spread {c['spread_s']} s over {c['shared_ids']} ids): clock skew or a "
                f"steady delay{zone}. Times are reported as written."
            )
        if self.not_processed:
            out.append(
                f"{len(self.not_processed)} file(s) not analysed (file or time limit), e.g. "
                f"{Path(self.not_processed[0]).name}."
            )
        if an.overflow is not None:
            out.append(
                f"more than {an.opt.max_templates} distinct templates; "
                f"{an.overflow.count} lines were pooled as one overflow template."
            )
        if an.hosts_capped:
            out.append(
                f"more than {MAX_HOSTS_TOTAL} hosts; host counts are lower bounds."
            )
        out.extend(self.entities["warnings"])
        return out

    def _skew_warnings(self) -> List[str]:
        an = self.an
        if len(an.files) < 2:
            return []
        seen = set()
        out = []
        for t in self.anomalies:
            if len(t.file_first) < 2:
                continue
            items = sorted(t.file_first.items(), key=lambda kv: kv[1][0])
            base_idx, (base_ts, _) = items[0]
            for fidx, (ts, _) in items[1:]:
                diff = ts - base_ts
                half_hours = round(diff / 1800.0)
                if half_hours >= 1 and abs(diff - half_hours * 1800) <= 300:
                    pair = (base_idx, fidx)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    a, b = an.files[base_idx], an.files[fidx]
                    if a.tz_aware and b.tz_aware:
                        continue
                    out.append(
                        f"{b.name} shows {t.tid} about {diff / 3600:.1f} h after {a.name}: the files may "
                        "use different time zones without offsets. Times are reported as written."
                    )
        return out[:3]

    def _checks(self) -> Dict[str, Any]:
        an = self.an
        failed = []
        tpl_total = sum(t.count for t in an.tpls.values()) + (
            an.overflow.count if an.overflow else 0
        )
        if tpl_total != an.entries:
            failed.append(
                f"template counts sum to {tpl_total}, entries are {an.entries}"
            )
        if sum(an.level_totals.values()) != an.entries:
            failed.append("level counts do not sum to entries")
        for f in an.files:
            accounted = f.entries + f.continuation + f.unparsed + f.blank
            if not f.error and not f.truncated_at_line and accounted != f.lines:
                failed.append(
                    f"{f.name}: {accounted} lines accounted for, {f.lines} read"
                )
        total_hosts = len(an.hosts)
        for b in self.bursts:
            if len(b["hosts"]) > total_hosts:
                failed.append("a burst has more hosts than the logs")
        if (
            len(self.entities["flagged"]) > max(self.entities["total"], 0)
            and not an.ents_capped
        ):
            failed.append("more entities flagged than exist")
        classes = len(self.anomalies) + len(self.baseline) + len(self.intermittent)
        if classes != len(an.tpls):
            failed.append("template classes do not cover every template")
        below = [
            f.name
            for f in an.files
            if f.entries + f.unparsed
            and f.entries / (f.entries + f.unparsed) < MIN_PARSED_SHARE
        ]
        return {
            "ok": not failed,
            "failed": failed,
            "parsed_share_ok": not below,
            "parsed_share_min": MIN_PARSED_SHARE,
            "files_below_parsed_share": below,
            "entries": an.entries,
            "template_count_sum": tpl_total,
            "level_count_sum": sum(an.level_totals.values()),
        }

    # -- views -------------------------------------------------------------

    def _tpl_view(self, t: _Tpl, *, full: bool = False) -> Dict[str, Any]:
        m = t.m
        hosts_sorted = sorted(t.hosts, key=lambda h: -t.hosts[h])
        view: Dict[str, Any] = {
            "id": t.tid,
            "template": t.key if full else _short(t.key),
            "level": t.levels.most_common(1)[0][0] if t.levels else "-",
            "severity": SEVERITY_NAMES[t.sev],
            "count": t.count,
            "hosts": (
                len(t.hosts) if not t.hosts_capped else f"{MAX_HOSTS_PER_TEMPLATE}+"
            ),
            "host_sample": hosts_sorted[: (50 if full else OUT_HOSTS)],
            "entities": len(t.ents),
        }
        if m.get("timed"):
            view.update(
                {
                    "span_pct": round(m["span_pct"], 1),
                    "presence_pct": round(m["presence_pct"], 1),
                    "peak_per_bucket": m["peak"],
                    "median_per_bucket": m["median_all"],
                    "first": {"ts": self.ts(t.first), **(self.loc(t.first_loc) or {})},
                    "last": {"ts": self.ts(t.last), **(self.loc(t.last_loc) or {})},
                    "peak_at": {
                        "ts": self.ts(m["peak_ts"]),
                        **(self.loc(m["peak_loc"]) or {}),
                    },
                }
            )
        if t.reasons:
            view["why"] = t.reasons
        if full:
            view["class"] = t.cls
            view["example"] = t.sample
            view["files"] = sorted({self.an.files[i].name for i in t.file_first})
        return view

    def _burst_view(self, b: Dict[str, Any], *, full: bool = False) -> Dict[str, Any]:
        rows = b["templates"]
        unique = [r for r in rows if r[2] >= 0.5 and r[3]]
        limit = None if full else 5
        return {
            "start": self.ts(b["start"]),
            "end": self.ts(b["end"]),
            "duration_s": (
                round((b["end"] - b["start"]), 3) if b["start"] is not None else None
            ),
            "lines": b["lines"],
            "excess_over_median": b["excess"],
            "hosts": len(b["hosts"]),
            "host_sample": b["hosts"][: (50 if full else OUT_HOSTS)],
            "first_line": self.loc(b["start_loc"]),
            "only_here": [r[0].tid for r in unique][:limit],
            "vs_quiet_window": self._quiet_view(b.get("quiet"), full),
            "templates": [
                {
                    "id": r[0].tid,
                    "template": _short(r[0].key, 100) if not full else r[0].key,
                    "severity": SEVERITY_NAMES[r[0].sev],
                    "in_burst": r[1],
                    "share_of_all": round(r[2], 2),
                }
                for r in rows[:limit]
            ],
        }

    def _quiet_view(
        self, q: Optional[Dict[str, Any]], full: bool
    ) -> Optional[Dict[str, Any]]:
        if not q:
            return None
        limit = None if full else 4

        def rows(items):
            return [
                {
                    "id": t.tid,
                    "template": _short(t.key, 80),
                    "in_burst": b,
                    "in_quiet": qq,
                }
                for t, b, qq in items[:limit]
            ]

        return {
            "start": self.ts(q["start"]),
            "end": self.ts(q["end"]),
            "rose": rows(q["rose"]),
            "fell_silent": rows(q["fell"]),
        }

    def _timeline(self) -> List[Dict[str, Any]]:
        """First occurrences of anomalies per file, plus each burst's exact
        start and end, in time order. Quote these timestamps verbatim."""
        events: List[Tuple[float, int, Dict[str, Any]]] = []
        for n, b in enumerate(self.bursts[:OUT_BURSTS], 1):
            if b["start"] is None:
                continue
            first = self.loc(b["start_loc"]) or {}
            events.append(
                (
                    b["start"],
                    0,
                    {"ts": self.ts(b["start"]), "event": f"burst {n} starts", **first},
                )
            )
            events.append(
                (b["end"], 2, {"ts": self.ts(b["end"]), "event": f"burst {n} ends"})
            )
        for t in self.anomalies:
            for fidx, (ts, line) in t.file_first.items():
                events.append(
                    (
                        ts,
                        1,
                        {
                            "ts": self.ts(ts),
                            "event": f"{t.tid} first seen",
                            "file": self.an.files[fidx].name,
                            "line": line,
                            "severity": SEVERITY_NAMES[t.sev],
                            "template": _short(t.key, 80),
                        },
                    )
                )
        events.sort(key=lambda e: (e[0], e[1]))
        return [e[2] for e in events[:OUT_TIMELINE]]

    def _quiet_hosts(self) -> List[Dict[str, Any]]:
        an = self.an
        if an.gmin is None or an.gmax is None:
            return []
        span = an.gmax - an.gmin
        out = []
        for host, (count, first, last) in an.hosts.items():
            if (
                first is None
                or count < 20
                or last >= an.gmax - max(0.1 * span, 3 * an.bucket_s)
            ):
                continue
            rate = count / max(last - first, an.bucket_s)
            expected = rate * (an.gmax - last)
            if expected >= 10:
                out.append(
                    {
                        "host": host,
                        "last_line": self.ts(last),
                        "lines": count,
                        "expected_after": int(expected),
                    }
                )
        out.sort(key=lambda h: h["last_line"])
        return out[:5]

    def _files_view(self) -> List[Dict[str, Any]]:
        rows = []
        for f in self.an.files:
            row: Dict[str, Any] = {
                "file": f.name,
                "format": f.format,
                "lines": f.lines,
                "entries": f.entries,
                "continuation": f.continuation,
                "unparsed": f.unparsed,
                "first": self.ts(f.first),
                "last": self.ts(f.last),
            }
            if f.no_timestamp:
                row["no_timestamp"] = f.no_timestamp
            if f.unparsed_samples:
                row["unparsed_samples"] = [
                    {"line": n, "text": s} for n, s in f.unparsed_samples
                ]
            if f.offsets:
                row["utc_offsets"] = sorted(_fmt_offset(o) for o in f.offsets)
            if f.error:
                row["error"] = f.error
            rows.append(row)
        return rows

    def compact(self, top_n: int = DEFAULT_TOP_N) -> Dict[str, Any]:
        """The result the model reads: capped lists, short strings."""
        an = self.an
        top_n = max(1, min(MAX_TOP_N, int(top_n)))
        files = self._files_view()
        baseline_lines = sum(t.count for t in self.baseline)
        result: Dict[str, Any] = {
            "status": "success",
            "headline": self._headline(),
            "coverage": {
                "files": files[:OUT_FILES],
                "files_total": len(files),
                "skipped": self.skipped[:10],
                "not_processed": [Path(p).name for p in self.not_processed[:10]],
                "entries": an.entries,
                "hosts": len(an.hosts),
                "first": self.ts(an.gmin),
                "last": self.ts(an.gmax),
                "bucket": Analyzer._fmt_duration(an.bucket_s),
                "times_in": "UTC" if self.utc else "as written in the logs",
            },
            "anomalies": [self._tpl_view(t) for t in self.anomalies[:top_n]],
            "anomaly_templates": len(self.anomalies),
            "bursts": [self._burst_view(b) for b in self.bursts[:OUT_BURSTS]],
            "timeline": self._timeline(),
            "baseline": {
                "templates": len(self.baseline),
                "lines": baseline_lines,
                "share_of_entries": (
                    round(baseline_lines / an.entries, 3) if an.entries else 0.0
                ),
                "top": [
                    {
                        "id": t.tid,
                        "template": _short(t.key, 100),
                        "severity": SEVERITY_NAMES[t.sev],
                        "count": t.count,
                        "hosts": len(t.hosts),
                        "median_per_bucket": t.m.get("median_all"),
                        "span_pct": round(t.m.get("span_pct", 0.0), 1),
                    }
                    for t in self.baseline[:OUT_BASELINE]
                ],
            },
            "intermittent_templates": len(self.intermittent),
            "levels": dict(an.level_totals.most_common()),
            "entities": self._entities_view(),
            "quiet_hosts": self._quiet_hosts(),
            "clock_offsets": self.clock,
            "checks": {
                k: v
                for k, v in self.checks.items()
                if k in ("ok", "failed", "parsed_share_ok", "files_below_parsed_share")
            },
            "warnings": [
                _short(w, OUT_WARNING_CHARS) for w in self.warnings[:OUT_WARNINGS]
            ],
        }
        if len(self.warnings) > OUT_WARNINGS:
            result["warnings"].append(
                f"... {len(self.warnings) - OUT_WARNINGS} more warnings in the full output."
            )
        if self.bursts and self.bursts[0]["start_loc"]:
            first = self.loc(self.bursts[0]["start_loc"])
            result["next_step"] = (
                f"read_file the lines before {first['file']}:{first['line']} (start_line="
                f"{max(1, first['line'] - 30)}) to see what led up to the first burst."
            )
        elif self.anomalies and self.anomalies[0].first_loc:
            first = self.loc(self.anomalies[0].first_loc)
            result["next_step"] = (
                f"read_file around {first['file']}:{first['line']} to see the context of {self.anomalies[0].tid}."
            )
        return result

    def _entities_view(self) -> Dict[str, Any]:
        """The ready anomalous-entity list, with how it was built."""
        ent = self.entities
        flagged = sorted(ent["flagged"])
        view: Dict[str, Any] = {
            "mode": ent["mode"],
            "kinds_used": ent["kinds_used"],
            "kinds_skipped": ent["kinds_skipped"][:4],
            "total": ent["total"],
            "flagged": len(flagged),
            "share": round(ent["share"], 4),
            "by_template": ent["per_template"],
        }
        if ent["narrowed"]:
            view["narrowed"] = ent["narrowed"]
        if len(flagged) <= OUT_ENTITY_LIST:
            view["list"] = flagged
        else:
            view["sample"] = flagged[:OUT_ENTITIES]
            view["full_list"] = (
                f"{len(flagged)} entities: call again with output_path='<folder>/"
                "anomalous_entities.csv' (or .txt) to write all of them."
            )
        return view

    def _headline(self) -> str:
        an = self.an
        parts = [
            f"{an.entries} entries in {len(an.files)} file(s), {len(an.tpls)} templates"
        ]
        parts.append(f"{len(self.anomalies)} anomaly template(s)")
        if self.bursts:
            b = self.bursts[0]
            parts.append(
                f"{len(self.bursts)} burst(s), largest {self.ts(b['start'])} to {self.ts(b['end'])} "
                f"({b['lines']} lines, {len(b['hosts'])} host(s))"
            )
        else:
            parts.append("no bursts")
        parts.append(
            f"{len(self.baseline)} baseline template(s) cover "
            f"{sum(t.count for t in self.baseline) / max(1, an.entries):.0%} of entries"
        )
        return "; ".join(parts) + "."

    def full(self) -> Dict[str, Any]:
        """Everything, for the optional JSON output."""
        tpls = sorted(
            self.an.tpls.values(), key=lambda t: (t.cls != "anomaly", -t.sev, -t.count)
        )
        return {
            "generator": OUTPUT_MARKER,
            "headline": self._headline(),
            "coverage": {
                "files": self._files_view(),
                "skipped": self.skipped,
                "not_processed": self.not_processed,
                "bucket_seconds": self.an.bucket_s,
                "first": self.ts(self.an.gmin),
                "last": self.ts(self.an.gmax),
            },
            "templates": [self._tpl_view(t, full=True) for t in tpls],
            "bursts": [self._burst_view(b, full=True) for b in self.bursts],
            "burst_threshold": self.burst_stats,
            "entities": {
                **{
                    k: v
                    for k, v in self._entities_view().items()
                    if k not in ("list", "sample", "full_list")
                },
                "list": [
                    {"entity": e, "kind": v["kind"], "templates": v["templates"]}
                    for e, v in sorted(self.entities["flagged"].items())
                ],
            },
            "levels": dict(self.an.level_totals),
            "checks": self.checks,
            "warnings": self.warnings,
        }

    def write(self, path: Path) -> Dict[str, Any]:
        """Write full detail (``.json``) or the entity list (``.csv`` / ``.txt``)."""
        if path.suffix.lower() == ".txt":
            with skill_context.open_write(path) as f:
                for e in sorted(self.entities["flagged"]):
                    f.write(e + "\n")
            return {
                "path": str(path),
                "rows": len(self.entities["flagged"]),
                "kind": "entity list (one per line)",
            }
        if path.suffix.lower() == ".csv":
            with skill_context.open_write(path, newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "entity",
                        "kind",
                        "templates",
                        "template_text",
                        "generator=" + OUTPUT_MARKER,
                    ]
                )
                by_id = {t.tid: t for t in self.anomalies}
                for e, v in sorted(self.entities["flagged"].items()):
                    texts = " || ".join(
                        _short(by_id[i].key, 120) for i in v["templates"] if i in by_id
                    )
                    writer.writerow([e, v["kind"], " ".join(v["templates"]), texts, ""])
            return {
                "path": str(path),
                "rows": len(self.entities["flagged"]),
                "kind": "entity list (CSV)",
            }
        payload = self.full()
        with skill_context.open_write(path) as f:
            json.dump(payload, f, indent=1, ensure_ascii=False, default=str)
        return {
            "path": str(path),
            "templates": len(payload["templates"]),
            "kind": "full detail (JSON)",
        }


def _fmt_offset(seconds: int) -> str:
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def _output_refusal(out: Path, inputs: Sequence[Path]) -> Optional[str]:
    """Why the output file may not be written, before the scope check."""
    resolved = out.resolve()
    if any(resolved == p.resolve() for p in inputs):
        return (
            f"output_path '{out}' is one of the input logs; inputs are never written."
        )
    if resolved.exists():
        return (
            f"'{out}' already exists: triage_logs never overwrites a file and never "
            "writes to its input logs. Pick a new name."
        )
    if out.suffix.lower() not in (".json", ".csv", ".txt"):
        return (
            "output_path must end in .json (full detail), or .csv or .txt (the "
            f"anomalous-entity list); got '{out.name}'."
        )
    return None


@tool(timeout=DEFAULT_TIME_BUDGET_S + 120, display_label="Triaging logs")
def triage_logs(
    paths: List[str],
    glob: str = "",
    timestamp_regex: str = "",
    level_regex: str = "",
    host_regex: str = "",
    entity_regex: str = "",
    bucket: str = "",
    top_n: int = DEFAULT_TOP_N,
    output_path: str = "",
) -> dict:
    """Find what went wrong in log files: call this first for any log question.

    Reads every file in full (read-only, any size) and separates steady
    baseline templates from anomalies. Returns checks, coverage, anomalies,
    bursts, baseline, entities and warnings; quote its numbers exactly.

    Args:
        paths: Log files and/or folders (folders are walked recursively).
        glob: Optional file-name filter for folders, e.g. "*.log;*.log.gz".
        timestamp_regex: Override when the format is misread: a regex with
            (?P<ts>...) or year/month/day/hour/minute[/second] groups.
        level_regex: Optional regex whose (?P<level>...) or first group is the level.
        host_regex: Optional regex whose (?P<host>...) or first group is the host.
        entity_regex: Optional regex for the ids to list, e.g. "(blk_-?[0-9]+)";
            by default the id kind is learned from the data.
        bucket: Optional time bucket such as "1min", "5min" or "1h"; chosen
            automatically from the range when empty.
        top_n: How many anomalies to return (1-50).
        output_path: Optional new file: .csv or .txt gets the full
            anomalous-entity list, .json the full detail.
    """
    if isinstance(paths, str):
        paths = [p for p in re.split(r"[\n;]", paths) if p.strip()]
    if not paths:
        return {
            "status": "error",
            "error": "paths is empty: give log files or a folder.",
        }
    options = TriageOptions(
        glob=glob or None,
        timestamp_regex=timestamp_regex or None,
        level_regex=level_regex or None,
        host_regex=host_regex or None,
        entity_regex=entity_regex or None,
        bucket=bucket or None,
        top_n=top_n,
    )
    try:
        result = triage(paths, options)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    if not result.an.files:
        reasons = "; ".join(
            f"{Path(s['file']).name}: {s['reason']}" for s in result.skipped[:5]
        )
        return {
            "status": "error",
            "error": "No readable log files found"
            + (f" ({reasons})." if reasons else "."),
        }
    compact = result.compact(top_n)
    if output_path:
        out = Path(os.path.expanduser(output_path.strip().strip('"')))
        refusal = _output_refusal(out, [Path(f.path) for f in result.an.files])
        if refusal:
            compact["output"] = {"written": False, "error": refusal}
        else:
            try:
                compact["output"] = {"written": True, **result.write(out)}
            except SkillPermissionError as exc:
                compact["output"] = {"written": False, "error": str(exc)}
            except OSError as exc:
                log.error("triage_logs could not write %s: %s", out, exc)
                compact["output"] = {
                    "written": False,
                    "error": f"could not write {out}: {exc}",
                }
    return compact
