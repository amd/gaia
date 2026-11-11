# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The audio half of a voice session: listen, transcribe, speak, interrupt.

The app runs each turn through its normal chat path. This session holds the
recognizer connection, plays the earcon, synthesizes the text the app hands it
and reports levels for the animations. It is host-agnostic: the Agent UI
server feeds it browser frames and sends speech back as PCM, the daemon feeds
it the local microphone and plays through the local speaker.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Deque, List, Optional, Protocol, Tuple

import numpy as np

from gaia.logger import get_logger
from gaia.voice import audio as pcm
from gaia.voice.chunker import ClauseChunker, first_sentence
from gaia.voice.errors import VoiceError
from gaia.voice.lemonade import LemonadeTarget, require_downloaded
from gaia.voice.listener import RealtimeListener
from gaia.voice.noise import (
    BARGE_VOICED_MS,
    MIN_VOICED_MS,
    SpeechMeter,
    is_filler,
    is_loop,
    is_noise_text,
    tidy,
)
from gaia.voice.turns import Decision, TurnTaker

log = get_logger(__name__)

EventSink = Callable[[dict], Awaitable[None]]
FRAME_MS = 100
LEVEL_INTERVAL = 0.05
MAX_DICTATION_SECONDS = 300
#: A lone "okay" or "so" with less voiced sound than this was a noise.
FILLER_VOICED_MS = 350
#: This much talking with no word from the recognizer means it stalled.
STALL_VOICED_MS = 1500
STALL_SECONDS = 4.0
MAX_UTTERANCE_SECONDS = 90.0
#: Reopen the recognizer at most this often before giving up.
MAX_REOPENS = 3
REOPEN_WINDOW = 120.0
PURPOSES = ("dictation", "voice")
SPEAK_KINDS = ("spoken", "status")


class AudioSink(Protocol):
    """Where synthesized speech goes. PCM16 mono at :data:`pcm.TTS_RATE`."""

    async def write(self, frame: bytes) -> None: ...

    async def flush(self) -> None:
        """Drop anything queued but not yet heard."""


class Synthesizer(Protocol):
    def synthesize(self, text: str, voice: str, speed: float): ...


@dataclass
class VoiceOptions:
    purpose: str = "voice"
    asr_model: str = "Moonshine-Tiny-Streaming"
    tts_model: str = "kokoro-v1"
    voice: str = "af_bella"
    speed: float = 1.0
    silence_ms: int = 500
    hands_free: bool = True
    speak: bool = True  # False for non-English voice languages
    english: bool = True  # the recognizer is expected to return English

    def __post_init__(self):
        if self.purpose not in PURPOSES:
            raise VoiceError("bad_request", f"purpose must be one of {PURPOSES}")
        if not 300 <= int(self.silence_ms) <= 1200:
            raise VoiceError("bad_request", "silence_ms must be 300–1200")
        if not 0.5 <= float(self.speed) <= 2.0:
            raise VoiceError("bad_request", "speed must be 0.5–2.0")


@dataclass
class _Piece:
    text: str
    kind: str
    generation: int
    queued_at: float = field(default_factory=time.monotonic)


def _realtime_listener(*args, **kwargs) -> RealtimeListener:
    return RealtimeListener(*args, **kwargs)


class VoiceSession:
    """One live session. Create, :meth:`start`, feed, then :meth:`close`."""

    def __init__(
        self,
        target: LemonadeTarget,
        options: VoiceOptions,
        *,
        emit: EventSink,
        sink: Optional[AudioSink],
        synthesizer: Synthesizer,
        catalog: Callable[[], List[dict]],
        listener_factory=None,
        voice_settings: Optional[Callable[[], Tuple[str, float]]] = None,
    ):
        self.target = target
        self.options = options
        self._emit = emit
        self._sink = sink
        self._synth = synthesizer
        self._catalog = catalog
        # Read before each reply, so a voice changed mid-conversation is heard next.
        self._voice_settings = voice_settings
        # Looked up at call time so tests can stand in a fake recognizer.
        self._listener_factory = listener_factory or _realtime_listener
        self._listener: Optional[RealtimeListener] = None
        self._reader: Optional[asyncio.Task] = None
        self._speaker: Optional[asyncio.Task] = None
        self._queue: "asyncio.Queue[_Piece]" = asyncio.Queue()
        self._generation = 0
        self._chunker = ClauseChunker()
        self._last_level = 0.0
        self._started_at = 0.0
        self._closed = False
        self._limit_sent = False
        self.speaking = False
        self._turns = TurnTaker()
        self._meter = SpeechMeter()
        # Voiced milliseconds of utterances whose transcript is still to come.
        self._voiced: Deque[int] = deque()
        self._sustained = False
        self._in_speech = False
        self._speech_at = 0.0
        self._heard_at = 0.0
        self._unheard_ms = 0
        self._reopening = False
        self._reopens: List[float] = []
        #: The app asked a yes/no question: a bare "yes" or "okay" is the answer.
        self.expecting_answer = False
        self._hold: Optional[asyncio.Task] = None
        self._duck: Optional[asyncio.Task] = None
        # Stage marks of the turn in flight, for the timing event.
        self._marks: dict = {}

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        catalog = await asyncio.to_thread(self._catalog)
        require_downloaded(catalog, self.options.asr_model, "transcription")
        wants_speech = self.options.purpose == "voice" and self.options.speak
        if wants_speech:
            require_downloaded(catalog, self.options.tts_model, "tts")
        self._listener = self._listener_factory(
            self.target,
            self.options.asr_model,
            silence_ms=self.options.silence_ms,
            vad=self.options.hands_free,
        )
        await self._listener.connect()
        self._started_at = time.monotonic()
        self._heard_at = self._started_at
        self._reader = asyncio.create_task(self._read_listener())
        if wants_speech and self._sink is not None:
            self._speaker = asyncio.create_task(self._speak_loop())
            # Warm Kokoro so the first reply doesn't pay its load.
            asyncio.create_task(self._warm_tts())
        await self._emit(
            {
                "type": "ready",
                "purpose": self.options.purpose,
                "asr_model": self.options.asr_model,
                "speech": wants_speech,
            }
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel("_hold")
        self._cancel("_duck")
        for task in (self._reader, self._speaker):
            if task is not None:
                task.cancel()
        for task in (self._reader, self._speaker):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, VoiceError):
                    pass
        if self._listener is not None:
            await self._listener.close()

    # ---- input -----------------------------------------------------------

    async def feed_mic(self, frame: bytes) -> None:
        """Take one PCM16 16 kHz mono frame from the host."""
        if self._listener is None:
            raise RuntimeError("feed_mic before start")
        if (
            self.options.purpose == "dictation"
            and time.monotonic() - self._started_at > MAX_DICTATION_SECONDS
        ):
            if not self._limit_sent:
                self._limit_sent = True
                await self._emit({"type": "limit", "seconds": MAX_DICTATION_SECONDS})
                await self.commit()
            return
        if self._reopening:
            return
        try:
            await self._listener.send_pcm16(frame)
        except VoiceError:
            # The reader sees the same loss and reopens the recognizer.
            await self._listener.close()
            return
        samples = pcm.pcm16_to_float(frame)
        await self._watch(self._meter.feed(samples))
        if (
            self.speaking
            and not self._sustained
            and self._meter.voiced_ms >= BARGE_VOICED_MS
        ):
            # Real talking over GAIA stops her now, before the words are known.
            self._sustained = True
            await self._apply(self._turns.sustained_speech())
        now = time.monotonic()
        if now - self._last_level >= LEVEL_INTERVAL:
            self._last_level = now
            await self._emit(
                {
                    "type": "level",
                    "source": "mic",
                    "value": round(pcm.level(samples), 3),
                }
            )

    async def commit(self) -> None:
        """End the utterance now (push-to-talk release, or dictation stop)."""
        if self._listener is not None:
            await self._listener.commit()

    # ---- speech ----------------------------------------------------------

    async def speak(self, text: str, kind: str = "spoken") -> None:
        """Queue streamed text. ``spoken`` text is cut into clauses as it
        arrives; a ``status`` line is spoken whole, first sentence only."""
        if kind not in SPEAK_KINDS:
            raise VoiceError("bad_request", f"speak kind must be one of {SPEAK_KINDS}")
        if self._speaker is None:
            return
        if (
            self._voice_settings is not None
            and not self.speaking
            and self._queue.empty()
        ):
            self.options.voice, self.options.speed = await asyncio.to_thread(
                self._voice_settings
            )
        if kind == "status":
            line = first_sentence(text)
            if line:
                self._queue.put_nowait(_Piece(line, "status", self._generation))
            return
        self._mark("text")
        for piece in self._chunker.feed(text):
            self._queue.put_nowait(_Piece(piece, "spoken", self._generation))

    async def speak_end(self) -> None:
        """The spoken block is complete: flush the last clause."""
        if self._speaker is None:
            return
        for piece in self._chunker.finish():
            self._queue.put_nowait(_Piece(piece, "spoken", self._generation))
        self._queue.put_nowait(_Piece("", "end", self._generation))

    async def interrupt(self) -> None:
        """Barge-in: drop queued and playing speech at once."""
        self._generation += 1
        self._chunker = ClauseChunker()
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._sink is not None:
            await self._sink.flush()
        self._turns.speaking_done()
        if self.speaking:
            self.speaking = False
            await self._emit({"type": "speech_stopped_by_user"})

    async def play_earcon(self) -> None:
        if self._sink is not None and self.options.purpose == "voice":
            await self._sink.write(pcm.earcon())

    # ---- internals -------------------------------------------------------

    async def _read_listener(self) -> None:
        assert self._listener is not None
        conversing = self.options.purpose == "voice" and self.options.hands_free
        while not self._closed:
            try:
                async for event in self._listener.events():
                    self._heard_at = time.monotonic()
                    self._unheard_ms = 0
                    if event["type"] == "speech_started":
                        self._in_speech = True
                        self._speech_at = self._heard_at
                    elif event["type"] in ("speech_stopped", "final"):
                        self._in_speech = False
                    if conversing:
                        await self._converse(event)
                        continue
                    if event["type"] == "speech_stopped":
                        # Heard you, before the transcript lands.
                        await self.play_earcon()
                    await self._emit(event)
                why = "the recognizer closed the connection"
            except VoiceError as e:
                if e.code not in ("session_lost", "asr_failed"):
                    await self._emit(e.to_event())
                    return
                why = e.detail or str(e)
            if self._closed or not await self._reopen(why):
                return

    async def _reopen(self, why: str) -> bool:
        """The recognizer dropped (another app loaded a different model, or it
        stalled): open it again. False, with the error sent, when it can't be."""
        now = time.monotonic()
        self._reopens = [t for t in self._reopens if now - t < REOPEN_WINDOW]
        if len(self._reopens) >= MAX_REOPENS:
            await self._emit(
                VoiceError(
                    "asr_failed",
                    "Speech recognition keeps stopping, so voice mode has paused.",
                    detail=why[:300],
                ).to_event()
            )
            return False
        self._reopens.append(now)
        log.warning("voice: recognizer lost (%s); reopening", why)
        self._reopening = True
        await self._emit({"type": "recovering"})
        try:
            assert self._listener is not None
            await self._listener.close()
            listener = self._listener_factory(
                self.target,
                self.options.asr_model,
                silence_ms=self.options.silence_ms,
                vad=self.options.hands_free,
            )
            await listener.connect()
        except VoiceError as e:
            await self._emit(e.to_event())
            return False
        finally:
            self._reopening = False
        self._listener = listener
        self._in_speech = False
        self._unheard_ms = 0
        self._heard_at = time.monotonic()
        self._voiced.clear()
        self._meter.end()
        await self._emit({"type": "recovered"})
        return True

    async def _watch(self, voiced_ms: int) -> None:
        """Someone is talking and the recognizer says nothing: it has stalled."""
        if self._reopening:
            return
        if self._in_speech:
            if time.monotonic() - self._speech_at > MAX_UTTERANCE_SECONDS:
                # Speech that never ends is a recognizer that stopped answering.
                self._in_speech = False
                assert self._listener is not None
                await self._listener.close()
            return
        self._unheard_ms += voiced_ms
        if (
            self._unheard_ms >= STALL_VOICED_MS
            and time.monotonic() - self._heard_at >= STALL_SECONDS
        ):
            self._unheard_ms = 0
            assert self._listener is not None
            # Closing it ends the reader's loop, which reopens it.
            await self._listener.close()

    async def _converse(self, event: dict) -> None:
        """Hands-free voice: the turn-taker decides what each sound means."""
        kind = event["type"]
        if kind == "speech_started":
            self._cancel("_hold")
            self._meter.begin()
            self._sustained = False
            await self._emit(event)
            await self._apply(self._turns.speech_started(self.speaking))
        elif kind == "partial":
            await self._emit(event)
            await self._apply(self._turns.partial(event.get("text", ""), self.speaking))
        elif kind == "speech_stopped":
            self._voiced.append(self._meter.end())
            self._marks = {"stopped": time.monotonic()}
            await self._emit(event)
        elif kind == "final":
            text = event.get("text", "")
            voiced = self._voiced.popleft() if self._voiced else self._meter.end()
            reason = self._noise_reason(text, voiced)
            log.info(
                "voice: heard %d chars, %d ms voiced: %s",
                len(text),
                voiced,
                reason or "speech",
            )
            if reason:
                await self._emit({"type": "ignored", "reason": reason})
                await self._apply(self._turns.noise())
                await self._rest()
                return
            text = tidy(text)
            if self.expecting_answer:
                # An answer to a question is never a backchannel or a half-thought.
                await self._apply(self._turns.noise())
                if self.speaking:
                    await self.interrupt()
                await self._emit({"type": "final", "text": text.strip()})
                return
            await self._apply(self._turns.final(text, self.speaking))
        else:
            await self._emit(event)

    def _noise_reason(self, text: str, voiced_ms: int) -> Optional[str]:
        """Why this final is not the user speaking, or None when it is."""
        if not text.strip():
            return "silence"
        if is_noise_text(text, english=self.options.english) or is_loop(text):
            return "not_words"
        if voiced_ms < MIN_VOICED_MS:
            return "not_voiced"
        if (
            is_filler(text)
            and voiced_ms < FILLER_VOICED_MS
            and not self.expecting_answer
        ):
            return "filler"
        return None

    async def _rest(self) -> None:
        """After a sound that was nothing: a held half-sentence keeps its clock."""
        if self._turns.holding:
            await self._apply(self._turns.timeout())

    async def _apply(self, decision: Decision) -> None:
        if decision.duck is not None:
            if not decision.duck:
                self._cancel("_duck")
            await self._emit({"type": "duck", "on": decision.duck})
        if decision.duck_ms:
            self._cancel("_duck")
            self._duck = asyncio.create_task(
                self._after(decision.duck_ms, self._turns.duck_timeout)
            )
        if decision.barge_in:
            await self.interrupt()
            await self._emit({"type": "barge_in"})
        if decision.holding is not None:
            await self._emit({"type": "holding", "text": decision.holding})
        if decision.hold_ms:
            self._cancel("_hold")
            self._hold = asyncio.create_task(
                self._after(decision.hold_ms, self._turns.timeout)
            )
        if decision.turn:
            self._marks.setdefault("stopped", time.monotonic())
            self._marks["turn"] = time.monotonic()
            await self.play_earcon()
            await self._emit({"type": "final", "text": decision.turn})

    async def _after(self, ms: int, then) -> None:
        await asyncio.sleep(ms / 1000)
        await self._apply(then())

    def _cancel(self, name: str) -> None:
        task = getattr(self, name)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        setattr(self, name, None)

    def _mark(self, name: str) -> Optional[dict]:
        """Record a stage of the turn in flight; returns the timing event once,
        when the first audio of the reply goes out."""
        if "turn" not in self._marks or name in self._marks:
            return None
        self._marks[name] = time.monotonic()
        if name != "audio":
            return None
        m = self._marks
        text = m.get("text", m["audio"])

        def ms(a: float, b: float) -> int:
            return int(round((b - a) * 1000))

        return {
            "type": "timing",
            "heard_ms": ms(m["stopped"], m["turn"]),
            "think_ms": ms(m["turn"], text),
            "voice_ms": ms(text, m["audio"]),
            "total_ms": ms(m["stopped"], m["audio"]),
        }

    async def _warm_tts(self) -> None:
        try:
            await asyncio.to_thread(
                self._synth.synthesize, "Hi.", self.options.voice, self.options.speed
            )
        except (ConnectionError, RuntimeError) as e:
            # Reported again, as tts_failed, if a real reply can't be spoken.
            log.info("Kokoro warm-up failed: %s", e)

    async def _speak_loop(self) -> None:
        """Synthesize the next piece while the current one plays."""
        audio: "asyncio.Queue" = asyncio.Queue(maxsize=2)
        player = asyncio.create_task(self._play_loop(audio))
        try:
            while True:
                piece = await self._queue.get()
                if piece.generation != self._generation:
                    continue
                if piece.kind == "end":
                    await audio.put((piece, None))
                    continue
                # A status line still waiting when newer speech is queued is stale.
                if piece.kind == "status" and not self._queue.empty():
                    continue
                try:
                    samples, rate = await asyncio.to_thread(
                        self._synth.synthesize,
                        piece.text,
                        self.options.voice,
                        self.options.speed,
                    )
                except (ConnectionError, RuntimeError, ValueError) as e:
                    await self._emit(
                        VoiceError(
                            "tts_failed",
                            "Couldn't speak this reply.",
                            detail=str(e)[:300],
                        ).to_event()
                    )
                    continue
                if piece.generation != self._generation:
                    continue
                samples = pcm.resample(
                    np.asarray(samples, dtype=np.float32), rate, pcm.TTS_RATE
                )
                await audio.put((piece, samples))
        finally:
            player.cancel()

    async def _play_loop(self, audio: "asyncio.Queue") -> None:
        assert self._sink is not None
        step = pcm.TTS_RATE * FRAME_MS // 1000
        while True:
            piece, samples = await audio.get()
            if piece.generation != self._generation:
                continue
            if samples is None:
                self._turns.speaking_done()
                if self.speaking:
                    self.speaking = False
                    await self._emit({"type": "speaking_done"})
                continue
            if not self.speaking:
                self.speaking = True
                await self._emit({"type": "speaking", "kind": piece.kind})
            if piece.kind == "spoken":
                timing = self._mark("audio")
                if timing:
                    await self._emit(timing)
            await self._emit(
                {"type": "spoken_piece", "kind": piece.kind, "text": piece.text}
            )
            for i in range(0, samples.size, step):
                if piece.generation != self._generation:
                    break
                chunk = samples[i : i + step]
                await self._sink.write(pcm.float_to_pcm16(chunk))
                await self._emit(
                    {
                        "type": "level",
                        "source": "speech",
                        "value": round(pcm.level(chunk), 3),
                    }
                )
            if (
                piece.kind == "status"
                and audio.empty()
                and self._queue.empty()
                and self.speaking
            ):
                self.speaking = False
                await self._emit({"type": "speaking_done"})
