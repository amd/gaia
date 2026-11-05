# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Voice against a real local Lemonade: Kokoro speaks a sentence, and the
realtime recognizer hears it back through gaia.voice's listener and session.

Runs only when GAIA_VOICE_TEST_LEMONADE names a Lemonade API root, e.g.
http://127.0.0.1:13990/api/v1, so it never touches a server another process
is using by accident. Set GAIA_VOICE_TEST_ASR to pick the recognizer; the
default is the English default, which a fresh install has not downloaded, so
the download path runs too.
"""

import asyncio
import os

import numpy as np
import pytest

URL = os.environ.get("GAIA_VOICE_TEST_LEMONADE", "").strip()
ASR = os.environ.get("GAIA_VOICE_TEST_ASR", "Moonshine-Tiny-Streaming")

pytestmark = pytest.mark.skipif(not URL, reason="GAIA_VOICE_TEST_LEMONADE not set")

SENTENCE = "Please turn off the kitchen lights at nine tonight."


def _target():
    from gaia.voice.lemonade import LemonadeTarget

    return LemonadeTarget(URL, None)


def _speech_16k() -> bytes:
    from gaia.audio.lemonade_tts import LemonadeTTSClient
    from gaia.voice import audio as pcm

    samples, rate = LemonadeTTSClient(URL).synthesize(SENTENCE, "af_bella", 1.0)
    samples = pcm.resample(np.asarray(samples, dtype=np.float32), rate, pcm.ASR_RATE)
    # A second of silence after, so the recognizer's pause detection ends the turn.
    tail = np.zeros(pcm.ASR_RATE * 2, dtype=np.float32)
    return pcm.float_to_pcm16(np.concatenate([samples, tail]))


def _ensure(model: str, label: str) -> None:
    from gaia.audio.lemonade_service import LemonadeAudioService

    service = LemonadeAudioService(URL, model, None, 30)
    service.ensure_model_pulled(label)


async def _hear(audio: bytes, model: str) -> str:
    from gaia.voice.listener import RealtimeListener

    async with RealtimeListener(_target(), model, silence_ms=600) as listener:
        step = 3200  # 100 ms of 16 kHz PCM16

        async def feed():
            for i in range(0, len(audio), step):
                await listener.send_pcm16(audio[i : i + step])
                await asyncio.sleep(0.02)
            await listener.commit()

        feeder = asyncio.create_task(feed())
        finals = []
        async for event in listener.events():
            if event["type"] == "final" and event["text"]:
                finals.append(event["text"])
                if "light" in " ".join(finals).lower():
                    break
        feeder.cancel()
        return " ".join(finals)


async def test_kokoro_speech_is_heard_back():
    _ensure("kokoro-v1", "tts")
    _ensure(ASR, "transcription")
    heard = await asyncio.wait_for(_hear(_speech_16k(), ASR), 120)
    words = heard.lower()
    assert "kitchen" in words and "light" in words, heard


async def test_session_end_to_end_with_real_models():
    from gaia.audio.lemonade_service import LemonadeAudioService
    from gaia.audio.lemonade_tts import LemonadeTTSClient
    from gaia.voice.session import VoiceOptions, VoiceSession

    _ensure("kokoro-v1", "tts")
    _ensure(ASR, "transcription")
    events, frames = [], []

    async def emit(e):
        events.append(e)

    class Sink:
        async def write(self, f):
            frames.append(f)

        async def flush(self):
            frames.clear()

    session = VoiceSession(
        _target(),
        VoiceOptions(purpose="voice", asr_model=ASR, silence_ms=600),
        emit=emit,
        sink=Sink(),
        synthesizer=LemonadeTTSClient(URL),
        catalog=lambda: LemonadeAudioService(URL, "kokoro-v1", None, 30).catalog(),
    )
    await session.start()
    audio = _speech_16k()
    for i in range(0, len(audio), 3200):
        await session.feed_mic(audio[i : i + 3200])
        await asyncio.sleep(0.02)
    await session.commit()
    for _ in range(600):
        if any(e["type"] == "final" and e.get("text") for e in events):
            break
        await asyncio.sleep(0.1)
    finals = [e["text"] for e in events if e["type"] == "final" and e.get("text")]
    assert finals, events[-5:]
    await session.speak("All set. ")
    await session.speak_end()
    for _ in range(300):
        if any(e["type"] == "speaking_done" for e in events):
            break
        await asyncio.sleep(0.1)
    assert any(e["type"] == "speaking_done" for e in events)
    assert sum(len(f) for f in frames) > 24000  # more than half a second of speech
    await session.close()
