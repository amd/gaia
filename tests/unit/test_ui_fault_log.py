# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hard crash of the Agent UI backend leaves its stack in the fault log."""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _run(code: str, home: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GAIA_HOME": str(home),
        "PYTHONPATH": str(SRC),
        "LEMONADE_BASE_URL": "http://127.0.0.1:59999",
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_segfault_writes_every_thread_stack_to_the_fault_log(tmp_path):
    code = (
        "import faulthandler, threading, time\n"
        "from gaia.ui.fault_log import enable_fault_log\n"
        "enable_fault_log()\n"
        "def extraction_worker(): time.sleep(30)\n"
        "threading.Thread(target=extraction_worker, daemon=True).start()\n"
        "time.sleep(0.2)\n"
        "faulthandler._sigsegv()\n"
    )
    result = _run(code, tmp_path)
    assert result.returncode != 0
    text = (tmp_path / "logs" / "backend-faults.log").read_text(encoding="utf-8")
    assert "--- Agent UI backend pid=" in text
    assert "Fatal Python error" in text
    assert "extraction_worker" in text


def test_each_start_appends_a_header_and_an_oversized_log_rotates(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "backend-faults.log").write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    code = "from gaia.ui.fault_log import enable_fault_log\nprint(enable_fault_log())\n"
    first = _run(code, tmp_path)
    assert first.returncode == 0, first.stderr
    assert (logs / "backend-faults.log.1").stat().st_size > 1024 * 1024
    _run(code, tmp_path)
    text = (logs / "backend-faults.log").read_text(encoding="utf-8")
    assert text.count("--- Agent UI backend pid=") == 2
