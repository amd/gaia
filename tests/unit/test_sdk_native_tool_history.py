"""A native tool call must be replayed to the model as a native tool call.

Found by recording the requests the flagship sent Fireworks through Lemonade:
the model answered with native ``tool_calls``, but the next request carried an
empty assistant turn (the call itself dropped) and the result as a ``user``
message reading ``[Tool result: read_file] ...``. The model could not see which
call it had made or which result answered it.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def sdk():
    from gaia.chat.sdk import AgentConfig, AgentSDK

    with patch("gaia.chat.sdk.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        yield AgentSDK(AgentConfig(model="test-model"))


def _call(call_id, name="read_file", args="{}"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _result(call_id, text, name="read_file"):
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": [{"type": "text", "text": text}],
    }


def assert_valid_tool_history(messages):
    """The rule OpenAI-style servers enforce, or they reject the request.

    Every tool message answers a call in the assistant turn directly before its
    contiguous block of tool messages, and every call in that turn is answered.
    """
    i = 0
    while i < len(messages):
        m = messages[i]
        if m["role"] == "tool":
            raise AssertionError(f"tool message at {i} answers no call: {m}")
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            j = i + 1
            answered = []
            while j < len(messages) and messages[j]["role"] == "tool":
                answered.append(messages[j]["tool_call_id"])
                j += 1
            assert sorted(answered) == sorted(ids), (ids, answered)
            i = j
            continue
        i += 1


def _structure(sdk, history):
    out = sdk._structure_history(history, "SYSTEM")
    assert out[0] == {"role": "system", "content": "SYSTEM"}
    assert_valid_tool_history(out)
    return out[1:]


def test_native_call_and_its_result_are_sent_natively(sdk):
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "what does cli.py do?"},
            {"role": "assistant", "content": "", "tool_calls": [_call("c1")]},
            _result("c1", "def main(): ..."),
        ],
    )
    assert out[1]["role"] == "assistant"
    assert [tc["id"] for tc in out[1]["tool_calls"]] == ["c1"]
    assert out[2] == {
        "role": "tool",
        "content": "def main(): ...",
        "tool_call_id": "c1",
    }
    assert not any("[Tool result:" in str(m.get("content")) for m in out)


def test_the_assistant_turn_keeps_the_call_it_made(sdk):
    """The regression itself: an empty-text native turn used to lose its call."""
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [_call("c1", "run_python")],
            },
            _result("c1", "3 passed", "run_python"),
        ],
    )
    assert out[1]["tool_calls"][0]["function"]["name"] == "run_python"


def test_parallel_calls_keep_their_order_and_pairing(sdk):
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "read three files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("a"), _call("b"), _call("c")],
            },
            _result("a", "A"),
            _result("b", "B"),
            _result("c", "C"),
        ],
    )
    assert [m["tool_call_id"] for m in out[2:5]] == ["a", "b", "c"]
    assert [m["content"] for m in out[2:5]] == ["A", "B", "C"]


def test_a_call_that_never_got_a_result_is_dropped(sdk):
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("a"), _call("b")],
            },
            _result("a", "A"),
        ],
    )
    assert [tc["id"] for tc in out[1]["tool_calls"]] == ["a"]


def test_a_result_with_a_fallback_id_is_sent_as_text(sdk):
    """Some paths mint an id that matches no call; sending it natively 400s."""
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_call("a")]},
            _result("a", "A"),
            _result("stub", "orphan", "search_file"),
        ],
    )
    assert out[-1] == {"role": "user", "content": "[Tool result: search_file] orphan"}


def test_a_text_protocol_call_keeps_text_history(sdk):
    """No native tool_calls on the assistant turn: nothing to pair, keep text."""
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": '{"tool":"remember","tool_args":{}}'},
            _result("abc", '{"status":"ok"}', "remember"),
        ],
    )
    assert out[1] == {
        "role": "assistant",
        "content": '{"tool":"remember","tool_args":{}}',
    }
    assert out[2] == {
        "role": "user",
        "content": '[Tool result: remember] {"status":"ok"}',
    }


def test_a_result_after_an_interruption_is_not_paired(sdk):
    """A message between a call and its result breaks the pairing the server needs."""
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_call("a")]},
            {"role": "user", "content": "[check:verify-after-change] run the tests"},
            _result("a", "A"),
        ],
    )
    assert "tool_calls" not in out[1]
    assert out[-1]["content"].startswith("[Tool result: read_file]")


def test_a_duplicate_result_for_one_call_is_not_sent_twice_natively(sdk):
    out = _structure(
        sdk,
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_call("a")]},
            _result("a", "first"),
            _result("a", "second"),
        ],
    )
    assert [m.get("tool_call_id") for m in out if m["role"] == "tool"] == ["a"]
    assert out[-1]["content"] == "[Tool result: read_file] second"


def test_send_messages_puts_the_native_history_on_the_wire(sdk):
    captured = []
    sdk.llm_client.chat.side_effect = (
        lambda messages, **kw: captured.extend(messages) or "ok"
    )
    sdk.send_messages(
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_call("c1")]},
            _result("c1", "A"),
        ]
    )
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in captured
    )
    assert_valid_tool_history([m for m in captured if m.get("role") != "system"])
