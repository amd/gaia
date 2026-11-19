# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``triage_logs``: the tool the ``log-triage`` skill provides, and its engine.

A small model handed a log folder reads the first page, counts the loudest
message and reports routine noise as the incident. This module does the
counting deterministically, in one streaming pass per file:

1. **Formats.** Each file's format is learned from a sample (head, middle and
   tail): JSON lines, syslog (RFC 3164 and 5424), Apache/Nginx access and
   error logs, glog, ``key=value``, CSV exports whose header names a time
   column, and any line that carries a timestamp at its start or after a
   level or logger name (ISO 8601, ``YYYY-MM-DD HH:MM:SS,mmm``,
   ``dd/Mon/yyyy:...``, ctime, epoch seconds or milliseconds). Indented and
   stack-trace lines join the entry above them. Lines that fit nothing are
   counted and sampled, never dropped silently, and a file with no usable
   timestamps fails the ``parsed_share_ok`` check.
2. **Templates.** Variable parts (numbers, hex, UUIDs, IPs, paths, URLs, mixed
   ids) are masked, then templates that differ in one high-cardinality word
   are merged (Drain-like, bounded by a template cap).
3. **Classification.** Per template: count, severity, hosts, entities, span,
   presence, peak against its own median, first and last occurrence with
   ``file:line``. A template steady across most of the range is BASELINE
   whatever its level; one that is short-lived, new, spiking, escalating or
   hits many hosts at once is an ANOMALY. Baseline errors are also reported
   as PERSISTENT when nothing is anomalous or the range is short: the log
   alone cannot tell routine errors from an outage that covers all of it.
4. **Bursts.** Buckets where errors and anomaly lines rise far above their
   median, merged into windows with exact first and last timestamps and the
   templates that occur only there.
5. **Entities.** Ids (request, session, order, job, host ...) taken only from
   anomaly templates, inside the anomaly's own window. ``output_path`` writes
   the list with the evidence for each id, and the result says how many of
   all entities it holds. A list that holds more than a third of them is
   reported as implausible, with the two arguments that tighten it.

Memory is bounded by caps on templates, hosts, entities and time buckets;
whatever a cap cut off is reported in ``warnings``. Only the standard library
is used, so the tool runs anywhere the agent does. The engine lives in the
``log_triage_engine`` package next to this file; this module is the tool.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from gaia.agents.base.tools import tool
from gaia.logger import get_logger
from gaia.skills.errors import SkillPermissionError

# A reload re-imports tools.py; drop the cached engine so it is re-read with it.
for _name in [m for m in sys.modules if m.split(".")[0] == "log_triage_engine"]:
    del sys.modules[_name]

from log_triage_engine.analysis import TriageOptions
from log_triage_engine.limits import DEFAULT_TIME_BUDGET_S, DEFAULT_TOP_N
from log_triage_engine.report import triage

log = get_logger(__name__)

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


def _not_written(compact: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """The analysis stands, but the file the caller asked for does not exist."""
    return {
        "status": "partial",
        "error": f"output_path was not written: {reason}",
        "fix": (
            "The analysis in this result is complete; only the file is missing. "
            "Call again with an output_path that does not exist yet, ends in "
            ".json, .csv or .txt and lies in a folder you may write. Until then "
            "tell the user the file was not written."
        ),
        **{k: v for k, v in compact.items() if k != "status"},
        "output": {"written": False, "error": reason},
    }


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
    entity_min_level: str = "",
    entity_max_share: float = 0.0,
) -> dict:
    """Find what went wrong in log files: call this first for any log question.

    Reads every file in full (read-only, any size) and separates steady
    baseline templates from anomalies; quote its numbers exactly.

    Asked which ids, hosts or jobs are anomalous? Pass output_path: this tool
    writes that list with each id's evidence. Deliver or filter that file;
    never rebuild the list with your own script. A list holding more than a
    third of all entities is not an anomaly list: follow entity_list_warning.

    Args:
        paths: Log files and/or folders (folders are walked recursively).
        glob: Optional file-name filter for folders, e.g. "*.log;*.log.gz".
        timestamp_regex: Override when the format is misread: a regex with
            (?P<ts>...) or year/month/day/hour/minute[/second] groups.
        level_regex: Optional regex whose (?P<level>...) or first group is the level.
        host_regex: Optional regex whose (?P<host>...) or first group is the host.
        entity_regex: Optional regex for the ids to list, e.g. "(order-[0-9]+)";
            by default the id kind is learned from the data.
        bucket: Optional time bucket such as "1min", "5min" or "1h"; chosen
            automatically from the range when empty.
        top_n: How many anomalies to return (1-50).
        output_path: New file for the anomalous-entity list: .csv (id and
            evidence per row), .txt (ids) or .json (full detail). Pass it on
            the first call when a list is asked for.
        entity_min_level: Keep only ids named by anomaly templates at this
            level or above, e.g. "ERROR" for error-bearing templates only.
        entity_max_share: Leave out anomaly templates that touch more than
            this share of all entities, e.g. 0.33; 0 keeps every template.
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
        entity_min_level=entity_min_level or None,
        entity_max_share=entity_max_share or None,
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
            return _not_written(compact, refusal)
        try:
            compact["output"] = {"written": True, **result.write(out)}
            compact["next_step"] = result.next_step(compact["output"])
        except SkillPermissionError as exc:
            return _not_written(compact, str(exc))
        except FileExistsError:
            return _not_written(
                compact,
                f"'{out}' already exists: triage_logs never overwrites a file. "
                "Pick a new name.",
            )
        except OSError as exc:
            log.error("triage_logs could not write %s: %s", out, exc)
            return _not_written(compact, f"could not write {out}: {exc}")
    return compact
