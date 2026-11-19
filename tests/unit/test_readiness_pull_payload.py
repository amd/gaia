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


def _sent(model_id, registered=()):
    with patch("requests.post") as post, patch("requests.get") as get:
        post.return_value.raise_for_status.return_value = None
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {"data": [{"id": i} for i in registered]}
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
    with pytest.raises(ValueError, match="Add a model from Hugging Face"):
        _sent("user.not-a-real-model")


def test_a_user_model_registered_in_lemonade_directly_is_pulled_by_name():
    """Lemonade already holds its checkpoint, so name only is what it accepts."""
    with patch("requests.post") as post, patch("requests.get") as get:
        post.return_value.raise_for_status.return_value = None
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {"data": [{"id": "user.My-Own-GGUF"}]}
        pull_model(BASE, "user.My-Own-GGUF")
    assert get.call_args.args[0] == f"{BASE}/models"
    assert get.call_args.kwargs["params"] == {"show_all": "true"}
    assert post.call_args.kwargs["json"] == {"model_name": "user.My-Own-GGUF"}


def test_a_user_model_lemonade_lists_without_its_prefix_is_pulled_by_name():
    """``/models`` lists it bare; non-object entries are skipped, not a crash."""
    with patch("requests.post") as post, patch("requests.get") as get:
        post.return_value.raise_for_status.return_value = None
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {
            "data": [None, "x", {"id": "My-Own-GGUF"}]
        }
        pull_model(BASE, "user.my-own-gguf")
    payload = post.call_args.kwargs["json"]
    assert payload == {"model_name": "user.My-Own-GGUF"}
    assert "recipe" not in payload and "checkpoint" not in payload


def test_an_unreadable_model_list_raises_value_error():
    with patch("requests.post") as post, patch("requests.get") as get:
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = ["My-Own-GGUF"]
        with pytest.raises(ValueError, match="no 'data' list"):
            pull_model(BASE, "user.My-Own-GGUF")
    assert post.call_count == 0


def test_a_known_user_model_does_not_list_models_first():
    with patch("requests.post") as post, patch("requests.get") as get:
        post.return_value.raise_for_status.return_value = None
        pull_model(BASE, "user.embeddinggemma-300m-GGUF")
    assert get.call_count == 0


def test_an_unknown_user_model_yields_a_failure_line_instead_of_raising():
    """``provision_progress`` promises a ✗ line on every failure path.

    ``pull_model`` signals an unregistered ``user.`` id with ``ValueError``,
    not ``RequestException``, so catching only the latter let it escape the
    generator and reach the caller as a crash.
    """
    with (
        patch("gaia.agents.base.readiness.probe_model_present", return_value=False),
        patch("requests.get") as get,
    ):
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {"data": []}
        lines = list(provision_progress(BASE, "user.not-a-real-model"))

    assert lines[-1].startswith("✗")
    assert any("Add a model from Hugging Face" in line for line in lines)
