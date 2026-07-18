# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ``gaia email`` CLI subcommand wiring.

Verifies argparse parses the flags correctly and the thin-client dispatch
path relays the query through the daemon without unintended cloud-LLM flags.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestDispatch:
    """Dispatch tests for the thin-client ``gaia email`` path (#2191).

    The query path no longer runs the email agent in-process; it relays through
    the always-on daemon via ``gaia.daemon.agent_query.run_query``. These tests
    patch that seam (and the CLI dispatcher) — never ``gaia_agent_email`` — so
    they run in framework-only envs without the standalone email wheel."""

    def _invoke_main(self, argv):
        """gaia.cli.main reads sys.argv directly — patch it for the call."""
        import sys

        from gaia import cli

        old_argv = sys.argv
        sys.argv = ["gaia", *argv]
        try:
            try:
                cli.main()
            except SystemExit as exc:
                assert exc.code == 0, f"unexpected non-zero exit: {exc.code}"
        finally:
            sys.argv = old_argv

    def test_email_subcommand_dispatches_to_handle_email_command(self):
        with (
            patch("gaia.cli.handle_email_command") as handler,
            patch("gaia.cli.initialize_lemonade_for_agent", return_value=(True, None)),
        ):
            self._invoke_main(["email", "-q", "ping"])
            handler.assert_called_once()
            args = handler.call_args[0][0]
            assert args.action == "email"
            assert args.query == "ping"

    def test_email_subcommand_does_not_pass_use_claude(self):
        """AC3: the gaia-email CLI path must not surface --use-claude."""
        with (
            patch("gaia.cli.handle_email_command") as handler,
            patch("gaia.cli.initialize_lemonade_for_agent", return_value=(True, None)),
        ):
            self._invoke_main(["email", "-q", "hi"])
            args = handler.call_args[0][0]
            # The flag may not exist on the namespace at all (best case).
            assert getattr(args, "use_claude", False) is False
            assert getattr(args, "use_chatgpt", False) is False

    def test_handle_email_command_relays_query_through_daemon(self):
        """``handle_email_command`` is a thin client (#2191): the query path
        ensures the daemon + ``email`` sidecar and relays through
        ``gaia.daemon.agent_query.run_query`` — NOT ``gaia_agent_email.cli.main``
        in-process. Assert the CLI args reach ``run_query`` intact."""
        import argparse

        from gaia import cli
        from gaia.daemon.agent_query import QueryOutcome

        ns = argparse.Namespace(
            action="email",
            query="ping",
            interactive=False,
            verbose=False,
            debug=False,
            no_lemonade_check=False,
            base_url=None,
            model="Gemma-4-E4B-it-GGUF",
            spec=False,
        )

        with (
            patch("gaia.daemon.agent_query.run_query") as run_query,
            patch(
                "gaia.cli.initialize_lemonade_for_agent", return_value=(True, None)
            ) as init_lemonade,
        ):
            run_query.return_value = QueryOutcome(
                exit_code=0, terminal_type="final", final_answer="pong"
            )
            with pytest.raises(SystemExit) as exc:
                cli.handle_email_command(ns)
            assert exc.value.code == 0

            run_query.assert_called_once()
            # agent_id + query flow through positionally; the model rides a kwarg.
            call = run_query.call_args
            assert call.args[0] == "email"
            assert call.args[1] == "ping"
            assert call.kwargs["model"] == "Gemma-4-E4B-it-GGUF"

            # AC3: the email path is local-LLM only — Lemonade is initialized
            # WITHOUT any cloud-provider flag, and none is relayed downstream.
            init_kwargs = init_lemonade.call_args.kwargs
            assert init_kwargs.get("use_claude", False) is False
            assert init_kwargs.get("use_chatgpt", False) is False
            assert "use_claude" not in call.kwargs
            assert "use_chatgpt" not in call.kwargs
