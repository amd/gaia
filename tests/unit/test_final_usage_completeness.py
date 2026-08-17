# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The terminal event must carry every measurement the turn actually produced.

``SSEOutputHandler.print_answer`` measures a turn and deliberately omits any
figure it does not really have — a token count of zero or a non-finite ttft is
left out rather than faked. ``CanonicalTranslator`` then forwards the answer to
the stdio transport, and it forwarded only three of the five fields.

The visible cost was in the TUI's dev footer: it renders tokens/sec from
``usage.tokens``, so with the count dropped the line read
``19.7s · ttft 17.4s · 2 steps · 1 tools`` and tokens/sec was simply absent —
looking like an unimplemented feature rather than a lost field. The email
agent's own copy of this translator already mapped ttft, so the two transports
disagreed about what a finished turn reports.
"""

from __future__ import annotations

import pytest

from gaia.ui.sse_translation import CanonicalTranslator

#: What print_answer emits for a turn where every measurement was available.
FULL_ANSWER = {
    "type": "answer",
    "content": "391",
    "elapsed": 19.7,
    "steps": 2,
    "tools_used": 1,
    "tokens": 148,
    "ttft": 17.4,
}


def translate(event: dict) -> dict:
    finals = [
        out
        for out in CanonicalTranslator(
            run_id=None, agent_id="gaia", debug=False
        ).translate(event)
        if out.get("type") == "final"
    ]
    assert len(finals) == 1, f"expected exactly one final event, got {finals}"
    return finals[0]


class TestEveryMeasurementSurvives:
    @pytest.mark.parametrize(
        "field,expected",
        [
            ("steps", 2),
            ("tools_used", 1),
            ("elapsed", 19.7),
            ("tokens", 148),
            ("ttft", 17.4),
        ],
    )
    def test_field_reaches_the_wire(self, field, expected):
        assert translate(dict(FULL_ANSWER))["usage"][field] == expected

    def test_the_answer_text_is_unchanged(self):
        assert translate(dict(FULL_ANSWER))["answer"] == "391"

    def test_no_measurement_is_silently_dropped(self):
        """A field added upstream must be forwarded, not ignored by omission."""
        measured = set(FULL_ANSWER) - {"type", "content"}
        assert measured <= set(translate(dict(FULL_ANSWER))["usage"])


class TestAbsentMeasurementsStayAbsent:
    """Upstream omits what it cannot measure; the translator must not invent it."""

    def test_a_turn_with_no_token_count_reports_none(self):
        event = {k: v for k, v in FULL_ANSWER.items() if k != "tokens"}
        assert "tokens" not in translate(event)["usage"]

    def test_a_turn_with_no_ttft_reports_none(self):
        event = {k: v for k, v in FULL_ANSWER.items() if k != "ttft"}
        assert "ttft" not in translate(event)["usage"]

    def test_a_bare_answer_carries_no_usage_at_all(self):
        final = translate({"type": "answer", "content": "hi"})
        assert "usage" not in final
