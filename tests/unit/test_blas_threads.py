# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every GAIA process caps OpenBLAS's pool before numpy or faiss loads (#7291).

Uncapped, OpenBLAS commits a buffer per hardware thread at load: ~5 GB per
process on a 32-thread PC, and a dozen GAIA processes exhausted the commit limit.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from gaia import blas_threads

REPO = Path(__file__).resolve().parents[2]
LEAF = REPO / "src" / "gaia" / "blas_threads.py"
FREEZE_SCRIPTS = [
    REPO / "installer" / "core" / "freeze.py",
    REPO / "hub" / "agents" / "gaia" / "python" / "packaging" / "freeze.py",
]

#: Every module a GAIA process starts from: console scripts, ``python -m``
#: targets, and the flagship sidecar's transports. ``gaia_agent.entry`` imports
#: nothing at module scope, so its ``main`` is run too.
ENTRY_MODULES = [
    ("gaia.cli", False),
    ("gaia.ui.server", False),
    ("gaia.daemon.server", False),
    ("gaia.mcp.mcp_bridge", False),
    ("gaia_agent.entry", True),
    ("gaia_agent.stdio", False),
    ("gaia_agent.server", False),
]

# Records the environment at the moment numpy or faiss is first looked up, then
# starts the entry point and imports numpy, as the process would later.
_PROBE = textwrap.dedent("""
    import importlib, json, os, sys

    seen = {}

    class Probe:
        def find_spec(self, name, path=None, target=None):
            if name in ("numpy", "faiss") and name not in seen:
                seen[name] = os.environ.get("OPENBLAS_NUM_THREADS")
            return None

    sys.meta_path.insert(0, Probe())
    module = importlib.import_module(sys.argv[1])
    if sys.argv[2:] == ["run-main"]:
        try:
            module.main(["--help"])
        except SystemExit:
            pass
    import numpy
    print(json.dumps(seen))
    """)


def _clean_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in blas_threads.OPENBLAS_THREAD_VARS
    }
    env["LEMONADE_BASE_URL"] = "http://127.0.0.1:1"
    # The child must import this checkout, not whichever one is pip-installed.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "hub" / "agents" / "gaia" / "python")]
    )
    return env


def test_cap_applies_on_a_clean_environment():
    env: dict = {}
    assert blas_threads.cap_blas_threads(env) is True
    assert env == {"OPENBLAS_NUM_THREADS": str(blas_threads.BLAS_THREADS)}


@pytest.mark.parametrize("name", blas_threads.OPENBLAS_THREAD_VARS)
def test_a_user_set_pool_size_is_left_alone(name):
    env = {name: "16"}
    assert blas_threads.cap_blas_threads(env) is False
    assert env == {name: "16"}


@pytest.mark.parametrize(
    "module,run_main", ENTRY_MODULES, ids=[m for m, _ in ENTRY_MODULES]
)
def test_entry_point_caps_before_numpy_loads(module, run_main):
    if importlib.util.find_spec(module.split(".")[0]) is None:
        pytest.skip(f"{module} is not installed")
    if module == "gaia_agent.server" and importlib.util.find_spec("fastapi") is None:
        pytest.skip("gaia_agent.server needs the [ui] extra")
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module] + (["run-main"] if run_main else []),
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    expected = str(blas_threads.BLAS_THREADS)
    assert all(
        v == expected for v in seen.values()
    ), f"{module} loaded {sorted(seen)} before OpenBLAS was capped: {seen}"


def test_user_value_reaches_numpy_through_an_entry_point():
    env = _clean_env()
    env["OPENBLAS_NUM_THREADS"] = "7"
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, "gaia.cli"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert json.loads(result.stdout.strip().splitlines()[-1])["numpy"] == "7"


def test_leaf_runs_as_a_pyinstaller_runtime_hook():
    """A runtime hook executes as ``__main__`` before the entry script."""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import os, runpy, sys; runpy.run_path(sys.argv[1], run_name='__main__');"
            " print(os.environ.get('OPENBLAS_NUM_THREADS'))",
            str(LEAF),
        ],
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(blas_threads.BLAS_THREADS)


def test_leaf_imports_only_the_stdlib():
    tree = ast.parse(LEAF.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names), imported


@pytest.mark.parametrize("script", FREEZE_SCRIPTS, ids=lambda p: p.parent.name)
def test_freeze_adds_the_hook_on_every_platform(script):
    """The hook is added straight in ``build()``, not under a platform check."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    hook_target = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "BLAS_THREADS_HOOK" for t in node.targets
        ):
            hook_target = ast.unparse(node.value)
    assert hook_target and hook_target.endswith("'blas_threads.py'"), hook_target
    build = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build"
    )
    assert any(
        isinstance(stmt, ast.AugAssign)
        and "--runtime-hook" in ast.unparse(stmt.value)
        and "BLAS_THREADS_HOOK" in ast.unparse(stmt.value)
        for stmt in build.body
    ), f"{script} does not pass the BLAS runtime hook unconditionally"
