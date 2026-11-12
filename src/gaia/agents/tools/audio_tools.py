# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AudioToolsMixin — transcribe audio and video files for GAIA agents."""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from gaia.agents.base.file_snapshots import SnapshotError, before_change
from gaia.agents.tools.path_access import read_access_error, write_access_error
from gaia.agents.tools.workspace_paths import _workspace_target
from gaia.home import gaia_home
from gaia.logger import get_logger

logger = get_logger(__name__)

# Below this, a word is worth showing to the correction pass.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# Transcripts land here unless the caller names a path.
TRANSCRIPT_DIR = gaia_home() / "transcripts"

# Finished chunks of a long recording, kept until its transcript is written.
CHUNK_CHECKPOINT_DIR = TRANSCRIPT_DIR / ".partial"

# Audio fetched from links, kept so asking about the same link again is instant.
DOWNLOAD_DIR = TRANSCRIPT_DIR / ".downloads"

# The transcript is what lasts; these caches go after this long.
MEDIA_CACHE_DAYS = 14

# Transcription runs at roughly 9x realtime, so a 45-minute recording needs
# ~5 minutes — far past the agent's 180s default, which abandons the call
# mid-flight and loses a transcript that did in fact complete.
TRANSCRIBE_TOOL_TIMEOUT = 7200

# Measured on a warm GPU slot; used only to tell the user how long to expect.
REALTIME_FACTOR = 9.0

# Enough transcript for the model to recognise what the recording is, and far
# short of the truncation budget. The rest is read from the file in sections.
PREVIEW_CHARS = 1200

# Correction targets worth naming inline; the count carries the rest.
MAX_REPORTED_SPANS = 25

# Minimum change before another progress line is worth emitting.
PROGRESS_STEP = 0.05

# Stages of a recording, in order. The Agent UI names them by position.
RECORDING_STAGES = ("download", "decode", "transcribe", "voices", "prepare", "analyze")
STAGE_RUNNING, STAGE_DONE, STAGE_SKIPPED = 0, 1, 2

# How often a stage that reports nothing itself gets an estimate.
PROGRESS_TICK_SECONDS = 3.0

# An estimate stops here: a bar at 100% with work left reads as a hang.
ESTIMATE_CEILING = 0.97

# Measured: 283 s of voice separation on 84 minutes of audio.
VOICE_SECONDS_PER_MINUTE = 3.5

# Speech runs near 900 characters a minute; silence and music come back far below.
MIN_SPEECH_CHARS_PER_MINUTE = 60

# A clip shorter than this is judged as this long: one word is fine in a second.
MIN_SPEECH_CHECK_SECONDS = 15.0

# Silence long enough to usually mean someone else started talking.
TURN_GAP_SECONDS = 0.8

# Turns per naming call. Small replies, so this is bounded by how much
# transcript the model can hold in view, not by its reply budget.
REFINE_TURNS_PER_CALL = 40

# Below this a batch is small enough that an overflow is not the batch's
# fault, so failing is more honest than splitting forever.
MIN_TURNS_PER_CALL = 4

# A real meeting has a handful of voices. Without this the model assigned
# a new speaker per sentence — sixty of them in a four-person meeting.
MAX_SPEAKERS = 8


# Refinement is one LLM pass per section; a 46-minute meeting is ~9 sections.
REFINE_TOOL_TIMEOUT = 3600

# Room for the section back out, plus a reasoning model's private thinking.
REFINE_MAX_TOKENS = 4096

# A long recording is many sections, each one a model call on a local GPU.
ANALYZE_TOOL_TIMEOUT = 7200

# One section can hold dozens of multi-field items.
ANALYZE_MAX_TOKENS = 8192

# Cloud models take sections at once: 15 seconds instead of 70 on 84 minutes.
ANALYZE_CLOUD_PARALLEL = 4

# One presenter talks for minutes on end; a paragraph every minute keeps the
# transcript readable and gives each stretch its own timestamp.
PARAGRAPH_SECONDS = 60.0

EXPORT_FORMATS = ("srt", "vtt", "txt")

_TURN_LABEL = re.compile(r"^\s*(\d+)\s*[:.]\s*(.+?)\s*$", re.MULTILINE)


_ALIAS_LINE = re.compile(r"^\s*(.+?)\s*->\s*(.+?)\s*$", re.MULTILINE)


def _parse_alias_map(reply, known):
    """Read `Old -> Final` lines into a rename map, ignoring unknown labels."""
    mapping = {}
    for old, new in _ALIAS_LINE.findall(reply):
        old, new = old.strip().strip("`"), new.strip().strip("`")
        if old in known and new and old != new:
            mapping[old] = new
    # A chain (A->B, B->C) must resolve to its endpoint.
    for key in list(mapping):
        seen = {key}
        value = mapping[key]
        while value in mapping and value not in seen:
            seen.add(value)
            value = mapping[value]
        mapping[key] = value
    return mapping


def _speaker_at(spans, start, end):
    """Which diarized voice best covers a transcript segment."""
    best, best_overlap = None, 0.0
    for span in spans:
        overlap = min(end, span["end"]) - max(start, span["start"])
        if overlap > best_overlap:
            best, best_overlap = span["speaker"], overlap
    return best


def _turns_from_speakers(segments, spans):
    """Group transcript segments into turns by diarized voice.

    Returns the turns and their acoustic labels. A segment no voice covers
    inherits the previous speaker rather than starting a phantom turn — a
    silent gap in the diarization is missing evidence, not a new person.
    """
    turns, labels = [], []
    for seg in segments:
        who = _speaker_at(spans, seg["start"], seg["end"]) or (
            labels[-1] if labels else "Speaker 1"
        )
        if labels and who == labels[-1]:
            turns[-1].append(seg)
        else:
            turns.append([seg])
            labels.append(who)
    return turns, labels


def _batch_turns(turns: List[List[dict]], per_call: int) -> List[List[List[dict]]]:
    """Chunk turns into naming calls."""
    return [turns[i : i + per_call] for i in range(0, len(turns), per_call)] or [[]]


def _parse_turn_labels(reply: str, count: int) -> List[str]:
    """Read `N: Name` lines into one label per turn, in order.

    A turn the model skipped inherits the previous speaker — silence is far
    more likely to mean "same person continuing" than a new voice.
    """
    found: Dict[int, str] = {}
    for raw_index, name in _TURN_LABEL.findall(reply):
        index = int(raw_index)
        label = name.strip().rstrip(":").strip()
        if 1 <= index <= count and label:
            found.setdefault(index, label)
    labels: List[str] = []
    for i in range(1, count + 1):
        labels.append(found.get(i) or (labels[-1] if labels else "Speaker A"))
    return labels


def _merge_adjacent(named: List[tuple]) -> List[tuple]:
    """Join consecutive turns by the same speaker into one block."""
    merged: List[tuple] = []
    for name, text in named:
        if merged and merged[-1][0] == name:
            merged[-1] = (name, f"{merged[-1][1]} {text}".strip())
        else:
            merged.append((name, text))
    return merged


def _timed_paragraphs(turns: List[List[dict]], named: List[tuple]) -> List[list]:
    """Group attributed segments into ``[start, speaker, text]`` paragraphs.

    A new paragraph opens on a change of speaker and every PARAGRAPH_SECONDS
    within one, so a single-presenter recording does not collapse into one
    untimed block that nobody can navigate and RAG cannot cite.
    """
    paragraphs: List[list] = []
    for turn, (name, _) in zip(turns, named):
        for seg in turn:
            text = seg["text"].strip()
            continues = (
                bool(paragraphs)
                and paragraphs[-1][1] == name
                and seg["start"] - paragraphs[-1][0] < PARAGRAPH_SECONDS
            )
            if continues:
                paragraphs[-1][2] = f"{paragraphs[-1][2]} {text}"
            else:
                paragraphs.append([seg["start"], name, text])
    return paragraphs


def timings_path_for(transcript_path: Path) -> Path:
    """Where the segment timings for a transcript live."""
    return transcript_path.with_suffix(transcript_path.suffix + ".timing.json")


def _read_timings(transcript_path: Path) -> dict:
    """The timings saved beside a transcript, or an empty dict if there are none."""
    timings_file = timings_path_for(transcript_path)
    if not timings_file.is_file():
        return {}
    try:
        return json.loads(timings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read timings %s: %s", timings_file, e)
        return {}


def _source_record(source: Path) -> dict:
    """What identifies a recording across moves, so reuse can tell two apart."""
    return {"name": source.name, "size": source.stat().st_size}


def _belongs_to_other_recording(transcript_path: Path, source: Path) -> bool:
    """Whether the transcript at this path came from a different recording.

    ``talk.mp4`` in two folders, or ``talk.mp4`` and ``talk.m4a``, map to one
    transcript name. Without this the second silently gets the first's words.
    """
    recorded = _read_timings(transcript_path).get("source")
    return bool(recorded) and recorded != _source_record(source)


def _write_timings(destination: Path, transcript, speaker_spans=None, source=None):
    """Persist segment start/end/text next to the transcript.

    Speaker turns cannot be recovered from continuous prose — asked to segment
    it blind, the model invented sixty speakers in a four-person meeting. A
    pause between segments is the one piece of real evidence available without
    acoustic diarization, so it has to survive past the transcribe call.
    """
    payload = {
        **({"source": _source_record(source)} if source else {}),
        "duration": transcript.duration,
        "speakers": [
            {
                "start": round(sp.start, 3),
                "end": round(sp.end, 3),
                "speaker": sp.speaker,
            }
            for sp in (speaker_spans or [])
        ],
        "segments": [
            {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
            }
            for seg in transcript.segments
            if seg.text.strip()
        ],
    }
    try:
        before_change(timings_path_for(destination), "transcribe")
        timings_path_for(destination).write_text(json.dumps(payload), encoding="utf-8")
    except (OSError, SnapshotError) as e:
        # The transcript is the artifact; losing timings costs turn quality,
        # not the run.
        logger.warning("Could not write timings for %s: %s", destination, e)


def _turns_from_gaps(segments: List[dict], gap_seconds: float) -> List[List[dict]]:
    """Group segments into candidate speaker turns on silence.

    A speaker change almost always follows a pause; a pause does not always
    mean a speaker change. Over-splitting here is safe because the model then
    merges adjacent turns by giving them the same name.
    """
    turns: List[List[dict]] = []
    for seg in segments:
        if turns and seg["start"] - turns[-1][-1]["end"] < gap_seconds:
            turns[-1].append(seg)
        else:
            turns.append([seg])
    return turns


def _render_refined(
    source: Path, speakers: List[str], blocks: List[str], acoustic: bool = False
) -> str:
    """Assemble the refined transcript, with its provenance on the page."""
    key = "\n".join(f"- {name}" for name in speakers) or "- (none identified)"
    how = (
        "Voices were separated from the audio itself. Any real names are "
        "inferred from what was said, so treat the names — not the voice "
        "separation — as best-effort."
        if acoustic
        else "Turns were split on pauses in the recording and attributed "
        "from the conversation, without voice identification, so treat the "
        "speaker labels as best-effort."
    )
    return (
        f"# Transcript — {source.stem}\n\n"
        f"Source: {source}\n\n"
        + how
        + "\n\n"
        + f"## Speakers\n\n{key}\n\n## Transcript\n\n"
        + "\n\n".join(blocks)
        + "\n"
    )


def _next_step_instructions(destination, char_count: int) -> str:
    """Tell the model the whole pipeline, not just where the file is.

    Skill activation is a retrieval decision that can go either way, so the
    staged pipeline is stated here too — a run without the skill loaded still
    corrects, attributes and summarises instead of stopping at raw text.
    """
    return (
        f"Raw transcript saved to {destination} ({char_count} chars). "
        "Tell the user this path. Then, in order:\n"
        f"1. Call refine_transcript('{destination}') — it labels the speakers "
        "and adds timestamps, and returns the path of a better transcript.\n"
        "2. Call summarize_document on the file refine_transcript returns, for "
        "the summary and action items. If the user wants every item of some "
        "kind, timestamps, or a structured list, call analyze_transcript on "
        "that file instead.\n"
        "Do NOT summarise this raw file, do NOT summarise from `preview` (it "
        "is the first 1200 characters only), and do NOT use query_documents "
        "for a summary — it returns only the top few matching chunks and would "
        "miss most of the meeting."
    )


@dataclass(frozen=True)
class _SoleSpeaker:
    """The one voice of a recording the user said has a single speaker."""

    end: float
    start: float = 0.0
    speaker: str = "Speaker 1"


def _chunk_checkpoint_dir(source: Path, model: str, language: Optional[str]) -> Path:
    """Where this recording's finished chunks wait for its transcript."""
    stat = source.stat()
    identity = f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{model}|{language}"
    return (
        CHUNK_CHECKPOINT_DIR / hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    )


class _StageEstimate:
    """Reports progress while a blocking call that cannot report its own runs.

    Starts from an expected duration, then follows the real pace once
    ``finished`` says how far the work has got.
    """

    def __init__(self, report: Callable[[int, int], None], expected_seconds: float):
        self._report = report
        self._expected = max(expected_seconds, 1.0)
        self._stop = threading.Event()
        self._began = time.monotonic()
        self._fraction = 0.0
        self._fraction_at = self._began
        self._thread = threading.Thread(
            target=self._run, name="recording-progress", daemon=True
        )

    def __enter__(self) -> "_StageEstimate":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()

    def finished(self, fraction: float) -> None:
        """The real share of the work that is done."""
        self._fraction = fraction
        self._fraction_at = time.monotonic()

    def reading(self) -> tuple[int, int]:
        """Percent done and seconds left, as of now."""
        now = time.monotonic()
        spent = max(self._fraction_at - self._began, 0.001)
        if self._fraction > 0:
            pace = self._fraction / spent
            share = self._fraction + (now - self._fraction_at) * pace
            left = (1.0 - share) / pace
        else:
            share = (now - self._began) / self._expected
            left = self._expected - (now - self._began)
        return int(min(share, ESTIMATE_CEILING) * 100), max(int(left), 0)

    def _run(self) -> None:
        while not self._stop.wait(PROGRESS_TICK_SECONDS):
            self._report(*self.reading())


def _speech_warning(text: str, seconds: float) -> Optional[str]:
    """What to say when a recording holds almost no speech.

    Whisper writes a stock phrase over silence or music, so a transcript of a
    few characters is not evidence that anything was said.
    """
    minutes = max(seconds, MIN_SPEECH_CHECK_SECONDS) / 60
    if len(text.strip()) / minutes >= MIN_SPEECH_CHARS_PER_MINUTE:
        return None
    return (
        f"This recording has almost no speech: {len(text.strip())} characters in "
        f"{_clock(seconds)}. What the transcript shows may not have been said at "
        "all. Tell the user that, and do not summarize it or analyze it."
    )


def _plain_stem(source: Path) -> str:
    """The recording's name as letters and digits joined by hyphens.

    Small models retype paths: a quote, bracket, full-width bar or doubled
    space in a video title came back changed, and every later step missed it.
    """
    kept = "".join(
        (
            char
            if unicodedata.category(char)[0] in "LN"
            else "" if unicodedata.category(char) == "Mn" else " "
        )
        for char in unicodedata.normalize("NFC", source.stem)
    )
    return "-".join(kept.split())[:120] or "recording"


def _missing_transcript(source: Path) -> Dict:
    """The error for a transcript path that isn't there, with the ones that are."""
    nearby = (
        sorted(TRANSCRIPT_DIR.glob("*.txt"), key=lambda p: -p.stat().st_mtime)[:5]
        if TRANSCRIPT_DIR.is_dir()
        else []
    )
    return {
        "status": "error",
        "error": (
            f"No transcript at {source}. Pass the transcript_path that "
            "transcribe_media returned, copied exactly, or run "
            "transcribe_media first."
            + (
                " Transcripts that do exist: " + "; ".join(str(path) for path in nearby)
                if nearby
                else ""
            )
        ),
    }


def _recording_name(transcript: Path) -> str:
    """The recording's name, whichever of its transcript files was given."""
    if transcript.name.endswith(".transcript.md"):
        return transcript.name.removesuffix(".transcript.md")
    return transcript.stem


def _clock(seconds: float) -> str:
    """Render a duration the way a person would say it."""
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class AudioToolsMixin:
    """
    Mixin providing media transcription tools.

    Tools provided:
    - transcribe_media: Transcribe an audio or video file via Lemonade
    - refine_transcript: Label speakers and write a timestamped transcript
    - analyze_transcript: Extract a structured, timestamped list from all of it
    - export_transcript: Write subtitles or a timestamped text file

    Speech-to-text runs on Lemonade Server, so no local torch or Whisper
    install is involved. ffmpeg is fetched on first use, never at startup.

    There is deliberately no status tool. A companion "is transcription
    available?" tool was removed after a trace showed the model calling it four
    times in a row and never reaching ``transcribe_media``: it reports readiness
    without changing any state, so re-calling it always looks reasonable. ``transcribe_media``
    already fails loudly and actionably when a component is missing, which is
    the same information at the moment it actually matters.
    ``transcription_status()`` remains as a plain method for diagnostics.
    """

    def register_audio_tools(self) -> None:
        """Register audio tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool(timeout=TRANSCRIBE_TOOL_TIMEOUT)
        def transcribe_media(
            file_path: str,
            language: str = "",
            model: str = "",
            output_path: str = "",
            speakers: int = 0,
        ) -> Dict:
            """Transcribe an audio or video file and SAVE it to a text file.

            Takes any format ffmpeg decodes (mp4, mkv, m4a, mp3, wav), of any
            length, or a link to a video such as a YouTube URL. Returns a path
            and a 1200-character preview, never the transcript: next call
            refine_transcript on that path. A 45-minute recording takes about
            10 minutes.

            Args:
                file_path: Path to the audio or video file, or an http(s) link
                           to a video or podcast.
                language: ISO 639-1 code such as "en". Leave empty unless the user
                          said what language the recording is in: it is not the
                          language they are writing to you in.
                model: Lemonade transcription model. Empty uses the default.
                output_path: Where to write the transcript. Empty writes to
                             ~/.gaia/transcripts/<name>.txt
                speakers: How many people speak, if the user said. 1 skips voice
                          separation for a lecture or solo video. 0 detects it.

            Returns:
                transcript_path, preview, duration, low-confidence spans.
            """
            return self._transcribe_media(
                file_path,
                language=language or None,
                model=model or None,
                output_path=output_path or None,
                speakers=speakers,
            )

        @tool(timeout=REFINE_TOOL_TIMEOUT)
        def refine_transcript(
            transcript_path: str,
            output_path: str = "",
        ) -> Dict:
            """Label the speakers and add timestamps to a raw transcript.

            Call on the file transcribe_media produced, BEFORE summarizing: a
            summary off the raw transcript has no speakers and repeats
            mis-heard names, so its action items come out wrong. Writes a new
            corrected file — summarize that one, not the raw one.

            Args:
                transcript_path: The raw transcript from transcribe_media.
                output_path: Where to write the refined transcript. Empty
                             writes alongside as <name>.transcript.md

            Returns:
                refined_path, speakers, sections, corrections_applied.
            """
            return self._refine_transcript(transcript_path, output_path or None)

        @tool(timeout=ANALYZE_TOOL_TIMEOUT)
        def analyze_transcript(
            transcript_path: str,
            request: str,
            fields: Optional[List[str]] = None,
            output_path: str = "",
        ) -> Dict:
            """Find EVERY matching item across a whole recording, with timestamps.

            Use when the user wants a complete list or a structured breakdown:
            every topic, recipe, question asked, product shown, chapter. It
            reads the entire transcript in sections and saves a
            Markdown report. summarize_document is for prose summaries, and
            query_documents returns only a few chunks, so neither can do this.

            Args:
                transcript_path: The file from refine_transcript or transcribe_media.
                request: What to find, e.g. "every recipe taught in the class".
                         Write it in the language spoken in the recording,
                         translating the user's words if they differ.
                fields: What to report per item, e.g. ["ingredients", "method",
                        "cooking time"], in the same language as request. Each
                        item always gets a title and times.
                output_path: Where to write the report. Empty writes alongside
                             as <name>.analysis.md

            Returns:
                analysis_path, overview, item_count, sections.
            """
            return self._analyze_transcript(
                transcript_path, request, fields or [], output_path or None
            )

        @tool
        def export_transcript(
            transcript_path: str,
            format: str = "srt",
            output_path: str = "",
        ) -> Dict:
            """Write a transcript as subtitles or as text with timestamps.

            Args:
                transcript_path: The file from refine_transcript or transcribe_media.
                format: "srt" or "vtt" for subtitles, "txt" for timestamped text.
                output_path: Where to write it. Empty writes alongside the transcript.

            Returns:
                export_path, format, cues.
            """
            return self._export_transcript(transcript_path, format, output_path or None)

    def _transcribe_media(
        self,
        file_path: str,
        language: Optional[str] = None,
        model: Optional[str] = None,
        output_path: Optional[str] = None,
        speakers: int = 0,
    ) -> Dict:
        """Decode the file to WAV, transcribe it, and persist the transcript."""
        from tempfile import TemporaryDirectory

        from gaia.agents.base.tools import ToolCancelled, raise_if_cancelled
        from gaia.audio.lemonade_asr import (
            DEFAULT_ASR_MODEL,
            LemonadeASRClient,
            LemonadeASRError,
        )
        from gaia.audio.media import (
            ensure_ffmpeg,
            probe_duration,
            prune_older_than,
            to_wav16k_mono,
        )
        from gaia.audio.media_url import MediaUrlError, fetch_media, is_url

        prune_older_than(CHUNK_CHECKPOINT_DIR, MEDIA_CACHE_DAYS)
        prune_older_than(DOWNLOAD_DIR, MEDIA_CACHE_DAYS)

        source_url = None
        if is_url(file_path):
            source_url = file_path.strip()
            try:
                source = fetch_media(
                    source_url, DOWNLOAD_DIR, self._report_download
                ).path
            except MediaUrlError as e:
                return {"status": "error", "error": str(e)}
            self._report_stage("download", "Downloaded", state=STAGE_DONE)
        else:
            source = _workspace_target(self, file_path)
        denied = self._transcript_access_error(source, write=False)
        if denied:
            return denied
        if speakers < 0:
            return {
                "status": "error",
                "error": (
                    f"speakers must be 0 to detect it, or the number of people "
                    f"who speak; got {speakers}."
                ),
            }
        if output_path:
            denied = self._transcript_access_error(
                self._transcript_destination(source, output_path), write=True
            )
            if denied:
                return denied
        if not source.is_file():
            return {
                "status": "error",
                "error": (
                    f"No such file: {source}. Give the full path to an audio or "
                    "video file."
                ),
            }
        if model and model != DEFAULT_ASR_MODEL:
            # A chat model's name here 404s only after minutes of decoding.
            try:
                speech_models = LemonadeASRClient().available_models()
            except LemonadeASRError as e:
                return {"status": "error", "error": str(e)}
            if model not in speech_models:
                return {
                    "status": "error",
                    "error": (
                        f"'{model}' is not a downloaded speech-to-text model. "
                        f"Leave model empty to use {DEFAULT_ASR_MODEL}"
                        + (
                            f", or use one of: {', '.join(speech_models)}"
                            if speech_models
                            else ""
                        )
                        + "."
                    ),
                }

        # A second call on the same recording must not redo five minutes of
        # decode, transcription and diarization. Asking a follow-up question
        # about a meeting reached this tool again and silently re-transcribed
        # the whole thing.
        cached = self._existing_transcript(source, output_path)
        if cached is not None:
            return cached

        # ffmpeg and the model pull are both slow and both happen here, never
        # at startup — the user only pays for them if they actually transcribe.
        try:
            ensure_ffmpeg()
        except Exception as e:
            return {"status": "error", "error": str(e)}

        scratch = None
        saved_path = None
        try:
            media_seconds = probe_duration(source)

            last_shown = [-1.0]

            def _decoding(fraction: float) -> None:
                # Decode is the one stage that reports often enough to abort
                # promptly if the agent has already stopped waiting.
                raise_if_cancelled()
                # ffmpeg emits one progress block per second of MEDIA, so a
                # 46-minute file would push ~2700 status events at the UI.
                # A percentage only changes usefully every few points.
                pct = min(fraction, 1.0)
                if pct - last_shown[0] < PROGRESS_STEP and pct < 1.0:
                    return
                last_shown[0] = pct
                self._report_stage(
                    "decode",
                    f"Decoding {source.name} — {pct:.0%} of {_clock(media_seconds)}",
                    percent=int(pct * 100),
                )

            scratch = TemporaryDirectory(prefix="gaia-media-")
            wav_path = to_wav16k_mono(
                source,
                dest=Path(scratch.name) / "audio.16k.wav",
                progress_callback=_decoding,
            )

            raise_if_cancelled()
            self._report_stage("decode", "Audio ready", state=STAGE_DONE)
            client = LemonadeASRClient(model=model or DEFAULT_ASR_MODEL)
            self._download_speech_model(client)
            expected = media_seconds / REALTIME_FACTOR
            self._report_stage(
                "transcribe",
                f"Transcribing {_clock(media_seconds)} of audio with {client.model} "
                f"— around {_clock(expected)} to go",
                percent=0,
                eta=int(expected),
            )

            def _transcribing(message: str) -> None:
                # A long recording runs for many chunks; stop between them.
                raise_if_cancelled()
                self._report_progress(message)

            chunks_dir = _chunk_checkpoint_dir(source, client.model, language)
            with _StageEstimate(
                self._stage_tick("transcribe", "Transcribing"), expected
            ) as estimate:
                transcript = client.transcribe(
                    wav_path,
                    language=language,
                    progress=_transcribing,
                    checkpoint_dir=chunks_dir,
                    on_chunk_done=estimate.finished,
                )
            self._report_stage("transcribe", "Transcribed", state=STAGE_DONE)

            destination = self._write_transcript(transcript, source, output_path)
            saved_path = destination
            speaker_spans = self._speaker_spans(wav_path, speakers, media_seconds)
            _write_timings(destination, transcript, speaker_spans, source)
            # Kept until here: Stop during voice separation must not lose them.
            shutil.rmtree(chunks_dir, ignore_errors=True)
            spans = transcript.low_confidence_spans(
                threshold=DEFAULT_CONFIDENCE_THRESHOLD
            )

            # The transcript goes back by PATH, never inline. A 46-minute
            # meeting serialises to ~135K chars against a 60K truncation
            # budget, so returning the text would silently drop most of the
            # meeting before the model ever summarised it. Downstream stages
            # read the file in sections instead.
            full_text = transcript.text
            return {
                "status": "success",
                "transcript_path": str(destination),
                **({"source_url": source_url} if source_url else {}),
                "audio_duration": _clock(media_seconds),
                "language": transcript.language,
                "model": transcript.model,
                "segment_count": len(transcript.segments),
                "character_count": len(full_text),
                "preview": full_text[:PREVIEW_CHARS],
                "next_step": _next_step_instructions(destination, len(full_text)),
                "low_confidence_spans": [
                    {
                        "text": span.text,
                        "context": span.context,
                        "start": round(span.start, 2),
                        "end": round(span.end, 2),
                        "min_probability": round(span.min_probability, 3),
                    }
                    for span in spans[:MAX_REPORTED_SPANS]
                ],
                "low_confidence_span_count": len(spans),
                **(
                    {"speaker_identification": note}
                    if (note := self._diarization_note())
                    else {}
                ),
                **(
                    {"speech_warning": warning}
                    if (warning := _speech_warning(full_text, media_seconds))
                    else {}
                ),
                **(
                    {
                        "unclear_audio": [
                            f"{_clock(start)}-{_clock(end)}"
                            for start, end in transcript.unclear_spans
                        ],
                        "unclear_audio_note": (
                            "The speech model kept repeating one line at these "
                            "times even after a second try, so what was said "
                            "there is missing. Tell the user which parts are "
                            "missing; do not guess what was said."
                        ),
                    }
                    if transcript.unclear_spans
                    else {}
                ),
            }
        except ToolCancelled:
            # The agent stopped waiting; don't burn minutes of GPU finishing
            # work whose result nothing will read.
            logger.warning("Transcription of %s stopped", source)
            raise ToolCancelled(
                "Transcription was stopped before it finished. The finished parts "
                "are saved: call transcribe_media again with the same file to "
                "continue from where it stopped."
            ) from None
        except Exception as e:
            logger.error("Transcription failed for %s: %s", source, e)
            failure = {"status": "error", "error": str(e)}
            # Minutes of compute already landed on disk. Losing the path
            # here is what makes a user pay for it twice.
            if saved_path is not None:
                failure["transcript_path"] = str(saved_path)
                failure["note"] = (
                    "The transcript itself was saved and is complete; the "
                    "failure happened after it was written."
                )
            return failure
        finally:
            # The decoded WAV lives in scratch; the transcript is the artifact.
            # Never let cleanup mask the real exception.
            if scratch is not None:
                try:
                    scratch.cleanup()
                except OSError as cleanup_error:
                    logger.warning(
                        "Could not remove scratch directory %s: %s",
                        scratch.name,
                        cleanup_error,
                    )

    def _refine_transcript(
        self, transcript_path: str, output_path: Optional[str] = None
    ) -> Dict:
        """Split a raw transcript into speaker turns and name them.

        Turn boundaries come from pauses in the recording, not from the model.
        Asked to segment continuous prose blind it invented sixty speakers in a
        four-person meeting, because nothing in the text marks a change of
        voice. The model's only job here is naming turns it is handed.
        """
        from gaia.agents.base.tools import ToolCancelled, raise_if_cancelled
        from gaia.audio.transcript_analysis import format_timestamp

        self._report_stage("prepare", "Preparing the transcript")
        source = _workspace_target(self, transcript_path)
        destination = (
            _workspace_target(self, output_path)
            if output_path
            else source.with_suffix(".transcript.md")
        )
        denied = self._transcript_access_error(
            source, write=False
        ) or self._transcript_access_error(destination, write=True)
        if denied:
            return denied
        if not source.is_file():
            return _missing_transcript(source)

        raw = source.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {"status": "error", "error": f"{source} is empty."}

        timings_file = timings_path_for(source)
        timings = _read_timings(source)
        segments: List[dict] = timings.get("segments", [])

        if not segments:
            return {
                "status": "error",
                "error": (
                    f"No segment timings found next to {source} "
                    f"(expected {timings_file.name}). Speaker turns are derived "
                    "from pauses in the recording; without them the result "
                    "would be invented. Re-run transcribe_media on the original "
                    "media file to regenerate both files."
                ),
            }

        speaker_spans = timings.get("speakers", [])

        if speaker_spans:
            # Real voices: identity is consistent across the whole recording,
            # which is the part text can never recover.
            turns, acoustic_labels = _turns_from_speakers(segments, speaker_spans)
        else:
            turns, acoustic_labels = _turns_from_gaps(segments, TURN_GAP_SECONDS), None
        batches = _batch_turns(turns, REFINE_TURNS_PER_CALL)
        named: List[tuple] = []
        speaker_notes: List[str] = []
        try:
            if acoustic_labels is not None:
                # Voices are already separated; only the names are unknown.
                named = [
                    (label, " ".join(seg["text"] for seg in turn).strip())
                    for turn, label in zip(turns, acoustic_labels)
                ]
                raise_if_cancelled()
                self._report_progress("Matching names to voices...")
                named = self._name_known_voices(named)
                speaker_notes = list(dict.fromkeys(n for n, _ in named))
            else:
                for index, batch in enumerate(batches, 1):
                    raise_if_cancelled()
                    self._report_progress(
                        f"Identifying speakers — batch {index} of {len(batches)}"
                    )
                    named.extend(self._name_turns(batch, speaker_notes))
            if acoustic_labels is None:
                # Only the text-only path over-splits. Merging voices separated
                # acoustically undoes real evidence.
                self._report_progress("Consolidating speakers...")
                named = self._consolidate_speakers(named)
        except ToolCancelled:
            logger.warning("Refinement of %s cancelled after timeout", source)
            raise
        except Exception as e:
            logger.error("Refinement failed for %s: %s", source, e)
            return {
                "status": "error",
                "error": (
                    f"Speaker identification failed: {e}. The raw transcript "
                    f"at {source} and its timings are unchanged; fix the cause "
                    "and call refine_transcript on it again."
                ),
                "source_transcript": str(source),
            }

        blocks = [
            f"[{format_timestamp(start)}] {name}: {text}"
            for start, name, text in _timed_paragraphs(turns, named)
        ]
        speakers = list(dict.fromkeys(name for name, _ in named))
        before_change(destination, "refine_transcript")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _render_refined(
                source, speakers, blocks, acoustic=acoustic_labels is not None
            ),
            encoding="utf-8",
        )
        # The refined file has to carry timings too: analysis and subtitles
        # read them, and this is where each segment's speaker becomes known.
        self._write_refined_timings(destination, timings, turns, named)

        self._report_stage("prepare", "Indexing transcript for questions...")
        indexed = self._index_refined(destination)
        self._report_stage("prepare", "Transcript ready", state=STAGE_DONE)

        return {
            "status": "success",
            "refined_path": str(destination),
            "speakers": speakers,
            "turns": len(_merge_adjacent(named)),
            "indexed": indexed,
            "source_transcript": str(source),
            "next_step": (
                f"Timestamped, speaker-labelled transcript saved to "
                f"{destination} and indexed, so questions about this recording "
                "can now be answered from it, with the time each thing was "
                "said.\n\n"
                "Do these two things, in this order:\n"
                f"1. If the user wants every item of some kind, timestamps or "
                f"a structured list, call analyze_transcript('{destination}', "
                "request=<what they asked for>, fields=<what to report per "
                "item>). Otherwise call "
                f"summarize_document('{destination}', summary_type='detailed') "
                "and build the brief and action items from what it returns. "
                "Both read the whole transcript. Do NOT use query_documents "
                "for this — it returns only the top few matching chunks, so "
                "the result would silently miss most of what was said.\n"
                "2. Tell the user where the files are, and that they can "
                "now ask questions about specific details of the recording.\n\n"
                "Answering with a question instead of a result is not "
                "acceptable here: the user already asked for one."
            ),
        }

    def _write_refined_timings(self, destination, timings, turns, named) -> None:
        """Save the timings beside the refined transcript, with final speakers."""
        attributed = [
            {**seg, "speaker": name}
            for turn, (name, _) in zip(turns, named)
            for seg in turn
        ]
        try:
            before_change(timings_path_for(destination), "refine_transcript")
            timings_path_for(destination).write_text(
                json.dumps({**timings, "segments": attributed}), encoding="utf-8"
            )
        except (OSError, SnapshotError) as e:
            logger.warning("Could not write timings for %s: %s", destination, e)

    def _analyze_transcript(
        self,
        transcript_path: str,
        request: str,
        fields: List[str],
        output_path: Optional[str] = None,
    ) -> Dict:
        """Read a whole transcript for one request and save the result."""
        from gaia.agents.base.tools import ToolCancelled, raise_if_cancelled
        from gaia.audio.transcript_analysis import (
            analyze_segments,
            format_timestamp,
            render_analysis,
        )

        source = _workspace_target(self, transcript_path)
        destination = (
            _workspace_target(self, output_path)
            if output_path
            else source.with_name(f"{_recording_name(source)}.analysis.md")
        )
        failure = self._timed_transcript_error(source) or (
            self._transcript_access_error(destination, write=True)
        )
        if failure:
            return failure
        if not request.strip():
            return {
                "status": "error",
                "error": "Say what to find in the recording, in `request`.",
            }

        started = time.monotonic()
        reached = [0]

        def _before_window(index, total, window):
            raise_if_cancelled()
            reached[0] += 1
            spent = time.monotonic() - started
            self._report_stage(
                "analyze",
                f"Reading section {index} of {total} "
                f"({format_timestamp(window.start)}-{format_timestamp(window.end)})",
                percent=int(min(reached[0] / total, ESTIMATE_CEILING) * 100),
                eta=int(spent / reached[0] * (total - reached[0])),
            )

        self._report_stage("analyze", "Reading the recording", percent=0)

        checkpoint = destination.with_name(destination.name + ".progress.json")
        try:
            analysis = analyze_segments(
                _read_timings(source)["segments"],
                request,
                fields,
                self._analysis_reply,
                checkpoint=checkpoint,
                on_window=_before_window,
                complete_json=self._analysis_json_reply,
                parallel=self._analysis_parallelism(),
                check=raise_if_cancelled,
                embed=self._transcript_embedder(),
                on_found=self._report_found,
            )
        except ToolCancelled:
            logger.warning("Analysis of %s cancelled", source)
            raise
        except Exception as e:
            logger.error("Analysis failed for %s: %s", source, e)
            return {
                "status": "error",
                "error": (
                    f"Could not finish analyzing {source}: {e}. Finished "
                    "sections are saved, so fix the cause and call "
                    "analyze_transcript with the same arguments to continue "
                    "from where it stopped."
                ),
            }

        before_change(destination, "analyze_transcript")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            render_analysis(analysis, _recording_name(source), str(source)),
            encoding="utf-8",
        )
        checkpoint.unlink(missing_ok=True)
        self._report_stage("analyze", "Report ready", state=STAGE_DONE)

        return {
            "status": "success",
            "analysis_path": str(destination),
            "overview": analysis.overview,
            "item_count": len(analysis.items),
            "sections": analysis.windows,
            "sections_reused": analysis.windows_reused,
            "approximate_timestamps": analysis.approximate_timestamps,
            "next_step": (
                f"The full report is saved at {destination}. Tell the user that "
                f"path, the overview and that {len(analysis.items)} items were "
                "found. To show the items, read that file: do not write them "
                "out from memory."
            ),
        }

    def _export_transcript(
        self,
        transcript_path: str,
        export_format: str = "srt",
        output_path: Optional[str] = None,
    ) -> Dict:
        """Write a timed transcript as subtitles or timestamped text."""
        from gaia.audio.transcript_analysis import to_srt, to_vtt, transcript_lines

        chosen = export_format.strip().lower().lstrip(".")
        if chosen not in EXPORT_FORMATS:
            return {
                "status": "error",
                "error": (
                    f"Unknown format '{export_format}'. Use one of: "
                    f"{', '.join(EXPORT_FORMATS)}."
                ),
            }
        source = _workspace_target(self, transcript_path)
        suffix = "timestamped.txt" if chosen == "txt" else chosen
        destination = (
            _workspace_target(self, output_path)
            if output_path
            else source.with_name(f"{_recording_name(source)}.{suffix}")
        )
        failure = self._timed_transcript_error(source) or (
            self._transcript_access_error(destination, write=True)
        )
        if failure:
            return failure

        segments = _read_timings(source)["segments"]
        rendered = {
            "srt": to_srt,
            "vtt": to_vtt,
            "txt": lambda timed: "\n".join(transcript_lines(timed)) + "\n",
        }[chosen](segments)
        before_change(destination, "export_transcript")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
        return {
            "status": "success",
            "export_path": str(destination),
            "format": chosen,
            "cues": len(segments),
        }

    def _timed_transcript_error(self, source: Path) -> Optional[Dict]:
        """Why this transcript cannot be read with timings, or None if it can."""
        denied = self._transcript_access_error(source, write=False)
        if denied:
            return denied
        if not source.is_file():
            return _missing_transcript(source)
        if not _read_timings(source).get("segments"):
            return {
                "status": "error",
                "error": (
                    f"No segment timings found next to {source} (expected "
                    f"{timings_path_for(source).name}), so times cannot be "
                    "reported. Re-run transcribe_media on the original recording "
                    "to regenerate them."
                ),
            }
        return None

    def _analysis_parallelism(self) -> int:
        """How many sections to read at once. A local server takes one at a time."""
        from gaia.llm.lemonade_client import cloud_model_provider

        model = getattr(self, "model_id", None)
        return ANALYZE_CLOUD_PARALLEL if model and cloud_model_provider(model) else 1

    def _analysis_json_reply(self, prompt: str) -> str:
        """A completion constrained to JSON. Without it, one stray bracket cost a
        section being read again in two halves."""
        return self._analysis_reply(prompt, response_format={"type": "json_object"})

    def _analysis_reply(self, prompt: str, **options) -> str:
        """One completion for analysis, raising when the reply was cut off."""
        from gaia.audio.transcript_analysis import ReplyOverflow
        from gaia.llm.lemonade_client import no_thinking_kwargs

        choice = self._llm_choice(
            prompt,
            ANALYZE_MAX_TOKENS,
            **no_thinking_kwargs(getattr(self, "model_id", None)),
            **options,
        )
        text = (choice["message"].get("content") or "").strip()
        if not text or choice.get("finish_reason") == "length":
            raise ReplyOverflow(
                f"reply cut off at {ANALYZE_MAX_TOKENS} tokens "
                f"(finish_reason={choice.get('finish_reason')!r})"
            )
        return text

    def _index_refined(self, path):
        """Index the refined transcript so summarizing and follow-ups work.

        Doing it here rather than instructing the model to: summarize_document
        requires an indexed file, and every extra instructed step is another
        one the model can skip — in testing it repeatedly reached for
        query_documents instead and answered from a couple of chunks. Indexing
        is also what makes later questions about the meeting answerable, so it
        should never depend on the model remembering.
        """
        from gaia.agents.base.tools import _TOOL_REGISTRY

        registry_getter = getattr(self, "_execution_tool_registry", None)
        registry = registry_getter() if callable(registry_getter) else _TOOL_REGISTRY
        entry = registry.get("index_document") or {}
        fn = entry.get("function") or entry.get("func")
        if not callable(fn):
            logger.info("index_document unavailable; leaving %s unindexed", path)
            return False
        try:
            result = fn(str(path))
        except Exception as e:  # noqa: BLE001 — the transcript still stands
            logger.warning("Could not index %s: %s", path, e)
            return False
        ok = isinstance(result, dict) and result.get("status") == "success"
        if not ok:
            logger.warning("Indexing %s did not succeed: %s", path, result)
        return ok

    def _name_known_voices(self, named):
        """Map diarized voice labels to real names where the text supports it.

        The voices are already separated, so this is only a naming problem —
        the stage the reference pipeline also ran after pyannote.
        """
        roster = {}
        for label, text in named:
            roster.setdefault(label, []).append(text)

        sample = "\n".join(
            f"{label}: " + " / ".join(t[:150] for t in texts[:4])
            for label, texts in roster.items()
        )
        prompt = (
            "Each label below is a distinct voice from a meeting recording, "
            "with a few things that voice said.\n\nGive each one a real name "
            "ONLY if the transcript supports it — the person introduces "
            "themselves, or someone addresses them by name. If there is no "
            "evidence, keep the label exactly as it is. A confidently wrong "
            "name is worse than an anonymous one.\n\nOutput ONLY lines of the "
            "form `Label -> Name`, one per label, including labels that stay "
            "unchanged.\n\nVoices:\n" + sample
        )
        mapping = _parse_alias_map(self._llm_text(prompt), set(roster))
        # Never let naming merge two distinct voices into one person.
        collisions = {
            v for v in mapping.values() if list(mapping.values()).count(v) > 1
        }
        mapping = {k: v for k, v in mapping.items() if v not in collisions}
        return [(mapping.get(label, label), text) for label, text in named]

    def _name_turns(
        self, batch: List[List[dict]], speaker_notes: List[str]
    ) -> List[tuple]:
        """Ask the model who speaks each pre-cut turn. Returns (name, text)."""
        texts = [" ".join(seg["text"] for seg in turn).strip() for turn in batch]
        # The pause before each turn is evidence the model would otherwise be
        # guessing without: a long silence usually means someone else started,
        # a short one usually means the same person carried on. Without it the
        # model over-split a four-person meeting into eight voices.
        gaps = [0.0] + [
            max(batch[i][0]["start"] - batch[i - 1][-1]["end"], 0.0)
            for i in range(1, len(batch))
        ]
        listing = "\n".join(
            f"{i}. [pause {gap:.1f}s] {t}" if i > 1 else f"{i}. {t}"
            for i, (gap, t) in enumerate(zip(gaps, texts), 1)
        )
        known = (
            f"\nSpeakers already identified in this meeting: "
            f"{', '.join(speaker_notes)}. Reuse those exact labels for the "
            "same people — do not invent a new label for someone already "
            "listed."
            if speaker_notes
            else ""
        )
        prompt = (
            "Below are consecutive turns from a meeting transcript. The "
            "turns are already split correctly — your only job is to say "
            "WHO speaks each one.\n\n"
            "Most meetings have 2 to 5 people. Consecutive turns are often "
            "the SAME person continuing — keep the same label unless there is "
            "a reason to change. The `[pause Xs]` before a turn is how long "
            "the silence was: under about 1.5s usually means the same person "
            "carried on, and a longer pause makes a new speaker more likely "
            "but does not prove one. Weigh it together with whether the "
            "content reads as a reply or a continuation. Use a real "
            "name only if the transcript supports it (a self-introduction, "
            "or someone addressed by name). Otherwise use Speaker A, "
            "Speaker B, and so on."
            f" Never use more than {MAX_SPEAKERS} distinct labels.{known}"
            "\n\nOutput ONLY lines of the form `N: Name`, one per turn, "
            "for every turn number below. Example:\n1: Speaker A\n"
            "2: Speaker A\n3: Priya\n\n"
            f"Turns:\n{listing}"
        )
        try:
            reply = self._llm_text(prompt)
        except RuntimeError as e:
            # A reasoning model spends its reply budget thinking; on a long
            # transcript that leaves nothing for the answer. Halving the batch
            # is better than failing the stage — the naming is per-turn and the
            # halves concatenate.
            if "finish_reason" not in str(e) or len(batch) <= MIN_TURNS_PER_CALL:
                raise
            mid = len(batch) // 2
            logger.info(
                "Naming reply overflowed at %d turns; retrying as %d + %d",
                len(batch),
                mid,
                len(batch) - mid,
            )
            return self._name_turns(batch[:mid], speaker_notes) + self._name_turns(
                batch[mid:], speaker_notes
            )
        labels = _parse_turn_labels(reply, len(texts))
        for name in labels:
            if name not in speaker_notes and len(speaker_notes) < MAX_SPEAKERS:
                speaker_notes.append(name)
        # A label past the cap is a hallucinated extra voice; fold it into
        # the previous speaker rather than let the roster grow unbounded.
        cleaned: List[tuple] = []
        for i, name in enumerate(labels):
            if name not in speaker_notes:
                name = cleaned[-1][0] if cleaned else "Speaker A"
            cleaned.append((name, texts[i]))
        return cleaned

    def _consolidate_speakers(self, named):
        """Merge labels that are the same person under different names.

        Turn naming runs in batches, and within a batch the model changes
        speaker more eagerly than people actually do — it found eight voices in
        a four-person meeting. This pass sees the whole roster at once and
        folds the duplicates together.
        """
        roster = {}
        for name, text in named:
            roster.setdefault(name, []).append(text)
        if len(roster) < 3:
            return named

        sample = "\n".join(
            f"{name}: " + " / ".join(t[:120] for t in texts[:3])
            for name, texts in roster.items()
        )
        prompt = (
            "A meeting transcript was labelled in batches, so the same person "
            "may have been given more than one label. Below is each label with "
            "a few of its lines.\n\nDecide which labels are the SAME person. "
            "Merge freely — most meetings have 2 to 5 people, and splitting one "
            "person across labels is the common error here.\n\nOutput ONLY "
            "lines of the form `OldLabel -> FinalLabel`, one per label, "
            "including labels that stay as they are. Use a real name as the "
            "final label when one is evident.\n\nLabels:\n" + sample
        )
        mapping = _parse_alias_map(self._llm_text(prompt), set(roster))
        if not mapping:
            return named
        return [(mapping.get(name, name), text) for name, text in named]

    def _llm_text(self, prompt: str) -> str:
        """One-shot completion on whatever LLM this agent is already using.

        The token ceiling is explicit: the client's 1000-token default truncated
        every section, and on a reasoning model the whole budget went to
        ``reasoning_content`` leaving ``content`` empty — which silently wrote a
        transcript with no transcript in it.
        """
        choice = self._llm_choice(prompt, REFINE_MAX_TOKENS)
        text = (choice["message"].get("content") or "").strip()
        if not text:
            raise RuntimeError(
                "The language model returned no transcript text for this "
                f"section (finish_reason={choice.get('finish_reason')!r}). The "
                "section may be too large for the model's reply budget — retry, "
                "or transcribe a shorter recording."
            )
        return text

    def _llm_choice(self, prompt: str, max_tokens: int, **options) -> dict:
        """The raw completion choice, on the LLM this agent already uses."""
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, create_lemonade_client

        client = getattr(self, "_refine_llm", None)
        if client is None:
            client = create_lemonade_client(auto_start=False, verbose=False)
            self._refine_llm = client

        model = getattr(self, "model_id", None) or DEFAULT_MODEL_NAME
        response = client.chat_completions(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=max_tokens,
            **options,
        )
        return response["choices"][0]

    def _existing_transcript(self, source, output_path):
        """Return the previous result for *source*, or None to transcribe it.

        Transcribing is the expensive, deterministic part of this pipeline, and
        the model reaches this tool again whenever a follow-up question mentions
        the recording. Without this, "what did they say about pricing?" costs
        another full decode, transcription and diarization pass.

        Re-transcribes only when the recording is newer than the transcript, so
        editing or replacing the file still does the right thing.
        """
        destination = self._transcript_destination(source, output_path)
        timings = timings_path_for(destination)
        if not (destination.is_file() and timings.is_file()):
            return None
        if _belongs_to_other_recording(destination, source):
            return None
        try:
            if source.stat().st_mtime > destination.stat().st_mtime:
                logger.info("%s is newer than its transcript; re-transcribing", source)
                return None
            text = destination.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.strip():
            return None

        logger.info("Reusing the transcript already at %s", destination)
        self._report_stage("transcribe", "Already transcribed", state=STAGE_DONE)
        duration = _read_timings(destination).get("duration") or 0.0
        warning = _speech_warning(text, duration) if duration else None
        return {
            "status": "success",
            "transcript_path": str(destination),
            "reused": True,
            **({"speech_warning": warning} if warning else {}),
            "language": "",
            "character_count": len(text),
            "preview": text[:PREVIEW_CHARS],
            "low_confidence_spans": [],
            "low_confidence_span_count": 0,
            "next_step": (
                f"This recording was already transcribed; {destination} is the "
                "existing transcript and nothing was re-run.\n\nIf you are "
                "answering a question about the meeting, the transcript is "
                "already indexed — use query_documents rather than calling this "
                "tool again. If you still need the speaker-labelled version, "
                f"call refine_transcript('{destination}'). For every item of "
                "some kind, with timestamps, call analyze_transcript on the "
                "refined file."
            ),
        }

    def _transcript_access_error(self, path: Path, *, write: bool):
        """The allowed-folders refusal for *path*, or None.

        GAIA's own transcript folder is exempt: it is where this tool writes,
        and the follow-up calls must be able to read back what it wrote.
        """
        if Path(os.path.realpath(path)).is_relative_to(
            Path(os.path.realpath(TRANSCRIPT_DIR))
        ):
            return None
        if write:
            return write_access_error(self, path)
        return read_access_error(self, path)

    def _transcript_destination(self, source, output_path):
        """Where this recording's transcript lives."""
        if output_path:
            return _workspace_target(self, output_path)
        stem = _plain_stem(source)
        destination = TRANSCRIPT_DIR / f"{stem}.txt"
        if _belongs_to_other_recording(destination, source):
            tag = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:8]
            return TRANSCRIPT_DIR / f"{stem}-{tag}.txt"
        return destination

    def _speaker_spans(self, wav_path, speakers: int, seconds: float):
        """Who spoke when, honouring what the caller knows about the speakers."""
        if speakers == 1:
            self._report_stage("voices", "One speaker", state=STAGE_SKIPPED)
            return [_SoleSpeaker(end=seconds)]
        expected = seconds / 60 * VOICE_SECONDS_PER_MINUTE
        self._report_stage("voices", "Separating voices", percent=0, eta=int(expected))
        with _StageEstimate(self._stage_tick("voices", "Separating voices"), expected):
            spans = self._diarize_if_possible(wav_path, speakers or None)
        self._report_stage("voices", "Voices separated", state=STAGE_DONE)
        return spans

    def _diarize_if_possible(self, wav_path, num_speakers=None):
        """Work out who spoke when, from the audio, while the WAV still exists.

        Runs here rather than in refine_transcript because this is the only
        point where the decoded audio is on disk — refinement sees text. A
        failure is reported and skipped rather than fatal: an unattributed
        transcript is still worth having, and the caller is told which one it
        got.
        """
        from gaia.audio import diarize as diarization
        from gaia.tool_cancellation import ToolCancelled

        try:
            if not diarization.is_available():
                self._report_progress(
                    "Setting up speaker identification (one-time download)..."
                )
                diarization.ensure_ready(self._report_progress)
            return diarization.diarize_in_child(wav_path, num_speakers=num_speakers)
        except ToolCancelled:
            raise
        except Exception as e:  # noqa: BLE001 — transcription still succeeded
            logger.warning("Speaker identification unavailable: %s", e)
            self._diarization_error = str(e)
            return []

    def _diarization_note(self):
        """What to tell the caller when voices could not be separated."""
        reason = getattr(self, "_diarization_error", None)
        if not reason:
            return None
        return (
            "Speaker identification did not run, so this transcript has no "
            f"speaker labels. Reason: {reason} Say this plainly in your reply "
            "rather than presenting the summary as if speakers were identified."
        )

    def _write_transcript(self, transcript, source: Path, output_path: Optional[str]):
        """Persist the transcript before any later stage can fail."""
        destination = self._transcript_destination(source, output_path)
        before_change(destination, "transcribe")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(transcript.text, encoding="utf-8")
        return destination

    def _transcription_status(self) -> Dict:
        """Report component availability without installing anything."""
        from gaia.audio.lemonade_asr import LemonadeASRClient
        from gaia.audio.media import find_ffmpeg

        ffmpeg_path = find_ffmpeg()

        models: List[str] = []
        lemonade_error = None
        try:
            models = LemonadeASRClient().available_models()
        except Exception as e:
            lemonade_error = str(e)

        ready = bool(ffmpeg_path) and bool(models)
        result = {
            "status": "success",
            "ready": ready,
            "ffmpeg": ffmpeg_path or "not installed (installed on first transcription)",
            "lemonade": "reachable" if lemonade_error is None else "unreachable",
            "available_models": models,
        }
        if lemonade_error:
            result["lemonade_error"] = lemonade_error
        return result

    def _download_speech_model(self, client) -> None:
        """Download a missing speech model as its own visible step.

        Lemonade would otherwise pull it inside the first transcription
        request, behind a "Transcribing" line that knows nothing of it.
        """
        from gaia.agents.base.tools import raise_if_cancelled

        line = [""]

        def say(message: str) -> None:
            size = re.search(r"\(~[\d.]+ GB\)", message)
            line[0] = (
                f"Downloading the speech model {client.model}"
                + (f" {size.group(0)}" if size else "")
                + ", one time"
            )
            self._report_progress(line[0])

        def on_percent(percent: int) -> None:
            raise_if_cancelled()
            self._report_progress(f"{line[0]} — {percent}%")

        client.ensure_model(say=say, on_percent=on_percent)

    def _report_download(self, message: str) -> None:
        """Turn the downloader's progress lines into the download stage."""
        match = re.search(r"(\d+)%", message)
        self._report_stage(
            "download", message, percent=int(match.group(1)) if match else None
        )

    def _stage_tick(self, stage: str, label: str) -> Callable[[int, int], None]:
        """A reporter for :class:`_StageEstimate`, one status line per percent."""

        def tick(percent: int, eta: int) -> None:
            self._report_stage(stage, f"{label} — {percent}%", percent=percent, eta=eta)

        return tick

    def _report_stage(
        self,
        stage: str,
        message: str,
        *,
        percent: Optional[int] = None,
        eta: Optional[int] = None,
        state: int = STAGE_RUNNING,
    ) -> None:
        """Say where a recording stands, for clients that can draw it."""
        logger.info(message)
        console = getattr(self, "console", None)
        if console is None or not hasattr(console, "report_phase"):
            return
        fields = {"stage": RECORDING_STAGES.index(stage), "state": state}
        if percent is not None:
            fields["percent"] = percent
        if eta is not None:
            fields["eta_seconds"] = eta
        console.report_phase("recording", message, **fields)

    def _transcript_embedder(self):
        """Embeds transcript lines with the agent's memory embedder, if it has one.

        Without one, analysis windows end at pauses and sentence ends instead
        of where the topic changes.
        """
        get_embedder = getattr(self, "_get_embedder", None)
        if get_embedder is None:
            logger.info("No embedder on this agent: sections end at pauses only")
            return None
        model = self._active_embedding_model()

        def embed(texts):
            return get_embedder().embed(texts, model=model)

        return embed

    def _report_found(self, index, total, window, items, summary) -> None:
        """Show one section's results while the rest are still being read."""
        from gaia.audio.transcript_analysis import format_timestamp

        console = getattr(self, "console", None)
        if console is None or not hasattr(console, "report_phase"):
            return
        console.report_phase(
            "recording_found",
            f"Section {index} of {total}: {len(items)} found",
            section=index,
            sections=total,
            start=format_timestamp(window.start),
            end=format_timestamp(window.end),
            summary=summary,
            items=[
                {
                    "title": item.title,
                    "start": format_timestamp(item.start),
                    "seconds": item.start,
                }
                for item in items
            ],
        )

    def _report_progress(self, message: str) -> None:
        """Surface a progress line if the agent has a console attached.

        Decoding and transcription both run for minutes on real recordings, so
        silence here reads as a hang.
        """
        logger.info(message)
        console = getattr(self, "console", None)
        if console is None or not hasattr(console, "start_progress"):
            return
        console.start_progress(message)
