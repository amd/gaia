# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The plan reaches the canonical stream with its steps and the current step."""

from gaia.ui.sse_translation import CanonicalTranslator


def _plan(current):
    return {
        "type": "plan",
        "steps": ["read_file", "write_file"],
        "current_step": current,
        "items": [
            {"tool": "read_file", "args": {"file_path": "a.txt"}},
            {"tool": "write_file", "description": "Write the memo"},
        ],
    }


def test_plan_status_carries_steps_and_current_step():
    out = CanonicalTranslator(run_id=None, agent_id="gaia").translate(_plan(0))
    assert len(out) == 1
    assert out[0]["type"] == "status"
    assert out[0]["plan"]["current_step"] == 0
    assert out[0]["plan"]["steps"][1]["description"] == "Write the memo"


def test_repeated_plan_still_moves_the_current_step():
    tr = CanonicalTranslator(run_id=None, agent_id="gaia")
    first = tr.translate(_plan(0))
    second = tr.translate(_plan(1))
    assert first[0]["message"].startswith("Plan: ")
    # Same headline, so it is not repeated — but the step that moved is sent.
    assert second == [
        {
            "type": "status",
            "message": "",
            "plan": {"steps": _plan(1)["items"], "current_step": 1},
        }
    ]


def test_plan_without_items_is_the_old_headline_only():
    tr = CanonicalTranslator(run_id=None, agent_id="gaia")
    out = tr.translate({"type": "plan", "steps": ["a", "b"], "current_step": 0})
    assert out == [{"type": "status", "message": "Plan: a → b"}]
