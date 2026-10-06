# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init --minimal` help must quote the download size the profile really has.

#3217: the help said ~400 MB after the minimal profile moved to a ~3 GB model,
so users picking it to save disk budgeted for a fraction of the real download.
"""

from __future__ import annotations

import argparse


def _init_minimal_help() -> str:
    from gaia.cli import build_parser  # deferred — runs cli.py module side-effects

    parser = build_parser()
    subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    init_parser = subparsers.choices["init"]
    action = next(a for a in init_parser._actions if "--minimal" in a.option_strings)
    return action.help


def test_minimal_help_quotes_the_profile_size():
    from gaia.installer.init_command import INIT_PROFILES

    size = INIT_PROFILES["minimal"]["approx_size"]
    assert size in _init_minimal_help()
