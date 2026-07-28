# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia email autonomy ...`` (#2516) — the thin-client CLI over the
sidecar's session-scoped ``/v1/email/agent/autonomy*`` REST surface.

Mirrors ``test_email_cli.py``: patches the relay seam
(``gaia.daemon.agent_control.relay_json``), never ``gaia_agent_email``, so
these run without the standalone email wheel or a live daemon.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from gaia import cli
from gaia.cli import build_parser
from gaia.daemon.errors import DaemonError


class TestArgparse:
    def test_status_parses_with_default_session(self):
        ns = build_parser().parse_args(["email", "autonomy", "status"])
        assert ns.action == "email"
        assert ns.email_action == "autonomy"
        assert ns.autonomy_action == "status"
        assert ns.session_id == "cli"

    def test_set_level_requires_a_valid_level(self):
        ns = build_parser().parse_args(["email", "autonomy", "set-level", "full"])
        assert ns.level == "full"
        with pytest.raises(SystemExit):
            build_parser().parse_args(["email", "autonomy", "set-level", "bogus"])

    def test_run_parses_max_messages(self):
        ns = build_parser().parse_args(
            ["email", "autonomy", "run", "--max-messages", "5"]
        )
        assert ns.max_messages == 5

    def test_plain_query_path_is_unaffected(self):
        """Adding subparsers to email_parser must not break `-q`/`-i` flags."""
        ns = build_parser().parse_args(["email", "-q", "hi"])
        assert ns.action == "email"
        assert ns.query == "hi"
        assert getattr(ns, "email_action", None) is None


class TestDispatch:
    def test_handle_email_command_routes_autonomy_before_lemonade_check(self):
        ns = argparse.Namespace(action="email", email_action="autonomy")
        with (
            patch("gaia.cli.handle_email_autonomy_command") as handler,
            patch("gaia.cli.initialize_lemonade_for_agent") as init_lemonade,
        ):
            cli.handle_email_command(ns)
            handler.assert_called_once_with(ns)
            init_lemonade.assert_not_called()


class TestAutonomyStatus:
    def test_status_creates_session_then_prints_level(self, capsys):
        ns = argparse.Namespace(autonomy_action="status", session_id="cli")
        calls = []

        def fake_relay(agent_id, method, path, *, json_body=None):
            calls.append((agent_id, method, path, json_body))
            if path == "agent/session":
                return {"session_id": "cli", "created": True}
            return {
                "level": "earn_trust",
                "enabled": True,
                "trust_min_samples": 5,
                "trust_threshold": 0.85,
                "trusted_scope_count": 2,
                "scopes": [],
            }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 0
        assert calls[0] == ("email", "POST", "agent/session", {"session_id": "cli"})
        assert calls[1] == ("email", "GET", "agent/autonomy/cli", None)
        out = capsys.readouterr().out
        assert "level: earn_trust" in out
        assert "enabled: True" in out


class TestAutonomySetLevel:
    def test_set_level_round_trips(self, capsys):
        ns = argparse.Namespace(
            autonomy_action="set-level", session_id="cli", level="full"
        )
        calls = []

        def fake_relay(agent_id, method, path, *, json_body=None):
            calls.append((method, path, json_body))
            if path == "agent/session":
                return {"session_id": "cli", "created": False}
            return {"level": "full", "enabled": True}

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 0
        assert calls[1] == (
            "POST",
            "agent/autonomy",
            {"session_id": "cli", "level": "full"},
        )
        assert "full" in capsys.readouterr().out


class TestAutonomyRunRefusal:
    def test_run_surfaces_off_refusal_loudly(self, capsys):
        """#2528: when the sidecar refuses /run because autonomy is off, the
        CLI must exit non-zero with the refusal's actionable message — never
        print a quiet success."""
        ns = argparse.Namespace(
            autonomy_action="run", session_id="cli", max_messages=25
        )

        def fake_relay(agent_id, method, path, *, json_body=None):
            if path == "agent/session":
                return {"session_id": "cli", "created": False}
            raise DaemonError(
                "the 'email' agent refused POST agent/autonomy/run "
                "(HTTP 409): autonomy is off for session 'cli' — "
                "POST /v1/email/agent/autonomy to enable it."
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "autonomy is off" in err

    def test_run_reports_sidecar_unreachable_loudly(self, capsys):
        ns = argparse.Namespace(
            autonomy_action="run", session_id="cli", max_messages=25
        )

        def fake_relay(agent_id, method, path, *, json_body=None):
            raise DaemonError("could not reach the 'email' agent through the daemon")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 1
        assert "could not reach" in capsys.readouterr().err
