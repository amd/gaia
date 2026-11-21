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


def test_a_module_runs_by_its_old_name():
    done = subprocess.run(
        [sys.executable, "-m", "gaia_agent.entry", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "--serve" in done.stdout


def test_a_module_that_reads_its_file_runs_by_its_old_name():
    """``export_openapi`` resolves its artifact path from ``__file__`` at import."""
    runs = [
        subprocess.run(
            [sys.executable, "-m", f"{name}.export_openapi", "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        for name in ("gaia_agent", "gaiapy")
    ]
    old, new = runs
    assert old.returncode == 0, old.stderr
    assert old.stdout == new.stdout


def test_importing_by_the_old_name_keeps_the_real_spec():
    code = (
        "import importlib\n"
        "import gaiapy.stdio as first\n"
        "import gaia_agent.stdio as old\n"
        "import gaia_agent.tool_bundles as fresh\n"
        "import gaiapy.tool_bundles as new\n"
        "assert old is first and fresh is new\n"
        "for m in (old, fresh):\n"
        "    assert m.__spec__.name == m.__name__, m.__spec__.name\n"
        "    assert m.__spec__.origin == m.__file__\n"
        "new.FULL_CORE_TOOLS = None\n"
        "assert importlib.reload(fresh) is new\n"
        "assert new.FULL_CORE_TOOLS is not None, 'reload did not re-run it'\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr


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


def test_the_frozen_binary_carries_the_old_name():
    """A skill's tools.py loaded by the frozen sidecar may import gaia_agent."""
    from pathlib import Path

    freeze_py = Path(__file__).resolve().parents[1] / "packaging" / "freeze.py"
    spec = importlib.util.spec_from_file_location("gaia_freeze_alias", freeze_py)
    freeze = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(freeze)

    assert "gaiapy" in freeze.COLLECT_SUBMODULES
    assert "gaia_agent" in freeze.HIDDEN_IMPORTS
    # gaia_agent has no submodule files of its own to collect.
    assert "gaia_agent" not in freeze.COLLECT_SUBMODULES
    assert not freeze.is_excluded("gaia_agent")
    assert not any(
        m == "gaia_agent" or m.startswith("gaia_agent.") for m in freeze.EXCLUDES
    )
