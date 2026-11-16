# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A benchmark that outgrows its ctx window must say so, never report 0% (#2663)."""

import json
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


def _answering_agent(error_history, answer="Sorry."):
    class _Agent:
        def process_query(self, prompt):
            return {
                "status": "success",
                "result": answer,
                "error_count": len(error_history),
                "error_history": list(error_history),
            }

    return _Agent


def test_overflow_returned_as_answer_is_still_reported():
    """The real loop returns an untrimmable overflow as its answer, never raises."""
    from gaia.agents.base.agent import _CONTEXT_STILL_OVERFLOWING_MESSAGE

    history = [
        {"step": 1, "error": LLAMA_OVERFLOW, "type": "llm_context_overflow_trimmed"},
        {"step": 1, "error": LLAMA_OVERFLOW, "type": "llm_error"},
    ]
    results = run_benchmark(
        "m",
        mbox_path="ignored",
        limit=100,
        ctx_size=16384,
        experiments=1,
        agent_factory=_answering_agent(history, _CONTEXT_STILL_OVERFLOWING_MESSAGE),
    )
    row = results[0]
    assert row["status"] == "ERRORED"
    assert row["error"] == LLAMA_OVERFLOW
    assert row["error_kind"] == "context_overflow"
    assert (row["requested_tokens"], row["available_tokens"]) == (19337, 16384)
    assert "--ctx-size" in row["remedy"]


@pytest.mark.parametrize(
    "history",
    [
        [
            {
                "step": 1,
                "error": LLAMA_OVERFLOW,
                "type": "llm_context_overflow_unrecoverable",
            }
        ],
        [{"step": 2, "error": "exceed_context_size", "type": "llm_streaming_error"}],
        [LLAMA_OVERFLOW],
    ],
)
def test_overflow_found_in_any_unrecovered_history_entry(history):
    assert (
        run_benchmark(
            "m",
            mbox_path="ignored",
            experiments=1,
            agent_factory=_answering_agent(history),
        )[0]["error_kind"]
        == "context_overflow"
    )


@pytest.mark.parametrize(
    "history",
    [
        [],
        [{"step": 1, "error": "connection reset by peer", "type": "llm_error"}],
        ["Empty LLM response"],
        # Trimmed and retried successfully: the run measured something.
        [{"step": 1, "error": LLAMA_OVERFLOW, "type": "llm_context_overflow_trimmed"}],
        [{"step": 1, "type": "llm_error"}, {"error": None}, 42],
    ],
)
def test_answer_path_non_overflow_is_not_reported_as_overflow(history):
    row = run_benchmark(
        "m",
        mbox_path="ignored",
        experiments=1,
        agent_factory=_answering_agent(history),
    )[0]
    assert row.get("status") != "ERRORED"
    assert "error_kind" not in row


@pytest.mark.parametrize("result", [None, "text", [], {"error_history": None}])
def test_malformed_results_are_not_overflow(result):
    assert benchmark.context_overflow_error_from_result(result) is None


def test_cli_exits_nonzero_for_answer_path_overflow(monkeypatch, capsys):
    from gaia import cli

    rows = run_benchmark(
        "m",
        mbox_path="ignored",
        limit=100,
        ctx_size=16384,
        experiments=2,
        agent_factory=_answering_agent(
            [{"step": 1, "error": LLAMA_OVERFLOW, "type": "llm_error"}]
        ),
    )
    monkeypatch.setattr(benchmark, "run_benchmark", lambda *a, **k: rows)

    def no_summary(*_a, **_k):
        raise AssertionError("an overflowed run must not be summarized")

    monkeypatch.setattr(benchmark, "summarize_benchmark", no_summary)
    monkeypatch.setattr(sys, "argv", ["gaia", "eval", "benchmark", "--limit", "100"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "2 of 2 experiment(s) hit the context window" in err
    assert "needed 19337 tokens but the window is 16384" in err


def _tool_agent(*tool_contents):
    class _Agent:
        def process_query(self, prompt):
            return {
                "status": "success",
                "result": "Done.",
                "error_history": [],
                "conversation": [{"role": "user", "content": prompt}]
                + [
                    {"role": "tool", "name": "triage_inbox", "content": c}
                    for c in tool_contents
                ],
            }

    return _Agent


TOOL_OVERFLOW = json.dumps(
    {
        "ok": False,
        "error": "LLMTriageError: LLM triage call failed for message 'm1': "
        + LLAMA_OVERFLOW,
    }
)


def test_overflow_inside_a_tool_is_reported():
    """A per-email classifier overflow comes back as a failed tool result."""
    row = run_benchmark(
        "m",
        mbox_path="ignored",
        limit=100,
        ctx_size=16384,
        experiments=1,
        agent_factory=_tool_agent(json.dumps({"ok": True, "data": {}}), TOOL_OVERFLOW),
    )[0]
    assert row["status"] == "ERRORED"
    assert row["error_kind"] == "context_overflow"
    assert (row["requested_tokens"], row["available_tokens"]) == (19337, 16384)


@pytest.mark.parametrize(
    "content",
    [
        # A successful result that merely quotes overflow wording (an email body).
        json.dumps({"ok": True, "data": {"snippet": LLAMA_OVERFLOW}}),
        {"status": "success", "result": LLAMA_OVERFLOW},
        "Triaged 100 emails: " + LLAMA_OVERFLOW,
        json.dumps({"ok": False, "error": "ConnectorsError: token expired"}),
        {"status": "error", "error": "timed out"},
        None,
    ],
)
def test_tool_results_that_are_not_an_overflow(content):
    row = run_benchmark(
        "m",
        mbox_path="ignored",
        experiments=1,
        agent_factory=_tool_agent(content),
    )[0]
    # A plain tool failure stays an ordinary ERRORED row; it is never an overflow.
    assert "error_kind" not in row
    assert "remedy" not in row


def test_overflow_text_from_a_non_tool_message_is_ignored():
    result = {
        "conversation": [
            {"role": "user", "content": {"ok": False, "error": LLAMA_OVERFLOW}},
            {
                "role": "assistant",
                "content": {"status": "error", "error": LLAMA_OVERFLOW},
            },
            "not-a-message",
        ]
    }
    assert benchmark.context_overflow_error_from_result(result) is None


def test_triage_succeeded_summary_overflowed_is_scored():
    """Only the closing summary overflowed: the triage measured real accuracy."""

    class _Agent:
        def process_query(self, prompt):
            return {
                "status": "success",
                "result": "Sorry.",
                "error_history": [
                    {"step": 2, "error": LLAMA_OVERFLOW, "type": "llm_error"}
                ],
                "conversation": [
                    {
                        "role": "tool",
                        "name": "triage_inbox",
                        "content": json.dumps(
                            {
                                "ok": True,
                                "data": {"results": [{"id": "m1", "category": "FYI"}]},
                            }
                        ),
                    }
                ],
            }

    row = run_benchmark(
        "m",
        mbox_path="ignored",
        experiments=1,
        ground_truth={"m1": {"category": "FYI"}},
        agent_factory=_Agent,
    )[0]
    assert row["status"] == "PASS"
    assert "error_kind" not in row
    assert row["quality"]["category_accuracy"] == 1.0


QUOTED_MODEL_OUTPUT = (
    "LLMTriageError: LLM triage returned no JSON object for message 'm1'; got: "
    + repr("The thread got too long to follow. Max length reached for me.")
)


@pytest.mark.parametrize(
    "message",
    [
        QUOTED_MODEL_OUTPUT,
        # repr switches to double quotes when the reply has an apostrophe.
        "LLMTriageError: LLM triage returned no JSON object for message 'm1'; "
        "got: " + repr("It's got too long, sorry"),
        # An overflow envelope the model echoed is still the model's words.
        "LLMTriageError: LLM triage returned malformed JSON for message 'm1': "
        "Expecting value; got: "
        + repr('{"error": {"message": "request exceeds the available context size"}}'),
        # A non-overflow server envelope that quotes the model's prompt text.
        'LemonadeError: {"error": {"message": "invalid grammar", '
        '"prompt": "my inbox got too long"}}',
    ],
)
def test_quoted_model_output_is_not_overflow(message):
    assert context_overflow_detail(message, 16384, 50) is None
    assert (
        benchmark.context_overflow_error_from_result(
            {
                "conversation": [
                    {"role": "tool", "content": {"ok": False, "error": message}}
                ]
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "message",
    [
        # Server envelope followed by trailing text the plain JSON parse rejects.
        'LemonadeError: 400 {"error": {"message": "request (19337 tokens) exceeds '
        'the available context size (16384 tokens)"}} (status 400)',
        "FastFlowLM can't continue: Max length reached!",
        'LemonadeError: {"error": {"message": "proxy failed", "details": '
        '{"response": {"error": {"message": "Max length reached!"}}}}}',
    ],
)
def test_server_overflow_message_still_classified(message):
    assert context_overflow_detail(message, 16384, 50)["error_kind"] == (
        "context_overflow"
    )
