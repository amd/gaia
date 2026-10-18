# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A timed-out PPTX→PDF conversion must never kill the user's own PowerPoint."""

import subprocess

import pytest

from gaia.rag import pptx_utils


class FakeRun:
    """Stands in for ``subprocess.run``: the conversion hangs, taskkill is recorded."""

    def __init__(self, our_pid=None):
        self.our_pid = our_pid
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "powershell":
            pid_file = kwargs["env"].get("GAIA_PPTX_CONVERT_PIDFILE")
            if pid_file and self.our_pid is not None:
                with open(pid_file, "w", encoding="utf-8") as f:
                    f.write(f"{self.our_pid}\n")
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    def kills(self):
        return [c for c in self.calls if c[0] == "taskkill"]


@pytest.fixture
def deck(tmp_path, monkeypatch):
    monkeypatch.setattr(pptx_utils.platform, "system", lambda: "Windows")
    path = tmp_path / "deck.pptx"
    path.write_bytes(b"")
    return path


def test_timeout_kills_only_the_powerpoint_gaia_started(deck, tmp_path, monkeypatch):
    run = FakeRun(our_pid=4242)
    monkeypatch.setattr(pptx_utils.subprocess, "run", run)

    assert pptx_utils.convert_pptx_to_pdf(str(deck), str(tmp_path)) is None

    kills = run.kills()
    assert len(kills) == 1
    assert "/im" not in [a.lower() for a in kills[0]]
    assert kills[0][kills[0].index("/pid") + 1] == "4242"
    # PID reuse guard: the kill only lands if that PID is still PowerPoint.
    assert "IMAGENAME eq POWERPNT.EXE" in kills[0]


def test_timeout_leaves_an_already_running_powerpoint_alone(
    deck, tmp_path, monkeypatch
):
    # The script writes no PID when PowerPoint was already open: it is the user's.
    run = FakeRun(our_pid=None)
    monkeypatch.setattr(pptx_utils.subprocess, "run", run)

    assert pptx_utils.convert_pptx_to_pdf(str(deck), str(tmp_path)) is None

    assert run.kills() == []


def test_pid_file_is_removed_after_the_conversion(deck, tmp_path, monkeypatch):
    run = FakeRun(our_pid=4242)
    seen = {}
    real_call = run.__call__

    def spy(argv, **kwargs):
        if argv[0] == "powershell":
            seen["pid_file"] = kwargs["env"][pptx_utils.PID_FILE_ENV_VAR]
        return real_call(argv, **kwargs)

    monkeypatch.setattr(pptx_utils.subprocess, "run", spy)

    pptx_utils.convert_pptx_to_pdf(str(deck), str(tmp_path))

    assert not (tmp_path / seen["pid_file"]).exists()


def test_script_quits_powerpoint_only_when_it_started_it():
    script = pptx_utils.PPTX_TO_PDF_PS_SCRIPT
    assert "if ($owned) { $ppt.Quit() }" in script
    assert f"$env:{pptx_utils.PID_FILE_ENV_VAR}" in script
