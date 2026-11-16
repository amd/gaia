# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The stage reports behind the Agent UI's recording progress card."""

from unittest.mock import MagicMock, patch

from gaia.agents.tools import audio_tools
from gaia.agents.tools.audio_tools import (
    RECORDING_STAGES,
    STAGE_DONE,
    AudioToolsMixin,
    _StageEstimate,
)
from gaia.ui.sse_translation import _PHASE_COUNT_FIELDS


class _Host(AudioToolsMixin):
    def __init__(self):
        self.console = MagicMock()


def test_a_stage_is_sent_by_position_so_the_ui_can_name_it_in_any_language():
    host = _Host()
    host._report_stage("transcribe", "Transcribing", percent=42, eta=330)
    host.console.report_phase.assert_called_once_with(
        "recording",
        "Transcribing",
        stage=RECORDING_STAGES.index("transcribe"),
        state=0,
        percent=42,
        eta_seconds=330,
    )


def test_a_finished_stage_carries_no_stale_percent():
    host = _Host()
    host._report_stage("decode", "Audio ready", state=STAGE_DONE)
    fields = host.console.report_phase.call_args.kwargs
    assert fields == {"stage": RECORDING_STAGES.index("decode"), "state": STAGE_DONE}


def test_every_field_of_a_stage_survives_the_sse_translation():
    for field in ("stage", "state", "percent", "eta_seconds"):
        assert field in _PHASE_COUNT_FIELDS


def test_a_console_that_cannot_draw_stages_is_left_alone():
    host = _Host()
    host.console = object()
    host._report_stage("decode", "Decoding")


def test_download_percent_is_read_from_the_downloader_line():
    host = _Host()
    host._report_download("Downloading the video — 37%")
    assert host.console.report_phase.call_args.kwargs["percent"] == 37


def test_a_download_line_without_a_percent_stays_indeterminate():
    host = _Host()
    host._report_download("Checking the link")
    assert "percent" not in host.console.report_phase.call_args.kwargs


def _estimate_at(clock, expected):
    with patch.object(audio_tools.time, "monotonic", return_value=clock):
        return _StageEstimate(lambda *_: None, expected)


def test_estimate_starts_from_the_expected_duration():
    estimate = _estimate_at(100.0, 100.0)
    with patch.object(audio_tools.time, "monotonic", return_value=125.0):
        assert estimate.reading() == (25, 75)


def test_estimate_never_reaches_a_full_bar_on_a_guess():
    estimate = _estimate_at(100.0, 10.0)
    with patch.object(audio_tools.time, "monotonic", return_value=500.0):
        percent, left = estimate.reading()
    assert percent == int(audio_tools.ESTIMATE_CEILING * 100)
    assert left == 0


def test_estimate_follows_the_real_pace_once_a_chunk_finishes():
    estimate = _estimate_at(100.0, 100.0)
    # The first half took 200 s, so the guess of 100 s total no longer applies.
    with patch.object(audio_tools.time, "monotonic", return_value=300.0):
        estimate.finished(0.5)
        assert estimate.reading() == (50, 200)
    with patch.object(audio_tools.time, "monotonic", return_value=340.0):
        assert estimate.reading() == (60, 160)


def test_estimate_ticker_stops_with_the_work():
    ticks = []
    with _StageEstimate(lambda *reading: ticks.append(reading), 10.0) as estimate:
        pass
    estimate._thread.join(timeout=audio_tools.PROGRESS_TICK_SECONDS + 1)
    assert not estimate._thread.is_alive()
    assert not ticks
