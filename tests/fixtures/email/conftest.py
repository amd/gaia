# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared pytest fixtures for email-agent tests.

Exposes the stub mbox + ground truth + baseline so unit and integration
tests can pull them via fixture injection — no relative paths, no
duplicated loading code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent
STUB_INBOX_MBOX = FIXTURES_DIR / "_stub_inbox.mbox"
CORPUS_INBOX_MBOX = FIXTURES_DIR / "synthetic_inbox.mbox"
GROUND_TRUTH_JSON = FIXTURES_DIR / "ground_truth.json"
BASELINE_ACCURACY_JSON = FIXTURES_DIR / "baseline_accuracy.json"


@pytest.fixture
def stub_inbox_path() -> Path:
    """Return the path to the small stub mbox fixture (legacy, ~10 msgs)."""
    assert STUB_INBOX_MBOX.exists(), STUB_INBOX_MBOX
    return STUB_INBOX_MBOX


@pytest.fixture
def corpus_inbox_path() -> Path:
    """Return the path to the committed 249-message vendor-derived corpus."""
    assert CORPUS_INBOX_MBOX.exists(), CORPUS_INBOX_MBOX
    return CORPUS_INBOX_MBOX


@pytest.fixture
def synthetic_inbox(corpus_inbox_path):
    """A pre-loaded ``FakeGmailBackend`` over the 249-message vendor-derived
    corpus. Its ground_truth is keyed by the Gmail-derived id, so it
    aligns 1:1 with this backend.
    """
    from tests.fixtures.email.fake_gmail import FakeGmailBackend

    return FakeGmailBackend(corpus_inbox_path)


@pytest.fixture
def ground_truth() -> Dict[str, Any]:
    return json.loads(GROUND_TRUTH_JSON.read_text())


@pytest.fixture
def baseline_accuracy() -> Dict[str, Any]:
    return json.loads(BASELINE_ACCURACY_JSON.read_text())
