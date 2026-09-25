# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn the rendered tables into a written analysis, using Claude Code.

The deterministic pipeline produces evidence, not a report: nothing in it ranks
what matters, connects two tables, or says what to do. That step was documented
only as "hand it to an AI assistant". This runs it.

Optional by construction, and it reads only what the other steps already wrote
— it never touches a raw transcript.

The honesty rules are not advice to the model, they are the contract: a
tool-failure rate is never a task-failure rate, friction signals are proxies,
cost is API-equivalent on a subscription, and duration is unusable past the
median. Analysis that quietly breaks those is worse than no analysis.

Privacy: this sends the rendered tables to Claude Code. They are aggregates,
but they carry binary names and use-case labels derived from your work.

Usage::

    python -m gaia.factory.harvest.synthesize [--cache DIR] > analysis.md
"""

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from gaia.factory.harvest.claude_cli import DEFAULT_MODEL, run_claude
from gaia.factory.harvest.scan import DEFAULT_OUT

# What the reference corpus showed was worth reporting. Kept in step with
# .claude/skills/analyzing-claude-sessions/SKILL.md, which is the source.
FOCUS = """\
- Token composition, not totals. Cache-read vs cache-write vs output: agentic
  coding is overwhelmingly context, with output a rounding error.
- Binary frequency inside shell commands — which tools the model actually
  reaches for, versus which it was given.
- Failure rate per tool, never corpus-wide alone.
- Failure rate by position in the session: does reliability decay as context
  fills, or is it flat?
- What happens after a failure — recovery rate and streak length separate
  "handles errors well" from "gets stuck".
- Main-session vs subagent rates, which isolates the cost of write capability.
- Whether this workload could run on a local model, given the per-request
  prompt sizes and KV-cache figures.
"""

HONESTY = """\
- There is NO ground truth for task success. Nothing in a transcript says
  whether the goal was met. Never present a tool-failure rate as a task-failure
  rate, and never call any number a success rate.
- Friction signals (corrections, interrupts) are regex proxies. They are
  evidence, not proof. Compare them between use-cases; do not quote absolutes.
- Cost is API-equivalent, not money spent, if the sessions ran on a
  subscription. Say so wherever you give a dollar figure.
- Duration is unusable past the median: a session left open overnight reports
  the whole night as active time.
- State the snapshot the corpus describes, and that it grows while it is
  analysed.
- Every figure you quote must come from the tables below. Do not estimate,
  extrapolate, or fill a gap with a plausible number. If the tables do not
  answer something, say it is not measured.
"""

PROMPT = """\
You are writing the analysis that accompanies a Claude Code session-analytics
report. The deterministic pipeline produced the tables below; your job is the
layer it does not do — what the numbers mean, ranked.

Write Markdown with these sections, and nothing else:

## What this corpus shows
Three to six findings, most important first. Each is a claim in plain language
first, with the figure that supports it second. A finding a reader cannot act
on or repeat is not a finding.

## What would change the numbers
The two or three interventions the data actually supports, each with the figure
that bounds it.

## What this data cannot tell you
The limits that matter for these specific findings.

Focus on:
{focus}
Honesty requirements — these are binding:
{honesty}
Style: lead with the finding, put the mechanism underneath. No preamble, no
restating the question, no summary of the summary. Every sentence earns its
place.

=== EVIDENCE ===
{evidence}
"""


def gather(cache: Path) -> Tuple[str, List[str]]:
    """The rendered reports, and the names of the ones that were present."""

    wanted = ["tables.md", "context.md", "savings.md"]
    parts, found = [], []
    for name in wanted:
        path = cache / name
        if not path.exists():
            continue
        parts.append(f"--- {name} ---\n{path.read_text(encoding='utf-8')}")
        found.append(name)
    if "tables.md" not in found:
        raise SystemExit(
            f"{cache / 'tables.md'} not found. Generate the evidence first:\n"
            "  python -m gaia.factory.harvest.scan\n"
            "  python -m gaia.factory.harvest.report > "
            f"{cache / 'tables.md'}"
        )
    return "\n\n".join(parts), found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--claude-bin", default=None)
    args = ap.parse_args()

    evidence, found = gather(args.cache)
    print(f"synthesising from {', '.join(found)}...", file=sys.stderr)
    answer = run_claude(
        PROMPT.format(focus=FOCUS, honesty=HONESTY, evidence=evidence),
        binary=args.claude_bin,
        model=args.model,
        timeout=args.timeout,
    )
    print(answer)
    print(
        f"\n---\n\n_Written by `gaia.factory.harvest.synthesize` from "
        f"{', '.join(found)}. Every figure should be checked against those "
        "tables before the analysis is shared._"
    )


if __name__ == "__main__":
    main()
