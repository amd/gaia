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

import json
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent_email")

# tests/unit/connectors/ -> repo root is 3 parents up.
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "connectors"
    / "email_scopes.json"
)


def _load_fixture() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _required_connections_by_provider():
    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    return {cr.connector_id: cr for cr in email_spec.required_connections}


def test_google_scopes_match_source_of_truth():
    from gaia_agent_email.scopes import ALL_SCOPES, REQUIRED_SCOPES

    by_provider = _required_connections_by_provider()
    cr = by_provider["google"]
    assert set(cr.scopes) == set(ALL_SCOPES)
    assert set(cr.required_scopes) == set(REQUIRED_SCOPES)


@pytest.mark.parametrize("connector_id", ["microsoft", "microsoft_work"])
def test_microsoft_scopes_match_source_of_truth(connector_id):
    from gaia_agent_email.outlook_scopes import (
        OUTLOOK_ALL_SCOPES,
        OUTLOOK_REQUIRED_SCOPES,
    )

    by_provider = _required_connections_by_provider()
    cr = by_provider[connector_id]
    assert set(cr.scopes) == set(OUTLOOK_ALL_SCOPES)
    assert set(cr.required_scopes) == set(OUTLOOK_REQUIRED_SCOPES)


def test_daemon_forwards_every_mailbox_provider():
    """The daemon only forwards tokens for ids listed in ``forward_providers``
    (#2629) — a connector present in ``required_connections`` but missing here
    would connect successfully and then silently never deliver a token."""
    from gaia_agent_email.mailbox_state import PROVIDERS

    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    assert set(email_spec.forward_providers) == set(PROVIDERS)


def test_namespaced_id_matches_source_of_truth():
    from gaia_agent_email.scopes import AGENT_NAMESPACED_ID

    from gaia.daemon.sidecars.spec import builtin_specs

    email_spec = builtin_specs()["email"]
    assert email_spec.grant_agent_id == AGENT_NAMESPACED_ID


# ---------------------------------------------------------------------------
# AC-1 — agent (mailbox) scopes agree across every surface. Identity scopes
# are deliberately NOT folded into this check: the catalog spec's
# default_scopes and the raw OAuthProvider's default_scopes legitimately
# differ in literal form today (#2730 checkpoint-1 finding, tracked as its
# own follow-up) — lumping them together would make this guard either fail
# spuriously or get written loosely enough to miss real agent-scope drift.
# ---------------------------------------------------------------------------


class TestAgentScopesAgreeAcrossSurfaces:
    def test_google_source_matches_fixture(self):
        from gaia_agent_email.scopes import (
            ALL_SCOPES,
            CALENDAR_SCOPES,
            GMAIL_SCOPES,
            REQUIRED_SCOPES,
        )

        fixture = _load_fixture()["google"]["agent_scopes"]
        assert set(GMAIL_SCOPES) == set(fixture["required"])
        assert set(CALENDAR_SCOPES) == set(fixture["optional"])
        assert set(REQUIRED_SCOPES) == set(fixture["required"])
        assert set(ALL_SCOPES) == set(fixture["required"]) | set(fixture["optional"])

    def test_microsoft_source_matches_fixture(self):
        from gaia_agent_email.outlook_scopes import (
            OUTLOOK_ALL_SCOPES,
            OUTLOOK_CALENDAR_SCOPES,
            OUTLOOK_MAIL_SCOPES,
            OUTLOOK_REQUIRED_SCOPES,
        )

        fixture = _load_fixture()["microsoft"]["agent_scopes"]
        assert set(OUTLOOK_MAIL_SCOPES) == set(fixture["required"])
        assert set(OUTLOOK_CALENDAR_SCOPES) == set(fixture["optional"])
        assert set(OUTLOOK_REQUIRED_SCOPES) == set(fixture["required"])
        assert set(OUTLOOK_ALL_SCOPES) == set(fixture["required"]) | set(
            fixture["optional"]
        )

    def test_daemon_sidecar_spec_matches_fixture(self):
        from gaia_agent_email.mailbox_state import PROVIDERS

        by_provider = _required_connections_by_provider()
        fixture = _load_fixture()
        for provider in PROVIDERS:
            cr = by_provider[provider]
            agent = fixture[provider]["agent_scopes"]
            assert set(cr.scopes) == set(agent["required"]) | set(agent["optional"])
            assert set(cr.required_scopes) == set(agent["required"])

    def test_build_scope_union_agent_portion_matches_fixture(self):
        from gaia_agent_email.connector_routes import _build_scope_union
        from gaia_agent_email.mailbox_state import PROVIDERS

        fixture = _load_fixture()
        for provider in PROVIDERS:
            union = set(_build_scope_union(provider))
            agent = fixture[provider]["agent_scopes"]
            assert (set(agent["required"]) | set(agent["optional"])) <= union
            assert union == set(fixture[provider]["connect_union"])

    def test_default_required_scopes_by_provider_matches_required_scopes_not_all(self):
        """#2730 D1: this gates a forwarded connection's usability at
        ``import_forwarded_connection`` time — the same mail-only
        enforcement the daemon's forward-out mint applies (D5). It must NOT
        equal ALL_SCOPES: requiring calendar here would reject a mail-only
        forwarded connection outright, the same all-or-nothing failure this
        issue removes, just relocated to the import boundary."""
        from gaia_agent_email.scopes import ALL_SCOPES, REQUIRED_SCOPES

        from gaia.connectors.api import _DEFAULT_REQUIRED_SCOPES_BY_PROVIDER

        assert set(_DEFAULT_REQUIRED_SCOPES_BY_PROVIDER["google"]) == set(
            REQUIRED_SCOPES
        )
        assert set(_DEFAULT_REQUIRED_SCOPES_BY_PROVIDER["google"]) != set(ALL_SCOPES)
