# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/machine_turn.py: queue on the machine's eval lock instead of failing."""

import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gaia.eval import eval_lock

_PATH = Path(__file__).resolve().parents[2] / "util" / "machine_turn.py"


@pytest.fixture
def turn(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("machine_turn", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = tmp_path / "gaia-eval.lock"
    holder = tmp_path / "gaia-eval.holder.json"
    for target in (module, eval_lock):
        monkeypatch.setattr(target, "LOCK_FILE", lock)
        monkeypatch.setattr(target, "HOLDER_FILE", holder)
    return module


def test_runs_the_command_and_returns_its_exit_code(turn):
    code = turn.main(["--", sys.executable, "-c", "import sys; sys.exit(7)"])
    assert code == 7
    assert not turn.HOLDER_FILE.exists()


def test_the_command_can_run_gaia_eval_inside_the_held_lock(turn):
    """The child is told the lock is already held, or `gaia eval` would refuse."""
    code = turn.main(
        [
            "--",
            sys.executable,
            "-c",
            "import os, sys; sys.exit(0 if os.environ.get('GAIA_EVAL_NO_LOCK') == '1' else 1)",
        ]
    )
    assert code == 0


def test_waits_for_a_running_eval_then_takes_its_turn(turn, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(eval_lock, "stay_awake", lambda required=True: _null())

    def other_eval():
        with eval_lock.exclusive_eval("other session's eval"):
            started.set()
            release.wait(10)

    holder = threading.Thread(target=other_eval)
    holder.start()
    assert started.wait(5)
    threading.Timer(0.6, release.set).start()
    t0 = time.monotonic()
    code = turn.main(["--poll", "0.1", "--", sys.executable, "-c", "pass"])
    waited = time.monotonic() - t0
    holder.join(5)
    assert code == 0
    assert waited >= 0.5


def test_gives_up_after_the_timeout_naming_the_holder(turn, capsys):
    import os

    from gaia.daemon.lock import try_lock, unlock

    fd = os.open(str(turn.LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    assert try_lock(fd)
    turn.HOLDER_FILE.write_text(
        '{"pid": 4242, "command": "gaia eval agent", "started": 0}',
        encoding="utf-8",
    )
    try:
        code = turn.main(
            ["--timeout", "0.2", "--poll", "0.05", "--", sys.executable, "-c", "pass"]
        )
    finally:
        unlock(fd)
        os.close(fd)
    assert code == 2
    assert "PID 4242" in capsys.readouterr().err


def test_refuses_an_empty_command(turn):
    with pytest.raises(SystemExit):
        turn.main([])


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_runs_as_a_script():
    result = subprocess.run(
        [sys.executable, str(_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Wait for this machine's model slot" in result.stdout
