# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Downloading a model from Settings registers a custom model on its first pull.

Lemonade lists a ``user.`` model only once it is registered, and registers it
only from a pull that carries its checkpoint and recipe. A pull by name alone
404s for such a model; a pull that sends a recipe for a built-in 400s (#1655).
"""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from gaia.llm import lemonade_client as lc
from gaia.ui.routers import system


class _Resp:
    status_code = 500

    async def aread(self):
        return b"stop here"


class _Stream:
    async def __aenter__(self):
        return _Resp()

    async def __aexit__(self, *exc):
        return False


class _Catalog:
    def __init__(self, ids, status_code=200):
        self.ids = ids
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://lemonade/models"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return {"data": [{"id": i} for i in self.ids]}


@pytest.fixture
def lemonade_lists():
    """What Lemonade's ``/models?show_all=true`` answers; edit per test."""
    return _Catalog([])


@pytest.fixture
def sent(monkeypatch, lemonade_lists):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            calls.append(json)
            return _Stream()

        async def get(self, url, params=None, headers=None):
            assert url.endswith("/models") and params == {"show_all": "true"}
            return lemonade_lists

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


def test_custom_model_pull_carries_its_registration(sent):
    asyncio.run(system._stream_lemonade_pull(lc.GPT_OSS_20B_MODEL_NAME, False))
    assert sent == [
        {
            "model_name": "user.ggml-gpt-oss-20b-MXFP4-GGUF",
            "stream": True,
            "checkpoint": "ggml-org/gpt-oss-20b-GGUF:gpt-oss-20b-MXFP4.gguf",
            "recipe": "llamacpp",
            "reasoning": True,
        }
    ]


def test_embedder_pull_carries_checkpoint_recipe_and_embedding(sent):
    """The embedder is a user. model; name-only returns HTTP 500 on a cold cache."""
    asyncio.run(system._stream_lemonade_pull(lc.DEFAULT_EMBEDDING_MODEL, False))
    assert sent == [
        {
            "model_name": "user.embeddinggemma-300m-GGUF",
            "stream": True,
            "checkpoint": "ggml-org/embeddinggemma-300M-GGUF:Q8_0",
            "recipe": "llamacpp",
            "embedding": True,
        }
    ]


def test_flash_pull_carries_the_registration_lemonade_cannot_infer(sent):
    """The opt-in Qwen3.8 Flash model is the Download button's user. case."""
    asyncio.run(system._stream_lemonade_pull(lc.FLASH_OPTION_MODEL_NAME, True))
    assert sent == [
        {
            "model_name": "user.Qwen3.8-Flash-Next-GGUF",
            "stream": True,
            "force": True,
            "checkpoint": "unsloth/Qwen3.8-Flash-Next-GGUF:UD-IQ3_XXS",
            "recipe": "llamacpp",
            "mmproj": "mmproj-F16.gguf",
            "vision": True,
            "reasoning": True,
        }
    ]


def test_gateway_model_is_not_posted(sent, monkeypatch):
    """A gateway model has no weights; the button must not call /pull."""

    async def _keep(_model_name):
        return None

    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    lc.record_cloud_models(
        {"data": [{"id": "other.remote-chat", "recipe": "cloud", "labels": []}]}
    )
    try:
        asyncio.run(system._stream_lemonade_pull("other.remote-chat", False))
        assert sent == []
        progress = system._get_download_progress("other.remote-chat")
        assert progress["state"] == "error"
        assert "gateway" in progress["message"]
    finally:
        lc.record_cloud_models({"data": []})


def test_unknown_user_model_is_not_posted(sent, monkeypatch):
    """Name-only for an unknown user. id is the HTTP 500 this pull used to send."""

    async def _keep(_model_name):
        return None

    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    asyncio.run(system._stream_lemonade_pull("user.not-a-real-model", False))
    assert sent == []
    progress = system._get_download_progress("user.not-a-real-model")
    assert progress["state"] == "error"
    assert "Add a model from Hugging Face" in progress["message"]


def test_user_model_registered_in_lemonade_directly_pulls_by_name(sent, lemonade_lists):
    """Lemonade already holds its checkpoint, so Download and Resume send the name."""
    lemonade_lists.ids.append("user.My-Own-GGUF")
    asyncio.run(system._stream_lemonade_pull("user.My-Own-GGUF", True))
    assert sent == [{"model_name": "user.My-Own-GGUF", "stream": True, "force": True}]


def test_user_model_lemonade_lists_without_its_prefix_pulls_by_name(
    sent, lemonade_lists
):
    """``/models`` lists it bare; the pull is name only, under ``user.``."""
    lemonade_lists.ids.append("My-Own-GGUF")
    asyncio.run(system._stream_lemonade_pull("user.my-own-gguf", False))
    assert sent == [{"model_name": "user.My-Own-GGUF", "stream": True}]


def test_settings_pull_does_not_auto_load_the_downloaded_model(monkeypatch):
    """A Settings download must leave the model currently in use untouched."""
    loaded = []

    class _Done:
        status_code = 200

        async def aiter_text(self):
            yield 'event: complete\ndata: {"file_index": 1, "total_files": 1}\n\n'

    class _DoneStream:
        async def __aenter__(self):
            return _Done()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _DoneStream()

        async def get(self, url, params=None, headers=None):
            return httpx.Response(
                200,
                json={"data": [{"id": "My-Own-GGUF"}]},
                request=httpx.Request("GET", url),
            )

    async def _load(model_name):
        loaded.append(model_name)

    async def _keep(_model_name):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(system, "_auto_load_after_download", _load)
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)

    async def _run():
        await system._stream_lemonade_pull("user.my-own-gguf", False)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert loaded == []
    assert system._get_download_progress("user.my-own-gguf")["state"] == "complete"


def test_auto_load_after_the_pull_uses_the_name_lemonade_listed(monkeypatch):
    """A missing-model pull opts in without loading a different id."""
    loaded = []

    class _Done:
        status_code = 200

        async def aiter_text(self):
            yield 'event: complete\ndata: {"file_index": 1, "total_files": 1}\n\n'

    class _DoneStream:
        async def __aenter__(self):
            return _Done()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None, headers=None):
            loaded.append(("pull", json["model_name"]))
            return _DoneStream()

        async def get(self, url, params=None, headers=None):
            return httpx.Response(
                200,
                json={"data": [{"id": "My-Own-GGUF"}]},
                request=httpx.Request("GET", url),
            )

    async def _load(model_name):
        loaded.append(("load", model_name))

    async def _keep(_model_name):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(system, "_auto_load_after_download", _load)
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)

    async def _run():
        await system._stream_lemonade_pull("user.my-own-gguf", False, True)
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert loaded == [("pull", "user.My-Own-GGUF"), ("load", "user.My-Own-GGUF")]
    assert system._get_download_progress("user.my-own-gguf")["state"] == "complete"


def test_user_model_lemonade_lists_but_gaia_does_not_pulls_by_name(
    sent, lemonade_lists
):
    """Registered in Lemonade directly: name only, as before #6566 (#6675)."""
    lemonade_lists.ids = ["Other-Model-GGUF", "My-Own-Finetune-GGUF"]
    asyncio.run(system._stream_lemonade_pull("user.My-Own-Finetune-GGUF", False))
    assert sent == [{"model_name": "user.My-Own-Finetune-GGUF", "stream": True}]


def test_unknown_user_model_error_is_plain_language(sent, monkeypatch):
    async def _keep(_model_name):
        return None

    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    asyncio.run(system._stream_lemonade_pull("user.not-a-real-model", False))
    message = system._get_download_progress("user.not-a-real-model")["message"]
    for developer_text in ("MODELS", "lemonade_client", "gaia init", "gaia model"):
        assert developer_text not in message


def test_unreadable_catalog_refuses_instead_of_guessing(
    sent, lemonade_lists, monkeypatch
):
    async def _keep(_model_name):
        return None

    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    lemonade_lists.status_code = 500
    asyncio.run(system._stream_lemonade_pull("user.not-a-real-model", False))
    assert sent == []
    progress = system._get_download_progress("user.not-a-real-model")
    assert progress["state"] == "error"
    assert "didn't say whether it has this model" in progress["message"]


def test_listed_name_without_the_namespace_still_registers_under_it(sent):
    """Lemonade lists a registered user model without ``user.``."""
    asyncio.run(system._stream_lemonade_pull("ggml-gpt-oss-20b-MXFP4-GGUF", False))
    assert sent[0]["model_name"] == "user.ggml-gpt-oss-20b-MXFP4-GGUF"
    assert sent[0]["checkpoint"]


@pytest.mark.parametrize(
    "model", ["gpt-oss-120b-mxfp-GGUF", lc.DEFAULT_MODEL_NAME, "Some-Unknown-GGUF"]
)
def test_builtin_and_unknown_models_pull_by_name_only(sent, model):
    asyncio.run(system._stream_lemonade_pull(model, True))
    assert sent == [{"model_name": model, "stream": True, "force": True}]


def test_progress_follows_the_model_once_lemonade_drops_the_user_prefix(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        lc.GPT_OSS_20B_MODEL_NAME, {"state": "downloading", "percent": 40}
    )
    listed = lc.GPT_OSS_20B_MODEL_NAME[len("user.") :]
    assert system._get_download_progress(listed)["percent"] == 40
    assert system._get_download_progress(lc.GPT_OSS_20B_MODEL_NAME)["percent"] == 40
    assert system._get_download_progress("gpt-oss-20b-mxfp4-GGUF") is None


def _broken_stream(monkeypatch, exc):
    """A pull whose SSE stream breaks as soon as it is read."""

    class _Broken:
        status_code = 200

        async def aiter_text(self):
            raise exc
            yield ""  # pragma: no cover

    class _Stream:
        async def __aenter__(self):
            return _Broken()

        async def __aexit__(self, *exc_args):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_args):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _Stream()

        async def get(self, url, params=None, headers=None):
            return httpx.Response(
                200, json={"data": []}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


async def _keep(_model_name):
    return None


def test_an_empty_stream_error_still_names_what_happened(monkeypatch):
    """A dropped httpx stream often has no message; a blank reason is not one."""
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    _broken_stream(monkeypatch, httpx.ReadError(""))

    asyncio.run(system._stream_lemonade_pull("Some-Unknown-GGUF", False))

    progress = system._get_download_progress("Some-Unknown-GGUF")
    assert progress["state"] == "error"
    assert progress["message"] == (
        "Download stream interrupted: the connection to the model server closed"
    )


def test_a_stream_error_keeps_its_own_message(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    _broken_stream(monkeypatch, httpx.ReadError("connection reset by peer"))

    asyncio.run(system._stream_lemonade_pull("Some-Unknown-GGUF", False))

    message = system._get_download_progress("Some-Unknown-GGUF")["message"]
    assert message == "Download stream interrupted: connection reset by peer"


def test_a_failed_download_outlives_the_terminal_window(monkeypatch):
    """The failure has to survive Settings closing and a hidden window's poll."""
    monkeypatch.setattr(system, "_DOWNLOAD_PROGRESS_TTL", 0)
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        "Some-GGUF", {"state": "error", "model_name": "Some-GGUF", "message": "boom"}
    )

    asyncio.run(system._evict_progress_after_ttl("Some-GGUF"))

    assert system._get_download_progress("Some-GGUF")["message"] == "boom"


def test_a_completed_download_still_retires_after_the_terminal_window(monkeypatch):
    monkeypatch.setattr(system, "_DOWNLOAD_PROGRESS_TTL", 0)
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        "Some-GGUF", {"state": "complete", "model_name": "Some-GGUF"}
    )

    asyncio.run(system._evict_progress_after_ttl("Some-GGUF"))

    assert system._get_download_progress("Some-GGUF") is None


def test_the_terminal_window_does_not_clear_a_retry(monkeypatch):
    monkeypatch.setattr(system, "_DOWNLOAD_PROGRESS_TTL", 0)
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        "Some-GGUF", {"state": "downloading", "model_name": "Some-GGUF"}
    )

    asyncio.run(system._evict_progress_after_ttl("Some-GGUF"))

    assert system._get_download_progress("Some-GGUF")["state"] == "downloading"


def _progress(model_name: str, state: str) -> dict:
    return {"state": state, "model_name": model_name}


def test_status_surfaces_a_download_of_a_model_other_than_the_active_one(monkeypatch):
    """A Settings download of any model, not only the chat's, reaches the chrome."""
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("default-GGUF", _progress("default-GGUF", "complete"))
    system._set_download_progress(
        "settings-GGUF", _progress("settings-GGUF", "downloading")
    )

    surfaced = system._surface_download_progress("default-GGUF")

    assert surfaced["model_name"] == "settings-GGUF"


def test_status_prefers_the_active_models_own_download(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        "settings-GGUF", _progress("settings-GGUF", "downloading")
    )
    system._set_download_progress(
        "default-GGUF", _progress("default-GGUF", "downloading")
    )

    assert (
        system._surface_download_progress("default-GGUF")["model_name"]
        == "default-GGUF"
    )


def test_status_surfaces_a_failure_for_a_model_other_than_the_active_one(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("default-GGUF", _progress("default-GGUF", "complete"))
    system._set_download_progress("settings-GGUF", _progress("settings-GGUF", "error"))

    assert system._surface_download_progress("default-GGUF")["state"] == "error"


def test_status_hides_a_finished_download(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("Some-GGUF", _progress("Some-GGUF", "complete"))

    assert system._surface_download_progress("Some-GGUF") is None


def test_status_uses_the_most_recent_of_two_other_downloads(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("first-GGUF", _progress("first-GGUF", "downloading"))
    system._set_download_progress(
        "second-GGUF", _progress("second-GGUF", "downloading")
    )

    assert (
        system._surface_download_progress("other-GGUF")["model_name"] == "second-GGUF"
    )


def _stream_of(monkeypatch, *blocks):
    """A pull whose SSE stream yields *blocks* and then closes cleanly."""

    class _Body:
        status_code = 200

        async def aiter_text(self):
            for block in blocks:
                yield block

    class _Stream:
        async def __aenter__(self):
            return _Body()

        async def __aexit__(self, *exc_args):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_args):
            return False

        def stream(self, method, url, json=None, headers=None):
            return _Stream()

        async def get(self, url, params=None, headers=None):
            return httpx.Response(
                200, json={"data": []}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


_HALFWAY = (
    'event: progress\ndata: {"file": "a.gguf", "bytes_downloaded": 5, '
    '"bytes_total": 10, "total_download_size": 10, "file_index": 1, '
    '"total_files": 1}\n\n'
)


@pytest.mark.parametrize("blocks", [(), (_HALFWAY,)])
def test_a_stream_that_just_ends_is_a_failure_not_a_download_forever(
    monkeypatch, blocks
):
    """Only "complete" retires, so a silent close must not stay "downloading"."""
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    _stream_of(monkeypatch, *blocks)

    asyncio.run(system._stream_lemonade_pull("Some-Unknown-GGUF", False))

    progress = system._get_download_progress("Some-Unknown-GGUF")
    assert progress["state"] == "error"
    assert progress["message"] == (
        "Download stream interrupted: the model server ended the download "
        "before it finished"
    )
    assert system._surface_download_progress(None)["state"] == "error"


def test_a_stream_that_completes_and_closes_stays_complete(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    _stream_of(
        monkeypatch,
        _HALFWAY,
        'event: complete\ndata: {"file_index": 1, "total_files": 1}\n\n',
    )

    asyncio.run(system._stream_lemonade_pull("Some-Unknown-GGUF", False))

    assert system._get_download_progress("Some-Unknown-GGUF")["state"] == "complete"


def test_a_stream_error_event_keeps_its_message_after_the_close(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    monkeypatch.setattr(system, "_evict_progress_after_ttl", _keep)
    _stream_of(monkeypatch, _HALFWAY, 'event: error\ndata: {"error": "disk full"}\n\n')

    asyncio.run(system._stream_lemonade_pull("Some-Unknown-GGUF", False))

    assert system._get_download_progress("Some-Unknown-GGUF")["message"] == (
        "disk full"
    )


def _dismiss(model_name: str):
    return asyncio.run(
        system.dismiss_download_failure(
            system.DismissDownloadRequest(model_name=model_name)
        )
    )


def test_dismiss_clears_only_the_failed_entry_for_that_model(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("settings-GGUF", _progress("settings-GGUF", "error"))
    system._set_download_progress("other-GGUF", _progress("other-GGUF", "error"))
    system._set_download_progress(
        "active-GGUF", _progress("active-GGUF", "downloading")
    )

    assert _dismiss("settings-GGUF") == {
        "status": "dismissed",
        "model": "settings-GGUF",
    }
    assert system._get_download_progress("settings-GGUF") is None
    assert system._get_download_progress("other-GGUF")["state"] == "error"
    assert system._get_download_progress("active-GGUF")["state"] == "downloading"


def test_a_dismissed_failure_is_gone_from_the_next_status_read(monkeypatch):
    """The dismissal lives on the server, so a reload's status poll stays clean."""
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress("settings-GGUF", _progress("settings-GGUF", "error"))

    _dismiss("settings-GGUF")

    assert system._surface_download_progress(None) is None


def test_dismiss_matches_the_name_lemonade_lists_without_the_user_prefix(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        lc.GPT_OSS_20B_MODEL_NAME,
        _progress(lc.GPT_OSS_20B_MODEL_NAME, "error"),
    )

    listed = lc.GPT_OSS_20B_MODEL_NAME[len("user.") :]
    assert _dismiss(listed)["status"] == "dismissed"
    assert system._get_download_progress(listed) is None


def test_dismiss_never_clears_an_active_download_of_the_same_model(monkeypatch):
    monkeypatch.setattr(system, "_download_progress", {})
    system._set_download_progress(
        "retrying-GGUF", _progress("retrying-GGUF", "downloading")
    )

    assert _dismiss("retrying-GGUF")["status"] == "unchanged"
    assert system._get_download_progress("retrying-GGUF")["state"] == "downloading"


def test_dismiss_requires_a_model_name():
    with pytest.raises(HTTPException) as err:
        _dismiss("   ")

    assert err.value.status_code == 400
