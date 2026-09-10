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


def _render_refined(source: Path, speakers: List[str], blocks: List[str]) -> str:
    """Assemble the refined transcript, with its provenance on the page."""
    key = "\n".join(f"- {name}" for name in speakers) or "- (none identified)"
    return (
        f"# Transcript — {source.stem}\n\n"
        f"Source: {source}\n\n"
        "Corrected for likely mis-hearings and split into speaker turns. "
        "Names are inferred from the conversation, not from voice "
        "identification, so treat them as best-effort.\n\n"
        f"## Speakers\n\n{key}\n\n## Transcript\n\n" + "\n\n".join(blocks) + "\n"
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

            def _decoding(fraction: float) -> None:
                # Decode is the one stage that reports often enough to abort
                # promptly if the agent has already stopped waiting.
                raise_if_cancelled()
                self._report_progress(
                    f"Decoding {source.name} — {min(fraction, 1.0):.0%} "
                    f"of {_clock(media_seconds)}"
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
        """Correct and attribute a raw transcript, section by section.

        The two quality stages are a tool rather than skill prose because a
        small local model reliably skips multi-step instructions — it summarised
        the raw transcript and reported no speakers. One call it cannot skip.
        """
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

        sections = _split_sections(raw, REFINE_SECTION_CHARS)
        refined: List[str] = []
        speaker_notes: List[str] = []
        try:
            for index, section in enumerate(sections, 1):
                raise_if_cancelled()
                self._report_progress(
                    f"Refining transcript — section {index} of {len(sections)}"
                )
                body = self._refine_section(section, index, speaker_notes)
                refined.append(body)
        except ToolCancelled:
            logger.warning("Refinement of %s cancelled after timeout", source)
            raise
        except Exception as e:
            logger.error("Refinement failed for %s: %s", source, e)
            return {"status": "error", "error": str(e)}

        speakers = _collect_speakers(refined)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            _render_refined(source, speakers, refined), encoding="utf-8"
        )

        return {
            "status": "success",
            "refined_path": str(destination),
            "speakers": speakers,
            "sections": len(sections),
            "source_transcript": str(source),
            "next_step": (
                f"Corrected, speaker-labelled transcript saved to {destination}. "
                "Tell the user this path. Then finish in two calls:\n"
                f"1. index_document('{destination}') — required before "
                "summarizing, and it is also what lets the user ask follow-up "
                "questions about this meeting afterwards. Never skip it.\n"
                f"2. summarize_document('{destination}', summary_type='detailed')"
                " — this folds the whole transcript forward in sections, so the "
                "brief and action items cover the entire meeting.\n"
                "Do not summarise the raw transcript, and do not use "
                "query_documents to build the summary — it returns only the top "
                "few matching chunks and would miss most of the meeting. "
                "query_documents IS the right tool for a later follow-up "
                "question about a specific detail.\n"
                "Finally, invite the follow-up: tell the user they can ask "
                "questions about the meeting and you will answer from the "
                "indexed transcript."
            ),
        }

    def _refine_section(
        self, section: str, index: int, speaker_notes: List[str]
    ) -> str:
        """Rewrite one section with speakers labelled and mis-hearings fixed."""
        carried = (
            f"\nSpeakers already identified earlier: {', '.join(speaker_notes)}."
            if speaker_notes
            else ""
        )
        prompt = (
            "You are cleaning up a raw speech-to-text transcript of a meeting.\n"
            "Do exactly two things and nothing else:\n"
            "1. Fix clear mis-hearings — words the recognizer obviously got "
            "wrong given the surrounding context (product names, jargon, "
            "names). If you are not confident, leave the original wording.\n"
            "2. Split the text into speaker turns and label each one. Use a "
            "real name when the transcript supports it (someone introduces "
            "themselves, or is addressed by name). Otherwise use Speaker A, "
            "Speaker B, and so on, consistently.\n\n"
            "Rules: keep every point that was made. Do not summarize, "
            "shorten, add, or editorialize. Output ONLY the labelled "
            "transcript, one turn per line, formatted exactly as "
            "'Name: what they said'." + carried + "\n\n"
            f"Transcript section {index}:\n{section}"
        )
        text = self._llm_text(prompt)
        for name in _collect_speakers([text]):
            if name not in speaker_notes:
                speaker_notes.append(name)
        return text.strip()

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
