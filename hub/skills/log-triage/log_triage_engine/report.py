# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Input resolution, the ``triage`` entry point and the ``TriageResult`` it returns."""

from __future__ import annotations

import csv
import fnmatch
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.skills import context as skill_context
from gaia.skills.errors import SkillPermissionError

from .analysis import (
    Analyzer,
    TriageOptions,
    _Tpl,
    build_entities,
    classify,
    clock_check,
    find_bursts,
)
from .limits import (
    DEFAULT_TOP_N,
    ENTITY_SHARE_IMPLAUSIBLE,
    MAX_FILES,
    MAX_HOSTS_PER_TEMPLATE,
    MAX_HOSTS_TOTAL,
    MAX_LINE_CHARS,
    MAX_TOP_N,
    MIN_PARSED_SHARE,
    NON_LOG_SUFFIXES,
    OUT_BASELINE,
    OUT_BURSTS,
    OUT_ENTITIES,
    OUT_ENTITY_LIST,
    OUT_FILES,
    OUT_HOSTS,
    OUT_PERSISTENT,
    OUT_TEMPLATE_CHARS,
    OUT_TIMELINE,
    OUT_WARNING_CHARS,
    OUT_WARNINGS,
    OUTPUT_MARKER,
    PERSISTENT_READING,
    PERSISTENT_SHORT_RANGE_S,
)
from .parsing import (
    SEVERITY_NAMES,
    format_ts,
)
from .reading import (
    _is_binary,
)

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
    """True for a file an earlier call wrote: each starts with OUTPUT_MARKER."""
    if path.suffix.lower() not in (".json", ".csv", ".txt"):
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
        ValueError: an option is invalid: a bucket that is no duration, or a
            regex that does not compile or backtracks out of control.
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
        # Steady errors are baseline, but with nothing else wrong, or in a
        # short log, they may be the outage itself: report them apart.
        self.persistent: List[_Tpl] = []
        if not self.anomalies or meta["span_s"] <= PERSISTENT_SHORT_RANGE_S:
            self.persistent = sorted(
                (t for t in self.baseline if t.sev >= 3 and t.m.get("timed")),
                key=lambda t: (-t.sev, -t.count),
            )
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
            elif f.no_timestamp > (1 - MIN_PARSED_SHARE) * f.entries:
                out.append(
                    f"{f.name}: {f.no_timestamp} of {f.entries} entries carry no "
                    "timestamp; they are counted but not ranked by time. Pass "
                    "timestamp_regex if the lines do carry one."
                )
            if f.long_lines:
                out.append(
                    f"{f.name}: {f.long_lines} line(s) longer than {MAX_LINE_CHARS} "
                    "characters were cut to that length"
                    + (
                        "; the file is one long line (a JSON array export?), so "
                        "convert it to one record per line."
                        if f.lines == 1
                        else "."
                    )
                )
            if f.undecodable_chars:
                out.append(
                    f"{f.name}: {f.undecodable_chars} character(s) on "
                    f"{f.undecodable_lines} line(s) (first: line "
                    f"{f.undecodable_first_line}) are not valid {f.encoding} and were "
                    "read as U+FFFD; ids and text on those lines may be damaged. If "
                    "the file is in another encoding, convert it to UTF-8 and run again."
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
            accounted = f.entries + f.continuation + f.unparsed + f.blank + f.header
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
        # A file with no usable timestamps was not ranked by time: it fails
        # the parsed-share check too, so the caller fixes the format first.
        untimed = [
            f.name
            for f in an.files
            if f.entries
            and (
                f.format == "plain"
                or f.no_timestamp > (1 - MIN_PARSED_SHARE) * f.entries
            )
        ]
        below = [
            f.name
            for f in an.files
            if f.name in untimed
            or f.entries + f.unparsed
            and f.entries / (f.entries + f.unparsed) < MIN_PARSED_SHARE
        ]
        return {
            "ok": not failed,
            "failed": failed,
            "parsed_share_ok": not below,
            "parsed_share_min": MIN_PARSED_SHARE,
            "files_below_parsed_share": below,
            "time_ranked": not untimed,
            "files_without_timestamps": untimed,
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

    def _persistent_view(self, limit: Optional[int]) -> List[Dict[str, Any]]:
        return [
            {
                "id": t.tid,
                "template": _short(t.key, 100),
                "severity": SEVERITY_NAMES[t.sev],
                "count": t.count,
                "hosts": len(t.hosts),
                "span_pct": round(t.m.get("span_pct", 0.0), 1),
                "first": {"ts": self.ts(t.first), **(self.loc(t.first_loc) or {})},
                "last": {"ts": self.ts(t.last), **(self.loc(t.last_loc) or {})},
                "reading": PERSISTENT_READING,
            }
            for t in self.persistent[:limit]
        ]

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
            if f.header:
                row["header_lines"] = f.header
            if f.long_lines:
                row["long_lines_cut"] = f.long_lines
            if f.undecodable_chars:
                row["undecodable_chars"] = f.undecodable_chars
                row["undecodable_lines"] = f.undecodable_lines
                row["encoding"] = f.encoding
            if f.no_timestamp:
                row["no_timestamp"] = f.no_timestamp
                # What the lines look like, to fit timestamp_regex to.
                row["line_samples"] = [
                    {"line": n, "text": s} for n, s in f.line_samples
                ]
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
            # Not cut to the warning length: this one changes what to deliver.
            **(
                {"entity_list_warning": self.entities["implausible"]}
                if self.entities["implausible"]
                else {}
            ),
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
            "persistent_errors": self._persistent_view(OUT_PERSISTENT),
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
                if k
                in (
                    "ok",
                    "failed",
                    "parsed_share_ok",
                    "files_below_parsed_share",
                    "time_ranked",
                    "files_without_timestamps",
                )
            },
            "warnings": [_short(w, OUT_WARNING_CHARS) for w in self._warnings_view()],
        }
        if len(self.warnings) > OUT_WARNINGS:
            result["warnings"].append(
                f"... {len(self.warnings) - OUT_WARNINGS} more warnings in the full output."
            )
        step = self.next_step()
        if step:
            result["next_step"] = step
        return result

    def _warnings_view(self) -> List[str]:
        """The warnings the compact result shows: the entity ones are never cut off."""
        first = [w for w in self.entities["warnings"] if w in self.warnings]
        rest = [w for w in self.warnings if w not in first]
        return (first + rest)[:OUT_WARNINGS]

    def _context_step(self) -> Optional[str]:
        """Which lines to read next, to see what led up to the finding."""
        if self.bursts and self.bursts[0]["start_loc"]:
            first = self.loc(self.bursts[0]["start_loc"])
            return (
                f"read_file the lines before {first['file']}:{first['line']} (start_line="
                f"{max(1, first['line'] - 30)}) to see what led up to the first burst."
            )
        if self.anomalies and self.anomalies[0].first_loc:
            first = self.loc(self.anomalies[0].first_loc)
            return f"read_file around {first['file']}:{first['line']} to see the context of {self.anomalies[0].tid}."
        if self.persistent and self.persistent[0].first_loc:
            first = self.loc(self.persistent[0].first_loc)
            return (
                f"{self.persistent[0].tid} is {PERSISTENT_READING}. read_file around "
                f"{first['file']}:{first['line']}, then say which reading the evidence "
                "supports; if the log cannot tell, ask the user whether these errors "
                "are expected."
            )
        return None

    def _entity_step(self, output: Optional[Dict[str, Any]]) -> Optional[str]:
        """What to do about the anomalous-entity list: never rebuild it by hand."""
        ent = self.entities
        count, total = len(ent["flagged"]), ent["total"]
        if not count:
            return None
        listed = bool(output and output.get("written") and "flagged" in output)
        if ent["implausible"]:
            return (
                f"The entity list holds {count} of {total} entities ({ent['share']:.0%}): "
                "too many to be the anomalous ones"
                + (f", so {output['path']} is not a deliverable yet" if listed else "")
                + ". Do not deliver it and do not recompute it with your own script. "
                f"Call again with entity_max_share={ENTITY_SHARE_IMPLAUSIBLE:.2f} or "
                'entity_min_level="ERROR" and a new output_path, or report the share '
                "itself as the finding."
            )
        if listed:
            return (
                f"{output['path']} is the anomalous-entity list ({count} of {total} "
                f"entities, {ent['share']:.1%}), with the evidence for each id: give "
                "that file to the user as the list, or filter its rows. Do not "
                "recompute the list with your own script."
            )
        where = (
            "entities.list is the complete list"
            if count <= OUT_ENTITY_LIST
            else "entities.sample is only a sample"
        )
        return (
            f"{count} of {total} entities ({ent['share']:.1%}) are flagged; {where}. "
            "If the user wants the list as a file, call again with output_path="
            "'<new file>.csv' and deliver that file. Do not rebuild the list with "
            "your own script."
        )

    def next_step(self, output: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """What the caller should do next, given the file this call wrote (if any).

        The entity list comes first when a file was written or the list is
        implausible: that is the part a caller otherwise redoes by hand.
        """
        context, entity = self._context_step(), self._entity_step(output)
        entity_first = bool(
            entity and (self.entities["implausible"] or (output or {}).get("written"))
        )
        parts = [entity, context] if entity_first else [context, entity]
        return " ".join(p for p in parts if p) or None

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
            "plausible": not ent["implausible"],
            "by_template": ent["per_template"],
        }
        if flagged and not ent["population_known"]:
            view["share_note"] = (
                "ids of this kind appear almost only in anomaly templates, so the "
                "share is of the ids the anomalies name, not of a known population."
            )
        if ent["broadest"]:
            view["broadest_template"] = ent["broadest"]
        if ent["filters"]:
            view["filters"] = ent["filters"]
        if ent["narrowed"]:
            view["narrowed"] = ent["narrowed"]
        if len(flagged) <= OUT_ENTITY_LIST:
            view["list"] = flagged
        else:
            view["sample"] = flagged[:OUT_ENTITIES]
            view["full_list"] = (
                f"{len(flagged)} entities, only a sample is shown here. Pass "
                "output_path='<folder>/anomalous_entities.csv' (or .txt) to write "
                "all of them; that call reads the logs again."
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
        if self.persistent:
            parts.append(
                f"{len(self.persistent)} persistent error template(s) "
                f"{PERSISTENT_READING}"
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
            "persistent_errors": self._persistent_view(None),
            "entities": {
                **{
                    k: v
                    for k, v in self._entities_view().items()
                    if k not in ("list", "sample", "full_list")
                },
                "list": [
                    {"entity": e, **self._evidence(v)}
                    for e, v in sorted(self.entities["flagged"].items())
                ],
            },
            "levels": dict(self.an.level_totals),
            "checks": self.checks,
            "warnings": self.warnings,
        }

    def _evidence(self, v: Dict[str, Any]) -> Dict[str, Any]:
        """Why one entity is on the list: enough to filter the list without the logs."""
        return {
            "kind": v["kind"],
            "templates": v["templates"],
            "severity": SEVERITY_NAMES[v["sev"]],
            "seen_from": self.ts(v["first"]),
            "seen_to": self.ts(v["last"]),
            "also_in_routine": v["routine"],
            "template_share": round(v["reach"], 4),
        }

    def _list_summary(self) -> Dict[str, Any]:
        """How many entities a written list holds, out of how many."""
        ent = self.entities
        return {
            "flagged": len(ent["flagged"]),
            "total": ent["total"],
            "share": round(ent["share"], 4),
            "plausible": not ent["implausible"],
        }

    def write(self, path: Path) -> Dict[str, Any]:
        """Write full detail (``.json``) or the entity list (``.csv`` / ``.txt``)."""
        if path.suffix.lower() == ".txt":
            with skill_context.open_write(path, "x") as f:
                # The marker line lets a later run skip this file as a log.
                f.write(f"# {OUTPUT_MARKER} anomalous entities, one per line\n")
                for e in sorted(self.entities["flagged"]):
                    f.write(e + "\n")
            return {
                "path": str(path),
                "rows": len(self.entities["flagged"]),
                "kind": "entity list (one per line after a '#' comment line)",
                **self._list_summary(),
            }
        if path.suffix.lower() == ".csv":
            with skill_context.open_write(path, "x", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "entity",
                        "kind",
                        "templates",
                        "template_text",
                        "severity",
                        "seen_from",
                        "seen_to",
                        "also_in_routine",
                        "template_share",
                        "generator=" + OUTPUT_MARKER,
                    ]
                )
                by_id = {t.tid: t for t in self.anomalies}
                for e, v in sorted(self.entities["flagged"].items()):
                    texts = " || ".join(
                        _short(by_id[i].key, 120) for i in v["templates"] if i in by_id
                    )
                    ev = self._evidence(v)
                    writer.writerow(
                        [
                            e,
                            v["kind"],
                            " ".join(v["templates"]),
                            texts,
                            ev["severity"],
                            ev["seen_from"] or "",
                            ev["seen_to"] or "",
                            "yes" if ev["also_in_routine"] else "no",
                            ev["template_share"],
                            "",
                        ]
                    )
            return {
                "path": str(path),
                "rows": len(self.entities["flagged"]),
                "kind": "entity list (CSV)",
                "columns": (
                    "entity, kind, templates and template_text (the anomaly "
                    "templates that name it), severity (the worst of them), "
                    "seen_from / seen_to (time buckets), also_in_routine (yes when "
                    "routine templates name it too), template_share (share of all "
                    "entities its rarest template touches)"
                ),
                **self._list_summary(),
            }
        payload = self.full()
        with skill_context.open_write(path, "x") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False, default=str)
        return {
            "path": str(path),
            "templates": len(payload["templates"]),
            "kind": "full detail (JSON); the entity list with its evidence is entities.list",
            **self._list_summary(),
        }


def _fmt_offset(seconds: int) -> str:
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
