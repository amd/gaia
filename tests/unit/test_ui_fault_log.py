# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A hard crash of the Agent UI backend leaves its stack in the fault log."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gaia import cli

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


def _fake_homes(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    return fake_home


def test_an_unsafe_gaia_home_is_refused_before_the_fault_log_is_opened(
    tmp_path, monkeypatch
):
    fake_home = _fake_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("GAIA_HOME", str(fake_home))
    monkeypatch.setattr(cli, "_ensure_webui_built", lambda log=None: None)
    monkeypatch.setattr(
        "gaia.ui.fault_log.enable_fault_log",
        lambda *a, **k: pytest.fail("opened the fault log under an unsafe GAIA_HOME"),
    )
    monkeypatch.setattr(
        "uvicorn.run", lambda *a, **k: pytest.fail("started under an unsafe GAIA_HOME")
    )

    with pytest.raises(SystemExit) as exc_info:
        cli._launch_agent_ui(port=4200, base_url="http://127.0.0.1:59999")

    assert exc_info.value.code == 64
    assert not (fake_home / "logs").exists()


def test_a_safe_gaia_home_opens_the_fault_log_before_serving(tmp_path, monkeypatch):
    _fake_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(cli, "_ensure_webui_built", lambda log=None: None)
    calls = []
    monkeypatch.setattr(
        "gaia.ui.fault_log.enable_fault_log",
        lambda *a, **k: calls.append("fault_log") or tmp_path / "faults.log",
    )
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append("serve"))

    cli._launch_agent_ui(port=4200, base_url="http://127.0.0.1:59999")

    assert calls == ["fault_log", "serve"]
