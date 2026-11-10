# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship's mailbox and calendar scopes are spelled the same on every surface.

The daemon's ``gaia`` sidecar spec transcribes them as literals (the shipped
flagship is a frozen binary whose own declaration core cannot import), so a
change to ``gaia.agents.tools._email.scopes`` or ``_calendar.scopes`` must
reach the spec, and the shared mail fixture, in the same edit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.agents.tools._calendar.scopes import CALENDAR_SCOPES
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


def _spec() -> dict:
    return {cr.connector_id: cr for cr in builtin_specs()["gaia"].required_connections}


def test_daemon_spec_requests_exactly_the_declared_scopes():
    requested = {k: tuple(cr.scopes) for k, cr in _spec().items()}
    assert requested == {
        k: (*mail, CALENDAR_SCOPES[k]) for k, mail in DECLARED_SCOPES.items()
    }


def test_only_the_mail_scopes_are_required_to_connect():
    required = {k: tuple(cr.required_scopes) for k, cr in _spec().items()}
    assert required == {k: tuple(v) for k, v in DECLARED_SCOPES.items()}


@pytest.mark.parametrize("provider", sorted(DECLARED_SCOPES))
def test_fixture_matches_the_declared_scopes(provider):
    assert _FIXTURE[provider]["agent_scopes"] == list(DECLARED_SCOPES[provider])


def test_no_email_sidecar_spec_remains():
    assert "email" not in builtin_specs()
