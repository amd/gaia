# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for tests/unit/agents/email.

Resets ``gaia_agent_email.model_select``'s success-only cache before AND
after every test in this directory (e.g. ``test_init_endpoint.py`` exercises
``_resolve_email_model_id`` / ``_probe_model_present`` repeatedly across many
test functions in the same file), so a cached resolution from one test can
never silently short-circuit another test's fake ``requests.get``.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_model_select_cache_between_tests():
    # ``model_select`` does not exist yet at RED time (#1439) -- this
    # autouse fixture must not break every OTHER already-passing test in
    # this directory by erroring at setup before the module lands.
    try:
        from gaia_agent_email.model_select import _reset_model_select_cache
    except ImportError:
        yield
        return
    _reset_model_select_cache()
    yield
    _reset_model_select_cache()
