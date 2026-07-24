# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the ``/query`` canonical SSE translation layer (#2016, spec §6).

These assert the translation map is TOTAL — every top-level event type
``sse_handler.py`` emits has an explicit canonical mapping — and that the
``tool_start`` + ``tool_args`` → one ``tool_call`` buffering (spec §6.3) is exact.
Dependency-light: no Lemonade, Gmail, or ``gaia.ui`` needed.
"""

from __future__ import annotations

import pytest
from gaia_agent_email.sse_translation import TERMINAL_TYPES, CanonicalTranslator

RUN_ID = "0f9c2b6e-2c4a-4b1e-9d6a-1e2f3a4b5c6d"


def _tr() -> CanonicalTranslator:
    return CanonicalTranslator(RUN_ID)


def _types(events):
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# The four clean maps (spec §6.1)
# ---------------------------------------------------------------------------


def test_chunk_maps_to_token():
    out = _tr().translate({"type": "chunk", "content": "hello"})
    assert out == [{"type": "token", "delta": "hello"}]


def test_answer_maps_to_final_with_usage():
    out = _tr().translate(
        {
            "type": "answer",
            "content": "Done.",
            "elapsed": 1.2,
            "steps": 3,
            "tools_used": 2,
        }
    )
    assert out[0]["type"] == "final"
    assert out[0]["answer"] == "Done."
    assert out[0]["usage"] == {"steps": 3, "tools_used": 2, "elapsed": 1.2}


def test_answer_is_terminal():
    out = _tr().translate({"type": "answer", "content": "Done."})
    assert out[0]["type"] in TERMINAL_TYPES


def test_permission_request_maps_to_needs_confirmation_without_confirm_url():
    out = _tr().translate(
        {
            "type": "permission_request",
            "tool": "send_now",
            "args": {"to": "a@b.com", "subject": "Hi"},
            "confirm_id": "x",
        }
    )
    assert out[0]["type"] == "needs_confirmation"
    assert out[0]["run_id"] == RUN_ID
    assert out[0]["action"] == "send_now"
    # Human-readable headline, not a raw key=value dump (issue #2404).
    summary = out[0]["summary"]
    assert "a@b.com" in summary
    assert "Send this email" in summary
    assert 'subject "Hi"' in summary
    assert "send_now:" not in summary
    assert "body=" not in summary
    # Stateless stop-and-hand-off (D1): confirm_url is omitted.
    assert "confirm_url" not in out[0]


def test_permission_request_summary_omits_body_and_is_human_readable():
    out = _tr().translate(
        {
            "type": "permission_request",
            "tool": "send_now",
            "args": {
                "to": "rocm-ci@amd.com",
                "subject": "Re: Security Incident SIC-4482",
                "body": "Acknowledged. I will review the security incident.",
            },
        }
    )
    summary = out[0]["summary"]
    assert summary.startswith("Send this email to rocm-ci@amd.com")
    # The verbatim body must never reach the confirmation headline.
    assert "Acknowledged" not in summary
    assert "body=" not in summary


def test_permission_request_summary_for_non_send_action():
    out = _tr().translate(
        {
            "type": "permission_request",
            "tool": "quarantine_phishing_message",
            "args": {"message_id": "abc123"},
        }
    )
    assert out[0]["summary"] == "Quarantine this message as phishing?"


# ---------------------------------------------------------------------------
# tool_start + tool_args → one tool_call (spec §6.3)
# ---------------------------------------------------------------------------


def test_tool_start_then_args_merge_to_one_tool_call():
    t = _tr()
    assert t.translate({"type": "tool_start", "tool": "triage_inbox"}) == []
    out = t.translate(
        {"type": "tool_args", "tool": "triage_inbox", "args": {"max_messages": 10}}
    )
    assert out == [
        {"type": "tool_call", "tool": "triage_inbox", "args": {"max_messages": 10}}
    ]


def test_argless_tool_flushes_on_next_event():
    t = _tr()
    assert t.translate({"type": "tool_start", "tool": "list_labels"}) == []
    # A non-tool_args event flushes the buffered tool_call (args {}) first.
    out = t.translate({"type": "tool_result", "title": "Result", "summary": "ok"})
    assert _types(out) == ["tool_call", "tool_result"]
    assert out[0] == {"type": "tool_call", "tool": "list_labels", "args": {}}
    assert out[1]["tool"] == "list_labels"


def test_flush_releases_trailing_tool_start():
    t = _tr()
    t.translate({"type": "tool_start", "tool": "list_labels"})
    out = t.flush()
    assert out == [{"type": "tool_call", "tool": "list_labels", "args": {}}]


# ---------------------------------------------------------------------------
# The remaining source events (spec §6.2)
# ---------------------------------------------------------------------------


def test_status_keeps_message_drops_subfields():
    out = _tr().translate(
        {
            "type": "status",
            "status": "working",
            "message": "Processing...",
            "steps": 2,
            "elapsed": 0.5,
        }
    )
    assert out == [{"type": "status", "message": "Processing..."}]


def test_step_folds_to_status():
    out = _tr().translate({"type": "step", "step": 3, "total": 20})
    assert out == [{"type": "status", "message": "Step 3/20"}]


def test_thinking_folds_to_status_not_token():
    out = _tr().translate({"type": "thinking", "content": "let me think"})
    assert out == [{"type": "status", "message": "let me think"}]


def test_plan_folds_to_status():
    out = _tr().translate({"type": "plan", "steps": ["a", "b"]})
    assert out == [{"type": "status", "message": "Plan: a → b"}]


def test_tool_result_carries_tool_and_data():
    t = _tr()
    t.translate({"type": "tool_start", "tool": "search_messages"})
    t.translate({"type": "tool_args", "tool": "search_messages", "args": {"q": "x"}})
    out = t.translate(
        {
            "type": "tool_result",
            "title": "Result",
            "result_data": {"type": "search_results", "count": 2},
        }
    )
    assert out == [
        {
            "type": "tool_result",
            "tool": "search_messages",
            "data": {"type": "search_results", "count": 2},
        }
    ]


def test_tool_result_render_key_for_pre_scan():
    t = _tr()
    t.translate({"type": "tool_start", "tool": "pre_scan_inbox"})
    t.translate({"type": "tool_args", "tool": "pre_scan_inbox", "args": {}})
    out = t.translate({"type": "tool_result", "title": "Result", "summary": "scan"})
    assert out[0]["render"] == "email_pre_scan"


def test_tool_end_after_result_is_dropped():
    t = _tr()
    t.translate({"type": "tool_start", "tool": "list_labels"})
    t.translate({"type": "tool_args", "tool": "list_labels", "args": {}})
    t.translate({"type": "tool_result", "title": "Result", "summary": "ok"})
    assert t.translate({"type": "tool_end", "success": True}) == []


def test_tool_end_without_result_synthesizes_tool_result():
    t = _tr()
    t.translate({"type": "tool_start", "tool": "list_labels"})
    t.translate({"type": "tool_args", "tool": "list_labels", "args": {}})
    out = t.translate({"type": "tool_end", "success": True})
    assert out == [{"type": "tool_result", "tool": "list_labels", "data": {}}]


def test_agent_error_maps_to_terminal_error():
    out = _tr().translate({"type": "agent_error", "content": "boom"})
    assert out == [{"type": "error", "detail": "boom", "status": 500}]


def test_policy_alert_maps_to_error_with_tail():
    out = _tr().translate(
        {
            "type": "policy_alert",
            "tool": "send_now",
            "decision": "BLOCK",
            "reason": "blocked",
            "rule_ids": ["r1"],
            "policy_version": "1",
        }
    )
    assert out[0]["type"] == "error"
    assert out[0]["status"] == 403
    assert "send_now" in out[0]["detail"] and "r1" in out[0]["detail"]


def test_user_input_request_maps_to_needs_confirmation_input():
    out = _tr().translate(
        {
            "type": "user_input_request",
            "request_id": "r",
            "message": "Which?",
            "choices": ["a", "b"],
        }
    )
    assert out[0]["type"] == "needs_confirmation"
    assert out[0]["action"] == "input"
    assert "a, b" in out[0]["summary"]


def test_tool_confirm_denied_folds_to_status():
    out = _tr().translate(
        {
            "type": "tool_confirm_denied",
            "tool": "send_now",
            "reason": "unattended",
            "message": "needs approval",
        }
    )
    assert out == [{"type": "status", "message": "needs approval"}]


def test_agent_created_is_dropped():
    assert _tr().translate({"type": "agent_created", "agent_id": "x"}) == []


def test_unknown_source_type_surfaces_not_silently_dropped():
    out = _tr().translate({"type": "brand_new_event"})
    assert out and out[0]["type"] == "status"
    assert "brand_new_event" in out[0]["message"]


@pytest.mark.parametrize(
    "source_type",
    [
        "status",
        "step",
        "thinking",
        "plan",
        "chunk",
        "tool_start",
        "tool_args",
        "tool_result",
        "tool_end",
        "answer",
        "agent_error",
        "policy_alert",
        "permission_request",
        "user_input_request",
        "tool_confirm_denied",
        "agent_created",
    ],
)
def test_every_documented_source_type_is_mapped(source_type):
    """The 16 top-level types sse_handler.py emits are all handled (no crash,
    and each yields only canonical types or nothing)."""
    out = _tr().translate(
        {
            "type": source_type,
            "content": "x",
            "message": "x",
            "tool": "t",
            "args": {},
            "steps": ["s"],
        }
    )
    canonical = {
        "status",
        "token",
        "tool_call",
        "tool_result",
        "needs_confirmation",
        "final",
        "error",
    }
    assert all(e["type"] in canonical for e in out)
