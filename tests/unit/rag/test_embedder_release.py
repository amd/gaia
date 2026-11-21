# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The embedder gives back the memory indexing used, once indexing goes quiet.

llama.cpp grows a buffer of about 1 MiB of host RAM per token of the longest
input it has embedded and keeps it until the process is replaced: 0.7 GB after
one 200-chunk document, held for as long as the app stayed open.
"""

import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from gaia.llm.lemonade_client import EMBEDDER_LLAMACPP_ARGS
from gaia.rag import sdk as rag_sdk
from gaia.rag.sdk import RAGSDK, _EmbedderRelease

MODEL = "test-embedder"


class _Client:
    def __init__(self, users_server=False, fail=None):
        self.calls = []
        self._users_server = users_server
        self._fail = fail

    def is_users_server(self):
        return self._users_server

    def unload_model(self, model, ignore_if_not_loaded=False):
        self.calls.append(("unload", model, ignore_if_not_loaded))
        if self._fail:
            raise self._fail

    def load_model(self, model, llamacpp_args=None):
        self.calls.append(("load", model, llamacpp_args))


@pytest.fixture(autouse=True)
def no_broker():
    with patch("gaia.daemon.broker_client.model_lease", return_value=nullcontext()):
        yield


def _sdk(client, lemonade=True):
    instance = RAGSDK.__new__(RAGSDK)
    instance.config = SimpleNamespace(embedding_model=MODEL, show_stats=False)
    instance.log = Mock()
    instance.llm_client = client
    instance.use_lemonade_embeddings = lemonade
    instance._load_embedder = lambda: None
    instance._encode_texts = lambda texts, **_: np.zeros((len(texts), 2), "float32")
    instance._create_faiss_index = lambda embeddings: SimpleNamespace(
        ntotal=len(embeddings)
    )
    return instance


def test_a_release_reloads_only_the_embedder_with_its_own_args():
    client = _Client()

    _EmbedderRelease.release(client, MODEL, Mock())

    assert client.calls == [
        ("unload", MODEL, True),
        ("load", MODEL, EMBEDDER_LLAMACPP_ARGS),
    ]


def test_a_release_waits_for_embedding_requests_in_flight():
    client = _Client()
    done = threading.Event()
    with rag_sdk._EMBEDDER_SWAP.request():
        thread = threading.Thread(
            target=lambda: (_EmbedderRelease.release(client, MODEL, Mock()), done.set())
        )
        thread.start()
        assert not done.wait(0.2)
        assert client.calls == []
    assert done.wait(5)
    thread.join()
    assert [c[0] for c in client.calls] == ["unload", "load"]


def test_a_failed_release_is_logged_with_what_it_means():
    log = Mock()

    _EmbedderRelease.release(_Client(fail=RuntimeError("server gone")), MODEL, log)

    message = log.warning.call_args.args[0]
    assert MODEL in message and "server gone" in message
    assert "keeps that memory" in message


def test_many_documents_in_a_row_cause_one_release(monkeypatch):
    monkeypatch.setattr(rag_sdk, "EMBEDDER_RELEASE_DELAY_S", 0.2)
    release = _EmbedderRelease()
    client = _Client()
    released = threading.Event()
    client.load_model = lambda *a, **k: (client.calls.append(("load",)), released.set())

    for _ in range(5):
        release.schedule(client, MODEL, Mock())

    assert released.wait(5)
    threading.Event().wait(0.4)
    assert [c[0] for c in client.calls].count("load") == 1


def test_a_new_document_cancels_the_pending_release(monkeypatch):
    monkeypatch.setattr(rag_sdk, "EMBEDDER_RELEASE_DELAY_S", 0.2)
    release = _EmbedderRelease()
    client = _Client()

    release.schedule(client, MODEL, Mock())
    release.cancel()

    threading.Event().wait(0.5)
    assert client.calls == []


def test_embedding_a_document_schedules_the_release():
    client = _Client()
    with patch.object(rag_sdk, "_EMBEDDER_RELEASE") as release:
        _sdk(client)._create_vector_index(["one", "two"])

    release.cancel.assert_called_once()
    assert release.schedule.call_args.args[:2] == (client, MODEL)


@pytest.mark.parametrize(
    "client, lemonade, chunks",
    [
        # A server the user runs is never unloaded by GAIA.
        (_Client(users_server=True), True, ["one"]),
        (_Client(), False, ["one"]),
        (_Client(), True, []),
    ],
)
def test_no_release_is_scheduled(client, lemonade, chunks):
    with patch.object(rag_sdk, "_EMBEDDER_RELEASE") as release:
        _sdk(client, lemonade)._create_vector_index(chunks)

    release.schedule.assert_not_called()
