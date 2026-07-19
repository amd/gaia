# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing (red-phase) coverage for the tokens-per-triage usage aggregation
increment (#1891, Increment 1).

Two things land together in this increment:

1. A new pure aggregation leaf, ``gaia_agent_email.tools.usage.aggregate_usage_stats``,
   that normalizes BOTH the Lemonade chat-completion "usage" shape and the
   legacy "/stats" shape into one summed dict -- shared by the REST path's
   ``_aggregate_usage`` (refactored to delegate to it) and the new tool-path
   wiring in ``EmailTriageAgent._triage_all_backends``.
2. ``make_llm_classifier`` gains an additive ``collect_stats`` kwarg that
   threads through to ``classify_email_llm``, whose own stats-collection now
   prefers ``response.usage`` over ``response.stats`` (falling back to
   ``.stats`` for providers that don't expose usage).

Neither exists yet in this worktree -- every test in the first section fails
with an ImportError until ``tools/usage.py`` is added; every ``collect_stats``
test in the second section fails with a TypeError until ``make_llm_classifier``
gains the kwarg.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaia_agent_email")  # noqa: E402


# ---------------------------------------------------------------------------
# aggregate_usage_stats
# ---------------------------------------------------------------------------


def test_aggregate_usage_stats_empty_list_returns_none():
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    assert aggregate_usage_stats([]) is None


def test_aggregate_usage_stats_single_usage_shape():
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "tokens_per_second": 25.0,
        }
    ]
    agg = aggregate_usage_stats(call_stats)
    assert agg == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "tokens_per_second": 25.0,
    }


def test_aggregate_usage_stats_single_legacy_stats_shape():
    """The old ``/stats`` shape (``input_tokens``/``output_tokens``) must
    normalize to the same output keys as the usage shape."""
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [{"input_tokens": 50, "output_tokens": 10, "tokens_per_second": 20.0}]
    agg = aggregate_usage_stats(call_stats)
    assert agg == {
        "prompt_tokens": 50,
        "completion_tokens": 10,
        "total_tokens": 60,
        "tokens_per_second": 20.0,
    }


def test_aggregate_usage_stats_sums_mixed_shapes():
    """One usage-shape call + one legacy-stats-shape call in the SAME list
    must normalize and sum together (mixed shapes, #1891)."""
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "tokens_per_second": 25.0,
        },
        {"input_tokens": 50, "output_tokens": 10, "tokens_per_second": 20.0},
    ]
    agg = aggregate_usage_stats(call_stats)
    assert agg["prompt_tokens"] == 150
    assert agg["completion_tokens"] == 30
    assert agg["total_tokens"] == 180
    # Aggregate decode throughput = total output / total decode time, not a
    # naive per-call average: (20+10) / (20/25 + 10/20) == 30 / 1.3.
    assert agg["tokens_per_second"] == pytest.approx(30 / 1.3)


def test_aggregate_usage_stats_excludes_zero_tps_calls_from_decode_time():
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [
        {
            "prompt_tokens": 30,
            "completion_tokens": 15,
            "total_tokens": 45,
            "tokens_per_second": 0.0,
        },
        {
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "total_tokens": 10,
            "tokens_per_second": 10.0,
        },
    ]
    agg = aggregate_usage_stats(call_stats)
    # Token counts still include the zero-tps call...
    assert agg["prompt_tokens"] == 35
    assert agg["completion_tokens"] == 20
    assert agg["total_tokens"] == 55
    # ...but its output can't inflate the decode-time denominator: only the
    # tps>0 call contributes, so the aggregate equals that call's own tps.
    assert agg["tokens_per_second"] == pytest.approx(10.0)


def test_aggregate_usage_stats_zero_when_no_call_has_usable_tps():
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        {
            "prompt_tokens": 20,
            "completion_tokens": 8,
            "total_tokens": 28,
            "tokens_per_second": 0.0,
        },
    ]
    agg = aggregate_usage_stats(call_stats)
    assert agg["prompt_tokens"] == 30
    assert agg["completion_tokens"] == 13
    assert agg["total_tokens"] == 43
    assert agg["tokens_per_second"] == 0.0


def test_aggregate_usage_stats_skips_malformed_entries_without_raising():
    """A non-dict item (e.g. a stray ``MagicMock()`` or ``None``) mixed in
    with valid entries must be skipped, not crash the aggregation."""
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    call_stats = [
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "tokens_per_second": 5.0,
        },
        MagicMock(),
        None,
        "not-a-dict",
    ]
    agg = aggregate_usage_stats(call_stats)
    assert agg == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "tokens_per_second": 5.0,
    }


def test_aggregate_usage_stats_all_malformed_returns_zero_dict_not_none():
    """A non-empty ``call_stats`` list of ONLY malformed entries must return
    the all-zero aggregation dict -- never ``None``, since ``call_stats``
    itself was non-empty. Only a genuinely EMPTY list returns ``None``
    (#1891 exact-edge-case requirement)."""
    from gaia_agent_email.tools.usage import aggregate_usage_stats

    agg = aggregate_usage_stats([object(), None])
    assert agg == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "tokens_per_second": 0.0,
    }
    assert agg is not None


# ---------------------------------------------------------------------------
# make_llm_classifier(chat, collect_stats=...)
# ---------------------------------------------------------------------------


def _fake_chat_for_classify(*, usage=None, stats=None, category="FYI"):
    """Chat stub returning a valid classify JSON body plus optional
    Lemonade-usage / legacy-stats attributes on the response -- exercises
    ``classify_email_llm``'s collect_stats preference (usage over stats).

    Uses ``types.SimpleNamespace`` so an attribute that isn't passed is
    simply ABSENT (not an auto-vivified Mock) -- required to exercise the
    "no .usage at all" fallback case honestly.
    """

    class _FakeChat:
        def send_messages(self, messages, system_prompt="", **kwargs):
            resp = types.SimpleNamespace()
            resp.text = json.dumps(
                {"category": category, "confidence": 0.9, "reasoning": "t"}
            )
            if usage is not None:
                resp.usage = usage
            if stats is not None:
                resp.stats = stats
            return resp

    return _FakeChat()


def test_make_llm_classifier_accepts_collect_stats_kwarg():
    """``make_llm_classifier(chat, collect_stats=list)`` must not raise a
    TypeError for an unexpected keyword -- this is the additive-kwarg wiring
    Increment 1 adds, and the appended entry must be the usage-shape dict."""
    from gaia_agent_email.tools.llm_triage import make_llm_classifier

    stats: list = []
    usage = {
        "prompt_tokens": 40,
        "completion_tokens": 8,
        "total_tokens": 48,
        "tokens_per_second": 15.0,
    }
    chat = _fake_chat_for_classify(usage=usage)
    classifier = make_llm_classifier(chat, collect_stats=stats)

    result = classifier(subject="S", sender="a@example.com", body="b", message_id="m1")

    assert result["category"] == "FYI"
    assert stats == [usage]


def test_make_llm_classifier_default_collect_stats_is_optional():
    """Omitting ``collect_stats`` must keep working exactly as before -- the
    returned classifier callable's own signature is unchanged."""
    from gaia_agent_email.tools.llm_triage import make_llm_classifier

    chat = _fake_chat_for_classify(category="URGENT")
    classifier = make_llm_classifier(chat)

    result = classifier(subject="S", sender="a@example.com", body="b", message_id="m1")
    assert result["category"] == "URGENT"


def test_make_llm_classifier_appends_one_entry_per_call():
    """N classifier calls -> N entries appended, in call order."""
    from gaia_agent_email.tools.llm_triage import make_llm_classifier

    stats: list = []
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "tokens_per_second": 5.0,
    }
    chat = _fake_chat_for_classify(usage=usage)
    classifier = make_llm_classifier(chat, collect_stats=stats)

    for i in range(3):
        classifier(subject="S", sender="a@example.com", body="b", message_id=f"m{i}")

    assert len(stats) == 3
    assert stats == [usage, usage, usage]


def test_make_llm_classifier_prefers_usage_over_stats_when_both_present():
    """When the response carries BOTH ``.usage`` and ``.stats``, the
    Lemonade-usage shape must win -- the legacy ``.stats`` values must not
    leak into ``collect_stats``."""
    from gaia_agent_email.tools.llm_triage import make_llm_classifier

    usage = {
        "prompt_tokens": 90,
        "completion_tokens": 15,
        "total_tokens": 105,
        "tokens_per_second": 30.0,
    }
    legacy_stats = {"input_tokens": 999, "output_tokens": 999, "tokens_per_second": 1.0}
    chat = _fake_chat_for_classify(usage=usage, stats=legacy_stats)
    stats: list = []
    classifier = make_llm_classifier(chat, collect_stats=stats)

    classifier(subject="S", sender="a@example.com", body="b", message_id="m1")

    assert len(stats) == 1
    assert stats[0] == usage
    assert stats[0] != legacy_stats


def test_make_llm_classifier_falls_back_to_stats_when_no_usage():
    """Backward compat: a provider response that exposes ONLY ``.stats`` (no
    ``.usage`` attribute at all) must still be collected via the fallback."""
    from gaia_agent_email.tools.llm_triage import make_llm_classifier

    legacy_stats = {"input_tokens": 33, "output_tokens": 7, "tokens_per_second": 12.0}
    chat = _fake_chat_for_classify(stats=legacy_stats)
    stats: list = []
    classifier = make_llm_classifier(chat, collect_stats=stats)

    classifier(subject="S", sender="a@example.com", body="b", message_id="m1")

    assert len(stats) == 1
    assert stats[0] == legacy_stats
