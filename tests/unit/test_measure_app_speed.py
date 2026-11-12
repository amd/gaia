# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The speed baseline is only worth comparing if each number means the same thing.

These pin how util/measure_app_speed.py turns raw progress events into stage,
step and chat times, and that its throwaway profile cannot see this PC's own
GAIA, Lemonade, Python or caches.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "util" / "measure_app_speed.py"


@pytest.fixture(scope="module")
def speed():
    spec = importlib.util.spec_from_file_location("measure_app_speed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_clean_env_hides_this_pcs_installs(speed, tmp_path):
    base = {
        "PATH": r"C:\Python312;C:\Users\me\.gaia\venv\Scripts",
        "GAIA_HOME": r"C:\Users\me\.gaia",
        "LEMONADE_BASE_URL": "http://localhost:8000",
        "HF_HOME": r"D:\hf",
        "UV_CACHE_DIR": r"D:\uv",
        "VIRTUAL_ENV": r"C:\venv",
        "PYTHONPATH": r"C:\src",
        "SystemRoot": r"C:\Windows",
    }
    env = speed.clean_env(base, tmp_path, [r"C:\Windows\System32", r"C:\node"])

    # uv's cache then defaults under the throwaway LOCALAPPDATA.
    for key in (
        "GAIA_HOME",
        "LEMONADE_BASE_URL",
        "HF_HOME",
        "UV_CACHE_DIR",
        "VIRTUAL_ENV",
        "PYTHONPATH",
    ):
        assert key not in env
    assert env["PATH"] == os.pathsep.join([r"C:\Windows\System32", r"C:\node"])
    home = tmp_path / "home"
    assert env["USERPROFILE"] == env["HOME"] == str(home)
    assert env["LOCALAPPDATA"] == str(home / "AppData" / "Local")
    assert env["APPDATA"] == str(home / "AppData" / "Roaming")
    assert env["TEMP"] == env["TMP"] == str(tmp_path / "temp")
    assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert env["SystemRoot"] == r"C:\Windows"


def test_stage_durations_split_the_backend_install(speed):
    events = [
        {"t": 1_000, "start": True},
        {"t": 1_000, "stage": "pre-checks", "percent": 0},
        {"t": 3_000, "stage": "ensure-uv", "percent": 1},
        {"t": 3_500, "stage": "create-venv", "percent": 2},
        {"t": 4_000, "stage": "create-venv", "percent": 3},
        {"t": 13_500, "stage": "install-package", "percent": 4},
        {"t": 60_000, "stage": "verify", "percent": 99},
        {"t": 61_000, "done": True, "bin": "gaia.exe"},
    ]
    out = speed.stage_durations(events)
    assert out["total"] == 60.0
    assert out["stages"] == {
        "pre-checks": 2.0,
        "ensure-uv": 0.5,
        "create-venv": 10.0,
        "install-package": 46.5,
        "verify": 1.0,
    }


def test_stage_durations_refuse_an_install_that_never_finished(speed):
    with pytest.raises(speed.MeasureError, match="no start or no finish"):
        speed.stage_durations([{"t": 0, "start": True}, {"t": 5, "stage": "ensure-uv"}])


def test_init_breakdown_times_each_step_and_model(speed):
    events = [
        (0.5, {"event": "step", "step": 1, "message": "Starting Lemonade Server..."}),
        (20.0, {"event": "step", "step": 2, "message": "Downloading models..."}),
        (
            21.0,
            {
                "event": "plan",
                "models": [
                    {"id": "chat", "bytes_total": 5_000},
                    {"id": "embed", "bytes_total": 300},
                ],
            },
        ),
        (21.5, {"event": "progress", "model": "chat"}),
        (101.0, {"event": "model_done", "model": "chat", "downloaded": True}),
        (111.0, {"event": "model_done", "model": "embed", "downloaded": True}),
        (111.2, {"event": "done", "ok": True}),
        (
            112.0,
            {
                "event": "step",
                "step": 3,
                "message": "Checking gaia agent installation...",
            },
        ),
    ]
    out = speed.init_breakdown(events, exit_at=140.0)
    assert out["total"] == 140.0
    assert [s["seconds"] for s in out["steps"]] == [19.5, 92.0, 28.0]
    assert out["models"] == [
        {"model": "chat", "bytes": 5_000, "downloaded": True, "seconds": 80.0},
        {"model": "embed", "bytes": 300, "downloaded": True, "seconds": 10.0},
    ]


def test_init_breakdown_refuses_output_without_steps(speed):
    with pytest.raises(speed.MeasureError, match="no step events"):
        speed.init_breakdown([(1.0, {"event": "done", "ok": True})], exit_at=2.0)


def test_app_version_comes_from_the_tag(speed):
    # A candidate's source says 0.25.4; the app it shipped is 0.25.4-rc.3.
    assert speed.app_version_of("v0.25.4-rc3") == "0.25.4-rc.3"
    assert speed.app_version_of("v0.25.3") == "0.25.3"
    with pytest.raises(speed.MeasureError, match="not a release tag"):
        speed.app_version_of("main")


def test_a_deep_work_folder_is_refused(speed):
    # A long home pushed the venv's DLLs past MAX_PATH and the backend died on
    # start, which no user with C:\Users\<name> would see.
    speed.check_work_path(Path(r"C:\Users\me\AppData\Local\Temp\gs-abc"))
    with pytest.raises(speed.MeasureError, match="too long"):
        speed.check_work_path(Path("C:\\" + "x" * 80))


def test_parse_sse_reads_only_data_lines(speed):
    lines = [
        'data: {"type": "status", "message": "Connecting"}\n',
        "\n",
        ": keep-alive\n",
        'data: {"type": "chunk", "content": "ready"}\n',
        "data: [DONE]\n",
    ]
    assert [e["type"] for e in speed.parse_sse(lines)] == ["status", "chunk"]


def test_busy_processes_skip_the_measured_profile(speed, monkeypatch, tmp_path):
    class Proc:
        def __init__(self, pid, name, cmdline):
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

    profile = tmp_path / "run-1"
    procs = [
        Proc(1, "llama-server.exe", [str(profile / "llama-server.exe"), "-m", "x"]),
        Proc(
            2, "llama-server.exe", [r"C:\Users\me\.cache\llama-server.exe", "-m", "y"]
        ),
        Proc(3, "LemonadeServer.exe", ["LemonadeServer.exe", "--silent"]),
        Proc(4, "python.exe", ["python", "-m", "pytest", "tests/unit"]),
        Proc(5, "bash.exe", ["bash", "-c", "timeout 900 python -m pytest tests"]),
        Proc(6, "gaia.exe", ["gaia", "eval", "agent", "--category", "rag"]),
        Proc(7, "python.exe", ["python", r"C:\venv\Scripts\gaia.exe", "chat", "--ui"]),
        Proc(8, "python.exe", ["python", "-m", "gaia.ui.server", "--port", "4978"]),
        Proc(9, "python.exe", ["python", r"C:\venv\Scripts\gaia.exe", "eval", "agent"]),
        Proc(10, "python.exe", [r"C:\x\pytest-of-me\helper.py"]),
    ]
    monkeypatch.setattr(speed.psutil, "process_iter", lambda attrs: procs)
    busy = speed.busy_processes(profile)
    # A shell that mentions pytest, the user's own Agent UI and a script under a
    # pytest temp folder are not running a test or a model.
    assert [b.split()[1] for b in busy] == ["2", "4", "6", "8", "9"]


def test_a_stalled_child_is_killed_with_its_children(speed):
    """A grandchild holding stdout must not keep the reader blocked."""
    grandchild = "import time; time.sleep(120)"
    child = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
        "print('started', flush=True); time.sleep(120)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", child], stdout=subprocess.PIPE, text=True
    )
    t0 = time.monotonic()
    timer, expired = speed.kill_tree_after(proc, 2.0)
    lines = list(proc.stdout)
    proc.wait(timeout=30)
    timer.cancel()
    assert expired.is_set()
    assert lines == ["started\n"]
    assert time.monotonic() - t0 < 60


def test_a_child_that_finishes_in_time_is_not_killed(speed):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    timer, expired = speed.kill_tree_after(proc, 30.0)
    assert proc.wait(timeout=30) == 0
    timer.cancel()
    assert not expired.is_set()


@pytest.mark.parametrize(
    "argv",
    [
        ["python", r"C:\venv\Scripts\gaia.exe", "eval", "agent"],
        ["python", "/home/me/venv/bin/gaia", "eval", "agent"],
    ],
)
def test_a_gaia_eval_is_busy_on_either_path_style(speed, argv):
    assert speed.is_busy("python.exe", argv)


def test_gaia_chat_is_not_busy_on_either_path_style(speed):
    assert not speed.is_busy("python.exe", ["python", r"C:\v\gaia.exe", "chat"])
    assert not speed.is_busy("python", ["python", "/v/bin/gaia", "chat"])
