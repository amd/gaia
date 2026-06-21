# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared fixtures for ``tests/unit/email/``.

Re-exports the email-agent fixtures from ``tests/fixtures/email/conftest.py``
and adds an autouse home-redirect (mirroring
``tests/unit/connectors/conftest.py:_autouse_isolate_home``) so unit tests
can never write to the developer's real ``~/.gaia/email/state.db``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``tests.fixtures.email`` importable as a normal package so unit tests
# can ``from tests.fixtures.email.fake_gmail import FakeGmailBackend``.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Re-export the email fixtures. Pytest discovers fixtures from any conftest
# above the test file in the directory tree, but ``tests/fixtures/`` is a
# parallel branch — explicitly importing them here makes them available.
from tests.fixtures.email.conftest import (  # noqa: F401, E402
    baseline_accuracy,
    corpus_inbox_path,
    ground_truth,
    stub_inbox_path,
    synthetic_inbox,
)


@pytest.fixture(autouse=True)
def _autouse_isolate_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` to a per-test ``tmp_path`` so unit tests can
    never contaminate the developer's real ``~/.gaia/`` files.

    Belt-and-braces alongside the explicit per-test ``db_path`` injection
    on ``EmailAgentConfig``.
    """
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)


@pytest.fixture(autouse=True)
def _autouse_snapshot_tool_registry():
    """Snapshot ``_TOOL_REGISTRY`` and restore it between tests.

    The agent base layer's tool registry is a process-wide singleton; some
    agents call ``_TOOL_REGISTRY.clear()`` in ``_register_tools``. Without
    this snapshot, building an EmailTriageAgent during one test would erase
    every tool registered by other agents in the same pytest process.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)
