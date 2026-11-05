# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar warms the model chat reopens on, not always ``default_model``.

A user whose setup chose the GPU model and who then picked the NPU model waited
~50 s on their first message after a restart: the sidecar warmed the GPU
default, Lemonade (one chat model per pool) evicted the NPU model for it, and
the turn evicted it back.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("gaia_agent")

import gaia_agent.session_registry as registry_mod  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

import gaia.config as gaia_config  # noqa: E402
import gaia.llm.lemonade_client as lc  # noqa: E402
import gaia.ui.warmup as ui_warmup  # noqa: E402

GPU = "Gemma-4-E4B-it-GGUF"
NPU = "gemma4-it-e4b-FLM"


class _Agent:
    def __init__(self, model_id):
        self.model_id = model_id
        self.warmed = False

    def warm_up(self, cancel=None):
        self.warmed = True
        return {"seconds": 1.0, "prompt_tokens": 20000, "cached_tokens": 0}


@pytest.fixture
def sidecar(monkeypatch, tmp_path):
    """A just-started sidecar on a roomy PC whose setup chose the GPU model."""
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(gaia_config, "GAIA_CONFIG_FILE", config_file)
    monkeypatch.setattr(server_mod, "_startup_warm_up", server_mod._StartupWarmUp())
    monkeypatch.setattr(ui_warmup, "preload_skip_reason", lambda: None)
    downloaded = {GPU, NPU}
    monkeypatch.setattr(
        lc.LemonadeClient,
        "get_model_info",
        lambda self, name: {"id": name, "downloaded": name in downloaded},
    )
    built = []

    def build(**kwargs):
        agent = _Agent(kwargs.get("model_id"))
        built.append((kwargs, agent))
        return agent

    monkeypatch.setattr(registry_mod, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "close_agent", lambda agent: None)

    def configure(**values):
        config_file.write_text(
            json.dumps({"default_model": GPU, **values}), encoding="utf-8"
        )

    configure()
    return configure, built, downloaded


def test_the_model_the_user_picked_is_warmed_not_the_setup_default(sidecar):
    configure, built, _ = sidecar
    configure(last_model=NPU)

    server_mod._warmup_blocking()

    assert [kwargs["model_id"] for kwargs, _ in built] == [NPU]
    assert built[0][1].warmed


def test_with_no_pick_the_setup_default_is_warmed(sidecar):
    _, built, _ = sidecar

    server_mod._warmup_blocking()

    assert [kwargs["model_id"] for kwargs, _ in built] == [GPU]


def test_building_the_warm_up_agent_loads_nothing(sidecar):
    """The load happens in warm_up, which a turn on another model can stop;
    a preload while the agent is built cannot be stopped."""
    configure, built, _ = sidecar
    configure(last_model=NPU)

    server_mod._warmup_blocking()

    assert built[0][0].get("skip_lemonade") is True


def test_a_picked_model_that_is_not_downloaded_is_not_swapped_for_the_default(
    sidecar,
):
    configure, built, downloaded = sidecar
    configure(last_model=NPU)
    downloaded.discard(NPU)

    server_mod._warmup_blocking()

    assert (
        built == []
    ), "warming the default would evict nothing but load the wrong model"


def test_a_claude_pick_warms_no_local_model(sidecar):
    configure, built, _ = sidecar
    configure(last_model="claude-sonnet-5", last_provider="claude")

    server_mod._warmup_blocking()

    assert built == []


def test_a_small_pc_loads_on_the_first_message_as_the_agent_ui_does(
    sidecar, monkeypatch
):
    _, built, _ = sidecar
    monkeypatch.setattr(ui_warmup, "preload_skip_reason", lambda: "16 GB of memory")

    server_mod._warmup_blocking()

    assert built == []


def test_a_turn_with_no_model_runs_the_default_so_it_stops_a_warm_up_of_the_pick(
    sidecar,
):
    """A turn that names no model runs ``default_model``, not ``last_model``."""
    configure, _, _ = sidecar
    configure(last_model=NPU)
    state = server_mod._startup_warm_up
    state.aim(NPU)

    state.note_turn(None)

    assert state.cancel.is_set()


def test_a_turn_with_no_model_keeps_a_warm_up_of_the_default(sidecar):
    state = server_mod._startup_warm_up
    state.aim(GPU)

    state.note_turn(None)

    assert not state.cancel.is_set()


def test_a_chat_agent_built_with_skip_lemonade_preloads_nothing():
    from unittest.mock import patch

    from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

    with (
        patch.dict("os.environ", {"GAIA_MEMORY_DISABLED": "1"}),
        patch("gaia_agent.chat_agent.RAGSDK"),
        patch("gaia_agent.chat_agent.RAGConfig"),
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready") as ready,
        patch("gaia.llm.lemonade_client.LemonadeClient"),
    ):
        agent = ChatAgent(
            ChatAgentConfig(silent_mode=True, model_id=NPU, skip_lemonade=True)
        )
        agent.stop_watching()

    ready.assert_not_called()
    assert agent.model_id == NPU
