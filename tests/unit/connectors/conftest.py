# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Connections-test fixtures.

Autouse fixtures here apply to every test under ``tests/unit/connectors/``
and ensure each test runs against a deterministic in-memory keyring backend
and a clean per-test access-token cache.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _autouse_in_memory_keyring(in_memory_keyring):  # noqa: F811
    """
    Force every connections test through the session-scoped in-memory keyring.

    Linux CI runners do not ship SecretService and the production-default
    ``keyrings.alt`` fallback writes plaintext; ``gaia.connectors.store``
    explicitly refuses that backend, so without this fixture every test would
    raise on first ``save_connection`` or first ``load_connection``.

    Depends on the session-scoped ``in_memory_keyring`` fixture from
    ``tests/conftest.py``. Clears the backing dict between tests so state
    from a previous test does not leak.
    """
    # Some tests temporarily install an alternate backend (e.g. PlaintextKeyring
    # to assert refusal). Re-install the in-memory backend at the start of
    # each test so subsequent tests see the deterministic fixture.
    import keyring

    keyring.set_keyring(in_memory_keyring)
    in_memory_keyring._store.clear()
    yield in_memory_keyring
    in_memory_keyring._store.clear()


@pytest.fixture(autouse=True)
def _autouse_reset_token_cache():
    """
    Reset the module-level token cache between tests.

    The cache is a process-wide singleton; without resetting it, AC6's
    "10 concurrent calls = 1 refresh round-trip" test would observe a
    cached token from an earlier test. Imports lazily so this fixture
    file does not pull in ``httpx`` at collection time.
    """
    try:
        from gaia.connectors import tokens
    except ImportError:
        # Module not yet importable during early TDD iterations.
        yield
        return

    if hasattr(tokens, "_cache"):
        tokens._cache.clear()
    yield
    if hasattr(tokens, "_cache"):
        tokens._cache.clear()


@pytest.fixture(autouse=True)
def _autouse_isolate_home(tmp_path, monkeypatch):
    """
    Redirect ``Path.home()`` for every grants/mcp_servers reader+writer
    to a per-test ``tmp_path`` so connector tests can never contaminate
    the developer's real ``~/.gaia/`` files. Belt-and-braces alongside
    the explicit per-file ``fake_home`` fixtures.
    """
    monkeypatch.setattr("gaia.connectors.grants.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.connectors.mcp_server.Path.home", lambda: tmp_path)
