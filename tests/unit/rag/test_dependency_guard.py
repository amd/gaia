# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for RAG dependency-import guards.

A broken native dependency (e.g. torchcodec/FFmpeg under sentence-transformers,
or an arch-mismatched faiss build) raises ``RuntimeError``/``OSError`` at import
rather than ``ImportError``. The guard in ``gaia.rag.sdk`` must treat that the
same as "not installed" so it cannot crash every module that transitively
imports RAG, while still surfacing a loud, actionable error at point of use.
"""

import importlib
from unittest.mock import patch

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
    assert hasattr(sdk, "_SENTENCE_TRANSFORMERS_IMPORT_ERROR")
    assert hasattr(sdk, "_FAISS_IMPORT_ERROR")


def test_broken_install_reports_actionable_cause():
    """An installed-but-broken dep surfaces the captured cause, not just 'install it'."""
    cause = RuntimeError("Could not load libtorchcodec (FFmpeg not found)")
    with (
        patch.object(sdk, "SentenceTransformer", None),
        patch.object(sdk, "_SENTENCE_TRANSFORMERS_IMPORT_ERROR", cause),
    ):
        with pytest.raises(ImportError) as excinfo:
            _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "installed but failed to load" in msg
    assert "libtorchcodec" in msg  # the captured cause is named


def test_genuinely_missing_dep_omits_broken_section():
    """A simply-missing dep gets install instructions, not the broken-load hint."""
    with (
        patch.object(sdk, "SentenceTransformer", None),
        patch.object(sdk, "_SENTENCE_TRANSFORMERS_IMPORT_ERROR", None),
    ):
        with pytest.raises(ImportError) as excinfo:
            _bare_sdk()._check_dependencies()
    msg = str(excinfo.value)
    assert "sentence-transformers" in msg
    assert "installed but failed to load" not in msg
