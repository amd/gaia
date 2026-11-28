# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which features each model tier gets — the table, not an agent."""

import sys
from dataclasses import fields

import pytest

import gaia.config
from gaia.agents.base.model_features import (
    COMPACT,
    FEATURE_TOOLS,
    FULL,
    SPEECH_OUTPUT_TOOLS,
    TIER_ENV,
    TIERS,
    ModelFeatures,
    features_for_model,
    platform_withheld_prompt,
    platform_withheld_tools,
    withheld_tools,
)
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    MODELS,
    NPU_DEFAULT_MODEL_NAME,
)


@pytest.fixture(autouse=True)
def _follow_the_model(monkeypatch):
    monkeypatch.delenv(TIER_ENV, raising=False)


def test_gemma_gets_the_same_compact_tier_on_the_gpu_and_the_npu():
    assert features_for_model(DEFAULT_MODEL_NAME) is COMPACT
    assert features_for_model(NPU_DEFAULT_MODEL_NAME) is COMPACT


@pytest.mark.parametrize(
    "model_id", [LARGE_DEFAULT_MODEL_NAME, "some-model-gaia-does-not-list", None]
)
def test_larger_and_unlisted_models_get_every_feature(model_id):
    assert features_for_model(model_id) is FULL


def test_the_env_forces_a_tier_for_any_model(monkeypatch):
    monkeypatch.setenv(TIER_ENV, "full")
    assert features_for_model(DEFAULT_MODEL_NAME) is FULL
    monkeypatch.setenv(TIER_ENV, "Compact")
    assert features_for_model(LARGE_DEFAULT_MODEL_NAME) is COMPACT


def test_an_unknown_env_tier_fails_loudly(monkeypatch):
    monkeypatch.setenv(TIER_ENV, "tiny")
    with pytest.raises(ValueError, match="GAIA_FEATURE_TIER='tiny'"):
        features_for_model(DEFAULT_MODEL_NAME)


def test_every_listed_model_names_a_real_tier():
    bad = {k: m.feature_tier for k, m in MODELS.items() if m.feature_tier not in TIERS}
    assert not bad


def test_every_feature_names_its_tools():
    assert set(FEATURE_TOOLS) == {f.name for f in fields(ModelFeatures)}


def test_full_withholds_nothing_and_compact_withholds_every_feature_tool():
    assert withheld_tools(FULL) == frozenset()
    assert withheld_tools(COMPACT) == frozenset().union(*FEATURE_TOOLS.values())


def test_the_email_feature_names_every_email_tool():
    """A tool missed here stays on a compact model that was meant to have no email."""
    import ast
    import inspect
    import textwrap

    from gaia.agents.tools import email_tools

    source = inspect.getsource(email_tools.EmailToolsMixin.register_email_tools)
    tree = ast.parse(textwrap.dedent(source))
    registered = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any("tool" in ast.unparse(d) for d in node.decorator_list)
    }
    assert registered, "found no @tool functions in register_email_tools"
    assert registered == {t for t in FEATURE_TOOLS["email"] if "/" not in t}


def test_compact_withholds_every_registered_email_tool():
    assert FEATURE_TOOLS["email"] == frozenset(
        {
            "check_mailbox_access",
            "list_inbox",
            "search_email",
            "read_email",
            "list_mail_folders",
            "get_thread",
            "list_connected_mailboxes",
            "list_waiting_on_you",
            "send-email/list_drafts",
            "send-email/create_draft",
            "send-email/update_draft",
            "send-email/discard_draft",
            "send-email/send_draft",
            "send-email/send_now",
            "send-email/reply_to_message",
            "send-email/forward_message",
        }
    )


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_a_proven_platform_withholds_no_speech_tool(platform, monkeypatch):
    assert gaia.config.speech_output_unproven_reason(platform) is None
    monkeypatch.setattr(gaia.config, "_current_platform", lambda: platform)
    assert platform_withheld_tools() == {}
    assert platform_withheld_prompt() == ""


def test_a_mac_withholds_the_speech_tools_saying_why(monkeypatch):
    reason = gaia.config.speech_output_unproven_reason("darwin")
    assert "aren't offered on macOS" in reason
    assert "Windows and Linux" in reason and "issues/8831" in reason
    monkeypatch.setattr(gaia.config, "_current_platform", lambda: "darwin")
    assert platform_withheld_tools() == dict.fromkeys(SPEECH_OUTPUT_TOOLS, reason)


def test_a_mac_tells_the_model_speech_is_off_and_not_to_improvise_it(monkeypatch):
    monkeypatch.setattr(gaia.config, "_current_platform", lambda: "darwin")
    block = platform_withheld_prompt()
    # The reason the user is to be given, with where to follow the fix.
    assert gaia.config.speech_output_unproven_reason("darwin") in block
    assert "read aloud" in block and "dictate" in block
    assert "reply with that and stop" in block
    for banned in ("shell commands", "installs", "downloads", "other tools"):
        assert banned in block
    assert block.startswith("\n**") and block.endswith("\n")


@pytest.mark.speech_platform_gate
def test_this_machine_is_judged_by_its_real_platform():
    proven = sys.platform in gaia.config.GaiaConfig.TESTED_SPEECH_OUTPUT_PLATFORMS
    assert (gaia.config.speech_output_unproven_reason() is None) == proven
