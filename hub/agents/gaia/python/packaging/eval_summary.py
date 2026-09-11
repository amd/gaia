# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Republish the gaia-agent eval's gate verdicts as a GitHub job summary.

Adapted nearly verbatim from ``hub/agents/email/python/packaging/eval_summary.py``
— one reporter shape across agents. A gate manifest under ``tests/fixtures/gaia/``
that ships ``enforce: false`` lets a BREACHED BAR exit 0 and produce a green step
with no annotation -- the number only exists inside ``eval-out/*.json``, which
nobody opens. That is not coverage. This reporter reads the gate reports the eval
already writes and republishes every verdict into ``$GITHUB_STEP_SUMMARY``,
raising a ``::warning::`` per breach, so an advisory result still lands in front
of a reviewer.

The enforce posture is read off the gates at render time (``_footer``), never
hardcoded: the manifests differ from each other and change over time, and a
summary that calls a gate advisory *after* it failed the build is worse than no
footer at all.

It is a REPORTER, not a gate: it always exits 0. Blocking stays where it belongs
-- ``should_fail`` (= ``enforce`` and not passed), owned by the manifests and
acted on by the gate-report scripts. Flip a manifest's ``enforce`` to true and
that gate blocks; this summary is unaffected either way.

Gate discovery is structural, not a hardcoded list: any ``*_gate`` object
carrying a ``passed`` OR a ``skipped`` key is picked up, at any depth, from any
report file, so a new gate shows up here for free. Both keys matter -- a skipped
gate (``{"skipped": true, "reason": ...}``) has NO ``passed`` field and hardcodes
``should_fail: false`` even under ``enforce: true``, so nothing else in the
pipeline catches it. "We could not measure this" is the one verdict that must
never be rendered as a pass.

Gates in ``GATE_LABELS`` that produce no verdict at all are reported as missing
for the same reason: a step that died before writing its report must not read as
one pass and a silence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

DEFAULT_EVAL_DIR = Path("eval-out")

# The gates the gaia eval workflow's steps are expected to produce, in pipeline
# order. Doubles as the EXPECTED SET: a key here with no verdict is reported as
# missing. A gate NOT in this map is still reported, under its raw JSON key, so a
# newly-added gate is never dropped.
GATE_LABELS = {
    "quality_gate": "Judged scenario quality (pass rate / avg score)",
    "perf_gate": "Performance (tokens / cache / latency / calls)",
}


def find_gates(node: Any, key: str = "") -> Iterator[tuple[str, dict]]:
    """Yield ``(gate_key, gate_dict)`` for every gate object under ``node``."""
    if isinstance(node, dict):
        # `skipped` as well as `passed`: a skip payload omits `passed` entirely.
        if key.endswith("_gate") and ("passed" in node or "skipped" in node):
            yield key, node
            return
        for child_key, child in node.items():
            yield from find_gates(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from find_gates(child, key)


def collect(eval_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Return the gates found across ``eval_dir``'s reports, and unreadable files."""
    gates: dict[str, dict] = {}
    unreadable: list[str] = []
    for path in sorted(eval_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Surfaced in the summary rather than swallowed: a corrupt report is
            # missing evidence, and reading it as "no gates" would look like a pass.
            unreadable.append(f"{path.name}: {exc}")
            continue
        for key, gate in find_gates(payload):
            if key in gates and gates[key] != gate:
                # Two reports disagree about one gate. Keeping the first silently
                # could let a passing duplicate mask a breach.
                unreadable.append(
                    f"{path.name}: conflicting verdict for '{key}' "
                    "(another report already reported it differently)"
                )
                continue
            gates.setdefault(key, gate)
    return gates, unreadable


def _verdict(gate: dict) -> tuple[str, str | None]:
    """Return ``(cell, warning_detail)``; ``warning_detail`` is None when clean."""
    if gate.get("skipped"):
        # `passed` is meaningless when nothing was scored - never render it as a pass.
        reason = gate.get("reason") or "no reason recorded"
        return (
            f"not evaluated ({reason})",
            "was NOT evaluated, so this run proves nothing about it. Read it as missing "
            "evidence, not as a pass.",
        )
    if gate.get("passed"):
        return "pass", None
    if gate.get("enforce"):
        return (
            "**BREACH** (BLOCKING)",
            "breached an enforced bar - the build should be red.",
        )
    return (
        "**BREACH** (advisory)",
        "breached its bar. The manifest ships enforce:false, so this does NOT fail the "
        "build - open the 'gaia-eval-report' artifact and treat it as a real regression "
        "until proven otherwise.",
    )


def render(gates: dict[str, dict], unreadable: list[str]) -> tuple[str, list[str]]:
    """Return ``(markdown, warnings)``."""
    lines = ["## GAIA agent eval - gate verdicts", ""]
    warnings: list[str] = []

    if not gates:
        lines += [
            "**No gate report was produced.** The eval did not get far enough to "
            "measure anything - read the failing step above. Do NOT read this as a pass.",
            "",
        ]
        warnings.append(
            "GAIA agent eval produced no gate report. There is no LLM-behavior "
            "evidence for this change."
        )
    else:
        lines += ["| Gate | Verdict |", "| --- | --- |"]
        # Pipeline order for the expected gates; unknown ones (a gate added later)
        # trail alphabetically rather than being dropped.
        order = list(GATE_LABELS)
        keys = sorted(
            set(gates) | set(order),
            key=lambda k: (order.index(k) if k in order else len(order), k),
        )
        for key in keys:
            label = GATE_LABELS.get(key, key)
            if key not in gates:
                cell = "no verdict produced"
                detail = (
                    "produced no verdict at all - its eval step did not get far enough "
                    "to write a report. Read it as missing evidence, not as a pass."
                )
            else:
                cell, detail = _verdict(gates[key])
            lines.append(f"| {label} | {cell} |")
            if detail:
                warnings.append(f"GAIA agent eval - {label} {detail}")
        lines.append("")

    if unreadable:
        lines.append("Unreadable report files (evidence missing):")
        lines += [f"- `{item}`" for item in unreadable]
        lines.append("")
        warnings += [
            f"GAIA agent eval - unreadable gate report {item}" for item in unreadable
        ]

    lines += [_footer(gates)]
    return "\n".join(lines), warnings


def _footer(gates: dict[str, dict]) -> str:
    """Describe the enforce posture from the gates themselves.

    Hardcoding "all report-only" is how the summary ends up telling a reader a
    gate that just failed the build was advisory.
    """
    intro = (
        "Bars and the on/off switch live in "
        "`tests/fixtures/gaia/*_gate_thresholds.json`. "
    )
    blocking = sorted(
        GATE_LABELS.get(k, k) for k, g in gates.items() if g.get("enforce")
    )
    advisory = sorted(
        GATE_LABELS.get(k, k) for k, g in gates.items() if not g.get("enforce")
    )
    if not gates:
        return intro + "No gate reported its enforce state in this run."
    if not blocking:
        return intro + (
            "Every gate here ships `enforce: false`, so a breach is reported, not "
            "blocking; flip a manifest's `enforce` to `true` to make that gate block."
        )
    parts = [intro, "**Blocking** (`enforce: true`): " + ", ".join(blocking) + "."]
    if advisory:
        parts.append(" Report-only: " + ", ".join(advisory) + ".")
    parts.append(
        " A breach on a blocking gate fails the build; a breach on a report-only "
        "gate does not."
    )
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    eval_dir = Path(argv[0]) if argv else DEFAULT_EVAL_DIR

    gates, unreadable = collect(eval_dir) if eval_dir.is_dir() else ({}, [])
    markdown, warnings = render(gates, unreadable)

    for warning in warnings:
        print(f"::warning::{warning}")
    if gates and not warnings:
        print(f"::notice::GAIA agent eval: all {len(gates)} gates passed.")

    print(markdown)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")

    # Reporter, never a gate -- see the module docstring.
    return 0


if __name__ == "__main__":
    sys.exit(main())
