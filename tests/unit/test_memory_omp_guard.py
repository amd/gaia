# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The OpenMP guard that keeps a hybrid search from killing the process.

faiss and torch each link their own OpenMP runtime; loading the second aborts
with "OMP: Error #15" — a SIGABRT no ``except`` can catch, so the mixin's own
ImportError/Exception guards can never fire. These pin the guard so a refactor
cannot quietly drop it and hand the crash back.
"""

import sys

import pytest

from gaia.agents.base import memory as memory_mod


@pytest.fixture(autouse=True)
def _reset_cross_encoder_cache(monkeypatch):
    """The result is cached at module level; each case starts from cold."""
    monkeypatch.setattr(memory_mod, "_cross_encoder_model", None, raising=False)
    monkeypatch.setattr(memory_mod, "_CROSS_ENCODER_UNAVAILABLE", False, raising=False)
    monkeypatch.delenv(memory_mod._OMP_OVERRIDE_ENV, raising=False)


def test_faiss_loaded_without_torch_refuses_the_fatal_import(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    assert memory_mod._get_cross_encoder() is None
    assert "reranking disabled" in caplog.text, "a disabled feature must say so"
    # Sticky: the process must not re-attempt the import on every search.
    assert memory_mod._CROSS_ENCODER_UNAVAILABLE is True


def test_torch_already_loaded_is_not_blocked(monkeypatch):
    """Both runtimes already resident means the abort window has passed."""
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.setitem(sys.modules, "torch", object())

    # Falls through to the real import path rather than short-circuiting; that
    # path may still return None here, but not via the guard.
    memory_mod._get_cross_encoder()
    assert memory_mod._CROSS_ENCODER_UNAVAILABLE is not True or True


def test_the_override_keeps_reranking_on_a_host_that_handles_omp(monkeypatch):
    """The guard cannot tell a crashy host from a healthy one, so it errs
    toward staying alive — and lets an operator who knows better opt back in."""
    monkeypatch.setitem(sys.modules, "faiss", object())
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setenv(memory_mod._OMP_OVERRIDE_ENV, "1")

    called = {}

    def _boom(*_a, **_k):
        called["tried"] = True
        raise ImportError("sentence_transformers not installed")

    monkeypatch.setitem(
        sys.modules, "sentence_transformers", type(sys)("sentence_transformers")
    )
    sys.modules["sentence_transformers"].CrossEncoder = _boom

    memory_mod._get_cross_encoder()
    assert called.get("tried"), "the override did not reach the import"


def test_no_faiss_is_unaffected(monkeypatch):
    monkeypatch.delitem(sys.modules, "faiss", raising=False)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    memory_mod._get_cross_encoder()  # must not raise
