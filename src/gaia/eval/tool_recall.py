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

Amendment 2: on native tool-calling models a semantic miss can't self-recover
until Part 2, so misses there are reported as a *known gap* and do not fail the
gate. On non-native models recall below ``--min-recall`` exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_TOOL_LOADER_RE = re.compile(r"TOOL_LOADER (\{.*\})\s*$")


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
            missing = sorted(c for c in called if c not in loaded_set)
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

    A new scenario begins at each ``"turn": 1`` selection line (the loader resets
    its turn counter per conversation).
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
        if payload["turn"] == 1 and current:
            scenarios.append(current)
            current = []
        current.append(list(payload["loaded"]))
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


def _model_is_native(scorecard: Dict) -> bool:
    """Whether the scorecard's model uses native tool-calling (Amendment-2 gate)."""
    model = (scorecard.get("config") or {}).get("model")
    try:
        from gaia.llm.lemonade_client import is_tool_calling_model

        return is_tool_calling_model(model)
    except Exception:
        # If we can't classify, treat as native so misses don't hard-fail.
        return True


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


def _format_report(report: RecallReport, native: bool) -> str:
    lines = [
        "# Tool-recall gate (#1449)",
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
        label = "known gap (native model, Part 2)" if native else "RECALL MISS"
        lines.append(f"## {label}")
        for t in misses:
            lines.append(
                f"- scenario {t.scenario_idx} turn {t.turn_idx}: called "
                f"{t.called} but NOT loaded: {t.missing}"
            )
    else:
        lines.append("All called tools were loaded when called. ✅")
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
        help="Minimum recall for a PASS on non-native models (default: 1.0).",
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
    loaded = parse_loaded_sets_from_log(log_path.read_text(encoding="utf-8"))
    if not loaded:
        raise SystemExit(
            f"{log_path} contained no TOOL_LOADER selection lines — was the loader "
            "enabled (GAIA_DYNAMIC_TOOLS=1) and the right log captured?"
        )
    called = parse_called_sets_from_scorecard(scorecard)

    report = compute_recall(loaded, called)
    native = _model_is_native(scorecard)
    print(_format_report(report, native))

    if not native and report.recall < args.min_recall:
        print(
            f"\nFAIL: recall {report.recall:.1%} < {args.min_recall:.1%} "
            "on a non-native model.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
