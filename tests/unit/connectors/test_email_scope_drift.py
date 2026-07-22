# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Drift guard: the email connector scopes transcribed as literals in
``gaia.daemon.sidecars.spec`` (core cannot import the hub wheel at runtime —
``server.py`` never imports it, #2154) must stay in lock-step with their
source of truth in the ``gaia-agent-email`` package (#2408).

Placement is deliberate, not incidental: ``test_email_agent_unit.yml``
installs the email wheel via ``-e hub/agents/email/python`` AND triggers on
``tests/unit/connectors/**``, so this test actually RUNS in CI rather than
being silently skipped by ``pytest.importorskip`` the way it would be under
``tests/unit/agents/`` or a bare ``tests/unit/test_*`` module (installed
there with only the ``[api]`` extra). Verify locally with ``pytest -rs``
that this file shows PASSED, not SKIPPED.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")


def test_google_scopes_match_source_of_truth():
    from gaia_agent_email.scopes import ALL_SCOPES

    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    by_provider = {
        cr.connector_id: set(cr.scopes) for cr in email_spec.required_connections
    }
    assert by_provider.get("google") == set(ALL_SCOPES)


def test_microsoft_scopes_match_source_of_truth():
    from gaia_agent_email.outlook_scopes import (
        OUTLOOK_CALENDAR_SCOPES,
        OUTLOOK_MAIL_SCOPES,
    )

    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    by_provider = {
        cr.connector_id: set(cr.scopes) for cr in email_spec.required_connections
    }
    expected = set(OUTLOOK_MAIL_SCOPES) | set(OUTLOOK_CALENDAR_SCOPES)
    assert by_provider.get("microsoft") == expected


def test_namespaced_id_matches_source_of_truth():
    from gaia_agent_email.scopes import AGENT_NAMESPACED_ID

    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    assert email_spec.grant_agent_id == AGENT_NAMESPACED_ID
