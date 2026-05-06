# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the ``gaia email`` CLI subcommand wiring.

Verifies argparse parses the flags correctly and the dispatch path
reaches the email-agent CLI module without unintended cloud-LLM flags.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestDispatch:
    """End-to-end dispatch tests — patch ``email_main`` and assert it's called."""

    def _invoke_main(self, argv):
        """gaia.cli.main reads sys.argv directly — patch it for the call."""
        import sys

        from gaia import cli

        old_argv = sys.argv
        sys.argv = ["gaia", *argv]
        try:
            try:
                cli.main()
            except SystemExit:
                pass
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

    def test_handle_email_command_passes_args_to_email_main(self):
        """``handle_email_command`` should defer to ``gaia.agents.email.cli.main``
        without the cloud-LLM flags."""
        import argparse

        from gaia import cli

        ns = argparse.Namespace(
            action="email",
            query="ping",
            interactive=False,
            verbose=False,
            debug=False,
            no_lemonade_check=True,
            base_url=None,
        )

        with patch("gaia.agents.email.cli.main") as email_main:

            async def _fake(args):
                return 0

            email_main.side_effect = _fake
            with patch(
                "gaia.cli.initialize_lemonade_for_agent", return_value=(True, None)
            ):
                with pytest.raises(SystemExit) as exc:
                    cli.handle_email_command(ns)
                assert exc.value.code == 0
            email_main.assert_called_once()
