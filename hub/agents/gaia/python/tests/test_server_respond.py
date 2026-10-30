# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``POST /v1/gaia/query/{run_id}/respond`` — the answer body.

Spec §5.1 names the answer ``value``, and the Agent UI, the TUI and the email
agent all send that. This agent only took ``response``, so their answers 422'd
and the run sat on the question until it timed out. ``response`` stays
accepted for callers that followed this agent's older docs.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.server import QueryRespondRequest  # noqa: E402
from pydantic import ValidationError  # noqa: E402


@pytest.mark.parametrize("field", ["value", "response"])
def test_either_spelling_carries_the_answer(field):
    assert QueryRespondRequest(request_id="q-1", **{field: "blue"}).answer == "blue"


def test_an_empty_answer_is_still_an_answer():
    assert QueryRespondRequest(request_id="q-1", value="").answer == ""


@pytest.mark.parametrize(
    "body",
    [
        {"request_id": "q-1"},
        {"request_id": "q-1", "value": "blue", "response": "red"},
        {"request_id": "q-1", "answer": "blue"},
    ],
)
def test_no_answer_two_answers_or_an_unknown_field_is_rejected(body):
    with pytest.raises(ValidationError):
        QueryRespondRequest(**body)
