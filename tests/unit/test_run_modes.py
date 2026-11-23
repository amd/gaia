# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run modes (``gaia.run_modes``): the saved format, defaults and the CLI switch."""

import json
from argparse import Namespace

import pytest

from gaia import config as config_mod
from gaia import run_modes
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.llm import cloud_keys, local_only
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

DEEPSEEK = "fireworks.deepseek-v4p1-flash"
GLM = "fireworks.glm-5p3-flash"


@pytest.fixture(autouse=True)
def _empty_config(monkeypatch):
    config_mod.GAIA_CONFIG_FILE.unlink(missing_ok=True)
    for name in ("FIREWORKS_API_KEY", "LEMONADE_FIREWORKS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(local_only, "cloud_blocked", lambda: False)
    yield
    config_mod.GAIA_CONFIG_FILE.unlink(missing_ok=True)


def _write(data):
    config_mod.GAIA_CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


def _read():
    return json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))


def test_defaults_per_mode():
    assert run_modes.default_chat("local") == DEFAULT_MODEL_NAME
    assert run_modes.default_chat("cloud") == DEEPSEEK
    _write({"default_model": "Qwen3.6-35B-A3B-GGUF"})
    assert run_modes.default_chat("local") == "Qwen3.6-35B-A3B-GGUF"


def test_a_cloud_default_model_is_never_locals_default():
    _write({"default_model": "amd.DeepSeek-V4.1-Flash"})
    assert run_modes.default_chat("local") == DEFAULT_MODEL_NAME


def test_an_unknown_mode_names_the_valid_ones():
    with pytest.raises(run_modes.RunModeError, match="local, cloud"):
        run_modes.default_chat("hybrid")


def test_a_mode_set_to_its_default_stores_nothing():
    assert run_modes.with_chat({}, "cloud", DEEPSEEK) == {}
    # Fireworks' long id for the same model is still the default.
    long_id = "fireworks.accounts/fireworks/models/deepseek-v4p1-flash"
    assert run_modes.with_chat({"cloud": {"chat": GLM}}, "cloud", long_id) == {}
    assert run_modes.with_chat({}, "cloud", GLM) == {"cloud": {"chat": GLM}}
    kept = run_modes.with_chat({"local": {"chat": "X-GGUF"}}, "cloud", GLM)
    assert kept == {"local": {"chat": "X-GGUF"}, "cloud": {"chat": GLM}}


def test_record_pick_moves_the_mode_with_the_model():
    doc = {"run_profiles": {"local": {"chat": "X-GGUF"}}}
    run_modes.record_pick(doc, GLM, remote=True)
    assert doc == {
        "run_mode": "cloud",
        "run_profiles": {"local": {"chat": "X-GGUF"}, "cloud": {"chat": GLM}},
    }
    run_modes.record_pick(doc, DEFAULT_MODEL_NAME, remote=False)
    assert doc == {"run_mode": "local", "run_profiles": {"cloud": {"chat": GLM}}}


def test_config_loads_the_saved_modes():
    _write({"run_mode": "cloud", "run_profiles": {"cloud": {"chat": GLM}}})
    config = GaiaConfig.load()
    assert config.run_mode == "cloud"
    assert run_modes.chat_for(config.run_profiles, "cloud") == GLM
    assert run_modes.chat_for(config.run_profiles, "local") == DEFAULT_MODEL_NAME


@pytest.mark.parametrize(
    "doc",
    [
        {"run_mode": "hybrid"},
        {"run_profiles": {"hybrid": {"chat": "x"}}},
        {"run_profiles": {"cloud": {"chat": ""}}},
        {"run_profiles": {"cloud": {"speech": "x"}}},
        {"run_profiles": ["cloud"]},
    ],
)
def test_a_malformed_saved_mode_is_reported_not_guessed(doc):
    _write(doc)
    with pytest.raises(GaiaConfigError, match="run_mode|run_profiles|run mode"):
        GaiaConfig.load()


def _set(key, value):
    from gaia.cli import handle_config_command

    handle_config_command(
        Namespace(config=None, config_action="set", key=key, value=value)
    )


def test_cli_switch_to_cloud_writes_what_every_surface_reopens_on(monkeypatch, capsys):
    _write({"last_model": "Qwen3.6-35B-A3B-GGUF", "full_access": True})
    monkeypatch.setattr(cloud_keys, "recall_key", lambda provider: "kept")
    _set("run_mode", "cloud")
    assert _read() == {
        "last_model": DEEPSEEK,
        "last_provider": "fireworks",
        "full_access": True,
        "run_mode": "cloud",
        # Local keeps the model it was on, as a switch in the Agent UI does.
        "run_profiles": {"local": {"chat": "Qwen3.6-35B-A3B-GGUF"}},
    }
    assert DEEPSEEK in capsys.readouterr().out


def test_cli_switch_to_a_gateway_cloud_model_asks_for_no_fireworks_key(monkeypatch):
    _write({"run_profiles": {"cloud": {"chat": "amd.DeepSeek-V4.1-Flash"}}})
    monkeypatch.setattr(cloud_keys, "recall_key", lambda provider: None)
    _set("run_mode", "cloud")
    saved = _read()
    assert saved["last_model"] == "amd.DeepSeek-V4.1-Flash"
    assert saved["last_provider"] == "amd"
    assert saved["run_mode"] == "cloud"


def test_cli_switch_back_to_local_uses_the_saved_local_model(monkeypatch):
    _write(
        {
            "last_model": GLM,
            "run_mode": "cloud",
            "run_profiles": {"local": {"chat": "Qwen3.6-35B-A3B-GGUF"}},
        }
    )
    _set("run_mode", "local")
    saved = _read()
    assert saved["last_model"] == "Qwen3.6-35B-A3B-GGUF"
    assert saved["last_provider"] == "local"
    assert saved["run_mode"] == "local"


def test_cli_refuses_cloud_without_a_key_and_changes_nothing(monkeypatch, capsys):
    _write({"last_model": "Qwen3.6-35B-A3B-GGUF"})
    monkeypatch.setattr(cloud_keys, "recall_key", lambda provider: None)
    with pytest.raises(SystemExit):
        _set("run_mode", "cloud")
    assert "FIREWORKS_API_KEY" in capsys.readouterr().err
    assert _read() == {"last_model": "Qwen3.6-35B-A3B-GGUF"}


def test_cli_refuses_cloud_under_local_only(monkeypatch, capsys):
    monkeypatch.setattr(local_only, "cloud_blocked", lambda: True)
    with pytest.raises(SystemExit):
        _set("run_mode", "cloud")
    assert "network_policy" in capsys.readouterr().err
    assert not config_mod.GAIA_CONFIG_FILE.exists()


def test_cli_refuses_an_unknown_mode_and_hand_edited_profiles(capsys):
    with pytest.raises(SystemExit):
        _set("run_mode", "hybrid")
    assert "local, cloud" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        _set("run_profiles", "{}")
    assert "Settings › Run mode" in capsys.readouterr().err


def test_the_tui_mirrors_the_run_mode_rules():
    """The TUI keeps its own copy of these rules (preflight/runmodes.go)."""
    import re
    from pathlib import Path

    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    go = (
        Path(__file__).resolve().parents[2]
        / "tui"
        / "internal"
        / "ui"
        / "preflight"
        / "runmodes.go"
    ).read_text(encoding="utf-8")
    consts = dict(re.findall(r'^\s*(\w+)\s*=\s*"([^"]*)"', go, re.MULTILINE))
    assert consts["LocalMode"] == run_modes.LOCAL
    assert consts["CloudMode"] == run_modes.CLOUD
    assert consts["DefaultLocalModel"] == DEFAULT_MODEL_NAME
    assert re.search(r"RunModes = \[\]string\{LocalMode, CloudMode\}", go)
    assert list(run_modes.MODES) == [run_modes.LOCAL, run_modes.CLOUD]


@pytest.mark.parametrize(
    "last_model, mode",
    [("Qwen3.5-4B-GGUF", "local"), ("fireworks.kimi-k3", "cloud")],
)
def test_cli_switch_to_the_mode_in_use_keeps_the_model(last_model, mode, capsys):
    # An install from before run modes: a last_model and no run_profiles.
    before = {"default_model": "Gemma-4-E4B-it-GGUF", "last_model": last_model}
    _write(before)
    _set("run_mode", mode)
    assert _read() == before
    assert f"Already in {mode}: chat runs {last_model}" in capsys.readouterr().out
