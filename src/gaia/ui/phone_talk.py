# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Talking with GAIA on a phone call.

Twilio streams the call's audio to ``/api/phone/stream`` over mobile access's
tunnel. The call names itself with a one-time token from the daemon. GAIA asks
for the PIN on the keypad before anything else; after it, the call is voice
mode over the phone: the same agent and tools, approvals and questions
answered by voice (or 1 / 2 on the keypad). See docs/plans/phone-calls.mdx.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.logger import get_logger
from gaia.voice import audio as pcm
from gaia.voice.errors import VoiceError
from gaia.voice.spoken import MISSING_SPOKEN_LINE, SpokenSplitter, without_tags

log = get_logger(__name__)

#: How long a call may last, and how long GAIA waits on a silent line.
MAX_CALL_SECONDS = 30 * 60
SILENCE_HANGUP_SECONDS = 120
ANSWER_WAIT_SECONDS = 60
PIN_DIGITS_MAX = 8
PHONE_FRAME_MS = 20

_YES = re.compile(r"\b(yes|yeah|yep|sure|ok(ay)?|go ahead|do it|allow|approve)\b", re.I)
_NO = re.compile(r"\b(no|nope|don'?t|stop|cancel|deny|never mind)\b", re.I)
_BYE = re.compile(r"^\W*(bye|goodbye|that'?s all|hang up|thanks,? bye)\W*$", re.I)

PIN_PROMPT = "Please enter your PIN, then press pound."
PIN_WRONG = "That PIN isn't right. Try again, then press pound."
PIN_LOCKED = "That PIN isn't right. Goodbye."
GREETING_ALERT = "Thanks. I'm here. What would you like to know?"
GREETING_CALLER = "Hi, it's GAIA. What can I do for you?"
GOODBYE = "Okay, talk soon. Bye."
APPROVAL = "I need your OK to {what}. Say yes or no, or press 1 to allow or 2 to deny."
APPROVAL_TIMEOUT = "I didn't hear an answer, so I won't do that."
TURN_FAILED = "Sorry, that didn't work. {why}"


def first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    match = re.search(r"[.!?](\s|$)", text)
    return (text[: match.end()] if match else text)[:limit].strip()


class TwilioSink:
    """Speech to the caller: 24 kHz PCM16 in, 8 kHz µ-law media messages out."""

    def __init__(self, send: Callable[[dict], Any], stream_sid: str):
        self._send = send
        self._sid = stream_sid

    async def write(self, frame: bytes) -> None:
        samples = pcm.resample(pcm.pcm16_to_float(frame), pcm.TTS_RATE, pcm.PHONE_RATE)
        law = pcm.mulaw_encode(pcm.float_to_pcm16(samples))
        step = pcm.PHONE_RATE * PHONE_FRAME_MS // 1000
        for i in range(0, len(law), step):
            await self._send(
                {
                    "event": "media",
                    "streamSid": self._sid,
                    "media": {"payload": base64.b64encode(law[i : i + step]).decode()},
                }
            )

    async def flush(self) -> None:
        # Twilio drops audio it has buffered but not yet played.
        await self._send({"event": "clear", "streamSid": self._sid})


def phone_to_asr(payload_b64: str) -> bytes:
    """One Twilio media payload (8 kHz µ-law) as 16 kHz PCM16 for the recognizer."""
    raw = pcm.mulaw_decode(base64.b64decode(payload_b64))
    samples = pcm.resample(pcm.pcm16_to_float(raw), pcm.PHONE_RATE, pcm.ASR_RATE)
    return pcm.float_to_pcm16(samples)


class PhoneCall:
    """One call, from Twilio's ``start`` event to hang-up.

    *daemon* is ``(method, path, body) -> dict`` for the phone service.
    *make_session* builds the :class:`~gaia.voice.session.VoiceSession` for a
    sink, voice and event callback. *run_turn* runs one agent turn and yields
    UI events: ``(text, history, events_out, answer_fn) -> awaitable`` where
    it puts each event on *events_out*; *answer_fn* answers approvals and
    questions. Both are injected so tests drive a call without Lemonade.
    """

    def __init__(
        self,
        *,
        receive: Callable[[], Any],
        send: Callable[[dict], Any],
        daemon: Callable[[str, str, Optional[dict]], Any],
        make_session: Callable[..., Any],
        run_turn: Callable[..., Any],
        save: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._receive = receive
        self._send = send
        self._daemon = daemon
        self._make_session = make_session
        self._run_turn = run_turn
        self._save = save
        self._clock = clock
        self.session = None
        self.stream_sid = ""
        self.call_id = ""
        self.phase = "starting"  # starting → pin → talk → ended
        self._pin = ""
        self._finals: "asyncio.Queue[str]" = asyncio.Queue()
        self._digits: "asyncio.Queue[str]" = asyncio.Queue()
        self._history: List[Tuple[str, str]] = []
        self._heard_at = clock()
        self.ended_reason = ""

    # ---- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        start = await self._wait_for_start()
        if start is None:
            return
        token = (start.get("customParameters") or {}).get("token", "")
        try:
            claim = await self._daemon("POST", "/talk/claim", {"token": token})
        except RuntimeError as e:
            log.warning("phone call refused: %s", e)
            self.ended_reason = "unknown call"
            return
        self.call_id = claim["call_id"]
        greeting = GREETING_CALLER if claim.get("incoming") else GREETING_ALERT
        self.session = self._make_session(
            sink=TwilioSink(self._send, self.stream_sid),
            voice=claim.get("voice") or "af_nicole",
            emit=self._on_voice_event,
        )
        try:
            await self.session.start()
        except VoiceError as e:
            log.error("phone call couldn't start voice: %s (%s)", e, e.code)
            self.ended_reason = e.code
            return
        reader = asyncio.create_task(self._read_twilio())
        try:
            self.phase = "pin"
            if await self._ask_pin():
                self.phase = "talk"
                await self._say(greeting)
                await self._converse()
        finally:
            self.phase = "ended"
            reader.cancel()
            await self.session.close()

    async def _wait_for_start(self) -> Optional[dict]:
        while True:
            message = await self._next_twilio()
            if message is None:
                return None
            if message.get("event") == "start":
                start = message.get("start") or {}
                self.stream_sid = str(
                    message.get("streamSid") or start.get("streamSid") or ""
                )
                return start

    async def _next_twilio(self) -> Optional[dict]:
        raw = await self._receive()
        if raw is None:
            return None
        try:
            message = json.loads(raw)
        except ValueError:
            return {}
        return message if isinstance(message, dict) else {}

    async def _read_twilio(self) -> None:
        while True:
            message = await self._next_twilio()
            if message is None or message.get("event") == "stop":
                self.ended_reason = self.ended_reason or "caller hung up"
                self._finals.put_nowait("\x00hangup")
                self._digits.put_nowait("\x00hangup")
                return
            event = message.get("event")
            if event == "media":
                payload = (message.get("media") or {}).get("payload")
                if payload:
                    await self.session.feed_mic(phone_to_asr(payload))
            elif event == "dtmf":
                digit = str((message.get("dtmf") or {}).get("digit") or "")
                if digit:
                    self._digits.put_nowait(digit)

    async def _on_voice_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "speech_started":
            self._heard_at = self._clock()
            if self.session is not None and self.session.speaking:
                await self.session.interrupt()  # the caller talked over GAIA
        elif kind == "final" and event.get("text"):
            self._heard_at = self._clock()
            if self.phase == "talk":
                self._finals.put_nowait(str(event["text"]))
        elif kind == "error":
            log.warning("phone call voice error: %s", event.get("message"))

    # ---- speaking --------------------------------------------------------

    async def _say(self, text: str) -> None:
        await self.session.speak(text)
        await self.session.speak_end()

    # ---- PIN -------------------------------------------------------------

    async def _ask_pin(self) -> bool:
        await self._say(PIN_PROMPT)
        while True:
            digits = ""
            while True:
                try:
                    digit = await asyncio.wait_for(
                        self._digits.get(), ANSWER_WAIT_SECONDS
                    )
                except asyncio.TimeoutError:
                    self.ended_reason = "no PIN entered"
                    return False
                if digit == "\x00hangup":
                    return False
                if digit == "#":
                    break
                if digit.isdigit() and len(digits) < PIN_DIGITS_MAX:
                    digits += digit
            result = await self._daemon(
                "POST", "/talk/pin", {"call_id": self.call_id, "pin": digits}
            )
            if result.get("ok"):
                return True
            if not result.get("tries_left"):
                await self._say(PIN_LOCKED)
                self.ended_reason = "wrong PIN"
                await asyncio.sleep(3)
                return False
            await self._say(PIN_WRONG)

    # ---- conversation ----------------------------------------------------

    async def _converse(self) -> None:
        started = self._clock()
        while self._clock() - started < MAX_CALL_SECONDS:
            try:
                text = await asyncio.wait_for(self._finals.get(), 5)
            except asyncio.TimeoutError:
                if self._clock() - self._heard_at > SILENCE_HANGUP_SECONDS:
                    await self._say(GOODBYE)
                    self.ended_reason = "silence"
                    await asyncio.sleep(3)
                    return
                continue
            if text == "\x00hangup":
                return
            if _BYE.match(text):
                await self._say(GOODBYE)
                self.ended_reason = "caller said goodbye"
                await asyncio.sleep(3)
                return
            await self._turn(text)

    async def _turn(self, text: str) -> None:
        events: "asyncio.Queue[Optional[dict]]" = asyncio.Queue()
        splitter = SpokenSplitter()
        answer = ""
        answerer: Dict[str, Callable] = {}
        task = asyncio.create_task(
            self._run_turn(text, list(self._history), events, answerer)
        )
        while True:
            event = await events.get()
            if event is None:
                break
            kind = event.get("type")
            if kind == "chunk":
                for channel, piece in splitter.feed(str(event.get("content") or "")):
                    if channel == "spoken":
                        await self.session.speak(piece)
            elif kind in ("tool_start", "commentary"):
                splitter.reset()
                if kind == "commentary" and event.get("content"):
                    await self.session.speak(str(event["content"]), "status")
            elif kind == "permission_request":
                await self._approve(event, answerer)
            elif kind == "user_input_request":
                await self._answer_question(event, answerer)
            elif kind == "answer":
                answer = str(event.get("content") or "")
            elif kind == "agent_error":
                await self._say(
                    TURN_FAILED.format(why=first_sentence(event.get("content")))
                )
        await task
        for channel, piece in splitter.finish():
            if channel == "spoken":
                await self.session.speak(piece)
        if splitter.missing and answer:
            await self.session.speak(MISSING_SPOKEN_LINE)
        await self.session.speak_end()
        detail = without_tags(answer)
        self._history.append((text, detail))
        if self._save is not None:
            self._save(text, answer)

    async def _wait_answer(self) -> Optional[bool]:
        """Yes or no, by voice or keypad; None on timeout or hang-up."""
        while not self._digits.empty():
            self._digits.get_nowait()
        deadline = self._clock() + ANSWER_WAIT_SECONDS
        while self._clock() < deadline:
            digit_task = asyncio.create_task(self._digits.get())
            final_task = asyncio.create_task(self._finals.get())
            done, pending = await asyncio.wait(
                {digit_task, final_task},
                timeout=deadline - self._clock(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            if not done:
                return None
            got = done.pop().result()
            if got == "\x00hangup":
                return None
            if got == "1" or _YES.search(got):
                return True
            if got == "2" or _NO.search(got):
                return False
        return None

    async def _approve(self, event: dict, answerer: Dict[str, Callable]) -> None:
        what = first_sentence(event.get("summary") or event.get("tool") or "go ahead")
        await self._say(APPROVAL.format(what=what.rstrip(".")))
        decision = await self._wait_answer()
        if decision is None:
            await self._say(APPROVAL_TIMEOUT)
        await answerer["approve"](bool(decision), str(event.get("confirm_id") or ""))

    async def _answer_question(
        self, event: dict, answerer: Dict[str, Callable]
    ) -> None:
        await self._say(first_sentence(event.get("question") or "", 300))
        try:
            reply = await asyncio.wait_for(self._finals.get(), ANSWER_WAIT_SECONDS)
        except asyncio.TimeoutError:
            reply = ""
        if reply == "\x00hangup":
            reply = ""
        await answerer["answer"](str(event.get("request_id") or ""), reply)


def mulaw_silence(ms: int) -> str:
    """Base64 µ-law silence, for tests and keep-alives."""
    return base64.b64encode(b"\xff" * (pcm.PHONE_RATE * ms // 1000)).decode()
