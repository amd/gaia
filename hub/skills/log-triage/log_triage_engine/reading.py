# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opening, sampling and labelling log files, and learning each file's format."""

from __future__ import annotations

import gzip
import io
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.skills import context as skill_context

from .limits import (
    BOM,
    MAX_LINE_CHARS,
    READ_CHUNK_CHARS,
    SAMPLE_HEAD_LINES,
    SAMPLE_MID_LINES,
    SAMPLE_TAIL_BYTES,
)
from .parsing import (
    _CONT_RE,
    _HOST_SHAPE,
    PARSERS,
    CustomParser,
    FileContext,
    _csv_match_rate,
    _learn_csv,
    _locate_ts,
    _search_ts,
    _split_lead,
    _strip_token,
    level_severity,
    parse_timestamp,
)

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


class _BudgetSpent(Exception):
    """The time budget ran out while a single over-long line was skipped."""


def _read_line(f: io.TextIOBase, deadline: float) -> Tuple[str, bool]:
    """The next line cut at MAX_LINE_CHARS, and whether it was longer.

    ``readline`` gets a limit, so a file with no line breaks (a one-line JSON
    export, a decompression bomb) is never held in memory whole. The rest of
    an over-long line is read in chunks and dropped.
    """
    raw = f.readline(MAX_LINE_CHARS + 1)
    if len(raw) <= MAX_LINE_CHARS or raw.endswith("\n"):
        return raw, False
    while True:
        more = f.readline(READ_CHUNK_CHARS)
        if not more or more.endswith("\n"):
            return raw[:MAX_LINE_CHARS], True
        if time.monotonic() > deadline:
            raise _BudgetSpent()


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
            line = f.readline(MAX_LINE_CHARS + 1)
            if not line:
                return [ln.rstrip("\r\n") for ln in lines]
            lines.append(line[:MAX_LINE_CHARS])
            if len(line) > MAX_LINE_CHARS and not line.endswith("\n"):
                break  # an over-long line: the sample stops here
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


def _below(folder: Path, root: str) -> str:
    rel = os.path.relpath(folder, root).replace(os.sep, "/")
    return "" if rel == "." else rel


def file_labels(paths: Sequence[Path]) -> Tuple[List[str], List[str]]:
    """The display name and the fallback host of every input file.

    A file is shown by its name, and lines that name no host get one from the
    file stem (``app.log`` and ``app.log.1`` are both ``app``). When several
    folders hold the same file name, as in one folder per host, the name is
    the path below their common folder and the host is the folder.
    """
    names = [p.name for p in paths]
    hosts = [p.name.split(".")[0] or p.name for p in paths]
    folders = [p.parent.resolve() for p in paths]

    def groups(keys: Sequence[str]) -> List[List[int]]:
        found: Dict[str, List[int]] = {}
        for i, key in enumerate(keys):
            found.setdefault(key, []).append(i)
        return [g for g in found.values() if len({folders[i] for i in g}) > 1]

    for group in groups(names):
        try:
            root = os.path.commonpath([str(folders[i]) for i in group])
        except ValueError:  # different drives
            for i in group:
                names[i] = str(paths[i])
            continue
        for i in group:
            rel = _below(folders[i], root)
            names[i] = f"{rel}/{paths[i].name}" if rel else paths[i].name
    for group in groups(hosts):
        try:
            root = os.path.commonpath([str(folders[i]) for i in group])
        except ValueError:
            continue
        rels = {folders[i]: _below(folders[i], root) for i in group}
        tops = {folder: rel.split("/")[0] for folder, rel in rels.items()}
        # The first folder below the common one, unless two hosts share it.
        labels = tops if len(set(tops.values())) == len(tops) else rels
        for i in group:
            hosts[i] = labels[folders[i]] or hosts[i]
    return names, hosts


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

    first = next((ln for ln in sample if ln.strip()), "").lstrip(BOM)
    _learn_csv(first, ctx)
    if ctx.csv_header is not None:
        rate = _csv_match_rate(sample, ctx)
        if rate >= 0.5:
            ctx.formats = ["csv"]
            return {"format": "csv", "match_rate": round(rate, 3), "secondary": []}
        ctx.csv_header = ctx.csv_header_line = None

    # Learn which timestamp layout the timestamped lines use first, and
    # whether the timestamp leads the line or follows a level or logger name.
    kinds = Counter()
    leading = following = 0
    for ln in headers[:200]:
        pos = 1 if ln.startswith("[") else 0
        tm = parse_timestamp(ln, pos, year_hint=ctx.year)
        if tm:
            leading += 1
        else:
            found = _search_ts(ln, ctx)
            tm = found[0] if found else None
            following += tm is not None
        if tm:
            kinds[tm.kind] += 1
    if kinds:
        ctx.prefer_ts = kinds.most_common(1)[0][0]
    ctx.ts_search = following > leading and following >= 0.5 * len(headers[:200])
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
    leads: List[List[str]] = []
    for ln in headers[:300]:
        located = _locate_ts(ln, ctx)
        if located is None:
            continue
        _, lead, end = located
        rows.append(ln[end:].lstrip(" \t|,;]-:").split(" ")[:6])
        leads.append(_split_lead(lead)[1])
    if len(rows) < 3:
        return
    n = len(rows)
    # One host-shaped name before the timestamp, shared by many lines: a host.
    named = [t[0] for t in leads if len(t) == 1 and _HOST_SHAPE.match(t[0])]
    ctx.lead_host = len(named) >= 0.9 * n and len(set(named)) <= max(1, 0.5 * n)
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
