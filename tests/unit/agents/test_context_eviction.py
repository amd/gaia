# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stale tool results leave the context sent to the model, never the log.

Off by default. When on, results older than ``keep_steps`` are evicted in one
batch once the last measured prompt is over the threshold and the batch is
worth a cache break; each is archived whole and its stub names the handle.
``auto`` follows the cached-input price table and never guesses.
"""

# pylint: disable=protected-access

import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.artifacts import ArtifactStore
from gaia.agents.base.context_eviction import (
    CONTEXT_EVICTION_ENV_VAR,
    EVICT_KEEP_ENV_VAR,
    EVICT_MIN_BATCH_ENV_VAR,
    EVICT_THRESHOLD_ENV_VAR,
    ContextEvictor,
    context_eviction_from_env,
    evict_keep_from_env,
    evict_min_batch_from_env,
    evict_threshold_from_env,
    resolve_context_eviction,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.cache_pricing import bare_model_id, cached_input_price_ratio
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_TOOL = "probe_for_eviction_test"
_RESULT_CHARS = 3000
_ANSWER = "Done: every file was read."
#: 3000 chars is ~834 tokens: one result is under this batch, two are over.
_MIN_BATCH = 1000
_KEEP = 2
_THRESHOLD = 1000
_LIVE = 5000


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    for name in (
        CONTEXT_EVICTION_ENV_VAR,
        EVICT_THRESHOLD_ENV_VAR,
        EVICT_KEEP_ENV_VAR,
        EVICT_MIN_BATCH_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


class _ProbeAgent(Agent):
    """One tool whose result is a distinct 3000-char text per path."""

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def probe_for_eviction_test(path: str) -> str:
            """Read a path."""
            return f"{path}:" + ("x" * _RESULT_CHARS)


def _make_agent(model_id=DEFAULT_MODEL_NAME, **kwargs) -> _ProbeAgent:
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ProbeAgent(
            silent_mode=True, skip_lemonade=True, model_id=model_id, **kwargs
        )


def _stub_chat(agent, replies, prompt_tokens=_LIVE):
    """Script the model and keep a deep copy of every sent message list."""
    queue = list(replies)
    sent = []
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **kwargs):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        sent.append(copy.deepcopy(kwargs["messages"]))
        stats = {"prompt_tokens": prompt_tokens, "completion_tokens": 10}
        return SimpleNamespace(text=queue.pop(0), stats=stats)

    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return sent


def _native_call(number: int) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{number}",
                    "type": "function",
                    "function": {
                        "name": _TOOL,
                        "arguments": json.dumps({"path": f"file{number}.py"}),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _script(calls: int):
    return [_native_call(n) for n in range(1, calls + 1)] + [_ANSWER]


def _tool_texts(messages):
    return [m["content"][0]["text"] for m in messages if m.get("role") == "tool"]


def _full(number: int) -> str:
    return f"file{number}.py:" + ("x" * _RESULT_CHARS)


def _stats_entries(result):
    return [
        m["content"]
        for m in result["conversation"]
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and m["content"].get("type") == "stats"
    ]


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_off_by_default_sends_every_result_whole():
    agent = _make_agent(
        context_eviction_threshold_tokens=_THRESHOLD,
        context_eviction_keep_steps=_KEEP,
        context_eviction_min_batch_tokens=_MIN_BATCH,
    )
    assert agent.context_eviction_enabled is False
    sent = _stub_chat(agent, _script(7))

    result = agent.process_query("read the files")

    assert result["result"].startswith(_ANSWER)
    assert _tool_texts(sent[-1]) == [_full(n) for n in range(1, 8)]
    assert not any("evicted" in entry for entry in _stats_entries(result))


# ---------------------------------------------------------------------------
# On: one batch, only old results, exact text behind each stub
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_on_evicts_old_results_in_batches_and_keeps_them_readable():
    agent = _make_agent(
        context_eviction="on",
        context_eviction_threshold_tokens=_THRESHOLD,
        context_eviction_keep_steps=_KEEP,
        context_eviction_min_batch_tokens=_MIN_BATCH,
    )
    sent = _stub_chat(agent, _script(7))

    result = agent.process_query("read the files")

    # Step 4: only result 1 is old enough (~834 tokens < 1000): no batch.
    assert _tool_texts(sent[3]) == [_full(n) for n in range(1, 4)]
    # Step 5: results 1 and 2 are older than keep_steps and make a batch.
    step5 = _tool_texts(sent[4])
    assert step5[2:] == [_full(3), _full(4)]
    assert all(
        t.startswith("[evicted: probe_for_eviction_test path=") for t in step5[:2]
    )
    # Step 6: result 3 alone is under the batch; step 7 evicts 3 and 4.
    assert _tool_texts(sent[5])[2:] == [_full(n) for n in range(3, 6)]
    step7 = _tool_texts(sent[6])
    assert [t.startswith("[evicted:") for t in step7] == [True] * 4 + [False] * 2
    # Evicted stays evicted: the earlier stubs are unchanged at the end.
    assert _tool_texts(sent[-1])[:4] == step7[:4]

    store = agent._output_artifacts
    for number, stub in enumerate(step7[:4], start=1):
        handle = stub.split("artifact=")[1].split(",")[0]
        assert f"file{number}.py" in stub and f"{len(_full(number))} chars" in stub
        assert store.read(handle, entry=1)["content"] == _full(number)

    # The transcript log still holds every full result.
    logged = [m["content"] for m in result["conversation"] if m.get("role") == "tool"]
    assert logged == [_full(n) for n in range(1, 8)]

    # Recorded in the stats record of the step the batch fired on.
    fired = {s["step"]: s for s in _stats_entries(result) if "evicted_results" in s}
    assert sorted(fired) == [5, 7]
    assert fired[5]["evicted_results"] == 2
    assert fired[5]["evicted_tokens_est"] == fired[7]["evicted_tokens_est"] > 1600


@pytest.mark.usefixtures("clean_registry")
def test_nothing_is_evicted_under_the_threshold():
    agent = _make_agent(
        context_eviction="on",
        context_eviction_threshold_tokens=_THRESHOLD,
        context_eviction_keep_steps=_KEEP,
        context_eviction_min_batch_tokens=_MIN_BATCH,
    )
    sent = _stub_chat(agent, _script(7), prompt_tokens=_THRESHOLD)

    agent.process_query("read the files")

    assert _tool_texts(sent[-1]) == [_full(n) for n in range(1, 8)]


# ---------------------------------------------------------------------------
# The evictor on its own: protected tools, condensed results, messages
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


def _hot_evictor() -> ContextEvictor:
    evictor = ContextEvictor(threshold_tokens=10, keep_steps=1, min_batch_tokens=1)
    evictor.note_prompt_tokens({"prompt_tokens": 100})
    return evictor


def test_read_tool_output_and_delegate_results_are_never_evicted():
    store = ArtifactStore()
    messages = [
        {"role": "user", "content": "go"},
        _tool_msg("read_tool_output", "c1", "page " * 100),
        _tool_msg("delegate_task", "c2", "worker answer " * 100),
        _tool_msg("read_file", "c3", "source " * 100),
    ]
    evictor = _hot_evictor()
    evictor.evict(messages, step=1, store=store)  # registers them at step 0

    fired = evictor.evict(messages, step=5, store=store)

    assert fired == {"evicted_results": 1, "evicted_tokens_est": 195}
    assert messages[1]["content"][0]["text"] == "page " * 100
    assert messages[2]["content"][0]["text"] == "worker answer " * 100
    assert messages[3]["content"][0]["text"].startswith("[evicted: read_file;")


def test_condensed_result_keeps_its_handle_and_index():
    store = ArtifactStore()
    original = "\n".join(f"line {n}" for n in range(200))
    handle = store.put(original)
    store.set_index(handle, [{"offset": 100, "length": 50}])
    condensed = json.dumps(
        {"shown": [{"offset": 0, "text": "line 0"}], "artifact": handle}
    )
    messages = [
        {"role": "user", "content": "go"},
        _assistant_call("c1", "read_file", {"path": "a.py"}),
        _tool_msg("read_file", "c1", condensed),
    ]
    evictor = _hot_evictor()
    evictor.evict(messages, step=1, store=store)

    assert evictor.evict(messages, step=3, store=store)["evicted_results"] == 1
    stub = messages[2]["content"][0]["text"]
    assert stub == (
        f"[evicted: read_file path=a.py; {len(condensed)} chars; "
        f"read_tool_output(artifact={handle}, offset=0, limit=8000) fetches it]"
    )
    assert store.text(handle) == original
    assert store.read(handle, entry=1)["content"] == original[100:150]


def test_user_and_assistant_messages_are_untouched():
    store = ArtifactStore()
    messages = [
        {"role": "user", "content": "x" * 5000},
        {"role": "assistant", "content": "y" * 5000},
        _assistant_call("c1", "read_file", {"path": "a.py"}),
        _tool_msg("read_file", "c1", "z" * 5000),
    ]
    before = copy.deepcopy(messages[:3])
    evictor = _hot_evictor()
    evictor.evict(messages, step=1, store=store)
    evictor.evict(messages, step=3, store=store)

    assert messages[:3] == before
    assert messages[3]["content"][0]["text"].startswith(
        "[evicted: read_file path=a.py;"
    )


# ---------------------------------------------------------------------------
# auto: the price table decides, never a guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("fireworks.kimi-k2p7-code", True),
        ("fireworks.glm-5p3-flash", True),
        ("fireworks.glm-5p3", True),
        ("fireworks.deepseek-v4p1-flash", False),
        ("fireworks.deepseek-v4-pro-0813", False),
        ("fireworks.some-new-model", False),
    ],
)
def test_auto_follows_the_cached_price_ratio(model_id, expected, caplog):
    with caplog.at_level("INFO", logger="gaia.agents.base.context_eviction"):
        assert resolve_context_eviction("auto", model_id, cloud=True) is expected
    if cached_input_price_ratio(model_id) is None:
        assert "no cached-input price ratio known" in caplog.text


def test_auto_is_off_for_a_local_model():
    assert resolve_context_eviction("auto", DEFAULT_MODEL_NAME, cloud=False) is False
    assert resolve_context_eviction("on", DEFAULT_MODEL_NAME, cloud=False) is True
    assert bare_model_id("fireworks.glm-5p3") == "glm-5p3"


@pytest.mark.usefixtures("clean_registry")
def test_agent_resolves_auto_from_its_model():
    assert _make_agent(
        "fireworks.kimi-k2p7-code", context_eviction="auto"
    ).context_eviction_enabled
    assert not _make_agent(
        "fireworks.deepseek-v4p1-flash", context_eviction="auto"
    ).context_eviction_enabled
    assert not _make_agent(
        DEFAULT_MODEL_NAME, context_eviction="auto"
    ).context_eviction_enabled


# ---------------------------------------------------------------------------
# Env: wins over the arguments, malformed fails loudly
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("clean_registry")
def test_env_wins_over_the_constructor(monkeypatch):
    monkeypatch.setenv(CONTEXT_EVICTION_ENV_VAR, "on")
    monkeypatch.setenv(EVICT_THRESHOLD_ENV_VAR, "123")
    monkeypatch.setenv(EVICT_KEEP_ENV_VAR, "4")
    monkeypatch.setenv(EVICT_MIN_BATCH_ENV_VAR, "56")

    agent = _make_agent(context_eviction="off", context_eviction_keep_steps=9)

    evictor = agent._context_evictor
    assert (evictor.threshold_tokens, evictor.keep_steps, evictor.min_batch_tokens) == (
        123,
        4,
        56,
    )
    monkeypatch.setenv(CONTEXT_EVICTION_ENV_VAR, "OFF")
    assert _make_agent(context_eviction="on")._context_evictor is None


@pytest.mark.parametrize(
    "name, value, reader",
    [
        (CONTEXT_EVICTION_ENV_VAR, "maybe", context_eviction_from_env),
        (EVICT_THRESHOLD_ENV_VAR, "lots", evict_threshold_from_env),
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
