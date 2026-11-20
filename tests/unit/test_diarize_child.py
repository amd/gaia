# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Speaker separation runs in a child: sherpa-onnx holds the GIL for minutes."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.audio import diarize
from gaia.tool_cancellation import ToolCancelled, set_tool_cancel_event


class _Child:
    """A stand-in process that writes what the real child would, or never ends."""

    def __init__(
        self, argv, stderr, *, code=0, said="", finishes=True, writes=True, **kwargs
    ):
        self.argv = argv
        self.env = kwargs.get("env") or {}
        self.returncode = None
        self.killed = False
        self._code, self._finishes = code, finishes
        if finishes:
            if code == 0 and writes:
                spans = [{"start": 0.0, "end": 2.5, "speaker": "Speaker 1"}]
                with open(argv[4], "w", encoding="utf-8") as out:
                    json.dump(spans, out)
            stderr.write(said)

    def wait(self, timeout=None):
        if self._finishes or self.killed:
            self.returncode = self._code if not self.killed else -9
            return self.returncode
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def kill(self):
        self.killed = True


def _launch(**behaviour):
    made = []

    def popen(argv, **kwargs):
        made.append(_Child(argv, kwargs["stderr"], env=kwargs.get("env"), **behaviour))
        return made[-1]

    return popen, made


def test_spans_come_back_from_the_child(tmp_path):
    popen, made = _launch()
    with patch.object(diarize.subprocess, "Popen", side_effect=popen):
        spans = diarize.diarize_in_child(tmp_path / "a.wav", num_speakers=2)

    assert spans == [diarize.SpeakerSpan(0.0, 2.5, "Speaker 1")]
    assert made[0].argv[:3] == [sys.executable, "-m", "gaia.audio.diarize"]
    assert made[0].argv[5] == "2"


def test_the_childs_own_error_is_what_the_caller_sees(tmp_path):
    popen, _ = _launch(code=3, said="noise\nNo such audio file: a.wav\n")
    with patch.object(diarize.subprocess, "Popen", side_effect=popen):
        with pytest.raises(diarize.DiarizationError, match="No such audio file"):
            diarize.diarize_in_child(tmp_path / "a.wav")


def test_a_silent_crash_still_says_something(tmp_path):
    popen, _ = _launch(code=-1073741819)
    with patch.object(diarize.subprocess, "Popen", side_effect=popen):
        with pytest.raises(diarize.DiarizationError, match="exited with code"):
            diarize.diarize_in_child(tmp_path / "a.wav")


def test_stop_kills_the_child(tmp_path):
    popen, made = _launch(finishes=False)
    cancelled = threading.Event()
    cancelled.set()
    set_tool_cancel_event(cancelled)
    try:
        with patch.object(diarize.subprocess, "Popen", side_effect=popen):
            with pytest.raises(ToolCancelled):
                diarize.diarize_in_child(tmp_path / "a.wav")
    finally:
        set_tool_cancel_event(None)

    assert made[0].killed


def test_the_child_entry_point_reports_errors_on_stderr(tmp_path, capsys):
    missing = diarize.DiarizationError("No such audio file: missing.wav")
    with patch.object(diarize, "diarize", side_effect=missing):
        code = diarize._child_main(
            [str(tmp_path / "missing.wav"), str(tmp_path / "o.json"), "0", "0.65"]
        )

    assert code == 3
    assert "No such audio file" in capsys.readouterr().err


def test_a_child_that_imports_another_gaia_fails_loudly(tmp_path):
    """It exited 0 with no result when it picked up a different install."""
    popen, _ = _launch(writes=False)
    with patch.object(diarize.subprocess, "Popen", side_effect=popen):
        with pytest.raises(diarize.DiarizationError, match="no result"):
            diarize.diarize_in_child(tmp_path / "a.wav")


def test_the_child_imports_this_copy_of_gaia(tmp_path):
    popen, made = _launch()
    with patch.object(diarize.subprocess, "Popen", side_effect=popen):
        diarize.diarize_in_child(tmp_path / "a.wav")

    here = str(Path(diarize.__file__).resolve().parents[2])
    assert made[0].env["PYTHONPATH"].split(os.pathsep)[0] == here
