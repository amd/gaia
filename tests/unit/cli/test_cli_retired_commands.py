# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A removed command names its replacement instead of "invalid choice".

`gaia email` went with the standalone email agent; scripts and muscle memory
that still call it must learn the flagship handles mail now.
"""

import argparse
import sys

import pytest

from gaia import cli


def _subcommands():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices


@pytest.mark.parametrize(
    "argv",
    [
        ["email"],
        ["email", "triage"],
        ["email", "--autonomy", "on", "--json"],
        ["email", "-h"],
    ],
)
def test_gaia_email_points_at_the_flagship(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" not in err
    assert "usage:" not in err
    assert "gaia chat" in err
    assert "https://amd-gaia.ai/docs/guides/email" in err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["chat", "--query", "email"],
        ["-p", "email"],
        ["emails"],
        ["Email"],
        ["connectors", "connect", "google"],
    ],
)
def test_only_the_retired_name_in_command_position_is_caught(argv):
    assert cli._retired_command_message(argv) is None


def test_retired_names_do_not_shadow_a_live_command():
    assert not set(cli.RETIRED_TOP_LEVEL_COMMANDS) & set(_subcommands())


def test_a_misspelled_command_still_gets_argparse_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gaia", "emails"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "invalid choice: 'emails'" in capsys.readouterr().err
