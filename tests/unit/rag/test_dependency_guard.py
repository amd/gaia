# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for RAG dependency-import guards.

A broken native dependency (e.g. torchcodec/FFmpeg under sentence-transformers,
or an arch-mismatched faiss build) raises ``RuntimeError``/``OSError`` at import
rather than ``ImportError``. The guard in ``gaia.rag.sdk`` must treat that the
same as "not installed" so it cannot crash every module that transitively
imports RAG, while still surfacing a loud, actionable error at point of use.
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
    assert hasattr(sdk, "SentenceTransformer")
    assert hasattr(sdk, "faiss")


def test_broken_install_reports_actionable_cause(monkeypatch):
    """An installed-but-broken dep surfaces the captured cause, not just 'install it'."""
    monkeypatch.setattr(sdk, "SentenceTransformer", None)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise RuntimeError("Could not load libtorchcodec (FFmpeg not found)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError) as excinfo:
        _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "installed but failed to load" in msg
    assert "libtorchcodec" in msg  # the captured cause is named


def test_genuinely_missing_dep_omits_broken_section(monkeypatch):
    """A simply-missing dep gets install instructions, not the broken-load hint."""
    monkeypatch.setattr(sdk, "SentenceTransformer", None)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError) as excinfo:
        _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "sentence-transformers" in msg
    assert "installed but failed to load" not in msg
