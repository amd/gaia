# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A command `main()` cannot dispatch must fail, not exit 0 (#3488).

`gaia report` stayed registered after its generator was deleted, so it fell
through to the unknown-action path, printed help, and exited 0.
"""

import argparse
import inspect
import sys

import pytest

from gaia import cli


def _subcommands():
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices


def test_report_is_not_a_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["report", "-d", "./eval_results"])
    assert exc.value.code == 2
    assert "invalid choice: 'report'" in capsys.readouterr().err


def test_youtube_without_a_url_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["youtube"])
    assert exc.value.code == 2
    assert "--download-transcript" in capsys.readouterr().err


@pytest.mark.parametrize("name", sorted(_subcommands()))
def test_every_command_has_a_dispatch_branch(name):
    assert f'"{name}"' in inspect.getsource(cli.main)


def test_undispatched_command_exits_nonzero(monkeypatch, capsys):
    real_build_parser = cli.build_parser

    def build_with_orphan():
        parser = real_build_parser()
        sub = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        sub.add_parser("orphan")
        return parser

    monkeypatch.setattr(cli, "build_parser", build_with_orphan)
    monkeypatch.setattr(sys, "argv", ["gaia", "orphan"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "usage:" in capsys.readouterr().err
