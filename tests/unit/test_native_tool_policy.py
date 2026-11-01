# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which gateway models get native tool schemas, and what that costs per call.

Native ``tools=`` resends every tool schema on every call, ~12K tokens more than
the in-text list for the flagship. A gateway model that follows the in-text
format (DeepSeek V4.1 Flash) stays on it; one that writes its own tool markup
there instead (Claude's ``<invoke>`` XML) is moved to native calls.
"""

import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_markup import MalformedToolMarkup
from gaia.llm import lemonade_client as lc

CATALOG = {
    "data": [
        {
            # A huge advertised window, probed native, follows the text format.
            "id": "amd.Flash",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
            "context_length": 1000000,
            "max_context_window": 1000000,
        },
        {
            # No window at all: Claude-like, writes its own markup.
            "id": "amd.Opus",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
        },
        {"id": "amd.Plain", "recipe": lc.CLOUD_RECIPE, "labels": ["cloud"]},
        {
            "id": "amd.Labelled",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "tool-calling"],
        },
    ]
}

INVOKE = (
    'Reading it.\n<function_calls>\n<invoke name="read_file">\n'
    '<parameter name="file_path">a.py</parameter>\n</invoke>\n</function_calls>'
)


@pytest.fixture(autouse=True)
def gateway(tmp_path, monkeypatch):
    path = tmp_path / "gateway.json"
    monkeypatch.setattr("gaia.llm.gateway.GATEWAY_STATE_FILE", path)
    monkeypatch.setattr(lc, "_CLOUD_TOOL_CALLING", {})
    monkeypatch.setattr(lc, "_CLOUD_PROBE_FAILURES", {})
    monkeypatch.setattr(lc, "_CLOUD_OWN_MARKUP", set())
    monkeypatch.setattr(lc, "_TOOL_CALLING_LOADED", False)
    previous = lc._CLOUD_MODELS
    lc.record_cloud_models(CATALOG)
    lc.record_tool_calling("amd.Flash", True)
    lc.record_tool_calling("amd.Opus", True)
    lc.record_tool_calling("amd.Plain", False)
    yield path
    lc._CLOUD_MODELS = previous


def _forget_in_memory():
    lc._CLOUD_TOOL_CALLING.clear()
    lc._CLOUD_OWN_MARKUP.clear()
    lc._TOOL_CALLING_LOADED = False


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


def _agent(model_id, use_claude=False):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _Agent(silent_mode=True, skip_lemonade=True)
    agent.model_id = model_id
    agent._use_claude = use_claude
    agent._instance_tools = {
        "read_file": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"file_path": {"type": "string", "required": True}},
            "function": lambda file_path: {"status": "success"},
            "atomic": True,
        }
    }
    agent.__dict__.pop("_system_prompt_cache", None)
    return agent


def _text_shape(agent):
    prompt = agent.system_prompt
    return (
        agent._openai_tools is None
        and "==== AVAILABLE TOOLS ====" in prompt
        and agent._response_format_template in prompt
    )


# --- policy -----------------------------------------------------------------


def test_a_probed_native_model_that_follows_the_text_format_stays_in_text():
    assert lc.is_tool_calling_model("amd.Flash") is True
    assert lc.uses_native_tool_calls("amd.Flash") is False


def test_a_model_that_wrote_its_own_markup_goes_native_and_it_persists(gateway):
    lc.record_own_tool_markup("amd.Opus")
    assert lc.uses_native_tool_calls("amd.Opus") is True
    assert json.loads(gateway.read_text())["own_tool_markup_models"] == ["amd.Opus"]
    _forget_in_memory()
    assert lc.uses_native_tool_calls("amd.Opus") is True


def test_a_record_keeps_what_another_process_saved_meanwhile(gateway):
    lc._load_tool_calling()
    saved = json.loads(gateway.read_text())
    saved["own_tool_markup_models"] = ["amd.Other"]
    gateway.write_text(json.dumps(saved))
    lc.record_own_tool_markup("amd.Opus")
    assert json.loads(gateway.read_text())["own_tool_markup_models"] == [
        "amd.Opus",
        "amd.Other",
    ]


def test_an_unwritable_gateway_file_keeps_the_switch_for_this_session(caplog):
    with patch("gaia.llm.gateway.GatewayState.save", side_effect=OSError("denied")):
        lc.record_own_tool_markup("amd.Opus")
    assert lc.uses_native_tool_calls("amd.Opus") is True
    assert "Could not persist the tool-call markup of amd.Opus" in caplog.text


def test_a_bug_while_saving_is_not_swallowed():
    with patch("gaia.llm.gateway.GatewayState.save", side_effect=TypeError("bug")):
        with pytest.raises(TypeError):
            lc.record_own_tool_markup("amd.Opus")


def test_a_model_the_probe_found_not_native_never_goes_native():
    lc.record_own_tool_markup("amd.Plain")
    assert lc.uses_native_tool_calls("amd.Plain") is False


def test_catalog_labels_and_local_models_are_unchanged():
    assert lc.uses_native_tool_calls("amd.Labelled") is True
    assert lc.uses_native_tool_calls("Gemma-4-E4B-it-GGUF") is True
    assert lc.uses_native_tool_calls("user.embeddinggemma-300m-GGUF") is False
    assert lc.uses_native_tool_calls(None) is False


def test_an_unknown_model_keeps_the_optimistic_local_default():
    assert lc.uses_native_tool_calls("some-future-model-GGUF") is True


@pytest.mark.parametrize("payload", ["[]", '"text"', "3", "null"])
def test_a_gateway_file_that_is_not_an_object_names_the_fix(gateway, payload):
    from gaia.llm.gateway import GatewayError, GatewayState

    gateway.write_text(payload)
    with pytest.raises(GatewayError, match="not an object") as raised:
        GatewayState.load()
    assert str(gateway) in str(raised.value)
    assert "delete it" in str(raised.value)


def test_a_gateway_file_without_the_new_field_loads(gateway):
    gateway.write_text(json.dumps({"tool_calling_models": ["amd.Flash"]}))
    _forget_in_memory()
    assert lc.is_tool_calling_model("amd.Flash") is True
    assert lc.uses_native_tool_calls("amd.Flash") is False


# --- before this process has read the catalog --------------------------------


@pytest.fixture
def cold_catalog():
    # A sidecar that never listed models: only gateway.json is known.
    lc._CLOUD_MODELS = {}


def test_a_cold_gateway_model_starts_in_text(cold_catalog):
    assert lc.uses_native_tool_calls("amd.Flash") is False
    assert lc.uses_native_tool_calls("amd.Never-Probed") is False
    assert _text_shape(_agent("amd.Flash"))


def test_a_cold_gateway_model_that_wrote_markup_is_native(cold_catalog):
    lc.record_own_tool_markup("amd.Opus")
    assert lc.uses_native_tool_calls("amd.Opus") is True


def test_a_cold_model_the_probe_found_not_native_never_goes_native(
    cold_catalog, gateway
):
    agent = _agent("amd.Plain")
    agent._parse_llm_response(INVOKE)
    _stays_text(agent, gateway)


def test_a_cold_gateway_model_switches_on_its_own_markup(cold_catalog, gateway):
    agent = _agent("amd.Opus")
    agent._parse_llm_response(INVOKE)
    assert agent._uses_native_tool_calls() is True
    assert json.loads(gateway.read_text())["own_tool_markup_models"] == ["amd.Opus"]


def test_an_unprobed_model_does_not_flip_when_the_catalog_loads(cold_catalog, gateway):
    unprobed = {"id": "amd.New", "recipe": lc.CLOUD_RECIPE, "labels": ["cloud"]}
    agent = _agent("amd.New")
    agent._parse_llm_response(INVOKE)
    before = agent._uses_native_tool_calls()
    lc.record_cloud_models({"data": CATALOG["data"] + [unprobed]})
    assert before is False
    assert agent._uses_native_tool_calls() is False
    _stays_text(agent, gateway)


def test_a_switched_model_stays_native_when_the_catalog_loads(cold_catalog):
    agent = _agent("amd.Opus")
    agent._parse_llm_response(INVOKE)
    assert agent._uses_native_tool_calls() is True
    lc.record_cloud_models(CATALOG)
    assert agent._uses_native_tool_calls() is True


def test_a_cold_local_model_is_still_native(cold_catalog):
    assert lc.uses_native_tool_calls("Gemma-4-E4B-it-GGUF") is True
    assert lc.uses_native_tool_calls("some-future-model-GGUF") is True


# --- what a DeepSeek-like model is sent ---------------------------------------


@pytest.mark.parametrize("model_id", ["amd.Flash", "amd.Opus"])
def test_a_text_format_model_is_sent_exactly_what_it_got_before_probing(model_id):
    # Before the probe the gateway model was not native: same prompt, no tools=.
    agent = _agent(model_id)
    assert _text_shape(agent)
    sent = (agent.system_prompt, agent._openai_tools, agent._truncation_budget())
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=False):
        before = _agent(model_id)
        assert (
            before.system_prompt,
            before._openai_tools,
            before._truncation_budget(),
        ) == sent


@pytest.mark.parametrize("model_id", ["amd.Flash", "amd.Opus", "amd.Plain"])
def test_the_tool_result_budget_ignores_the_advertised_window(model_id):
    # A 1M window, no window, and a non-native model all keep the gateway budget.
    assert _agent(model_id)._truncation_budget() == lc.truncation_budget(None)


# --- Claude-like: switches on its own markup ----------------------------------


def test_own_markup_runs_and_switches_the_model_to_native(gateway):
    agent = _agent("amd.Opus")
    assert _text_shape(agent)
    parsed = agent._parse_llm_response(INVOKE)
    assert parsed["tool"] == "read_file"
    assert parsed["tool_args"] == {"file_path": "a.py"}
    assert agent._uses_native_tool_calls() is True
    assert [s["function"]["name"] for s in agent._openai_tools] == ["read_file"]
    assert "==== AVAILABLE TOOLS ====" not in agent.system_prompt
    assert json.loads(gateway.read_text())["own_tool_markup_models"] == ["amd.Opus"]


def test_the_markup_parser_itself_never_switches(gateway):
    agent = _agent("amd.Opus")
    assert agent._parse_markup_tool_calls(INVOKE)["tool"] == "read_file"
    _stays_text(agent, gateway)


def test_the_request_after_a_mid_turn_switch_goes_out_native(gateway):
    agent = _agent("amd.Opus")
    agent.streaming = False
    replies = iter([INVOKE, "a.py holds one print call."])
    sent = []

    def send(messages, system_prompt, tools=None, **_):
        sent.append((deepcopy(messages), system_prompt, tools))
        reply = MagicMock()
        reply.text, reply.stats = next(replies), {}
        return reply

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    with patch.object(agent, "_tool_requires_confirmation", return_value=False):
        agent.process_query("What is in a.py?", max_steps=4)

    (_, first_prompt, first_tools), (messages, prompt, tools) = sent[:2]
    assert first_tools is None
    assert "==== AVAILABLE TOOLS ====" in first_prompt
    assert [t["function"]["name"] for t in tools] == ["read_file"]
    assert "==== AVAILABLE TOOLS ====" not in prompt
    assert agent._response_format_template not in prompt
    # No call asked for the markup step's result, so it goes out as user text:
    # strict tools= routes reject a tool result without a matching call.
    assistant, result = messages[-2:]
    assert assistant["role"] == "assistant" and assistant["content"] == INVOKE
    assert result["role"] == "user"
    assert result["content"].startswith("[Recorded tool result: read_file] ")
    asked = {c["id"] for m in messages for c in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in asked for m in messages if m.get("role") == "tool")


def _recast(agent, messages):
    agent._recast_unpaired_tool_results(messages)
    return messages


def test_a_paired_native_result_stays_a_tool_message():
    agent = _agent("amd.Labelled")
    call = {"id": "c1", "type": "function", "function": {"name": "read_file"}}
    paired = [
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "name": "read_file", "tool_call_id": "c1", "content": "x"},
    ]
    assert _recast(agent, [dict(m) for m in paired]) == paired


def test_a_local_or_text_format_route_keeps_its_tool_messages():
    orphan = [
        {"role": "tool", "name": "read_file", "tool_call_id": "z", "content": "x"}
    ]
    for model_id in ("Gemma-4-E4B-it-GGUF", "amd.Flash"):
        assert _recast(_agent(model_id), [dict(m) for m in orphan]) == orphan


def test_a_new_session_starts_native_after_the_switch():
    _agent("amd.Opus")._parse_llm_response(INVOKE)
    _forget_in_memory()
    agent = _agent("amd.Opus")
    assert agent._openai_tools is not None
    assert agent._response_format_template not in agent.system_prompt


def test_direct_claude_is_always_native():
    agent = _agent("claude-opus-5", use_claude=True)
    assert agent._uses_native_tool_calls() is True
    assert agent._openai_tools is not None


# --- adversarial: nothing else flips a model ----------------------------------


def _stays_text(agent, gateway):
    assert agent._uses_native_tool_calls() is False
    assert _text_shape(agent)
    if gateway.exists():
        saved = json.loads(gateway.read_text())
        assert saved.get("own_tool_markup_models", []) == []


def test_markup_inside_a_code_fence_is_an_example_not_a_switch(gateway):
    agent = _agent("amd.Flash")
    assert agent._parse_markup_tool_calls(f"For example:\n```\n{INVOKE}\n```") is None
    _stays_text(agent, gateway)


def test_markup_for_a_tool_the_agent_lacks_does_not_switch(gateway):
    agent = _agent("amd.Flash")
    agent._parse_llm_response(INVOKE.replace("read_file", "rm_rf"))
    _stays_text(agent, gateway)


def test_malformed_markup_is_retried_without_switching(gateway):
    agent = _agent("amd.Flash")
    with pytest.raises(MalformedToolMarkup):
        agent._parse_llm_response('<invoke name="read_file">\n<parameter')
    _stays_text(agent, gateway)


def test_a_model_without_native_support_still_runs_its_markup_in_text(gateway):
    agent = _agent("amd.Plain")
    assert agent._parse_llm_response(INVOKE)["tool"] == "read_file"
    _stays_text(agent, gateway)


@pytest.mark.parametrize("model_id", ["amd.Labelled", "Gemma-4-E4B-it-GGUF"])
def test_an_already_native_or_local_model_records_nothing(gateway, model_id):
    agent = _agent(model_id)
    agent._parse_llm_response(INVOKE)
    assert agent._uses_native_tool_calls() is True
    assert json.loads(gateway.read_text()).get("own_tool_markup_models") == []


def test_a_json_reply_quoting_markup_is_not_a_switch(gateway):
    agent = _agent("amd.Flash")
    reply = json.dumps({"answer": INVOKE})
    parsed = agent._parse_llm_response(reply)
    assert "tool" not in parsed
    _stays_text(agent, gateway)
