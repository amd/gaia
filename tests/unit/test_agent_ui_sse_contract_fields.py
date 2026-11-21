# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard the published Agent UI SSE contract against known wire drift."""

from __future__ import annotations

import json
from pathlib import Path

from gaia.ui.sse_translation import CanonicalTranslator

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "hub" / "agents" / "gaia" / "python" / "openapi.gaia.json"
CONTRACT = ROOT / "docs" / "spec" / "agent-ui-query-sse-contract.md"


def test_reasoning_is_a_canonical_event_when_requested():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia", reasoning=True)

    assert translator.translate({"type": "thinking", "content": "plan"}) == [
        {"type": "reasoning", "delta": "plan"}
    ]


def test_a_retraction_crosses_to_the_host():
    """Reasoning a lone ``</think>`` closed streamed as tokens; the host drops them."""
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia", reasoning=True)
    event = {"type": "thinking", "content": "plan", "retracts_answer": True}

    assert translator.translate(event) == [
        {"type": "reasoning", "delta": "plan", "retracts_answer": True}
    ]


def test_a_restarted_answer_crosses_to_the_host_every_time():
    """A reply cut off by a model-server restart: the host drops its tokens."""
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    event = {
        "type": "status",
        "status": "warning",
        "phase": "answer_restart",
        "message": "cut off",
        "retracts_answer": True,
    }
    expected = [
        {
            "type": "status",
            "message": "cut off",
            "retracts_answer": True,
            "level": "warning",
            "phase": "answer_restart",
        }
    ]

    assert translator.translate(event) == expected
    # A second restart takes back the second reply; it is never deduplicated.
    assert translator.translate(event) == expected


def test_openapi_and_human_contract_document_emitted_final_fields():
    description = json.loads(OPENAPI.read_text(encoding="utf-8"))["paths"][
        "/v1/gaia/query"
    ]["post"]["responses"]["200"]["description"]
    contract = CONTRACT.read_text(encoding="utf-8")

    for field in (
        "reasoning {delta, retracts_answer?}",
        "performance?",
        "context?",
        "context_error?",
        "model_messages?",
    ):
        assert field in description, field
    for field in (
        "reasoning",
        "retracts_answer",
        "performance?",
        "context?",
        "context_error?",
        "model_messages?",
    ):
        assert field in contract, field


def test_contract_declares_ten_canonical_types():
    contract = CONTRACT.read_text(encoding="utf-8")
    assert "The ten canonical event types" in contract
    assert '"const": "reasoning"' in contract
    assert '"const": "context_usage"' in contract
    assert '"const": "final"' in contract
