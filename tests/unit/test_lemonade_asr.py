# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for gaia.audio.lemonade_asr.

These tests intercept ``requests.Session.send``, which receives the fully
``PreparedRequest`` — the real wire bytes. Asserting against that proves the
outgoing call would actually be *accepted* by Lemonade (multipart body, a
``file`` part carrying WAV bytes, a supported ``response_format``), not merely
that some client method was invoked. See CLAUDE.md, "Mocks prove 'we called
it,' not 'the call is valid'".

``TestRealServer`` closes the loop with one live round-trip.
"""

import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import pytest
import requests

from gaia.audio import media
from gaia.audio.lemonade_asr import (
    DEFAULT_ASR_MODEL,
    RESPONSE_FORMATS,
    TRANSCRIPTION_RESPONSE_FORMAT,
    LemonadeASRClient,
    LemonadeASRError,
    Segment,
    Transcript,
    Word,
    is_flm_model,
)

# Captured verbatim from Lemonade v11.5.0 (Whisper-Base) on a TTS probe saying
# "...ship the transcription feature with speaker diarization next week." The
# mis-heard "diarization" is the low-confidence run the correction pass targets.
VERBOSE_JSON = {
    "detected_language": "english",
    "detected_language_probability": 0.9981269240379333,
    "duration": 6.828249931335449,
    "language": "english",
    "task": "transcribe",
    "text": " Alice here. I think we should ship the transcription\n"
    " feature with speaker diarization next week.\n",
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.52,
            "text": " Alice here. I think we should ship the transcription",
            "avg_logprob": -0.07919042557477951,
            "no_speech_prob": 1.9e-05,
            "temperature": 0.0,
            "tokens": [50364, 21182],
            "words": [
                {"word": " Alice", "start": 0.12, "end": 0.69, "probability": 0.873},
                {"word": " here", "start": 0.69, "end": 1.02, "probability": 0.978},
                {"word": ".", "start": 1.02, "end": 1.2, "probability": 0.779},
                {"word": " I", "start": 1.2, "end": 1.36, "probability": 0.683},
                {"word": " think", "start": 1.36, "end": 1.6, "probability": 0.999},
                {"word": " we", "start": 1.6, "end": 1.74, "probability": 0.999},
                {"word": " should", "start": 1.74, "end": 1.96, "probability": 0.999},
                {"word": " ship", "start": 1.96, "end": 2.24, "probability": 0.938},
                {"word": " the", "start": 2.24, "end": 2.42, "probability": 0.996},
                {
                    "word": " transcription",
                    "start": 2.42,
                    "end": 3.52,
                    "probability": 0.988,
                },
            ],
        },
        {
            "id": 1,
            "start": 3.52,
            "end": 6.0,
            "text": " feature with speaker diarization next week.",
            "avg_logprob": -0.30469265580177307,
            "no_speech_prob": 3.1e-05,
            "temperature": 0.0,
            "tokens": [50564, 4111],
            "words": [
                {"word": " feature", "start": 3.52, "end": 3.9, "probability": 0.982},
                {"word": " with", "start": 3.9, "end": 4.12, "probability": 0.990},
                {"word": " speaker", "start": 4.12, "end": 4.5, "probability": 0.580},
                {"word": " di", "start": 4.5, "end": 4.66, "probability": 0.419},
                {"word": "ar", "start": 4.66, "end": 4.78, "probability": 0.307},
                {"word": "ization", "start": 4.78, "end": 5.2, "probability": 0.712},
                {"word": " next", "start": 5.2, "end": 5.5, "probability": 0.996},
                {"word": " week", "start": 5.5, "end": 5.8, "probability": 0.993},
                {"word": ".", "start": 5.8, "end": 6.0, "probability": 0.932},
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# HTTP interception helpers
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for the pieces of requests.Response the client reads."""

    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("No JSON object could be decoded")
        return self._body


class Recorder:
    """Captures every PreparedRequest the client actually puts on the wire."""

    def __init__(self, monkeypatch, responses):
        self.responses = list(responses)
        self.requests = []

        def fake_send(_session, request, **_kwargs):
            self.requests.append(request)
            if not self.responses:
                raise AssertionError(
                    f"Unexpected extra request to {request.url} - no response queued"
                )
            return self.responses.pop(0)

        monkeypatch.setattr(requests.sessions.Session, "send", fake_send)

    @property
    def last(self):
        assert self.requests, "no request was sent"
        return self.requests[-1]


def parse_multipart(request):
    """Decode a PreparedRequest's multipart body into (fields, files).

    ``files[name] == (filename, raw_bytes, content_type)``.
    """
    content_type = request.headers["Content-Type"]
    assert content_type.startswith(
        "multipart/form-data; boundary="
    ), f"not a multipart request: {content_type}"
    boundary = content_type.split("boundary=", 1)[1].encode()

    fields, files = {}, {}
    for part in request.body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        name_match = re.search(rb'name="([^"]+)"', head)
        if not name_match:
            continue
        name = name_match.group(1).decode()
        filename_match = re.search(rb'filename="([^"]+)"', head)
        if filename_match:
            ctype_match = re.search(rb"Content-Type: (\S+)", head)
            files[name] = (
                filename_match.group(1).decode(),
                payload,
                ctype_match.group(1).decode() if ctype_match else None,
            )
        else:
            fields[name] = payload.decode()
    return fields, files


@pytest.fixture
def wav_file(tmp_path):
    """A real, if silent, 16 kHz mono WAV — valid RIFF bytes, not a stub."""
    path = tmp_path / "probe.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000)
    return path


@pytest.fixture
def client():
    return LemonadeASRClient(base_url="http://localhost:13305")


# ---------------------------------------------------------------------------
# Outgoing request shape
# ---------------------------------------------------------------------------


class TestOutgoingRequestShape:
    def test_posts_multipart_form_data(self, monkeypatch, client, wav_file):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        request = recorder.last
        assert request.method == "POST"
        assert request.headers["Content-Type"].startswith(
            "multipart/form-data; boundary="
        )
        assert isinstance(request.body, bytes)

    def test_file_part_carries_the_actual_wav_bytes(
        self, monkeypatch, client, wav_file
    ):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        _fields, files = parse_multipart(recorder.last)
        assert "file" in files, f"no 'file' part in multipart body: {list(files)}"
        filename, payload, content_type = files["file"]
        assert filename == "probe.wav"
        assert content_type == "audio/wav"
        assert payload == wav_file.read_bytes()
        assert payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"

    def test_response_format_is_a_supported_value(self, monkeypatch, client, wav_file):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        fields, _files = parse_multipart(recorder.last)
        assert fields["response_format"] in RESPONSE_FORMATS
        # verbose_json is the only format carrying segments/words/confidence.
        assert fields["response_format"] == TRANSCRIPTION_RESPONSE_FORMAT == "verbose_json"

    def test_model_field_matches_configured_model(self, monkeypatch, wav_file):
        client = LemonadeASRClient(base_url="http://localhost:13305", model="Whisper-Base")
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        fields, _files = parse_multipart(recorder.last)
        assert fields["model"] == "Whisper-Base"

    def test_language_omitted_when_not_requested(self, monkeypatch, client, wav_file):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        fields, _files = parse_multipart(recorder.last)
        assert "language" not in fields

    def test_language_sent_as_iso_639_1_when_requested(
        self, monkeypatch, client, wav_file
    ):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file, language="en")

        fields, _files = parse_multipart(recorder.last)
        assert fields["language"] == "en"

    def test_url_is_the_documented_endpoint(self, monkeypatch, client, wav_file):
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        assert recorder.last.url == (
            "http://localhost:13305/api/v1/audio/transcriptions"
        )

    def test_no_auth_header_when_no_key_configured(
        self, monkeypatch, client, wav_file
    ):
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        client = LemonadeASRClient(base_url="http://localhost:13305")
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        assert "Authorization" not in recorder.last.headers

    def test_bearer_header_when_key_configured(self, monkeypatch, wav_file):
        monkeypatch.setenv("LEMONADE_API_KEY", "sk-test-123")
        client = LemonadeASRClient(base_url="http://localhost:13305")
        recorder = Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        client.transcribe(wav_file)

        assert recorder.last.headers["Authorization"] == "Bearer sk-test-123"


# ---------------------------------------------------------------------------
# FLM guard
# ---------------------------------------------------------------------------


class TestFlmGuard:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("whisper-v3-turbo-FLM", True),
            ("whisper-v3-turbo-flm", True),
            ("embed-gemma-300m-FLM", True),
            ("Whisper-Large-v3-Turbo", False),
            ("Whisper-Base", False),
            # No false positive on a name that merely contains the letters.
            ("Whisper-Filmstrip", False),
            ("", False),
        ],
    )
    def test_is_flm_model(self, model, expected):
        assert is_flm_model(model) is expected

    def test_flm_plus_timestamps_is_rejected_before_any_http(
        self, monkeypatch, wav_file
    ):
        client = LemonadeASRClient(
            base_url="http://localhost:13305", model="whisper-v3-turbo-FLM"
        )
        recorder = Recorder(monkeypatch, [])

        with pytest.raises(ValueError) as excinfo:
            client.transcribe(wav_file)

        message = str(excinfo.value)
        assert "whisper-v3-turbo-FLM" in message
        assert "no segment or word timestamps" in message
        assert DEFAULT_ASR_MODEL in message
        assert "require_timestamps=False" in message
        assert recorder.requests == [], "must fail before spending a request"

    def test_flm_allowed_when_caller_waives_timestamps(
        self, monkeypatch, wav_file
    ):
        client = LemonadeASRClient(
            base_url="http://localhost:13305", model="whisper-v3-turbo-FLM"
        )
        Recorder(
            monkeypatch,
            [FakeResponse(body={"text": " hello there", "language": "english"})],
        )
        transcript = client.transcribe(wav_file, require_timestamps=False)

        assert transcript.segments == []
        assert transcript.text == "hello there"

    def test_flm_transcript_refuses_to_fake_confidence_spans(self):
        transcript = Transcript(
            segments=[Segment(start=0.0, end=1.0, text=" hi", avg_logprob=0.0)],
            language="english",
            duration=1.0,
            model="whisper-v3-turbo-FLM",
        )
        with pytest.raises(ValueError, match="no word-level confidence"):
            transcript.low_confidence_spans()


# ---------------------------------------------------------------------------
# Base URL resolution (must match lemonade_client)
# ---------------------------------------------------------------------------


class TestBaseUrlResolution:
    def test_defaults_to_lemonade_client_config(self, monkeypatch):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        client = LemonadeASRClient()
        assert client.base_url == "http://localhost:13305/api/v1"

    def test_honours_lemonade_base_url_env(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-box:9000")
        client = LemonadeASRClient()
        assert client.base_url == "http://gpu-box:9000/api/v1"
        assert client.transcriptions_url == (
            "http://gpu-box:9000/api/v1/audio/transcriptions"
        )

    def test_port_is_not_hardcoded(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:9999/api/v1")
        assert "9999" in LemonadeASRClient().base_url

    @pytest.mark.parametrize(
        "given",
        [
            "http://host:1234",
            "http://host:1234/",
            "http://host:1234/api/v1",
            "http://host:1234/api/v1/",
        ],
    )
    def test_api_suffix_is_normalized(self, given):
        assert LemonadeASRClient(base_url=given).base_url == "http://host:1234/api/v1"

    def test_empty_model_is_rejected(self):
        with pytest.raises(ValueError, match="transcription model id"):
            LemonadeASRClient(model="")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParsing:
    @pytest.fixture
    def transcript(self, monkeypatch, client, wav_file):
        Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        return client.transcribe(wav_file)

    def test_segments_and_words(self, transcript):
        assert len(transcript.segments) == 2
        first = transcript.segments[0]
        assert first.start == 0.0
        assert first.end == 3.52
        assert first.avg_logprob == pytest.approx(-0.0791904, rel=1e-4)
        assert first.speaker is None
        assert len(first.words) == 10
        assert first.words[0] == Word(
            word=" Alice", start=0.12, end=0.69, probability=0.873
        )

    def test_metadata(self, transcript):
        assert transcript.language == "english"
        assert transcript.duration == pytest.approx(6.828, abs=1e-3)
        assert transcript.model == DEFAULT_ASR_MODEL

    def test_text_joins_segments(self, transcript):
        assert transcript.text == (
            "Alice here. I think we should ship the transcription feature "
            "with speaker diarization next week."
        )

    def test_words_property_flattens_in_order(self, transcript):
        words = transcript.words
        assert len(words) == 19
        assert [w.word for w in words[:2]] == [" Alice", " here"]
        assert words[-1].word == "."

    def test_non_list_segments_raises(self, monkeypatch, client, wav_file):
        Recorder(monkeypatch, [FakeResponse(body={"segments": "oops"})])
        with pytest.raises(LemonadeASRError, match="non-list 'segments'"):
            client.transcribe(wav_file)

    def test_non_json_body_raises(self, monkeypatch, client, wav_file):
        Recorder(monkeypatch, [FakeResponse(body=None, text="<html>502</html>")])
        with pytest.raises(LemonadeASRError, match="non-JSON body"):
            client.transcribe(wav_file)


# ---------------------------------------------------------------------------
# Low-confidence spans
# ---------------------------------------------------------------------------


class TestLowConfidenceSpans:
    @pytest.fixture
    def transcript(self, monkeypatch, client, wav_file):
        Recorder(monkeypatch, [FakeResponse(body=VERBOSE_JSON)])
        return client.transcribe(wav_file)

    def test_isolates_the_mis_heard_word(self, transcript):
        """The design unlock: the mis-hearing is exactly the low-confidence run.

        Whisper rendered "diarization" as " di"/"ar"/"ization"; the first two
        tokens are the least confident in the file (0.419, 0.307).
        """
        spans = transcript.low_confidence_spans(threshold=0.5)

        assert len(spans) == 1
        span = spans[0]
        assert span.text == "diar"
        assert span.min_probability == pytest.approx(0.307)
        assert span.start == 4.5
        assert span.end == 4.78
        assert span.segment_index == 1

    def test_adjacent_low_words_merge_into_one_run(self, transcript):
        """At the default threshold " speaker" (0.580) joins the same run."""
        spans = transcript.low_confidence_spans()

        assert len(spans) == 1
        assert spans[0].text == "speaker diar"
        assert spans[0].start == 4.12
        assert [word.word for word in spans[0].words] == [" speaker", " di", "ar"]

    def test_context_surrounds_the_span(self, transcript):
        span = transcript.low_confidence_spans(threshold=0.5)[0]
        assert "speaker" in span.context
        assert "ization" in span.context
        assert span.text in span.context
        assert len(span.context) > len(span.text)

    def test_context_window_is_respected(self, transcript):
        narrow = transcript.low_confidence_spans(threshold=0.5, context_words=1)[0]
        wide = transcript.low_confidence_spans(threshold=0.5, context_words=6)[0]
        assert narrow.context == "speaker diarization"
        assert len(wide.context) > len(narrow.context)

    def test_zero_context_returns_just_the_run(self, transcript):
        span = transcript.low_confidence_spans(threshold=0.5, context_words=0)[0]
        assert span.context == span.text

    def test_higher_threshold_catches_more_runs(self, transcript):
        spans = transcript.low_confidence_spans(threshold=0.8)

        assert [span.text for span in spans] == [". I", "speaker diarization"]
        assert len(spans) > len(transcript.low_confidence_spans(threshold=0.5))

    def test_confident_transcript_has_no_spans(self, transcript):
        assert transcript.low_confidence_spans(threshold=0.05) == []

    def test_runs_do_not_span_segments(self):
        """Each run is anchored to one segment so context stays local."""

        def low(text, start):
            return Word(word=text, start=start, end=start + 0.1, probability=0.1)

        transcript = Transcript(
            segments=[
                Segment(0.0, 1.0, " a", -0.1, [low(" a", 0.0)]),
                Segment(1.0, 2.0, " b", -0.1, [low(" b", 1.0)]),
            ],
            language="english",
            duration=2.0,
            model=DEFAULT_ASR_MODEL,
        )
        spans = transcript.low_confidence_spans()
        assert [span.segment_index for span in spans] == [0, 1]
        assert [span.text for span in spans] == ["a", "b"]

    def test_empty_transcript_has_no_spans(self):
        transcript = Transcript(
            segments=[], language="", duration=0.0, model=DEFAULT_ASR_MODEL
        )
        assert transcript.low_confidence_spans() == []

    @pytest.mark.parametrize("threshold", [-0.1, 1.5])
    def test_out_of_range_threshold_raises(self, transcript, threshold):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            transcript.low_confidence_spans(threshold=threshold)

    def test_negative_context_raises(self, transcript):
        with pytest.raises(ValueError, match="context_words"):
            transcript.low_confidence_spans(context_words=-1)


# ---------------------------------------------------------------------------
# Errors — loud, actionable, no leaks
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_file(self, client, tmp_path):
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            client.transcribe(tmp_path / "gone.wav")

    def test_non_wav_input_points_at_the_converter(self, client, tmp_path):
        mp4 = tmp_path / "meeting.mp4"
        mp4.write_bytes(b"\x00")
        with pytest.raises(ValueError) as excinfo:
            client.transcribe(mp4)
        assert "WAV only" in str(excinfo.value)
        assert "to_wav16k_mono" in str(excinfo.value)

    def test_server_error_message_is_surfaced(self, monkeypatch, client, wav_file):
        Recorder(
            monkeypatch,
            [
                FakeResponse(
                    status_code=404,
                    body={
                        "error": {
                            "code": "model_not_found",
                            "message": "Model 'No-Such-Model' was not found.",
                        }
                    },
                )
            ],
        )
        with pytest.raises(LemonadeASRError) as excinfo:
            client.transcribe(wav_file)
        assert "404" in str(excinfo.value)
        assert "Model 'No-Such-Model' was not found." in str(excinfo.value)

    def test_processing_error_is_surfaced(self, monkeypatch, client, wav_file):
        Recorder(
            monkeypatch,
            [
                FakeResponse(
                    status_code=500,
                    body={
                        "error": {
                            "message": "Transcription failed: whisper-server "
                            "returned status 400: Invalid request",
                            "type": "audio_processing_error",
                        }
                    },
                )
            ],
        )
        with pytest.raises(LemonadeASRError, match="Transcription failed"):
            client.transcribe(wav_file)

    def test_401_does_not_echo_the_response_body(self, monkeypatch, client, wav_file):
        Recorder(
            monkeypatch,
            [
                FakeResponse(
                    status_code=401,
                    body={"error": {"message": "Bearer sk-leaked-secret rejected"}},
                )
            ],
        )
        with pytest.raises(LemonadeASRError) as excinfo:
            client.transcribe(wav_file)
        assert "sk-leaked-secret" not in str(excinfo.value)
        assert "LEMONADE_API_KEY" in str(excinfo.value)

    def test_unreachable_server_names_url_and_next_step(
        self, monkeypatch, client, wav_file
    ):
        def refuse(_session, _request, **_kwargs):
            raise requests.ConnectionError("connection refused")

        monkeypatch.setattr(requests.sessions.Session, "send", refuse)

        with pytest.raises(ConnectionError) as excinfo:
            client.transcribe(wav_file)
        message = str(excinfo.value)
        assert "http://localhost:13305/api/v1" in message
        assert "lemonade-server serve" in message
        assert "gaia init" in message

    def test_timeout_suggests_a_bigger_timeout(self, monkeypatch, client, wav_file):
        def stall(_session, _request, **_kwargs):
            raise requests.Timeout("read timed out")

        monkeypatch.setattr(requests.sessions.Session, "send", stall)

        with pytest.raises(LemonadeASRError, match="timed out"):
            client.transcribe(wav_file)


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


class TestAvailableModels:
    MODEL_LIST = {
        "data": [
            {"id": "Whisper-Base", "labels": ["transcription", "realtime-transcription"]},
            {"id": "Gemma-4-E4B-it-GGUF", "labels": ["tool-calling", "vision"]},
            {"id": "SDXL-Turbo", "labels": ["image"]},
            {"id": "whisper-v3-turbo-FLM", "labels": ["audio", "transcription"]},
            {"id": "embed-gemma-300m-FLM", "labels": ["embeddings"]},
        ]
    }

    def test_returns_only_transcription_models(self, monkeypatch, client):
        Recorder(monkeypatch, [FakeResponse(body=self.MODEL_LIST)])
        assert client.available_models() == [
            "Whisper-Base",
            "whisper-v3-turbo-FLM",
        ]

    def test_queries_the_models_endpoint(self, monkeypatch, client):
        recorder = Recorder(monkeypatch, [FakeResponse(body=self.MODEL_LIST)])
        client.available_models()
        assert recorder.last.method == "GET"
        assert recorder.last.url == "http://localhost:13305/api/v1/models"

    def test_malformed_payload_raises(self, monkeypatch, client):
        Recorder(monkeypatch, [FakeResponse(body={"data": "nope"})])
        with pytest.raises(LemonadeASRError, match="expected a 'data' list"):
            client.available_models()


# ---------------------------------------------------------------------------
# Live server
# ---------------------------------------------------------------------------


def _synthesize_wav(tmp_path: Path) -> Path:
    """Generate real speech at runtime (no committed binaries) via Windows TTS."""
    if sys.platform != "win32":
        pytest.skip("speech synthesis fixture requires Windows TTS")
    if media.find_ffmpeg() is None:
        pytest.skip("ffmpeg not installed - cannot build the 16 kHz probe")

    raw = tmp_path / "raw.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{raw}'); "
        "$s.Speak('Alice here. I think we should ship the transcription "
        "feature with speaker diarization next week.'); "
        "$s.Dispose()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not raw.exists():
        pytest.skip(f"Windows TTS unavailable: {result.stderr.strip()[:200]}")

    return media.to_wav16k_mono(raw, dest=tmp_path / "probe.wav")


@pytest.mark.integration
@pytest.mark.allow_network
class TestRealServer:
    """One live round-trip against the running Lemonade Server."""

    def test_real_transcription_round_trip(self, require_lemonade, tmp_path):
        client = LemonadeASRClient()
        downloaded = client.available_models()
        whispers = [name for name in downloaded if name.lower().startswith("whisper")]
        if not whispers:
            pytest.skip(
                "no Whisper model downloaded - run "
                f"`lemonade-server pull {DEFAULT_ASR_MODEL}`"
            )
        model = DEFAULT_ASR_MODEL if DEFAULT_ASR_MODEL in whispers else whispers[0]

        wav = _synthesize_wav(tmp_path)
        transcript = LemonadeASRClient(model=model).transcribe(wav, language="en")

        assert "transcription" in transcript.text.lower()
        assert transcript.duration > 0
        assert transcript.language
        assert transcript.segments, "verbose_json must return segments"

        words = transcript.words
        assert words, "whispercpp must return word-level timestamps"
        assert all(word.end >= word.start for word in words)
        assert words[-1].end <= transcript.duration + 1.0
        assert all(0.0 <= word.probability <= 1.0 for word in words)

        # Every span the correction pass would see is a real, bounded slice.
        for span in transcript.low_confidence_spans():
            assert span.text
            assert span.text in span.context
            assert span.min_probability < 0.6
