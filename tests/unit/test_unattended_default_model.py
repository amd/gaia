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


class _Catalog:
    """Lemonade as the schedule runner sees it: records calls, answers the catalog."""

    calls = []
    downloaded = True

    def __init__(self, **kwargs):
        _Catalog.calls.append(("__init__", kwargs))

    def _is_cloud_model(self, model):
        _Catalog.calls.append(("_is_cloud_model", model))
        return False

    def get_model_info(self, model):
        _Catalog.calls.append(("get_model_info", model))
        return {"id": model, "size_gb": 5.6, "downloaded": _Catalog.downloaded}


@pytest.fixture
def catalog(monkeypatch):
    _Catalog.calls = []
    _Catalog.downloaded = True
    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient", _Catalog)
    monkeypatch.setattr(runner.sinks, "validate", lambda *a: None)
    monkeypatch.setattr(runner.sinks, "dispatch", lambda *a: None)
    return _Catalog


def _write_config(tmp_path, monkeypatch, **values):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(values))
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", config_file)


def test_unset_default_model_keeps_the_local_default(
    tmp_path, fake_sdk, catalog, monkeypatch
):
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "missing.json")

    runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt="hi"))
    assert fake_sdk.configs[0].model == DEFAULT_MODEL_NAME
    assert ("get_model_info", DEFAULT_MODEL_NAME) in catalog.calls


def test_schedule_fire_runs_the_model_the_apps_reopen_on(
    tmp_path, fake_sdk, catalog, monkeypatch
):
    """A PC set up in the app with a cloud model has only ``last_model`` (#7974)."""
    _write_config(tmp_path, monkeypatch, default_model=None, last_model=CONFIGURED)

    assert runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt="hi")) == "hi"
    assert fake_sdk.configs[0].model == CONFIGURED
    # A cloud model needs no catalog check and nothing on disk.
    assert catalog.calls == []


def test_schedule_fire_refuses_a_local_model_that_is_not_downloaded(
    tmp_path, fake_sdk, catalog, monkeypatch
):
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "missing.json")
    catalog.downloaded = False

    with pytest.raises(runner.ScheduleModelError) as excinfo:
        runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt="hi"))
    message = str(excinfo.value)
    assert message.startswith(f"{DEFAULT_MODEL_NAME} is not downloaded (5.6 GB)")
    assert "gaia config set default_model" in message
    # No agent session, so nothing asked the server to load (and fetch) it.
    assert fake_sdk.configs == []


def test_a_missing_last_model_points_at_the_app_not_default_model(
    tmp_path, fake_sdk, catalog, monkeypatch
):
    """``last_model`` wins over ``default_model``, so setting the default can't help."""
    _write_config(tmp_path, monkeypatch, last_model="Gemma-4-E4B-it-GGUF")
    catalog.downloaded = False

    with pytest.raises(runner.ScheduleModelError) as excinfo:
        runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt="hi"))
    assert "Pick a model you have in the GAIA app" in str(excinfo.value)
    assert "default_model" not in str(excinfo.value)


# --- a ``hybrid`` pick routes each scheduled run, as a chat turn does -------

CHEAP = "amd.DeepSeek-V4.1-Flash"
STRONG = "amd.Claude-Opus-5-5"


@pytest.fixture
def classifier(monkeypatch):
    """The hybrid classifier's answer for the next run; records each prompt."""
    from gaia.llm import hybrid

    asked = []
    answer = {"label": ("write_prose", 1, "low")}

    def classify(_router, text, base_url=None):
        asked.append(text)
        label = answer["label"]
        if isinstance(label, BaseException):
            raise label
        kind, difficulty, stakes = label
        return {
            "kind": kind,
            "difficulty": difficulty,
            "stakes": stakes,
            "reason": "r",
            "format": hybrid.FORMAT_JSON,
        }

    monkeypatch.setattr(hybrid.HybridRouter, "classify", classify)
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    hybrid.reset_classifier_health()
    yield answer, asked
    hybrid.reset_classifier_health()


def _fire(prompt="Summarise my notes."):
    return runner.fire(Schedule(name="t", cron="0 7 1 1 *", prompt=prompt))


@pytest.mark.parametrize(
    "config",
    [{"last_model": "hybrid"}, {"default_model": "hybrid"}],
    ids=["last_model", "default_model"],
)
def test_a_routine_scheduled_run_on_hybrid_runs_on_the_cheap_model(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch, config
):
    _write_config(tmp_path, monkeypatch, **config)
    _answer, asked = classifier

    _fire()

    assert asked == ["Summarise my notes."]
    assert fake_sdk.configs[0].model == CHEAP


def test_a_hard_scheduled_run_on_hybrid_runs_on_the_strong_model(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    _write_config(tmp_path, monkeypatch, last_model="hybrid")
    answer, _asked = classifier
    answer["label"] = ("find_defects", 4, "high")

    _fire("Audit the deploy script.")

    assert fake_sdk.configs[0].model == STRONG


def test_a_failed_classification_runs_the_scheduled_run_strong(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    """Fail closed, as chat does: never quietly fall to the cheap model."""
    _write_config(tmp_path, monkeypatch, last_model="hybrid")
    answer, _asked = classifier
    answer["label"] = TimeoutError("classifier timed out")

    _fire()

    assert fake_sdk.configs[0].model == STRONG


def test_scheduled_runs_on_hybrid_do_not_stick_to_strong(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    """Each run is its own session: a hard one doesn't pin the next."""
    _write_config(tmp_path, monkeypatch, last_model="hybrid")
    answer, _asked = classifier
    answer["label"] = ("find_defects", 4, "high")
    _fire("Audit it.")
    answer["label"] = ("write_prose", 1, "low")
    _fire("Summarise it.")

    assert [c.model for c in fake_sdk.configs] == [STRONG, CHEAP]


def test_local_only_refuses_a_cloud_hybrid_scheduled_run(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    _write_config(tmp_path, monkeypatch, last_model="hybrid")
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: True)
    _answer, asked = classifier

    with pytest.raises(RuntimeError, match="Local only is on"):
        _fire()
    assert asked == [] and fake_sdk.configs == []


def test_a_routed_local_model_that_is_not_downloaded_names_its_hybrid_setting(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    _write_config(
        tmp_path,
        monkeypatch,
        last_model="hybrid",
        hybrid_cheap_model="Gemma-4-E4B-it-GGUF",
        hybrid_strong_model=STRONG,
    )
    catalog.downloaded = False

    with pytest.raises(runner.ScheduleModelError) as excinfo:
        _fire()
    message = str(excinfo.value)
    assert message.startswith("Gemma-4-E4B-it-GGUF is not downloaded")
    assert "gaia config set hybrid_cheap_model" in message
    assert "default_model" not in message
    assert fake_sdk.configs == []


def test_a_broken_hybrid_config_fails_the_scheduled_run(
    tmp_path, fake_sdk, catalog, classifier, monkeypatch
):
    from gaia.llm.hybrid import HybridConfigError

    _write_config(
        tmp_path,
        monkeypatch,
        last_model="hybrid",
        hybrid_cheap_model=STRONG,
        hybrid_strong_model=STRONG,
    )

    with pytest.raises(HybridConfigError, match="nothing to route between"):
        _fire()
    assert fake_sdk.configs == []
