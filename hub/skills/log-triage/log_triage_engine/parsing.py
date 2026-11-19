# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Severity, timestamps, template masking, entity ids and the per-format line parsers."""

from __future__ import annotations

import csv
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # the regex parser moved in Python 3.11
    from re import _parser as _regex_parser
except ImportError:  # pragma: no cover - Python 3.10
    import sre_parse as _regex_parser

from .limits import (
    BOM,
    MAX_MESSAGE_CHARS,
    REGEX_PROBE_BUDGET_S,
    REGEX_PROBE_LINES,
    TS_SEARCH_CHARS,
)

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
                   r"out of memory|outofmemory\w*|oom)\b", re.I)),
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
    "container_id|pod|upstream|upstream_addr|client_id|account_id|tenant|"
    "tenant_id|device_id|txn_id|transaction_id|correlation_id|correlationid|node_id|"
    "instance_id|host_id|shard|partition|session|sess|token|trace|request|req|txn|"
    "transaction|job|task|order|block|container|device|account|correlation"
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
#: word + separator + id with a digit: job-17, vol_0042, req-a3f9c2d1, sess:x9k2.
_PREFIXED_ID_RE = re.compile(
    r"(?<![\w.-])([A-Za-z]{1,16})([-_:#])([A-Za-z0-9]*\d[A-Za-z0-9]*)(?![\w-])"
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
    ``job-N`` / ``req-H`` / ``sess:A`` (a word, a separator and an id with a
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
            prefix, sep, ident = m.groups()
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
    return f"({re.escape(word)}{sep}{_SHAPE_RE[shape]})"


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
    "timegenerated", "timecreated", "time_local", "date and time",
)  # fmt: skip
_LEVEL_KEYS = (
    "level", "severity", "lvl", "loglevel", "log_level", "levelname", "level_name",
    "log.level", "severitytext", "severity_text", "@level", "priority", "status_level",
    "leveldisplayname", "entrytype",
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
    "machinename", "computername",
)  # fmt: skip
_SERVICE_KEYS = (
    "service", "service.name", "service_name", "app", "application", "component",
    "logger", "logger_name", "loggername", "module", "source", "providername",
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
        # The timestamp follows a level or logger name instead of leading.
        self.ts_search = False
        self.lead_host = False
        # CSV export: lower-cased column names, delimiter, the header text,
        # and whether a quoted field is still open on the current line.
        self.csv_header: Optional[List[str]] = None
        self.csv_delim = ","
        self.csv_header_line: Optional[str] = None
        self.in_quote = False
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


#: Where a timestamp may start inside a line: after a separator, at a digit
#: or a month or weekday name.
_TS_START = re.compile(r"(?<=[\s|,;:\[(<\"'=])(?=\d|[A-Z][a-z]{2}[ ,])")
_LEAD_SPLIT = re.compile(r"[\s:|,;\[\]()<>\"'=]+")


def _search_ts(line: str, ctx: FileContext) -> Optional[Tuple[TsMatch, int]]:
    """The first timestamp that starts after a separator near the line start."""
    for m in _TS_START.finditer(line, 1, TS_SEARCH_CHARS):
        tm = parse_timestamp(line, m.start(), prefer=ctx.prefer_ts, year_hint=ctx.year)
        if tm is not None:
            return tm, m.start()
    return None


def _locate_ts(
    line: str, ctx: FileContext, search: Optional[bool] = None
) -> Optional[Tuple[TsMatch, str, int]]:
    """``(timestamp, lead, end)`` for a line that carries its own timestamp.

    The timestamp leads the line (after ``[`` or a syslog ``<pri>``). When the
    file's sample put it after a level or logger name (``ctx.ts_search``), it
    is looked for within the first TS_SEARCH_CHARS and ``lead`` is the text
    before it. ``end`` is where the rest of the line starts.
    """
    pos = 0
    bracket = False
    lead = ""
    if line.startswith("["):
        pos, bracket = 1, True
    elif line.startswith("<"):
        close = line.find(">", 1, 5)
        if close != -1 and line[1:close].isdigit():
            pos = close + 1
    tm = parse_timestamp(line, pos, prefer=ctx.prefer_ts, year_hint=ctx.year)
    if tm is None:
        if not (ctx.ts_search if search is None else search):
            return None
        found = _search_ts(line, ctx)
        if found is None:
            return None
        tm, pos = found
        bracket = line[pos - 1] == "["
        lead = line[: pos - 1] if bracket else line[:pos]
    end = tm.end
    if bracket:
        close = line.find("]", end, end + 40)
        if close == -1:
            return None
        end = close + 1
    if end < len(line) and line[end] not in " \t|,;]-:":
        return None
    return tm, lead, end


def _split_lead(lead: str) -> Tuple[Optional[str], List[str]]:
    """The level token and the other tokens of the text before a timestamp."""
    level = None
    rest = []
    for token in _LEAD_SPLIT.split(lead):
        if not token:
            continue
        if level is None and level_severity(token) is not None:
            level = token
        else:
            rest.append(token)
    return level, rest


def _parse_generic(line: str, ctx: FileContext) -> Optional[Rec]:
    located = _locate_ts(line, ctx)
    if located is None:
        return None
    tm, lead, end = located
    rec = Rec()
    rec.ts, rec.has_tz, rec.offset, rec.ts_kind = (
        tm.epoch,
        tm.has_tz,
        tm.offset,
        tm.kind,
    )
    lead_rest: List[str] = []
    if lead:
        lead_level, lead_rest = _split_lead(lead)
        if lead_level is not None:
            rec.sev, rec.level = level_severity(lead_level), lead_level.upper()
        if ctx.lead_host and len(lead_rest) == 1:
            rec.host = lead_rest.pop()
    rest = line[end:].lstrip(" \t|,;]-:")
    tokens = rest.split(" ")
    drop = set()
    if rec.sev is not None:
        pass  # the level came before the timestamp
    elif ctx.level_pos is not None and ctx.level_pos < len(tokens):
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
    if rec.host is None and ctx.host_pos is not None and ctx.host_pos < len(tokens):
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
    # A logger or component name before the timestamp is part of the event.
    rec.msg = " ".join(lead_rest + [msg]) if lead_rest else msg
    return rec


_CSV_DELIMS = (",", ";", "\t")


def _csv_fields(line: str, delim: str) -> List[str]:
    try:
        return next(csv.reader([line], delimiter=delim), [])
    except csv.Error:
        return []


def _learn_csv(line: str, ctx: FileContext) -> None:
    """Remember the columns of a CSV export whose header names a time column."""
    for delim in _CSV_DELIMS:
        if delim not in line:
            continue
        names = [f.strip().lower() for f in _csv_fields(line, delim)]
        if (
            len(names) >= 2
            and all(len(n) <= 40 for n in names)
            and any(n in _TS_KEYS for n in names)
            and not any(parse_timestamp(n) for n in names)
        ):
            ctx.csv_header, ctx.csv_delim, ctx.csv_header_line = names, delim, line
            return


def _parse_csv(line: str, ctx: FileContext) -> Optional[Rec]:
    """A row of a CSV export, read by the column names of its header.

    Columns past the header (an event viewer export leaves the message column
    unnamed) are the message.
    """
    names = ctx.csv_header
    if names is None:
        return None
    fields = _csv_fields(line, ctx.csv_delim)
    if len(fields) < 2:
        return None
    flat: Dict[str, Any] = {n: v.strip() for n, v in zip(names, fields) if n}
    extra = fields[len(names) :]
    if extra and not any(k in flat for k in _MSG_KEYS):
        flat["message"] = ctx.csv_delim.join(extra).strip()
    rec = _apply_mapping(flat, ctx, Rec())
    return rec if rec.ts is not None else None


def _csv_match_rate(sample: Sequence[str], ctx: FileContext) -> float:
    """Share of sample rows that parse; lines inside a quoted field do not count."""
    ok = rows = 0
    in_quote = False
    for ln in sample:
        if not ln.strip() or ln == ctx.csv_header_line:
            continue
        parsed = _parse_csv(ln, ctx) is not None
        if parsed:
            ok += 1
            rows += 1
            in_quote = ln.count('"') % 2 == 1
        elif in_quote:
            in_quote = ln.count('"') % 2 == 0
        else:
            rows += 1
    return ok / rows if rows else 0.0


PARSERS: Dict[str, Callable[[str, FileContext], Optional[Rec]]] = {
    "json": _parse_json,
    "csv": _parse_csv,
    "syslog5424": _parse_syslog5424,
    "access": _parse_access,
    "apache_error": _parse_apache_error,
    "glog": _parse_glog,
    "syslog": _parse_syslog3164,
    "logfmt": _parse_logfmt,
    "timestamped": _parse_generic,
}


def _self_repeating(items: Sequence[Tuple[Any, Any]]) -> bool:
    """True when a group's content can meet itself with nothing fixed between.

    Every part is a variable repeat, and leaving out the optional ones puts
    the same repeat next to itself: ``(\\w+\\s*)+``, ``(a+)*``, ``(\\d+,?)+``.
    Repeating such a group lets the same text be split in a number of ways
    that doubles with every character, and a line that does not match tries
    them all. ``(\\s+\\S+)*`` is left to the timing probe: its two repeats
    differ, so they may well not overlap.
    """
    if not items:
        return False
    needed: List[str] = []
    for op, av in items:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT"):
            if av[0] == av[1]:
                return False
            if av[0] > 0:
                needed.append(repr(av[2]))
        elif name == "SUBPATTERN":
            if not _self_repeating(av[3]):
                return False
            needed.append(repr(av[3]))
        elif name == "BRANCH":
            if not any(_self_repeating(alt) for alt in av[1]):
                return False
            needed.append(repr(av[1]))
        else:
            return False
    if len(needed) <= 1:
        return True
    return any(a == b for a, b in zip(needed, needed[1:] + needed[:1]))


def _has_nested_repeat(items: Sequence[Tuple[Any, Any]]) -> bool:
    for op, av in items:
        name = str(op)
        if name in ("MAX_REPEAT", "MIN_REPEAT"):
            body = av[2]
            if av[1] > 1 and any(str(o) in ("SUBPATTERN", "BRANCH") for o, _ in body):
                if _self_repeating(body):
                    return True
            if _has_nested_repeat(body):
                return True
        elif name in ("SUBPATTERN", "ATOMIC_GROUP"):
            if _has_nested_repeat(av[3] if name == "SUBPATTERN" else av):
                return True
        elif name == "BRANCH":
            if any(_has_nested_repeat(alt) for alt in av[1]):
                return True
        elif name in ("ASSERT", "ASSERT_NOT"):
            if _has_nested_repeat(av[1]):
                return True
    return False


def compile_regex(pattern: Optional[str], what: str) -> Optional["re.Pattern[str]"]:
    """Compile a regex the caller supplied; None when it is empty.

    Raises:
        ValueError: naming the argument, when the pattern does not compile or
            repeats a group whose content repeats on its own.
    """
    if not pattern:
        return None
    try:
        compiled = re.compile(pattern)
        nested = _has_nested_repeat(list(_regex_parser.parse(pattern)))
    except (re.error, RecursionError, OverflowError) as exc:
        raise ValueError(f"{what} is not a valid regular expression: {exc}") from exc
    if nested:
        raise ValueError(
            f"{what} repeats a group whose content can repeat on its own, like "
            "(\\w+\\s*)+ or (a+)*. One line that does not match can keep such a "
            "pattern busy for hours. Rewrite it without a quantifier inside the "
            "repeated group, for example [\\w\\s]+."
        )
    return compiled


#: Runs of one kind of character: what runaway backtracking feeds on.
_PROBE_RUNS = tuple(
    unit * (48 // len(unit)) for unit in ("a", "1", " ", "a ", "a1", "a.")
)


def probe_regex(pattern: "re.Pattern[str]", what: str, lines: Sequence[str]) -> None:
    """Time a caller's regex on sample lines before it meets the whole log.

    A running ``re`` search cannot be stopped, so the pattern is tried on
    prefixes that grow a little at a time, with and without a character that
    makes an end anchor fail. Runaway backtracking then shows as a slow search
    on a short prefix, long before a full line would hang the call.

    Raises:
        ValueError: naming the argument, when one search passes the budget.
    """
    chosen = list(lines[: REGEX_PROBE_LINES // 2])
    chosen += sorted(lines, key=len, reverse=True)[: REGEX_PROBE_LINES // 2]
    for line in list(_PROBE_RUNS) + chosen:
        for tail in ("", "\x00"):
            n = min(8, len(line))
            while True:
                text = line[:n] + tail
                started = time.perf_counter()
                pattern.search(text)
                took = time.perf_counter() - started
                if took > REGEX_PROBE_BUDGET_S:
                    raise ValueError(
                        f"{what} is too slow: {took:.2f} s on a line of {len(text)} "
                        "characters, and it gets worse with length. Overlapping "
                        "or nested quantifiers cause this; simplify the pattern."
                    )
                if n >= len(line):
                    break
                n = min(len(line), n + 2 if n < 48 else n * 2)


class CustomParser:
    """Line parser built from the caller's ``timestamp_regex``."""

    def __init__(self, timestamp_regex: str) -> None:
        self.pattern = compile_regex(timestamp_regex, "timestamp_regex")
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


def _group_value(m: "re.Match[str]", name: str) -> Optional[str]:
    if name in m.re.groupindex:
        return m.group(name)
    if m.re.groups:
        return next((g for g in m.groups() if g), None)
    return m.group(0)
