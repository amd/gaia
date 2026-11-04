# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``pull_model`` must send the payload Lemonade actually accepts.

Lemonade wants OPPOSITE payloads for its two kinds of model, and the wrong one
fails only on a COLD cache — so a warm machine, and every mock that returns 200
without looking, reports success either way. These tests assert the *shape of
the outgoing request*, which is the part that differs.

The live failure: ``user.embeddinggemma-300m-GGUF`` pulled with name only
returns HTTP 500 ``not registered with Lemonade Server``, which made the TUI's
"press f to fix" unable to fix anything.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.readiness import provision_progress, pull_model

BASE = "http://localhost:61901/api/v1"


def _sent(model_id):
    with patch("requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        pull_model(BASE, model_id)
        assert post.call_count == 1
        return post.call_args.kwargs["json"]


def test_a_builtin_model_is_pulled_by_name_only():
    """Passing recipe makes Lemonade read it as a new registration and 400 (#1655)."""
    payload = _sent("Gemma-4-E4B-it-GGUF")
    assert payload == {"model_name": "Gemma-4-E4B-it-GGUF"}
    assert "recipe" not in payload


def test_a_user_model_carries_checkpoint_recipe_and_the_embedding_flag():
    """A user.-prefixed model is not registered yet, so the pull must register it."""
    payload = _sent("user.embeddinggemma-300m-GGUF")
    assert payload == {
        "model_name": "user.embeddinggemma-300m-GGUF",
        "checkpoint": "ggml-org/embeddinggemma-300M-GGUF:Q8_0",
        "recipe": "llamacpp",
        "embedding": True,
    }


def test_a_gateway_model_is_refused_before_any_request():
    """Lemonade 400s on a pull of a gateway model, and the message talks about a download."""
    from gaia.llm.lemonade_client import record_cloud_models

    record_cloud_models(
        {
            "data": [
                {
                    "id": "other.remote-chat",
                    "recipe": "cloud",
                    "labels": [],
                }
            ]
        }
    )
    try:
        with patch("requests.post") as post:
            with pytest.raises(RuntimeError, match="gateway"):
                pull_model(BASE, "other.remote-chat")
            assert post.call_count == 0
    finally:
        record_cloud_models({"data": []})


def test_a_known_gateway_namespace_is_refused_on_a_cold_catalog():
    """``amd.`` is a gateway even before this process has read /models."""
    from gaia.llm.lemonade_client import record_cloud_models

    record_cloud_models({"data": []})
    with patch("requests.post") as post:
        with pytest.raises(RuntimeError, match="gateway"):
            pull_model(BASE, "amd.Claude-Opus-5")
        assert post.call_count == 0


def test_an_unknown_user_model_fails_loudly_rather_than_sending_a_bad_pull():
    """Silently sending name-only here is what produced the confusing 500."""
    with pytest.raises(ValueError, match="not in GAIA's model registry"):
        _sent("user.not-a-real-model")


def test_an_unknown_user_model_yields_a_failure_line_instead_of_raising():
    """``provision_progress`` promises a ✗ line on every failure path.

    ``pull_model`` signals an unregistered ``user.`` id with ``ValueError``,
    not ``RequestException``, so catching only the latter let it escape the
    generator and reach the caller as a crash.
    """
    with patch("gaia.agents.base.readiness.probe_model_present", return_value=False):
        lines = list(provision_progress(BASE, "user.not-a-real-model"))

    assert lines[-1].startswith("✗")
    assert any("not in GAIA's model registry" in line for line in lines)
