# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's voice routes: status, model download, and the socket."""

import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gaia.audio import lemonade_tts
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.ui.routers import voice
from gaia.voice import session as session_mod
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import LemonadeTarget

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


class FakeListener:
    """A recognizer that hears one utterance after two frames."""

    sent = []

    def __init__(self, target, model, **kw):
        self.model = model
        self.kw = kw
        self.frames = 0
        import asyncio

        self.queue = asyncio.Queue()

    async def connect(self):
        pass

    async def send_pcm16(self, pcm):
        FakeListener.sent.append(pcm)
        self.frames += 1
        if self.frames == 2:
            await self.queue.put({"type": "speech_stopped"})
            await self.queue.put({"type": "final", "text": "turn off the lights"})

    async def commit(self):
        await self.queue.put({"type": "final", "text": ""})

    async def events(self):
        while True:
            yield await self.queue.get()

    async def close(self):
        pass


class FakeTTS:
    def __init__(self, *a, **k):
        pass

    def synthesize(self, text, voice, speed):
        return np.full(2400, 0.1, dtype=np.float32), 24000


@pytest.fixture
def app(monkeypatch):
    catalog = [dict(e) for e in CATALOG]
    monkeypatch.setattr(voice, "_catalog", lambda: catalog)
    monkeypatch.setattr(
        voice,
        "resolve_target",
        lambda: LemonadeTarget("http://127.0.0.1:13305/api/v1", None),
    )
    monkeypatch.setattr(session_mod, "RealtimeListener", FakeListener)
    monkeypatch.setattr(lemonade_tts, "LemonadeTTSClient", FakeTTS)
    FakeListener.sent = []
    application = FastAPI()
    application.include_router(voice.router)
    application.state.catalog = catalog
    return application


@pytest.fixture
def client(app):
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def test_status_lists_missing_models(client):
    body = client.get("/api/voice/status?lang=en").json()
    assert body["ready"] is False and body["error_code"] == "model_missing"
    assert body["asr_model"] == "Moonshine-Tiny-Streaming"
    assert [(m["id"], m["downloaded"]) for m in body["models"]] == [
        ("Moonshine-Tiny-Streaming", True),
        ("kokoro-v1", False),
    ]
    assert body["models"][1]["size_gb"] == 0.34


def test_status_arabic_uses_whisper_and_no_speech(client):
    body = client.get("/api/voice/status?lang=ar").json()
    assert body["asr_model"] == "Whisper-Base" and body["speaks"] is False
    assert [m["id"] for m in body["models"]] == ["Whisper-Base"]


def test_status_reports_remote_lemonade(client, monkeypatch):
    def remote():
        raise VoiceError("remote_lemonade", "Voice only runs locally.")

    monkeypatch.setattr(voice, "_catalog", remote)
    body = client.get("/api/voice/status").json()
    assert body["ready"] is False and body["error_code"] == "remote_lemonade"


def test_download_refuses_non_voice_models(client):
    r = client.post(
        "/api/voice/models/download", json={"model": "Qwen3.6-35B-A3B-GGUF"}
    )
    assert r.status_code == 422


def test_download_pulls_by_name(client, monkeypatch):
    from gaia.audio import lemonade_service

    pulled = []

    def fake_pull(self, label, say=None):
        pulled.append((self.model, label))

    monkeypatch.setattr(
        lemonade_service.LemonadeAudioService, "ensure_model_pulled", fake_pull
    )
    r = client.post("/api/voice/models/download", json={"model": "kokoro-v1"})
    assert r.status_code == 200 and pulled == [("kokoro-v1", "tts")]


def test_socket_refuses_foreign_origin(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(
            "/api/voice/session", headers={"origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()
    assert e.value.code == 4403


def test_socket_refuses_tunnel_peer_without_token(app):
    class Tunnel:
        active = True

        def validate_token(self, token):
            return token == "good"

        def get_status(self):
            return {"url": "https://abc.ngrok-free.app"}

    app.state.tunnel = Tunnel()
    client = TestClient(app)
    headers = {"x-forwarded-for": "203.0.113.9"}
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/voice/session", headers=headers) as ws:
            ws.receive_json()
    assert e.value.code == 4403
    with client.websocket_connect(
        "/api/voice/session?token=good", headers=headers
    ) as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "stop"}))


def test_socket_dictation_round_trip(client):
    frame = np.full(1600, 1000, dtype="<i2").tobytes()
    with client.websocket_connect("/api/voice/session") as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "dictation"}))
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["speech"] is False
        ws.send_bytes(frame)
        ws.send_bytes(frame)
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] == "final":
                assert event["text"] == "turn off the lights"
                break
        assert "level" in seen and "speech_stopped" in seen
        ws.send_text(json.dumps({"type": "stop"}))
        assert ws.receive_json() == {"type": "closed"}
    assert FakeListener.sent == [frame, frame]


def test_socket_voice_needs_tts_model(client):
    with client.websocket_connect("/api/voice/session") as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "voice"}))
        event = ws.receive_json()
        assert event["type"] == "error" and event["code"] == "model_missing"
        assert ws.receive_json() == {"type": "closed"}


def test_socket_voice_speaks_pcm(client, app):
    for entry in app.state.catalog:
        entry["downloaded"] = True
    with client.websocket_connect("/api/voice/session") as ws:
        ws.send_text(json.dumps({"type": "start", "purpose": "voice"}))
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "speak", "text": "All done."}))
        ws.send_text(json.dumps({"type": "speak_end"}))
        got_audio = False
        while True:
            message = ws.receive()
            if message.get("bytes"):
                got_audio = True
                assert len(message["bytes"]) % 2 == 0
                continue
            event = json.loads(message["text"])
            if event["type"] == "speaking_done":
                break
        assert got_audio
        ws.send_text(json.dumps({"type": "stop"}))


def test_voice_config_keys_validate(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"voice_turn_taking": "telepathy"}), encoding="utf-8")
    with pytest.raises(GaiaConfigError) as e:
        GaiaConfig.load(path)
    assert "voice_turn_taking" in str(e.value)
    path.write_text(json.dumps({"voice_silence_ms": 50}), encoding="utf-8")
    with pytest.raises(GaiaConfigError):
        GaiaConfig.load(path)
    path.write_text(
        json.dumps({"voice_language": "AR", "voice_silence_ms": 700}), encoding="utf-8"
    )
    loaded = GaiaConfig.load(path)
    assert loaded.voice_language == "ar" and loaded.voice_silence_ms == 700
