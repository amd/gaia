# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Post-run failure analyzer for the MCP tool-calling reliability eval.

Reads per-scenario trace JSON files (written by `gaia eval agent`) and,
optionally, downstream MCP service logs, then emits structured failure
reports and per-tool rollups:

    <run_dir>/failure_report.json   (list of FailureRecord dicts)
    <run_dir>/failure_report.md     (grouped by tool, worst-first)
    <run_dir>/per_tool_report.json  (per-tool rollup)
    <run_dir>/per_tool_report.md    (gate table: X tools meet pass threshold)

Usage:
    python -m gaia.eval.analyze_failures \\
        --run-id eval-YYYYMMDD-HHMMSS \\
        [--results-dir eval/results] \\
        [--tool-log-dir /path/to/mcp/service/logs] \\
        [--tool-log-tz-offset-hours -4] \\
        [--scenarios-dir eval/scenarios/mcp_tool_reliability] \\
        [--iterations-glob "eval-20260423-*"]

The service-log correlation is optional and vendor-agnostic: any directory
containing ``*.log`` files in the bracketed-timestamp format below will be
parsed; non-matching lines are silently skipped.

If --iterations-glob is supplied, all matching run_dirs are merged (one
FailureRecord per iteration). Otherwise the single run_id contributes
iteration=1.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from gaia.logger import get_logger

log = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULTS_DIR = REPO_ROOT / "eval" / "results"
DEFAULT_SCENARIOS_DIR = REPO_ROOT / "eval" / "scenarios" / "mcp_tool_reliability"

# Statuses that count as "failures" for the analyzer. Anything not in this set
# and not {"PASS", "SKIPPED_NO_DOCUMENT"} still gets a record as "ERRORED".
_FAILURE_STATUSES = {
    "FAIL",
    "TIMEOUT",
    "BUDGET_EXCEEDED",
    "INFRA_ERROR",
    "SETUP_ERROR",
    "BLOCKED_BY_ARCHITECTURE",
    "ERRORED",
}
_PASS_STATUSES = {"PASS"}
_NON_COUNTED_STATUSES = {"SKIPPED_NO_DOCUMENT"}


@dataclass
class ToolScenario:
    scenario_id: str
    tool_name: str
    utterance_style: str  # "verbatim" or "paraphrase"
    expected_behavior: str
    user_messages: List[str] = field(default_factory=list)


@dataclass
class ToolLogEntry:
    timestamp: datetime
    tool_name: str
    args: str
    result: str
    raw_line: str
    log_file: str


@dataclass
class FailureRecord:
    scenario_id: str
    tool: str
    utterance_style: str
    iteration: int
    status: str
    failure_category: str
    observations: Dict[str, Any]
    reproduction: Dict[str, Any]


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------


def _derive_tool_and_style(scenario_id: str, filename: str) -> tuple[str, str]:
    """Extract (tool_name, utterance_style) from scenario id/filename.

    Convention: tool_<tool_name>_<verbatim|paraphrase>
    - filename `tool_perf_silent_verbatim.yaml` → ("perf_silent", "verbatim")
    - filename `tool_dark_mode_on_paraphrase.yaml` → ("dark_mode_on", "paraphrase")
    """
    stem = Path(filename).stem
    if stem.endswith("_verbatim"):
        style = "verbatim"
        core = stem[: -len("_verbatim")]
    elif stem.endswith("_paraphrase"):
        style = "paraphrase"
        core = stem[: -len("_paraphrase")]
    else:
        # Fall back to scenario id suffix
        if scenario_id.endswith("_verbatim"):
            style = "verbatim"
            core = scenario_id[: -len("_verbatim")]
        elif scenario_id.endswith("_paraphrase"):
            style = "paraphrase"
            core = scenario_id[: -len("_paraphrase")]
        else:
            style = "unknown"
            core = stem

    # Strip tool_ prefix
    tool = core[len("tool_") :] if core.startswith("tool_") else core
    return tool, style


def load_scenarios(scenarios_dir: Path) -> Dict[str, ToolScenario]:
    """Glob all YAML files under scenarios_dir and build a scenario_id → ToolScenario map."""
    out: Dict[str, ToolScenario] = {}
    if not scenarios_dir.exists():
        log.warning(f"Scenarios dir missing: {scenarios_dir}")
        return out

    for yf in sorted(scenarios_dir.glob("**/*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not parse {yf.name}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        sid = data.get("id") or yf.stem
        tool, style = _derive_tool_and_style(sid, yf.name)

        turns = data.get("turns") or []
        user_messages = [
            t.get("user_message", "") for t in turns if isinstance(t, dict)
        ]

        # expected_behavior: prefer first turn, fall back to last turn, else empty
        expected_behavior = ""
        for t in turns:
            if not isinstance(t, dict):
                continue
            gt = t.get("ground_truth") or {}
            eb = gt.get("expected_behavior")
            if eb:
                expected_behavior = eb.strip()
                break
        if not expected_behavior and turns:
            last = turns[-1]
            if isinstance(last, dict):
                gt = last.get("ground_truth") or {}
                expected_behavior = (gt.get("expected_behavior") or "").strip()

        out[sid] = ToolScenario(
            scenario_id=sid,
            tool_name=tool,
            utterance_style=style,
            expected_behavior=expected_behavior,
            user_messages=user_messages,
        )
    return out


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------


def _iter_run_dirs(
    results_dir: Path, run_id: str, iterations_glob: Optional[str]
) -> List[Path]:
    """Collect run directories to scan. If iterations_glob given, expand it."""
    if iterations_glob:
        matches = sorted(p for p in results_dir.glob(iterations_glob) if p.is_dir())
        if not matches:
            log.warning(
                f"--iterations-glob {iterations_glob!r} matched no dirs under {results_dir}"
            )
        return matches
    single = results_dir / run_id
    return [single]


def load_traces(run_dirs: List[Path]) -> List[Dict[str, Any]]:
    """Read all traces/*.json files across run_dirs.

    Each trace dict is annotated with:
        _run_dir:    absolute run directory
        _run_id:     the run_id (dir name)
        _iteration:  1-based index across run_dirs (in sort order)
    """
    traces: List[Dict[str, Any]] = []
    for idx, run_dir in enumerate(run_dirs, start=1):
        if not run_dir.exists():
            log.warning(f"Run dir missing: {run_dir}")
            continue
        traces_dir = run_dir / "traces"
        if not traces_dir.exists():
            log.warning(f"No traces/ subdir in {run_dir}")
            continue
        for tf in sorted(traces_dir.glob("*.json")):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning(f"Corrupt trace {tf}: {e}")
                continue
            data["_run_dir"] = str(run_dir)
            data["_run_id"] = run_dir.name
            data["_iteration"] = idx
            data["_trace_file"] = tf.name
            traces.append(data)
    return traces


def load_scorecards(run_dirs: List[Path]) -> List[Dict[str, Any]]:
    """Read scorecard.json from each run_dir (best-effort)."""
    cards: List[Dict[str, Any]] = []
    for rd in run_dirs:
        sp = rd / "scorecard.json"
        if not sp.exists():
            continue
        try:
            cards.append(json.loads(sp.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning(f"Could not read {sp}: {e}")
    return cards


# ---------------------------------------------------------------------------
# MCP service log parsing
# ---------------------------------------------------------------------------

# Bracketed-timestamp service log format (vendor-agnostic):
#   [2026-04-26 16:33:26.918] [INFO] tool_activity_indicator_disable | Tool called: ...
#   [2026-04-26 16:33:26.984] [DEBUG] tool_activity_indicator_disable | Sending command: ...
# The timestamp is local wall-clock; pass --tool-log-tz-offset-hours to
# convert to UTC for correlation with trace timestamps.
_TOOL_LOG_LINE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]\s+"
    r"\[(?P<level>INFO|DEBUG|WARN|ERROR|FATAL)\]\s+"
    r"(?P<context>[^|]+?)\s*\|\s*"
    r"(?P<body>.*)$",
    re.IGNORECASE,
)

# Tool-call heuristic. Service logs commonly carry tool/command names in the
# context field (e.g. "tool_perf_silent", "launch_app"); fall back to scanning
# the message body.
_TOOL_HINT = re.compile(
    r"(?P<tool>tool_[a-z0-9_]+|launch_[a-z0-9_]+|ms_settings_[a-z0-9_]+|meeting_mode_[a-z0-9_]+)",
    re.IGNORECASE,
)


def _parse_tool_timestamp(
    s: str, tz_offset: timedelta = timedelta(0)
) -> Optional[datetime]:
    """Parse '2026-04-26 16:33:26.918' (local tz) → aware UTC datetime."""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return (dt - tz_offset).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_tool_logs(
    log_dir: Optional[Path],
    tz_offset: timedelta = timedelta(0),
    file_glob: str = "*.log",
) -> List[ToolLogEntry]:
    """Parse MCP service log files under log_dir. Tolerate absence.

    Any file matching ``file_glob`` (default ``*.log``) whose lines match the
    bracketed-timestamp format is parsed; non-matching lines are skipped.
    """
    if not log_dir:
        log.info("No --tool-log-dir provided; skipping service log correlation.")
        return []
    if not log_dir.exists():
        log.warning(f"Tool log dir does not exist: {log_dir}")
        return []

    entries: List[ToolLogEntry] = []
    files = sorted(set(log_dir.glob(file_glob)))
    if not files:
        log.warning(f"No log files matched {file_glob!r} under {log_dir}")
        return []

    for lf in files:
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning(f"Could not read {lf}: {e}")
            continue
        for raw in text.splitlines():
            m = _TOOL_LOG_LINE.match(raw)
            if not m:
                continue
            ts = _parse_tool_timestamp(m.group("ts"), tz_offset=tz_offset)
            if ts is None:
                continue
            body = m.group("body").strip()
            ctx = m.group("context").strip()
            tool_name = ""
            ctx_match = _TOOL_HINT.match(ctx)
            if ctx_match:
                tool_name = ctx_match.group("tool").lower()
            else:
                th = _TOOL_HINT.search(body)
                if th:
                    tool_name = th.group("tool").lower()
            entries.append(
                ToolLogEntry(
                    timestamp=ts,
                    tool_name=tool_name,
                    args="",
                    result=body[:300],
                    raw_line=raw[:500],
                    log_file=lf.name,
                )
            )
    entries.sort(key=lambda e: e.timestamp)
    log.info(f"Parsed {len(entries)} log entries from {len(files)} file(s).")
    return entries


# ---------------------------------------------------------------------------
# Log-to-turn correlation
# ---------------------------------------------------------------------------


def correlate_log_to_turn(
    scenario_start: Optional[datetime],
    turn_offset_s: float,
    expected_tool: str,
    observed_tools: List[str],
    tool_entries: List[ToolLogEntry],
    window_s: int = 60,
) -> Dict[str, Any]:
    """Find service log activity near the turn's time window and bucket it.

    bucket ∈ {"no_service_call", "wrong_tool", "service_error", "service_ok",
              "no_log_data"}
    """
    if not tool_entries:
        return {
            "service_saw_call": False,
            "service_tool_name": None,
            "service_args": None,
            "service_result": None,
            "log_excerpt": None,
            "bucket": "no_log_data",
        }

    if scenario_start is None:
        return {
            "service_saw_call": False,
            "service_tool_name": None,
            "service_args": None,
            "service_result": None,
            "log_excerpt": None,
            "bucket": "no_log_data",
        }

    turn_ts = scenario_start + timedelta(seconds=turn_offset_s)
    lo = turn_ts - timedelta(seconds=window_s)
    hi = turn_ts + timedelta(seconds=window_s)
    window = [e for e in tool_entries if lo <= e.timestamp <= hi]

    if not window:
        return {
            "service_saw_call": False,
            "service_tool_name": None,
            "service_args": None,
            "service_result": None,
            "log_excerpt": None,
            "bucket": "no_service_call",
        }

    # Pick the most informative entry — prefer one that names a tool
    named = [e for e in window if e.tool_name]
    pick = named[0] if named else window[0]

    obs_tools_lc = [t.lower() for t in observed_tools]
    expected_lc = (expected_tool or "").lower()

    # Heuristic: error if the body contains 'error' or 'fail'
    is_err = bool(re.search(r"\b(error|fail|exception)\b", pick.result, re.IGNORECASE))
    tool_matches_expected = bool(
        expected_lc and pick.tool_name and expected_lc in pick.tool_name
    )
    tool_matches_observed = any(
        pick.tool_name and pick.tool_name in t for t in obs_tools_lc
    )

    if is_err:
        bucket = "service_error"
    elif pick.tool_name and not tool_matches_expected and not tool_matches_observed:
        bucket = "wrong_tool"
    else:
        bucket = "service_ok"

    return {
        "service_saw_call": True,
        "service_tool_name": pick.tool_name or None,
        "service_args": pick.args or None,
        "service_result": pick.result,
        "log_excerpt": pick.raw_line,
        "bucket": bucket,
    }


# ---------------------------------------------------------------------------
# Failure record construction
# ---------------------------------------------------------------------------


def _parse_scorecard_start(scorecard: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not scorecard:
        return None
    ts = scorecard.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def build_failure_records(
    traces: List[Dict[str, Any]],
    scenarios: Dict[str, ToolScenario],
    tool_entries: List[ToolLogEntry],
    scorecards_by_run: Dict[str, Dict[str, Any]],
    run_config: Dict[str, Any],
) -> List[FailureRecord]:
    """Emit one FailureRecord per non-PASS, non-SKIPPED trace."""
    records: List[FailureRecord] = []

    for tr in traces:
        status = tr.get("status", "ERRORED")
        if status in _PASS_STATUSES or status in _NON_COUNTED_STATUSES:
            continue

        sid = tr.get("scenario_id", "unknown")
        scen = scenarios.get(sid)
        tool_name = scen.tool_name if scen else "UNKNOWN"
        style = scen.utterance_style if scen else "unknown"
        expected_behavior = scen.expected_behavior if scen else ""
        user_messages = (
            scen.user_messages
            if scen
            else [t.get("user_message", "") for t in tr.get("turns", [])]
        )

        turns = tr.get("turns") or []
        # Pull the first non-passing turn (or last turn if all passed at turn level)
        failing_turn = None
        for t in turns:
            if not t.get("pass", False):
                failing_turn = t
                break
        if failing_turn is None and turns:
            failing_turn = turns[-1]

        failure_category = (failing_turn or {}).get("failure_category") or "unknown"
        reasoning = (failing_turn or {}).get("reasoning") or ""
        agent_tools = (failing_turn or {}).get("agent_tools") or []
        agent_response = (failing_turn or {}).get("agent_response") or ""
        agent_response_excerpt = agent_response[:300]

        # Correlate downstream MCP service logs
        scorecard = scorecards_by_run.get(tr.get("_run_id", ""))
        scenario_start = _parse_scorecard_start(scorecard)
        # Turn offset: sum elapsed across prior turns is unavailable; best-effort
        # approximation uses (trace.elapsed_s / len(turns)) * (turn_index - 1).
        elapsed = tr.get("elapsed_s") or 0.0
        n_turns = max(1, len(turns))
        turn_idx = (failing_turn or {}).get("turn", 1)
        turn_offset_s = (elapsed / n_turns) * max(0, turn_idx - 1)

        service_info = correlate_log_to_turn(
            scenario_start,
            turn_offset_s,
            expected_tool=tool_name,
            observed_tools=agent_tools,
            tool_entries=tool_entries,
        )

        observations = {
            "what_happened": reasoning or f"status={status}",
            "root_cause": tr.get("root_cause"),
            "recommended_fix": tr.get("recommended_fix"),
            "tools_observed": agent_tools,
            "expected_tool": tool_name,
            "expected_behavior": expected_behavior,
            "agent_response_excerpt": agent_response_excerpt,
            "tool_service_log": service_info,
        }

        repro_cli = (
            f"gaia eval agent --scenario {sid} --agent-type "
            f"{run_config.get('agent_type', 'tool')} "
            f"--iterations 1 --model {run_config.get('model', 'claude-sonnet-4-6')}"
        )
        reproduction = {
            "cli": repro_cli,
            "user_messages": user_messages,
            "agent_type": run_config.get("agent_type", "tool"),
            "model": run_config.get("model", "claude-sonnet-4-6"),
            "backend_url": run_config.get("backend_url", "http://localhost:4200"),
            "trace_file": tr.get("_trace_file"),
            "run_id": tr.get("_run_id"),
        }

        records.append(
            FailureRecord(
                scenario_id=sid,
                tool=tool_name,
                utterance_style=style,
                iteration=int(tr.get("_iteration", 1)),
                status=status,
                failure_category=failure_category,
                observations=observations,
                reproduction=reproduction,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Per-tool rollup
# ---------------------------------------------------------------------------


def build_per_tool_report(
    traces: List[Dict[str, Any]],
    scenarios: Dict[str, ToolScenario],
    pass_threshold: float = 0.90,
) -> List[Dict[str, Any]]:
    """Aggregate per-tool pass rates across verbatim + paraphrase variants."""
    # Map: tool → style → [pass_count, total]
    counts: Dict[str, Dict[str, List[int]]] = defaultdict(
        lambda: {"verbatim": [0, 0], "paraphrase": [0, 0]}
    )

    for tr in traces:
        sid = tr.get("scenario_id", "")
        scen = scenarios.get(sid)
        if not scen:
            continue
        status = tr.get("status", "ERRORED")
        if status in _NON_COUNTED_STATUSES:
            continue
        bucket = counts[scen.tool_name].get(scen.utterance_style)
        if bucket is None:
            # Shouldn't happen for known styles, but be tolerant
            continue
        bucket[1] += 1  # total
        if status in _PASS_STATUSES:
            bucket[0] += 1  # passes

    # Ensure all scenario-declared tools appear even if no traces exist
    for scen in scenarios.values():
        _ = counts[scen.tool_name]

    rows: List[Dict[str, Any]] = []
    for tool in sorted(counts.keys()):
        v_pass, v_total = counts[tool]["verbatim"]
        p_pass, p_total = counts[tool]["paraphrase"]
        all_pass = v_pass + p_pass
        all_total = v_total + p_total

        def _rate(p: int, t: int) -> Optional[float]:
            return (p / t) if t > 0 else None

        combined = _rate(all_pass, all_total)
        rows.append(
            {
                "tool": tool,
                "verbatim_pass": v_pass,
                "verbatim_total": v_total,
                "verbatim_pass_rate": _rate(v_pass, v_total),
                "paraphrase_pass": p_pass,
                "paraphrase_total": p_total,
                "paraphrase_pass_rate": _rate(p_pass, p_total),
                "combined_pass": all_pass,
                "combined_total": all_total,
                "combined_pass_rate": combined,
                "meets_gate": (combined is not None and combined >= pass_threshold),
            }
        )

    # Sort worst-first (None rates → bottom)
    rows.sort(
        key=lambda r: (r["combined_pass_rate"] is None, r["combined_pass_rate"] or 0.0)
    )
    return rows


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _fmt_pct(r: Optional[float]) -> str:
    return f"{r * 100:.0f}%" if isinstance(r, (int, float)) else "n/a"


def _bucket_counts(records: List[FailureRecord]) -> Dict[str, int]:
    out: Dict[str, int] = defaultdict(int)
    for r in records:
        b = r.observations.get("tool_service_log", {}).get("bucket", "no_log_data")
        out[b] += 1
    return dict(out)


def write_failure_reports(
    run_dir: Path,
    records: List[FailureRecord],
    per_tool_rows: List[Dict[str, Any]],
    run_config: Dict[str, Any],
    warnings: List[str],
) -> None:
    """Write failure_report.json + failure_report.md to run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    fr_json = [dataclasses.asdict(r) for r in records]
    (run_dir / "failure_report.json").write_text(
        json.dumps(fr_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Markdown — group by tool using per_tool_rows order (worst-first)
    run_id = run_dir.name
    model = run_config.get("model", "unknown")
    n_iter = run_config.get("iterations", 1)
    unique_tools = sorted({r.tool for r in records})

    lines: List[str] = [
        f"# Failure Report — {run_id}",
        f"**Model:** {model}   **Iterations:** {n_iter}   **Agent:** "
        f"{run_config.get('agent_type', 'tool')}",
        "",
        f"**Total failure records:** {len(records)} across {len(unique_tools)} unique tool(s)",
        "",
    ]
    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]

    # Summary table by tool
    lines += [
        "## Summary by Tool (worst-first)",
        "",
        "| Tool | Failures | Total | Pass Rate | Bucket Breakdown |",
        "|------|---------:|------:|----------:|------------------|",
    ]
    by_tool: Dict[str, List[FailureRecord]] = defaultdict(list)
    for r in records:
        by_tool[r.tool].append(r)

    tool_order = [row["tool"] for row in per_tool_rows]
    for row in per_tool_rows:
        tool = row["tool"]
        recs = by_tool.get(tool, [])
        if not recs:
            continue
        buckets = _bucket_counts(recs)
        bucket_str = ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())) or "—"
        lines.append(
            f"| {tool} | {len(recs)} | {row['combined_total']} | "
            f"{_fmt_pct(row['combined_pass_rate'])} | {bucket_str} |"
        )

    # Detailed failures
    lines += ["", "## Detailed Failures", ""]
    for tool in tool_order:
        recs = by_tool.get(tool, [])
        if not recs:
            continue
        row = next((r for r in per_tool_rows if r["tool"] == tool), None)
        rate_str = _fmt_pct(row["combined_pass_rate"]) if row else "?"
        lines.append(f"### {tool}  ({rate_str} combined pass rate)")
        lines.append("")
        for r in recs:
            svc = r.observations.get("tool_service_log") or {}
            if svc.get("service_saw_call"):
                svc_line = (
                    f"service log bucket={svc.get('bucket')} "
                    f"tool={svc.get('service_tool_name')} "
                    f"result={str(svc.get('service_result'))[:120]!r}"
                )
            else:
                svc_line = f"service log bucket={svc.get('bucket', 'no_log_data')}"
            lines += [
                f"- **{r.scenario_id}** iter {r.iteration} [{r.utterance_style}]: "
                f"{r.status} / {r.failure_category}",
                f"  - what happened: {r.observations.get('what_happened', '')[:240]}",
                f"  - tools observed: {r.observations.get('tools_observed')}",
                f"  - expected: contains {r.observations.get('expected_tool')!r}",
                f"  - {svc_line}",
                f"  - repro: `{r.reproduction['cli']}`",
                "",
            ]

    (run_dir / "failure_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def write_per_tool_reports(
    run_dir: Path,
    per_tool_rows: List[Dict[str, Any]],
    run_config: Dict[str, Any],
    pass_threshold: float = 0.90,
) -> None:
    """Write per_tool_report.json + per_tool_report.md."""
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "per_tool_report.json").write_text(
        json.dumps(per_tool_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    total_tools = len(per_tool_rows)
    meets = sum(1 for r in per_tool_rows if r["meets_gate"])

    lines: List[str] = [
        f"# Per-Tool Reliability Report — {run_dir.name}",
        f"**Model:** {run_config.get('model', 'unknown')}   "
        f"**Iterations:** {run_config.get('iterations', 1)}   "
        f"**Gate:** {pass_threshold * 100:.0f}%",
        "",
        f"**{meets} of {total_tools} tools meet the {pass_threshold * 100:.0f}% reliability gate**",
        "",
        "| Tool | Verbatim | Paraphrase | Combined | Gate |",
        "|------|----------|------------|----------|------|",
    ]
    for row in per_tool_rows:
        lines.append(
            f"| {row['tool']} | "
            f"{row['verbatim_pass']}/{row['verbatim_total']} "
            f"({_fmt_pct(row['verbatim_pass_rate'])}) | "
            f"{row['paraphrase_pass']}/{row['paraphrase_total']} "
            f"({_fmt_pct(row['paraphrase_pass_rate'])}) | "
            f"{row['combined_pass']}/{row['combined_total']} "
            f"({_fmt_pct(row['combined_pass_rate'])}) | "
            f"{'PASS' if row['meets_gate'] else 'FAIL'} |"
        )

    (run_dir / "per_tool_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------


def _self_check(
    per_tool_rows: List[Dict[str, Any]],
    records: List[FailureRecord],
    scorecards: List[Dict[str, Any]],
) -> List[str]:
    """Return a list of warnings. Warn rather than fail."""
    warnings: List[str] = []

    if len(per_tool_rows) != 41:
        warnings.append(
            f"Expected 41 unique tools in per_tool_report, got {len(per_tool_rows)}"
        )

    sc_failed = sum(sc.get("summary", {}).get("failed", 0) for sc in scorecards)
    sc_errored = sum(
        sc.get("summary", {}).get("errored", 0)
        + sc.get("summary", {}).get("timeout", 0)
        + sc.get("summary", {}).get("infra_error", 0)
        + sc.get("summary", {}).get("budget_exceeded", 0)
        + sc.get("summary", {}).get("blocked", 0)
        for sc in scorecards
    )
    expected_total = sc_failed + sc_errored
    if expected_total > 0 and records:
        drift = abs(len(records) - expected_total) / expected_total
        if drift > 0.10:
            warnings.append(
                f"Failure record count ({len(records)}) differs from "
                f"scorecard failed+errored ({expected_total}) by {drift * 100:.0f}%"
            )
    return warnings


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gaia.eval.analyze_failures",
        description="Post-run failure analyzer for the MCP tool reliability eval.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Run id, e.g. eval-20260423-143020. Becomes the output dir name.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help=f"Base results dir (default: {DEFAULT_RESULTS_DIR})",
    )
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=DEFAULT_SCENARIOS_DIR,
        help=f"Scenarios dir (default: {DEFAULT_SCENARIOS_DIR})",
    )
    parser.add_argument(
        "--tool-log-dir",
        type=Path,
        default=None,
        help="Directory containing downstream MCP service logs to correlate "
        "with traces (e.g. /var/log/mcp-service or C:/ProgramData/.../logs). "
        "If omitted, service-log correlation is skipped.",
    )
    parser.add_argument(
        "--tool-log-tz-offset-hours",
        type=float,
        default=0.0,
        help="Local-tz offset of the service log timestamps in hours "
        "(default: 0 = UTC). Use -4 for EDT, -5 for EST, etc.",
    )
    parser.add_argument(
        "--tool-log-glob",
        default="*.log",
        help="Glob for service log files under --tool-log-dir (default: '*.log').",
    )
    parser.add_argument(
        "--iterations-glob",
        default=None,
        help="Optional glob (relative to --results-dir) matching multiple "
        "run_dirs to merge (one iteration each). Example: 'eval-20260423-1*'",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=0.90,
        help="Per-tool reliability gate (default: 0.90)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Override output dir (default: <results-dir>/<run-id>)",
    )
    args = parser.parse_args(argv)

    results_dir: Path = args.results_dir
    run_dirs = _iter_run_dirs(results_dir, args.run_id, args.iterations_glob)
    if not run_dirs:
        print(f"[ERROR] No run dirs to scan for run-id={args.run_id}", file=sys.stderr)
        return 2

    existing = [p for p in run_dirs if p.exists()]
    if not existing:
        print(
            f"[ERROR] None of the candidate run dirs exist: {run_dirs}",
            file=sys.stderr,
        )
        return 2

    out_dir = args.out_dir or (results_dir / args.run_id)
    print(f"[ANALYZE] run-id={args.run_id}  out={out_dir}")
    print(f"[ANALYZE] Scanning {len(existing)} run dir(s):")
    for p in existing:
        print(f"  - {p}")

    scenarios = load_scenarios(args.scenarios_dir)
    print(f"[ANALYZE] Loaded {len(scenarios)} scenarios from {args.scenarios_dir}")

    traces = load_traces(existing)
    print(f"[ANALYZE] Loaded {len(traces)} trace files")

    scorecards = load_scorecards(existing)
    scorecards_by_run = {}
    for sc in scorecards:
        rid = sc.get("run_id")
        if rid:
            scorecards_by_run[rid] = sc
    print(f"[ANALYZE] Loaded {len(scorecards)} scorecard(s)")

    tool_entries = parse_tool_logs(
        args.tool_log_dir,
        tz_offset=timedelta(hours=args.tool_log_tz_offset_hours),
        file_glob=args.tool_log_glob,
    )

    # Derive run_config from the first scorecard we find (fallbacks if absent)
    run_config = {
        "iterations": len(existing),
        "agent_type": "tool",
        "model": "claude-sonnet-4-6",
        "backend_url": "http://localhost:4200",
    }
    if scorecards:
        cfg = scorecards[0].get("config") or {}
        run_config["model"] = cfg.get("model", run_config["model"])
        run_config["backend_url"] = cfg.get("backend_url", run_config["backend_url"])
        run_config["agent_type"] = cfg.get("agent_type", run_config["agent_type"])

    records = build_failure_records(
        traces, scenarios, tool_entries, scorecards_by_run, run_config
    )
    print(f"[ANALYZE] Built {len(records)} failure record(s)")

    per_tool_rows = build_per_tool_report(traces, scenarios, args.pass_threshold)
    print(
        f"[ANALYZE] Per-tool rollup: {len(per_tool_rows)} tool(s), "
        f"{sum(1 for r in per_tool_rows if r['meets_gate'])} meet the gate."
    )

    warnings = _self_check(per_tool_rows, records, scorecards)
    for w in warnings:
        log.warning(w)

    write_failure_reports(out_dir, records, per_tool_rows, run_config, warnings)
    write_per_tool_reports(out_dir, per_tool_rows, run_config, args.pass_threshold)

    print(f"[ANALYZE] Reports written to {out_dir}")
    print("  - failure_report.json / failure_report.md")
    print("  - per_tool_report.json / per_tool_report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
