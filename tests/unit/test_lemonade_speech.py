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
    SpeechUnavailableError,
    mp3_frames,
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


def mp3_bytes(frames):
    """Whole MPEG-2 Layer III frames: 24 kHz mono at 160 kbps, as Lemonade sends."""
    return (b"\xff\xf3\xe4\xc4" + b"\x00" * 476) * frames


def id3v2(payload):
    size = len(payload)
    syncsafe = bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))
    return b"ID3\x03\x00\x00" + syncsafe + payload


def mp3_response(content):
    return FakeResponse(content=content, headers={"Content-Type": "audio/mpeg"})


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

    def test_missing_model_preflight_offers_the_download_in_the_apps(self, monkeypatch):
        wire = Wire(
            monkeypatch,
            [
                catalog(
                    {"id": DEFAULT_TTS_MODEL, "labels": ["tts"], "downloaded": False}
                )
            ],
        )
        with pytest.raises(
            LemonadeTTSError,
            match=r"not downloaded.*Read aloud under a reply.*/read-aloud in the TUI",
        ) as refusal:
            LemonadeTTSClient(base_url=BASE).check_model_available()
        # The apps recover; a chat reply never sends the user to a terminal.
        assert "gaia download" not in str(refusal.value)
        assert [request.url for request in wire.requests] == [
            f"{BASE}/models?show_all=true"
        ]

    def test_a_voice_missing_from_the_catalog_points_at_setup(self, monkeypatch):
        Wire(monkeypatch, [catalog()])
        with pytest.raises(LemonadeTTSError, match=r"no TTS model.*Run setup") as e:
            LemonadeTTSClient(base_url=BASE).check_model_available()
        assert "gaia download" not in str(e.value)

    def test_long_speech_is_split_and_joined(self, monkeypatch, tmp_path):
        wire = Wire(
            monkeypatch,
            [
                pcm_response(np.full(3, 0.1)),
                pcm_response(np.full(5, 0.2)),
            ],
        )
        out = tmp_path / "long.wav"
        text = "First sentence. Second sentence."
        seconds, chunks = LemonadeTTSClient(base_url=BASE).synthesize_to_wav_long(
            text, out, limit=20
        )
        speech_requests = [
            request for request in wire.requests if request.method == "POST"
        ]
        assert len(speech_requests) == 2
        assert chunks == 2
        assert seconds == pytest.approx(8 / TTS_SAMPLE_RATE)
        with wave.open(str(out)) as handle:
            assert handle.getnframes() == 8

    def test_a_speech_file_goes_to_disk_piece_by_piece(self, monkeypatch, tmp_path):
        wire = Wire(
            monkeypatch,
            [pcm_response(np.full(3, 0.1)), pcm_response(np.full(5, 0.2))],
        )
        out = tmp_path / "long.wav"
        seen = []
        seconds = LemonadeTTSClient(base_url=BASE).synthesize_file(
            ["First.", "  ", "Second."],
            out,
            voice="bm_george",
            speed=1.25,
            on_piece=lambda index, total: seen.append((index, total)),
        )
        assert seen == [(1, 2), (2, 2)]
        assert [wire.json_body(i)["input"] for i in (0, 1)] == ["First.", "Second."]
        assert wire.json_body(0)["voice"] == "bm_george"
        assert wire.json_body(0)["speed"] == 1.25
        assert wire.json_body(0)["response_format"] == "pcm"
        assert seconds == pytest.approx(8 / TTS_SAMPLE_RATE)
        with wave.open(str(out)) as handle:
            assert handle.getnframes() == 8
            assert handle.getframerate() == TTS_SAMPLE_RATE

    def test_mp3_pieces_are_joined_without_their_tags(self, monkeypatch, tmp_path):
        first, second = mp3_bytes(3), mp3_bytes(2)
        tagged = id3v2(b"x" * 20) + first + b"TAG" + b"\x00" * 125
        wire = Wire(monkeypatch, [mp3_response(tagged), mp3_response(second)])
        out = tmp_path / "speech.mp3"
        seconds = LemonadeTTSClient(base_url=BASE).synthesize_file(
            ["One.", "Two."], out, audio_format="mp3"
        )
        assert wire.json_body(0)["response_format"] == "mp3"
        assert out.read_bytes() == first + second
        assert seconds == pytest.approx(5 * 576 / 24000)

    def test_mp3_frames_refuses_what_is_not_whole_frames(self):
        frames, seconds = mp3_frames(mp3_bytes(4))
        assert frames == mp3_bytes(4)
        assert seconds == pytest.approx(4 * 576 / 24000)
        with pytest.raises(ValueError, match="no MPEG frame"):
            mp3_frames(b"RIFF" + b"\x00" * 64)
        with pytest.raises(ValueError, match="cut short"):
            mp3_frames(mp3_bytes(2)[:-10])
        with pytest.raises(ValueError, match="no MPEG frames"):
            mp3_frames(id3v2(b"only a tag"))

    @staticmethod
    def _info_frame(tag=b"Info", header=b"\xff\xf3\xe4\xc4", offset=13):
        """A silent header frame as LAME writes it: the tag after the side info."""
        size = 480 if header == b"\xff\xf3\xe4\xc4" else 417
        body = bytearray(size - 4)
        body[offset - 4 : offset] = tag
        return header + bytes(body)

    def test_a_pieces_info_frame_is_dropped_so_the_join_has_one_length(self):
        # MPEG-2 mono: 9 bytes of side info, so the tag sits at byte 13.
        for tag in (b"Info", b"Xing"):
            frames, seconds = mp3_frames(self._info_frame(tag) + mp3_bytes(3))
            assert frames == mp3_bytes(3)
            assert seconds == pytest.approx(3 * 576 / 24000)

    def test_mpeg1_stereo_and_vbri_headers_are_found_where_they_live(self):
        # MPEG-1 Layer III, 128 kbps, 44.1 kHz, stereo: 32 bytes of side info.
        mpeg1 = b"\xff\xfb\x90\x00"
        plain = (mpeg1 + b"\x00" * 413) * 2
        xing = self._info_frame(b"Xing", header=mpeg1, offset=36)
        vbri = self._info_frame(b"VBRI", header=mpeg1, offset=36)
        assert mp3_frames(xing + plain)[0] == plain
        assert mp3_frames(vbri + plain)[0] == plain
        # Mono MPEG-1 has 17 bytes of side info, so its tag is at byte 21.
        mono = b"\xff\xfb\x90\xc0"
        mono_plain = mono + b"\x00" * 413
        assert (
            mp3_frames(self._info_frame(header=mono, offset=21) + mono_plain)[0]
            == mono_plain
        )

    def test_audio_that_happens_to_spell_info_is_kept(self):
        # Only a leading frame can be a header; later ones are sound.
        frames, seconds = mp3_frames(mp3_bytes(2) + self._info_frame())
        assert len(frames) == 3 * 480
        assert seconds == pytest.approx(3 * 576 / 24000)
        # The tag at the wrong offset is sound too.
        wrong = self._info_frame(offset=21) + mp3_bytes(1)
        assert mp3_frames(wrong)[0] == wrong

    def test_a_file_that_is_only_an_info_frame_has_no_audio(self):
        with pytest.raises(ValueError, match="no MPEG frames"):
            mp3_frames(self._info_frame())

    def test_joined_pieces_keep_no_info_frame(self, monkeypatch, tmp_path):
        first = self._info_frame() + mp3_bytes(2)
        second = self._info_frame() + mp3_bytes(1)
        Wire(monkeypatch, [mp3_response(first), mp3_response(second)])
        out = tmp_path / "speech.mp3"
        seconds = LemonadeTTSClient(base_url=BASE).synthesize_file(
            ["One.", "Two."], out, audio_format="mp3"
        )
        assert out.read_bytes() == mp3_bytes(3)
        assert seconds == pytest.approx(3 * 576 / 24000)

    def test_a_reply_that_is_not_mp3_is_refused(self, monkeypatch, tmp_path):
        Wire(monkeypatch, [pcm_response(np.zeros(4))])
        out = tmp_path / "speech.mp3"
        with pytest.raises(LemonadeTTSError, match="expected 'audio/mpeg'"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                ["One."], out, audio_format="mp3"
            )
        assert not out.exists()

    def test_a_failed_piece_leaves_no_half_written_file(self, monkeypatch, tmp_path):
        Wire(
            monkeypatch,
            [pcm_response(np.zeros(4)), FakeResponse(500, {"error": "boom"})],
        )
        out = tmp_path / "speech.wav"
        with pytest.raises(LemonadeTTSError):
            LemonadeTTSClient(base_url=BASE).synthesize_file(["One.", "Two."], out)
        assert not out.exists()

    def test_a_stop_between_pieces_leaves_no_file(self, monkeypatch, tmp_path):
        Wire(monkeypatch, [pcm_response(np.zeros(4))])
        out = tmp_path / "speech.wav"

        def stop_at_two(index, _total):
            if index == 2:
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                ["One.", "Two."], out, on_piece=stop_at_two
            )
        assert not out.exists()

    @pytest.mark.parametrize("audio_format", ["wav", "mp3"])
    def test_a_failure_on_the_first_piece_keeps_its_own_error(
        self, monkeypatch, tmp_path, audio_format
    ):
        """An unopened WAV used to raise "sampling rate not specified" instead."""
        Wire(monkeypatch, [FakeResponse(500, {"error": "boom"})])
        out = tmp_path / f"speech.{audio_format}"
        with pytest.raises(LemonadeTTSError, match="500"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                ["One."], out, audio_format=audio_format
            )
        assert not out.exists()

    def test_a_stop_before_the_first_piece_keeps_its_own_error(self, tmp_path):
        out = tmp_path / "speech.wav"

        def stop(_index, _total):
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                ["One."], out, on_piece=stop
            )
        assert not out.exists()

    def test_pieces_in_different_formats_are_not_joined(self, monkeypatch, tmp_path):
        mono_24k = mp3_bytes(2)
        stereo_22k = (b"\xff\xf3\xe0\x04" + b"\x00" * 518) * 2
        Wire(monkeypatch, [mp3_response(mono_24k), mp3_response(stereo_22k)])
        out = tmp_path / "speech.mp3"
        with pytest.raises(LemonadeTTSError, match="piece 2 of 2"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                ["One.", "Two."], out, audio_format="mp3"
            )
        assert not out.exists()

        Wire(
            monkeypatch,
            [pcm_response(np.zeros(4)), pcm_response(np.zeros(4), rate=16000)],
        )
        wav = tmp_path / "speech.wav"
        with pytest.raises(LemonadeTTSError, match="piece 2 of 2"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(["One.", "Two."], wav)
        assert not wav.exists()

    def test_an_unknown_file_format_or_empty_text_is_refused(self, tmp_path):
        client = LemonadeTTSClient(base_url=BASE)
        with pytest.raises(ValueError, match="wav, mp3"):
            client.synthesize_file(["One."], tmp_path / "a.ogg", audio_format="ogg")
        with pytest.raises(ValueError, match="must not be empty"):
            client.synthesize_file(["  "], tmp_path / "a.wav")


#: 30 words: at most 3 words a second, at least 10 s of speech.
THIRTY_WORDS = (
    "The board approved the new berth. Work starts in spring and ends before "
    "winter. Traffic rose twelve percent to four thousand three hundred ten "
    "vessels, the busiest year on record."
)


class TestClippedSpeech:
    """A speech runtime that returns half a second for any text is refused."""

    def test_the_fixture_is_thirty_words(self):
        assert len(THIRTY_WORDS.split()) == 30

    def test_half_a_second_for_thirty_words_is_refused(self, monkeypatch):
        Wire(monkeypatch, [pcm_response(np.zeros(int(0.48 * TTS_SAMPLE_RATE)))])
        with pytest.raises(LemonadeTTSError) as caught:
            LemonadeTTSClient(base_url=BASE).synthesize(THIRTY_WORDS)
        said = str(caught.value)
        assert "returned 0.5 s of audio for 30 words" in said
        assert "at least 10 s" in said
        assert "update Lemonade Server" in said

    def test_a_clipped_wav_piece_leaves_no_file(self, monkeypatch, tmp_path):
        Wire(monkeypatch, [pcm_response(np.zeros(int(0.48 * TTS_SAMPLE_RATE)))])
        out = tmp_path / "report.wav"
        with pytest.raises(LemonadeTTSError, match="cut off"):
            LemonadeTTSClient(base_url=BASE).synthesize_file([THIRTY_WORDS], out)
        assert not out.exists()

    def test_a_clipped_mp3_piece_leaves_no_file(self, monkeypatch, tmp_path):
        # 20 MPEG-2 frames of 576 samples at 24 kHz: 0.48 s.
        Wire(monkeypatch, [mp3_response(mp3_bytes(20))])
        out = tmp_path / "report.mp3"
        with pytest.raises(LemonadeTTSError, match="0.5 s of audio for 30 words"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                [THIRTY_WORDS], out, audio_format="mp3"
            )
        assert not out.exists()

    def test_a_later_clipped_piece_removes_the_whole_file(self, monkeypatch, tmp_path):
        Wire(
            monkeypatch,
            [
                pcm_response(np.zeros(12 * TTS_SAMPLE_RATE)),
                pcm_response(np.zeros(int(0.48 * TTS_SAMPLE_RATE))),
            ],
        )
        out = tmp_path / "report.wav"
        with pytest.raises(LemonadeTTSError, match="cut off"):
            LemonadeTTSClient(base_url=BASE).synthesize_file(
                [THIRTY_WORDS, THIRTY_WORDS], out
            )
        assert not out.exists()

    def test_speech_of_a_normal_length_is_saved(self, monkeypatch, tmp_path):
        Wire(monkeypatch, [pcm_response(np.zeros(12 * TTS_SAMPLE_RATE))])
        out = tmp_path / "report.wav"
        seconds = LemonadeTTSClient(base_url=BASE).synthesize_file([THIRTY_WORDS], out)
        assert seconds == pytest.approx(12)
        with wave.open(str(out)) as handle:
            assert handle.getnframes() == 12 * TTS_SAMPLE_RATE

    def test_fast_speech_is_judged_at_its_speed(self, monkeypatch):
        # At 2x the 30 words need 5 s at most-fast, so 1.5 s is whole speech.
        Wire(monkeypatch, [pcm_response(np.zeros(int(1.5 * TTS_SAMPLE_RATE)))])
        samples, _ = LemonadeTTSClient(base_url=BASE).synthesize(
            THIRTY_WORDS, speed=2.0
        )
        assert len(samples) == int(1.5 * TTS_SAMPLE_RATE)

    def test_short_text_is_not_length_checked(self, monkeypatch):
        Wire(monkeypatch, [pcm_response(np.zeros(100))])
        samples, _ = LemonadeTTSClient(base_url=BASE).synthesize("GAIA is ready.")
        assert len(samples) == 100


class TestSpeechWithheldOnUnprovenPlatform:
    """Where GAIA hasn't proven its voice, no speech request is ever sent."""

    @pytest.fixture(autouse=True)
    def _on_a_mac(self, monkeypatch):
        import gaia.config

        monkeypatch.setattr(gaia.config, "_current_platform", lambda: "darwin")

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.synthesize(THIRTY_WORDS),
            lambda c: c.synthesize_mp3(THIRTY_WORDS),
            lambda c: c.check_model_available(),
            lambda c: c.ensure_model(),
        ],
    )
    def test_every_speech_call_is_refused_before_the_wire(self, monkeypatch, call):
        wire = Wire(monkeypatch, [])
        with pytest.raises(SpeechUnavailableError, match="aren't offered on macOS"):
            call(LemonadeTTSClient(base_url=BASE))
        assert wire.requests == []


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

    def test_a_caller_that_can_show_progress_gets_the_download_percent(
        self, monkeypatch
    ):
        """The pull streams, by name only, and each new percent reaches the caller."""
        from gaia.llm.lemonade_client import LemonadeClient

        pulls, said, percents = [], [], []

        def pull_model_stream(client, model_name, **kwargs):
            pulls.append((client.base_url, model_name, kwargs))
            yield {"event": "progress", "bytes_downloaded": 0, "bytes_total": 200}
            yield {"event": "progress", "bytes_downloaded": 50, "bytes_total": 200}
            yield {"event": "progress", "bytes_downloaded": 51, "bytes_total": 200}
            yield {"event": "progress", "bytes_downloaded": 200, "bytes_total": 200}
            yield {"event": "complete", "percent": 100}

        monkeypatch.setattr(LemonadeClient, "pull_model_stream", pull_model_stream)
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
                )
            ],
        )
        LemonadeASRClient(base_url=BASE, model="Whisper-Base").ensure_model(
            say=said.append, on_percent=percents.append
        )
        # Only the catalog read went out as a plain request; the pull streamed.
        assert [r.url for r in wire.requests] == [f"{BASE}/models?show_all=true"]
        assert pulls == [(BASE, "Whisper-Base", {})]
        assert percents == [0, 25, 100]
        assert said and "0.15 GB" in said[0]

    def test_the_streamed_pull_carries_the_speech_clients_key(self, monkeypatch):
        """An authenticated Lemonade rejects a pull without the caller's key."""
        from gaia.llm.lemonade_client import LemonadeClient

        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        keys = []

        def pull_model_stream(client, model_name, **kwargs):
            keys.append(client._auth_headers().get("Authorization"))
            yield {"event": "complete", "percent": 100}

        monkeypatch.setattr(LemonadeClient, "pull_model_stream", pull_model_stream)
        Wire(
            monkeypatch,
            [
                catalog(
                    {
                        "id": "Whisper-Base",
                        "labels": ["transcription"],
                        "downloaded": False,
                    }
                )
            ],
        )
        LemonadeASRClient(
            base_url=BASE, model="Whisper-Base", api_key="k-123"
        ).ensure_model(on_percent=lambda _p: None)

        assert keys == ["Bearer k-123"]

    def test_a_failed_streamed_pull_raises_the_speech_error(self, monkeypatch):
        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

        def pull_model_stream(client, model_name, **kwargs):
            yield {"event": "progress", "bytes_downloaded": 1, "bytes_total": 2}
            raise LemonadeClientError("disk full")

        monkeypatch.setattr(LemonadeClient, "pull_model_stream", pull_model_stream)
        Wire(
            monkeypatch,
            [
                catalog(
                    {
                        "id": "Whisper-Base",
                        "labels": ["transcription"],
                        "downloaded": False,
                    }
                )
            ],
        )
        with pytest.raises(LemonadeASRError, match="Whisper-Base.*disk full"):
            LemonadeASRClient(base_url=BASE, model="Whisper-Base").ensure_model(
                on_percent=lambda _p: None
            )

    def test_a_model_already_on_disk_is_not_pulled(self, monkeypatch):
        wire = Wire(
            monkeypatch,
            [
                catalog(
                    {
                        "id": "Whisper-Base",
                        "labels": ["transcription"],
                        "downloaded": True,
                    }
                )
            ],
        )
        client = LemonadeASRClient(base_url=BASE, model="Whisper-Base")
        assert client.ensure_model() is False
        assert len(wire.requests) == 1

    def test_is_loaded_reads_the_models_lemonade_holds(self, monkeypatch):
        health = FakeResponse(
            body={"all_models_loaded": [{"model_name": "Whisper-Base"}]}
        )
        wire = Wire(monkeypatch, [health, FakeResponse(body={})])
        assert LemonadeASRClient(base_url=BASE, model="Whisper-Base").is_loaded()
        assert not LemonadeASRClient(base_url=BASE, model="Whisper-Base").is_loaded()
        assert wire.requests[0].url == f"{BASE}/health"

    def test_load_posts_the_model_by_name(self, monkeypatch):
        """A built-in is loaded by name, the same way a transcription would load it."""
        wire = Wire(monkeypatch, [FakeResponse(body={"status": "success"})])
        LemonadeASRClient(base_url=BASE, model="Whisper-Base").load()
        assert wire.requests[0].url == f"{BASE}/load"
        assert wire.json_body(0) == {"model_name": "Whisper-Base"}

    def test_a_failed_load_raises_the_speech_error(self, monkeypatch):
        Wire(
            monkeypatch,
            [FakeResponse(status_code=500, body={"error": {"message": "no NPU"}})],
        )
        with pytest.raises(LemonadeASRError, match="no NPU"):
            LemonadeASRClient(base_url=BASE, model="Whisper-Base").load()

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


class TestNpuPart:
    """Whisper on the NPU fetches a second file when it first loads."""

    TURBO = {
        "id": "Whisper-Large-v3-Turbo",
        "labels": ["transcription"],
        "downloaded": True,
        "size": 1.62,
        "checkpoints": {
            "main": "ggerganov/whisper.cpp:ggml-large-v3-turbo.bin",
            "npu_cache": "amd/whisper-large-turbo-onnx-npu:"
            "ggml-large-v3-turbo-encoder-vitisai.rai",
        },
    }

    @staticmethod
    def system_info(state):
        backends = {"cpu": {"state": "installed"}}
        if state:
            backends["npu"] = {"state": state}
        return FakeResponse(body={"recipes": {"whispercpp": {"backends": backends}}})

    def client(self):
        return LemonadeASRClient(base_url=BASE, model="Whisper-Large-v3-Turbo")

    @pytest.mark.parametrize("state", ["installed", "installable", "update_required"])
    def test_an_npu_pc_gets_the_part_and_its_size(self, monkeypatch, state):
        wire = Wire(monkeypatch, [catalog(self.TURBO), self.system_info(state)])

        part = self.client().npu_part()

        assert part is not None and part.size_gb == pytest.approx(0.74)
        assert [r.url for r in wire.requests] == [
            f"{BASE}/models?show_all=true",
            f"{BASE}/system-info",
        ]

    @pytest.mark.parametrize("state", [None, "unsupported"])
    def test_a_pc_without_the_npu_backend_has_none(self, monkeypatch, state):
        Wire(monkeypatch, [catalog(self.TURBO), self.system_info(state)])

        assert self.client().npu_part() is None

    def test_a_model_without_an_npu_file_has_none(self, monkeypatch):
        base = {k: v for k, v in self.TURBO.items() if k != "checkpoints"}
        wire = Wire(monkeypatch, [catalog(base)])

        assert self.client().npu_part() is None
        assert len(wire.requests) == 1

    def test_load_asks_for_the_model_by_name(self, monkeypatch):
        wire = Wire(monkeypatch, [FakeResponse(body={"status": "success"})])

        self.client().load()

        assert wire.requests[0].url == f"{BASE}/load"
        assert wire.json_body(0) == {"model_name": "Whisper-Large-v3-Turbo"}

    @pytest.mark.parametrize(
        "loaded,expected", [(["Whisper-Large-v3-Turbo"], True), (["Gemma"], False)]
    )
    def test_is_loaded_reads_health(self, monkeypatch, loaded, expected):
        health = {"all_models_loaded": [{"model_name": m} for m in loaded]}
        wire = Wire(monkeypatch, [FakeResponse(body=health)])

        assert self.client().is_loaded() is expected
        assert wire.requests[0].url == f"{BASE}/health"


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

        # An editable checkout is told `-e <checkout>[talk]`; pin the wheel wording.
        with (
            patch("gaia.audio.kokoro_tts.sd", None),
            patch("gaia.agents.install_hints.editable_gaia_root", return_value=None),
        ):
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
