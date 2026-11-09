# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship's mailbox scopes are spelled the same on every surface.

The daemon's ``gaia`` sidecar spec transcribes them as literals (the shipped
flagship is a frozen binary whose own declaration core cannot import), so a
change to ``gaia.agents.tools._email.scopes`` must reach the spec and the
shared fixture in the same edit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.agents.tools._email.scopes import DECLARED_SCOPES
from gaia.daemon.sidecars.spec import builtin_specs

_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "connectors"
        / "mail_scopes.json"
    ).read_text(encoding="utf-8")
)


def _spec_scopes() -> dict:
    spec = builtin_specs()["gaia"]
    return {cr.connector_id: tuple(cr.scopes) for cr in spec.required_connections}


def test_daemon_spec_requests_exactly_the_declared_scopes():
    assert _spec_scopes() == {k: tuple(v) for k, v in DECLARED_SCOPES.items()}


@pytest.mark.parametrize("provider", sorted(DECLARED_SCOPES))
def test_fixture_matches_the_declared_scopes(provider):
    assert _FIXTURE[provider]["agent_scopes"] == list(DECLARED_SCOPES[provider])


def test_no_email_sidecar_spec_remains():
    assert "email" not in builtin_specs()
