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
import logging
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

    def test_run_surfaces_connectors_error_detail_loudly(self, capsys):
        """#2617: when the sidecar route maps an unhandled ``ConnectorsError``
        to an HTTP 500 with a real ``detail`` body (the fix under test in
        ``agent_routes.py``), ``relay_json`` already turns that into a
        ``DaemonError`` carrying the full actionable message (see
        ``src/gaia/daemon/agent_control.py:67-70``) — this is a regression
        guard proving the CLI passes that message through to the user
        end-to-end, not a red test for the route fix itself."""
        ns = argparse.Namespace(
            autonomy_action="run", session_id="cli", max_messages=25
        )

        def fake_relay(agent_id, method, path, *, json_body=None):
            if path == "agent/session":
                return {"session_id": "cli", "created": False}
            raise DaemonError(
                "the 'email' agent refused POST agent/autonomy/run "
                "(HTTP 500): All connected mailboxes failed during triage: "
                "microsoft: CONNECTOR_ERROR: no forwarded 'microsoft' "
                "credential is available to the email sidecar. ... gaia "
                "connectors connect microsoft --scopes <scopes> "
                "--grant-agent installed:email ..."
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "gaia connectors connect" in err
        assert "microsoft" in err


class TestPrintAutonomyRun:
    """Direct tests of ``_print_autonomy_run`` (#2651) — no existing coverage
    before this. ``errors``/``stopped`` (added to the report by #2624/#2625)
    reached the JSON body over REST already; this print function silently
    dropped both, so a CLI user saw the same clean summary line whether the
    run succeeded cleanly or hit failures partway through."""

    def test_clean_run_shows_zero_errors_and_no_stop_line(self, capsys):
        cli._print_autonomy_run(
            {
                "executed": [{"message_id": "m1"}],
                "proposals": [],
                "skipped": 2,
                "already_proposed": 0,
                "errors": [],
                "stopped": None,
            }
        )
        out = capsys.readouterr().out
        assert "executed=1 proposals=0 skipped=2 already_proposed=0 errors=0" in out
        assert "stopped early" not in out

    def test_nonzero_errors_are_counted(self, capsys):
        cli._print_autonomy_run(
            {
                "executed": [{"message_id": "m1"}],
                "proposals": [],
                "skipped": 0,
                "already_proposed": 0,
                "errors": [
                    {
                        "message_id": "m2",
                        "error_type": "ConnectionError",
                        "error": "502",
                    },
                    {
                        "message_id": "m3",
                        "error_type": "ConnectionError",
                        "error": "502",
                    },
                ],
                "stopped": None,
            }
        )
        out = capsys.readouterr().out
        assert "errors=2" in out

    def test_consecutive_failures_stop_reason_is_printed(self, capsys):
        cli._print_autonomy_run(
            {
                "executed": [],
                "proposals": [],
                "skipped": 0,
                "already_proposed": 0,
                "errors": [{"message_id": f"m{i}"} for i in range(3)],
                "stopped": "consecutive_failures",
            }
        )
        out = capsys.readouterr().out
        assert "stopped early: consecutive_failures" in out

    def test_autonomy_off_stop_reason_is_printed(self, capsys):
        cli._print_autonomy_run(
            {
                "executed": [],
                "proposals": [],
                "skipped": 0,
                "already_proposed": 0,
                "errors": [],
                "stopped": "autonomy_off",
            }
        )
        out = capsys.readouterr().out
        assert "stopped early: autonomy_off" in out

    def test_missing_keys_default_safely(self, capsys):
        """A minimal/older-shaped body (missing errors/stopped) must not
        raise — the same ``.get()``-with-default pattern the other whitelist
        fields already use keeps this backward compatible."""
        cli._print_autonomy_run({"executed": [], "proposals": []})
        out = capsys.readouterr().out
        assert "errors=0" in out
        assert "stopped early" not in out


class TestAutonomyRunEndToEnd:
    def test_run_prints_error_count_and_stop_reason_via_relay(self, capsys):
        ns = argparse.Namespace(
            autonomy_action="run", session_id="cli", max_messages=25
        )

        def fake_relay(agent_id, method, path, *, json_body=None):
            if path == "agent/session":
                return {"session_id": "cli", "created": False}
            return {
                "executed": [{"message_id": "m1"}],
                "proposals": [],
                "skipped": 0,
                "already_proposed": 0,
                "errors": [
                    {
                        "message_id": "m2",
                        "error_type": "ConnectionError",
                        "error": "502",
                    }
                ],
                "stopped": "consecutive_failures",
            }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "errors=1" in out
        assert "stopped early: consecutive_failures" in out


class TestAutonomyRunErrorSurfacedOnBothChannels:
    """#2617: ``handle_email_autonomy_command``'s ``except DaemonError`` block
    intentionally emits the failure on two channels — ``log.error(...)`` and
    ``print(f"❌ {e}", file=sys.stderr)``. An earlier draft of this fix
    dropped one of the two; keeping only the stderr print would make the
    failure invisible to ``gaia diagnostics`` (which bundles ~/.gaia/gaia.log,
    not stdout capture), and keeping only the log line would drop the ``❌``
    convention every sibling ``except DaemonError`` handler in this file
    uses. The duplicate line at the terminal is cosmetic; a missing bug
    report is not — so both are required, not a bug to fix."""

    def test_error_message_reaches_both_the_log_and_stderr(self, capsys, caplog):
        ns = argparse.Namespace(
            autonomy_action="run", session_id="cli", max_messages=25
        )
        distinctive = (
            "no forwarded 'microsoft' credential is available to the email sidecar"
        )

        def fake_relay(agent_id, method, path, *, json_body=None):
            if path == "agent/session":
                return {"session_id": "cli", "created": False}
            raise DaemonError(
                "the 'email' agent refused POST agent/autonomy/run "
                "(HTTP 500): All connected mailboxes failed during triage: "
                f"microsoft: CONNECTOR_ERROR: {distinctive}. ... gaia "
                "connectors connect microsoft --scopes <scopes> "
                "--grant-agent installed:email ..."
            )

        with caplog.at_level(logging.ERROR, logger="gaia.cli"):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("gaia.daemon.agent_control.relay_json", fake_relay)
                with pytest.raises(SystemExit) as exc:
                    cli.handle_email_autonomy_command(ns)
        assert exc.value.code == 1

        # The durable record `gaia diagnostics` bundles from ~/.gaia/gaia.log.
        error_records_with_message = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and distinctive in r.getMessage()
        ]
        assert error_records_with_message, (
            "the failure must reach an ERROR-level log record (the bug-report "
            f"channel), not just stderr; caplog had: {caplog.records}"
        )

        # The immediate, guaranteed-visible terminal line.
        err = capsys.readouterr().err
        assert (
            distinctive in err
        ), f"the failure must also print to stderr; got: {err!r}"
