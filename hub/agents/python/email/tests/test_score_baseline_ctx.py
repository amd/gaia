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

Two thinner interface tests pin the surface: ``score()``'s signature accepts a
``ctx_size`` kwarg (via ``inspect.signature``, never invoking it) and
``main()``'s argparse parser exposes a ``--ctx-size`` flag. ``main()`` takes no
``argv`` param today (it reads ``sys.argv`` via ``parser.parse_args()``), so
rather than assume a *second* new seam (``main(argv)``), the flag test drives
``--help`` through ``sys.argv`` itself -- the only assumed-new-seam there is
the ``--ctx-size`` flag existing on the parser.

The third (stronger) test pins the actual BEHAVIOR #1892 needs: ``score()``'s
returned dict must carry ``ctx_size`` set to the READBACK value the constructed
agent reports -- not merely the value the caller requested. This is achievable
offline without a live agent by monkeypatching the module-level
``EmailTriageAgent`` (imported as ``from gaia_agent_email.agent import
EmailTriageAgent`` -> the module symbol ``score_baseline.EmailTriageAgent``)
with a stub whose ``.chat.llm_client._backend.ctx_size_override`` reports a
known readback (the exact attribute path the peer ctx-wiring test
``test_ctx_size_config_wiring.py`` established -- referenced here for the stub,
not re-tested), plus stubbing ``make_llm_classifier`` / ``triage_inbox_impl``
so no real LLM call fires. The stub reports a readback (16384) that DIFFERS
from the requested value passed in (4096), so the assertion genuinely
distinguishes readback-stamping from a naive echo of the request.
"""

import inspect
import json

import pytest

from tests.fixtures.email import score_baseline

_FIXTURES = score_baseline.FIXTURES_DIR
_GROUND_TRUTH = score_baseline.GROUND_TRUTH


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


class _StubBackend:
    """Minimal Lemonade backend that reports a readback ctx override."""

    def __init__(self, ctx_size_override):
        self.ctx_size_override = ctx_size_override


class _StubLLMClient:
    def __init__(self, ctx_size_override):
        self._backend = _StubBackend(ctx_size_override)


class _StubChat:
    def __init__(self, ctx_size_override):
        self.llm_client = _StubLLMClient(ctx_size_override)


class _StubAgent:
    """Stand-in for EmailTriageAgent: no Lemonade, reports a readback ctx.

    Mirrors the real object graph the peer ctx-wiring test pins:
    ``agent.chat.llm_client._backend.ctx_size_override``. The readback value is
    fixed here (16384) so a test can request a DIFFERENT ctx and prove score()
    stamps the readback, not the request.
    """

    _READBACK_CTX = 16384

    def __init__(self, config=None):
        self.chat = _StubChat(self._READBACK_CTX)


def test_score_baseline_stamps_readback_ctx_in_result(monkeypatch):
    """score()'s returned dict must carry the READBACK ctx_size (16384) even
    when a different ctx (4096) is requested -- proving it reads the value back
    off the constructed agent, not echoes the argument.

    RED today: score() has no ctx_size param and never stamps ctx_size into its
    result, so this fails before the implementation lands.
    """
    labels = {
        k: v
        for k, v in json.loads(_GROUND_TRUTH.read_text()).items()
        if not k.startswith("_")
    }
    some_ids = list(labels)[:3]
    assert some_ids, "ground_truth.json must carry at least one labelled id"

    # Canned triage output keyed to real GT ids so score()'s alignment check
    # (total > 0) passes offline -- exact accuracy is irrelevant to this test.
    def _fake_triage(backend, *, max_messages, classifier, **kwargs):
        return {
            "results": [
                {
                    "id": mid,
                    "category": labels[mid]["category"],
                    "is_spam": labels[mid]["is_spam"],
                    "is_phishing": labels[mid]["is_phishing"],
                    "source": "stub",
                }
                for mid in some_ids
            ]
        }

    monkeypatch.setattr(score_baseline, "EmailTriageAgent", _StubAgent)
    monkeypatch.setattr(
        score_baseline, "make_llm_classifier", lambda chat: (lambda **kw: {})
    )
    monkeypatch.setattr(score_baseline, "triage_inbox_impl", _fake_triage)

    # Request 4096 but the stub agent reads back 16384 -> result must carry 16384.
    result = score_baseline.score(model="m", ctx_size=4096, max_messages=5)

    assert result["ctx_size"] == _StubAgent._READBACK_CTX


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
