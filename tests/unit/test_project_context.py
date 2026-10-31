# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which models get a repository's CLAUDE.md, AGENTS.md and skills.

Measured: Claude gains from them, DeepSeek V4.1 Flash only pays for them. So the
default is per model, unknown models get nothing, and the user can override it.
"""

from __future__ import annotations

import importlib
import json

import pytest

from gaia.project_context import (
    AUTO,
    FULL,
    OFF,
    PROJECT_CONTEXT_ENV,
    ProjectContextError,
    model_family,
    normalize_project_context,
    project_context_setting,
    wants_full_project_context,
)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(PROJECT_CONTEXT_ENV, raising=False)


def _config():
    """gaia.config as it is now: other tests reload it, which replaces its classes."""
    return importlib.import_module("gaia.config")


@pytest.mark.parametrize(
    "model, family",
    [
        ("claude-opus-5", "claude"),
        ("claude-sonnet-5", "claude"),
        ("amd.Claude-Opus-5-5", "claude"),
        ("amd.claude-opus-5.5", "claude"),
        ("anthropic/claude-sonnet-5", "claude"),
        ("us.anthropic.claude-sonnet-5-v1:0", "claude"),
        ("amd.DeepSeek-V4.1-Flash", "deepseek"),
        ("fireworks.deepseek-v4p1-flash", "deepseek"),
        ("Gemma-4-E4B-it-GGUF", "gemma"),
        ("Qwen3.6-35B-A3B-GGUF", "qwen3"),
        ("user.Qwen3.8-Flash-Next-GGUF", "qwen3"),
        ("", ""),
        (None, ""),
    ],
)
def test_model_family(model, family):
    assert model_family(model) == family


@pytest.mark.parametrize(
    "model",
    [
        "amd.DeepSeek-V4.1-Flash",
        "fireworks.deepseek-v4p1-flash",
        "Gemma-4-E4B-it-GGUF",
        "Qwen3.6-35B-A3B-GGUF",
        "some-unknown-model",
        # Names that merely contain "claude" are not the family.
        "notclaude-7B-GGUF",
        "user.my-claude-distill-GGUF",
        "",
        None,
    ],
)
def test_auto_is_conservative_for_every_other_model(model):
    assert not wants_full_project_context(AUTO, model)


@pytest.mark.parametrize(
    "model, use_claude",
    [
        ("claude-opus-5", True),
        ("amd.Claude-Opus-5-5", False),  # the gateway, either client path
        ("amd.Claude-Opus-5-5", True),
        ("claude-sonnet-5", False),
    ],
)
def test_auto_gives_claude_the_full_context(model, use_claude):
    assert wants_full_project_context(AUTO, model, use_claude)


def test_the_anthropic_client_counts_as_claude_whatever_the_id():
    assert wants_full_project_context(AUTO, None, use_claude=True)


def test_full_and_off_override_the_model():
    assert wants_full_project_context(FULL, "amd.DeepSeek-V4.1-Flash")
    assert not wants_full_project_context(OFF, "claude-opus-5", use_claude=True)


@pytest.mark.parametrize("raw, word", [("FULL", FULL), (" off ", OFF), ("", AUTO)])
def test_values_are_normalized(raw, word):
    assert normalize_project_context(raw, "test", "") == word


@pytest.mark.parametrize("bad", ["on", "true", "1", "compact", "full;rm -rf /"])
def test_a_bad_env_value_names_the_variable_and_its_fix(monkeypatch, bad):
    monkeypatch.setenv(PROJECT_CONTEXT_ENV, bad)
    with pytest.raises(ProjectContextError) as e:
        project_context_setting()
    assert str(e.value).startswith(f"{PROJECT_CONTEXT_ENV}:")
    assert "auto, full, off" in str(e.value) and "or unset it" in str(e.value)


def test_precedence_is_explicit_then_env_then_config(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project_context": "off"}), encoding="utf-8")
    monkeypatch.setattr(
        _config().GaiaConfig, "config_path", staticmethod(lambda path=None: config)
    )
    assert project_context_setting() == OFF
    monkeypatch.setenv(PROJECT_CONTEXT_ENV, "full")
    assert project_context_setting() == FULL
    assert project_context_setting("auto") == AUTO


def test_unset_everywhere_is_auto(monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "config.json"
    monkeypatch.setattr(
        _config().GaiaConfig, "config_path", staticmethod(lambda path=None: missing)
    )
    assert project_context_setting() == AUTO


# ── gaia config ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, stored", [("full", "full"), ("OFF", "off")])
def test_config_set_stores_the_choice(value, stored):
    cfg = _config().GaiaConfig()
    cfg.set("project_context", value)
    assert cfg.project_context == stored


@pytest.mark.parametrize("value", ["auto", "", " AUTO "])
def test_config_set_auto_clears_it(value):
    cfg = _config().GaiaConfig(project_context="full")
    cfg.set("project_context", value)
    assert cfg.project_context is None


def test_config_set_rejects_a_bad_value():
    with pytest.raises(_config().GaiaConfigError, match="auto, full, off"):
        _config().GaiaConfig().set("project_context", "max")


def test_a_bad_value_in_config_json_fails_at_load_with_a_fix_that_works(tmp_path):
    """`gaia config set` reloads the file first, so it cannot be the remedy."""
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project_context": "everything"}), encoding="utf-8")
    with pytest.raises(_config().GaiaConfigError) as e:
        _config().GaiaConfig.load(config)
    assert str(config) in str(e.value)
    assert "Fix or delete that key in the file" in str(e.value)
    assert "gaia config set" not in str(e.value)
