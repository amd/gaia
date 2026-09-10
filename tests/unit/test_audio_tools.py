# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for AudioToolsMixin — the agent-facing transcription surface."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.registry import KNOWN_TOOLS
from gaia.agents.tools.audio_tools import AudioToolsMixin
from gaia.audio.lemonade_asr import Segment, Transcript, Word


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

        # One tool only: a status companion caused a 4-call loop that never transcribed.
        assert set(registered) == {"transcribe_media", "refine_transcript"}

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


class TestTranscribeMedia:
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

    def test_scratch_wav_is_removed_even_on_failure(self, tmp_path):
        """The decoded WAV is large; it must not survive a failed transcription."""
        source = tmp_path / "meeting.mp4"
        source.write_bytes(b"stub")
        wav = tmp_path / "scratch.wav"
        wav.write_bytes(b"stub")

        with (
            patch("gaia.audio.media.ensure_ffmpeg", return_value="ffmpeg"),
            patch("gaia.audio.media.probe_duration", return_value=2754.0),
            patch("gaia.audio.media.to_wav16k_mono", return_value=wav),
            patch("gaia.audio.lemonade_asr.LemonadeASRClient") as client,
        ):
            client.return_value.transcribe.side_effect = ConnectionError("server down")
            result = Host()._transcribe_media(str(source))

        assert result["status"] == "error"
        assert "server down" in result["error"]
        assert not wav.exists()

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

    def test_missing_transcript_is_actionable(self, tmp_path):
        result = Host()._refine_transcript(str(tmp_path / "nope.txt"))
        assert result["status"] == "error"
        assert "transcribe_media first" in result["error"]

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

    ``import gaia_agent_chat`` can resolve to a different checkout when the hub
    packages are editable-installed elsewhere, which would silently test the
    wrong source.
    """
    import importlib.util

    path = (
        Path(__file__).resolve().parents[2]
        / "hub/agents/chat/python/gaia_agent_chat/profiles.py"
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
