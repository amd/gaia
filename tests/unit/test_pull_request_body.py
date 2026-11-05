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
    with pytest.raises(ValueError, match="Add a model from Hugging Face"):
        pull_request_body(model_id)


def test_user_model_lemonade_already_registered_is_pulled_by_name_only():
    """Registered in Lemonade directly, so Lemonade holds its checkpoint."""
    body = pull_request_body(
        "user.my-own-model", registered=["Gemma-4-E4B-it-GGUF", "user.My-Own-Model"]
    )
    assert body == {"model_name": "user.My-Own-Model"}


@pytest.mark.parametrize(
    "requested", ["user.My-Own-GGUF", "user.my-own-gguf", "USER.MY-OWN-GGUF"]
)
def test_user_model_lemonade_lists_without_its_prefix_is_pulled_by_name(requested):
    """``/models`` drops the ``user.`` prefix; the pull keeps it in Lemonade's casing."""
    body = pull_request_body(requested, registered=["My-Own-GGUF"])
    assert body == {"model_name": "user.My-Own-GGUF"}
    assert not body.keys() & _REGISTRATION_KEYS


def test_registered_match_is_whole_name_not_a_prefix():
    with pytest.raises(ValueError, match="Add a model from Hugging Face"):
        pull_request_body("user.My-Own", registered=["My-Own-GGUF"])


def test_registered_model_ids_skips_entries_that_are_not_objects():
    listing = {"data": ["My-Own-GGUF", None, 7, {"id": 3}, {}, {"id": "Bare-GGUF"}]}
    assert lc.registered_model_ids(listing) == ["Bare-GGUF"]


@pytest.mark.parametrize("listing", [[], None, "x", {}, {"data": None}, {"data": {}}])
def test_registered_model_ids_raises_on_a_body_without_a_data_list(listing):
    with pytest.raises(ValueError, match="no 'data' list"):
        lc.registered_model_ids(listing)


def test_user_model_missing_from_registered_still_raises():
    with pytest.raises(ValueError, match="Add a model from Hugging Face"):
        pull_request_body("user.not-a-real-model", registered=["user.other"])


def test_unknown_user_model_message_gives_user_facing_advice():
    """The 400 reaches the Agent UI as-is, so it must not send people to code."""
    with pytest.raises(ValueError) as exc:
        pull_request_body("user.not-a-real-model")
    message = str(exc.value)
    assert "Settings > Model > Add a model from Hugging Face" in message
    assert "/provider, choose Local, then ctrl+a" in message
    assert "`gaia model add <link>`" in message
    assert "gaia init" not in message
    assert "MODELS" not in message
    assert "lemonade_client" not in message


def test_unknown_non_user_model_is_left_to_lemonade_by_name():
    assert pull_request_body("Some-Lemonade-Builtin-GGUF") == {
        "model_name": "Some-Lemonade-Builtin-GGUF"
    }


def test_custom_entry_missing_its_checkpoint_raises(monkeypatch):
    broken = dataclasses.replace(MODELS["embeddinggemma"], checkpoint=None)
    monkeypatch.setattr(lc, "find_model_requirement", lambda _id: broken)
    with pytest.raises(ValueError, match="Add a model from Hugging Face"):
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


class _FakeAsyncClient:
    """Stands in for ``httpx.AsyncClient``; records the ``/models`` request."""

    calls: list = []
    registered: list = []
    listing = None  # raw JSON body; overrides ``registered`` when set

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        import httpx

        _FakeAsyncClient.calls.append((url, params))
        listing = _FakeAsyncClient.listing
        if listing is None:
            listing = {"data": [{"id": i} for i in _FakeAsyncClient.registered]}
        return httpx.Response(200, json=listing, request=httpx.Request("GET", url))


@pytest.fixture
def lemonade_models(monkeypatch):
    import httpx

    _FakeAsyncClient.calls = []
    _FakeAsyncClient.registered = []
    _FakeAsyncClient.listing = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


@pytest.mark.parametrize("model_id", ["amd.Claude-Opus-5", "user.not-a-real-model"])
def test_download_endpoint_refuses_up_front(model_id, monkeypatch, lemonade_models):
    """The button gets a 400 it can show, not a 202 for a pull that never runs."""
    started = []
    monkeypatch.setattr(system, "_stream_lemonade_pull", lambda *a: started.append(a))
    req = system.DownloadModelRequest(model_name=model_id)
    with pytest.raises(HTTPException) as err:
        asyncio.run(system.download_model_endpoint(req))
    assert err.value.status_code == 400
    assert started == []


def test_download_endpoint_accepts_a_user_model_lemonade_registered(
    monkeypatch, lemonade_models
):
    """Registered in Lemonade directly: Download/Resume must start, not 400."""
    lemonade_models.registered = ["user.My-Own-GGUF"]
    started = []

    async def _fake_pull(*a):
        started.append(a)

    monkeypatch.setattr(system, "_stream_lemonade_pull", _fake_pull)
    req = system.DownloadModelRequest(model_name="user.My-Own-GGUF")

    async def _run():
        result = await system.download_model_endpoint(req)
        await asyncio.sleep(0)
        return result

    assert asyncio.run(_run()) == {"status": "downloading", "model": "user.My-Own-GGUF"}
    assert started == [("user.My-Own-GGUF", False)]
    url, params = lemonade_models.calls[0]
    assert url.endswith("/models") and params == {"show_all": "true"}


def test_pull_body_names_the_registered_model_lemonade_listed(lemonade_models):
    lemonade_models.registered = ["user.My-Own-GGUF"]
    body = asyncio.run(system._pull_body("user.my-own-gguf"))
    assert body == {"model_name": "user.My-Own-GGUF"}


def test_pull_body_skips_the_listing_for_a_model_gaia_knows(lemonade_models):
    asyncio.run(system._pull_body(lc.DEFAULT_MODEL_NAME))
    assert lemonade_models.calls == []


def test_pull_body_matches_the_bare_name_lemonade_lists(lemonade_models):
    lemonade_models.listing = {"data": ["junk", None, {"id": "My-Own-GGUF"}]}
    body = asyncio.run(system._pull_body("user.my-own-gguf"))
    assert body == {"model_name": "user.My-Own-GGUF"}


@pytest.mark.parametrize("listing", [["My-Own-GGUF"], {"models": []}, {"data": "x"}])
def test_download_endpoint_400s_when_the_model_list_cannot_be_read(
    listing, monkeypatch, lemonade_models
):
    """An unreadable list is a 400 naming Lemonade, not a 500 from a bad lookup."""
    lemonade_models.listing = listing
    started = []
    monkeypatch.setattr(system, "_stream_lemonade_pull", lambda *a: started.append(a))
    req = system.DownloadModelRequest(model_name="user.My-Own-GGUF")
    with pytest.raises(HTTPException) as err:
        asyncio.run(system.download_model_endpoint(req))
    assert err.value.status_code == 400
    assert "model list from Lemonade" in err.value.detail
    assert started == []
