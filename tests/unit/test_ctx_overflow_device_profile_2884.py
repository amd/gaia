# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A context overflow must be judged against the machine's own profile (#2884).

Both context-overflow classifiers decide one thing: was the model loaded at
the *wrong* (too small) ctx — in which case a reload fixes it and the error is
``retryable`` — or is the conversation genuinely too big for a correctly
loaded model? They answered that by comparing the reported ``n_ctx`` against a
hardcoded 65536, which is the GPU/CPU profile's window. On NPU a *correctly*
loaded model sits at ``NPU_CTX_SIZE`` (32768), so the comparison was
unconditionally true there: every real NPU overflow was labelled retryable,
the chat layer announced "Model reloaded — retrying...", replayed the identical
oversized turn, and only then surfaced an error. The user is told the model was
reloaded when nothing was wrong with the load.

The threshold is now derived from the active device profile
(``active_profile_ctx_size``), not from either constant hardcoded in place.
"""

from __future__ import annotations

import json

import pytest

from gaia import config as config_mod
from gaia.config import GaiaConfigError
from gaia.llm.lemonade_client import (
    GPU_CTX_SIZE,
    NPU_CTX_SIZE,
    active_profile_ctx_size,
)
from gaia.llm.providers.lemonade import (
    LemonadeContextOverflowError,
    _classify_lemonade_response,
)
from gaia.ui._chat_helpers import _classify_chat_exception


def _set_device(device: str) -> None:
    """Persist ``default_device`` the way ``gaia config set`` does.

    Writes the real file (``tests/unit/conftest.py`` already redirects
    ``GAIA_CONFIG_FILE`` into ``tmp_path``) so the load path under test is the
    production one, not a mocked accessor.
    """
    config_mod.GAIA_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"default_device": device}), encoding="utf-8"
    )


def _overflow_payload(n_ctx: int) -> dict:
    """A Lemonade ``exceed_context_size`` envelope reporting *n_ctx*."""
    return {
        "error": {
            "type": "exceed_context_size",
            "message": (
                f"the request exceeds the available context size ({n_ctx} tokens)"
            ),
            "n_ctx": n_ctx,
        }
    }


def _overflow_exception(n_ctx: int) -> Exception:
    """The same condition as a stringified exception (the UI-side input)."""
    return RuntimeError(
        f"request (99999 tokens) exceeds the available context size ({n_ctx} tokens)"
    )


# ── active_profile_ctx_size ─────────────────────────────────────────────


def test_active_profile_ctx_size_follows_the_npu_profile() -> None:
    _set_device("npu")
    assert active_profile_ctx_size() == NPU_CTX_SIZE


@pytest.mark.parametrize("device", ["gpu", "cpu"])
def test_active_profile_ctx_size_follows_the_non_npu_profiles(device: str) -> None:
    _set_device(device)
    assert active_profile_ctx_size() == GPU_CTX_SIZE


def test_active_profile_ctx_size_defaults_when_no_config_written() -> None:
    """A fresh install has no config file; that is not an error."""
    assert active_profile_ctx_size() == GPU_CTX_SIZE


def test_active_profile_ctx_size_raises_actionably_on_corrupt_config() -> None:
    """A corrupt config must not be guessed past — the device decides the ctx."""
    config_mod.GAIA_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    config_mod.GAIA_CONFIG_FILE.write_text("{not json", encoding="utf-8")

    with pytest.raises(GaiaConfigError) as excinfo:
        active_profile_ctx_size()

    message = str(excinfo.value)
    assert str(config_mod.GAIA_CONFIG_FILE) in message, "names the file that failed"
    assert "gaia config set default_device" in message, "names the fix"


# ── Provider side: response-payload classification ──────────────────────


def test_provider_npu_overflow_at_the_profile_window_is_not_retryable() -> None:
    """32768 on NPU is a correct load — retrying cannot help."""
    _set_device("npu")

    err, is_error = _classify_lemonade_response(_overflow_payload(NPU_CTX_SIZE))

    assert is_error is True
    assert isinstance(err, LemonadeContextOverflowError)
    assert err.retryable is False, (
        "a correctly loaded NPU model reported its real window; labelling this "
        "retryable makes the UI claim it reloaded the model for nothing"
    )


def test_provider_npu_overflow_below_the_profile_window_is_retryable() -> None:
    """8192 on NPU really is an undersized load — a reload fixes it."""
    _set_device("npu")

    err, _ = _classify_lemonade_response(_overflow_payload(8192))

    assert err.retryable is True


def test_provider_gpu_overflow_at_the_npu_window_is_retryable() -> None:
    """32768 on a GPU box IS an undersized load — the NPU constant is not a
    stand-in for the GPU threshold."""
    _set_device("gpu")

    err, _ = _classify_lemonade_response(_overflow_payload(NPU_CTX_SIZE))

    assert err.retryable is True


def test_provider_gpu_threshold_is_the_gpu_profile_window() -> None:
    _set_device("gpu")

    assert _classify_lemonade_response(_overflow_payload(GPU_CTX_SIZE - 1))[
        0
    ].retryable is True
    assert _classify_lemonade_response(_overflow_payload(GPU_CTX_SIZE))[
        0
    ].retryable is False


# ── UI side: stringified-exception classification ───────────────────────


def test_ui_npu_overflow_at_the_profile_window_is_not_retryable() -> None:
    _set_device("npu")

    classified = _classify_chat_exception(_overflow_exception(NPU_CTX_SIZE))

    assert isinstance(classified, LemonadeContextOverflowError)
    assert classified.retryable is False, (
        "the non-streaming and streaming chat paths both gate their "
        "reload-and-replay on this flag"
    )


def test_ui_npu_overflow_below_the_profile_window_is_retryable() -> None:
    _set_device("npu")

    assert _classify_chat_exception(_overflow_exception(4096)).retryable is True


def test_ui_gpu_overflow_at_the_npu_window_is_retryable() -> None:
    _set_device("gpu")

    assert (
        _classify_chat_exception(_overflow_exception(NPU_CTX_SIZE)).retryable is True
    )


def test_ui_gpu_threshold_is_the_gpu_profile_window() -> None:
    _set_device("gpu")

    assert (
        _classify_chat_exception(_overflow_exception(GPU_CTX_SIZE - 1)).retryable
        is True
    )
    assert (
        _classify_chat_exception(_overflow_exception(GPU_CTX_SIZE)).retryable is False
    )


# ── The plain-CLI path has to say something actionable ──────────────────
#
# ``gaia chat "<message>"`` printed ``str(exc)`` verbatim, so a backend
# overflow arrived as a bare ``❌ Error: Max length reached!`` — no cause, no
# next step. The UI path had the classifier; the CLI could not import it,
# because it lives behind the fastapi-dependent ``gaia.ui`` package.


def test_classifier_is_importable_without_the_ui_extras() -> None:
    """The CLI must reach it without pulling in gaia.ui (fastapi)."""
    from gaia.llm.providers.lemonade import classify_lemonade_exception

    classified = classify_lemonade_exception(
        RuntimeError("the request exceeds the available context size (32768 tokens)")
    )
    assert isinstance(classified, LemonadeContextOverflowError)


def test_ui_helper_still_exposes_the_classifier() -> None:
    """Existing ``gaia.ui`` callers keep the name they import today."""
    from gaia.llm.providers.lemonade import classify_lemonade_exception

    assert _classify_chat_exception is classify_lemonade_exception


def _run_cli_chat_raising(exc: Exception, capsys) -> str:
    """Drive ``GaiaCliClient.chat`` to its error path and return what it printed."""
    import logging

    from gaia import cli as cli_mod

    client = object.__new__(cli_mod.GaiaCliClient)
    client.log = logging.getLogger("test_cli_chat")

    class _RaisingSDK:
        def __init__(self, *_args, **_kwargs):
            raise exc

    original = __import__("gaia.chat.sdk", fromlist=["AgentSDK"])
    saved = original.AgentSDK
    original.AgentSDK = _RaisingSDK
    try:
        with pytest.raises(SystemExit):
            client.chat(message="hello")
    finally:
        original.AgentSDK = saved
    return capsys.readouterr().out


def test_cli_chat_surfaces_the_typed_message_not_the_backend_string(capsys) -> None:
    _set_device("npu")

    out = _run_cli_chat_raising(
        RuntimeError(
            "request (67000 tokens) exceeds the available context size "
            f"({NPU_CTX_SIZE} tokens)"
        ),
        capsys,
    )

    assert "context window" in out, "names what failed"
    assert "fresh task" in out, "names what to do next"


def test_cli_chat_still_prints_an_unclassifiable_error(capsys) -> None:
    """Nothing is swallowed — an unrelated failure keeps its own text."""
    out = _run_cli_chat_raising(RuntimeError("disk on fire"), capsys)

    assert "disk on fire" in out


# ── The non-retryable message has to stand on its own ───────────────────


def test_non_retryable_overflow_message_names_the_constraint_and_next_step() -> None:
    """With no reload to fall back on, this text is all the user gets."""
    _set_device("npu")

    err, _ = _classify_lemonade_response(_overflow_payload(NPU_CTX_SIZE))

    assert "context window" in err.user_message
    assert "fresh task" in err.user_message
