# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia eval retrieval --component code``: run a suite, write JSON + markdown, gate."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from gaia.eval.code_retrieval import quality, robustness, scale
from gaia.eval.code_retrieval.common import embedder_preflight, environment
from gaia.eval.code_retrieval.report import compare, markdown
from gaia.eval.code_retrieval.suites import (
    FILE_KS,
    NIGHTLY_MAX_CHUNKS,
    SEARCH_DEPTH,
    SUITES,
    SYMBOL_KS,
    Suite,
)

PARTS = ("quality", "incremental", "robustness", "scale")
SCHEMA = 1


def suite_parts(suite: Suite) -> Sequence[str]:
    """The parts *suite* defines, in run order."""
    has = {
        "quality": bool(
            suite.verified_ids or suite.verified_sample or suite.rebench_sample
        ),
        "incremental": suite.incremental is not None,
        "robustness": suite.robustness is not None,
        "scale": bool(suite.scale),
    }
    return [p for p in PARTS if has[p]]


def load_result(path: Path) -> Dict[str, Any]:
    """A result JSON, given the file or the directory holding ``results.json``."""
    target = path / "results.json" if path.is_dir() else path
    if not target.is_file():
        raise FileNotFoundError(f"no retrieval results at {target}")
    return json.loads(target.read_text(encoding="utf-8"))


def run(
    suite_name: str,
    out_dir: Path,
    work_root: Path,
    parts: Optional[Sequence[str]] = None,
    baseline: Optional[Path] = None,
    max_drop: float = 0.05,
    keep_index: bool = False,
    progress=print,
) -> Dict[str, Any]:
    suite = SUITES[suite_name]
    available = suite_parts(suite)
    chosen = list(parts or available)
    unknown = [p for p in chosen if p not in available]
    if unknown:
        raise ValueError(
            f"suite {suite_name!r} has no {unknown}; it runs {list(available)}"
        )
    base = load_result(baseline) if baseline else None
    if base is not None and base.get("suite") != suite_name:
        raise ValueError(
            f"baseline {baseline} is suite {base.get('suite')!r}, not {suite_name!r}"
        )
    started = time.time()
    env = {**environment(), **embedder_preflight()}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    index_root = work_root / "retrieval" / "index" / f"{suite_name}-{stamp}"
    result: Dict[str, Any] = {
        "schema": SCHEMA,
        "component": "code",
        "suite": suite_name,
        "parts": chosen,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "environment": env,
        "config": {
            "search_depth": SEARCH_DEPTH,
            "file_ks": list(FILE_KS),
            "symbol_ks": list(SYMBOL_KS),
            "seed": suite.seed,
            "max_repo_chunks": suite.max_repo_chunks,
            "nightly_max_chunks": NIGHTLY_MAX_CHUNKS,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        (out_dir / "results.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (out_dir / "report.md").write_text(markdown(result), encoding="utf-8")

    try:
        if "quality" in chosen:
            result["quality"] = quality.run(
                suite, work_root, index_root / "quality", progress
            )
        if "incremental" in chosen:
            result["incremental"] = scale.replay(
                suite.incremental, work_root, index_root, progress
            )
        if "robustness" in chosen:
            pin, subdir = suite.robustness
            result["robustness"] = robustness.run(
                pin, subdir, work_root, index_root, progress
            )
        if "scale" in chosen:
            progress(f"[scale] {len(suite.scale)} repositories, smallest first")
            result["scale"] = []
            for pin in suite.scale:
                result["scale"].append(
                    scale.measure(pin, work_root, index_root, progress)
                )
                save()
    except BaseException as exc:
        # Hours of finished parts are worth keeping even when a later one dies.
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["duration_s"] = round(time.time() - started, 1)
        save()
        raise
    finally:
        if not keep_index and index_root.exists():
            shutil.rmtree(index_root)
    result["duration_s"] = round(time.time() - started, 1)
    save()
    if base is not None:
        result["comparison"] = compare(base, result, max_drop)
        save()
    return result
