# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The streaming pass (``Analyzer``), template classification, bursts, clocks and entities."""

from __future__ import annotations

import gzip
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .limits import (
    BASELINE_MIN_QUARTERS,
    BASELINE_MIN_SPAN_PCT,
    BLAST_HOST_SHARE,
    BLAST_MIN_HOSTS,
    BOM,
    BURST_MEDIAN_FACTOR,
    BURST_MIN_BUCKET,
    BURST_MIN_LINES,
    BURST_SIGMAS,
    CLUSTER_SHARE,
    CLUSTER_WINDOW_FRAC,
    DEFAULT_TIME_BUDGET_S,
    DEFAULT_TOP_N,
    ENTITY_BASELINE_MIN_SHARE,
    ENTITY_SHARE_IMPLAUSIBLE,
    ENTITY_SHARE_MIN_TOTAL,
    ENTITY_SHARE_WARN,
    ESCALATION_FACTOR,
    ESCALATION_MIN_COUNT,
    MAX_BUCKETS,
    MAX_CELL_HOSTS,
    MAX_ENTITIES_PER_TEMPLATE,
    MAX_ENTITIES_TOTAL,
    MAX_ENTITY_BUCKET_PAIRS,
    MAX_ENTITY_KINDS,
    MAX_HOSTS_PER_TEMPLATE,
    MAX_HOSTS_TOTAL,
    MAX_TEMPLATES,
    MERGE_MIN_VARIANTS,
    NEW_TEMPLATE_EXPECTED_BEFORE,
    NEW_TEMPLATE_MIN_FIRST_PCT,
    OUT_SAMPLE_CHARS,
    OUT_SAMPLES,
    OUT_TEMPLATE_CHARS,
    REPLACEMENT_CHAR,
    SHORT_SPAN_PCT,
    SPIKE_FACTOR,
    SPIKE_MIN_LINES,
    TARGET_BUCKETS,
)
from .parsing import (
    _CONT_RE,
    _PLACEHOLDER,
    _SHAPE_RE,
    FIXED_KIND_REGEX,
    PARSERS,
    SEVERITY_NAMES,
    YEARLESS,
    CustomParser,
    FileContext,
    Rec,
    _group_value,
    _regex_entities,
    _shift_years,
    auto_entities,
    compile_regex,
    exception_class,
    keyword_severity,
    kind_regex,
    level_severity,
    mask_message,
    probe_regex,
)
from .reading import (
    _BudgetSpent,
    _mtime,
    _open_text,
    _read_line,
    _sample_lines,
    _year_hint,
    detect_format,
    file_labels,
)

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
    header: int = 0
    long_lines: int = 0
    entries: int = 0
    continuation: int = 0
    unparsed: int = 0
    no_timestamp: int = 0
    unparsed_samples: List[Tuple[int, str]] = field(default_factory=list)
    line_samples: List[Tuple[int, str]] = field(default_factory=list)
    first: Optional[float] = None
    last: Optional[float] = None
    tz_aware: int = 0
    tz_naive: int = 0
    offsets: Counter = field(default_factory=Counter)
    truncated_at_line: Optional[int] = None
    error: Optional[str] = None
    encoding: str = "utf-8"
    undecodable_chars: int = 0
    undecodable_lines: int = 0
    undecodable_first_line: Optional[int] = None
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
    #: Keep only anomaly templates at this level or above in the entity list.
    entity_min_level: Optional[str] = None
    #: Leave out anomaly templates that touch more than this share of all entities.
    entity_max_share: Optional[float] = None
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


def parse_min_level(text: Optional[str]) -> Optional[int]:
    """``entity_min_level`` as a severity number; None when not given.

    Raises:
        ValueError: the text names no level.
    """
    if text is None or not str(text).strip():
        return None
    sev = level_severity(str(text))
    if sev is None:
        raise ValueError(
            f"entity_min_level {text!r} is not a level; use one of "
            f"{', '.join(SEVERITY_NAMES)}."
        )
    return sev


def parse_max_share(value: Optional[float]) -> Optional[float]:
    """``entity_max_share`` as a fraction in (0, 1]; None when not given.

    Raises:
        ValueError: the value is not a number in that range.
    """
    if value is None or value == "" or value == 0:
        return None
    try:
        share = float(value)
    except (TypeError, ValueError):
        share = -1.0
    if not 0.0 < share <= 1.0:
        raise ValueError(
            f"entity_max_share {value!r} is not a share; use a number above 0 and "
            "up to 1, such as 0.33."
        )
    return share


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
        self.level_re = compile_regex(options.level_regex, "level_regex")
        self.host_re = compile_regex(options.host_regex, "host_regex")
        entity_re = compile_regex(options.entity_regex, "entity_regex")
        self.entity_patterns: Tuple["re.Pattern[str]", ...] = (
            (entity_re,) if entity_re is not None else ()
        )
        self.custom_entities = entity_re is not None
        self.entity_min_sev = parse_min_level(options.entity_min_level)
        self.entity_max_share = parse_max_share(options.entity_max_share)
        self.probed = False
        self.bucket_s: float = parse_bucket(options.bucket) or 60.0
        self.bucket_fixed = options.bucket not in (None, "")
        self.bucket_asked = self.bucket_s
        # Lowest and highest bucket seen while streaming, and how many
        # buckets may lie between them before they are widened.
        self.bmin: Optional[int] = None
        self.bmax = 0
        self.bucket_cap = MAX_BUCKETS * (20 if self.bucket_fixed else 1)
        self.coarsened = False
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
        names, hosts = file_labels(paths)
        for idx, path in enumerate(paths):
            ctx = FileContext(idx, path, _year_hint(path))
            ctx.stem = hosts[idx]
            report = FileReport(path=str(path), name=names[idx])
            self.files.append(report)
            try:
                sample = _sample_lines(path)
            except (OSError, EOFError, gzip.BadGzipFile) as exc:
                report.error = f"could not read: {exc}"
                plans.append((path, ctx, {"format": "unreadable"}))
                continue
            if not self.probed and sample:
                self._probe_patterns(sample)
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

    def _probe_patterns(self, sample: Sequence[str]) -> None:
        """Time every regex the caller supplied, once, on the first sample."""
        self.probed = True
        named = (
            ("timestamp_regex", self.custom.pattern if self.custom else None),
            ("level_regex", self.level_re),
            ("host_regex", self.host_re),
            ("entity_regex", self.entity_patterns[0] if self.custom_entities else None),
        )
        for what, pattern in named:
            if pattern is not None:
                probe_regex(pattern, what, sample)

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
        deadline = self.started + self.opt.time_budget_s
        n = 0
        try:
            with _open_text(path) as f:
                report.encoding = f.encoding or report.encoding
                while True:
                    try:
                        raw, cut = _read_line(f, deadline)
                    except _BudgetSpent:
                        report.truncated_at_line = n + 1
                        self.out_of_time = True
                        break
                    if not raw:
                        break
                    n += 1
                    report.lines = n
                    report.long_lines += cut
                    if REPLACEMENT_CHAR in raw:
                        report.undecodable_chars += raw.count(REPLACEMENT_CHAR)
                        report.undecodable_lines += 1
                        if report.undecodable_first_line is None:
                            report.undecodable_first_line = n
                    if n % 4096 == 0 and time.monotonic() > deadline:
                        report.truncated_at_line = n
                        self.out_of_time = True
                        break
                    line = raw.rstrip("\r\n")
                    if not line.strip():
                        report.blank += 1
                        continue
                    if n == 1:
                        line = line.lstrip(BOM)
                    if line == ctx.csv_header_line and not report.header:
                        report.header = 1
                        continue
                    if len(report.line_samples) < OUT_SAMPLES:
                        report.line_samples.append((n, line[:OUT_SAMPLE_CHARS]))
                    rec = None if plain else self._parse_line(line, ctx)
                    if ctx.csv_header is not None:
                        # A quoted field may run over several lines.
                        quotes = line.count('"') % 2
                        if rec is not None:
                            ctx.in_quote = quotes == 1
                        elif ctx.in_quote:
                            ctx.in_quote = quotes == 0
                            report.continuation += 1
                            continue
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
            if self.bmin is None:
                self.bmin = self.bmax = bucket
            elif bucket < self.bmin:
                self.bmin = bucket
            elif bucket > self.bmax:
                self.bmax = bucket
            if self.bmax - self.bmin >= self.bucket_cap:
                self._coarsen()
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

    def _coarsen(self) -> None:
        """Widen the buckets mid-stream so their number stays bounded.

        The bucket comes from a sample, and a compressed file is sampled from
        its head only: a multi-day ``.gz`` starts at buckets of a second or
        two. Without this every template would keep one cell per second until
        the file ends.
        """
        span_s = (self.bmax - self.bmin + 1) * self.bucket_s
        factor = 2
        if not self.bucket_fixed:
            factor = max(2, int(math.ceil(choose_bucket(span_s) / self.bucket_s)))
        self._rebase(self.bmin, factor)
        self.bmax = (self.bmax - self.bmin) // factor
        self.bmin = 0
        self.coarsened = True
        self.ent_pairs = sum(
            len(marks) for t in self._all_templates() for marks in t.ents.values()
        )

    def finalize(self) -> None:
        """Rebucket if the sample misjudged the range, then merge templates."""
        self._unify_id_shapes()
        if self.gmin is None or self.gmax is None:
            return
        lo = self._bucket(self.gmin)
        hi = self._bucket(self.gmax)
        factor = 1
        if not self.bucket_fixed and (self.coarsened or hi - lo + 1 > MAX_BUCKETS):
            target = choose_bucket(max(self.gmax - self.gmin, 1.0))
            factor = max(1, int(math.ceil(target / self.bucket_s)))
        self._rebase(lo, factor)
        if self.bucket_fixed and self.bucket_s != self.bucket_asked:
            self.warnings.append(
                f"bucket {self.opt.bucket} gave more than {self.bucket_cap} buckets; "
                f"widened to {self._fmt_duration(self.bucket_s)}."
            )
        self._merge_templates()

    def _unify_id_shapes(self) -> None:
        """One kind per id prefix: a ``vol-`` id made only of digits is still
        a ``vol-H`` id when the others are hex."""
        widest: Dict[str, str] = {}
        for kind in self.kinds:
            stem, shape = kind[:-1], kind[-1]
            if kind not in FIXED_KIND_REGEX and shape in _SHAPE_RE:
                widest[stem] = max(widest.get(stem, "N"), shape, key="NHA".index)
        rename = {
            stem + shape: stem + widest[stem]
            for stem in widest
            for shape in _SHAPE_RE
            if shape != widest[stem] and stem + shape in self.kinds
        }
        if not rename:
            return
        for e, kind in self.ent_kind.items():
            if kind in rename:
                self.ent_kind[e] = rename[kind]
        for old, new in rename.items():
            merged = self.kinds[new] + self.kinds.pop(old)
            self.kinds[new] = merged[:5]

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

    Baseline templates are left out of the series: steady error lines
    would otherwise raise the median until a real burst disappears into it. A baseline template that spikes is an anomaly,
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
    # The caller's own tightening: error-bearing templates only.
    below_level: List[str] = []
    if an.entity_min_sev is not None:
        below_level = [t.tid for t in anomalies if t.sev < an.entity_min_sev]
        anomalies = [t for t in anomalies if t.sev >= an.entity_min_sev]
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
                    e,
                    {
                        "templates": [],
                        "kind": an.ent_kind.get(e),
                        "sev": 0,
                        "reach": 1.0,
                        "first": None,
                        "last": None,
                        "routine": e in routine,
                    },
                )
                if tid not in entry["templates"] and len(entry["templates"]) < 5:
                    entry["templates"].append(tid)
                tpl = tpl_by_id[tid]
                entry["sev"] = max(entry["sev"], tpl.sev)
                entry["reach"] = min(entry["reach"], reach[tid])
                window = windows[tid]
                marks = [
                    b for b in tpl.ents[e] if b >= 0 and (window is None or b in window)
                ]
                if marks:
                    lo = an.origin + min(marks) * an.bucket_s
                    hi = an.origin + (max(marks) + 1) * an.bucket_s
                    entry["first"] = (
                        lo if entry["first"] is None else min(entry["first"], lo)
                    )
                    entry["last"] = (
                        hi if entry["last"] is None else max(entry["last"], hi)
                    )
        return flagged

    windows = {t.tid: _anomaly_window(t) for t in anomalies}
    # How much of the population each anomaly template touches on its own.
    reach = {
        tid: sum(1 for e in hits if an.ent_kind.get(e) in used_set) / max(1, total)
        for tid, hits in hits_by_tpl.items()
    }
    active = [tid for tid, hits in hits_by_tpl.items() if hits]
    # The caller's own tightening: templates rare relative to all entities.
    too_broad: List[str] = []
    if an.entity_max_share is not None:
        too_broad = [tid for tid in active if reach[tid] > an.entity_max_share]
        active = [tid for tid in active if tid not in too_broad]
    flagged = collect(active)
    narrowed: List[str] = []

    def routine_heavy(fl: Dict[str, Any]) -> bool:
        if (
            not fl
            or total < ENTITY_SHARE_MIN_TOTAL
            or len(fl) / total <= ENTITY_SHARE_WARN
        ):
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
    filters: Dict[str, Any] = {}
    if an.entity_min_sev is not None:
        filters["entity_min_level"] = {
            "level": SEVERITY_NAMES[an.entity_min_sev],
            "templates_left_out": below_level,
        }
    if an.entity_max_share is not None:
        filters["entity_max_share"] = {
            "share": an.entity_max_share,
            "templates_left_out": too_broad,
        }
    broadest = None
    if per_template:
        tid = max(per_template, key=lambda t: (reach[t], t))
        broadest = {
            "id": tid,
            "severity": SEVERITY_NAMES[tpl_by_id[tid].sev],
            "entities": per_template[tid],
            "share": round(reach[tid], 4),
        }
    # The share means something only against a population the routine lines
    # also name: ids seen in anomaly lines alone are all flagged by construction.
    in_routine = sum(
        1 for e, k in an.ent_kind.items() if k in used_set and e in routine
    )
    population_known = total > 0 and in_routine >= ENTITY_BASELINE_MIN_SHARE * total
    implausible = None
    if (
        population_known
        and total >= ENTITY_SHARE_MIN_TOTAL
        and share > ENTITY_SHARE_IMPLAUSIBLE
    ):
        below_error = sum(1 for tid in per_template if tpl_by_id[tid].sev < 3)
        implausible = (
            f"IMPLAUSIBLE ENTITY LIST: {len(flagged)} of {total} entities ({share:.0%}) "
            "are flagged. A criterion that matches most entities is not an anomaly "
            "criterion: it describes normal operation, and real anomalies are usually "
            "a small minority. Do not deliver this list as the anomalous ones. "
            "Tighten it with the tool: call again with entity_max_share="
            f"{ENTITY_SHARE_IMPLAUSIBLE:.2f} to keep only templates that are rare "
            "relative to all entities"
            + (
                f" (the broadest, {broadest['id']}, touches {broadest['share']:.0%})"
                if broadest
                else ""
            )
            + ', or with entity_min_level="ERROR" to keep error-bearing templates only'
            + (
                f" ({below_error} of the {len(per_template)} templates behind the "
                "list are below ERROR)"
                if below_error
                else ""
            )
            + ". If the incident really touched this share, report the share as the "
            "finding, not the list."
        )
        warnings.append(implausible)
    elif routine_heavy(flagged):
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
        "in_routine": in_routine,
        "population_known": population_known,
        "filters": filters,
        "broadest": broadest,
        "implausible": implausible,
        "warnings": warnings,
    }
