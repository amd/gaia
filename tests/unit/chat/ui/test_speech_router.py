# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read replies aloud: the shared voice settings and the speech endpoint."""

import io
import json
import wave

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.audio import lemonade_tts
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.ui.routers import speech


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(speech.router)
    return TestClient(app, headers={"X-Gaia-UI": "1"})


class _FakeTTS:
    """Stands in for Lemonade's Kokoro: records what it was asked to say."""

    calls = []
    error = None
    model = "kokoro-v1"
    base_url = "http://127.0.0.1:13305/api/v1"
    catalog_entries = [
        {"id": "kokoro-v1", "labels": ["tts"], "downloaded": True, "size": 0.35}
    ]

    def __init__(self, *a, **k):
        pass

    def catalog(self):
        if _FakeTTS.error:
            raise _FakeTTS.error
        return _FakeTTS.catalog_entries

    def ensure_model(self, say=None):
        if _FakeTTS.error:
            raise _FakeTTS.error

    def synthesize(self, text, voice, speed):
        _FakeTTS.calls.append((text, voice, speed))
        return np.zeros(2400, dtype=np.float32), 24000


@pytest.fixture
def tts(monkeypatch):
    _FakeTTS.calls = []
    _FakeTTS.error = None
    _FakeTTS.catalog_entries = [
        {"id": "kokoro-v1", "labels": ["tts"], "downloaded": True, "size": 0.35}
    ]
    monkeypatch.setattr(lemonade_tts, "LemonadeTTSClient", _FakeTTS)
    return _FakeTTS


def _saved():
    path = config_mod.GAIA_CONFIG_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ── Settings ───────────────────────────────────────────────────────────────


def test_a_fresh_install_reads_aloud_in_the_default_voice(client):
    body = client.get("/api/speech/settings").json()
    assert (body["voice"], body["speed"], body["auto"]) == ("af_bella", 100, False)
    assert (body["min_speed"], body["max_speed"]) == (50, 200)


def test_every_voice_says_its_accent_and_gender(client):
    voices = {v["id"]: v for v in client.get("/api/speech/settings").json()["voices"]}
    assert set(voices) == set(lemonade_tts.KOKORO_VOICES)
    assert voices["af_bella"] == {
        "id": "af_bella",
        "name": "Bella",
        "accent": "american",
        "gender": "female",
        "quality": "A-",
    }
    assert (voices["bm_george"]["accent"], voices["bm_george"]["gender"]) == (
        "british",
        "male",
    )


def test_settings_save_to_the_config_the_tui_reads(client):
    reply = client.put(
        "/api/speech/settings", json={"voice": "bm_george", "speed": 120, "auto": True}
    )
    assert reply.status_code == 200
    assert (reply.json()["voice"], reply.json()["speed"], reply.json()["auto"]) == (
        "bm_george",
        120,
        True,
    )
    saved = _saved()
    assert saved["read_aloud_voice"] == "bm_george"
    assert saved["read_aloud_speed"] == 120
    assert saved["read_aloud_auto"] is True


def test_saving_one_setting_keeps_the_others(client):
    client.put("/api/speech/settings", json={"voice": "bf_emma", "speed": 80})
    client.put("/api/speech/settings", json={"auto": True})
    body = client.get("/api/speech/settings").json()
    assert (body["voice"], body["speed"], body["auto"]) == ("bf_emma", 80, True)


@pytest.mark.parametrize(
    "update,named",
    [
        ({"voice": "af_nobody"}, "af_nobody"),
        ({"speed": 300}, "300"),
        ({"speed": 10}, "10"),
    ],
)
def test_an_unusable_setting_is_refused_and_nothing_is_saved(client, update, named):
    reply = client.put("/api/speech/settings", json=update)
    assert reply.status_code == 422
    assert named in reply.json()["detail"]
    assert _saved() == {}


# ── Speech ─────────────────────────────────────────────────────────────────


def test_speech_is_a_wav_in_the_saved_voice_and_speed(client, tts):
    client.put("/api/speech/settings", json={"voice": "am_michael", "speed": 150})
    reply = client.post("/api/speech", json={"text": "Hello there."})
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(reply.content)) as clip:
        assert (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) == (
            1,
            2,
            24000,
        )
        assert clip.getnframes() == 2400
    assert tts.calls == [("Hello there.", "am_michael", 1.5)]


def test_a_preview_can_name_its_own_voice_and_speed(client, tts):
    reply = client.post(
        "/api/speech", json={"text": "Preview", "voice": "bf_lily", "speed": 75}
    )
    assert reply.status_code == 200
    assert tts.calls == [("Preview", "bf_lily", 0.75)]


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "x" * (speech.MAX_SPEECH_CHARS + 1)},
        {"text": "Hi", "voice": "af_nobody"},
        {"text": "Hi", "speed": 500},
    ],
    ids=["empty", "blank", "too-long", "unknown-voice", "too-fast"],
)
def test_a_request_that_cannot_be_spoken_is_refused_before_lemonade(client, tts, body):
    assert client.post("/api/speech", json=body).status_code == 422
    assert tts.calls == []


def test_an_unreachable_lemonade_is_503_with_its_reason(client, tts):
    tts.error = ConnectionError("Lemonade Server is not reachable at http://x")
    reply = client.post("/api/speech", json={"text": "Hi"})
    assert reply.status_code == 503
    assert "not reachable" in reply.json()["detail"]


def test_a_lemonade_speech_failure_is_502_with_its_reason(client, tts):
    tts.error = lemonade_tts.LemonadeTTSError("Lemonade has no model 'kokoro-v1'.")
    reply = client.post("/api/speech", json={"text": "Hi"})
    assert reply.status_code == 502
    assert "kokoro-v1" in reply.json()["detail"]


# ── The voice model ────────────────────────────────────────────────────────


def test_the_voice_model_says_whether_it_is_downloaded(client, tts):
    assert client.get("/api/speech/model").json() == {
        "downloaded": True,
        "size_gb": 0.35,
    }
    tts.catalog_entries = [
        {"id": "kokoro-v1", "labels": ["tts"], "downloaded": False, "size": 0.35}
    ]
    assert client.get("/api/speech/model").json()["downloaded"] is False


def test_a_lemonade_without_the_voice_model_is_502(client, tts):
    tts.catalog_entries = []
    reply = client.get("/api/speech/model")
    assert reply.status_code == 502
    assert "kokoro-v1" in reply.json()["detail"]


def test_the_voice_model_check_is_503_when_lemonade_is_unreachable(client, tts):
    tts.error = ConnectionError("Lemonade Server is not reachable at http://x")
    assert client.get("/api/speech/model").status_code == 503


# ── gaia config ────────────────────────────────────────────────────────────


def test_gaia_config_set_checks_the_voice_and_speed():
    config = GaiaConfig()
    config.set("read_aloud_voice", "bm_lewis")
    config.set("read_aloud_speed", "125")
    assert (config.read_aloud_voice, config.read_aloud_speed) == ("bm_lewis", 125)
    config.set("read_aloud_voice", "")
    assert config.read_aloud_voice is None
    with pytest.raises(GaiaConfigError, match="Kokoro voice"):
        config.set("read_aloud_voice", "nobody")
    with pytest.raises(GaiaConfigError, match="50 to 200"):
        config.set("read_aloud_speed", "201")
