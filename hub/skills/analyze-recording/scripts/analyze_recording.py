#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run the whole analyze-recording pipeline from one command, without the agent.

For batch and scheduled jobs: transcribes a recording, writes a timestamped
transcript, extracts everything matching a request, and writes subtitles. All of
it goes next to the recording unless --output-dir says otherwise.

Finished work is reused, so a re-run after a failure, or with a new request,
only does what is left. Pass --force to start the transcript again.

    python analyze_recording.py cooking-class.mp4 \\
        --request "every recipe taught in the class" \\
        --field "ingredients" --field "method" --field "cooking time" \\
        --speakers 1 --subtitles srt
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from gaia.agents.tools.audio_tools import (
    DOWNLOAD_DIR,
    EXPORT_FORMATS,
    AudioToolsMixin,
    timings_path_for,
)
from gaia.audio.media_url import MediaUrlError, fetch_media, is_url
from gaia.llm.lemonade_client import resolve_default_chat_model

DEFAULT_REQUEST = "each distinct topic or section of the recording, in order"


class RecordingPipeline(AudioToolsMixin):
    """The agent's audio tools, without an agent around them."""

    def __init__(self, model: str):
        self.model_id = model
        self.started = time.monotonic()

    def _report_progress(self, message: str) -> None:
        print(f"[{self._elapsed()}] {message}", flush=True)

    def _elapsed(self) -> str:
        minutes, seconds = divmod(int(time.monotonic() - self.started), 60)
        return f"{minutes:02d}:{seconds:02d}"


def _require_success(result: dict, stage: str) -> dict:
    if result.get("status") != "success":
        raise SystemExit(f"{stage} failed: {result.get('error', result)}")
    return result


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcribe a recording and extract a timestamped report."
    )
    parser.add_argument("recording", help="Audio or video file, or a link to one")
    parser.add_argument(
        "--request",
        default=DEFAULT_REQUEST,
        help="What to find in the recording (default: its topics, in order)",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        dest="fields",
        help="What to report for each item. Repeat for several.",
    )
    parser.add_argument(
        "--model", help="Chat model (default: GAIA's configured default model)"
    )
    parser.add_argument("--whisper-model", help="Lemonade transcription model")
    parser.add_argument("--language", help='Language code such as "en" (default: detect)')
    parser.add_argument(
        "--speakers",
        type=int,
        default=0,
        help="How many people speak. 1 skips voice separation. 0 detects it.",
    )
    parser.add_argument(
        "--subtitles",
        action="append",
        choices=EXPORT_FORMATS,
        default=[],
        help="Also write this format. Repeat for several.",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="Where files go (default: next to the recording)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Transcribe again even if a transcript exists"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    pipeline = RecordingPipeline(args.model or resolve_default_chat_model())
    print(f"Chat model: {pipeline.model_id}", flush=True)

    if is_url(args.recording):
        try:
            recording = fetch_media(
                args.recording, DOWNLOAD_DIR, pipeline._report_progress
            ).path
        except MediaUrlError as e:
            raise SystemExit(str(e)) from e
        output_dir = (args.output_dir or Path.cwd()).expanduser().resolve()
    else:
        recording = Path(args.recording).expanduser().resolve()
        output_dir = (args.output_dir or recording.parent).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript = output_dir / f"{recording.stem}.txt"
    if args.force:
        transcript.unlink(missing_ok=True)
        timings_path_for(transcript).unlink(missing_ok=True)

    transcribed = _require_success(
        pipeline._transcribe_media(
            str(recording),
            language=args.language,
            model=args.whisper_model,
            output_path=str(transcript),
            speakers=args.speakers,
        ),
        "Transcription",
    )
    refined = _require_success(
        pipeline._refine_transcript(transcribed["transcript_path"]), "Refinement"
    )
    analysis = _require_success(
        pipeline._analyze_transcript(
            refined["refined_path"], args.request, args.fields
        ),
        "Analysis",
    )
    exports = [
        _require_success(
            pipeline._export_transcript(refined["refined_path"], chosen), "Export"
        )["export_path"]
        for chosen in args.subtitles
    ]

    print(
        json.dumps(
            {
                "transcript": transcribed["transcript_path"],
                "timestamped_transcript": refined["refined_path"],
                "report": analysis["analysis_path"],
                "subtitles": exports,
                "speakers": refined["speakers"],
                "items": analysis["item_count"],
                "approximate_timestamps": analysis["approximate_timestamps"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
