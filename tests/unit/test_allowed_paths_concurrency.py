# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Folder grants saved by several GAIA processes at once must all survive.

``allowed_paths.json`` is shared by every GAIA process under one home. A
read-modify-write without the cross-process lock loses a concurrent grant, and a
reader that meets a half-written file used to "rebuild" it from empty, wiping
every grant saved before it. Real processes, not threads: the race is between
processes.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WORKER = """
import sys
from pathlib import Path
from gaia.security import PathValidator

worker, count, root = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
validator = PathValidator()
for i in range(count):
    validator._save_persisted_path(root / f"w{worker}" / f"p{i}")
"""


def test_concurrent_grants_from_several_processes_all_persist(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "granted"
    workers, per_worker = 4, 25
    env = {
        **os.environ,
        "GAIA_HOME": str(home),
        "PYTHONPATH": str(_REPO / "src"),
    }
    env.pop("GAIA_ALLOWED_PATHS", None)
    # Logs go to files: a worker blocked on a full pipe would hold the lock.
    logs = [tmp_path / f"worker{w}.log" for w in range(workers)]
    procs = []
    for w, log in enumerate(logs):
        with open(log, "wb") as out:
            procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", _WORKER, str(w), str(per_worker), str(root)],
                    env=env,
                    cwd=str(tmp_path),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                )
            )
    try:
        for proc, log in zip(procs, logs):
            proc.wait(timeout=120)
            output = log.read_text("utf-8", errors="replace")
            assert proc.returncode == 0, output
            assert "Failed to save allowed path" not in output, output
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)

    saved = json.loads((home / "cache" / "allowed_paths.json").read_text("utf-8"))
    expected = {
        str(root / f"w{w}" / f"p{i}") for w in range(workers) for i in range(per_worker)
    }
    missing = expected - set(saved["paths"])
    assert not missing, f"{len(missing)} of {len(expected)} grants were lost"
