# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Context-overflow stress, deterministic tier (#5070).

46 tool steps, a 3 MB result, a scripted model that refuses any request over
the window, and a final answer that needs facts from the earliest documents.
With eviction on, the run never overflows and loses no fact: each one is
re-read through its stub's handle. With it off, the overflow retry is the only
recovery, and it must still leave every result re-readable.
"""

import pytest

from tests.unit.agents.context_overflow_stress import (
    FLAGSHIP_PREFIX_TOKENS,
    NEEDED,
    plan,
    run,
)

#: (window, fixed prefix): GAIA's smallest window bare and with the flagship's
#: real prefix, and its default window with that prefix.
_CASES = (
    (32768, 0),
    (32768, FLAGSHIP_PREFIX_TOKENS),
    (65536, FLAGSHIP_PREFIX_TOKENS),
)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    for name in ("GAIA_CONTEXT_EVICTION", "GAIA_EVICT_THRESHOLD", "GAIA_EVICT_KEEP"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("window, prefix", _CASES)
def test_a_long_tool_heavy_run_fits_and_keeps_every_fact(monkeypatch, window, prefix):
    monkeypatch.setenv("GAIA_CTX_SIZE", str(window))

    report = run(window, "auto", prefix)

    assert report["completed"], report
    assert report["tool_steps_done"] == len(plan()) >= 40
    assert report["overflows"] == 0, report
    assert report["facts_correct"] == len(NEEDED), report["answer"]
    assert report["rereads"] >= 1  # the facts came back through handles
    assert report["tokens_per_call_max"] < window
    assert report["eviction_batches"] == report["eviction_events"] >= 1
    # Stubs break the cache late in the prompt: most of each request is reused.
    assert report["prefix_reuse_mean"] >= 0.7, report
    if window - prefix >= 40000:
        # With room to spare, batches are rare.
        assert report["eviction_batches"] <= report["model_calls"] / 6, report


@pytest.mark.parametrize("window, prefix", _CASES)
def test_with_eviction_off_the_overflow_retry_keeps_results_rereadable(
    monkeypatch, window, prefix
):
    monkeypatch.setenv("GAIA_CTX_SIZE", str(window))

    report = run(window, "off", prefix)

    assert report["completed"], report
    assert report["tool_steps_done"] == len(plan())
    assert report["overflows"] >= 1
    assert report["eviction_events"] == report["overflows"]
    # Results the retry put away kept handles, and the model re-read them.
    assert report["rereads"] >= 1
    assert report["facts_correct"] == len(NEEDED), report["answer"]


def test_a_500_step_run_reaches_its_ceiling_loudly_with_bounded_growth(monkeypatch):
    """Every step leaves a stub and a tool call behind, and neither is folded,
    so a long enough turn fills any window. It must end with the overflow
    message, never an exception or a silent cut, and stay small on disk and
    in memory on the way."""
    monkeypatch.setenv("GAIA_CTX_SIZE", "32768")

    report = run(32768, "on", 0, docs=340)

    assert report["tool_steps_planned"] > 500
    assert 150 <= report["tool_steps_done"] < report["tool_steps_planned"]
    assert report["answer"].startswith("This request needs more than fits")
    assert report["tokens_per_call_max"] <= 32768
    assert report["archive_files"] >= report["tool_steps_done"] - 2
    assert report["store_memory_kb"] < 64 * 1024
    assert report["peak_python_mb"] < 200
