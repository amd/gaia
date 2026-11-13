# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Runs with no model named use the configured ``default_model``.

`gaia schedule run`, Telegram sessions and the MCP bridge's /chat and /llm
used to fall back to the local Gemma model, so a cloud-model install started a
4.7 GB download.
"""

import json

import pytest

import gaia.config
from gaia.mcp.mcp_bridge import GAIAMCPBridge
from gaia.schedule import runner
from gaia.schedule.store import Schedule

CONFIGURED = "fireworks.deepseek-v4p1-flash"


@pytest.fixture
def configured_default(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"default_model": CONFIGURED}))
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", config_file)
    return CONFIGURED


class _FakeSDK:
    configs = []

    def __init__(self, config=None):
        _FakeSDK.configs.append(config)

    def send(self, prompt, **_):
        class _Reply:
            text = "hi"

        return _Reply()


@pytest.fixture
def fake_sdk(monkeypatch):
    import gaia.chat.sdk

    _FakeSDK.configs = []
    monkeypatch.setattr(gaia.chat.sdk, "AgentSDK", _FakeSDK)
    return _FakeSDK


def test_schedule_fire_uses_the_configured_default_model(
    configured_default, fake_sdk, monkeypatch
):
    monkeypatch.setattr(runner.sinks, "validate", lambda *a: None)
    monkeypatch.setattr(runner.sinks, "dispatch", lambda *a: None)
    schedule = Schedule(name="t", cron="0 7 1 1 *", prompt="Reply with: hi")

    assert runner.fire(schedule) == "hi"
    assert fake_sdk.configs[0].model == configured_default


def test_bridge_chat_uses_the_configured_default_model(configured_default, fake_sdk):
    bridge = GAIAMCPBridge(port=0, base_url="http://127.0.0.1:1/api/v1")

    assert bridge._execute_chat({"query": "hi"})["success"] is True
    assert fake_sdk.configs[0].model == configured_default


class _FakeLLM:
    def __init__(self):
        self.models = []

    def generate(self, prompt, model=None, **_):
        self.models.append(model)
        return "hi"


def test_bridge_llm_uses_the_configured_default_model(configured_default):
    bridge = GAIAMCPBridge(port=0, base_url="http://127.0.0.1:1/api/v1")
    bridge.llm_client = _FakeLLM()

    assert bridge._execute_query({"query": "hi"})["result"] == "hi"
    assert bridge.llm_client.models == [configured_default]


def test_bridge_llm_keeps_a_model_the_caller_named(configured_default):
    bridge = GAIAMCPBridge(port=0, base_url="http://127.0.0.1:1/api/v1")
    bridge.llm_client = _FakeLLM()

    bridge._execute_query({"query": "hi", "model": "Gemma-4-E4B-it-GGUF"})
    assert bridge.llm_client.models == ["Gemma-4-E4B-it-GGUF"]


def test_telegram_session_uses_the_configured_default_model(
    configured_default, monkeypatch
):
    from gaia.messaging import telegram

    seen = []
    monkeypatch.setattr(telegram, "AgentSDK", lambda config: seen.append(config))
    monkeypatch.setattr(telegram, "_USER_SESSIONS", {})

    telegram.get_or_create_session(42)
    assert seen[0].model == configured_default


def test_unset_default_model_keeps_the_local_default(tmp_path, fake_sdk, monkeypatch):
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(runner.sinks, "validate", lambda *a: None)
    monkeypatch.setattr(runner.sinks, "dispatch", lambda *a: None)

    runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt="hi"))
    assert fake_sdk.configs[0].model == DEFAULT_MODEL_NAME
