# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship release's eval subset compares against a baseline that exists (#5197).

test_gaia_agent_eval.yml once pointed at a directory nobody created, so every
category printed "no committed baseline" and a release that made the agent
worse was never flagged.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _baseline_dir() -> Path:
    return REPO_ROOT / _load("test_gaia_agent_eval.yml")["env"]["BASELINE_DIR"]


def test_the_baseline_directory_exists_and_is_the_flagships():
    baseline = _baseline_dir()
    assert baseline.is_dir(), f"{baseline} does not exist"
    meta = json.loads((baseline / "meta.json").read_text(encoding="utf-8"))
    assert meta["agent_type"] == "gaia"
    assert meta["model"] == "Gemma-4-E4B-it-GGUF"


def test_every_release_category_has_a_baseline_scorecard():
    job = _load("release_agent_gaia.yml")["jobs"]["gaia-eval"]
    assert job["uses"] == "./.github/workflows/test_gaia_agent_eval.yml"
    categories = job["with"]["categories"].split(",")
    missing = [
        c for c in categories if not (_baseline_dir() / f"scorecard_{c}.json").is_file()
    ]
    assert not missing, f"no baseline scorecard for {missing} in {_baseline_dir()}"
