# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The analyze-recording skill's script runs the pipeline from one command."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "hub/skills/analyze-recording/scripts/analyze_recording.py"
)


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("analyze_recording", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "talk.mp4"
    path.write_bytes(b"stub")
    return path


def _ok(**fields):
    return {"status": "success", **fields}


def _stub_stages(pipeline_class, tmp_path):
    return [
        patch.object(
            pipeline_class,
            "_transcribe_media",
            return_value=_ok(transcript_path=str(tmp_path / "talk.txt")),
        ),
        patch.object(
            pipeline_class,
            "_refine_transcript",
            return_value=_ok(
                refined_path=str(tmp_path / "talk.transcript.md"),
                speakers=["Speaker 1"],
            ),
        ),
        patch.object(
            pipeline_class,
            "_analyze_transcript",
            return_value=_ok(
                analysis_path=str(tmp_path / "talk.analysis.md"),
                item_count=2,
                approximate_timestamps=0,
            ),
        ),
        patch.object(
            pipeline_class,
            "_export_transcript",
            return_value=_ok(export_path=str(tmp_path / "talk.srt")),
        ),
    ]


def test_arguments_reach_each_stage_and_the_paths_are_printed(
    script, recording, tmp_path, capsys
):
    stages = _stub_stages(script.RecordingPipeline, tmp_path)
    for stage in stages:
        stage.start()
    try:
        code = script.main(
            [
                str(recording),
                "--request",
                "every drill",
                "--field",
                "cues",
                "--field",
                "reps",
                "--model",
                "fireworks.deepseek-v4p1-flash",
                "--speakers",
                "1",
                "--subtitles",
                "srt",
            ]
        )
        transcribe = script.RecordingPipeline._transcribe_media
        analyze = script.RecordingPipeline._analyze_transcript
    finally:
        for stage in stages:
            stage.stop()

    assert code == 0
    assert transcribe.call_args.kwargs["speakers"] == 1
    assert transcribe.call_args.kwargs["output_path"] == str(
        recording.with_suffix(".txt")
    )
    assert analyze.call_args.args[1:] == ("every drill", ["cues", "reps"])
    printed = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert printed["items"] == 2
    assert printed["subtitles"] == [str(tmp_path / "talk.srt")]


def test_a_failed_stage_stops_with_its_message(script, recording):
    failure = {"status": "error", "error": "Lemonade is down"}
    with patch.object(
        script.RecordingPipeline, "_transcribe_media", return_value=failure
    ):
        with pytest.raises(SystemExit, match="Lemonade is down"):
            script.main([str(recording), "--model", "m"])


def test_the_default_request_works_for_any_recording(script, recording):
    args = script._parse_args([str(recording)])
    assert args.request == script.DEFAULT_REQUEST
    assert args.fields == []
