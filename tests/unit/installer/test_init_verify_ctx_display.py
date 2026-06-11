# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the per-model context display in `gaia init` verification.

Issue #318: SD/embedding models (which never run context verification) were
inheriting a stale "context unverified" flag from __init__, so the verification
summary printed a misleading "⚠️ Context unverified!" next to a model that was
fine. The warning must appear only for LLM models where verification was
actually attempted and lemonade did not report a context size back.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.installer import init_command as init_mod
from gaia.installer.init_command import InitCommand


@pytest.fixture(autouse=True)
def _isolate_remote_env(monkeypatch):
    """Strip LEMONADE_BASE_URL so InitCommand doesn't auto-flip to remote mode."""
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)


def _run_verify(profile, inference_behavior, models=None):
    """Drive _verify_setup with a stubbed client and capture printed text.

    inference_behavior maps model_id -> a callable(cmd) that emulates what
    _test_model_inference does to cmd._ctx_verified, returning (success, error).
    `models` optionally overrides the profile's model list (and its order).
    """
    cmd = InitCommand(profile=profile, yes=True)
    cmd.console = MagicMock()

    client_instance = MagicMock()
    client_instance.health_check.return_value = {"status": "ok"}
    client_instance.check_model_available.return_value = True

    def _fake_inference(client, model_id):
        return inference_behavior[model_id](cmd)

    profile_override = dict(init_mod.INIT_PROFILES[profile])
    if models is not None:
        profile_override["models"] = list(models)

    with (
        patch.dict(init_mod.INIT_PROFILES, {profile: profile_override}, clear=False),
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client_instance),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready", return_value=True
        ),
        patch.object(cmd, "_test_model_inference", side_effect=_fake_inference),
    ):
        cmd._verify_setup()

    printed = "\n".join(
        str(c.args[0]) for c in cmd.console.print.call_args_list if c.args
    )
    return printed


def test_sd_model_first_does_not_show_context_unverified():
    """An SD model verified first must not inherit a stale 'unverified' flag.

    SDXL-Turbo never runs context verification (not an LLM), so _ctx_verified
    is left unset; the display must show a clean OK, not the warning.
    """

    def sd_ok(_cmd):
        # SD path in _test_model_inference never touches _ctx_verified.
        return (True, None)

    def llm_ok(cmd):
        cmd._ctx_verified = 32768
        return (True, None)

    printed = _run_verify(
        "sd",
        {"SDXL-Turbo": sd_ok, "Gemma-4-E4B-it-GGUF": llm_ok},
    )

    assert "Context unverified" not in printed
    assert "SDXL-Turbo" in printed
    assert "ctx: 32768" in printed


def test_llm_without_reported_ctx_shows_unverified():
    """When lemonade reports no ctx for an LLM, the warning must still appear."""

    def llm_no_ctx(cmd):
        # Mirrors the "ctx not in recipe_options" branch.
        cmd._ctx_verified = None
        return (True, None)

    def sd_ok(_cmd):
        return (True, None)

    printed = _run_verify(
        "sd",
        {"SDXL-Turbo": sd_ok, "Gemma-4-E4B-it-GGUF": llm_no_ctx},
    )

    assert "Context unverified" in printed


def test_ctx_state_does_not_leak_between_models():
    """A verified ctx on one model must not bleed onto a later N/A model."""

    def llm_ok(cmd):
        cmd._ctx_verified = 32768
        return (True, None)

    def sd_ok(_cmd):
        return (True, None)

    # Gemma (LLM, ctx verified) precedes SDXL (SD, N/A) — forced order.
    printed = _run_verify(
        "sd",
        {"Gemma-4-E4B-it-GGUF": llm_ok, "SDXL-Turbo": sd_ok},
        models=["Gemma-4-E4B-it-GGUF", "SDXL-Turbo"],
    )

    # The ctx line belongs only to the LLM; the SD line after it stays clean.
    sd_line = next(line for line in printed.splitlines() if "SDXL-Turbo" in line)
    assert "ctx:" not in sd_line
    assert "Context unverified" not in sd_line
