# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for AudioToolsMixin — the agent-facing transcription surface."""

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.registry import KNOWN_TOOLS
from gaia.agents.tools import audio_tools
from gaia.agents.tools.audio_tools import RECORDING_STAGES, AudioToolsMixin
from gaia.audio.lemonade_asr import LemonadeASRError, Segment, Transcript, Word


class Host(AudioToolsMixin):
    """Bare mixin host — no Agent machinery needed for these paths."""


def _collecting_tool(registered: dict):
    """Stand-in for @tool that supports both ``@tool`` and ``@tool(...)``."""

    def fake_tool(func=None, **_kwargs):
        def decorate(f):
            registered[f.__name__] = f
            return f

        return decorate(func) if callable(func) else decorate

    return fake_tool


def _transcript() -> Transcript:
    """A transcript whose only shaky word is the one Whisper actually got wrong."""
    words = [
        Word(word=" ship", start=0.0, end=0.4, probability=0.98),
        Word(word=" diar", start=0.4, end=0.7, probability=0.31),
        Word(word="ization", start=0.7, end=1.1, probability=0.95),
    ]
    return Transcript(
        segments=[
            Segment(
                start=0.0,
                end=1.1,
                text=" ship diarization",
                avg_logprob=-0.2,
                words=words,
            )
        ],
        language="english",
        duration=1.1,
        model="Whisper-Base",
    )


class TestRegistration:
    def test_registered_in_known_tools(self):
        assert KNOWN_TOOLS["audio"] == (
            "gaia.agents.tools.audio_tools",
            "AudioToolsMixin",
        )

    def test_registrar_exposes_the_pipeline_tools(self):
        registered = {}
        with patch("gaia.agents.base.tools.tool", _collecting_tool(registered)):
            Host().register_audio_tools()

        # No status tool: a companion one caused a 4-call loop that never transcribed.
        assert set(registered) == {
            "transcribe_media",
            "refine_transcript",
            "analyze_transcript",
            "export_transcript",
        }

    def test_transcribe_declares_a_timeout_long_enough_for_a_real_meeting(self):
        """The agent's 180s default abandons a 45-min transcription mid-flight.

        Regression: the call was dropped at 180s even though the transcript
        completed and landed on disk, so the agent reported a timeout while
        the work had actually succeeded.
        """
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.tools.audio_tools import TRANSCRIBE_TOOL_TIMEOUT

        Host().register_audio_tools()
        timeout = _TOOL_REGISTRY["transcribe_media"].get("timeout")

        assert timeout == TRANSCRIBE_TOOL_TIMEOUT
        # 45 min of audio at ~9x realtime is ~300s; leave real headroom.
        assert timeout >= 1800

    def test_tools_are_documented(self):
        """Docstrings are the model's only spec for these tools."""
        registered = {}
        with patch("gaia.agents.base.tools.tool", _collecting_tool(registered)):
            Host().register_audio_tools()

        for name, fn in registered.items():
            assert fn.__doc__ and len(fn.__doc__) > 80, f"{name} needs a real docstring"


class TestSystemPromptAgreesWithTheToolset:
    """A prompt that says "I can't" beats a tool that says "I can"."""

    def _chat_agent_source(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "hub/agents/gaia/python/gaia_agent/chat_agent.py"
        )
        if not path.is_file():
            pytest.skip(f"chat agent not present at {path}")
        return path.read_text(encoding="utf-8")

    def test_the_prompt_does_not_call_audio_or_video_unsupported(self):
        """Regression: the installed agent refused an .mp4 outright.

        transcribe_media was registered and working, but the system prompt
        still listed "video/audio analysis" as UNSUPPORTED, so the model
        declined before trying — and pointed the user at a feature request
        for something that had already shipped.
        """
        source = self._chat_agent_source()
        for claim in ("video/audio analysis", "audio/video analysis"):
            assert claim not in source, (
                f"the system prompt still lists {claim!r} as unsupported, "
                "which makes the agent refuse recordings it can transcribe"
            )

    def test_the_prompt_says_recordings_are_supported(self):
        source = self._chat_agent_source()
        assert "transcribe_media" in source, (
            "the prompt should name transcribe_media so the model knows "
            "recordings are handled"
        )


class TestTranscribeMedia:
    @pytest.fixture(autouse=True)
    def _no_real_diarization(self):
        # The real path pip-installs sherpa-onnx and downloads models.
        with patch.object(Host, "_diarize_if_possible", return_value=[]):
            yield

    @pytest.mark.parametrize("fail_decode", [False, True])
    def test_owned_scratch_directory_is_removed_on_failure(self, tmp_path, fail_decode):
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"source")
        decoded = []

        def decode(_source, *, dest, progress_callback):
            decoded.append(dest)
            dest.write_bytes(b"partial audio")
            if fail_decode:
                raise RuntimeError("decode failed")
            return dest

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=10),
            patch("gaia.audio.media.to_wav16k_mono", side_effect=decode),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.transcribe.side_effect = ConnectionError("ASR down")
            result = Host()._transcribe_media(str(source))

        assert result["status"] == "error"
        assert len(decoded) == 1
        assert not decoded[0].parent.exists()
        assert source.read_bytes() == b"source"

    def test_a_speech_model_download_is_shown_before_transcribing(self, tmp_path):
        """The first transcription must not sit on "Transcribing" while 1.5 GB downloads."""
        source = tmp_path / "standup.wav"
        source.write_bytes(b"stub")
        shown = []
        host = Host()
        host.console = type(
            "Console",
            (),
            {
                "start_progress": lambda self, msg: shown.append(("status", msg)),
                "report_phase": lambda self, phase, msg, **f: shown.append(
                    (RECORDING_STAGES[f["stage"]], msg)
                ),
            },
        )()

        def ensure_model(say=None, on_percent=None):
            say("Downloading Whisper-Large-v3-Turbo through Lemonade (~1.55 GB)")
            for percent in (0, 40, 100):
                on_percent(percent)

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=14.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=source),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "Whisper-Large-v3-Turbo"
            client.return_value.ensure_model.side_effect = ensure_model
            client.return_value.transcribe.return_value = _transcript()
            result = host._transcribe_media(
                str(source), output_path=str(tmp_path / "standup.txt")
            )

        assert result["status"] == "success", result
        downloading = [msg for kind, msg in shown if "speech model" in msg]
        assert downloading == [
            "Downloading the speech model Whisper-Large-v3-Turbo (~1.55 GB), one time",
            "Downloading the speech model Whisper-Large-v3-Turbo (~1.55 GB), "
            "one time — 0%",
            "Downloading the speech model Whisper-Large-v3-Turbo (~1.55 GB), "
            "one time — 40%",
            "Downloading the speech model Whisper-Large-v3-Turbo (~1.55 GB), "
            "one time — 100%",
        ]
        # Its own state: nothing says "Transcribing" until the download is over.
        first_transcribe = next(
            i for i, (kind, _) in enumerate(shown) if kind == "transcribe"
        )
        last_download = max(
            i for i, (_, msg) in enumerate(shown) if "speech model" in msg
        )
        assert last_download < first_transcribe

    def test_a_failed_speech_model_download_is_the_tools_error(self, tmp_path):
        source = tmp_path / "standup.wav"
        source.write_bytes(b"stub")
        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=14.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=source),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "Whisper-Large-v3-Turbo"
            client.return_value.ensure_model.side_effect = LemonadeASRError(
                "Download of Whisper-Large-v3-Turbo failed: disk full"
            )
            result = Host()._transcribe_media(str(source))

        assert result["status"] == "error"
        assert "disk full" in result["error"]
        client.return_value.transcribe.assert_not_called()

    def test_missing_file_is_actionable_and_does_no_work(self, tmp_path):
        """A bad path must not trigger an ffmpeg install or a model pull."""
        with patch("gaia.audio.media.ensure_ffmpeg") as ffmpeg:
            result = Host()._transcribe_media(str(tmp_path / "nope.mp4"))

        assert result["status"] == "error"
        assert "No such file" in result["error"]
        ffmpeg.assert_not_called()

    def test_success_returns_low_confidence_spans(self, tmp_path):
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "meeting.wav"
        wav.write_bytes(b"stub")
        out = tmp_path / "meeting.txt"

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=2754.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "Whisper-Base"
            client.return_value.transcribe.return_value = _transcript()
            result = Host()._transcribe_media(str(source), output_path=str(out))

        assert result["status"] == "success"
        assert out.read_text(encoding="utf-8") == "ship diarization"
        # The transcript travels by path; inlining it truncates long meetings.
        assert "text" not in result
        assert "segments" not in result
        assert result["transcript_path"] == str(out)
        assert result["preview"] == "ship diarization"
        assert result["character_count"] == len("ship diarization")

        # The whole point: surface the shaky span, not the whole transcript.
        spans = result["low_confidence_spans"]
        assert len(spans) == 1
        assert spans[0]["text"].strip() == "diar"
        assert spans[0]["min_probability"] == pytest.approx(0.31, abs=0.01)

    def test_a_second_call_reuses_the_transcript(self, tmp_path):
        """A follow-up question must not re-transcribe the recording.

        Regression: asking "what did they say about X?" reached this tool
        again and silently redid five minutes of decode, transcription and
        diarization.
        """
        from gaia.agents.tools.audio_tools import timings_path_for

        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        out = tmp_path / "meeting.txt"
        out.write_text("already transcribed words", encoding="utf-8")
        timings_path_for(out).write_text('{"segments": []}', encoding="utf-8")

        with (
            patch("gaia.audio.media.ensure_ffmpeg") as ffmpeg,
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            result = Host()._transcribe_media(str(source), output_path=str(out))

        assert result["status"] == "success"
        assert result["reused"] is True
        assert result["transcript_path"] == str(out)
        # Nothing expensive may run on the reuse path.
        ffmpeg.assert_not_called()
        client.assert_not_called()
        # And it must point the model at the cheap way to answer.
        assert "query_documents" in result["next_step"]

    def test_a_newer_recording_is_transcribed_again(self, tmp_path):
        """Replacing the media file must invalidate the old transcript."""
        import os
        import time

        from gaia.agents.tools.audio_tools import timings_path_for

        out = tmp_path / "meeting.txt"
        out.write_text("stale", encoding="utf-8")
        timings_path_for(out).write_text('{"segments": []}', encoding="utf-8")
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        # Make the recording unambiguously newer than its transcript.
        future = time.time() + 60
        os.utime(source, (future, future))

        with patch(
            "gaia.audio.media.ensure_ffmpeg",
            side_effect=RuntimeError("ffmpeg probe reached"),
        ):
            result = Host()._transcribe_media(str(source), output_path=str(out))

        # It got past the reuse check and tried to do real work.
        assert result["status"] == "error"
        assert "ffmpeg probe reached" in result["error"]

    def test_long_meeting_result_stays_under_the_truncation_budget(self, tmp_path):
        """Regression: a 46-min meeting serialised to ~135K chars vs a 60K cap.

        The agent truncated the tool result, so roughly two-thirds of the
        meeting never reached the summariser and the summary looked complete
        while silently missing most of the content.
        """
        import json

        from gaia.llm.lemonade_client import NPU_CTX_SIZE, budget_for_ctx

        # ~46 minutes of speech, the size that triggered the bug.
        body = "This is a sentence of meeting dialogue that carries content. "
        long_text = body * 820
        words = [
            Word(word=" w", start=i, end=i + 0.1, probability=0.2 if i % 50 else 0.9)
            for i in range(700)
        ]
        transcript = Transcript(
            segments=[
                Segment(
                    start=float(i),
                    end=i + 1.0,
                    text=long_text[i * 75 : (i + 1) * 75],
                    avg_logprob=-0.2,
                    words=words,
                )
                for i in range(654)
            ],
            language="english",
            duration=2756.0,
            model="Whisper-Large-v3-Turbo",
        )

        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "meeting.wav"
        wav.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=2756.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.transcribe.return_value = transcript
            result = Host()._transcribe_media(
                str(source), output_path=str(tmp_path / "t.txt")
            )

        assert result["status"] == "success"
        size = len(json.dumps(result, ensure_ascii=False))
        # Must fit the *smallest* profile, since device may be unknown.
        npu_threshold, _ = budget_for_ctx(NPU_CTX_SIZE)
        assert size < npu_threshold, f"result is {size} chars, would be truncated"
        # The full transcript is still on disk, in one piece.
        assert (tmp_path / "t.txt").read_text(encoding="utf-8").strip()

    def test_ffmpeg_failure_surfaces_the_install_hint(self, tmp_path):
        """No silent degradation — the user must be told how to fix it."""
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")

        with patch(
            "gaia.audio.media.ensure_ffmpeg",
            side_effect=RuntimeError("ffmpeg not found. Install: winget install ..."),
        ):
            result = Host()._transcribe_media(str(source))

        assert result["status"] == "error"
        assert "winget install" in result["error"]

    def test_default_output_lands_in_the_transcript_dir(self, tmp_path):
        source = tmp_path / "standup.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "standup.wav"
        wav.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=2754.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
            patch("gaia.agents.tools.audio_tools.TRANSCRIPT_DIR", tmp_path / "out"),
        ):
            client.return_value.transcribe.return_value = _transcript()
            result = Host()._transcribe_media(str(source))

        assert Path(result["transcript_path"]) == tmp_path / "out" / "standup.txt"

    def test_a_chat_model_name_is_refused_before_decoding(self, tmp_path):
        """A model passed 'lemonade' here and learned it only after the decode."""
        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.to_wav16k_mono") as decode,
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.available_models.return_value = ["Whisper-Base"]
            result = Host()._transcribe_media(str(source), model="lemonade")

        assert result["status"] == "error"
        assert "Whisper-Base" in result["error"]
        decode.assert_not_called()

    @pytest.mark.parametrize(
        "title, plain",
        [
            ("Ana's 2026 Keynote [aB3-x_9]", "Ana-s-2026-Keynote-aB3-x-9"),
            ("Reunión – año (final) #1", "Reunión-año-final-1"),
            ("درس الحياة ｜  [8EzdQ59JJ-Q]", "درس-الحياة-8EzdQ59JJ-Q"),
            ("تعلَّم", "تعلم"),
            ("!!!", "recording"),
        ],
    )
    def test_transcript_names_survive_being_retyped(self, title, plain):
        """A quote, bracket or odd separator in a title broke every later step."""
        from gaia.agents.tools.audio_tools import _plain_stem

        assert _plain_stem(Path(f"{title}.webm")) == plain


class TestRefineTranscript:
    """Speaker turns come from pauses; the model only names them."""

    def _transcript_with_timings(self, tmp_path, segments, name="meeting"):
        from gaia.agents.tools.audio_tools import timings_path_for

        raw = tmp_path / f"{name}.txt"
        raw.write_text(" ".join(s["text"] for s in segments), encoding="utf-8")
        timings_path_for(raw).write_text(
            json.dumps({"duration": segments[-1]["end"], "segments": segments}),
            encoding="utf-8",
        )
        return raw

    @pytest.mark.parametrize("method", ["_name_known_voices", "_consolidate_speakers"])
    @pytest.mark.parametrize(
        "failure", [ConnectionError("server down"), RuntimeError("empty reply")]
    )
    def test_speaker_processing_does_not_hide_infrastructure_failure(
        self, method, failure
    ):
        host = Host()
        turns = [("A", "first"), ("B", "second"), ("C", "third")]
        with patch.object(host, "_llm_text", side_effect=failure):
            with pytest.raises(type(failure), match=str(failure)):
                getattr(host, method)(turns)

    @pytest.mark.parametrize("diarized", [True, False], ids=["diarized", "text-only"])
    def test_naming_failure_returns_error_without_output(self, tmp_path, diarized):
        """Both branches report a failed model call and leave the raw files alone."""
        from gaia.agents.tools.audio_tools import timings_path_for

        raw = self._transcript_with_timings(
            tmp_path, [{"start": 0.0, "end": 1.0, "text": "Hello"}]
        )
        timings = timings_path_for(raw)
        if diarized:
            payload = json.loads(timings.read_text(encoding="utf-8"))
            payload["speakers"] = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
            timings.write_text(json.dumps(payload), encoding="utf-8")
        raw_before, timings_before = raw.read_text(), timings.read_text()
        destination = tmp_path / "refined.md"
        host = Host()
        failing = "_name_known_voices" if diarized else "_consolidate_speakers"
        with (
            patch.object(host, "_name_turns", return_value=[("A", "Hello")]),
            patch.object(host, failing, side_effect=ConnectionError("server down")),
        ):
            result = host._refine_transcript(str(raw), str(destination))

        assert result["status"] == "error"
        assert "server down" in result["error"]
        # The error says the raw transcript survived and how to retry.
        assert str(raw) in result["error"]
        assert "refine_transcript" in result["error"]
        assert result["source_transcript"] == str(raw)
        assert not destination.exists()
        assert raw.read_text() == raw_before
        assert timings.read_text() == timings_before

    def test_missing_transcript_is_actionable(self, tmp_path):
        with patch.object(audio_tools, "TRANSCRIPT_DIR", tmp_path / "none"):
            result = Host()._refine_transcript(str(tmp_path / "nope.txt"))
        assert result["status"] == "error"
        assert "transcribe_media first" in result["error"]
        assert "do exist" not in result["error"]

    def test_a_mistyped_path_is_told_the_transcripts_that_exist(self, tmp_path):
        """Small models retype a long path; the error hands them the real one."""
        transcripts = tmp_path / "transcripts"
        transcripts.mkdir()
        real = transcripts / "Budget-Review-2026.txt"
        real.write_text("[00:00] words", encoding="utf-8")
        with patch.object(audio_tools, "TRANSCRIPT_DIR", transcripts):
            result = Host()._refine_transcript(
                str(transcripts / "Budget Review 2026.txt")
            )
        assert str(real) in result["error"]

    def test_empty_transcript_is_rejected(self, tmp_path):
        raw = tmp_path / "t.txt"
        raw.write_text("   ", encoding="utf-8")
        result = Host()._refine_transcript(str(raw))
        assert result["status"] == "error"
        assert "empty" in result["error"]

    def test_missing_timings_fails_loudly_rather_than_guessing(self, tmp_path):
        """Without pauses there is no evidence for turns — do not invent them.

        Regression: segmenting continuous prose blind produced sixty speakers
        in a four-person meeting.
        """
        raw = tmp_path / "meeting.txt"
        raw.write_text("some words that were spoken", encoding="utf-8")
        result = Host()._refine_transcript(str(raw))
        assert result["status"] == "error"
        assert "timing" in result["error"].lower()
        assert "transcribe_media" in result["error"]

    def test_turns_follow_pauses_and_text_is_verbatim(self, tmp_path):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "We should ship it."},
            {"start": 2.1, "end": 4.0, "text": "Next quarter."},
            {"start": 6.0, "end": 8.0, "text": "I disagree."},
        ]
        raw = self._transcript_with_timings(tmp_path, segments)

        with patch.object(Host, "_llm_text", return_value="1: Alice\n2: Bob"):
            result = Host()._refine_transcript(str(raw))

        assert result["status"] == "success"
        assert result["speakers"] == ["Alice", "Bob"]
        body = Path(result["refined_path"]).read_text(encoding="utf-8")
        # Two segments 0.1s apart are one turn; the 2s gap starts a new one.
        assert "Alice: We should ship it. Next quarter." in body
        assert "Bob: I disagree." in body
        # Every spoken word survives — the model never re-emits the text.
        for segment in segments:
            assert segment["text"] in body

    def test_speaker_count_is_capped(self, tmp_path):
        """The model once assigned a new speaker to every sentence."""
        from gaia.agents.tools.audio_tools import MAX_SPEAKERS

        segments = [
            {"start": i * 3.0, "end": i * 3.0 + 1.0, "text": f"Sentence {i}."}
            for i in range(30)
        ]
        raw = self._transcript_with_timings(tmp_path, segments)
        runaway = "\n".join(f"{i}: Speaker {i}" for i in range(1, 31))

        with patch.object(Host, "_llm_text", return_value=runaway):
            result = Host()._refine_transcript(str(raw))

        assert result["status"] == "success"
        assert len(result["speakers"]) <= MAX_SPEAKERS

    def test_indexes_its_own_output_and_points_at_summarization(self, tmp_path):
        """Indexing is done here, not instructed — the model kept skipping it."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        segments = [{"start": 0.0, "end": 1.0, "text": "Hello."}]
        raw = self._transcript_with_timings(tmp_path, segments)
        indexed = []

        fake = {"function": lambda path: indexed.append(path) or {"status": "success"}}
        with patch.dict(_TOOL_REGISTRY, {"index_document": fake}, clear=False):
            with patch.object(Host, "_llm_text", return_value="1: Alice"):
                result = Host()._refine_transcript(str(raw))

        assert result["indexed"] is True
        assert indexed == [result["refined_path"]]

        step = result["next_step"]
        assert "summarize_document" in step
        assert "query_documents" in step, "must warn against the chunk shortcut"
        assert result["refined_path"] in step

    def test_missing_indexer_does_not_fail_the_refinement(self, tmp_path):
        """A transcript that exists but is unindexed still beats losing it."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        segments = [{"start": 0.0, "end": 1.0, "text": "Hello."}]
        raw = self._transcript_with_timings(tmp_path, segments)

        registry = dict(_TOOL_REGISTRY)
        registry.pop("index_document", None)
        with patch("gaia.agents.base.tools._TOOL_REGISTRY", registry):
            with patch.object(Host, "_llm_text", return_value="1: Alice"):
                result = Host()._refine_transcript(str(raw))

        assert result["status"] == "success"
        assert result["indexed"] is False
        assert Path(result["refined_path"]).exists()

    def test_result_is_small_enough_to_never_truncate(self, tmp_path):
        """The refined transcript travels by path, like the raw one."""
        from gaia.llm.lemonade_client import NPU_CTX_SIZE, budget_for_ctx

        segments = [
            {"start": i * 3.0, "end": i * 3.0 + 1.0, "text": "A spoken sentence. " * 20}
            for i in range(60)
        ]
        raw = self._transcript_with_timings(tmp_path, segments)

        with patch.object(Host, "_llm_text", return_value="1: Alice"):
            result = Host()._refine_transcript(str(raw))

        size = len(json.dumps(result, ensure_ascii=False))
        threshold, _ = budget_for_ctx(NPU_CTX_SIZE)
        assert size < threshold, f"refine result is {size} chars"
        assert "text" not in result


class TestTimedTranscripts:
    """Times have to reach the files a reader, RAG and analysis actually use."""

    @staticmethod
    def _timed(tmp_path, segments, speakers=()):
        from gaia.agents.tools.audio_tools import timings_path_for

        raw = tmp_path / "talk.txt"
        raw.write_text("spoken words", encoding="utf-8")
        timings_path_for(raw).write_text(
            json.dumps({"segments": segments, "speakers": list(speakers)}),
            encoding="utf-8",
        )
        return raw

    def _refined(self, tmp_path, segments, speakers=()):
        raw = self._timed(tmp_path, segments, speakers)
        with patch.object(Host, "_llm_text", return_value="Speaker 1 -> Speaker 1"):
            return Host()._refine_transcript(str(raw))

    def test_one_presenter_is_paragraphed_and_timed_not_one_block(self, tmp_path):
        segments = [
            {"start": i * 10.0, "end": i * 10.0 + 9.0, "text": f"Point {i}."}
            for i in range(60)
        ]
        spans = [{"start": 0.0, "end": 600.0, "speaker": "Speaker 1"}]

        result = self._refined(tmp_path, segments, spans)

        body = Path(result["refined_path"]).read_text(encoding="utf-8")
        assert "[00:00] Speaker 1: Point 0." in body
        assert "[09:00] Speaker 1:" in body
        assert body.count("Speaker 1:") == 10
        assert result["turns"] == 1, "paragraphs are not new speaker turns"
        for segment in segments:
            assert segment["text"] in body

    def test_a_change_of_speaker_opens_a_paragraph_at_its_time(self, tmp_path):
        segments = [
            {"start": 70.0, "end": 71.0, "text": "Mine."},
            {"start": 72.0, "end": 73.0, "text": "Yours."},
        ]
        spans = [
            {"start": 70.0, "end": 71.5, "speaker": "Speaker 1"},
            {"start": 71.5, "end": 73.0, "speaker": "Speaker 2"},
        ]

        body = Path(self._refined(tmp_path, segments, spans)["refined_path"]).read_text(
            encoding="utf-8"
        )

        assert "[01:10] Speaker 1: Mine." in body
        assert "[01:12] Speaker 2: Yours." in body

    def test_the_refined_file_carries_timings_with_final_speakers(self, tmp_path):
        from gaia.agents.tools.audio_tools import timings_path_for

        segments = [{"start": 1.0, "end": 2.0, "text": "Hello."}]
        spans = [{"start": 0.0, "end": 3.0, "speaker": "Speaker 1"}]
        refined = Path(self._refined(tmp_path, segments, spans)["refined_path"])

        saved = json.loads(timings_path_for(refined).read_text(encoding="utf-8"))

        assert saved["segments"][0]["speaker"] == "Speaker 1"

    def test_analyze_writes_a_report_and_cleans_up_its_checkpoint(self, tmp_path):
        segments = [{"start": 5.0, "end": 9.0, "text": "Do ten lunges."}]
        refined = Path(
            self._refined(
                tmp_path, segments, [{"start": 0.0, "end": 9.0, "speaker": "Speaker 1"}]
            )["refined_path"]
        )
        reply = json.dumps(
            {
                "items": [
                    {
                        "title": "Lunges",
                        "start": "00:05",
                        "end": "00:05",
                        "fields": {"reps": "ten"},
                    }
                ],
                "summary": "Lunges.",
            }
        )

        with patch.object(Host, "_analysis_reply", return_value=reply):
            result = Host()._analyze_transcript(
                str(refined), "every exercise", ["reps"]
            )

        assert result["status"] == "success"
        report = Path(result["analysis_path"])
        assert report.name == "talk.analysis.md"
        assert "**Lunges** (00:05 → 00:09)" in report.read_text(encoding="utf-8")
        assert not list(tmp_path.glob("*.progress.json"))

    def test_each_section_is_shown_while_the_rest_are_read(self, tmp_path):
        segments = [{"start": 5.0, "end": 9.0, "text": "Do ten lunges."}]
        raw = self._timed(tmp_path, segments)
        reply = json.dumps(
            {
                "items": [
                    {"title": "Lunges", "start": "00:05", "end": "00:05", "fields": {}}
                ],
                "summary": "Lunges.",
            }
        )
        phases = []
        host = Host()
        host.console = type(
            "Console",
            (),
            {"report_phase": lambda self, phase, msg, **f: phases.append((phase, f))},
        )()

        with patch.object(Host, "_analysis_reply", return_value=reply):
            assert host._analyze_transcript(str(raw), "x", [])["status"] == "success"

        found = [fields for phase, fields in phases if phase == "recording_found"]
        assert found == [
            {
                "section": 1,
                "sections": 1,
                "start": "00:05",
                "end": "00:09",
                "summary": "Lunges.",
                "items": [{"title": "Lunges", "start": "00:05", "seconds": 5.0}],
            }
        ]

    def test_sections_use_the_agents_memory_embedder_when_it_has_one(self):
        embedded = []

        class WithMemory(Host):
            def _active_embedding_model(self):
                return "embed-model"

            def _get_embedder(self):
                return type(
                    "Embedder",
                    (),
                    {
                        "embed": lambda _s, texts, model: embedded.append(model)
                        or [[1.0]] * len(texts)
                    },
                )()

        assert Host()._transcript_embedder() is None
        assert WithMemory()._transcript_embedder()(["a", "b"]) == [[1.0], [1.0]]
        assert embedded == ["embed-model"]

    def test_analyze_failure_says_finished_sections_are_kept(self, tmp_path):
        raw = self._timed(tmp_path, [{"start": 0.0, "end": 1.0, "text": "Hi."}])

        with patch.object(
            Host, "_analysis_reply", side_effect=ConnectionError("Lemonade down")
        ):
            result = Host()._analyze_transcript(str(raw), "every exercise", [])

        assert result["status"] == "error"
        assert "Lemonade down" in result["error"]
        assert "same arguments" in result["error"]

    @pytest.mark.parametrize(
        "method,args",
        [("_analyze_transcript", ("every exercise", [])), ("_export_transcript", ())],
    )
    def test_no_timings_is_refused_rather_than_invented(self, tmp_path, method, args):
        raw = tmp_path / "talk.txt"
        raw.write_text("words", encoding="utf-8")

        result = getattr(Host(), method)(str(raw), *args)

        assert result["status"] == "error"
        assert "transcribe_media" in result["error"]

    def test_a_cut_off_reply_is_reported_as_overflow(self):
        from gaia.audio.transcript_analysis import ReplyOverflow

        choice = {"message": {"content": '{"items": ['}, "finish_reason": "length"}
        with patch.object(Host, "_llm_choice", return_value=choice):
            with pytest.raises(ReplyOverflow):
                Host()._analysis_reply("prompt")

    def test_analysis_asks_a_thinking_model_not_to_think(self):
        """DeepSeek spent 7,274 of 8,192 tokens reasoning over one section."""
        choice = {"message": {"content": "{}"}, "finish_reason": "stop"}
        host = Host()
        host.model_id = "fireworks.deepseek-v4p1-flash"

        with patch.object(Host, "_llm_choice", return_value=choice) as ask:
            host._analysis_reply("prompt")

        assert ask.call_args.kwargs["chat_template_kwargs"] == {
            "enable_thinking": False
        }

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("fireworks.deepseek-v4p1-flash", 4),
            ("Gemma-4-E4B-it-GGUF", 1),
            ("Qwen3.6-35B-A3B-GGUF", 1),
            (None, 1),
        ],
    )
    def test_only_a_cloud_model_reads_sections_at_once(self, model, expected):
        host = Host()
        host.model_id = model

        assert host._analysis_parallelism() == expected

    def test_section_replies_ask_for_valid_json(self):
        choice = {"message": {"content": "{}"}, "finish_reason": "stop"}

        with patch.object(Host, "_llm_choice", return_value=choice) as ask:
            Host()._analysis_json_reply("prompt")

        assert ask.call_args.kwargs["response_format"] == {"type": "json_object"}

    def test_analysis_sends_no_switch_a_cloud_model_may_reject(self):
        choice = {"message": {"content": "{}"}, "finish_reason": "stop"}
        host = Host()
        host.model_id = "fireworks.glm-5p3-flash"

        with patch.object(Host, "_llm_choice", return_value=choice) as ask:
            host._analysis_reply("prompt")

        assert "chat_template_kwargs" not in ask.call_args.kwargs

    @pytest.mark.parametrize(
        "chosen,name,marker",
        [
            ("srt", "talk.srt", "00:00:01,000 --> 00:00:02,000"),
            (".VTT", "talk.vtt", "WEBVTT"),
            ("txt", "talk.timestamped.txt", "[00:01] Hello."),
        ],
    )
    def test_export_formats(self, tmp_path, chosen, name, marker):
        raw = self._timed(tmp_path, [{"start": 1.0, "end": 2.0, "text": "Hello."}])

        result = Host()._export_transcript(str(raw), chosen)

        assert result["status"] == "success"
        exported = Path(result["export_path"])
        assert exported.name == name
        assert marker in exported.read_text(encoding="utf-8")

    def test_export_refuses_an_unknown_format(self, tmp_path):
        raw = self._timed(tmp_path, [{"start": 1.0, "end": 2.0, "text": "Hello."}])

        result = Host()._export_transcript(str(raw), "docx")

        assert result["status"] == "error"
        assert "srt" in result["error"]


class TestStopDuringVoiceSeparation:
    def test_it_is_a_stop_and_not_a_missing_engine(self):
        """Regression: Stop was recorded as speaker identification being unavailable."""
        from gaia.audio import diarize
        from gaia.tool_cancellation import ToolCancelled

        with (
            patch.object(diarize, "is_available", return_value=True),
            patch.object(diarize, "diarize_in_child", side_effect=ToolCancelled()),
        ):
            with pytest.raises(ToolCancelled):
                Host()._diarize_if_possible("a.wav")

    def test_chunks_survive_it_so_a_retry_is_quick(self, tmp_path):
        from gaia.agents.tools import audio_tools
        from gaia.tool_cancellation import ToolCancelled

        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")

        with (
            patch.object(audio_tools, "CHUNK_CHECKPOINT_DIR", tmp_path / "partial"),
            patch.object(Host, "_diarize_if_possible", side_effect=ToolCancelled()),
            patch("gaia.audio.media.ensure_ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=120.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=Path("a.wav")),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "m"
            client.return_value.transcribe.return_value = _transcript()
            kept = audio_tools._chunk_checkpoint_dir(source, "m", None)
            kept.mkdir(parents=True)
            with pytest.raises(ToolCancelled, match="call transcribe_media again"):
                Host()._transcribe_media(
                    str(source), output_path=str(tmp_path / "talk.txt")
                )

        assert kept.is_dir()


class TestSpeechWarning:
    """Whisper writes "Thank you." over silence; that is not a transcript."""

    @pytest.mark.parametrize(
        "text,seconds",
        [
            ("Thank you.", 20.0),
            ("so", 20.0),
            (".", 600.0),
            ("", 90.0),
            ("Hi. " * 10, 3600.0),
        ],
    )
    def test_almost_no_speech_is_called_out(self, text, seconds):
        from gaia.agents.tools.audio_tools import _speech_warning

        assert "almost no speech" in _speech_warning(text, seconds)

    @pytest.mark.parametrize(
        "text,seconds",
        [("word " * 200, 60.0), ("Yes, go ahead please.", 5.0), ("a" * 300, 300.0)],
    )
    def test_real_speech_is_left_alone(self, text, seconds):
        from gaia.agents.tools.audio_tools import _speech_warning

        assert _speech_warning(text, seconds) is None


class TestLargeRecordings:
    @staticmethod
    def _transcribed(host, source, output, **kwargs):
        """Run _transcribe_media with every external stage stubbed."""
        with (
            patch("gaia.audio.media.ensure_ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=120.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=Path("a.wav")),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "Whisper-Large-v3-Turbo"
            client.return_value.transcribe.return_value = _transcript()
            result = host._transcribe_media(
                str(source), output_path=str(output), **kwargs
            )
        return result, client

    def test_speakers_one_skips_voice_separation(self, tmp_path):
        from gaia.agents.tools.audio_tools import timings_path_for

        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")
        out = tmp_path / "talk.txt"

        with patch.object(Host, "_diarize_if_possible") as diarize:
            result, _ = self._transcribed(Host(), source, out, speakers=1)

        assert result["status"] == "success"
        diarize.assert_not_called()
        saved = json.loads(timings_path_for(out).read_text(encoding="utf-8"))
        assert saved["speakers"] == [
            {"start": 0.0, "end": 120.0, "speaker": "Speaker 1"}
        ]

    def test_a_known_speaker_count_reaches_the_diarizer(self, tmp_path):
        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")

        with patch.object(Host, "_diarize_if_possible", return_value=[]) as diarize:
            self._transcribed(Host(), source, tmp_path / "talk.txt", speakers=3)

        assert diarize.call_args.args[1] == 3

    def test_a_negative_speaker_count_is_refused(self, tmp_path):
        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")

        result = Host()._transcribe_media(str(source), speakers=-1)

        assert result["status"] == "error"

    def test_chunks_resume_and_are_removed_with_the_transcript(self, tmp_path):
        from gaia.agents.tools import audio_tools

        source = tmp_path / "talk.mp4"
        source.write_bytes(b"stub")

        with (
            patch.object(audio_tools, "CHUNK_CHECKPOINT_DIR", tmp_path / "partial"),
            patch.object(Host, "_diarize_if_possible", return_value=[]),
        ):
            expected = audio_tools._chunk_checkpoint_dir(
                source, "Whisper-Large-v3-Turbo", "en"
            )
            expected.mkdir(parents=True)
            _, client = self._transcribed(
                Host(), source, tmp_path / "talk.txt", language="en"
            )

        kwargs = client.return_value.transcribe.call_args.kwargs
        assert kwargs["checkpoint_dir"] == expected
        assert not expected.exists()

    def test_a_replaced_recording_gets_its_own_checkpoints(self, tmp_path):
        from gaia.agents.tools.audio_tools import _chunk_checkpoint_dir

        source = tmp_path / "talk.mp4"
        source.write_bytes(b"one")
        first = _chunk_checkpoint_dir(source, "m", None)
        source.write_bytes(b"other")

        assert _chunk_checkpoint_dir(source, "m", None) != first

    def test_two_recordings_with_one_name_do_not_share_a_transcript(self, tmp_path):
        """talk.mp4 here and talk.mp4 there must not answer for each other."""
        from gaia.agents.tools import audio_tools

        first = tmp_path / "a" / "talk.mp4"
        second = tmp_path / "b" / "talk.mp4"
        for path, body in ((first, b"short"), (second, b"much longer")):
            path.parent.mkdir()
            path.write_bytes(body)
        transcripts = tmp_path / "transcripts"

        with (
            patch.object(audio_tools, "TRANSCRIPT_DIR", transcripts),
            patch.object(audio_tools, "CHUNK_CHECKPOINT_DIR", transcripts / ".partial"),
            patch.object(Host, "_diarize_if_possible", return_value=[]),
            patch.object(Host, "_transcript_access_error", return_value=None),
            patch("gaia.audio.media.ensure_ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=1.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=Path("a.wav")),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.model = "m"
            client.return_value.transcribe.return_value = _transcript()
            one = Host()._transcribe_media(str(first))
            two = Host()._transcribe_media(str(second))
            again = Host()._transcribe_media(str(first))

        assert one["transcript_path"] != two["transcript_path"]
        assert "reused" not in two
        assert again["reused"] is True
        assert again["transcript_path"] == one["transcript_path"]


class TestNamingSurvivesReplyOverflow:
    """A long meeting must not lose speaker labels to the reply budget."""

    def test_overflow_splits_the_batch_instead_of_failing(self):
        """Regression: a 46-min transcript failed refinement outright.

        finish_reason='length' — the model spent its whole budget reasoning
        and emitted no content, so the stage died and the summary came back
        with no speakers at all.
        """
        batch = [
            [{"start": float(i), "end": i + 0.5, "text": f"Sentence {i}."}]
            for i in range(16)
        ]
        seen = []

        def fake(self, prompt):
            # Count the numbered turn lines, not the word "[pause" — the
            # instructions mention it too.
            n = len(re.findall(r"^\d+\. ", prompt, re.MULTILINE))
            seen.append(n)
            if n > 4:
                raise RuntimeError("no transcript text (finish_reason='length')")
            return "\n".join(f"{i}: Speaker A" for i in range(1, n + 1))

        with patch.object(Host, "_llm_text", fake):
            named = Host()._name_turns(batch, [])

        assert len(named) == 16, "every turn must still be labelled"
        assert max(seen) > 4 and min(seen) <= 4, "it should have split down"

    def test_a_small_batch_that_overflows_still_raises(self):
        """Splitting forever would hide a real problem."""
        batch = [[{"start": 0.0, "end": 1.0, "text": "One."}]]

        def always_overflow(self, prompt):
            raise RuntimeError("no transcript text (finish_reason='length')")

        with patch.object(Host, "_llm_text", always_overflow):
            with pytest.raises(RuntimeError, match="finish_reason"):
                Host()._name_turns(batch, [])


class TestDegradedSpeakerIdIsVisible:
    """Silently dropping speaker labels reads as "there was one speaker"."""

    def test_a_failed_diarizer_is_reported_on_the_result(self, tmp_path):
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "meeting.wav"
        wav.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=120.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
            patch.object(
                Host, "_diarize_if_possible", lambda self, w, n: self._fail_diar()
            ),
        ):
            Host._fail_diar = lambda self: (
                setattr(self, "_diarization_error", "sherpa-onnx is not installed."),
                [],
            )[1]
            client.return_value.transcribe.return_value = _transcript()
            result = Host()._transcribe_media(
                str(source), output_path=str(tmp_path / "t.txt")
            )

        assert result["status"] == "success"
        note = result.get("speaker_identification", "")
        assert "no speaker labels" in note
        assert "sherpa-onnx" in note

    def test_a_working_diarizer_adds_no_note(self, tmp_path):
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "meeting.wav"
        wav.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=120.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
            patch.object(Host, "_diarize_if_possible", lambda self, w, n: []),
        ):
            client.return_value.transcribe.return_value = _transcript()
            result = Host()._transcribe_media(
                str(source), output_path=str(tmp_path / "t.txt")
            )

        assert "speaker_identification" not in result


class TestTranscriptionStatus:
    def test_reports_not_ready_without_ffmpeg_but_does_not_install(self):
        with (
            patch("gaia.audio.media.find_ffmpeg", return_value=None),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.available_models.return_value = ["Whisper-Base"]
            result = Host()._transcription_status()

        assert result["ready"] is False
        assert "not installed" in result["ffmpeg"]

    def test_unreachable_lemonade_is_reported_not_raised(self):
        with (
            patch("gaia.audio.media.find_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.available_models.side_effect = ConnectionError(
                "not reachable at http://localhost:13305"
            )
            result = Host()._transcription_status()

        assert result["ready"] is False
        assert result["lemonade"] == "unreachable"
        assert "13305" in result["lemonade_error"]

    def test_ready_when_both_present(self):
        with (
            patch("gaia.audio.media.find_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.available_models.return_value = ["Whisper-Base"]
            result = Host()._transcription_status()

        assert result["ready"] is True
        assert result["available_models"] == ["Whisper-Base"]


def _repo_profiles():
    """Load this checkout's chat profiles by path.

    ``import gaia_agent`` can resolve to a different checkout when the hub
    packages are editable-installed elsewhere, which would silently test the
    wrong source.
    """
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "hub/agents/gaia/python/gaia_agent/profiles.py"
    )
    if not path.is_file():
        pytest.skip(f"chat profiles not present at {path}")
    name = "_repo_chat_profiles"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves annotations via sys.modules[cls.__module__].
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


class TestFlagshipWiring:
    """The tool is useless if the flagship never registers it."""

    def test_full_profile_registers_audio_tools(self):
        profiles = _repo_profiles()
        assert "media_transcribe" in profiles.get_profile_spec("full").tool_groups
        assert profiles.TOOL_GROUP_REGISTRARS["media_transcribe"] == (
            "register_audio_tools",
        )

    def test_audio_registrar_exists_on_the_mixin(self):
        """Every registrar the profile names must actually be callable."""
        profiles = _repo_profiles()
        for group in profiles.get_profile_spec("full").tool_groups:
            for registrar in profiles.TOOL_GROUP_REGISTRARS[group]:
                if registrar == "register_audio_tools":
                    assert callable(getattr(AudioToolsMixin, registrar))
