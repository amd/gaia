# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia.voice: the spoken splitter, clause chunker, PCM helpers, listener and
session, against an in-process fake of Lemonade's realtime WebSocket."""

import asyncio
import base64
import json

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from gaia.voice import audio as pcm
from gaia.voice.chunker import ClauseChunker, first_sentence
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import (
    LemonadeTarget,
    pick_asr_model,
    require_downloaded,
    resolve_target,
)
from gaia.voice.listener import RealtimeListener, realtime_url
from gaia.voice.protocol import run_voice_socket
from gaia.voice.session import VoiceOptions, VoiceSession
from gaia.voice.spoken import MISSING_SPOKEN_LINE, SpokenSplitter, split_spoken

# ---------------------------------------------------------------- spoken


def _stream(text, size):
    s = SpokenSplitter()
    out = []
    for i in range(0, len(text), size):
        out += s.feed(text[i : i + size])
    out += s.finish()
    return s, out


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 100])
def test_splitter_any_chunking(size):
    s, out = _stream("<spoken>It's done. Three files.</spoken>\nDetail **here**.", size)
    assert s.spoken_text == "It's done. Three files."
    assert "".join(t for c, t in out if c == "detail") == "Detail **here**."
    assert not s.missing


def test_splitter_missing_block():
    spoken, detail, missing = split_spoken("Just an answer.")
    assert (spoken, detail, missing) == ("", "Just an answer.", True)
    assert MISSING_SPOKEN_LINE


def test_splitter_leading_whitespace_and_unclosed():
    spoken, detail, missing = split_spoken("  \n<spoken>Half said")
    assert spoken == "Half said" and detail == "" and not missing


def test_splitter_tag_like_text_is_detail():
    spoken, detail, missing = split_spoken("<spo is not a tag")
    assert missing and detail == "<spo is not a tag"


def test_splitter_spoken_never_leaks_tag_fragment():
    s = SpokenSplitter()
    pieces = s.feed("<spoken>Hi there</spo")
    assert all("<" not in t for _, t in pieces)
    pieces += s.feed("ken>rest")
    assert s.spoken_text == "Hi there"
    assert [t for c, t in pieces if c == "detail"] == ["rest"]


# ---------------------------------------------------------------- chunker


def test_chunker_first_clause_then_sentences():
    c = ClauseChunker()
    out = c.feed("I found three invoices, totaling $4,210. ")
    out += c.feed("The largest is from March. And ")
    out += c.finish()
    assert out == [
        "I found three invoices,",
        "totaling $4,210.",
        "The largest is from March.",
        "And",
    ]


def test_chunker_first_piece_word_cap():
    c = ClauseChunker(first_words=4)
    assert c.feed("one two three four five six") == ["one two three four"]


def test_first_sentence():
    assert first_sentence("Let me look.\nThen more.") == "Let me look."
    assert first_sentence("no stop") == "no stop"


# ---------------------------------------------------------------- pcm


def test_mulaw_roundtrip_is_close():
    t = np.linspace(0, 1, 800, endpoint=False)
    wave = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    data = pcm.float_to_pcm16(wave)
    back = pcm.pcm16_to_float(pcm.mulaw_decode(pcm.mulaw_encode(data)))
    assert np.max(np.abs(back - wave)) < 0.02
    assert len(pcm.mulaw_encode(data)) == len(data) // 2


def test_mulaw_known_values():
    # G.711: silence encodes to 0xFF, full negative scale to 0x00.
    assert pcm.mulaw_encode(np.array([0], dtype="<i2").tobytes()) == b"\xff"
    assert pcm.mulaw_encode(np.array([-32768], dtype="<i2").tobytes()) == b"\x00"


def test_resample_and_level():
    x = np.ones(160, dtype=np.float32)
    assert pcm.resample(x, 8000, 16000).size == 320
    assert pcm.level(np.zeros(10, dtype=np.float32)) == 0.0
    assert 0 < pcm.level(x * 0.05) <= 1.0
    with pytest.raises(ValueError):
        pcm.pcm16_to_float(b"\x00")


def test_earcon_is_short_pcm16():
    data = pcm.earcon()
    assert len(data) % 2 == 0 and 0.1 < len(data) / 2 / pcm.TTS_RATE < 0.25


# ---------------------------------------------------------------- lemonade


def test_resolve_target_refuses_remote(monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.resolve_lemonade_base_url",
        lambda base_url=None: "http://10.0.0.5:13305/api/v1",
    )
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.resolve_lemonade_api_key",
        lambda api_key=None, base_url=None: None,
    )
    with pytest.raises(VoiceError) as e:
        resolve_target()
    assert e.value.code == "remote_lemonade"


def test_pick_asr_model():
    assert pick_asr_model("en") == "Moonshine-Tiny-Streaming"
    assert pick_asr_model("ar") == "Whisper-Base"
    assert pick_asr_model("en", "Whisper-Small") == "Whisper-Small"


def test_require_downloaded_reports_size():
    catalog = [
        {"id": "kokoro-v1", "labels": ["tts"], "downloaded": False, "size": 0.34}
    ]
    with pytest.raises(VoiceError) as e:
        require_downloaded(catalog, "kokoro-v1", "tts")
    assert e.value.code == "model_missing" and "0.34 GB" in str(e.value)
    with pytest.raises(VoiceError):
        require_downloaded([], "kokoro-v1", "tts")
    require_downloaded(
        [{"id": "M", "labels": ["realtime-transcription"], "downloaded": True}],
        "M",
        "transcription",
    )


def test_realtime_url_prefers_dedicated_port():
    t = LemonadeTarget("http://127.0.0.1:13305/api/v1", "k")
    assert realtime_url(t, {"websocket_port": 9001}, "M") == (
        "ws://127.0.0.1:9001/realtime?model=M&api_key=k"
    )
    assert realtime_url(
        LemonadeTarget("http://127.0.0.1:13305/api/v1", None), {}, "M"
    ) == ("ws://127.0.0.1:13305/v1/realtime?model=M")


# ---------------------------------------------------------------- fake lemonade


class FakeLemonade:
    """Health + realtime WS. Speech starts on the first loud frame and stops
    (with a final transcript) on commit or after `stop_after` frames."""

    def __init__(self, transcript="hello there", stop_after=3, refuse=False):
        self.transcript = transcript
        self.stop_after = stop_after
        self.refuse = refuse
        self.received = []  # every JSON message from the client
        self.auth = []

    def app(self):
        app = web.Application()
        app.router.add_get("/api/v1/health", self.health)
        app.router.add_get("/v1/realtime", self.realtime)
        return app

    async def health(self, request):
        self.auth.append(request.headers.get("Authorization"))
        return web.json_response({"status": "ok"})

    async def realtime(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.refuse:
            await ws.send_json({"type": "error", "error": {"message": "no model"}})
            await ws.close()
            return ws
        await ws.send_json({"type": "session.created", "session": {"id": "s1"}})
        frames = 0
        async for msg in ws:
            data = json.loads(msg.data)
            self.received.append(data)
            if data["type"] == "input_audio_buffer.append":
                frames += 1
                if frames == 1:
                    await ws.send_json({"type": "input_audio_buffer.speech_started"})
                    await ws.send_json(
                        {
                            "type": "conversation.item.input_audio_transcription.delta",
                            "delta": "hel",
                        }
                    )
                if frames == self.stop_after:
                    await self._finish(ws)
            elif data["type"] == "input_audio_buffer.commit":
                await self._finish(ws)
        return ws

    async def _finish(self, ws):
        await ws.send_json({"type": "input_audio_buffer.speech_stopped"})
        await ws.send_json(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": self.transcript,
            }
        )


@pytest.fixture
async def fake_lemonade():
    servers = []

    async def make(**kw):
        fake = FakeLemonade(**kw)
        server = TestServer(fake.app(), host="127.0.0.1")
        await server.start_server()
        servers.append(server)
        target = LemonadeTarget(f"http://127.0.0.1:{server.port}/api/v1", "secret")
        return fake, target

    yield make
    for server in servers:
        await server.close()


async def test_listener_protocol_shape(fake_lemonade):
    fake, target = await fake_lemonade()
    async with RealtimeListener(
        target, "Moonshine-Tiny-Streaming", silence_ms=600
    ) as lis:
        frame = pcm.float_to_pcm16(np.full(1600, 0.2, dtype=np.float32))
        for _ in range(3):
            await lis.send_pcm16(frame)
        events = []
        async for event in lis.events():
            events.append(event)
            if event["type"] == "final":
                break
    assert [e["type"] for e in events] == [
        "speech_started",
        "partial",
        "speech_stopped",
        "final",
    ]
    assert events[-1]["text"] == "hello there"
    update = fake.received[0]
    assert update == {
        "type": "session.update",
        "session": {
            "model": "Moonshine-Tiny-Streaming",
            "turn_detection": {"silence_duration_ms": 600},
        },
    }
    append = fake.received[1]
    assert append["type"] == "input_audio_buffer.append"
    assert base64.b64decode(append["audio"]) == frame
    assert fake.auth == ["Bearer secret"]


async def test_listener_reports_refusal(fake_lemonade):
    _, target = await fake_lemonade(refuse=True)
    with pytest.raises(VoiceError) as e:
        await RealtimeListener(target, "M").connect()
    assert e.value.code == "asr_failed" and "no model" in str(e.value)


async def test_listener_unreachable():
    target = LemonadeTarget("http://127.0.0.1:9/api/v1", None)
    with pytest.raises(VoiceError) as e:
        await RealtimeListener(target, "M").connect()
    assert e.value.code == "lemonade_unreachable"


# ---------------------------------------------------------------- session


class FakeSynth:
    def __init__(self, fail=False, delay=0.0):
        self.calls = []
        self.fail = fail
        self.delay = delay

    def synthesize(self, text, voice, speed):
        import time

        if self.delay:
            time.sleep(self.delay)
        self.calls.append(text)
        if self.fail and text != "Hi.":
            raise RuntimeError("kokoro broke")
        return np.full(2400, 0.1, dtype=np.float32), 24000


class Sink:
    def __init__(self):
        self.frames = []
        self.flushes = 0

    async def write(self, frame):
        self.frames.append(frame)

    async def flush(self):
        self.flushes += 1


CATALOG = [
    {
        "id": "Moonshine-Tiny-Streaming",
        "labels": ["realtime-transcription"],
        "downloaded": True,
    },
    {"id": "kokoro-v1", "labels": ["tts"], "downloaded": True},
]


async def _session(target, synth=None, purpose="voice", catalog=CATALOG):
    events = []

    async def emit(e):
        events.append(e)

    sink = Sink()
    s = VoiceSession(
        target,
        VoiceOptions(purpose=purpose),
        emit=emit,
        sink=sink,
        synthesizer=synth or FakeSynth(),
        catalog=lambda: catalog,
    )
    return s, events, sink


async def _until(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


async def test_session_transcribes_and_plays_earcon(fake_lemonade):
    _, target = await fake_lemonade()
    s, events, sink = await _session(target)
    await s.start()
    frame = pcm.float_to_pcm16(np.full(1600, 0.2, dtype=np.float32))
    for _ in range(3):
        await s.feed_mic(frame)
    await _until(lambda: any(e["type"] == "final" for e in events))
    types = [e["type"] for e in events]
    assert types[0] == "ready" and "level" in types
    assert sink.frames and sink.frames[0] == pcm.earcon()
    await s.close()


async def test_session_speaks_clauses_and_interrupt_drops_queue(fake_lemonade):
    _, target = await fake_lemonade()
    synth = FakeSynth(delay=0.05)
    s, events, sink = await _session(target, synth)
    await s.start()
    await s.speak("First clause, then a sentence. Another sentence. ")
    await s.speak("More words here. ")
    await _until(lambda: any(e["type"] == "spoken_piece" for e in events))
    await s.interrupt()
    spoken_before = len([e for e in events if e["type"] == "spoken_piece"])
    await asyncio.sleep(0.3)
    spoken_after = len([e for e in events if e["type"] == "spoken_piece"])
    assert spoken_after == spoken_before  # nothing queued survived
    assert sink.flushes == 1
    assert any(e["type"] == "speech_stopped_by_user" for e in events)
    await s.close()


async def test_session_speak_end_reports_done(fake_lemonade):
    _, target = await fake_lemonade()
    s, events, _ = await _session(target)
    await s.start()
    await s.speak("Short reply")
    await s.speak_end()
    await _until(lambda: any(e["type"] == "speaking_done" for e in events))
    pieces = [e["text"] for e in events if e["type"] == "spoken_piece"]
    assert pieces == ["Short reply"]
    await s.close()


async def test_session_tts_failure_is_reported(fake_lemonade):
    _, target = await fake_lemonade()
    s, events, _ = await _session(target, FakeSynth(fail=True))
    await s.start()
    await s.speak("Will fail.")
    await s.speak_end()
    await _until(lambda: any(e.get("code") == "tts_failed" for e in events))
    await s.close()


async def test_session_refuses_missing_model(fake_lemonade):
    _, target = await fake_lemonade()
    catalog = [dict(CATALOG[0]), dict(CATALOG[1], downloaded=False)]
    s, _, _ = await _session(target, catalog=catalog)
    with pytest.raises(VoiceError) as e:
        await s.start()
    assert e.value.code == "model_missing" and e.value.detail == "kokoro-v1"


async def test_dictation_needs_no_tts_model(fake_lemonade):
    _, target = await fake_lemonade()
    s, events, sink = await _session(target, purpose="dictation", catalog=CATALOG[:1])
    await s.start()
    await s.speak("ignored")
    assert events[0] == {
        "type": "ready",
        "purpose": "dictation",
        "asr_model": "Moonshine-Tiny-Streaming",
        "speech": False,
    }
    await s.close()


def test_options_validation():
    with pytest.raises(VoiceError):
        VoiceOptions(purpose="karaoke")
    with pytest.raises(VoiceError):
        VoiceOptions(silence_ms=50)


# ---------------------------------------------------------------- protocol


async def test_protocol_requires_start_first():
    sent = []

    async def receive():
        return json.dumps({"type": "speak", "text": "x"})

    async def send(e):
        sent.append(e)

    async def make(_):
        raise AssertionError("must not build a session")

    await run_voice_socket(receive, send, make, accepts_mic_frames=True)
    assert sent[0]["code"] == "bad_request" and sent[-1] == {"type": "closed"}


async def test_protocol_end_to_end(fake_lemonade):
    _, target = await fake_lemonade()
    frame = pcm.float_to_pcm16(np.full(1600, 0.2, dtype=np.float32))
    inbox = asyncio.Queue()
    for m in [json.dumps({"type": "start", "purpose": "dictation"}), frame, frame]:
        inbox.put_nowait(m)
    sent = []

    async def receive():
        return await inbox.get()

    async def send(e):
        sent.append(e)
        if e["type"] == "partial":
            inbox.put_nowait(json.dumps({"type": "commit"}))
        if e["type"] == "final":
            inbox.put_nowait(json.dumps({"type": "stop"}))

    async def make(start):
        s, _, _ = await _session(target, purpose=start["purpose"], catalog=CATALOG[:1])
        s._emit = send
        return s

    await asyncio.wait_for(
        run_voice_socket(receive, send, make, accepts_mic_frames=True), 5
    )
    finals = [e for e in sent if e["type"] == "final"]
    assert finals and finals[0]["text"] == "hello there"
    assert sent[-1] == {"type": "closed"}


async def test_protocol_rejects_frames_when_host_has_mic():
    sent = []
    inbox = [json.dumps({"type": "start"}), b"\x00\x00"]

    async def receive():
        return inbox.pop(0) if inbox else None

    async def send(e):
        sent.append(e)

    class Dummy:
        async def start(self):
            pass

        async def close(self):
            pass

    async def make(_):
        return Dummy()

    await run_voice_socket(receive, send, make, accepts_mic_frames=False)
    assert sent[0]["code"] == "bad_request"
