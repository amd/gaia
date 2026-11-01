# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A benchmark that outgrows its ctx window must say so, never report 0% (#2663)."""

import sys

import pytest

from gaia.eval import benchmark
from gaia.eval.benchmark import context_overflow_detail, run_benchmark

LLAMA_OVERFLOW = (
    "LemonadeError: request (19337 tokens) exceeds the available context size "
    "(16384 tokens), try increasing it"
)


def _raising_agent(message):
    class _Agent:
        def process_query(self, prompt):
            raise RuntimeError(message)

    return _Agent


def test_overflow_row_names_tokens_and_remedy():
    results = run_benchmark(
        "m",
        mbox_path="ignored",
        limit=100,
        ctx_size=16384,
        experiments=1,
        agent_factory=_raising_agent(LLAMA_OVERFLOW),
    )
    row = results[0]
    assert row["status"] == "ERRORED"
    assert row["error_kind"] == "context_overflow"
    assert (row["requested_tokens"], row["available_tokens"]) == (19337, 16384)
    assert "not a quality result" in row["remedy"]
    assert "--ctx-size" in row["remedy"] and "--limit" in row["remedy"]
    assert "100 emails" in row["remedy"]


@pytest.mark.parametrize(
    "message",
    [
        "LemonadeError: exceed_context_size",
        "FastFlowLM: Max length reached!",
        '{"error": {"message": "the request got too long for the context"}}',
    ],
)
def test_overflow_without_numbers_still_classified(message):
    detail = context_overflow_detail(message, 16384, 50)
    assert detail["error_kind"] == "context_overflow"
    assert "requested_tokens" not in detail
    assert "16384-token window" in detail["remedy"]


def test_unknown_window_is_worded_without_a_number():
    detail = context_overflow_detail("exceed_context_size", None, 50)
    assert "loaded-token window" in detail["remedy"]


@pytest.mark.parametrize(
    "message",
    [
        "boom",
        "context size (4096",  # mentions ctx but is not a backend overflow
        "failed to find free space in the KV cache",  # shared-pool, not overflow
        "",
    ],
)
def test_other_failures_are_not_overflow(message):
    assert context_overflow_detail(message, 16384, 50) is None


def test_generic_failure_row_has_no_overflow_fields():
    results = run_benchmark(
        "m",
        mbox_path="ignored",
        experiments=1,
        agent_factory=_raising_agent("boom"),
    )
    assert results[0]["status"] == "ERRORED"
    assert "error_kind" not in results[0]


def test_cli_exits_nonzero_without_summarizing(monkeypatch, capsys):
    from gaia import cli

    row = {
        "status": "ERRORED",
        "error": LLAMA_OVERFLOW,
        **context_overflow_detail(LLAMA_OVERFLOW, 16384, 100),
    }
    monkeypatch.setattr(benchmark, "run_benchmark", lambda *a, **k: [row])

    def no_summary(*_a, **_k):
        raise AssertionError("an overflowed run must not be summarized")

    monkeypatch.setattr(benchmark, "summarize_benchmark", no_summary)
    monkeypatch.setattr(sys, "argv", ["gaia", "eval", "benchmark", "--limit", "100"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "1 of 1 experiment(s) hit the context window" in err
    assert "needed 19337 tokens but the window is 16384" in err
