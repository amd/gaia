# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent must say where its chat is actually processed (#3674).

The flagship's system prompt opened with "a personal AI running locally on the
user's machine" regardless of backend, so a Fireworks-backed session described
itself as local while the TUI header above it correctly said remote. Asked
outright, the agent went reading configuration and still could not answer.

Two layers:

* the pure resolver, over every backend a session can be on;
* the composed system prompt, which must carry the destination and must change
  when the session switches provider.

No LLM or external service required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.inference_location import (
    InferenceLocation,
    resolve_inference_location,
)

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402

# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

BACKENDS = [
    # (id, kwargs, provider, remote)
    ("local_gemma", {"model": "Gemma-4-E4B-it-GGUF"}, "lemonade", False),
    ("local_user_ns", {"model": "user.my-finetune-GGUF"}, "lemonade", False),
    ("local_dotted_version", {"model": "Qwen3-4B-v0.2-GGUF"}, "lemonade", False),
    (
        "fireworks",
        {"model": "fireworks.accounts/fireworks/models/glm-4p6"},
        "fireworks",
        True,
    ),
    ("amd_gateway", {"model": "amd.Llama-3.3-70B"}, "amd", True),
    (
        "claude",
        {"model": "claude-sonnet-4-5", "use_claude": True},
        "claude",
        True,
    ),
    ("openai", {"model": "gpt-4o", "use_openai": True}, "openai", True),
]


@pytest.mark.parametrize(
    "kwargs,provider,remote",
    [(case[1], case[2], case[3]) for case in BACKENDS],
    ids=[case[0] for case in BACKENDS],
)
def test_backend_classification(kwargs, provider, remote):
    location = resolve_inference_location(**kwargs)
    assert location.provider == provider
    assert location.remote is remote


def test_a_local_session_says_nothing_is_sent_to_a_cloud():
    described = resolve_inference_location("Gemma-4-E4B-it-GGUF").describe()
    assert "this machine" in described
    assert "GAIA is not sending this conversation to any cloud provider" in described


def test_a_remote_session_names_the_provider_and_says_it_leaves_the_machine():
    described = resolve_inference_location(
        "fireworks.accounts/fireworks/models/glm-4p6"
    ).describe()
    assert "Fireworks AI" in described
    assert "cloud provider" in described
    assert "sent there" in described


def test_a_remote_session_says_what_is_sent_not_merely_what_is_stored():
    """Remote chat is not remote everything — but a recalled memory does travel.

    "memory runs on this machine" alone reads as a promise that what is in it
    stays here, which is false the moment the agent reads one out to answer.
    """
    described = resolve_inference_location("amd.Llama-3.3-70B").describe()
    for named in ("files", "documents", "memory"):
        assert named in described
    assert "stored and searched on this machine" in described
    assert "sent there to be answered, including whatever you read out" in described


def test_a_lemonade_routed_provider_says_it_goes_via_lemonade():
    assert "via Lemonade" in resolve_inference_location("amd.Llama-3.3-70B").describe()


def test_a_direct_provider_does_not_claim_to_go_via_lemonade():
    described = resolve_inference_location(
        "claude-sonnet-4-5", use_claude=True
    ).describe()
    assert "via Lemonade" not in described


def test_an_empty_model_id_is_still_answerable():
    location = resolve_inference_location("")
    assert location.model == "unknown"
    assert isinstance(location, InferenceLocation)


# ---------------------------------------------------------------------------
# The system prompt
# ---------------------------------------------------------------------------


def _agent(model: str, use_claude: bool = False, llm_client=None) -> ChatAgent:
    """A ChatAgent skeleton with a live-looking chat client on *model*."""
    cfg = ChatAgentConfig(rag_documents=[], streaming=False, silent_mode=True)
    with patch("gaia.agents.base.agent.Agent.__init__", return_value=None):
        agent = ChatAgent.__new__(ChatAgent)
        agent.config = cfg
        agent.rag = type("R", (), {"indexed_files": set()})()
        agent.library_documents = []
        chat_config = MagicMock()
        chat_config.use_claude = use_claude
        chat_config.use_chatgpt = False
        agent.chat = MagicMock()
        agent.chat.config = chat_config
        agent.chat.effective_model = model
        # Not a MagicMock: the agent duck-types ``cloud_model_provider`` off the
        # live client, and a mock answers every call truthily.
        agent.chat.llm_client = llm_client
    return agent


def test_a_local_session_prompt_says_the_agent_runs_locally():
    prompt = _agent("Gemma-4-E4B-it-GGUF")._get_system_prompt()
    assert "running locally on the user's machine" in prompt
    assert "WHERE THIS SESSION IS PROCESSED" in prompt


def test_a_fireworks_session_prompt_does_not_claim_to_be_local():
    prompt = _agent("fireworks.accounts/fireworks/models/glm-4p6")._get_system_prompt()
    assert "a personal AI running locally on the user's machine" not in prompt
    assert "Fireworks AI" in prompt


def test_a_claude_session_prompt_does_not_claim_to_be_local():
    prompt = _agent("claude-sonnet-4-5", use_claude=True)._get_system_prompt()
    assert "a personal AI running locally on the user's machine" not in prompt
    assert "Anthropic Claude" in prompt


def test_the_prompt_tells_the_agent_not_to_go_looking_for_the_answer():
    """The reported session spent a turn inspecting configuration instead."""
    prompt = _agent("fireworks.accounts/fireworks/models/glm-4p6")._get_system_prompt()
    assert "Never read config files" in prompt


def test_the_prompt_names_the_live_model():
    model = "fireworks.accounts/fireworks/models/glm-4p6"
    assert model in _agent(model)._get_system_prompt()


def test_switching_provider_changes_the_next_prompt():
    """A switch rebuilds the prompt; the destination must move with it."""
    agent = _agent("Gemma-4-E4B-it-GGUF")
    assert "running locally" in agent._get_system_prompt()

    agent.chat.effective_model = "fireworks.accounts/fireworks/models/glm-4p6"
    switched = agent._get_system_prompt()

    assert "Fireworks AI" in switched
    assert "a personal AI running locally on the user's machine" not in switched


def test_the_prompt_composes_before_the_chat_client_exists():
    """MCP registration rebuilds the prompt during __init__, before AgentSDK."""
    cfg = ChatAgentConfig(rag_documents=[], streaming=False, silent_mode=True)
    with patch("gaia.agents.base.agent.Agent.__init__", return_value=None):
        agent = ChatAgent.__new__(ChatAgent)
        agent.config = cfg
        agent.rag = type("R", (), {"indexed_files": set()})()
        agent.library_documents = []
        agent.model_id = "Gemma-4-E4B-it-GGUF"
        agent._use_claude = False

    assert "WHERE THIS SESSION IS PROCESSED" in agent._get_system_prompt()


def test_an_openai_session_is_not_described_as_local_before_the_client_exists():
    """The prompt can be composed during MCP registration, before AgentSDK."""
    cfg = ChatAgentConfig(rag_documents=[], streaming=False, silent_mode=True)
    cfg.use_chatgpt = True
    cfg.model_id = "gpt-4o"
    with patch("gaia.agents.base.agent.Agent.__init__", return_value=None):
        agent = ChatAgent.__new__(ChatAgent)
        agent.config = cfg
        agent.rag = type("R", (), {"indexed_files": set()})()
        agent.library_documents = []

    assert agent._inference_location().provider == "openai"
    assert "a personal AI running locally" not in agent._get_system_prompt()


def test_a_claude_session_names_the_claude_model_before_the_client_exists():
    """``model_id`` holds the LOCAL id on a Claude session; the config knows."""
    cfg = ChatAgentConfig(rag_documents=[], streaming=False, silent_mode=True)
    cfg.use_claude = True
    cfg.claude_model = "claude-sonnet-5"
    cfg.model_id = "Gemma-4-E4B-it-GGUF"
    with patch("gaia.agents.base.agent.Agent.__init__", return_value=None):
        agent = ChatAgent.__new__(ChatAgent)
        agent.config = cfg
        agent.rag = type("R", (), {"indexed_files": set()})()
        agent.library_documents = []

    location = agent._inference_location()
    assert location.provider == "claude"
    assert location.model == "claude-sonnet-5"


# ---------------------------------------------------------------------------
# The live client classifies better than the id prefix can
# ---------------------------------------------------------------------------


class _CatalogClient:
    """A Lemonade-style client that knows a provider the id doesn't announce."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def cloud_model_provider(self, model):
        self.asked.append(model)
        return self.answer


def test_a_runtime_discovered_provider_is_recognised():
    """``some-model`` looks local by its id; the catalog says otherwise."""
    client = _CatalogClient("together")
    agent = _agent("some-plainly-named-model", llm_client=client)

    location = agent._inference_location()

    assert location.provider == "together"
    assert location.remote is True
    assert client.asked == ["some-plainly-named-model"]


def test_a_client_that_says_local_still_reads_local():
    agent = _agent("Gemma-4-E4B-it-GGUF", llm_client=_CatalogClient(None))
    assert agent._inference_location().remote is False


def test_a_client_without_a_classifier_falls_back_to_the_id_rule():
    agent = _agent("fireworks.accounts/fireworks/routers/glm-4p6", llm_client=object())
    assert agent._inference_location().provider == "fireworks"


def test_a_lookup_that_answers_with_a_non_string_is_ignored(caplog):
    """A broken classifier must not put its return value in the user's sentence."""
    location = resolve_inference_location(
        "fireworks.accounts/fireworks/models/glm-4p6",
        cloud_provider_lookup=lambda _m: object(),
    )
    assert location.provider == "fireworks"
    assert "not a provider name" in caplog.text


def test_the_local_sentence_claims_only_gaias_own_routing():
    """Lemonade can be configured to route upstream; GAIA cannot see that."""
    described = resolve_inference_location("Gemma-4-E4B-it-GGUF").describe()
    assert "GAIA is not sending" in described
    assert "nothing in this conversation goes" not in described
