# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A lemond that dies without its tree must not leave its model servers behind.

Each llama-server holds gigabytes of GPU and system memory. When lemond is
killed without its tree or crashes, they keep running, and the next start
loads the same models again beside them.

The processes here are real: a copy of the Python interpreter stands in for
llama-server inside the install's ``cache/bin``, and for lemond inside its
``dist`` tree, so the path checks run against what the OS reports.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from gaia.llm import lemonade_embedded as emb
from gaia.llm.lemonade_embedded import EmbeddedLemonade

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="copies the interpreter under another name; the layout is Windows'",
)

# A stand-in lemond: start one backend (argv 1) running code (argv 3) that
# touches a marker file (argv 2) once it runs, then wait.
_LEMOND = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.argv[1], '-c', sys.argv[3], sys.argv[2]]); "
    "time.sleep(120)"
)
_BACKEND = "import sys, time; open(sys.argv[1], 'w').close(); time.sleep(120)"


def _interpreter_as(target: Path) -> Path:
    """Copy the base interpreter, with the DLLs it loads, to *target*."""
    target.parent.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    shutil.copy2(Path(sys._base_executable), target)  # pylint: disable=protected-access
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, target.parent / dll.name)
    return target


@pytest.fixture
def manager(tmp_path):
    return EmbeddedLemonade(home=tmp_path)


@pytest.fixture
def spawned(monkeypatch):
    """Every process a test starts, killed at the end even once orphaned."""
    monkeypatch.setattr(emb, "_SPAWNED", {})
    procs = []
    yield procs
    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


def _env():
    return {**os.environ, "PYTHONHOME": sys.base_prefix}


def _start_tree(lemond: Path, backend: Path, spawned) -> tuple:
    """Run *lemond* (an interpreter copy) starting one *backend*; return both
    once the backend is running its own code."""
    marker = backend.parent / f"started-{time.monotonic_ns()}"
    parent = psutil.Popen(
        [str(lemond), "-c", _LEMOND, str(backend), str(marker), _BACKEND],
        env=_env(),
    )
    spawned.append(parent)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.1)
    assert marker.exists(), "the stand-in lemond never started its backend"
    for child in parent.children():
        if Path(child.exe()) == backend:  # not the conhost it may get
            spawned.append(child)
            return parent, child
    raise AssertionError("the backend is running but is not lemond's child")


def _orphan_backend(lemond: Path, backend: Path, spawned) -> psutil.Process:
    """Start a tree, then kill only its lemond, as a crash or `/F` does."""
    parent, child = _start_tree(lemond, backend, spawned)
    parent.kill()  # TerminateProcess: the backend survives
    parent.wait(timeout=10)
    assert child.is_running(), "the backend died with lemond; nothing to test"
    return child


def _gone(proc: psutil.Process, timeout: float = 10) -> bool:
    try:
        proc.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        return False
    return True


def _backend_path(manager) -> Path:
    return manager.cache_dir / "bin" / "llamacpp" / "vulkan" / "llama-server.exe"


def _lemond_path(manager) -> Path:
    return manager.dist_dir / "lemond.exe"


def test_a_dead_lemonds_backends_are_reaped_when_status_finds_it_gone(
    manager, tmp_path, spawned
):
    backend = _interpreter_as(_backend_path(manager))
    stand_in = _interpreter_as(tmp_path / "other" / "lemond.exe")
    orphan = _orphan_backend(stand_in, backend, spawned)
    manager._write_state(
        {"pid": spawned[0].pid, "port": 65530, "api_key": "k", "version": "x"}
    )

    assert manager.status().running is False

    assert _gone(orphan), "the dead lemond's llama-server is still running"


def test_a_start_reaps_backends_an_earlier_lemond_left(
    manager, tmp_path, spawned, monkeypatch
):
    """No record is left to say which lemond died; the path and the dead
    parent are enough."""
    backend = _interpreter_as(_backend_path(manager))
    stand_in = _interpreter_as(tmp_path / "other" / "lemond.exe")
    orphan = _orphan_backend(stand_in, backend, spawned)

    monkeypatch.setattr(manager, "is_installed", lambda: True)
    monkeypatch.setattr(manager, "write_config", lambda: None)
    monkeypatch.setattr(manager, "_health", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(
        emb.subprocess,
        "Popen",
        lambda argv, **kw: SimpleNamespace(pid=4321, poll=lambda: None),
    )
    manager.start(port=65531)

    assert _gone(orphan), "start left the earlier lemond's llama-server running"


def test_a_running_lemonds_backends_are_left_alone(manager, spawned):
    backend = _interpreter_as(_backend_path(manager))
    lemond = _interpreter_as(_lemond_path(manager))
    _, child = _start_tree(lemond, backend, spawned)

    manager._reap_orphaned_backends()

    assert not _gone(child, timeout=2), "killed a live lemond's backend"


def test_backends_outside_this_install_are_never_touched(manager, tmp_path, spawned):
    """Another Lemonade's llama-server, even orphaned, is not GAIA's to kill."""
    foreign = _interpreter_as(tmp_path / "users-lemonade" / "llama-server.exe")
    stand_in = _interpreter_as(tmp_path / "other" / "lemond.exe")
    orphan = _orphan_backend(stand_in, foreign, spawned)

    manager._reap_orphaned_backends()

    assert not _gone(orphan, timeout=2), "killed a backend GAIA does not own"
