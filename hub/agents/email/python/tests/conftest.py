# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the gaia-agent-email package's own test suite.

Resets ``gaia_agent_email.model_select``'s success-only cache before AND
after every test in this directory, so a cached resolution from one test
can never silently short-circuit another test's fake ``requests.get``
(order-dependent flakiness).

Also pins every test to the SLM-unavailable path so the suite stays hermetic —
see :func:`_hermetic_slm_classifiers`.
"""

import pytest

# ScriptedConsole / FakeAgent live in onboarding_fakes.py, NOT here — see that
# module's docstring for why a bare `from conftest import ...` is unsafe
# under CI's multi-root pytest invocation (test_email_agent.yml).


@pytest.fixture(autouse=True)
def _hermetic_slm_classifiers(request, monkeypatch):
    """Keep the suite off the network by never building a real SLM classifier.

    Any test that sets ``use_slm=True`` would otherwise reach out to a Lemonade
    server whenever one happens to be listening — the suite would then take a
    different path on a developer's machine than on CI, which advertises "no
    Lemonade server, no network". Construction is pinned to the documented
    unavailable path (heuristic + LLM) unless a test opts in with
    ``@pytest.mark.real_slm_build``; tests that exercise SLM routing inject
    their own fake classifier and are unaffected.
    """
    if request.node.get_closest_marker("real_slm_build"):
        yield
        return

    from gaia_agent_email.tools import slm_common, slm_phishing, slm_triage

    slm_common._reset_cache_for_tests()
    for module in (slm_phishing, slm_triage):
        monkeypatch.setattr(module, "get_classifier", lambda **kwargs: None)
    yield
    slm_common._reset_cache_for_tests()


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
