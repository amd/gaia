# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A recognizer that drops or goes quiet is reopened; one that keeps dropping
stops voice mode with an error instead of leaving it on "Listening"."""

import asyncio

import numpy as np
from test_noise import frames, vowel
from test_voice_core import CATALOG, FakeSynth, Sink, _until

from gaia.voice import session as session_module
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import LemonadeTarget
from gaia.voice.session import VoiceOptions, VoiceSession

TARGET = LemonadeTarget("http://127.0.0.1:1/api/v1", None)


class FakeListener:
    """Scripted recognizer: ``script`` is what this connection does."""

    def __init__(self, script: str):
        self.script = script
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.sent = 0

    async def connect(self):
        if self.script == "refuse":
            raise VoiceError("asr_failed", "Lemonade couldn't load the recognizer.")
        if self.script == "drop":
            self.queue.put_nowait(None)

    async def close(self):
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)

    async def send_pcm16(self, pcm_bytes):
        self.sent += 1
        if self.script == "works" and self.sent == 2:
            for event in (
                {"type": "speech_started"},
                {"type": "speech_stopped"},
                {"type": "final", "text": "Check my email."},
            ):
                self.queue.put_nowait(event)

    async def commit(self):
        pass

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                return
            yield event


async def start(scripts):
    made = []

    def factory(*_args, **_kw):
        listener = FakeListener(scripts[min(len(made), len(scripts) - 1)])
        made.append(listener)
        return listener

    events = []

    async def emit(event):
        events.append(event)

    session = VoiceSession(
        TARGET,
        VoiceOptions(),
        emit=emit,
        sink=Sink(),
        synthesizer=FakeSynth(),
        catalog=lambda: CATALOG,
        listener_factory=factory,
    )
    await session.start()
    return session, events, made


def kinds(events):
    return [e["type"] for e in events if e["type"] != "level"]


async def talk(session, ms=1000):
    for frame in frames(vowel(ms)):
        await session.feed_mic(frame)
        await asyncio.sleep(0.01)


async def test_a_dropped_recognizer_is_reopened_and_hears_the_next_words():
    session, events, made = await start(["drop", "works"])
    await _until(lambda: "recovered" in kinds(events))
    assert kinds(events)[:3] == ["ready", "recovering", "recovered"]
    await talk(session)
    await _until(lambda: "final" in kinds(events))
    assert {"type": "final", "text": "Check my email."} in events
    assert len(made) == 2
    await session.close()


async def test_a_silent_recognizer_is_reopened_when_someone_is_talking(monkeypatch):
    monkeypatch.setattr(session_module, "STALL_SECONDS", 0.2)
    session, events, made = await start(["stalls", "works"])
    await talk(session, 2500)
    await _until(lambda: "recovered" in kinds(events))
    await talk(session)
    await _until(lambda: "final" in kinds(events))
    assert len(made) == 2
    await session.close()


async def test_typing_alone_never_reopens_the_recognizer(monkeypatch):
    from test_noise import typing

    monkeypatch.setattr(session_module, "STALL_SECONDS", 0.0)
    session, events, made = await start(["stalls"])
    for frame in frames(typing(4000)):
        await session.feed_mic(frame)
    await asyncio.sleep(0.05)
    assert "recovering" not in kinds(events)
    assert len(made) == 1
    await session.close()


async def test_a_recognizer_that_keeps_dropping_stops_with_an_error():
    session, events, made = await start(["drop"])
    await _until(lambda: "error" in kinds(events))
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "asr_failed"
    assert len(made) == 1 + session_module.MAX_REOPENS
    await session.close()


async def test_a_recognizer_that_cannot_be_reloaded_reports_why():
    session, events, _ = await start(["drop", "refuse"])
    await _until(lambda: "error" in kinds(events))
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "asr_failed" and "load" in error["message"]
    await session.close()


async def test_audio_during_a_reopen_is_dropped_not_fatal():
    session, _, _ = await start(["works"])
    session._reopening = True
    await session.feed_mic(next(frames(np.zeros(1600, dtype=np.float32))))
    await session.close()


async def test_speech_that_never_ends_reopens_the_recognizer(monkeypatch):
    monkeypatch.setattr(session_module, "MAX_UTTERANCE_SECONDS", 0.1)
    session, events, made = await start(["stalls", "works"])
    made[0].queue.put_nowait({"type": "speech_started"})
    await _until(lambda: "speech_started" in kinds(events))
    await asyncio.sleep(0.15)
    await talk(session, 300)
    await _until(lambda: "recovered" in kinds(events))
    assert len(made) == 2
    await session.close()


async def test_a_voice_changed_mid_conversation_is_used_for_the_next_reply():
    from test_voice_core import FakeSynth as Synth

    chosen = {"voice": "af_bella", "speed": 1.0}
    synth = Synth()
    events = []

    async def emit(event):
        events.append(event)

    session = VoiceSession(
        TARGET,
        VoiceOptions(),
        emit=emit,
        sink=Sink(),
        synthesizer=synth,
        catalog=lambda: CATALOG,
        listener_factory=lambda *_a, **_k: FakeListener("stalls"),
        voice_settings=lambda: (chosen["voice"], chosen["speed"], True),
    )
    await session.start()
    chosen.update(voice="bm_george", speed=1.25)
    await session.speak("How is this one?")
    await session.speak_end()
    await _until(lambda: "speaking_done" in kinds(events))
    assert session.options.voice == "bm_george" and session.options.speed == 1.25
    await session.close()


async def test_speech_the_host_is_still_playing_can_be_interrupted():
    """Audio is sent faster than it plays: the server is done, the room still hears her."""
    session, events, made = await start(["stalls"])
    await session.speak("A long answer that is still playing.")
    await session.speak_end()
    session.playback(True)
    await _until(lambda: "speaking_done" in kinds(events))
    assert not session.speaking and session.audible
    made[0].queue.put_nowait({"type": "speech_started"})
    made[0].queue.put_nowait({"type": "partial", "text": "no wait"})
    await _until(lambda: "barge_in" in kinds(events))
    session.playback(False)
    assert not session.audible
    await session.close()


async def test_the_chime_can_be_turned_off():
    from gaia.voice import audio as pcm

    session, events, made = await start(["works"])
    session.options.chime = False
    await talk(session)
    await _until(lambda: "final" in kinds(events))
    assert pcm.earcon() not in session._sink.frames
    await session.close()
