# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One model-driving eval per machine, on Windows too.

The previous lock needed fcntl and did nothing on Windows, where every eval
machine runs; two evals shared one Lemonade, evicted each other's models and
corrupted both runs' timings.
"""

import subprocess
import sys
import textwrap

import psutil
import pytest

from gaia.eval import eval_lock


@pytest.fixture(autouse=True)
def _private_lock(tmp_path, monkeypatch):
    monkeypatch.delenv(eval_lock.BYPASS_ENV, raising=False)
    monkeypatch.setattr(eval_lock, "LOCK_FILE", tmp_path / "gaia-eval.lock")
    monkeypatch.setattr(eval_lock, "HOLDER_FILE", tmp_path / "gaia-eval.holder.json")


def _hold_in_child(tmp_path, seconds=30):
    """Start a process that takes the lock and holds it; returns once held."""
    ready = tmp_path / "ready"
    code = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from gaia.eval import eval_lock
        eval_lock.LOCK_FILE = Path({str(eval_lock.LOCK_FILE)!r})
        eval_lock.HOLDER_FILE = Path({str(eval_lock.HOLDER_FILE)!r})
        with eval_lock.exclusive_eval("gaia eval tasks run"):
            Path({str(ready)!r}).write_text("1")
            time.sleep({seconds})
        """)
    child = subprocess.Popen([sys.executable, "-c", code])
    for _ in range(300):
        if ready.exists():
            return child
        if child.poll() is not None:
            raise AssertionError("the holder exited before taking the lock")
        subprocess.run([sys.executable, "-c", "import time; time.sleep(0.05)"])
    child.kill()
    raise AssertionError("the holder never took the lock")


def _kill_tree(proc):
    """The venv's python.exe is a launcher; the lock is held by its child."""
    parent = psutil.Process(proc.pid)
    tree = parent.children(recursive=True) + [parent]
    for p in tree:
        p.kill()
    psutil.wait_procs(tree, timeout=10)


def test_a_second_eval_is_refused_while_one_runs(tmp_path, capsys):
    holder = _hold_in_child(tmp_path)
    try:
        with pytest.raises(SystemExit) as exc:
            with eval_lock.exclusive_eval("gaia eval agent"):
                pass
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "Another eval is already running" in err
        assert "gaia eval tasks run" in err
    finally:
        _kill_tree(holder)


def test_a_crashed_holder_leaves_no_stale_lock(tmp_path):
    holder = _hold_in_child(tmp_path)
    _kill_tree(holder)
    with eval_lock.exclusive_eval("gaia eval agent"):
        pass


def test_the_lock_is_released_after_the_run():
    with eval_lock.exclusive_eval("gaia eval agent"):
        pass
    with eval_lock.exclusive_eval("gaia eval agent"):
        pass
    assert not eval_lock.HOLDER_FILE.exists()


def test_the_bypass_skips_the_lock(tmp_path, monkeypatch):
    holder = _hold_in_child(tmp_path)
    try:
        monkeypatch.setenv(eval_lock.BYPASS_ENV, "1")
        with eval_lock.exclusive_eval("gaia eval agent"):
            pass
    finally:
        _kill_tree(holder)
