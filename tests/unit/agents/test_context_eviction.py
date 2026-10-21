# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Already-read tool results leave the context sent to the model, never the log.

On by default. Before each model call the prompt is measured; when the next
call plus one more capped result would not fit the usable window, the oldest
eligible results move out in one batch until two would fit. Each is archived
whole, its stub names the call that re-reads it, and the console is told.
"""

# pylint: disable=protected-access

import copy
import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.artifacts import ArtifactStore
from gaia.agents.base.context_eviction import (
    CONTEXT_EVICTION_ENV_VAR,
    DEFAULT_EVICT_HEADROOM_TOKENS,
    DEFAULT_MIN_BATCH_RATIO,
    EVICT_HEADROOM_ENV_VAR,
    EVICT_KEEP_ENV_VAR,
    EVICT_MIN_BATCH_ENV_VAR,
    EVICT_THRESHOLD_ENV_VAR,
    SAFETY_MARGIN_TOKENS,
    ContextEvictor,
    context_eviction_from_env,
    context_eviction_message,
    evict_headroom_from_env,
    evict_keep_from_env,
    evict_min_batch_from_env,
    evict_threshold_from_env,
    overflow_numbers,
    resolve_context_eviction,
    shrink_for_overflow,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "probe_for_eviction_test"
_ANSWER = "Done: every file was read."
_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey"
).split()
_ENV = (
    CONTEXT_EVICTION_ENV_VAR,
    EVICT_THRESHOLD_ENV_VAR,
    EVICT_HEADROOM_ENV_VAR,
    EVICT_KEEP_ENV_VAR,
    EVICT_MIN_BATCH_ENV_VAR,
)


def prose(seed, chars: int) -> str:
    """Deterministic word salad: roughly one token per word, like real text."""
    rnd = random.Random(seed)
    out, size = [], 0
    while size < chars:
        word = rnd.choice(_WORDS)
        out.append(word)
        size += len(word) + 1
    return " ".join(out)[:chars]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.setenv("GAIA_CTX_SIZE", "16384")
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


_RESULT_CHARS = 12000


class _ProbeAgent(Agent):
    """One tool whose result is distinct prose per path."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def probe_for_eviction_test(path: str) -> str:
            """Read a path."""
            return f"{path}: " + prose(path, _RESULT_CHARS)


def _make_agent(model_id=DEFAULT_MODEL_NAME, **kwargs) -> _ProbeAgent:
    # Opt-in: these tests exercise the reducer unless they say otherwise.
    kwargs.setdefault("context_eviction", "on")
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ProbeAgent(
            silent_mode=True, skip_lemonade=True, model_id=model_id, **kwargs
        )


def _measure(kwargs) -> int:
    return ContextEvictor().raw_tokens(
        kwargs["messages"], kwargs.get("system_prompt"), kwargs.get("tools")
    )


def _stub_chat(agent, replies, prompt_tokens=None):
    """Script the model; report the request's real size unless told otherwise."""
    queue = list(replies)
    sent = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **kwargs):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        sent.append(copy.deepcopy(list(kwargs["messages"])))
        measured = prompt_tokens if prompt_tokens is not None else _measure(kwargs)
        stats = {"prompt_tokens": measured, "completion_tokens": 10}
        reply = queue.pop(0)
        if callable(reply):
            reply = reply(kwargs)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply, stats=stats)

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return sent


def _native_call(number: int, name: str = _TOOL, args=None) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            args if args is not None else {"path": f"file{number}.py"}
                        ),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _script(calls: int, start: int = 1):
    return [_native_call(n) for n in range(start, start + calls)] + [_ANSWER]


def _tool_texts(messages):
    out = []
    for m in messages:
        if m.get("role") != "tool":
            continue
        content = m["content"]
        if isinstance(content, str):
            out.append(content)
        else:
            out.append(content[0].get("text"))
    return out


def _full(number: int) -> str:
    return f"file{number}.py: " + prose(f"file{number}.py", _RESULT_CHARS)


def _stats_entries(result):
    return [
        m["content"]
        for m in result["conversation"]
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and m["content"].get("type") == "stats"
    ]


def _handle(stub: str) -> str:
    return stub.split("artifact=")[1].split(",")[0].split(")")[0]


def _read_all(store, handle) -> str:
    page = store.read(handle, entry=1)
    text = page["content"]
    while page.get("next_offset") is not None:
        page = store.read(handle, offset=page["next_offset"], limit=8000)
        text += page["content"]
    return text


# ---------------------------------------------------------------------------
# Modes: on by default, off on request
# ---------------------------------------------------------------------------


def test_modes():
    assert resolve_context_eviction("auto") is True
    assert resolve_context_eviction("on") is True
    assert resolve_context_eviction("off") is False
    with pytest.raises(ValueError, match="context_eviction must be one of"):
        resolve_context_eviction("sometimes")


@pytest.mark.usefixtures("clean_registry")
def test_off_by_default_and_opt_in_for_local_and_cloud_models():
    # Default-on waits on the planted-fact retrieval gate (plan #5263).
    with patch("gaia.agents.base.agent.AgentSDK"):
        default = _ProbeAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    assert default.context_eviction_enabled is False
    assert default.context_state() == {"put_away": None, "summarized": None}
    assert _make_agent().context_eviction_enabled is True
    assert _make_agent("fireworks.deepseek-v4p1-flash").context_eviction_enabled
    assert _make_agent(context_eviction="off")._context_evictor is None


@pytest.mark.usefixtures("clean_registry")
def test_off_sends_every_result_whole():
    agent = _make_agent(context_eviction="off")
    sent = _stub_chat(agent, _script(7))

    result = agent.process_query("read the files")

    assert result["result"].startswith(_ANSWER)
    assert _tool_texts(sent[-1]) == [_full(n) for n in range(1, 8)]
    assert not any("evicted_results" in entry for entry in _stats_entries(result))


# ---------------------------------------------------------------------------
# Threshold math: headroom in the usable window, not a share of it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window", [32768, 65536, 131072])
def test_marks_leave_room_for_the_reply_and_one_more_result(window):
    evictor = ContextEvictor()
    evictor.begin_turn(window, output_reserve=8192)

    usable = window - 8192 - SAFETY_MARGIN_TOKENS
    assert evictor.usable() == usable
    assert evictor.threshold() == usable - DEFAULT_EVICT_HEADROOM_TOKENS
    assert evictor.target() == usable - 2 * DEFAULT_EVICT_HEADROOM_TOKENS
    assert evictor.min_batch() == int(window * DEFAULT_MIN_BATCH_RATIO)


def test_the_gap_does_not_shrink_with_the_window():
    # A percentage gap shrinks on small windows until one result crosses both
    # marks at once; the headroom gap is one capped result everywhere.
    gaps = set()
    for window in (32768, 65536, 131072):
        evictor = ContextEvictor()
        evictor.begin_turn(window, output_reserve=8192)
        gaps.add(evictor.threshold() - evictor.target())
    assert gaps == {DEFAULT_EVICT_HEADROOM_TOKENS}


def test_absolute_values_pin_the_marks():
    evictor = ContextEvictor(threshold_tokens=9000, min_batch_tokens=500)
    evictor.begin_turn(131072)
    assert (evictor.threshold(), evictor.target(), evictor.min_batch()) == (
        9000,
        6000,
        500,
    )


def test_a_call_before_begin_turn_fails_loudly():
    with pytest.raises(RuntimeError, match="begin_turn"):
        ContextEvictor().threshold()
    with pytest.raises(ValueError, match="loaded window"):
        ContextEvictor().begin_turn(0)


# ---------------------------------------------------------------------------
# The policy on its own
# ---------------------------------------------------------------------------


def _tool_msg(name, call_id, text):
    return {
        "role": "tool",
        "name": name,
        "tool_call_id": call_id,
        "content": [{"type": "text", "text": text}],
    }


def _assistant_call(call_id, name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


class _Run:
    """A turn driven call by call: append a step, ask the evictor, repeat."""

    def __init__(self, window, history=(), **kwargs):
        self.evictor = ContextEvictor(**kwargs)
        self.evictor.begin_turn(window)
        self.store = ArtifactStore()
        self.messages = [*history, {"role": "user", "content": "go"}]
        self.events = []
        self.sent = []

    def call(self):
        event = self.evictor.before_call(
            self.messages, self.store, system_prompt="system", tools=[]
        )
        self.sent.append(copy.deepcopy(self.messages))
        if event:
            self.events.append(event)
        return event

    def step(self, n, text, name="read_file"):
        self.messages.append(_assistant_call(f"c{n}", name, {"path": f"f{n}.txt"}))
        self.messages.append(_tool_msg(name, f"c{n}", text))

    def size(self):
        return self.evictor.raw_tokens(self.messages, "system", [])


def _is_stub(text):
    return text.startswith("[Not shown, put away to save context: ")


def test_nothing_goes_under_the_threshold():
    run = _Run(16384)
    run.call()
    for n in range(1, 4):
        run.step(n, prose(n, 4000))
        assert run.call() is None
    assert not any(_is_stub(t) for t in _tool_texts(run.messages))


def test_over_the_threshold_oldest_go_first_until_under_the_target():
    run = _Run(32768)
    run.call()
    event = None
    for n in range(1, 20):
        run.step(n, prose(n, 8000))
        event = run.call()
        if event:
            break
    assert event is not None
    texts = _tool_texts(run.messages)
    evicted = [_is_stub(t) for t in texts]
    # Oldest first, with no holes, and not everything.
    assert evicted == sorted(evicted, reverse=True)
    assert evicted[0] and not evicted[-1]
    assert event["prompt_tokens_after"] <= run.evictor.target()
    assert event["prompt_tokens_before"] > run.evictor.threshold()
    assert event["evicted_results"] == sum(evicted)
    assert event["context_window"] == 32768
    assert event["reason"] == "proactive"


def test_latest_step_and_unread_results_never_go():
    # keep_steps=2: a result must have been in two requests before it can go.
    run = _Run(16384, threshold_tokens=100, min_batch_tokens=1)
    run.call()
    run.step(1, prose(1, 8000))
    run.call()  # result 1 read once
    run.step(2, prose(2, 8000))
    assert run.call() is None  # 1 read once, 2 is the latest step
    run.step(3, prose(3, 8000))
    event = run.call()  # 1 read twice: goes; 2 read once; 3 latest
    assert event["evicted_results"] == 1
    assert [_is_stub(t) for t in _tool_texts(run.messages)] == [True, False, False]


def test_history_results_count_as_read():
    history = [
        {"role": "user", "content": "earlier"},
        _assistant_call("h1", "read_file", {"path": "old.txt"}),
        _tool_msg("read_file", "h1", prose("h1", 12000)),
        {"role": "assistant", "content": "earlier answer"},
    ]
    run = _Run(4096, history=history, threshold_tokens=1000, min_batch_tokens=1)
    event = run.call()
    assert event["evicted_results"] == 1
    assert _is_stub(_tool_texts(run.messages)[0])


@pytest.mark.parametrize(
    "name, text",
    [
        (
            "write_file",
            json.dumps({"status": "denied", "error": "Tool 'write_file' was denied."})
            + " " * 4000,
        ),
        (
            "run_shell",
            json.dumps(
                {
                    "status": "denied",
                    "error": "blocked by governance: " + "x" * 4000,
                    "governance_decision": "block",
                }
            ),
        ),
        ("run_shell", json.dumps({"executed": False, "reason": "y" * 4000})),
        ("delegate_task", "worker answer " * 400),
        ("read_file", "tiny"),
    ],
)
def test_refusals_worker_answers_and_tiny_results_never_go(name, text):
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.call()
    run.step(1, text, name=name)
    run.step(2, prose(2, 8000))
    run.call()
    run.step(3, prose(3, 100))
    run.call()
    run.step(4, prose(4, 100))
    run.call()
    texts = _tool_texts(run.messages)
    assert texts[0] == text
    assert _is_stub(texts[1])


def test_user_assistant_and_non_text_messages_are_untouched():
    image = {
        "role": "tool",
        "name": "screenshot",
        "tool_call_id": "img",
        "content": [{"type": "image_url", "image_url": {"url": "data:,x"}}],
    }
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.messages = [
        {"role": "user", "content": "x " * 5000},
        {"role": "assistant", "content": "y " * 5000},
        _assistant_call("img", "screenshot", {}),
        image,
        _assistant_call("c1", "read_file", {"path": "a.py"}),
        _tool_msg("read_file", "c1", prose(1, 8000)),
    ]
    before = copy.deepcopy(run.messages[:5])
    for _ in range(3):
        run.call()
    run.step(9, prose(9, 100))
    run.call()

    assert run.messages[:5] == before
    assert _is_stub(_tool_texts(run.messages)[1])
    assert run.messages[3] is image


def test_a_small_batch_waits_unless_the_window_is_nearly_full():
    # Over the threshold, but the only old result frees less than min_batch.
    run = _Run(16384, threshold_tokens=1000, min_batch_tokens=10_000)
    run.call()
    run.step(1, prose(1, 6000))
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    assert run.call() is None

    # Same small batch, but the next capped result could not fit: it goes.
    urgent = _Run(3072, threshold_tokens=1000, min_batch_tokens=10_000)
    urgent.call()
    urgent.step(1, prose(1, 9000))
    urgent.call()
    urgent.call()
    urgent.step(2, prose(2, 100))
    assert urgent.call()["evicted_results"] == 1


def test_stub_names_the_tool_its_arguments_and_its_outcome():
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    payload = json.dumps(
        {
            "status": "success",
            "message": "Found 12 matches",
            "results": [prose(i, 300) for i in range(12)],
        }
    )
    run.messages += [
        _assistant_call("c1", "search_files", {"query": "quarterly revenue"}),
        _tool_msg("search_files", "c1", payload),
    ]
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    run.call()

    stub = _tool_texts(run.messages)[0]
    handle = _handle(stub)
    assert stub == (
        "[Not shown, put away to save context: search_files query=quarterly "
        "revenue; summary: success, Found 12 matches, 12 results; "
        f"{len(payload)} chars. Call read_tool_output(artifact={handle}, entry=1) "
        "to read it.]"
    )


def test_every_evicted_byte_reads_back():
    run = _Run(16384)
    run.call()
    originals = {}
    for n in range(1, 25):
        originals[n] = prose(n, 9000 + 37 * n)
        run.step(n, originals[n])
        run.call()

    stubs = [(n, t) for n, t in enumerate(_tool_texts(run.messages), 1) if _is_stub(t)]
    assert len(stubs) >= 10
    for n, stub in stubs:
        assert _read_all(run.store, _handle(stub)) == originals[n]


def test_a_reread_page_goes_back_to_its_own_archive():
    store = ArtifactStore()
    original = prose("orig", 20000)
    handle = store.put(original)
    store.set_index(handle, [{"offset": 0, "length": len(original)}])
    page = json.dumps(store.read(handle, offset=8000, limit=8000))
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.store = store
    run.messages += [
        _assistant_call("r1", "read_tool_output", {"artifact": handle}),
        _tool_msg("read_tool_output", "r1", page),
    ]
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    run.call()

    stub = _tool_texts(run.messages)[0]
    assert f"read_tool_output(artifact={handle}, offset=8000, limit=8000)" in stub
    assert len(store._items) == 1  # nothing re-archived


def test_condensed_result_keeps_its_handle_and_index():
    store = ArtifactStore()
    original = "\n".join(f"line {n} " + prose(n, 80) for n in range(200))
    handle = store.put(original)
    store.set_index(handle, [{"offset": 100, "length": 50}])
    condensed = json.dumps(
        {"shown": [{"offset": 0, "text": original[:3000]}], "artifact": handle}
    )
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.store = store
    run.messages += [
        _assistant_call("c1", "read_file", {"path": "a.py"}),
        _tool_msg("read_file", "c1", condensed),
    ]
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    run.call()

    stub = _tool_texts(run.messages)[0]
    assert f"read_tool_output(artifact={handle}, offset=0, limit=8000)" in stub
    assert store.text(handle) == original
    assert store.read(handle, entry=1)["content"] == original[100:150]


def test_a_full_archive_keeps_the_result_whole(caplog):
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.store = ArtifactStore(max_bytes=10)
    run.call()
    run.step(1, prose(1, 8000))
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    with caplog.at_level("WARNING", logger="gaia.agents.base.context_eviction"):
        assert run.call() is None
    assert _tool_texts(run.messages)[0] == prose(1, 8000)
    assert "Cannot put a tool result away" in caplog.text


def test_a_stub_is_never_evicted_twice():
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.call()
    run.step(1, prose(1, 8000))
    for n in range(2, 6):
        run.call()
        run.step(n, prose(n, 100))
    stubs = [t for t in _tool_texts(run.messages) if _is_stub(t)]
    run.call()
    assert [t for t in _tool_texts(run.messages) if _is_stub(t)][: len(stubs)] == stubs
    assert len(run.store._items) == len(run.events)


# ---------------------------------------------------------------------------
# Batching keeps the cached prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("window", [16384, 32768, 65536])
def test_batches_are_rare_and_only_change_the_prompt_from_the_oldest_stub(window):
    run = _Run(window)
    run.call()
    for n in range(1, 61):
        run.step(n, prose(n, 6000))
        run.call()

    calls = len(run.sent)
    batches = len(run.events)
    assert batches > 0
    if window >= 32768:
        # Each batch buys back the gap between the marks: ~1 batch per 4+ steps.
        assert batches <= calls / 4
    else:
        # At 16K, 60 steps of stubs and tool calls alone near the target, so
        # late in the run a batch fires most steps. It still never overflows.
        assert batches < calls

    for prev, cur in zip(run.sent, run.sent[1:]):
        changed = [i for i, (a, b) in enumerate(zip(prev, cur)) if a != b]
        if not changed:
            continue  # the previous request is a prefix of this one
        # The first change is a stub: the prefix before it stays cached.
        first = changed[0]
        assert cur[first]["role"] == "tool"
        assert _is_stub(cur[first]["content"][0]["text"])
    assert max(run.evictor.raw_tokens(s, "system", []) for s in run.sent) < window


# ---------------------------------------------------------------------------
# Measurement: the backend's count, never below the tokenizer's
# ---------------------------------------------------------------------------


def test_the_estimate_follows_the_backend():
    run = _Run(65536)
    run.call()
    raw = run.size()
    assert run.evictor.estimate(run.messages, "system", []) == raw

    run.evictor.note_prompt_tokens({"prompt_tokens": raw + 500})
    run.step(1, prose(1, 4000))
    delta = run.size() - raw
    assert run.evictor.estimate(run.messages, "system", []) == raw + 500 + delta

    # A model whose tokenizer packs text tighter than cl100k (Gemma does): its
    # own count is the size, not the tokenizer's larger one.
    run.call()
    tighter = int(run.size() * 0.7)
    run.evictor.note_prompt_tokens({"prompt_tokens": tighter})
    assert run.evictor.estimate(run.messages, "system", []) == tighter


def test_an_implausibly_small_backend_count_is_not_trusted():
    # E.g. a backend that reports only the uncached part of the prompt.
    run = _Run(65536)
    run.call()
    run.evictor.note_prompt_tokens({"prompt_tokens": 1})
    assert run.evictor.estimate(run.messages, "system", []) == run.size()


def test_measurement_counts_system_prompt_and_tools():
    evictor = ContextEvictor()
    messages = [{"role": "user", "content": "hi"}]
    bare = evictor.raw_tokens(messages, "", [])
    assert evictor.raw_tokens(messages, prose("s", 4000), []) > bare + 500
    tools = [
        {"type": "function", "function": {"name": "t", "description": prose(1, 4000)}}
    ]
    assert evictor.raw_tokens(messages, "", tools) > bare + 500


def test_an_edited_message_is_recounted():
    evictor = ContextEvictor()
    message = {"role": "tool", "content": [{"type": "text", "text": "short"}]}
    small = evictor._count(message)
    message["content"].append({"type": "text", "text": prose(1, 4000)})
    assert evictor._count(message) > small + 500


# ---------------------------------------------------------------------------
# The agent loop: measured before each call, told to the console
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_agent_evicts_before_the_call_and_keeps_everything_readable():
    agent = _make_agent()
    agent.console.print_context_eviction = MagicMock()
    sent = _stub_chat(agent, [*_script(12), _ANSWER])

    result = agent.process_query("read the files")

    assert result["result"].startswith(_ANSWER)
    final = _tool_texts(sent[-1])
    evicted = [_is_stub(t) for t in final]
    assert any(evicted) and evicted == sorted(evicted, reverse=True)
    assert all(
        _measure({"messages": s, "system_prompt": agent.system_prompt}) < 16384
        for s in sent
    )

    reader = agent._tools_registry["read_tool_output"]["function"]
    for number, stub in enumerate(final, start=1):
        if _is_stub(stub):
            assert f"path=file{number}.py" in stub
            page = reader(artifact=_handle(stub), entry=1)
            text = page["content"]
            while page.get("next_offset") is not None:
                page = reader(artifact=_handle(stub), offset=page["next_offset"])
                text += page["content"]
            assert text == _full(number)

    # The log keeps every result whole.
    logged = [m["content"] for m in result["conversation"] if m.get("role") == "tool"]
    assert logged == [_full(n) for n in range(1, 13)]
    assert not any(_is_stub(t) for t in _tool_texts(result["model_messages"]))

    # Every batch was announced, and recorded on its step's stats record.
    events = [c.args[0] for c in agent.console.print_context_eviction.call_args_list]
    fired = [s for s in _stats_entries(result) if "evicted_results" in s]
    assert events and len(events) == len(fired)
    assert sum(e["evicted_results"] for e in events) == sum(evicted)
    assert all("items" not in s for s in fired)
    assert all(e["items"][0]["tool"] == _TOOL for e in events)


@pytest.mark.usefixtures("clean_registry")
def test_eviction_still_fires_in_a_second_turn_on_replayed_history():
    """History replays every result whole (the log keeps the pre-eviction copy);
    those results were read last turn, so they can go at once."""
    agent = _make_agent()
    _stub_chat(agent, _script(10))
    result1 = agent.process_query("read the first batch of files")
    assert any("evicted_results" in s for s in _stats_entries(result1))

    agent.conversation_history = result1["model_messages"]
    sent2 = _stub_chat(agent, _script(3, start=11))
    result2 = agent.process_query("read a second batch of files")

    assert any("evicted_results" in s for s in _stats_entries(result2))
    first_call = _tool_texts(sent2[0])
    assert first_call and all(_is_stub(t) for t in first_call[:5])


def test_console_message_says_what_happened():
    assert context_eviction_message(
        {"reason": "proactive", "evicted_results": 3, "evicted_tokens_est": 12400}
    ) == (
        "Moved 3 older tool results out of the prompt (~12.4K tokens); still readable"
    )
    assert context_eviction_message(
        {"reason": "proactive", "evicted_results": 1, "evicted_tokens_est": 1500}
    ) == ("Moved 1 older tool result out of the prompt (~1.5K tokens); still readable")
    assert "didn't fit" in context_eviction_message(
        {"reason": "overflow", "evicted_results": 1, "evicted_tokens_est": 800}
    )


def test_the_base_console_shows_the_notice_through_print_info():
    from gaia.agents.base.console import SilentConsole

    console = SilentConsole()
    console.print_info = MagicMock()
    console.print_context_eviction({"evicted_results": 2, "evicted_tokens_est": 50})
    assert "Moved 2 older tool results out" in console.print_info.call_args.args[0]


# ---------------------------------------------------------------------------
# The overflow retry: last resort, and it must actually help (#2780)
# ---------------------------------------------------------------------------


def test_overflow_numbers_are_read_from_the_backend_error():
    assert overflow_numbers(
        "request (72438 tokens) exceeds the available context size (65536 tokens)"
    ) == (72438, 65536)
    assert overflow_numbers('{"n_prompt_tokens": 9000, "n_ctx": 8192}') == (9000, 8192)
    assert overflow_numbers("something else broke") is None


def test_shrink_cuts_the_latest_large_result_to_a_preview():
    store = ArtifactStore()
    latest = prose("latest", 40000)
    messages = [
        {"role": "user", "content": "go"},
        _assistant_call("c1", "read_file", {"path": "a.txt"}),
        _tool_msg("read_file", "c1", prose(1, 8000)),
        _assistant_call("c2", "write_file", {"path": "b.txt"}),
        _tool_msg("write_file", "c2", json.dumps({"status": "denied", "error": "no"})),
        _assistant_call("c3", "read_file", {"path": "big.txt"}),
        _tool_msg("read_file", "c3", latest),
    ]
    shrunk, items = shrink_for_overflow(messages, store)
    texts = _tool_texts(shrunk)

    assert _is_stub(texts[0])
    assert texts[1] == json.dumps({"status": "denied", "error": "no"})
    head, preview = texts[2].split("\n", 1)
    assert head.startswith(
        "[shortened to fit the context window: read_file path=big.txt"
    )
    assert preview == latest[:2000]
    assert len(items) == 2
    rest = store.read(_handle(head), offset=2000, limit=len(latest))
    assert latest[:2000] + rest["content"] == latest[: 2000 + len(rest["content"])]
    assert _read_all(store, items[1]["artifact"]) == latest


def _overflow(requested, available):
    return RuntimeError(
        f"request ({requested} tokens) exceeds the available context size "
        f"({available} tokens)"
    )


@pytest.mark.usefixtures("clean_registry")
def test_overflow_retry_runs_when_the_shrink_fits():
    agent = _make_agent(context_eviction="off")
    agent.console.print_context_eviction = MagicMock()

    def overflow_by_200(kwargs):
        return _overflow(_measure(kwargs) + 200, _measure(kwargs))

    # The fourth request overflows by 200 tokens; the shrink frees far more.
    sent = _stub_chat(agent, [*_script(3)[:3], overflow_by_200, _ANSWER])
    with patch.object(Agent, "_is_loaded_ctx_too_small", return_value=False):
        result = agent.process_query("read the files")

    assert result["result"].startswith(_ANSWER)
    retried = _tool_texts(sent[-1])
    assert _is_stub(retried[0]) and _is_stub(retried[1])
    assert retried[2].startswith("[shortened to fit the context window: ")
    trimmed = [
        e for e in agent.error_history if e["type"] == "llm_context_overflow_trimmed"
    ]
    assert trimmed and trimmed[0]["fits"] is True
    assert trimmed[0]["freed_tokens_est"] > 200
    event = agent.console.print_context_eviction.call_args.args[0]
    assert event["reason"] == "overflow" and event["evicted_results"] == 3


@pytest.mark.usefixtures("clean_registry")
def test_overflow_retry_is_skipped_when_it_cannot_fit():
    agent = _make_agent(context_eviction="off")
    sent = _stub_chat(agent, _script(2)[:2] + [_overflow(500_000, 6000)])

    with patch.object(Agent, "_is_loaded_ctx_too_small", return_value=False):
        result = agent.process_query("read the files")

    assert len(sent) == 3  # no retry that would only be refused again
    assert "needs more than fits in the model's context window" in result["result"]
    failed = [
        e
        for e in agent.error_history
        if e["type"] == "llm_context_overflow_unrecoverable"
    ]
    assert failed and failed[0]["fits"] is False
    assert failed[0]["requested_tokens"] == 500_000


# ---------------------------------------------------------------------------
# Env: wins over the arguments, malformed fails loudly
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_env_wins_over_the_constructor(monkeypatch):
    monkeypatch.setenv(CONTEXT_EVICTION_ENV_VAR, "on")
    monkeypatch.setenv(EVICT_THRESHOLD_ENV_VAR, "123")
    monkeypatch.setenv(EVICT_HEADROOM_ENV_VAR, "3000")
    monkeypatch.setenv(EVICT_KEEP_ENV_VAR, "4")
    monkeypatch.setenv(EVICT_MIN_BATCH_ENV_VAR, "56")

    agent = _make_agent(context_eviction="off", context_eviction_keep_steps=9)

    evictor = agent._context_evictor
    assert (
        evictor.threshold_tokens,
        evictor.headroom_tokens,
        evictor.keep_steps,
        evictor.min_batch_tokens,
    ) == (123, 3000, 4, 56)
    monkeypatch.setenv(CONTEXT_EVICTION_ENV_VAR, "OFF")
    assert _make_agent(context_eviction="on")._context_evictor is None


@pytest.mark.parametrize(
    "name, value, reader",
    [
        (CONTEXT_EVICTION_ENV_VAR, "maybe", context_eviction_from_env),
        (EVICT_THRESHOLD_ENV_VAR, "lots", evict_threshold_from_env),
        (EVICT_HEADROOM_ENV_VAR, "half", evict_headroom_from_env),
        (EVICT_KEEP_ENV_VAR, "0", evict_keep_from_env),
        (EVICT_MIN_BATCH_ENV_VAR, "-1", evict_min_batch_from_env),
    ],
)
def test_malformed_env_fails_loudly(monkeypatch, name, value, reader):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        reader()
    with pytest.raises(ValueError, match=name):
        _make_agent()


def test_malformed_arguments_fail_at_construction():
    with pytest.raises(ValueError, match="context_eviction must be one of"):
        _make_agent(context_eviction="sometimes")
    with pytest.raises(ValueError, match="context_eviction_keep_steps"):
        _make_agent(context_eviction_keep_steps=0)
    with pytest.raises(ValueError, match="context_eviction_threshold_tokens"):
        _make_agent(context_eviction_threshold_tokens=0)
    with pytest.raises(ValueError, match="context_eviction_headroom_tokens"):
        _make_agent(context_eviction_headroom_tokens=0)


# ---------------------------------------------------------------------------
# Review fixes: fixed prefix, reused ids, handles across turns, every call path
# ---------------------------------------------------------------------------


def test_a_flagship_sized_prompt_does_not_evict_on_every_call():
    system = prose("system", 50000)  # ~13K tokens of prompt and schemas
    evictor = ContextEvictor()
    evictor.begin_turn(32768)
    store = ArtifactStore()
    messages = [{"role": "user", "content": "go"}]
    batches = 0
    for n in range(1, 21):
        messages.append(_assistant_call(f"c{n}", "read_file", {"path": f"f{n}"}))
        messages.append(_tool_msg("read_file", f"c{n}", prose(n, 6000)))
        if evictor.before_call(messages, store, system_prompt=system, tools=[]):
            batches += 1
        assert evictor.raw_tokens(messages, system, []) < 32768
    assert 0 < batches <= 8


def test_a_reused_call_id_is_a_new_result_the_model_has_not_read():
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.call()
    run.messages += [
        _assistant_call("call_0", "read_file", {"path": "a"}),
        _tool_msg("read_file", "call_0", prose(1, 8000)),
    ]
    run.call()
    run.call()
    # A backend that numbers calls per response reuses call_0.
    run.messages += [
        _assistant_call("call_0", "read_file", {"path": "b"}),
        _tool_msg("read_file", "call_0", prose(2, 8000)),
    ]
    run.call()
    texts = _tool_texts(run.messages)
    assert _is_stub(texts[0])
    assert texts[1] == prose(2, 8000)


def test_the_same_result_keeps_its_handle_and_stub_across_turns():
    store = ArtifactStore()
    stubs = []
    for _ in range(2):
        evictor = ContextEvictor(threshold_tokens=10, min_batch_tokens=1)
        evictor.begin_turn(4096)
        messages = [
            {"role": "user", "content": "go"},
            _assistant_call("c1", "read_file", {"path": "a"}),
            _tool_msg("read_file", "c1", prose(1, 8000)),
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "again"},
        ]
        evictor.before_call(messages, store, system_prompt="s", tools=[])
        stubs.append(_tool_texts(messages)[0])
    assert _is_stub(stubs[0]) and stubs[0] == stubs[1]
    assert len(store._items) == 1


def test_reusing_a_handle_restarts_its_clock(monkeypatch):
    import gaia.agents.base.artifacts as artifacts

    now = [1000.0]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: now[0])
    store = ArtifactStore(ttl=3600)
    handle = store.put_result(prose(1, 4000))
    now[0] += 3500
    assert store.put_result(prose(1, 4000)) == handle
    now[0] += 3500
    assert store.read(handle, offset=0, limit=10)["content"]


def test_error_results_stay_whole():
    error = json.dumps(
        {"status": "error", "error": "clock not configured: " + "x" * 4000}
    )
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.call()
    run.step(1, error)
    run.step(2, prose(2, 8000))
    for n in range(3, 6):
        run.call()
        run.step(n, prose(n, 100))
    texts = _tool_texts(run.messages)
    assert texts[0] == error and _is_stub(texts[1])


def test_a_full_archive_during_overflow_keeps_results_whole():
    store = ArtifactStore(max_bytes=10)
    messages = [
        {"role": "user", "content": "go"},
        _assistant_call("c1", "read_file", {"path": "a"}),
        _tool_msg("read_file", "c1", prose(1, 8000)),
        _assistant_call("c2", "read_file", {"path": "b"}),
        _tool_msg("read_file", "c2", prose(2, 8000)),
    ]
    shrunk, items = shrink_for_overflow(messages, store)
    assert items == []
    assert _tool_texts(shrunk) == [prose(1, 8000), prose(2, 8000)]


@pytest.mark.usefixtures("clean_registry")
def test_the_step_cap_answer_is_measured_and_evicted_too():
    agent = _make_agent(max_steps=8)
    agent.console.print_context_eviction = MagicMock()
    sent = _stub_chat(agent, [*_script(8)[:8], "Closing: done."])

    before_call = MagicMock(wraps=agent._context_evictor.before_call)
    agent._context_evictor.before_call = before_call

    agent.process_query("read the files")

    # Every request, the closing one included, was measured first.
    assert before_call.call_count == len(sent) == 9
    closing = sent[-1]
    assert any(_is_stub(t) for t in _tool_texts(closing))
    assert _measure({"messages": closing, "system_prompt": agent.system_prompt}) < 16384


def test_batches_from_steps_without_a_stats_record_add_up():
    agent = _make_agent()
    agent._pending_eviction = {"evicted_results": 2, "evicted_tokens_est": 900}
    agent._context_evictor.before_call = MagicMock(
        return_value={"evicted_results": 1, "evicted_tokens_est": 300, "items": []}
    )
    agent._context_evictor.begin_turn(16384)
    agent._evict_before_model_call([{"role": "user", "content": "go"}])
    assert agent._pending_eviction["evicted_results"] == 3
    assert agent._pending_eviction["evicted_tokens_est"] == 1200


def test_a_bare_reread_of_a_put_away_result_reads_a_full_page():
    # Small models drop the stub's entry=1; the result must still come back.
    store = ArtifactStore()
    text = prose("whole", 12000)
    handle = store.put_result(text)
    page = store.read(handle)
    assert page["content"] == text[:8000]
    assert page["next_offset"] == 8000
    # A condensed result's own archive keeps the old default.
    other = store.put(prose("other", 12000))
    assert len(store.read(other)["content"]) == 2000


def test_evicted_results_are_archived_as_whole_results():
    run = _Run(4096, threshold_tokens=10, min_batch_tokens=1)
    run.call()
    run.step(1, prose(1, 12000))
    run.call()
    run.call()
    run.step(2, prose(2, 100))
    run.call()
    handle = _handle(_tool_texts(run.messages)[0])
    assert run.store.read(handle)["content"] == prose(1, 12000)[:8000]


def test_the_overflow_retry_never_cuts_a_page_the_model_just_reread():
    store = ArtifactStore()
    original = prose("orig", 20000)
    handle = store.put_result(original)
    page = json.dumps(store.read(handle, entry=1))
    messages = [
        {"role": "user", "content": "go"},
        _assistant_call("r1", "read_tool_output", {"artifact": handle, "entry": 1}),
        _tool_msg("read_tool_output", "r1", page),
    ]
    shrunk, items = shrink_for_overflow(messages, store)
    assert _tool_texts(shrunk) == [page] and items == []


# ---------------------------------------------------------------------------
# The run archive: content-hash handles, on disk, outliving the TTL
# ---------------------------------------------------------------------------


def test_the_same_result_gets_the_same_handle_in_any_store():
    text = prose("same", 6000)
    assert ArtifactStore().put_result(text) == ArtifactStore().put_result(text)
    assert ArtifactStore().put_result(text) != ArtifactStore().put_result(text + "!")


def test_an_archived_result_outlives_the_ttl_and_the_process(tmp_path, monkeypatch):
    import gaia.agents.base.artifacts as artifacts

    now = [1000.0]
    monkeypatch.setattr(artifacts.time, "monotonic", lambda: now[0])
    text = prose("disk", 12000)
    handle = ArtifactStore(ttl=60, archive_dir=tmp_path).put_result(text)
    assert (tmp_path / f"{handle}.txt").read_text(encoding="utf-8") == text

    # A fresh store on the same run directory, long after the TTL.
    now[0] += 10_000
    reborn = ArtifactStore(ttl=60, archive_dir=tmp_path)
    assert reborn.has(handle)
    assert _read_all(reborn, handle) == text
    assert reborn.read(handle)["content"] == text[:8000]


def test_a_missing_original_fails_loudly(tmp_path):
    store = ArtifactStore(archive_dir=tmp_path)
    with pytest.raises(ValueError, match="Unknown output handle output_"):
        store.read("output_" + "0" * 32)


@pytest.mark.usefixtures("clean_registry")
def test_the_agent_archives_put_away_results_under_its_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    agent = _make_agent()
    _stub_chat(agent, _script(12))
    agent.process_query("read the files")

    run_dir = tmp_path / "home" / "runs" / agent.run_id / "results"
    archived = sorted(p.read_text(encoding="utf-8") for p in run_dir.glob("*.txt"))
    assert archived and all(
        text in [_full(n) for n in range(1, 13)] for text in archived
    )


def test_counts_lean_high_for_json_and_cjk():
    from gaia.agents.base.context_eviction import message_tokens

    for text in ('{"a": [1, 2, 3]}' * 200, "日本語のテキスト" * 200):
        message = {"role": "tool", "content": text}
        size = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        assert message_tokens(message) >= size / 3


# ---------------------------------------------------------------------------
# context_state(): what the context panel shows between turns
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_context_state_reports_the_stubs_the_last_request_carried():
    agent = _make_agent()
    assert agent.context_state() == {"put_away": None, "summarized": None}
    sent = _stub_chat(agent, [*_script(12), _ANSWER])
    agent.process_query("read the files")

    state = agent.context_state()
    stubs = [t for t in _tool_texts(sent[-1]) if _is_stub(t)]
    assert state["summarized"] is None
    assert state["put_away"]["results"] == len(stubs) > 0
    full = {_full(n) for n in range(1, 13)}
    assert state["put_away"]["tokens"] > len(stubs) * 3000
    assert all(_read_all(agent._output_artifacts, _handle(t)) in full for t in stubs)


@pytest.mark.usefixtures("clean_registry")
def test_context_state_counts_what_an_overflow_retry_put_away():
    agent = _make_agent(context_eviction="on", context_eviction_threshold_tokens=10**9)

    def overflow_by_200(kwargs):
        return _overflow(_measure(kwargs) + 200, _measure(kwargs))

    _stub_chat(agent, [*_script(3)[:3], overflow_by_200, _ANSWER])
    with patch.object(Agent, "_is_loaded_ctx_too_small", return_value=False):
        agent.process_query("read the files")
    assert agent.context_state()["put_away"]["results"] == 3


def test_context_state_off_reports_nothing():
    agent = _make_agent(context_eviction="off")
    assert agent.context_state() == {"put_away": None, "summarized": None}
