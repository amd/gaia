# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Importing gaia must not reconfigure the host's root logging.

An application that embeds the SDK got its root logger reconfigured to INFO,
with GAIA's console and file handlers attached, just by importing
``gaia.agents.base``. Three sources contributed: two ``logging.basicConfig``
calls in ``gaia/agents/base`` and the module-level ``GaiaLogger()``
instantiation in ``gaia/logger.py``. All three now live behind the explicit
``gaia.logger.configure`` call that owning entry points make. These tests pin
the contract: import purity in fresh interpreters, and the behavior of
``configure`` and the inert ``get_logger`` path.
"""

import ast
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia import logger as gaia_logger

REPO_ROOT = Path(__file__).resolve().parents[2]

_IMPORT_AND_PROBE_ROOT = (
    "import logging\n"
    "root = logging.getLogger()\n"
    "pre = (root.level, len(root.handlers))\n"
    "{imports}\n"
    "post = (root.level, len(root.handlers))\n"
    "print(pre == post, pre)\n"
)

_IMPORTS = {
    "gaia-logger": "import gaia.logger",
    "gaia": "import gaia",
    "agents-base": "import gaia.agents.base",
}


def _child_env(tmp_path):
    env = dict(os.environ)
    # The suite moves GAIA's log file around; these tests are about import
    # purity and must not depend on (or write to) whichever path is current.
    env.pop(gaia_logger.LOG_FILE_ENV, None)
    for key in ("TMP", "TEMP", "TMPDIR"):
        env[key] = str(tmp_path)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPO_ROOT / "src"), env.get("PYTHONPATH")])
    )
    return env


def _run_child(code, tmp_path):
    return subprocess.run(
        [sys.executable, "-c", code],
        env=_child_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def _child_unchanged(result):
    assert result.returncode == 0, result.stderr
    line = result.stdout.strip().splitlines()[-1]
    unchanged, pre = line.split(" ", 1)
    return unchanged == "True", pre


def test_import_gaia_logger_leaves_root_untouched(tmp_path):
    result = _run_child(
        _IMPORT_AND_PROBE_ROOT.format(imports=_IMPORTS["gaia-logger"]),
        tmp_path,
    )
    unchanged, pre = _child_unchanged(result)
    assert unchanged, f"root changed on import; pre-import state was {pre}"


def test_import_gaia_leaves_root_untouched(tmp_path):
    # `gaia/__init__` imports `gaia.agents.base`, the path that used to
    # reconfigure the root logger via `logging.basicConfig` (#4775).
    result = _run_child(
        _IMPORT_AND_PROBE_ROOT.format(imports=_IMPORTS["gaia"]),
        tmp_path,
    )
    unchanged, pre = _child_unchanged(result)
    assert unchanged, f"root changed on import; pre-import state was {pre}"


def test_import_gaia_agents_base_leaves_root_untouched(tmp_path):
    # The two `logging.basicConfig` calls lived in this package.
    result = _run_child(
        _IMPORT_AND_PROBE_ROOT.format(imports=_IMPORTS["agents-base"]),
        tmp_path,
    )
    unchanged, pre = _child_unchanged(result)
    assert unchanged, f"root changed on import; pre-import state was {pre}"


def test_configure_installs_and_is_idempotent(tmp_path):
    code = (
        "import logging\n"
        "from gaia.logger import configure, get_logger\n"
        "first = configure()\n"
        "root = logging.getLogger()\n"
        "root_state = (root.level, [type(h).__name__ for h in root.handlers])\n"
        "probe = get_logger('gaia.probe')\n"
        "second = configure()\n"
        "print(root_state)\n"
        "print(probe.level == logging.NOTSET, first is second)\n"
    )
    result = _run_child(code, tmp_path)
    assert result.returncode == 0, result.stderr
    root_state = ast.literal_eval(result.stdout.splitlines()[0])
    probe_notset, same_manager = result.stdout.splitlines()[1].split()
    level, handler_names = root_state
    assert level == logging.INFO
    assert any("FileHandler" in name for name in handler_names)
    assert any("StreamHandler" in name for name in handler_names)
    # get_logger post-configure maps to the manager, which pins the module
    # default level.
    assert probe_notset == "False"
    assert same_manager == "True"


def test_get_logger_pre_configure_in_and_of_itself(monkeypatch):
    monkeypatch.setattr(gaia_logger, "log_manager", None)
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    logger = gaia_logger.get_logger("gaia.probe.purity")
    try:
        assert (root.level, len(root.handlers)) == (before[0], len(before[1]))
        assert logger.level == logging.NOTSET
        assert logger.handlers == []
    finally:
        root.handlers[:] = before[1]


def test_route_console_logging_to_stderr_safe_before_configure(monkeypatch):
    """Pre-configure the call inspects the root handler list, not the manager."""
    monkeypatch.setattr(gaia_logger, "log_manager", None)
    root = logging.getLogger()
    probe = logging.StreamHandler(sys.stdout)
    root.addHandler(probe)
    try:
        gaia_logger.route_console_logging_to_stderr()
        assert probe.stream is sys.stderr
    finally:
        root.removeHandler(probe)


def test_flagship_sidecar_writes_gaia_log(monkeypatch, tmp_path):
    """Regression: the HTTP sidecar calls configure() so the log file gets
    written. Before the fix, importing the entry point left log_manager as
    None and no log file was created."""
    entry = pytest.importorskip("gaia_agent.entry")
    log_file = tmp_path / "sidecar.log"
    monkeypatch.setenv("GAIA_LOG_FILE", str(log_file))
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(tmp_path))

    # Reset the singleton so configure() installs a fresh manager with our
    # temp log file (configure() is idempotent).
    monkeypatch.setattr(gaia_logger, "log_manager", None)
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    root.handlers.clear()

    try:
        with patch("uvicorn.run"):
            entry.main(["--serve", "--host", "127.0.0.1", "--port", "1"])

        assert log_file.exists(), "configure() should have created gaia.log"

        # Verify a gaia_agent.* logger can write to the file post-configure.
        logger = gaia_logger.get_logger("gaia_agent.sidecar_test")
        logger.info("regression check")
        assert "regression check" in log_file.read_text(encoding="utf-8")
    finally:
        # Close and remove the file handler configure() installed, and close
        # the console handler, so later tests don't inherit them.
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()
        root.handlers.extend(saved_handlers)
        root.setLevel(saved_level)


# Every process GAIA spawns, or documents running with ``python -m``, must
# install logging itself now that importing gaia no longer does.
_ENTRY_POINTS_HELP = [
    ("gaia.cli", "main", None),
    ("gaia.mcp.mcp_bridge", "main", None),
    ("gaia.ui.server", "main", None),
    ("gaia.mcp.servers.agent_ui_mcp", "main", None),
    ("gaia.mcp.servers.tui_mcp", "main", None),
    ("gaia.mcp.servers.engineering_mcp", "main", ["--help"]),
    ("gaia.connectors.cli", "main", ["--help"]),
    ("gaia_agent_chat.app", "main", None),
    ("gaia_agent_email.server", "main", ["--help"]),
    ("gaia_agent_email.mcp_server", "main", None),
]


@pytest.fixture
def unconfigured(monkeypatch, tmp_path):
    """Start with no GAIA logging installed; restore the root logger after."""
    monkeypatch.setenv(gaia_logger.LOG_FILE_ENV, str(tmp_path / "gaia.log"))
    monkeypatch.setattr(gaia_logger, "log_manager", None)
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(saved_level)


@pytest.mark.parametrize(
    "module_name,func_name,argv",
    _ENTRY_POINTS_HELP,
    ids=[f"{m}:{f}" for m, f, _ in _ENTRY_POINTS_HELP],
)
def test_entry_point_configures_logging(
    unconfigured, monkeypatch, module_name, func_name, argv
):
    module = pytest.importorskip(module_name)
    assert gaia_logger.log_manager is None, "importing must not configure"
    monkeypatch.setattr(sys, "argv", [module_name, "--help"])
    with pytest.raises(SystemExit):
        if argv is None:
            getattr(module, func_name)()
        else:
            getattr(module, func_name)(argv)
    assert (
        gaia_logger.log_manager is not None
    ), f"{module_name}:{func_name} must call gaia.logger.configure()"


def test_eval_bench_child_configures_logging(unconfigured):
    from gaia.eval.bench import gaia_child

    assert gaia_child.main([]) == 2
    assert gaia_logger.log_manager is not None


def _calls_configure(node):
    return any(
        isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "configure")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "configure")
        )
        for n in ast.walk(node)
    )


# Entry points that start a server or parse argv before configuring, so they
# are checked statically instead of by running them.
_ENTRY_POINTS_STATIC = [
    ("src/gaia/daemon/server.py", "run"),
    ("src/gaia/api/app.py", "main"),
    ("src/gaia/chat/app.py", "main"),
    ("src/gaia/apps/llm/app.py", "cli_main"),
]


@pytest.mark.parametrize("rel_path,func_name", _ENTRY_POINTS_STATIC)
def test_server_entry_point_calls_configure(rel_path, func_name):
    tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    funcs = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == func_name
    ]
    assert len(funcs) == 1, f"{rel_path}:{func_name} not found"
    assert _calls_configure(funcs[0]), f"{rel_path}:{func_name} skips configure()"


@pytest.mark.parametrize("agent", ["gaia", "email"])
def test_sidecar_packaging_entry_configures_at_module_level(agent):
    """The frozen binary and daemon dev mode (``uvicorn server:app``) load this
    module without calling any main(), so configure() must be top-level."""
    path = REPO_ROOT / "hub" / "agents" / agent / "python" / "packaging" / "server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, ast.Expr)]
    assert any(_calls_configure(n) for n in top_level), f"{path} skips configure()"


def test_flagship_frozen_entry_dispatches_scripts_before_configure():
    """``run_python`` scripts must start before GAIA (and its logging) loads."""
    path = REPO_ROOT / "hub" / "agents" / "gaia" / "python" / "packaging" / "server.py"
    source = path.read_text(encoding="utf-8")
    assert source.index("python_dispatch(sys.argv[1:])") < source.index("\nconfigure()")


@pytest.mark.parametrize(
    "module_name",
    [
        "gaia.mcp.servers.agent_ui_mcp",
        "gaia.mcp.servers.tui_mcp",
        "gaia.connectors.cli",
        "gaia.eval.bench.gaia_child",
    ],
)
def test_entry_modules_do_not_configure_on_import(tmp_path, module_name):
    result = _run_child(
        _IMPORT_AND_PROBE_ROOT.format(imports=f"import {module_name}"), tmp_path
    )
    unchanged, pre = _child_unchanged(result)
    assert unchanged, f"{module_name} changed the root logger on import ({pre})"
