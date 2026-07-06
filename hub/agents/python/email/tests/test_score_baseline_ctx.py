# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pin the score_baseline.py ctx_size contract (#1892).

``score()`` drives a REAL ``EmailTriageAgent`` over the production LLM-assist
classifier path (``make_llm_classifier(agent.chat)`` + ``triage_inbox_impl``),
which makes genuine LLM calls once invoked -- there's no existing offline seam
(no ``agent_factory``-style injection, no fake classifier hook) like
``gaia.eval.benchmark.run_benchmark`` has. Driving it end-to-end from a unit
test would need either a live Lemonade server or a deep mock of the
classifier/chat internals, both too heavy/flaky for this offline slice.

So this is the THINNER test variant the brief allows: it pins that
``score()``'s signature accepts a ``ctx_size`` kwarg (via ``inspect.signature``,
never invoking it) and that ``main()``'s argparse parser exposes a
``--ctx-size`` flag. ``main()`` takes no ``argv`` param today (it reads
``sys.argv`` via ``parser.parse_args()``), so rather than assume a *second*
new seam (``main(argv)``), this drives ``--help`` through ``sys.argv`` itself
-- the only assumed-new-seam pinned here is the ``--ctx-size`` flag existing
on the parser, nothing about ``main``'s call signature. Neither test drives a
real triage run, so neither needs Lemonade or a mocked corpus.
"""

import inspect

import pytest

from tests.fixtures.email import score_baseline


def test_score_accepts_ctx_size_kwarg():
    sig = inspect.signature(score_baseline.score)
    assert "ctx_size" in sig.parameters, (
        "score_baseline.score() must accept a ctx_size kwarg so the recorded "
        "baseline states the ctx window it was measured under (#1892)"
    )


def test_score_baseline_cli_exposes_ctx_size_flag(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["score_baseline.py", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        score_baseline.main()
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--ctx-size" in out, (
        "score_baseline.py's CLI must expose --ctx-size so the baseline "
        "recording command can pin the ctx window under test (#1892)"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
