# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia_agent``, the package's old name, still imports and is ``gaiapy`` itself."""

import importlib
import importlib.util
import subprocess
import sys


def test_the_old_name_is_the_same_package():
    import gaia_agent
    import gaiapy

    assert gaia_agent is gaiapy


def test_a_submodule_is_one_module_under_both_names():
    old = importlib.import_module("gaia_agent.tool_bundles")
    new = importlib.import_module("gaiapy.tool_bundles")

    assert old is new
    from gaia_agent.tool_bundles import FULL_CORE_TOOLS as through_old

    assert through_old is new.FULL_CORE_TOOLS


def test_the_old_name_works_when_it_is_imported_first():
    code = (
        "import gaia_agent.entry as old, gaiapy.entry as new, sys; "
        "sys.exit(0 if old is new and old.main is new.main else 1)"
    )
    done = subprocess.run([sys.executable, "-c", code], check=False, timeout=60)
    assert done.returncode == 0


def test_find_spec_does_not_invent_a_missing_submodule():
    assert importlib.util.find_spec("gaia_agent.no_such_module") is None
    assert importlib.util.find_spec("gaia_agent.tool_bundles") is not None


def test_a_missing_submodule_still_raises_import_error():
    try:
        importlib.import_module("gaia_agent.no_such_module")
    except ModuleNotFoundError as e:
        assert "no_such_module" in str(e)
    else:
        raise AssertionError("a module that does not exist was imported")


def test_both_command_names_start_the_same_program():
    from pathlib import Path

    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'gaiapy = "gaiapy.entry:main"' in pyproject
    assert 'gaia-agent = "gaiapy.entry:main"' in pyproject
