# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/voice/*`` — the TUI's voice host: mic, speaker, socket, auth.

sounddevice and the Lemonade recognizer are faked: the tests pin the contract
the TUI depends on (token auth, stable error codes, capture held while GAIA
speaks, an interrupt dropping queued speech at once), not PortAudio itself.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import types

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from gaia.audio import lemonade_tts
from gaia.daemon import voice_routes
from gaia.voice import session as session_mod
from gaia.voice.lemonade import LemonadeTarget
from gaia.voice.session import VoiceOptions

# TestClient drives ASGI in-process; nothing leaves the machine.
pytestmark = pytest.mark.allow_network

TOKEN = "daemon-client-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

CATALOG = [
    {
        "id": "Moonshine-Tiny-Streaming",
        "labels": ["realtime-transcription"],
        "downloaded": True,
        "size": 0.2,
    },
    {"id": "Whisper-Base", "labels": ["transcription"], "downloaded": False},
    {"id": "kokoro-v1", "labels": ["tts"], "downloaded": False, "size": 0.34},
]


class PortAudioError(Exception):
    pass


class FakeStream:
    """A PortAudio stream; the input kind feeds frames from a thread."""

    def __init__(self, sd, kind, samplerate, channels, dtype, blocksize, callback):
        assert channels == 1 and dtype == "int16"
        self.sd, self.kind, self.rate = sd, kind, samplerate
        self.blocksize, self.callback = blocksize, callback
        self.running = False
        self.closed = False
        self._thread = None

    def start(self):
        if self.kind in self.sd.fail_open:
            raise PortAudioError(self.sd.fail_open[self.kind])
        self.running = True
        self.sd.streams.append(self)
        if self.kind == "input":
            self._thread = threading.Thread(target=self._feed, daemon=True)
            self._thread.start()

    def _feed(self):
        frame = np.full(self.blocksize, 1000, dtype="<i2").tobytes()
        while self.running:
            self.callback(frame, self.blocksize, None, None)
            time.sleep(0.01)

    def stop(self):
        self.running = False

    def close(self):
        self.closed = True


def fake_sounddevice(inputs=1, fail_open=None):
    sd = types.SimpleNamespace()
    sd.PortAudioError = PortAudioError
    sd.streams = []
    sd.fail_open = fail_open or {}
    devices = [
        {"name": f"Mic {i}", "max_input_channels": 1, "max_output_channels": 0}
        for i in range(inputs)
    ] + [{"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2}]
    sd.query_devices = lambda: devices
    sd.default = types.SimpleNamespace(device=(0 if inputs else -1, inputs))
    sd.RawInputStream = lambda **kw: FakeStream(sd, "input", **kw)
    sd.RawOutputStream = lambda **kw: FakeStream(sd, "output", **kw)
    return sd


class FakeListener:
    """A recognizer that hears one utterance after two frames."""

    instances = []

    def __init__(self, target, model, **kw):
        self.model = model
        self.kw = kw
        self.sent = []
        self.queue = asyncio.Queue()
        FakeListener.instances.append(self)

    async def connect(self):
        pass

    async def send_pcm16(self, pcm):
        self.sent.append(pcm)
        if len(self.sent) == 2:
            await self.queue.put({"type": "speech_stopped"})
            await self.queue.put({"type": "final", "text": "turn off the lights"})

    async def commit(self):
        await self.queue.put({"type": "final", "text": "committed"})

    async def events(self):
        while True:
            yield await self.queue.get()

    async def close(self):
        pass


class FakeTTS:
    seconds = 0.1

    def __init__(self, *a, **k):
        pass

    def synthesize(self, text, voice, speed):
        return np.full(int(24000 * FakeTTS.seconds), 0.1, dtype=np.float32), 24000


def use_config(monkeypatch, **values):
    config = voice_routes.GaiaConfig(**values)
    monkeypatch.setattr(
        voice_routes.GaiaConfig, "load", classmethod(lambda cls, path=None: config)
    )


@pytest.fixture
def sd(monkeypatch):
    fake = fake_sounddevice()
    monkeypatch.setattr(voice_routes, "_sounddevice", lambda: fake)
    return fake


@pytest.fixture
def catalog(monkeypatch):
    entries = [dict(e) for e in CATALOG]
    monkeypatch.setattr(voice_routes, "_catalog", lambda: entries)
    return entries


@pytest.fixture
def client(monkeypatch, sd, catalog):
    use_config(monkeypatch)
    monkeypatch.setattr(
        voice_routes,
        "resolve_target",
        lambda: LemonadeTarget("http://127.0.0.1:13305/api/v1", None),
    )
    monkeypatch.setattr(session_mod, "RealtimeListener", FakeListener)
    monkeypatch.setattr(lemonade_tts, "LemonadeTTSClient", FakeTTS)
    FakeListener.instances = []
    FakeTTS.seconds = 0.1
    app = FastAPI()
    app.include_router(voice_routes.build_voice_router(TOKEN))
    return TestClient(app)


def _until(ws, kind):
    seen = []
    while True:
        event = ws.receive_json()
        seen.append(event["type"])
        if event["type"] == kind:
            return event, seen


# ---------------------------------------------------------------- auth


def test_status_requires_the_client_token(client):
    assert client.get("/daemon/v1/voice/status").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/daemon/v1/voice/status", headers=bad).status_code == 401


def test_socket_refuses_a_missing_or_wrong_token(client):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}):
        with pytest.raises(WebSocketDenialResponse) as e:
            with client.websocket_connect("/daemon/v1/voice/session", headers=headers):
                pass
        assert e.value.status_code == 401


# ---------------------------------------------------------------- status


def test_status_lists_missing_models_and_the_microphone(client):
    body = client.get("/daemon/v1/voice/status", headers=AUTH).json()
    assert body["ready"] is False and body["error_code"] == "model_missing"
    assert body["microphone"] == {
        "available": True,
        "name": "Mic 0",
        "error": None,
        "error_code": None,
    }
    assert [(m["id"], m["downloaded"]) for m in body["models"]] == [
        ("Moonshine-Tiny-Streaming", True),
        ("kokoro-v1", False),
    ]


def test_status_reports_no_microphone(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "_sounddevice", lambda: fake_sounddevice(0))
    body = client.get("/daemon/v1/voice/status", headers=AUTH).json()
    assert body["ready"] is False and body["error_code"] == "mic_missing"
    assert "No microphone" in body["error"]


def test_missing_sounddevice_names_the_package(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_sounddevice(name, *a, **k):
        if name == "sounddevice":
            raise ImportError("No module named 'sounddevice'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_sounddevice)
    with pytest.raises(voice_routes.VoiceError) as e:
        voice_routes._sounddevice()
    assert e.value.code == "mic_missing" and "sounddevice" in str(e.value)


def test_devices_lists_inputs_and_outputs(client):
    body = client.get("/daemon/v1/voice/devices", headers=AUTH).json()
    assert body["inputs"] == [{"index": 0, "name": "Mic 0", "default": True}]
    assert body["outputs"][0]["name"] == "Speakers"


def test_download_refuses_non_voice_models(client):
    r = client.post(
        "/daemon/v1/voice/models/download",
        json={"model": "Qwen3.6-35B-A3B-GGUF"},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_download_pulls_by_name(client, monkeypatch):
    from gaia.audio import lemonade_service

    pulled = []
    monkeypatch.setattr(
        lemonade_service.LemonadeAudioService,
        "ensure_model_pulled",
        lambda self, label, say=None: pulled.append((self.model, label)),
    )
    r = client.post(
        "/daemon/v1/voice/models/download", json={"model": "kokoro-v1"}, headers=AUTH
    )
    assert r.status_code == 200 and pulled == [("kokoro-v1", "tts")]


def test_no_websocket_library_is_a_named_error(monkeypatch, sd, catalog):
    monkeypatch.setattr(
        voice_routes.importlib.util, "find_spec", lambda name: None, raising=True
    )
    app = FastAPI()
    app.include_router(voice_routes.build_voice_router(TOKEN))
    client = TestClient(app)
    r = client.get("/daemon/v1/voice/session", headers=AUTH)
    assert r.status_code == 503 and "websockets" in r.json()["detail"]
    body = client.get("/daemon/v1/voice/status", headers=AUTH).json()
    assert body["ready"] is False and "websockets" in body["error"]


# ---------------------------------------------------------------- socket


def test_dictation_records_the_local_microphone(client, sd):
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["speech"] is False
        final, seen = _until(ws, "final")
        assert final["text"] == "turn off the lights"
        assert "level" in seen
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "closed"}
    # Dictation opens no speaker, and the microphone is released on stop.
    assert [s.kind for s in sd.streams] == ["input"]
    assert sd.streams[0].closed


def test_mic_frames_from_the_client_are_refused(client):
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"\x00\x00" * 160)
        event, _ = _until(ws, "error")
        assert event["code"] == "bad_request"


def test_no_microphone_is_mic_missing(client, monkeypatch):
    monkeypatch.setattr(voice_routes, "_sounddevice", lambda: fake_sounddevice(0))
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        event = ws.receive_json()
        assert event["type"] == "error" and event["code"] == "mic_missing"
        assert "Traceback" not in event["message"]
        assert ws.receive_json() == {"type": "closed"}


@pytest.mark.parametrize(
    "failure, code",
    [
        ("Device unavailable [PaErrorCode -9985]", "mic_busy"),
        ("Permission denied", "mic_denied"),
        ("Invalid device", "mic_missing"),
    ],
)
def test_microphone_open_failures_map_to_stable_codes(
    client, monkeypatch, failure, code
):
    fake = fake_sounddevice(fail_open={"input": failure})
    monkeypatch.setattr(voice_routes, "_sounddevice", lambda: fake)
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        event = ws.receive_json()
        assert event["type"] == "error" and event["code"] == code
        assert event["detail"] == failure


def test_voice_needs_the_tts_model(client):
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "voice"}))
        event = ws.receive_json()
        assert event["type"] == "error" and event["code"] == "model_missing"
        assert ws.receive_json() == {"type": "closed"}


def test_interrupt_drops_queued_speech_at_once(client, sd, catalog):
    for entry in catalog:
        entry["downloaded"] = True
    FakeTTS.seconds = 5  # far longer than the speaker may queue ahead
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "voice"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "speak", "text": "A long reply."}))
        ws.send_text(json.dumps({"type": "speak_end"}))
        _, seen = _until(ws, "spoken_piece")
        assert "speaking" in seen
        # The fake speaker never plays, so its queue sits full until flushed.
        speaker = next(s for s in sd.streams if s.kind == "output")
        ws.send_text(json.dumps({"type": "interrupt"}))
        _until(ws, "speech_stopped_by_user")
        ws.send_text(json.dumps({"type": "stop"}))
        _until(ws, "closed")
    assert speaker.closed


# ---------------------------------------------------------------- capture gate


class _FakeSpeaker:
    busy = False

    async def write(self, frame):
        pass

    async def flush(self):
        pass

    def close(self):
        pass


class _FakeMic:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.closed = False

    def close(self):
        self.closed = True


def _session(**options):
    mic, speaker = _FakeMic(), _FakeSpeaker()
    listener = FakeListener(None, "Moonshine-Tiny-Streaming")
    session = voice_routes.DaemonVoiceSession(
        LemonadeTarget("http://127.0.0.1:13305/api/v1", None),
        VoiceOptions(**options),
        emit=lambda event: asyncio.sleep(0),
        microphone=mic,
        speaker=speaker,
        synthesizer=FakeTTS(),
        catalog=lambda: [dict(e, downloaded=True) for e in CATALOG],
        listener_factory=lambda *a, **k: listener,
    )
    return session, mic, speaker, listener


async def _feed(mic, n=3):
    for _ in range(n):
        mic.frames.put_nowait(b"\x10\x00" * 1600)
    await asyncio.sleep(0.05)


async def test_capture_pauses_while_gaia_speaks():
    session, mic, speaker, listener = _session(purpose="voice")
    await session.start()
    try:
        await _feed(mic)
        assert len(listener.sent) == 3
        speaker.busy = True
        await _feed(mic)
        assert len(listener.sent) == 3, "frames reached the recognizer mid-speech"
        speaker.busy = False
        session.speaking = True
        await _feed(mic)
        assert len(listener.sent) == 3
        session.speaking = False
        await _feed(mic)
        assert len(listener.sent) == 6
    finally:
        await session.close()
    assert mic.closed


async def test_push_to_talk_gate():
    session, mic, _, listener = _session(purpose="voice", hands_free=False)
    await session.start()
    try:
        await _feed(mic)
        assert listener.sent == []
        session.talk_held = True
        await _feed(mic, 2)
        assert len(listener.sent) == 2
    finally:
        await session.close()


async def test_dictation_ignores_the_turn_taking_setting():
    session, mic, _, listener = _session(purpose="dictation", hands_free=False)
    await session.start()
    try:
        await _feed(mic, 2)
        assert len(listener.sent) == 2
    finally:
        await session.close()


async def test_speaker_flush_drops_queued_audio():
    sd = fake_sounddevice()
    speaker = voice_routes.Speaker(sd)
    await speaker.write(b"\x01\x00" * 2400)
    assert speaker.busy
    await speaker.flush()
    out = bytearray(b"\xff" * 200)
    speaker._callback(out, 100, None, None)
    assert out == bytearray(200), "flushed audio still played"
    speaker.close()


async def test_speaker_write_waits_for_playback():
    sd = fake_sounddevice()
    speaker = voice_routes.Speaker(sd)
    task = asyncio.create_task(speaker.write(b"\x01\x00" * 24000))  # 1 s
    await asyncio.sleep(0.05)
    assert not task.done(), "write ran a second ahead of the speaker"
    await speaker.flush()
    await asyncio.wait_for(task, 1)
    speaker.close()


def test_push_to_talk_messages_are_handled_by_the_daemon(client, catalog, monkeypatch):
    for entry in catalog:
        entry["downloaded"] = True
    use_config(monkeypatch, voice_turn_taking="push_to_talk")
    with client.websocket_connect("/daemon/v1/voice/session", headers=AUTH) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "voice"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "ptt_down"}))
        ws.send_text(json.dumps({"type": "ptt_up"}))
        final, _ = _until(ws, "final")
        assert final["text"] in ("committed", "turn off the lights")
        ws.send_text(json.dumps({"type": "stop"}))
        _until(ws, "closed")
