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
    with FakeLemonade(downloaded=(GPU, NPU, EMBEDDER), env=env) as fake:
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
