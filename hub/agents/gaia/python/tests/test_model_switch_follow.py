# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat switched to another model behaves like one started on it.

The switch swaps the chat client in place so the conversation survives. What
the agent chose once for the model it was built on has to move too: Gemma on
the NPU reads no images, Gemma on the GPU does, and a chat that went from one
to the other kept the first one's answer. A real flagship runs here against
the installer journeys' stand-in Lemonade, which records what it is asked.
"""

from __future__ import annotations

import json

import pytest

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, NPU_DEFAULT_MODEL_NAME
from gaia.llm.lemonade_manager import LemonadeManager
from tests.fixtures.fake_lemonade import FakeLemonade

GPU = DEFAULT_MODEL_NAME
NPU = NPU_DEFAULT_MODEL_NAME
EMBEDDER = "user.embeddinggemma-300m-GGUF"
NPU_EMBEDDER = "embed-gemma-300m-FLM"


@pytest.fixture
def server(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    # The app saves a pick as last_model before the chat's next turn.
    config_file.write_text(json.dumps({"last_model": NPU}), encoding="utf-8")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "home" / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    LemonadeManager.reset()
    env = {"FAKE_LEMONADE_MAX_LLM": "1", "FAKE_LEMONADE_NPU": "1"}
    with FakeLemonade(downloaded=(GPU, NPU, EMBEDDER, NPU_EMBEDDER), env=env) as fake:
        monkeypatch.setenv("LEMONADE_BASE_URL", fake.base_url)
        yield fake
    LemonadeManager.reset()


@pytest.fixture
def npu_chat(server, tmp_path):
    """A flagship in a chat that started on Gemma's NPU build."""
    from gaia_agent.session_registry import build_session_agent, close_agent

    agent = build_session_agent(model_id=NPU, allowed_paths=[str(tmp_path / "work")])
    yield agent
    close_agent(agent)


def test_a_chat_moved_to_the_gpu_reads_images_and_answers_there(npu_chat, server):
    from gaia_agent import stdio

    assert npu_chat.vlm_model is None, "reading images is off on the NPU"

    stdio.switch_model(npu_chat, GPU)

    assert npu_chat.model_id == GPU
    assert npu_chat.vlm_model == GPU
    assert npu_chat.vlm_client is not None
    assert npu_chat._rag_config_kwargs["model"] == GPU
    assert npu_chat._rag_config.model == GPU
    assert npu_chat._rag_config.vlm_model == GPU

    before = len(server.requests)
    npu_chat.process_query("hello")
    asked = server.requests[before:]
    chats = [r.model for r in asked if r.path.endswith("/chat/completions")]
    loads = [r.model for r in asked if r.path.endswith("/load")]
    assert chats and set(chats) == {GPU}
    assert GPU in loads and NPU not in loads


def test_a_chat_moved_to_the_npu_stops_reading_images(npu_chat):
    from gaia_agent import stdio

    stdio.switch_model(npu_chat, GPU)
    stdio.switch_model(npu_chat, NPU)

    assert npu_chat.vlm_model is None
    assert npu_chat.vlm_client is None
    assert npu_chat._rag_config.model == NPU
    assert npu_chat._rag_config.vlm_model is None


def test_a_switch_that_fails_leaves_the_old_models_settings(npu_chat, monkeypatch):
    from gaia_agent import stdio

    def broken():
        raise ValueError("prompt composition failed")

    monkeypatch.setattr(npu_chat, "rebuild_system_prompt", broken)

    with pytest.raises(RuntimeError, match="rolled back"):
        stdio.switch_model(npu_chat, GPU)

    assert npu_chat.model_id == NPU
    assert npu_chat.vlm_model is None
    assert npu_chat.vlm_client is None
    assert npu_chat._rag_config_kwargs["model"] == NPU
    assert npu_chat._rag_config.model == NPU
    assert npu_chat._rag_config.vlm_model is None


def test_a_vision_model_chosen_in_settings_reaches_an_open_chat(server, tmp_path):
    """It was read once, when the chat's agent was built."""
    from gaia_agent.session_registry import build_session_agent, close_agent

    from gaia.config import GaiaConfig

    GaiaConfig.save_keys({"last_model": GPU})
    agent = build_session_agent(model_id=GPU, allowed_paths=[str(tmp_path / "work")])
    try:
        assert agent.vlm_model == GPU
        GaiaConfig.save_keys({"vision_model": "Qwen3-VL-4B-Instruct-GGUF"})

        agent.follow_model_switch()

        assert agent.vlm_model == "Qwen3-VL-4B-Instruct-GGUF"
        assert agent.vlm_client.vlm_model == "Qwen3-VL-4B-Instruct-GGUF"
        assert agent._rag_config.vlm_model == "Qwen3-VL-4B-Instruct-GGUF"
    finally:
        close_agent(agent)


def test_image_reading_follows_the_agents_own_model_not_the_saved_pick(
    server, tmp_path
):
    """Tools re-register every in-process turn; the pick saved elsewhere must not win."""
    from gaia_agent.session_registry import build_session_agent, close_agent

    from gaia.config import GaiaConfig

    agent = build_session_agent(model_id=NPU, allowed_paths=[str(tmp_path / "work")])
    try:
        GaiaConfig.save_keys({"last_model": GPU})
        agent._register_tools()
        assert agent.vlm_model is None, "chat on the NPU reads no images"
    finally:
        close_agent(agent)


@pytest.mark.parametrize("profile", ["npu", "gpu"])
def test_every_chat_on_a_pc_uses_its_profiles_embedder(server, tmp_path, profile):
    """A chat built on, or switched to, either device keeps the PC's embedder.

    Memory has one store; a different embedder clears all its vectors (#6008).
    It is the GPU one on an NPU PC too: the NPU build's search isn't proven.
    """
    from gaia_agent import stdio
    from gaia_agent.session_registry import build_session_agent, close_agent

    from gaia.config import GaiaConfig

    GaiaConfig.save_keys({"default_device": profile})
    expected = EMBEDDER
    for model, device in ((GPU, "gpu"), (NPU, None)):
        agent = build_session_agent(
            model_id=model, device=device, allowed_paths=[str(tmp_path / "work")]
        )
        try:
            assert agent._rag_config_kwargs["embedding_model"] == expected
            assert agent._embedding_model == expected
            stdio.switch_model(agent, NPU if model == GPU else GPU)
            assert agent._embedding_model == expected
        finally:
            close_agent(agent)


def test_an_open_chat_follows_an_embedder_moved_in_settings(server, tmp_path):
    """Moving the embedder rebuilds an open chat on its next turn, keeping it.

    The model, the conversation and the loaded skills carry over; nothing is
    rebuilt while the embedder stays where it is.
    """
    from gaia_agent import session_registry as sr

    from gaia.config import GaiaConfig

    # A choice saved before the NPU embedder was withdrawn still loads.
    path = GaiaConfig.config_path()
    path.write_text(
        json.dumps(
            {**json.loads(path.read_text(encoding="utf-8")), "embedding_device": "npu"}
        ),
        encoding="utf-8",
    )
    registry = sr._SessionRegistry()
    kwargs = {"model_id": NPU, "allowed_paths": [str(tmp_path / "work")]}
    try:
        session, took = registry.checkout("chat-1", **kwargs)
        assert took
        old = session.agent
        assert old._embedding_model == NPU_EMBEDDER
        old.conversation_history = [{"role": "user", "content": "hello"}]
        assert session.follow_settings(**kwargs) is False
        assert session.agent is old

        GaiaConfig.save_key("embedding_device", "auto")
        # A later turn names no model: the chat keeps the one it is on.
        assert session.follow_settings(allowed_paths=kwargs["allowed_paths"])
        assert session.agent is not old
        assert session.agent._embedding_model == EMBEDDER
        assert session.agent.model_id == NPU
        assert session.agent.conversation_history == old.conversation_history
        session.run_lock.release()

        # What the user set in the chat survives the next move too.
        session, _ = registry.checkout("chat-1", **kwargs)
        agent = session.agent
        granted = tmp_path / "granted"
        granted.mkdir()
        agent.internet_off = True
        agent._incognito, agent._incognito_reason = True, "private"
        agent.path_validator.add_allowed_path(str(granted))
        agent._attached_documents = {str(granted / "notes.txt")}
        path.write_text(
            json.dumps(
                {
                    **json.loads(path.read_text(encoding="utf-8")),
                    "embedding_device": "npu",
                }
            ),
            encoding="utf-8",
        )
        assert session.follow_settings(allowed_paths=kwargs["allowed_paths"])
        moved = session.agent
        assert moved is not agent and moved._embedding_model == NPU_EMBEDDER
        assert moved.internet_off is True
        assert (moved._incognito, moved._incognito_reason) == (True, "private")
        assert moved.path_validator.is_path_allowed(str(granted), prompt_user=False)
        assert moved._attached_documents == agent._attached_documents
        session.run_lock.release()
    finally:
        registry.clear()


def test_only_a_moved_embedder_counts_as_changed(monkeypatch):
    """The check behind every chat's rebuild, in the sidecar and the Agent UI.

    An agent that recorded no embedder never rebuilds; an unreadable config
    fails the turn loudly rather than quietly keeping the old embedder.
    """
    from gaia_agent import chat_agent as ca

    from gaia.config import GaiaConfigError

    agent = object.__new__(ca.ChatAgent)
    monkeypatch.setattr(ca, "machine_embedding_model", lambda: EMBEDDER)
    assert agent.embedder_changed() is False
    agent._embedder_at_build = EMBEDDER
    assert agent.embedder_changed() is False
    agent._embedder_at_build = NPU_EMBEDDER
    assert agent.embedder_changed() is True

    def unreadable():
        raise GaiaConfigError("config.json is not valid JSON")

    monkeypatch.setattr(ca, "machine_embedding_model", unreadable)
    with pytest.raises(GaiaConfigError):
        agent.embedder_changed()
