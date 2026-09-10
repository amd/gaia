# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AudioToolsMixin — transcribe audio and video files for GAIA agents."""

import re
from pathlib import Path
from typing import Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

# Below this, a word is worth showing to the correction pass.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# Transcripts land here unless the caller names a path.
TRANSCRIPT_DIR = Path.home() / ".gaia" / "transcripts"

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

# Silence long enough to usually mean someone else started talking.
TURN_GAP_SECONDS = 0.8

# Turns per naming call. Small replies, so this is bounded by how much
# transcript the model can hold in view, not by its reply budget.
REFINE_TURNS_PER_CALL = 40

# A real meeting has a handful of voices. Without this the model assigned
# a new speaker per sentence — sixty of them in a four-person meeting.
MAX_SPEAKERS = 8


# Refinement is one LLM pass per section; a 46-minute meeting is ~9 sections.
REFINE_TOOL_TIMEOUT = 3600

# A section must fit the REPLY budget, not just the context window: the model
# re-emits the whole section with speaker labels, so output ~= input.
REFINE_SECTION_CHARS = 3500

# Room for the section back out, plus a reasoning model's private thinking.
REFINE_MAX_TOKENS = 4096

# A speaker label is one to three capitalised words, or "Speaker X". Prose
# preambles a small model emits ("Note:", "Action items:", "Here is the
# corrected transcript:") must not become speakers — they used to, and then
# propagated into every later section via the carried speaker list.
_SPEAKER_LINE = re.compile(
    r"^(?:Speaker [A-Z0-9]{1,3}|[A-Z][a-z'-]+(?: [A-Z][a-z'-]+){0,2})\s*:",
    re.MULTILINE,
)
_NOT_A_SPEAKER = frozenset(
    {"Note", "Notes", "Summary", "Transcript", "Action", "Speakers", "Output"}
)


def _split_sections(text: str, limit: int) -> List[str]:
    """Split on sentence boundaries so a turn is not cut mid-thought."""
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?])\s+", text)
    sections, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > limit:
            sections.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        sections.append(current)
    return sections


_TURN_SPAN = re.compile(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)


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


def timings_path_for(transcript_path: Path) -> Path:
    """Where the segment timings for a transcript live."""
    return transcript_path.with_suffix(transcript_path.suffix + ".timing.json")


def _write_timings(destination: Path, transcript, speaker_spans=None) -> None:
    """Persist segment start/end/text next to the transcript.

    Speaker turns cannot be recovered from continuous prose — asked to segment
    it blind, the model invented sixty speakers in a four-person meeting. A
    pause between segments is the one piece of real evidence available without
    acoustic diarization, so it has to survive past the transcribe call.
    """
    import json

    payload = {
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
        timings_path_for(destination).write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
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


def _sentences(text: str) -> List[str]:
    """Split a section into sentences, the unit a speaker turn starts on."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _parse_turn_spans(reply: str, count: int) -> List[tuple]:
    """Read `START-END: Name` lines into ordered, gap-free spans.

    Every sentence must land in exactly one turn — a model that skips or
    overlaps ranges would otherwise silently drop speech. Gaps are healed by
    extending the previous turn, and anything left over is appended, so the
    output always contains the whole section.
    """
    spans: List[tuple] = []
    for raw_start, raw_end, name in _TURN_SPAN.findall(reply):
        start, end = int(raw_start), int(raw_end)
        label = name.strip().rstrip(":").strip()
        if not label or start < 1 or end < start or start > count:
            continue
        end = min(end, count)
        if spans and start <= spans[-1][1]:
            start = spans[-1][1] + 1  # overlap: the earlier turn wins
            if start > end:
                continue
        if spans and start > spans[-1][1] + 1:
            prev = spans[-1]
            spans[-1] = (prev[0], start - 1, prev[2])  # heal the gap
        spans.append((start, end, label))

    if not spans:
        return [(1, count, "Speaker A")]
    if spans[0][0] > 1:
        spans[0] = (1, spans[0][1], spans[0][2])
    if spans[-1][1] < count:
        last = spans[-1]
        spans[-1] = (last[0], count, last[2])
    return spans


def _collect_speakers(blocks: List[str]) -> List[str]:
    """Distinct speaker labels, in first-appearance order."""
    seen: List[str] = []
    for block in blocks:
        for line in _SPEAKER_LINE.findall(block):
            clean = line.rstrip(":").strip()
            if not clean or clean in seen:
                continue
            if clean.split()[0] in _NOT_A_SPEAKER:
                continue
            seen.append(clean)
    return seen


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
        f"1. Call refine_transcript('{destination}') — it fixes mis-hearings "
        "and labels the speakers, and returns the path of a better transcript.\n"
        "2. Call summarize_document on the file refine_transcript returns, for "
        "the summary and action items.\n"
        "Do NOT summarise this raw file, do NOT summarise from `preview` (it "
        "is the first 1200 characters only), and do NOT use query_documents "
        "for a summary — it returns only the top few matching chunks and would "
        "miss most of the meeting."
    )


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

    Speech-to-text runs on Lemonade Server, so no local torch or Whisper
    install is involved. ffmpeg is fetched on first use, never at startup.

    Deliberately one tool. A companion "is transcription available?" tool was
    removed after a trace showed the model calling it four times in a row and
    never reaching ``transcribe_media``: it reports readiness without changing
    any state, so re-calling it always looks reasonable. ``transcribe_media``
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
        ) -> Dict:
            """Transcribe an audio or video file and SAVE it to a text file.

            Handles mp4, mkv, mov, m4a, mp3, wav and anything else ffmpeg can
            decode.

            This does NOT return the transcript — a long meeting would be
            truncated on the way back. It returns the file path plus a short
            preview. To summarise or answer questions, read the saved file:
            index_document(transcript_path) then summarize_document(...), or
            query_documents(...). Never summarise from `preview`; it is only
            the first 1200 characters.

            Long files take minutes. A 45-minute recording is roughly 5 minutes
            of transcription.

            Args:
                file_path: Path to the audio or video file.
                language: ISO 639-1 code such as "en". Empty auto-detects.
                model: Lemonade transcription model. Empty uses the default.
                output_path: Where to write the transcript. Empty writes to
                             ~/.gaia/transcripts/<name>.txt

            Returns:
                Dictionary with status, transcript_path, preview,
                character_count, segment_count, audio_duration, language,
                model, next_step, low_confidence_spans, and
                low_confidence_span_count
            """
            return self._transcribe_media(
                file_path,
                language=language or None,
                model=model or None,
                output_path=output_path or None,
            )

        @tool(timeout=REFINE_TOOL_TIMEOUT)
        def refine_transcript(
            transcript_path: str,
            output_path: str = "",
        ) -> Dict:
            """Correct mis-hearings and label the speakers in a raw transcript.

            Call this on the file `transcribe_media` produced, BEFORE
            summarizing. It works through the transcript in sections, repairs
            wording the recognizer was unsure about, works out who is speaking
            from self-introductions and direct address, and writes a corrected,
            speaker-labelled transcript to a new file.

            A summary built from the raw transcript has no speakers and can
            repeat mis-heard names, so the action items come out wrong. Always
            summarize the file this returns, not the raw one.

            Args:
                transcript_path: The raw transcript from transcribe_media.
                output_path: Where to write the refined transcript. Empty
                             writes alongside as <name>.transcript.md

            Returns:
                Dictionary with status, refined_path, speakers, sections,
                corrections_applied and next_step
            """
            return self._refine_transcript(transcript_path, output_path or None)

    def _transcribe_media(
        self,
        file_path: str,
        language: Optional[str] = None,
        model: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Dict:
        """Decode the file to WAV, transcribe it, and persist the transcript."""
        from gaia.agents.base.tools import ToolCancelled, raise_if_cancelled
        from gaia.audio.lemonade_asr import DEFAULT_ASR_MODEL, LemonadeASRClient
        from gaia.audio.media import ensure_ffmpeg, probe_duration, to_wav16k_mono

        source = Path(file_path).expanduser()
        if not source.is_file():
            return {
                "status": "error",
                "error": (
                    f"No such file: {source}. Give the full path to an audio or "
                    "video file."
                ),
            }

        # ffmpeg and the model pull are both slow and both happen here, never
        # at startup — the user only pays for them if they actually transcribe.
        try:
            ensure_ffmpeg()
        except Exception as e:
            return {"status": "error", "error": str(e)}

        wav_path = None
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
                self._report_progress(
                    f"Decoding {source.name} — {pct:.0%} of {_clock(media_seconds)}"
                )

            wav_path = to_wav16k_mono(source, progress_callback=_decoding)

            raise_if_cancelled()
            client = LemonadeASRClient(model=model or DEFAULT_ASR_MODEL)
            self._report_progress(
                f"Transcribing {_clock(media_seconds)} of audio with {client.model} "
                f"— around {_clock(media_seconds / REALTIME_FACTOR)} to go"
            )
            transcript = client.transcribe(wav_path, language=language)

            destination = self._write_transcript(transcript, source, output_path)
            saved_path = destination
            speaker_spans = self._diarize_if_possible(wav_path)
            _write_timings(destination, transcript, speaker_spans)
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
            }
        except ToolCancelled:
            # The agent stopped waiting; don't burn minutes of GPU finishing
            # work whose result nothing will read.
            logger.warning("Transcription of %s cancelled after timeout", source)
            raise
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
            # The decoded WAV is a large scratch file; the transcript is
            # the artifact. Never let cleanup mask the real exception.
            if wav_path is not None:
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning(
                        "Could not remove scratch WAV %s: %s",
                        wav_path,
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
        import json

        from gaia.agents.base.tools import ToolCancelled, raise_if_cancelled

        source = Path(transcript_path).expanduser()
        if not source.is_file():
            return {
                "status": "error",
                "error": (
                    f"No transcript at {source}. Run transcribe_media first and "
                    "pass the transcript_path it returns."
                ),
            }

        raw = source.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {"status": "error", "error": f"{source} is empty."}

        destination = (
            Path(output_path).expanduser()
            if output_path
            else source.with_suffix(".transcript.md")
        )

        timings_file = timings_path_for(source)
        segments: List[dict] = []
        if timings_file.is_file():
            try:
                segments = json.loads(timings_file.read_text(encoding="utf-8")).get(
                    "segments", []
                )
            except (OSError, ValueError) as e:
                logger.warning("Could not read timings %s: %s", timings_file, e)

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

        speaker_spans = []
        if timings_file.is_file():
            try:
                speaker_spans = json.loads(
                    timings_file.read_text(encoding="utf-8")
                ).get("speakers", [])
            except (OSError, ValueError):
                speaker_spans = []

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
        except ToolCancelled:
            logger.warning("Refinement of %s cancelled after timeout", source)
            raise
        except Exception as e:
            logger.error("Refinement failed for %s: %s", source, e)
            return {"status": "error", "error": str(e)}

        if acoustic_labels is None:
            # Only the text-only path over-splits. Merging voices
            # separated acoustically undoes real evidence — it
            # collapsed four measured voices into two.
            self._report_progress("Consolidating speakers...")
            named = self._consolidate_speakers(named)
        blocks = [f"{name}: {text}" for name, text in _merge_adjacent(named)]
        speakers = list(dict.fromkeys(name for name, _ in named))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _render_refined(
                source, speakers, blocks, acoustic=acoustic_labels is not None
            ),
            encoding="utf-8",
        )

        self._report_progress("Indexing transcript for questions...")
        indexed = self._index_refined(destination)

        return {
            "status": "success",
            "refined_path": str(destination),
            "speakers": speakers,
            "turns": len(blocks),
            "indexed": indexed,
            "source_transcript": str(source),
            "next_step": (
                f"Speaker-labelled transcript saved to {destination} and "
                "indexed, so questions about this meeting can now be "
                "answered from it.\n\n"
                "Do these two things, in this order:\n"
                f"1. Call summarize_document('{destination}', "
                "summary_type='detailed') and build the brief and action "
                "items from what it returns. It folds the whole transcript "
                "forward in sections, so the brief covers the entire "
                "meeting. Do NOT use query_documents for this — it returns "
                "only the top few matching chunks, so the brief would "
                "silently miss most of what was said.\n"
                "2. Tell the user where both files are, and that they can "
                "now ask questions about specific details of the meeting.\n\n"
                "Answering with a question instead of a summary is not "
                "acceptable here: the user already asked for one."
            ),
        }

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

        entry = _TOOL_REGISTRY.get("index_document") or {}
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
        try:
            mapping = _parse_alias_map(self._llm_text(prompt), set(roster))
        except Exception as e:  # noqa: BLE001 — anonymous labels still work
            logger.info("Could not put names to voices (%s)", e)
            return named
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
        labels = _parse_turn_labels(self._llm_text(prompt), len(texts))
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
        try:
            mapping = _parse_alias_map(self._llm_text(prompt), set(roster))
        except Exception as e:  # noqa: BLE001 — consolidation is an improvement
            logger.info("Speaker consolidation skipped (%s)", e)
            return named
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
            max_completion_tokens=REFINE_MAX_TOKENS,
        )
        choice = response["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if not text:
            raise RuntimeError(
                "The language model returned no transcript text for this "
                f"section (finish_reason={choice.get('finish_reason')!r}). The "
                "section may be too large for the model's reply budget — retry, "
                "or transcribe a shorter recording."
            )
        return text

    def _diarize_if_possible(self, wav_path):
        """Work out who spoke when, from the audio, while the WAV still exists.

        Runs here rather than in refine_transcript because this is the only
        point where the decoded audio is on disk — refinement sees text. A
        failure is reported and skipped rather than fatal: an unattributed
        transcript is still worth having, and the caller is told which one it
        got.
        """
        from gaia.audio import diarize as diarization

        try:
            if not diarization.is_available():
                self._report_progress(
                    "Setting up speaker identification (one-time download)..."
                )
                diarization.ensure_ready(self._report_progress)
            return diarization.diarize(wav_path, progress=self._report_progress)
        except Exception as e:  # noqa: BLE001 — transcription still succeeded
            logger.warning("Speaker identification unavailable: %s", e)
            self._diarization_error = str(e)
            return []

    def _write_transcript(self, transcript, source: Path, output_path: Optional[str]):
        """Persist the transcript before any later stage can fail."""
        if output_path:
            destination = Path(output_path).expanduser()
        else:
            TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
            destination = TRANSCRIPT_DIR / f"{source.stem}.txt"
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
