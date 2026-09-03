# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""CLI surface for ``gaia lemonade embedded``.

``tests/unit/test_lemonade_embedded.py`` covers the manager class and stops at
its boundary. These tests cover the layer above it: that every subcommand is
reachable through the real parser, that refusals exit non-zero, and -- the
failure mode this file exists for -- that every ``gaia ...`` remedy the module
tells users to run is a command the parser actually accepts.
"""

import re
import shlex
from pathlib import Path

import pytest

from gaia.llm import lemonade_embedded


def _parser():
    from gaia.cli import build_parser

    return build_parser()


class TestParsing:
    """Every documented invocation reaches the parser."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["lemonade", "embedded", "start"],
            ["lemonade", "embedded", "start", "--port", "13399"],
            ["lemonade", "embedded", "start", "--no-install"],
            ["lemonade", "embedded", "start", "--timeout", "90"],
            ["lemonade", "embedded", "stop"],
            ["lemonade", "embedded", "status"],
            ["lemonade", "embedded", "install"],
            ["lemonade", "embedded", "install", "--force"],
            ["lemonade", "embedded", "install-backend", "llamacpp:vulkan"],
        ],
    )
    def test_accepted(self, argv):
        parsed = _parser().parse_args(argv)
        assert parsed.action == "lemonade"
        assert parsed.lemonade_action == "embedded"

    @pytest.mark.parametrize(
        "argv",
        [
            # install-backend needs a spec; a typo'd verb must not be silently
            # swallowed as one.
            ["lemonade", "embedded", "install-backend"],
            ["lemonade", "embedded", "staart"],
        ],
    )
    def test_rejected(self, argv):
        with pytest.raises(SystemExit):
            _parser().parse_args(argv)

    def test_port_is_parsed_as_an_integer(self):
        assert (
            _parser()
            .parse_args(["lemonade", "embedded", "start", "--port", "13399"])
            .port
            == 13399
        )


class TestRemediesAreRunnable:
    """Remedy strings must be commands the CLI accepts, not just plausible text.

    A message telling a stuck user to run something the parser rejects leaves
    them stuck. Extract every ``gaia ...`` command the module emits and parse it.
    """

    def test_every_gaia_command_in_the_module_parses(self):
        source = Path(lemonade_embedded.__file__).read_text(encoding="utf-8")
        # Commands are written inside backticks in the error messages.
        commands = set(re.findall(r"`(gaia [^`]+)`", source))
        assert commands, "no remedy commands found -- has the format changed?"

        for command in sorted(commands):
            # Stand in for `<other>`-style prose placeholders and `{port}`
            # f-string fields, which read literally from source.
            command = re.sub(r"<[^>]+>|\{[^}]+\}", "13399", command)
            argv = shlex.split(command)
            try:
                _parser().parse_args(argv[1:])
            except SystemExit as exc:
                pytest.fail(f"parser rejected remedy {command!r} (exit={exc.code})")


class TestDispatch:
    """Refusals must exit non-zero so scripts can chain on them."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["lemonade"],
            ["lemonade", "embedded"],
        ],
    )
    def test_missing_subaction_exits_non_zero(self, argv, capsys):
        from gaia.cli import handle_lemonade_command

        with pytest.raises(SystemExit) as exc:
            handle_lemonade_command(_parser().parse_args(argv))
        assert exc.value.code == 1
        assert "❌" in capsys.readouterr().out

    def test_manager_errors_exit_non_zero_with_the_message(
        self, monkeypatch, capsys, tmp_path
    ):
        from gaia.cli import handle_lemonade_embedded_command

        def boom(*_args, **_kwargs):
            raise lemonade_embedded.EmbeddedLemonadeError("the actionable reason")

        monkeypatch.setattr(lemonade_embedded.EmbeddedLemonade, "start", boom)
        monkeypatch.setenv("GAIA_HOME", str(tmp_path))

        with pytest.raises(SystemExit) as exc:
            handle_lemonade_embedded_command(
                _parser().parse_args(["lemonade", "embedded", "start"])
            )
        assert exc.value.code == 1
        assert "the actionable reason" in capsys.readouterr().out

    def test_os_errors_are_explained_not_dumped(self, monkeypatch, capsys, tmp_path):
        # A noexec mount or read-only home raises OSError out of Popen; the user
        # should get a sentence, not a traceback.
        from gaia.cli import handle_lemonade_embedded_command

        def boom(*_args, **_kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(lemonade_embedded.EmbeddedLemonade, "start", boom)
        monkeypatch.setenv("GAIA_HOME", str(tmp_path))

        with pytest.raises(SystemExit) as exc:
            handle_lemonade_embedded_command(
                _parser().parse_args(["lemonade", "embedded", "start"])
            )
        assert exc.value.code == 1
        assert "Could not run embedded Lemonade" in capsys.readouterr().out


class TestStatusOutput:
    """`status` must be readable from a cold state."""

    def test_cold_status_reports_not_installed(self, monkeypatch, capsys, tmp_path):
        from gaia.cli import handle_lemonade_embedded_command

        monkeypatch.setenv("GAIA_HOME", str(tmp_path))
        handle_lemonade_embedded_command(
            _parser().parse_args(["lemonade", "embedded", "status"])
        )
        out = capsys.readouterr().out
        assert "Installed: no" in out
        assert "Running:   no" in out
        assert "Backends:  none" in out
