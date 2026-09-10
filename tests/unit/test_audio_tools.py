# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for AudioToolsMixin — the agent-facing transcription surface."""

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
    """The correction + attribution stage, as a tool the model cannot skip."""

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

    def test_writes_speaker_labelled_file_and_points_at_summarization(self, tmp_path):
        raw = tmp_path / "meeting.txt"
        raw.write_text("alice here we should ship it bob i disagree", encoding="utf-8")

        with patch.object(
            Host,
            "_llm_text",
            return_value="Alice: Alice here, we should ship it.\nBob: I disagree.",
        ):
            result = Host()._refine_transcript(str(raw))

        assert result["status"] == "success"
        assert result["speakers"] == ["Alice", "Bob"]
        refined = Path(result["refined_path"])
        assert refined.exists()
        body = refined.read_text(encoding="utf-8")
        assert "Alice:" in body and "Bob:" in body
        # Must steer to the corrected file, and index before summarizing.
        assert "index_document" in result["next_step"]
        assert "summarize_document" in result["next_step"]
        assert str(refined) in result["next_step"]

    def test_long_transcript_is_refined_in_sections(self, tmp_path):
        from gaia.agents.tools.audio_tools import REFINE_SECTION_CHARS

        raw = tmp_path / "long.txt"
        raw.write_text("This is a spoken sentence. " * 2000, encoding="utf-8")

        calls = []

        def fake(self, prompt):
            calls.append(prompt)
            return "Speaker A: content."

        with patch.object(Host, "_llm_text", fake):
            result = Host()._refine_transcript(str(raw))

        assert result["status"] == "success"
        assert result["sections"] > 1, "a long transcript must be sectioned"
        assert len(calls) == result["sections"]
        for prompt in calls:
            assert len(prompt) < REFINE_SECTION_CHARS * 2

    def test_result_is_small_enough_to_never_truncate(self, tmp_path):
        """The refined transcript travels by path, like the raw one."""
        import json

        from gaia.llm.lemonade_client import NPU_CTX_SIZE, budget_for_ctx

        raw = tmp_path / "long.txt"
        raw.write_text("This is a spoken sentence. " * 2000, encoding="utf-8")

        with patch.object(Host, "_llm_text", return_value="Speaker A: " + "x" * 4000):
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
