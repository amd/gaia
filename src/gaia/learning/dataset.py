# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The learning corpus and the per-night training set built from it.

Each night's adapter is trained from the base model on the whole corpus — every
item learned so far — not stacked on yesterday's adapter. That keeps one
adapter per base model, and makes forgetting real: removing an item, or the
user asking the agent to forget something, drops it from the corpus and the
next adapter never sees it.

Layout under ``<gaia_home>/learning/corpus/``:

- ``items.jsonl`` — one learned item per line (an opportunity that produced
  examples), with its examples and probes.
- ``anchors.jsonl`` — the base model's own answers to general prompts, mixed in
  so the adapter keeps its general behaviour.
"""

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.learning.analyze import Opportunity, Probe, Report, Teacher
from gaia.learning.errors import LearningError
from gaia.learning.paths import corpus_dir
from gaia.utils.file_lock import file_lock, write_text_atomic

# General prompts whose base-model answers anchor the adapter. Short, varied,
# and unrelated to any one user.
ANCHOR_PROMPTS = [
    "Explain what a hash table is in two sentences.",
    "Write a haiku about autumn rain.",
    "What are three tips for writing a clear email?",
    "Convert 72 degrees Fahrenheit to Celsius and show the formula.",
    "Summarize the plot of Romeo and Juliet in one paragraph.",
    "What is the difference between a list and a tuple in Python?",
    "Give me a packing checklist for a two-day hiking trip.",
    "Why is the sky blue? Answer for a ten-year-old.",
    "Write a polite message declining a meeting invitation.",
    "What does HTTP status code 404 mean?",
    "Name four planets in our solar system.",
    "How do I compute the average of a list of numbers?",
    "Suggest a name for a golden retriever puppy and explain why.",
    "What is photosynthesis?",
    "Rewrite this more formally: 'gonna be late, traffic is nuts'.",
    "List the steps to boil an egg.",
]

# Share of the training set that is anchors.
ANCHOR_RATIO = 0.25


@dataclass
class TrainingSet:
    train: List[Dict[str, Any]]
    eval: List[Dict[str, Any]]
    probes: List[Probe]
    item_count: int
    counts: Dict[str, int]

    def write(self, out_dir: Path) -> Dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "train": out_dir / "train.jsonl",
            "eval": out_dir / "eval.jsonl",
            "probes": out_dir / "probes.jsonl",
        }
        for key, rows in (("train", self.train), ("eval", self.eval)):
            paths[key].write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
            )
        paths["probes"].write_text(
            "".join(json.dumps(p.__dict__) + "\n" for p in self.probes),
            encoding="utf-8",
        )
        return paths


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise LearningError(
                    f"{path} line {n} is not valid JSON ({e}). Delete the line or "
                    "the file to rebuild the corpus."
                ) from e
    return rows


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, "".join(json.dumps(r) + "\n" for r in rows))


class Corpus:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or corpus_dir()
        self.items_path = self.root / "items.jsonl"
        self.anchors_path = self.root / "anchors.jsonl"

    # -- items --------------------------------------------------------------

    def items(self) -> List[Dict[str, Any]]:
        return _read_jsonl(self.items_path)

    def merge(self, report: Report, run_id: str) -> List[str]:
        """Add the report's usable opportunities; a changed reading of the same
        opportunity replaces the older one. Returns the ids added or updated."""
        changed = []
        with file_lock(self.items_path):
            items = {i["id"]: i for i in self.items()}
            for opp in report.opportunities:
                if opp.error or not opp.examples:
                    continue
                previous = items.get(opp.id)
                fresh = _item(opp, run_id, previous)
                if previous and all(
                    previous.get(k) == fresh[k] for k in ("examples", "probes", "tools")
                ):
                    continue  # read again, unchanged: not a reason to retrain
                items[opp.id] = fresh
                changed.append(opp.id)
            _write_jsonl(self.items_path, list(items.values()))
        return changed

    def remove(self, item_id: str) -> None:
        with file_lock(self.items_path):
            items = self.items()
            kept = [i for i in items if i["id"] != item_id]
            if len(kept) == len(items):
                raise LearningError(
                    f"No learned item {item_id!r}. `gaia learn items` lists them."
                )
            _write_jsonl(self.items_path, kept)

    def drop_forgotten(self, forgotten: List[str]) -> List[str]:
        """Remove every item that mentions something the user asked the agent
        to forget. Returns the removed ids."""
        lows = [f.lower() for f in forgotten if f]
        if not lows:
            return []
        with file_lock(self.items_path):
            items = self.items()
            kept, dropped = [], []
            for i in items:
                text = (
                    json.dumps(i["examples"]).lower() + " ".join(i["evidence"]).lower()
                )
                (dropped if any(f in text for f in lows) else kept).append(i)
            if dropped:
                _write_jsonl(self.items_path, kept)
        return [i["id"] for i in dropped]

    # -- anchors ------------------------------------------------------------

    def anchors(self, base_model: str, teacher: Teacher) -> List[Dict[str, Any]]:
        """The base model's answers to :data:`ANCHOR_PROMPTS`, cached per base."""
        rows = [
            r for r in _read_jsonl(self.anchors_path) if r["base_model"] == base_model
        ]
        if rows:
            return rows
        rows = []
        for prompt in ANCHOR_PROMPTS:
            answer = teacher(prompt).strip()
            if not answer:
                raise LearningError(
                    f"{base_model} returned an empty answer to an anchor prompt "
                    f"({prompt!r}); training without anchors would let the adapter "
                    "drift. Check that the model is loaded and answering."
                )
            rows.append(
                {
                    "base_model": base_model,
                    "messages": [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": answer},
                    ],
                }
            )
        with file_lock(self.anchors_path):
            others = [
                r
                for r in _read_jsonl(self.anchors_path)
                if r["base_model"] != base_model
            ]
            _write_jsonl(self.anchors_path, others + rows)
        return rows

    # -- training set -------------------------------------------------------

    def build(self, anchors: List[Dict[str, Any]], *, seed: int = 0) -> TrainingSet:
        items = self.items()
        if not items:
            raise LearningError(
                "Nothing to learn yet: the corpus is empty. Chat with the agent, "
                "then run `gaia learn run` again."
            )
        rng = random.Random(seed)
        train: List[Dict[str, Any]] = []
        evals: List[Dict[str, Any]] = []
        probes: List[Probe] = []
        counts: Dict[str, int] = {}
        for item in items:
            rows = [
                {
                    "messages": ex,
                    **({"tools": item["tools"]} if item.get("tools") else {}),
                }
                for ex in item["examples"]
            ]
            rng.shuffle(rows)
            # Keep one example out for the held-out loss once there are enough.
            if len(rows) >= 4:
                evals.append(rows.pop())
            train.extend(rows)
            probes.extend(Probe(**p) for p in item.get("probes", []))
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        n_anchor = min(len(anchors), max(1, int(len(train) * ANCHOR_RATIO)))
        picked = rng.sample(anchors, n_anchor) if anchors else []
        train.extend({"messages": a["messages"]} for a in picked)
        rng.shuffle(train)
        return TrainingSet(
            train=train, eval=evals, probes=probes, item_count=len(items), counts=counts
        )


def _item(
    opp: Opportunity, run_id: str, previous: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": opp.id,
        "kind": opp.kind,
        "title": opp.title,
        "evidence": opp.evidence,
        "examples": opp.examples,
        "probes": [p.__dict__ for p in opp.probes],
        "tools": opp.tools,
        "first_learned": previous["first_learned"] if previous else now,
        "updated_at": now,
        "run_id": run_id,
    }
