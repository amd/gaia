# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Judge the artefacts a task produced. Correctness is already settled elsewhere.

The verifier has said whether the task was accomplished. It cannot say whether
the work is any good: a fix that passes the tests by special-casing them, a
README that invents a flag, a review that finds the three planted defects and
eight imaginary ones — all exit 0. That judgement is what this adds, and it is
kept strictly apart from correctness so the two are never blended into a single
number that means neither.

Two properties make the verdict worth trusting, both mechanical:

* **Blind.** For each task the arms are permuted onto letters by a stable hash
  of the task key, so the judge never sees which model wrote what and cannot
  favour one it recognises. The mapping is saved, so a score is still traceable.
* **Anchored to the task's own rubric.** Each task states what matters for it —
  minimality for a bug fix, honesty for a review, runnability for a README. A
  single generic "is this good code" prompt scores style, not fitness.

**Only accomplished attempts are judged.** Rating the elegance of work that did
not function invites a model to be rewarded for a beautifully-written wrong
answer. Where an arm failed the task it has no quality score, and the report
shows the count so a high score over two attempts is never read as a high score
over eight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Dict, List, Sequence

RUBRIC = """You are reviewing work produced by several engineers for the same task.

For each task you get: the request, what mattered for it, and each engineer's
submitted files labelled A, B, C… Every submission already PASSED the automated
check, so correctness is settled — do not re-score it. Judge the quality of the
work on the stated criteria.

3 - work you would approve without comment. Minimal, idiomatic, does exactly
    what was asked and nothing more.
2 - you would approve it with a nit: slightly clumsy, mildly over-built, or a
    missed edge the criteria mention.
1 - you would send it back. It works, but by special-casing, by inventing
    things that are not true, or by changing far more than the task needed.
0 - it passes the check almost by accident: gamed the test, or the content is
    largely fabricated.

Penalise inventing facts heavily — a README describing a flag that does not
exist is worse than a terse one. Penalise scope creep: unrequested refactors,
reformatting of untouched code, new dependencies.

Return ONLY a JSON array, no prose:
[{"task": "...", "scores": {"A": 2, "B": 3}, "note": "one short sentence"}]
"""


def blind_mapping(task_key: str, arms: List[str]) -> Dict[str, str]:
    """Stable per-task permutation of arms onto letters.

    Derived from the task key so a rerun reproduces it exactly, while position
    still carries no information about which arm is which.
    """
    ordered = sorted(arms)
    seed = int(hashlib.sha256(task_key.encode()).hexdigest()[:8], 16)
    shift = seed % len(ordered)
    rotated = ordered[shift:] + ordered[:shift]
    return {chr(ord("A") + i): arm for i, arm in enumerate(rotated)}


def load_episodes(arms_dir: Path) -> Dict[str, Dict[str, dict]]:
    """arm -> task key -> episode."""
    out: Dict[str, Dict[str, dict]] = {}
    for d in sorted(arms_dir.iterdir()):
        f = d / "episodes.json"
        if f.exists():
            out[d.name] = {
                e["task"]: e for e in json.loads(f.read_text(encoding="utf-8"))
            }
    return out


#: Per-file and per-submission caps. The per-file cap is the important one: a
#: single large file must never consume the whole budget. It did exactly that
#: once — a 12,000-character log crowded out the 263-character source file the
#: judge was meant to review, and the arm was scored on the log.
MAX_FILE_CHARS = 4000
MAX_SUBMISSION_CHARS = 12000


def render_submission(episode: dict, expected: Sequence[str] = ()) -> str:
    """The files an attempt left behind, ordered so the work comes first.

    Files the task expected to be touched are rendered first, because budget is
    finite and truncation must fall on incidentals rather than on the artefact
    being judged.
    """
    artefacts = {
        rel: body
        for rel, body in (episode.get("artefacts") or {}).items()
        if not rel.startswith("__")
    }
    order = sorted(artefacts, key=lambda r: (r not in set(expected), r))

    parts, used = [], 0
    for rel in order:
        body = artefacts[rel]
        if len(body) > MAX_FILE_CHARS:
            body = body[:MAX_FILE_CHARS] + f"\n…[truncated, {len(body)} chars]"
        chunk = f"--- {rel} ---\n{body}"
        if used + len(chunk) > MAX_SUBMISSION_CHARS:
            parts.append(f"--- {rel} ---\n[omitted, budget exhausted]")
            continue
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts) or "(no files produced)"


def build_batches(tasks, episodes, arms, batch_size):
    from .suite import BY_KEY

    batches, mapping, cur = [], {}, []
    for key in tasks:
        task = BY_KEY[key]
        eligible = [a for a in arms if (episodes[a].get(key) or {}).get("accomplished")]
        if len(eligible) < 2:
            # Nothing to compare. A single submission judged alone drifts against
            # whatever else shares its batch, which is the confound that made the
            # step-level scores incomparable across passes.
            continue
        letters = blind_mapping(key, eligible)
        mapping[key] = letters
        cur.append(
            {
                "task": key,
                "request": task.prompt,
                "what_matters": task.rubric,
                "submissions": {
                    ltr: render_submission(episodes[arm][key], task.expect_touched)
                    for ltr, arm in letters.items()
                },
            }
        )
        if len(cur) >= batch_size:
            batches.append(cur)
            cur = []
    if cur:
        batches.append(cur)
    return batches, mapping


def judge_batch(batch, model, usage, lock):
    from anthropic import Anthropic

    resp = Anthropic().messages.create(
        model=model,
        max_tokens=8000,
        system=RUBRIC,
        messages=[
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)[:150000]}
        ],
    )
    with lock:
        usage["in"] += resp.usage.input_tokens
        usage["out"] += resp.usage.output_tokens
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        return json.loads(m.group(0))
    # A truncated response still holds complete objects before the cut. Salvaging
    # them beats discarding a batch the arms already paid to produce.
    salvaged = [
        json.loads(obj) for obj in re.findall(r"\{\s*\"task\".*?\}\s*\}", text, re.S)
    ]
    if salvaged:
        return salvaged
    raise ValueError(f"judge returned no parseable scores: {text[:200]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--arms", default="_arms")
    ap.add_argument("--model", default="Claude-Opus-5")
    ap.add_argument("--batch-size", type=int, default=3)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()

    episodes = load_episodes(a.run / a.arms)
    if len(episodes) < 2:
        raise SystemExit(
            f"quality judging compares arms against each other and found "
            f"{len(episodes)} under {a.run / a.arms}. Run at least two arms first."
        )
    arms = sorted(episodes)
    tasks = sorted({k for m in episodes.values() for k in m})
    batches, mapping = build_batches(tasks, episodes, arms, a.batch_size)
    if not batches:
        raise SystemExit(
            "No task was accomplished by two or more arms, so there is nothing "
            "to compare. Correctness results are still in the arm directories."
        )

    print(f"{len(mapping)} comparable tasks x {len(arms)} arms: {', '.join(arms)}")
    usage, lock, verdicts, errors = {"in": 0, "out": 0}, Lock(), {}, {}

    def work(item):
        i, batch = item
        try:
            for v in judge_batch(batch, a.model, usage, lock):
                with lock:
                    verdicts[v["task"]] = v
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            with lock:
                errors[i] = f"{type(exc).__name__}: {exc}"[:200]

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(work, enumerate(batches)))
    elapsed = time.time() - t0

    totals = defaultdict(list)
    for key, v in verdicts.items():
        for ltr, score in (v.get("scores") or {}).items():
            arm = mapping.get(key, {}).get(ltr)
            if arm:
                totals[arm].append(score)

    out = a.run / "_quality"
    out.mkdir(parents=True, exist_ok=True)
    (out / "mapping.json").write_text(json.dumps(mapping, indent=1), encoding="utf-8")
    (out / "verdicts.json").write_text(json.dumps(verdicts, indent=1), encoding="utf-8")
    (out / "scorecard.json").write_text(
        json.dumps(
            {
                "judge_model": a.model,
                "tasks_comparable": len(mapping),
                "tasks_judged": len(verdicts),
                "arms": {
                    arm: {
                        "tasks_scored": len(s),
                        "mean_raw": round(sum(s) / len(s), 3),
                        "quality": round(100 * sum(s) / (len(s) * 3), 1),
                    }
                    for arm, s in totals.items()
                    if s
                },
                "judge_usage": usage,
                "judge_wall_clock_s": round(elapsed, 1),
                "batch_errors": errors,
                "measures": (
                    "output quality of ACCOMPLISHED attempts only, judged 0-3 "
                    "blind against each task's own rubric. Correctness is "
                    "separate and mechanical - see episodes.json."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\njudged {len(verdicts)}/{len(mapping)} tasks in {elapsed:.0f}s")
    if errors:
        print(f"  {len(errors)} batch error(s): {list(errors.values())[:2]}")
    print(f"  judge tokens in={usage['in']:,} out={usage['out']:,}\n")
    for arm, s in sorted(totals.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        print(f"{arm[:30]:31s}{100*sum(s)/(len(s)*3):>7.1f}  (n={len(s)})")


if __name__ == "__main__":
    main()
