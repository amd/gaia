# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Assign a use-case to every session, using Claude Code.

``scan`` writes ``intents.jsonl`` but no use-case label, so the "by use case"
sections of every report come up empty until someone labels the corpus by hand.
This does that step, and writes the ``labels.txt`` that ``report --labels`` and
``context --labels`` already expect.

Optional by construction: the deterministic reports are complete without it.

Labels are assigned from each session's **opening instruction**, never the
auto-generated title — a title summarises what happened, which leaks the
outcome into the label.

Privacy: this sends the opening instruction of every session to Claude Code.
The deterministic steps never leave the machine; this one does.

Usage::

    python -m gaia.factory.harvest.classify [--cache DIR] [--batch N]
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from gaia.factory.harvest.claude_cli import (
    DEFAULT_MODEL,
    extract_json,
    run_claude,
)
from gaia.factory.harvest.scan import DEFAULT_OUT

# The starting taxonomy from the reference corpus. Closed on purpose: a batch
# that invents its own tags makes the per-use-case tables incomparable between
# batches, which is the failure this step exists to avoid.
TAXONOMY = [
    "pr_lifecycle",
    "code_review",
    "doc_audit",
    "feature_impl",
    "bug_fix",
    "ci_debug",
    "security_fix",
    "release_ops",
    "eval_analysis",
    "research",
    "other",
]

# Assigned locally, never by the model: a session whose transcript carries no
# opening instruction (resumed, aborted, or metadata-only) cannot be classified
# from one. Folding these into "other" hides them among sessions that did state
# a goal the taxonomy simply did not fit.
NO_INSTRUCTION = "no_instruction"

PROMPT = """\
You are labelling Claude Code sessions by the work the user asked for.

Assign exactly one tag to each session, chosen ONLY from this list:
{taxonomy}

Rules:
- Judge from the opening instruction alone. It states what was asked; do not
  infer what the outcome turned out to be.
- Use "other" only when no tag fits. Do not invent tags.
- Every id below must appear exactly once in your answer.

Return ONLY a JSON object mapping id to tag, no prose:
{{"<id>": "<tag>", ...}}

Sessions:
{sessions}
"""


def load_intents(cache: Path) -> List[dict]:
    path = cache / "intents.jsonl"
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run `python -m gaia.factory.harvest.scan` first."
        )
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise SystemExit(f"{path} is empty — nothing to classify.")
    return rows


def split_unclassifiable(rows: List[dict]) -> Tuple[List[dict], Dict[str, str]]:
    """Separate sessions with no opening instruction from the rest."""

    askable, fixed = [], {}
    for row in rows:
        if " ".join((row.get("goal") or "").split()):
            askable.append(row)
        else:
            fixed[row["session_id"][:8]] = NO_INSTRUCTION
    return askable, fixed


def build_prompt(batch: List[dict]) -> str:
    lines = []
    for row in batch:
        goal = " ".join((row.get("goal") or "").split())[:600]
        lines.append(f'- {row["session_id"][:8]}: {goal or "(no opening instruction)"}')
    return PROMPT.format(
        taxonomy="\n".join(f"- {t}" for t in TAXONOMY),
        sessions="\n".join(lines),
    )


def validate(answer: object, batch: List[dict]) -> Dict[str, str]:
    """Accept a batch's labels only if they cover it exactly, with known tags.

    A batch that silently drops sessions or invents a tag would produce a
    labels.txt whose coverage nobody checked — the exact failure #3934 makes
    report refuse to render.
    """

    if not isinstance(answer, dict):
        raise SystemExit(
            f"Expected a JSON object mapping id to tag, got {type(answer).__name__}."
        )
    wanted = {row["session_id"][:8] for row in batch}
    got = {str(k)[:8] for k in answer}
    missing = sorted(wanted - got)
    extra = sorted(got - wanted)
    if missing or extra:
        raise SystemExit(
            "The model's labels do not match the batch it was given: "
            f"{len(missing)} session(s) unlabelled ({', '.join(missing[:5])}), "
            f"{len(extra)} id(s) invented ({', '.join(extra[:5])}). "
            "Re-run, or use a smaller --batch."
        )
    out = {}
    for key, tag in answer.items():
        tag = str(tag).strip()
        if tag not in TAXONOMY:
            raise SystemExit(
                f"Session {key} got tag {tag!r}, which is not in the taxonomy. "
                "The taxonomy is closed so batches stay comparable."
            )
        out[str(key)[:8]] = tag
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--out", type=Path, default=None, help="Default: <cache>/labels.txt"
    )
    ap.add_argument("--batch", type=int, default=40, help="Sessions per request.")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--claude-bin", default=None)
    args = ap.parse_args()

    rows = load_intents(args.cache)
    out_path = args.out or (args.cache / "labels.txt")
    askable, labels = split_unclassifiable(rows)
    if labels:
        print(
            f"{len(labels)} session(s) carry no opening instruction -> {NO_INSTRUCTION}"
        )
    batches = [askable[i : i + args.batch] for i in range(0, len(askable), args.batch)]
    for n, batch in enumerate(batches, 1):
        print(f"[{n}/{len(batches)}] labelling {len(batch)} sessions...", flush=True)
        answer = run_claude(
            build_prompt(batch),
            binary=args.claude_bin,
            model=args.model,
            timeout=args.timeout,
        )
        labels.update(validate(extract_json(answer, what="labels"), batch))

    # Written only after every batch validated: a partial labels.txt silently
    # shrinks the population every use-case table is drawn over.
    out_path.write_text(
        "".join(f"{sid} {tag}\n" for sid, tag in sorted(labels.items())),
        encoding="utf-8",
    )
    spread = ", ".join(
        f"{t}={sum(1 for v in labels.values() if v == t)}"
        for t in TAXONOMY + [NO_INSTRUCTION]
        if any(v == t for v in labels.values())
    )
    print(f"wrote {len(labels)} labels to {out_path}\n{spread}")


if __name__ == "__main__":
    main()
