# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The embeddings test must be dispatchable onto every box the driver-update workflow patches.

stx_driver_update.yml exists to fix a GPU-driver window that breaks embedder loading,
and a maintainer validates a candidate driver on ``stx-test`` before rolling it to
``stx``. That only works if Test Lemonade Embeddings can be pointed at the same boxes,
while push/PR runs stay on the DevLab pool.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
DEVLAB_POOL = ["self-hosted", "Windows", "devlab-dispatch", "strix-halo"]


def _load(name: str) -> dict:
    # PyYAML 1.1 parses the `on:` key as the boolean True.
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def embeddings() -> dict:
    return _load("test_embeddings.yml")


def _dispatch_input(wf: dict) -> dict:
    inputs = (wf[True]["workflow_dispatch"] or {}).get("inputs") or {}
    assert "runner_label" in inputs, "test_embeddings.yml has no runner_label input"
    return inputs["runner_label"]


def _resolve_runs_on(expr: str, label: str | None) -> list[str]:
    """Evaluate the one expression shape the job uses: a static-box allowlist, else the pool."""
    m = re.fullmatch(
        r"\$\{\{ contains\(fromJSON\('(?P<allow>\[.*?\])'\), inputs\.runner_label\) "
        r"&& fromJSON\(format\('\[\"\{0\}\"\]', inputs\.runner_label\)\) "
        r"\|\| fromJSON\('(?P<pool>\[.*?\])'\) \}\}",
        expr.strip(),
    )
    assert m, f"runs-on changed shape; update this test to match: {expr!r}"
    allow, pool = json.loads(m["allow"]), json.loads(m["pool"])
    return [label] if label in allow else pool


def test_dispatch_offers_every_driver_update_runner(embeddings):
    driver_opts = set(_dispatch_input(_load("stx_driver_update.yml"))["options"])
    ours = _dispatch_input(embeddings)
    assert driver_opts <= set(ours["options"])
    assert ours["default"] == "devlab"


@pytest.mark.parametrize(
    "label, expected",
    [
        (None, DEVLAB_POOL),  # push / pull_request / merge_group / workflow_call
        ("", DEVLAB_POOL),
        ("devlab", DEVLAB_POOL),
        ("stx", ["stx"]),
        ("stx-test", ["stx-test"]),
        ("stx-test-2", DEVLAB_POOL),  # near-miss names must not leak through
    ],
)
def test_runs_on_resolves(embeddings, label, expected):
    expr = embeddings["jobs"]["test-embeddings"]["runs-on"]
    assert isinstance(
        expr, str
    ), "runs-on is a static list; a dispatch can't retarget it"
    assert _resolve_runs_on(expr, label) == expected


def test_every_dispatch_option_resolves_to_a_runner(embeddings):
    expr = embeddings["jobs"]["test-embeddings"]["runs-on"]
    for opt in _dispatch_input(embeddings)["options"]:
        resolved = _resolve_runs_on(expr, opt)
        assert resolved == (DEVLAB_POOL if opt == "devlab" else [opt]), opt


def test_runs_on_different_boxes_do_not_cancel_each_other(embeddings):
    group = embeddings["concurrency"]["group"]
    assert "inputs.runner_label" in group, (
        "concurrency group ignores the target box, so a dispatch to stx-test "
        "cancels a run on stx or on the pool"
    )
