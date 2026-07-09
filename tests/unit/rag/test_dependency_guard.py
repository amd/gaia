# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for RAG dependency-import guards.

RAG embeds via Lemonade, so **sentence-transformers is NOT a RAG dependency** — it
is only an optional dep of the memory cross-encoder reranker. RAG must import and
run even when sentence-transformers is absent or broken.

Regression this file protects against (the EmbeddingGemma embedder switch, #1952):
a broken ``torchcodec``/FFmpeg under sentence-transformers made ``import
sentence_transformers`` raise, which — because the old dependency guard *required*
sentence-transformers — made every RAG indexing call fail with 0 chunks even though
the Lemonade embedder was loaded and ready.

The genuine native dep (faiss) can still raise ``RuntimeError``/``OSError`` at
import when the build is arch-mismatched; the guard must treat that as "not
installed" so it cannot crash every module that transitively imports RAG, while
still surfacing a loud, actionable error at point of use.
"""

import builtins
import importlib

import pytest

# Importing the module must NOT raise even when an optional native dep is broken
# in the environment — that is the regression this guard protects against.
sdk = importlib.import_module("gaia.rag.sdk")


def _bare_sdk():
    """An RAGSDK instance without running __init__ (it only needs the method)."""
    return sdk.RAGSDK.__new__(sdk.RAGSDK)


def test_module_imports_without_optional_deps():
    """The module is importable regardless of optional-dependency health."""
    assert hasattr(sdk, "RAGSDK")
    assert hasattr(sdk, "faiss")


def test_rag_does_not_import_sentence_transformers():
    """RAG must not even import sentence-transformers — it embeds via Lemonade.

    If this fails, someone re-added a sentence-transformers import to the RAG SDK,
    reintroducing a heavy dep (torch/torchcodec) whose broken native libs would
    take down all RAG indexing.
    """
    assert not hasattr(sdk, "SentenceTransformer")


def test_check_dependencies_passes_when_sentence_transformers_is_broken(monkeypatch):
    """A broken/absent sentence-transformers must NOT fail the RAG dependency check.

    This is the core regression: RAG indexing worked in production only because
    sentence-transformers happened to import; on a box with broken torchcodec it
    failed with 0 chunks. RAG does not use sentence-transformers, so the guard must
    never list it — regardless of how broken it is.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers" or name.startswith("sentence_transformers."):
            raise RuntimeError("Could not load libtorchcodec (FFmpeg not found)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        _bare_sdk()._check_dependencies()
    except ImportError as exc:
        # If the check raises at all (e.g. faiss genuinely missing in this env),
        # it must never be because of sentence-transformers.
        assert "sentence-transformers" not in str(exc).lower()


def test_broken_faiss_reports_actionable_cause(monkeypatch):
    """An installed-but-broken faiss surfaces the captured cause, not just 'install it'."""
    monkeypatch.setattr(sdk, "faiss", None)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faiss":
            raise RuntimeError("arch-mismatched faiss build (illegal instruction)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError) as excinfo:
        _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "installed but failed to load" in msg
    assert "arch-mismatched faiss" in msg  # the captured cause is named


def test_genuinely_missing_faiss_omits_broken_section(monkeypatch):
    """A simply-missing dep gets install instructions, not the broken-load hint."""
    monkeypatch.setattr(sdk, "faiss", None)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faiss":
            raise ImportError("No module named 'faiss'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError) as excinfo:
        _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "faiss-cpu" in msg
    assert "installed but failed to load" not in msg
