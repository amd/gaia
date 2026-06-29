# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-recall gate for the dynamic tool loader (#1449, parent #688).

Merge gate: every tool the agent *called* on a turn must have been *loaded* (in
the prompt) that turn. This joins the per-turn ``TOOL_LOADER`` selection log
lines against a ``gaia eval agent`` scorecard's per-turn ``agent_tools`` (the
called set) and reports recall = fraction of turns where ``called ⊆ loaded``.

Where the inputs come from
--------------------------
* **loaded sets** — the ``TOOL_LOADER {json}`` INFO lines the loader emits each
  turn. ``gaia eval agent`` drives the agent through the backend server, so
  these land in the *server* log. GAIA's logger writes to **stdout**, so capture
  stdout (``2>`` alone misses it); ``tee`` also keeps it on screen::

      GAIA_DYNAMIC_TOOLS=1 python -m gaia.ui.server --port 4200 --host 127.0.0.1 \
          2>&1 | tee server.log

  The loader's per-session turn counter resets to 1 on each new conversation, so
  ``turn == 1`` marks a scenario boundary; lines are grouped into scenarios in
  order.
* **called sets** — ``scorecard.json`` in the eval run dir
  (``scenarios[].turns[].agent_tools``).

Part 2 (#1450): native tool-calling models recover a semantically-missed tool
via the always-on ``load_tools`` meta-tool, so the Amendment-2 native exemption
is **removed** — a miss fails the gate on every model. Two parser changes make
that correct: a mid-loop ``load_tools`` line (same ``turn``, ``event":
"load_tools"``) is **unioned** into that turn's loaded set so a successful
recovery shows the tool as loaded; and ``load_tools`` itself counts as
always-satisfied. Recall below ``--min-recall`` exits non-zero.

Escape-hatch activation rate (the τ-tuning signal, rising ⇒ τ too strict) is
derived from the **raw per-turn log events** — explicit ``load_tools`` lines and
free-recovery ``TOOL_LOADER_ESCAPE_HATCH`` lines over the turn count — because
those appear on every run. The per-session ``TOOL_LOADER_SESSION`` summary
(emitted only on ``reset_session``, i.e. the ``gaia chat``/CLI path, **not** the
UI-server/eval path) is a convenience for CLI logs; the recall gate does not
depend on it, so the rate is reported for eval runs too. (#1450)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_TOOL_LOADER_RE = re.compile(r"TOOL_LOADER (\{.*\})\s*$")
_SESSION_RE = re.compile(r"TOOL_LOADER_SESSION (\{.*\})\s*$")
_ESCAPE_HATCH_RE = re.compile(r'"event"\s*:\s*"TOOL_LOADER_ESCAPE_HATCH"')

# Tools that never count as a recall miss: ``load_tools`` is the always-on
# escape hatch (CORE), so calling it is always satisfiable by construction.
_ALWAYS_SATISFIED = frozenset({"load_tools"})


# ── pure join logic (unit-tested) ─────────────────────────────────────────


@dataclass
class TurnRecall:
    scenario_idx: int
    turn_idx: int
    called: List[str]
    loaded: List[str]
    missing: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


@dataclass
class RecallReport:
    turns: List[TurnRecall]
    alignment_warnings: List[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        if not self.turns:
            return 0.0
        return sum(1 for t in self.turns if t.ok) / len(self.turns)

    @property
    def all_missing(self) -> List[str]:
        out: List[str] = []
        for t in self.turns:
            out.extend(t.missing)
        return out


def compute_recall(
    loaded_per_scenario: List[List[List[str]]],
    called_per_scenario: List[List[List[str]]],
) -> RecallReport:
    """Join loaded vs called sets per (scenario, turn); flag missing called tools.

    Both inputs are scenarios → turns → tool-name lists. Misaligned scenario or
    turn counts are reported as warnings (never silently truncated) and the
    overlap is still scored.
    """
    turns: List[TurnRecall] = []
    warnings: List[str] = []

    n_scen = max(len(loaded_per_scenario), len(called_per_scenario))
    if len(loaded_per_scenario) != len(called_per_scenario):
        warnings.append(
            f"scenario count mismatch: {len(loaded_per_scenario)} loaded-groups "
            f"vs {len(called_per_scenario)} scorecard scenarios — check that the "
            "server log covers exactly this run."
        )

    for s in range(n_scen):
        loaded_turns = loaded_per_scenario[s] if s < len(loaded_per_scenario) else []
        called_turns = called_per_scenario[s] if s < len(called_per_scenario) else []
        if len(loaded_turns) != len(called_turns):
            warnings.append(
                f"scenario {s}: turn count mismatch "
                f"({len(loaded_turns)} loaded vs {len(called_turns)} called)."
            )
        n_turn = min(len(loaded_turns), len(called_turns))
        for t in range(n_turn):
            loaded = list(loaded_turns[t])
            called = list(called_turns[t])
            loaded_set = set(loaded)
            missing = sorted(
                c for c in called if c not in loaded_set and c not in _ALWAYS_SATISFIED
            )
            turns.append(
                TurnRecall(
                    scenario_idx=s,
                    turn_idx=t,
                    called=called,
                    loaded=loaded,
                    missing=missing,
                )
            )

    return RecallReport(turns=turns, alignment_warnings=warnings)


# ── parsing ───────────────────────────────────────────────────────────────


def parse_loaded_sets_from_log(text: str) -> List[List[List[str]]]:
    """Extract per-scenario, per-turn loaded sets from server-log TOOL_LOADER lines.

    A new scenario begins at each ``"turn": 1`` *selection* line (the loader
    resets its turn counter per conversation). A mid-loop ``load_tools`` line
    (Part 2) shares its turn's number but carries ``"event": "load_tools"``; it
    is **unioned** into that turn's loaded set rather than opening a new turn, so
    a within-turn recovery shows the loaded set as it stood *after* the load.
    Only ``event``-less selection lines move the turn/scenario cursor, so two
    consecutive single-turn scenarios still split correctly.

    Assumption: every scenario emits a ``turn == 1`` line. A turn-1 *embedder
    failure* session-disables the loader before ``_log_selection`` runs, so that
    scenario emits no ``TOOL_LOADER`` line and its boundary is missed — quietly
    shifting the per-scenario alignment. This is an eval-tooling edge case (the
    embedder is up for a real recall run); if it happens, the per-scenario /
    per-turn count mismatch surfaces as an alignment warning in
    :func:`compute_recall` rather than passing silently.
    """
    scenarios: List[List[List[str]]] = []
    current: List[List[str]] = []
    for line in text.splitlines():
        m = _TOOL_LOADER_RE.search(line)
        if not m:
            continue
        payload = json.loads(m.group(1))
        if "loaded" not in payload or "turn" not in payload:
            continue  # not a selection line (e.g. escape-hatch event)
        loaded = list(payload["loaded"])
        if payload.get("event") == "load_tools":
            # Mid-loop expansion: union into the current turn's loaded set. A
            # load_tools line always follows its turn's selection line, so
            # ``current`` is non-empty in a well-formed log; tolerate the start.
            if current:
                current[-1] = sorted(set(current[-1]) | set(loaded))
            else:
                current.append(loaded)
            continue
        if payload["turn"] == 1 and current:
            scenarios.append(current)
            current = []
        current.append(loaded)
    if current:
        scenarios.append(current)
    return scenarios


def parse_called_sets_from_scorecard(scorecard: Dict) -> List[List[List[str]]]:
    """Extract per-scenario, per-turn called sets (``agent_tools``) from a scorecard."""
    out: List[List[List[str]]] = []
    for scenario in scorecard.get("scenarios", []):
        turns = [list(t.get("agent_tools") or []) for t in scenario.get("turns", [])]
        out.append(turns)
    return out


def parse_session_summaries_from_log(text: str) -> List[Dict]:
    """Extract ``TOOL_LOADER_SESSION`` payloads (one per finished conversation)."""
    out: List[Dict] = []
    for line in text.splitlines():
        m = _SESSION_RE.search(line)
        if not m:
            continue
        out.append(json.loads(m.group(1)))
    return out


def aggregate_escape_hatch(summaries: List[Dict]) -> Dict:
    """Aggregate per-session ``TOOL_LOADER_SESSION`` summaries into a per-turn rate.

    Only ``gaia chat``/CLI logs carry these summaries (they emit on
    ``reset_session()``). For the canonical τ-tuning path — eval logs — use
    :func:`escape_hatch_rate_from_log`, which derives the same rate from the raw
    per-turn lines that are always present. The rate (free non-tool-calling
    recovery + native ``load_tools``, per turn) is the tuning signal: rising ⇒ τ
    too strict; the two component counts are kept separate so the tuner sees
    which path fired.
    """
    turns = sum(int(s.get("turns", 0)) for s in summaries)
    escape = sum(int(s.get("escape_hatch_count", 0)) for s in summaries)
    loads = sum(int(s.get("load_tools_count", 0)) for s in summaries)
    return {
        "sessions": len(summaries),
        "turns": turns,
        "escape_hatch_count": escape,
        "load_tools_count": loads,
        "escape_hatch_rate": (escape + loads) / max(turns, 1),
    }


def count_recovery_events_from_log(text: str) -> Tuple[int, int]:
    """Count the two escape-hatch recovery paths from raw per-turn log lines.

    Returns ``(free_recovery_count, load_tools_count)`` — free non-tool-calling
    recovery (``TOOL_LOADER_ESCAPE_HATCH`` lines) and explicit native recovery
    (``TOOL_LOADER {… "event": "load_tools" …}`` lines). These per-turn lines are
    emitted on **every** run (eval and CLI), independent of ``reset_session()``,
    so they — not the per-session ``TOOL_LOADER_SESSION`` summary — are the
    source of truth for the activation rate. (The UI-server/eval path never calls
    ``reset_session()``, so eval logs carry no summary; #1450.)
    """
    free = loads = 0
    for line in text.splitlines():
        if _ESCAPE_HATCH_RE.search(line):
            free += 1
            continue
        m = _TOOL_LOADER_RE.search(line)
        if m and json.loads(m.group(1)).get("event") == "load_tools":
            loads += 1
    return free, loads


def escape_hatch_rate_from_log(
    text: str, loaded_per_scenario: List[List[List[str]]]
) -> Dict:
    """Per-turn escape-hatch activation rate derived from the raw log.

    ``rate = (free recoveries + explicit load_tools) / total turns``, where total
    turns is the number of per-turn selection lines across all scenarios. Works
    on eval logs (which lack ``TOOL_LOADER_SESSION``) — this is the τ-tuning
    signal the recall gate reports.
    """
    free, loads = count_recovery_events_from_log(text)
    turns = sum(len(scenario) for scenario in loaded_per_scenario)
    return {
        "turns": turns,
        "free_recovery_count": free,
        "load_tools_count": loads,
        "escape_hatch_rate": (free + loads) / max(turns, 1),
        "session_summaries": len(parse_session_summaries_from_log(text)),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def _discover_log(run_dir: Path) -> Optional[Path]:
    """Find a file under *run_dir* containing TOOL_LOADER lines, if any."""
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "TOOL_LOADER {" in head:
            return path
    return None


def _format_report(report: RecallReport, escape_hatch: Optional[Dict] = None) -> str:
    lines = [
        "# Tool-recall gate (#1449, #1450)",
        "",
        f"Turns scored: {len(report.turns)}  |  recall: {report.recall:.1%}",
        "",
    ]
    for w in report.alignment_warnings:
        lines.append(f"⚠️  {w}")
    if report.alignment_warnings:
        lines.append("")
    misses = [t for t in report.turns if not t.ok]
    if misses:
        # Part 2 removed the native exemption: a miss is a miss on every model
        # (native models recover via load_tools, which the parser unions in).
        lines.append("## RECALL MISS")
        for t in misses:
            lines.append(
                f"- scenario {t.scenario_idx} turn {t.turn_idx}: called "
                f"{t.called} but NOT loaded: {t.missing}"
            )
    else:
        lines.append("All called tools were loaded when called. ✅")
    if escape_hatch is not None:
        lines.extend(
            [
                "",
                "## Escape-hatch activation (τ-tuning signal)",
                f"turns: {escape_hatch['turns']}  |  rate/turn: "
                f"{escape_hatch['escape_hatch_rate']:.3f} "
                f"(free recovery: {escape_hatch['free_recovery_count']}, "
                f"load_tools: {escape_hatch['load_tools_count']})  — "
                "rising ⇒ τ too strict.",
                f"(derived from per-turn log events; "
                f"{escape_hatch['session_summaries']} TOOL_LOADER_SESSION "
                "summaries present)",
            ]
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaia.eval.tool_recall",
        description="Tool-recall merge gate for the dynamic tool loader (#1449).",
    )
    parser.add_argument("run_dir", help="Eval run dir containing scorecard.json")
    parser.add_argument(
        "--scorecard",
        default=None,
        help="Scorecard path (default: <run_dir>/scorecard.json)",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Server log with TOOL_LOADER lines (default: discover under run_dir).",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=1.0,
        help="Minimum recall for a PASS (default: 1.0). Applies to every model "
        "— Part 2 removed the native exemption.",
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    scorecard_path = (
        Path(args.scorecard) if args.scorecard else run_dir / "scorecard.json"
    )
    if not scorecard_path.is_file():
        raise SystemExit(
            f"scorecard not found at {scorecard_path}. Pass --scorecard explicitly "
            "or point run_dir at the eval output directory."
        )
    scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))

    log_path = Path(args.log) if args.log else _discover_log(run_dir)
    if log_path is None or not log_path.is_file():
        raise SystemExit(
            "no TOOL_LOADER log found. GAIA logs to stdout, so capture stdout "
            "(`... 2>&1 | tee server.log`, NOT `2> server.log`) and pass it via "
            "--log. The loader emits one `TOOL_LOADER {json}` line per turn."
        )
    log_text = log_path.read_text(encoding="utf-8")
    loaded = parse_loaded_sets_from_log(log_text)
    if not loaded:
        raise SystemExit(
            f"{log_path} contained no TOOL_LOADER selection lines — was the loader "
            "enabled (GAIA_DYNAMIC_TOOLS=1) and the right log captured?"
        )
    called = parse_called_sets_from_scorecard(scorecard)

    report = compute_recall(loaded, called)
    # Derive the τ-tuning rate from the raw per-turn events (present in eval logs);
    # the per-session TOOL_LOADER_SESSION summary only exists on the CLI path.
    escape_hatch = escape_hatch_rate_from_log(log_text, loaded)
    print(_format_report(report, escape_hatch))

    if report.recall < args.min_recall:
        print(
            f"\nFAIL: recall {report.recall:.1%} < {args.min_recall:.1%}.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
