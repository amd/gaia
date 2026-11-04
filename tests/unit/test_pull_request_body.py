# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial shape checks for ``pull_request_body``, the one ``/pull`` body.

Built-ins must go out name-only (a ``recipe`` 400s, #1655); ``user.`` models
must carry ``checkpoint`` + ``recipe`` (name-only 500s on a cold cache).
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from fastapi import HTTPException

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import MODELS, pull_request_body, record_cloud_models
from gaia.ui.routers import system

_BUILTINS = sorted(
    {m.model_id for m in MODELS.values() if not m.model_id.startswith("user.")}
)
_CUSTOM = sorted(
    {m.model_id for m in MODELS.values() if m.model_id.startswith("user.")}
)
_REGISTRATION_KEYS = {
    "checkpoint",
    "recipe",
    "embedding",
    "mmproj",
    "vision",
    "reasoning",
}


@pytest.fixture(autouse=True)
def _empty_cloud_catalog():
    record_cloud_models({"data": []})
    yield
    record_cloud_models({"data": []})


@pytest.mark.parametrize("model_id", _BUILTINS)
def test_every_builtin_is_pulled_by_name_only(model_id):
    assert pull_request_body(model_id) == {"model_name": model_id}


@pytest.mark.parametrize("model_id", _CUSTOM)
def test_every_custom_model_registers_under_its_user_name(model_id):
    body = pull_request_body(model_id)
    assert body["model_name"] == model_id
    assert body["checkpoint"] and body["recipe"]
    assert set(body) - {"model_name"} <= _REGISTRATION_KEYS
    assert None not in body.values()


@pytest.mark.parametrize("model_id", _CUSTOM)
def test_listed_name_without_namespace_still_registers(model_id):
    body = pull_request_body(model_id[len("user.") :])
    assert body["model_name"] == model_id
    assert body["checkpoint"] and body["recipe"]


def test_user_prefixed_builtin_goes_out_as_the_builtin_name_only():
    """``_model_ids_match`` strips ``user.``; the body must not invent a registration."""
    body = pull_request_body("user." + lc.DEFAULT_MODEL_NAME)
    assert body == {"model_name": lc.DEFAULT_MODEL_NAME}


def test_miscased_builtin_is_sent_under_its_registered_name():
    body = pull_request_body(lc.DEFAULT_MODEL_NAME.lower())
    assert body == {"model_name": lc.DEFAULT_MODEL_NAME}


@pytest.mark.parametrize("model_id", ["user.not-a-real-model", "USER.not-a-real-model"])
def test_unknown_user_model_raises_instead_of_a_name_only_pull(model_id):
    with pytest.raises(ValueError, match="not in GAIA's model registry"):
        pull_request_body(model_id)


def test_unknown_non_user_model_is_left_to_lemonade_by_name():
    assert pull_request_body("Some-Lemonade-Builtin-GGUF") == {
        "model_name": "Some-Lemonade-Builtin-GGUF"
    }


def test_custom_entry_missing_its_checkpoint_raises(monkeypatch):
    broken = dataclasses.replace(MODELS["embeddinggemma"], checkpoint=None)
    monkeypatch.setattr(lc, "find_model_requirement", lambda _id: broken)
    with pytest.raises(ValueError, match="checkpoint and recipe are unknown"):
        pull_request_body(broken.model_id)


@pytest.mark.parametrize("model_id", ["amd.Claude-Opus-5", "fireworks.some-model"])
def test_known_gateway_namespace_is_refused_on_a_cold_catalog(model_id):
    with pytest.raises(RuntimeError, match="gateway"):
        pull_request_body(model_id)


def test_discovered_gateway_model_is_refused():
    record_cloud_models({"data": [{"id": "other.remote", "recipe": "cloud"}]})
    with pytest.raises(RuntimeError, match="gateway"):
        pull_request_body("other.remote")


def test_dotted_local_name_is_not_mistaken_for_a_gateway():
    assert pull_request_body("Qwen2.5-0.5B-Instruct-GGUF") == {
        "model_name": "Qwen2.5-0.5B-Instruct-GGUF"
    }


@pytest.mark.parametrize("model_id", ["amd.Claude-Opus-5", "user.not-a-real-model"])
def test_download_endpoint_refuses_up_front(model_id, monkeypatch):
    """The button gets a 400 it can show, not a 202 for a pull that never runs."""
    started = []
    monkeypatch.setattr(system, "_stream_lemonade_pull", lambda *a: started.append(a))
    req = system.DownloadModelRequest(model_name=model_id)
    with pytest.raises(HTTPException) as err:
        asyncio.run(system.download_model_endpoint(req))
    assert err.value.status_code == 400
    assert started == []
