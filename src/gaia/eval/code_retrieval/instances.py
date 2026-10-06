# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Labelled queries from real fixed issues: SWE-bench Verified and SWE-rebench.

A query is an issue's problem statement; what it should retrieve comes from the
fix's gold patch (:mod:`gaia.eval.code_retrieval.gold`). Nothing is committed: rows
are fetched at run time and cached as JSON under the work root.

SWE-rebench (``nebius/SWE-rebench-leaderboard``) publishes a split per month of
newly merged fixes. Its 2026 splits postdate the embedding model's release
(September 2025) and the training cutoff of most current models, so a high
score there cannot come from having seen the fix.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlencode

from gaia.eval.bench import swebench

REBENCH = "nebius/SWE-rebench-leaderboard"
REBENCH_COLUMNS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "patch",
    "created_at",
)
FETCH_TIMEOUT_S = 120


class InstanceError(RuntimeError):
    """A dataset could not be read, or an instance is unusable as a query."""


@dataclass(frozen=True)
class Query:
    id: str
    source: str
    repo: str
    base_commit: str
    text: str
    patch: str
    created_at: Optional[str] = None

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}.git"


def verified(ids: Sequence[str], work_root: Path) -> List[Query]:
    """SWE-bench Verified instances as queries, from the bench's instance cache."""
    rows = swebench.load_instances(list(ids), swebench.cache_dir(work_root))
    return [
        Query(
            id=r["instance_id"],
            source="swebench-verified",
            repo=r["repo"],
            base_commit=r["base_commit"],
            text=r["problem_statement"],
            patch=r["patch"],
        )
        for r in rows
    ]


def _rows(dataset: str, split: str) -> List[Dict[str, Any]]:
    import requests  # pylint: disable=import-outside-toplevel

    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        query = urlencode(
            {
                "dataset": dataset,
                "config": "default",
                "split": split,
                "offset": offset,
                "length": swebench.ROWS_PER_PAGE,
            }
        )
        try:
            resp = requests.get(
                f"{swebench.DATASET_ROWS_URL}?{query}", timeout=FETCH_TIMEOUT_S
            )
            resp.raise_for_status()
            page = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise InstanceError(
                f"could not read {dataset} split {split} from "
                f"{swebench.DATASET_ROWS_URL} (offset {offset}): {exc}. Retry when "
                "the Hugging Face datasets-server answers."
            ) from exc
        batch = [entry["row"] for entry in page.get("rows") or []]
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("num_rows_total") or 0):
            return rows


def rebench_rows(split: str, work_root: Path) -> List[Dict[str, Any]]:
    """Every row of one SWE-rebench split, cut to the columns used, cached."""
    path = work_root / "retrieval" / "datasets" / f"swe-rebench-{split}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for row in _rows(REBENCH, split):
        missing = [c for c in REBENCH_COLUMNS if c not in row]
        if missing:
            raise InstanceError(f"{REBENCH} {split} row lacks {missing}")
        rows.append({c: row[c] for c in REBENCH_COLUMNS})
    if not rows:
        raise InstanceError(f"{REBENCH} has no rows in split {split!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def rebench_pool(splits: Sequence[str], work_root: Path) -> Dict[str, Query]:
    """Every SWE-rebench row in *splits*, as queries keyed by instance id."""
    pool: Dict[str, Query] = {}
    for split in splits:
        for row in rebench_rows(split, work_root):
            pool[row["instance_id"]] = Query(
                id=row["instance_id"],
                source="swe-rebench",
                repo=row["repo"],
                base_commit=row["base_commit"],
                text=row["problem_statement"],
                patch=row["patch"],
                created_at=row["created_at"],
            )
    return pool


def seeded_order(ids: Sequence[str], seed: int) -> List[str]:
    """*ids* sorted, then shuffled by *seed*: the order a seeded sample is drawn in."""
    order = sorted(ids)
    random.Random(seed).shuffle(order)
    return order
