# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The SDK and the real engine under tools, hosts and providers that misbehave.

Whatever a tool returns, a callback raises or a provider does, a turn comes
back as a ``Turn`` or a named error, the next turn gets its own answer, and
nothing waits past the turn's deadline.
"""

import os
import random
import threading
import time

import pytest

from gaia_sdk import Agent, AgentBusy, Budget, EngineError, tool

# Scripts per fuzz test. GAIA_FUZZ_SEEDS=300 is a deeper local sweep.
FUZZ_SEEDS = int(os.environ.get("GAIA_FUZZ_SEEDS", "25"))


def _cycle():
    loop = {}
    loop["self"] = loop
    return loop


def _deep(levels):
    top = inner = {}
    for _ in range(levels):
        inner["d"] = {}
        inner = inner["d"]
    return top


ODD_RESULTS = {
    "object": object,
    "bytes": lambda: b"\x00\xff",
    "set": lambda: {1, 2},
    "cycle": _cycle,
    "nan": lambda: float("nan"),
    "infinity": lambda: {"total": float("inf")},
    "tuple_key": lambda: {("a", 1): 2},
    "deep": lambda: _deep(3000),
}
FINE_RESULTS = {
    "none": lambda: None,
    "text": lambda: "plain text",
    "number": lambda: 7,
    "huge": lambda: {"rows": ["x" * 1000] * 2000},
    "unicode": lambda: {"note": "‮\u0000 \U0001f600"},
}


def _agent(real_engine, model, gate=None, **kwargs):
    @tool
    def lookup(kind: str) -> dict:
        """Look something up in a system that answers oddly."""
        if gate is not None:
            gate()
        if kind == "raise":
            raise RuntimeError("archive offline")
        return {**ODD_RESULTS, **FINE_RESULTS}[kind]()

    return Agent(
        model="host-model",
        base_url=model.base_url,
        system_prompt="You answer questions about orders.",
        tools=[lookup],
        engine=real_engine,
        **kwargs,
    )


@pytest.mark.parametrize("kind", sorted(ODD_RESULTS))
def test_tool_result_json_cannot_hold_is_a_tool_error_the_model_is_told_of(
    real_engine, model, kind
):
    model.call("lookup", kind=kind)
    model.say("I could not read that.")
    model.say("Second answer.")

    with _agent(real_engine, model) as agent:
        turn = agent.run("look it up")
        after = agent.run("and again")

    assert turn.ok and turn.text == "I could not read that."
    assert turn.tool_calls[0]["failed"] is True
    assert (
        "cannot be sent to the model as JSON" in turn.tool_calls[0]["result"]["error"]
    )
    assert "cannot be sent to the model as JSON" in str(model.requests[1]["messages"])
    assert after.text == "Second answer.", "the engine was left mid-turn"


@pytest.mark.parametrize("kind", sorted(FINE_RESULTS))
def test_tool_result_of_any_json_shape_reaches_the_model(real_engine, model, kind):
    model.call("lookup", kind=kind)
    model.say("Done.")

    with _agent(real_engine, model) as agent:
        turn = agent.run("look it up")

    assert turn.ok and turn.text == "Done."
    assert turn.tool_calls[0]["failed"] is False


def test_callback_that_raises_does_not_hand_its_answer_to_the_next_turn(
    real_engine, model
):
    model.say("First answer.")
    model.say("Second answer.")
    seen = []

    def report(call):
        seen.append(call)
        if len(seen) == 1:
            raise RuntimeError("metrics sink down")

    with _agent(real_engine, model, on_model_call=report) as agent:
        with pytest.raises(RuntimeError, match="metrics sink down"):
            agent.run("one")
        second = agent.run("two")

    assert second.text == "Second answer."
    assert len(model.requests) == 2


def test_second_thread_on_a_busy_agent_is_told_so(real_engine, model):
    in_tool = threading.Event()
    go_on = threading.Event()

    def gate():
        in_tool.set()
        assert go_on.wait(timeout=30)

    model.call("lookup", kind="text")
    model.say("First answer.")
    model.say("Second answer.")
    turns = []

    with _agent(real_engine, model, gate=gate) as agent:
        worker = threading.Thread(target=lambda: turns.append(agent.run("one")))
        worker.start()
        assert in_tool.wait(timeout=30)
        with pytest.raises(AgentBusy, match="Give each thread its own Agent"):
            agent.run("two")
        go_on.set()
        worker.join(timeout=30)
        after = agent.run("two")

    assert turns[0].text == "First answer."
    assert after.text == "Second answer."


def test_closed_agent_says_so(real_engine, model):
    agent = _agent(real_engine, model)
    agent.close()

    with pytest.raises(EngineError, match="This agent was closed"):
        agent.run("hi")


def test_message_that_is_not_a_dict_is_named_and_the_agent_still_runs(
    real_engine, model
):
    model.say("Hello.")

    with _agent(real_engine, model) as agent:
        with pytest.raises(TypeError, match="Each message must be a dict"):
            agent.run(["hi"])
        turn = agent.run("hi")

    assert turn.text == "Hello."


def test_turn_the_engine_refuses_leaves_the_agent_usable(real_engine, model):
    model.say("Hello.")

    with _agent(real_engine, model) as agent:
        with pytest.raises(EngineError) as refused:
            agent.run([{"role": "assistant", "content": "I spoke last"}])
        turn = agent.run("hi")

    assert refused.value.code == "bad_turn"
    assert turn.text == "Hello."


def test_request_still_in_flight_at_the_deadline_is_cut_off(real_engine, model):
    model.say("far too late")
    model.delay = 15
    model.say("On time.")

    with _agent(real_engine, model) as agent:
        started = time.monotonic()
        turn = agent.run("hi", budget=Budget(seconds=1))
        waited = time.monotonic() - started
        after = agent.run("hi")

    assert (turn.status, turn.stop_reason) == ("incomplete", "deadline")
    assert turn.text == ""
    assert waited < 8, f"waited {waited:.1f}s for a 1s deadline"
    assert after.text == "On time."


def test_many_agents_on_many_threads_each_get_their_own_answers(real_engine, model):
    # One model server per agent: a shared reply queue would hand out answers
    # in arrival order and prove nothing about the agents.
    from conftest import ModelServer

    count, turns_each = 12, 3
    outcomes = [None] * count
    start = threading.Barrier(count)

    def run(index):
        server = ModelServer()
        try:
            for turn_number in range(turns_each):
                server.call("lookup", kind="text")
                server.say(f"agent {index} turn {turn_number}")
            with _agent(real_engine, server) as agent:
                start.wait(timeout=60)
                outcomes[index] = [
                    agent.run(f"question {n}").text for n in range(turns_each)
                ]
        except BaseException as error:  # noqa: BLE001 - reported by the assert
            outcomes[index] = error
        finally:
            server.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert outcomes == [
        [f"agent {i} turn {n}" for n in range(turns_each)] for i in range(count)
    ]


def test_long_run_of_mixed_turns_keeps_each_answer_with_its_own_turn(
    real_engine, model
):
    with _agent(real_engine, model) as agent:
        for round_number in range(40):
            kind = round_number % 5
            label = f"answer {round_number}"
            if kind == 0:
                model.say(label)
                turn = agent.run("hi")
                assert (turn.ok, turn.text, turn.tool_calls) == (True, label, [])
            elif kind == 1:
                model.call("lookup", kind="text")
                model.say(label)
                turn = agent.run("hi")
                assert turn.text == label
                assert [call["failed"] for call in turn.tool_calls] == [False]
            elif kind == 2:
                model.fail(503)
                turn = agent.run("hi")
                assert (turn.status, turn.stop_reason) == ("failed", "model_call")
                assert turn.text != label
            elif kind == 3:
                model.call("lookup", kind="cycle")
                model.say(label)
                turn = agent.run("hi")
                assert turn.text == label
                assert turn.tool_calls[0]["failed"] is True
            else:
                model.call("lookup", kind="text")
                turn = agent.run("hi", budget=Budget(model_calls=1))
                assert (turn.status, turn.stop_reason) == ("incomplete", "model_calls")
            assert model.replies == [], f"round {round_number} left a reply unread"
            if kind != 4:
                assert turn.usage["model_calls"] == len(turn.model_calls)


def _bad_reply(rng, model):
    kind = rng.choice(
        ["say", "call", "raise", "odd", "unknown", "bad_args", "fail", "junk", "empty"]
    )
    if kind == "say":
        model.say(rng.choice(["Done.", "ok", "I will check that now."]))
    elif kind == "call":
        model.call("lookup", kind=rng.choice(sorted(FINE_RESULTS)))
    elif kind == "raise":
        model.call("lookup", kind="raise")
    elif kind == "odd":
        model.call("lookup", kind=rng.choice(sorted(ODD_RESULTS)))
    elif kind == "unknown":
        model.call("delete_everything", confirm=True)
    elif kind == "bad_args":
        model.call("lookup", wrong=[1, {"a": None}])
    elif kind == "fail":
        model.fail(rng.choice([429, 500, 503]))
    elif kind == "empty":
        model.say("")
    else:
        model.replies.append(
            (
                200,
                rng.choice(
                    [
                        {},
                        {"choices": []},
                        {"choices": [{"message": None}]},
                        {"choices": [{"message": {"content": 5}}]},
                        {"choices": [{"message": {"tool_calls": "x"}}]},
                        {"choices": [{"message": {"tool_calls": [{"id": "c"}]}}]},
                        "not an object",
                    ]
                ),
            )
        )


@pytest.mark.parametrize("seed", range(FUZZ_SEEDS))
def test_no_run_of_bad_replies_and_tool_results_breaks_the_agent(
    real_engine, model, seed
):
    rng = random.Random(seed)

    with _agent(real_engine, model, max_steps=8) as agent:
        for _ in range(3):
            model.replies.clear()
            for _ in range(rng.randint(1, 5)):
                _bad_reply(rng, model)
            for _ in range(12):
                model.say("Here is the answer.")
            requests_before = len(model.requests)

            turn = agent.run("Where is my order?")

            assert turn.status in ("success", "incomplete", "failed")
            assert isinstance(turn.text, str) and isinstance(turn.stop_reason, str)
            assert len(model.requests) - requests_before <= 12, "the loop ran away"
            if turn.ok:
                assert turn.text.strip()

        model.replies.clear()
        model.say("Fine again.")
        assert agent.run("hi").text == "Fine again."
