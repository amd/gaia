# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A delegate_task row shows each helper and the plain summary, never the model's advice.

The tool's result carries advice and an error line written for the model,
token counts and clipped-answer handles. The row used to read "4 helpers", or
"error: <summary> <advice>" when every helper failed, and the helper list never
reached the browser. Every path the result takes to a screen (in-process, the
/query translator, the relay, saved steps) goes through ``helpers_view``.
"""

from __future__ import annotations

import pytest

from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.helper_results import MAX_SHOWN_HELPERS, helpers_view
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator
from gaia.ui.utils import message_to_response

from .test_empty_answer_tail import _run, runtime  # noqa: F401  (fixture)

ADVICE = (
    "A FAILED helper returned nothing usable: do that work yourself or tell the "
    "user it is missing."
)
VIEW_HELPER_KEYS = {"id", "task", "status", "reason", "conclusion", "steps", "wall_s"}


def _helper(number, status, **over):
    record = {
        "id": number,
        "task": f"Read notes_0{number}.md and list each decision",
        "status": status,
        "conclusion": f"Decision {number}: keep the Friday review.",
        "steps": 2,
        "tool_calls": 1,
        "input_tokens": 900,
        "cached_tokens": 10,
        "output_tokens": 80,
        "wall_s": 3.1,
    }
    record.update(over)
    return {k: v for k, v in record.items() if v is not None}


def _result(helpers, status, summary, **over):
    out = {
        "status": status,
        "summary": summary,
        "helpers": helpers,
        "width": 1,
        "width_source": "local_serial",
        "usage": {"input_tokens": 3600, "cached_tokens": 40, "output_tokens": 320},
    }
    out.update(over)
    return out


MIXED = _result(
    [
        _helper(1, "ok"),
        _helper(
            2,
            "partial",
            reason="step_limit",
            detail="clipped; read_tool_output artifact=a1 returns all of it",
        ),
        _helper(
            3, "failed", reason="timeout", conclusion=None, error="No answer in 600 s."
        ),
        _helper(4, "ok"),
    ],
    "partial",
    "2 of 4 helpers finished, 1 stopped short, 1 failed.",
    advice=ADVICE,
    width_note="Ran one helper at a time: local requests are served one at a time.",
)
ALL_FAILED = _result(
    [
        _helper(1, "failed", reason="error", conclusion=None, error="ConnectionError"),
        _helper(2, "failed", reason="error", conclusion=None, error="ConnectionError"),
    ],
    "error",
    "0 of 2 helpers finished, 2 failed.",
    advice=ADVICE,
    error=f"0 of 2 helpers finished, 2 failed. {ADVICE}",
)
ALL_OK = _result(
    [_helper(i, "ok") for i in (1, 2, 3, 4)], "success", "4 of 4 helpers finished."
)


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _in_process(result, tool="delegate_task"):
    handler = SSEOutputHandler()
    handler.print_tool_usage(tool)
    _drain(handler)
    handler.pretty_print_json(result, title="Result")
    (event,) = _drain(handler)
    return event


def _assert_browser_safe(view):
    text = repr(view)
    for leaked in (ADVICE, "usage", "input_tokens", "artifact=", "ConnectionError"):
        assert leaked not in text
    assert "error" not in view
    assert "advice" not in view
    for item in view["helpers"]:
        assert set(item) <= VIEW_HELPER_KEYS


# --- the view ----------------------------------------------------------------


def test_the_view_keeps_what_a_person_reads_and_drops_the_rest():
    view = helpers_view(MIXED)

    assert view["type"] == "helpers"
    assert view["status"] == "partial"
    assert view["success"] is True
    assert view["summary"] == MIXED["summary"]
    assert view["width"] == 1
    assert view["width_note"] == MIXED["width_note"]
    assert [h["id"] for h in view["helpers"]] == [1, 2, 3, 4]
    assert view["helpers"][1] == {
        "id": 2,
        "task": "Read notes_02.md and list each decision",
        "status": "partial",
        "reason": "step_limit",
        "conclusion": "Decision 2: keep the Friday review.",
        "steps": 2,
        "wall_s": 3.1,
    }
    _assert_browser_safe(view)


def test_an_all_failed_run_is_not_a_success():
    view = helpers_view(ALL_FAILED)

    assert view["status"] == "error"
    assert view["success"] is False
    _assert_browser_safe(view)


@pytest.mark.parametrize(
    "data",
    [
        None,
        "4 helpers",
        {"status": "error", "error": "Nothing ran. tasks is empty.", "executed": False},
        {"status": "success", "summary": "x", "helpers": "four"},
        {"status": "done", "summary": "x", "helpers": []},
        {"status": "success", "helpers": []},
    ],
)
def test_anything_but_a_run_of_helpers_has_no_view(data):
    assert helpers_view(data) is None


def test_malformed_entries_are_dropped_and_the_list_is_capped():
    many = [_helper(i, "ok") for i in range(1, MAX_SHOWN_HELPERS + 4)]
    data = _result(
        many + [{"id": "x", "status": "ok"}, {"id": 99, "status": "maybe"}, None],
        "success",
        "19 of 19 helpers finished.",
    )
    view = helpers_view(data)

    assert len(view["helpers"]) == MAX_SHOWN_HELPERS
    assert view["hidden"] == 3


# --- in process --------------------------------------------------------------


def test_the_in_process_row_reads_the_summary_and_carries_the_helpers():
    event = _in_process(MIXED)

    assert event["summary"] == MIXED["summary"]
    assert event["success"] is True
    assert event["status"] == "partial"
    assert event["result_data"] == helpers_view(MIXED)
    _assert_browser_safe(event["result_data"])


def test_an_all_failed_run_says_the_summary_never_the_advice():
    event = _in_process(ALL_FAILED)

    assert event["summary"] == ALL_FAILED["summary"]
    assert ADVICE not in event["summary"]
    assert event["success"] is False
    assert event["status"] == "error"


def test_a_refusal_before_any_helper_ran_keeps_its_own_line():
    event = _in_process(
        {
            "status": "error",
            "error": 'Nothing ran. "tasks" is empty.',
            "executed": False,
        }
    )

    assert event["success"] is False
    assert "Nothing ran" in event["summary"]
    assert "result_data" not in event


def test_another_tool_with_a_helpers_key_is_left_alone():
    event = _in_process(ALL_OK, tool="read_file")

    assert "result_data" not in event
    assert event["summary"] != ALL_OK["summary"]


# --- the /query translator ---------------------------------------------------


def test_the_canonical_result_carries_the_view_with_the_tuis_keys():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    translator.translate(
        {"type": "tool_start", "tool": "delegate_task", "detail": "Handing work"}
    )
    translator.translate({"type": "tool_args", "tool": "delegate_task", "args": {}})
    [out] = translator.translate(_in_process(MIXED))

    assert out["tool"] == "delegate_task"
    assert out["data"] == helpers_view(MIXED)
    assert out["data"]["helpers"][2]["reason"] == "timeout"
    assert MIXED["summary"] in out["preview"]
    _assert_browser_safe(out["data"])


def test_other_tools_still_forward_only_the_summary_keys():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    translator.translate({"type": "tool_start", "tool": "read_file"})
    translator.translate({"type": "tool_args", "tool": "read_file", "args": {}})
    [out] = translator.translate(
        _in_process({"status": "success", "content": "x"}, "read_file")
    )

    assert "helpers" not in out["data"]
    assert set(out["data"]) <= {"summary", "success", "status", "latency_ms"}


# --- the relay ---------------------------------------------------------------


def _relayed(data, tool="delegate_task"):
    handler = SSEOutputHandler()
    relay._dispatch_one(
        handler, {"type": "tool_result", "tool": tool, "data": data}, GAIA_PROFILE
    )
    return [e for e in _drain(handler) if e["type"] == "tool_result"][0]


@pytest.mark.parametrize(
    "result, success", [(MIXED, True), (ALL_OK, True), (ALL_FAILED, False)]
)
def test_a_relayed_cpp_result_reaches_the_browser_without_the_models_text(
    result, success
):
    out = _relayed(result)

    assert out["summary"] == result["summary"]
    assert out["success"] is success
    assert out["data"] == helpers_view(result)
    _assert_browser_safe(out["data"])


def test_a_relayed_python_sidecar_result_keeps_its_view():
    translator = CanonicalTranslator(run_id="run-1", agent_id="gaia")
    translator.translate({"type": "tool_start", "tool": "delegate_task"})
    translator.translate({"type": "tool_args", "tool": "delegate_task", "args": {}})
    [canonical] = translator.translate(_in_process(MIXED))

    out = _relayed(canonical["data"])

    assert out["summary"] == MIXED["summary"]
    assert out["data"] == helpers_view(MIXED)


def test_the_relay_names_the_call():
    assert GAIA_PROFILE.tool_labels["delegate_task"] == "Handing work to helpers"


# --- saved with the turn -----------------------------------------------------


_START = {"type": "tool_start", "tool": "delegate_task", "detail": "Handing work"}


@pytest.mark.asyncio
async def test_the_helpers_are_saved_with_the_turn_and_come_back(runtime):  # noqa: F811
    sid, _events = await _run(runtime, [_START, _in_process(MIXED)])

    [step] = runtime.get_messages(sid)[-1]["agent_steps"]
    assert step["helpers"] == helpers_view(MIXED)
    assert step["result"] == MIXED["summary"]

    reopened = message_to_response(runtime.get_messages(sid)[-1]).agent_steps
    assert reopened[0].helpers.summary == MIXED["summary"]
    assert [h.id for h in reopened[0].helpers.helpers] == [1, 2, 3, 4]
    assert reopened[0].helpers.helpers[2].reason == "timeout"


@pytest.mark.asyncio
async def test_relayed_helpers_are_saved_too(runtime):  # noqa: F811
    relayed = _relayed(MIXED)
    sid, _events = await _run(runtime, [_START, relayed])

    [step] = runtime.get_messages(sid)[-1]["agent_steps"]
    assert step["helpers"] == helpers_view(MIXED)
