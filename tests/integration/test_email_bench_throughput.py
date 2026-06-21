# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration test for the email-triage throughput benchmark (#1233).

Skipped automatically when Lemonade is not running (via ``require_lemonade``).
Direct-drives ``EmailTriageAgent`` over the committed synthetic stub corpus and
asserts that perf metrics are harvested end-to-end. The committed >10 tok/s bar
is *non-gating for the demo* (#1233): a miss is surfaced via ``xfail`` (visible
in CI) rather than a hard failure, since throughput is hardware-dependent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.integration

from gaia.eval.benchmark import (  # noqa: E402
    THROUGHPUT_BAR_TPS,
    load_ground_truth,
    run_benchmark,
    summarize_benchmark,
)

FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
STUB_INBOX = FIXTURES_DIR / "_stub_inbox.mbox"
GROUND_TRUTH = FIXTURES_DIR / "ground_truth.json"

MODEL = "Gemma-4-E4B-it-GGUF"


def test_throughput_benchmark_end_to_end(require_lemonade, tmp_path):
    """Run one benchmark pass and assert perf metrics are harvested."""
    ground_truth = load_ground_truth(GROUND_TRUTH)

    results = run_benchmark(
        MODEL,
        mbox_path=str(STUB_INBOX),
        limit=10,
        experiments=1,
        ground_truth=ground_truth,
        db_path=str(tmp_path / "state.db"),
    )

    assert len(results) == 1
    r = results[0]
    assert r["status"] == "PASS", f"benchmark did not complete: {r.get('error')}"
    assert r["total_emails"] > 0
    assert "quality" in r  # ground truth was supplied

    summary = summarize_benchmark(results, run_id="itest-bench")
    perf = summary["scorecard"]["performance"]
    tps = perf["avg_tokens_per_second"]
    ttft = perf["avg_time_to_first_token"]
    print(
        f"\nThroughput benchmark ({MODEL}):\n"
        f"  tokens/sec: {tps}\n"
        f"  TTFT (s):   {ttft}\n"
        f"  category accuracy: {r['quality']['category_accuracy']}\n"
    )

    # Perf must actually be harvested from /stats (the core deliverable).
    assert isinstance(tps, (int, float)) and tps > 0, "no throughput harvested"
    assert isinstance(ttft, (int, float)) and ttft > 0, "no TTFT harvested"

    # Committed bar is non-gating for the demo — xfail (visible) on a miss.
    if tps < THROUGHPUT_BAR_TPS:
        pytest.xfail(
            f"throughput {tps} tok/s below committed bar {THROUGHPUT_BAR_TPS} "
            "tok/s (non-gating demo; hardware-dependent)"
        )
