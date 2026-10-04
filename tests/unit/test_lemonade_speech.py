# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Voice I/O for ``gaia talk`` through Lemonade: Kokoro TTS and live Whisper ASR.

The HTTP boundary is stubbed at ``requests.Session.send`` so every test asserts
the request Lemonade would actually receive — URL, JSON body, multipart fields —
not just that a method was called. ``TestRealServer`` repeats the round trip
against a running Lemonade Server.
"""

import io
import json
import queue
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import requests

from gaia.audio.lemonade_asr import LemonadeASRClient, LemonadeASRError
from gaia.audio.lemonade_tts import (
    DEFAULT_TTS_MODEL,
    KOKORO_VOICES,
    TTS_SAMPLE_RATE,
    LemonadeTTSClient,
    LemonadeTTSError,
)

BASE = "http://lemonade.test:13305/api/v1"


@pytest.fixture(autouse=True)
def _no_embedded_server(request, monkeypatch, tmp_path_factory):
    """Keep a developer's own embedded Lemonade out of the stubbed tests."""
    if request.node.get_closest_marker("integration"):
        return
    from gaia.llm import lemonade_client as lc

    monkeypatch.setattr(
        lc, "EMBEDDED_LEMONADE_STATE", tmp_path_factory.mktemp("none") / "state.json"
    )
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)


class FakeResponse:
    def __init__(self, status_code=200, body=None, content=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.content = content
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else content.decode("latin1")

    def json(self):
        if self._body is None:
            raise ValueError("No JSON object could be decoded")
        return self._body


class Wire:
    """Captures every PreparedRequest the clients put on the wire."""

    def __init__(self, monkeypatch, responses):
        self.responses = list(responses)
        self.requests = []

        def fake_send(_session, request, **_kwargs):
            self.requests.append(request)
            if not self.responses:
                raise AssertionError(f"unexpected request to {request.url}")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(requests.sessions.Session, "send", fake_send)

    def json_body(self, index=-1):
        return json.loads(self.requests[index].body)


def pcm_response(samples, rate=TTS_SAMPLE_RATE):
    pcm = (np.asarray(samples, dtype=np.float32) * 32767).astype("<i2").tobytes()
    return FakeResponse(
        content=pcm,
        headers={"Content-Type": f"audio/l16;rate={rate};endianness=little-endian"},
    )


def catalog(*entries):
    return FakeResponse(body={"object": "list", "data": list(entries)})


# --------------------------------------------------------------------------- TTS


class TestTTSRequestShape:
    def test_posts_documented_body_to_speech_endpoint(self, monkeypatch):
        wire = Wire(monkeypatch, [pcm_response(np.zeros(240))])
        LemonadeTTSClient(base_url=BASE).synthesize("Hello there.", voice="am_michael")

        request = wire.requests[0]
        assert request.method == "POST"
        assert request.url == f"{BASE}/audio/speech"
        assert wire.json_body() == {
            "model": DEFAULT_TTS_MODEL,
            "input": "Hello there.",
            "voice": "am_michael",
            "speed": 1.0,
            "response_format": "pcm",
        }

    def test_decodes_l16_pcm_at_the_advertised_rate(self, monkeypatch):
        tone = np.sin(np.linspace(0, 20, 2400)) * 0.5
        Wire(monkeypatch, [pcm_response(tone, rate=24000)])
        samples, rate = LemonadeTTSClient(base_url=BASE).synthesize("Hi.")
        assert rate == 24000
        assert samples.dtype == np.float32
        np.testing.assert_allclose(samples, tone, atol=1e-3)

    def test_non_pcm_body_is_refused(self, monkeypatch):
        Wire(
            monkeypatch,
            [FakeResponse(content=b"ID3...", headers={"Content-Type": "audio/mpeg"})],
        )
        with pytest.raises(LemonadeTTSError, match="audio/l16"):
            LemonadeTTSClient(base_url=BASE).synthesize("Hi.")

    def test_server_error_is_surfaced(self, monkeypatch):
        Wire(
            monkeypatch,
            [FakeResponse(500, body={"error": {"message": "backend returned 500"}})],
        )
        with pytest.raises(LemonadeTTSError, match="500.*backend returned 500"):
            LemonadeTTSClient(base_url=BASE).synthesize("Hi.")

    def test_unreachable_server_names_the_url(self, monkeypatch):
        Wire(monkeypatch, [requests.ConnectionError("refused")])
        with pytest.raises(ConnectionError, match="lemonade.test:13305"):
            LemonadeTTSClient(base_url=BASE).synthesize("Hi.")

    def test_empty_text_never_reaches_the_server(self, monkeypatch):
        wire = Wire(monkeypatch, [])
        with pytest.raises(ValueError):
            LemonadeTTSClient(base_url=BASE).synthesize("   ")
        assert not wire.requests

    def test_synthesize_to_wav_writes_a_readable_file(self, monkeypatch, tmp_path):
        Wire(monkeypatch, [pcm_response(np.full(TTS_SAMPLE_RATE, 0.25))])
        out = tmp_path / "speech.wav"
        seconds = LemonadeTTSClient(base_url=BASE).synthesize_to_wav("Hi.", out)
        assert seconds == pytest.approx(1.0)
        with wave.open(str(out)) as handle:
            assert handle.getframerate() == TTS_SAMPLE_RATE
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getnframes() == TTS_SAMPLE_RATE


class TestEnsureModelPulled:
    def test_downloaded_model_is_not_pulled_again(self, monkeypatch):
        wire = Wire(
            monkeypatch,
            [catalog({"id": "kokoro-v1", "labels": ["tts"], "downloaded": True})],
        )
        LemonadeTTSClient(base_url=BASE).ensure_model()
        assert [r.url for r in wire.requests] == [f"{BASE}/models?show_all=true"]

    def test_missing_model_is_pulled_by_name_only(self, monkeypatch):
        """Built-ins are pulled by name; a recipe on a built-in 400s (#1655)."""
        said = []
        wire = Wire(
            monkeypatch,
            [
                catalog(
                    {
                        "id": "Whisper-Base",
                        "labels": ["transcription"],
                        "downloaded": False,
                        "size": 0.148,
                    }
                ),
                FakeResponse(body={"status": "success"}),
            ],
        )
        LemonadeASRClient(base_url=BASE, model="Whisper-Base").ensure_model(
            say=said.append
        )
        assert wire.requests[1].url == f"{BASE}/pull"
        assert wire.json_body(1) == {"model_name": "Whisper-Base"}
        assert said and "Whisper-Base" in said[0] and "0.15 GB" in said[0]

    def test_unknown_model_lists_the_ones_that_exist(self, monkeypatch):
        Wire(
            monkeypatch,
            [
                catalog(
                    {"id": "kokoro-v1", "labels": ["tts"], "downloaded": False},
                    {"id": "Gemma", "labels": ["chat"], "downloaded": True},
                )
            ],
        )
        with pytest.raises(LemonadeTTSError, match="no model 'kokoro-v9'.*kokoro-v1"):
            LemonadeTTSClient(base_url=BASE, model="kokoro-v9").ensure_model()

    def test_model_with_the_wrong_purpose_is_refused(self, monkeypatch):
        Wire(
            monkeypatch,
            [catalog({"id": "Gemma", "labels": ["chat"], "downloaded": True})],
        )
        with pytest.raises(LemonadeASRError, match="not a transcription model"):
            LemonadeASRClient(base_url=BASE, model="Gemma").ensure_model()

    def test_failed_pull_raises(self, monkeypatch):
        Wire(
            monkeypatch,
            [
                catalog({"id": "kokoro-v1", "labels": ["tts"], "downloaded": False}),
                FakeResponse(507, body={"error": {"message": "disk full"}}),
            ],
        )
        with pytest.raises(LemonadeTTSError, match="disk full"):
            LemonadeTTSClient(base_url=BASE).ensure_model()

    def test_unreachable_server_is_a_connection_error(self, monkeypatch):
        Wire(monkeypatch, [requests.ConnectionError("refused")])
        with pytest.raises(ConnectionError, match="not reachable"):
            LemonadeASRClient(base_url=BASE).ensure_model()


# ------------------------------------------------------------------ KokoroTTS


@pytest.fixture
def fake_sd():
    with patch("gaia.audio.kokoro_tts.sd") as sd:
        yield sd


class TestKokoroPlayer:
    def _player(self, fake_sd, client):
        from gaia.audio.kokoro_tts import KokoroTTS

        return KokoroTTS(client=client)

    def test_pulls_the_voice_model_at_startup(self, fake_sd):
        client = MagicMock()
        self._player(fake_sd, client)
        client.ensure_model.assert_called_once()

    def test_speaks_in_sentence_sized_requests_with_the_chosen_voice(self, fake_sd):
        client = MagicMock()
        client.synthesize.return_value = (np.zeros(100, np.float32), TTS_SAMPLE_RATE)
        tts = self._player(fake_sd, client)
        tts.set_voice("bf_emma")
        tts.chunk_size = 4
        audio, stats = tts.generate_speech("One two three. Four five six. Seven.")

        texts = [c.args[0] for c in client.synthesize.call_args_list]
        assert texts == ["One two three.", "Four five six. Seven."]
        assert {c.kwargs["voice"] for c in client.synthesize.call_args_list} == {
            "bf_emma"
        }
        assert len(audio) == 200
        assert stats["audio_duration"] == pytest.approx(200 / TTS_SAMPLE_RATE, abs=1e-3)

    def test_audio_at_another_rate_is_refused(self, fake_sd):
        client = MagicMock()
        client.synthesize.return_value = (np.zeros(100, np.float32), 22050)
        tts = self._player(fake_sd, client)
        with pytest.raises(LemonadeTTSError, match="22050"):
            tts.generate_speech("Hello.")

    def test_unknown_voice_is_rejected_before_any_request(self, fake_sd):
        client = MagicMock()
        tts = self._player(fake_sd, client)
        with pytest.raises(ValueError, match="Unknown voice"):
            tts.set_voice("nope")
        client.synthesize.assert_not_called()

    def test_every_voice_has_display_metadata(self):
        for voice, meta in KOKORO_VOICES.items():
            assert voice[:2] in {"af", "am", "bf", "bm"}
            assert {"name", "quality", "duration"} <= set(meta)

    def test_audio_file_test_writes_wav_at_the_kokoro_rate(self, fake_sd, tmp_path):
        client = MagicMock()
        client.synthesize.return_value = (np.full(480, 0.1, np.float32), 24000)
        tts = self._player(fake_sd, client)
        out = tmp_path / "out.wav"
        tts.test_generate_audio_file("Hello.", str(out))
        with wave.open(str(out)) as handle:
            assert handle.getframerate() == TTS_SAMPLE_RATE
            assert handle.getnframes() == 480

    def test_playback_without_sounddevice_names_the_talk_extra(self):
        from gaia.audio.kokoro_tts import KokoroTTS

        with patch("gaia.audio.kokoro_tts.sd", None):
            tts = KokoroTTS(client=MagicMock())  # file output needs no device
            with pytest.raises(ImportError, match=r"amd-gaia\[talk\]"):
                tts.generate_speech_streaming(queue.Queue())


# ------------------------------------------------------------- live Whisper ASR


@pytest.fixture
def mic_sd():
    with patch("gaia.audio.audio_recorder.sd") as sd:
        sd.query_devices.return_value = {
            "name": "Test Mic",
            "index": 0,
            "max_input_channels": 1,
            "default_samplerate": 16000.0,
        }
        yield sd


def _parse_multipart(request):
    content_type = request.headers["Content-Type"]
    boundary = content_type.split("boundary=", 1)[1].encode()
    fields, files = {}, {}
    for part in request.body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        value = value.rstrip(b"\r\n")
        name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        if b"filename=" in head:
            files[name] = value
        else:
            fields[name] = value.decode()
    return fields, files


class TestLiveWhisper:
    def _asr(self, mic_sd, **kwargs):
        from gaia.audio.whisper_asr import WhisperAsr

        return WhisperAsr(base_url=BASE, **kwargs)

    def test_size_maps_to_the_lemonade_whisper_model(self, monkeypatch, mic_sd):
        Wire(
            monkeypatch,
            [
                catalog(
                    {
                        "id": "Whisper-Small",
                        "labels": ["transcription"],
                        "downloaded": True,
                    }
                )
            ],
        )
        asr = self._asr(mic_sd, model_size="small")
        assert asr.client.model == "Whisper-Small"

    def test_unknown_size_is_rejected(self, mic_sd):
        with pytest.raises(ValueError, match="Unknown Whisper model size"):
            self._asr(mic_sd, model_size="huge")

    def test_utterance_is_posted_as_16k_mono_wav(self, monkeypatch, mic_sd):
        wire = Wire(
            monkeypatch,
            [
                catalog(
                    {"id": "Whisper-Base", "labels": ["transcription"], "downloaded": 1}
                ),
                FakeResponse(
                    body={"text": " Turn on the lights.", "segments": [], "duration": 1}
                ),
            ],
        )
        asr = self._asr(mic_sd)
        utterance = (np.sin(np.linspace(0, 400, 16000)) * 0.3).astype(np.float32)
        assert asr.transcribe_utterance(utterance) == "Turn on the lights."

        request = wire.requests[1]
        assert request.url == f"{BASE}/audio/transcriptions"
        fields, files = _parse_multipart(request)
        assert fields["model"] == "Whisper-Base"
        assert fields["language"] == "en"
        assert fields["response_format"] == "verbose_json"
        with wave.open(io.BytesIO(files["file"])) as handle:
            assert handle.getframerate() == 16000
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getnframes() == 16000

    def test_processing_loop_queues_transcribed_text(self, mic_sd):
        client = MagicMock()
        client.transcribe.return_value = MagicMock(text=" Hello ")
        transcripts = queue.Queue()
        asr = self._asr(mic_sd, client=client, transcription_queue=transcripts)
        asr.audio_queue.put(np.zeros(8000, np.float32))
        asr._is_recording = True

        def stop_after_one(*_args, **_kwargs):
            asr._is_recording = False
            return MagicMock(text=" Hello ")

        client.transcribe.side_effect = stop_after_one
        asr._process_audio()
        assert transcripts.get_nowait() == "Hello"
        assert client.transcribe.call_args.kwargs["require_timestamps"] is False

    def test_lemonade_failure_stops_listening_and_says_why(self, mic_sd):
        client = MagicMock()
        client.transcribe.side_effect = ConnectionError("Lemonade went away")
        asr = self._asr(mic_sd, client=client, transcription_queue=queue.Queue())
        asr.audio_queue.put(np.zeros(8000, np.float32))
        asr._is_recording = True
        asr._process_audio()

        assert asr.is_recording is False
        assert "Lemonade went away" in asr.asr_error
        assert asr.capture_failed is False, "an ASR failure is not a microphone fault"

    def test_construction_fails_loudly_when_lemonade_is_down(self, monkeypatch, mic_sd):
        Wire(monkeypatch, [requests.ConnectionError("refused")])
        with pytest.raises(ConnectionError, match="not reachable"):
            self._asr(mic_sd)


class TestVoiceChatReportsASRFailure:
    def test_asr_error_becomes_the_session_error(self):
        """A dead Lemonade must end `gaia talk` with its reason, not "Listening"."""
        import asyncio

        from gaia.audio.audio_client import AudioClient

        with patch("gaia.audio.audio_client.create_client"):
            client = AudioClient(enable_tts=False)

        fake_asr = MagicMock()
        fake_asr.is_recording = False
        fake_asr.mic_error = None
        fake_asr.asr_error = "Speech recognition failed: Lemonade went away."
        fake_asr.capture_failed = False
        fake_asr.RATE = 16000
        fake_asr.SILENCE_THRESHOLD = 0.003
        fake_asr.MIN_AUDIO_LENGTH = 8000

        with (
            patch("gaia.audio.whisper_asr.WhisperAsr", return_value=fake_asr),
            patch.object(AudioClient, "_check_mic_levels"),
            patch.object(AudioClient, "_process_audio_wrapper"),
        ):
            with pytest.raises(RuntimeError, match="Lemonade went away"):
                asyncio.run(client.start_voice_chat(lambda _text: None))


# ---------------------------------------------------------------- real server

REAL_ASR_MODEL = "Whisper-Tiny"


@pytest.mark.integration
@pytest.mark.allow_network
class TestRealServer:
    """Kokoro speaks a sentence and Whisper hears it, both on the live server."""

    def test_speech_round_trip_through_talk_and_file_paths(
        self, require_lemonade, tmp_path
    ):
        from gaia.audio.lemonade_tts import write_wav

        tts = LemonadeTTSClient()
        tts.ensure_model()
        samples, rate = tts.synthesize(
            "Please schedule the design review for Thursday morning.",
            voice="am_michael",
        )
        assert rate == TTS_SAMPLE_RATE
        assert len(samples) > rate, "a full sentence is over a second of audio"

        n_out = int(len(samples) * 16000 / rate)
        clip = np.interp(
            np.linspace(0, len(samples) - 1, n_out), np.arange(len(samples)), samples
        ).astype(np.float32)

        # gaia talk: one utterance, plain text.
        asr = LemonadeASRClient(model=REAL_ASR_MODEL)
        asr.ensure_model()
        wav = tmp_path / "clip.wav"
        write_wav(wav, clip, 16000)
        plain = asr.transcribe(wav, language="en", require_timestamps=False)
        assert "thursday" in plain.text.lower()

        # transcribe_media: segments and word timings diarization aligns on.
        timed = asr.transcribe(wav, language="en")
        assert timed.segments, "verbose_json must return segments"
        assert timed.words, "whispercpp must return word timestamps"
        assert all(w.end >= w.start for w in timed.words)
        assert timed.segments[-1].end <= timed.duration + 1.0
