# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Talking on a call: PIN, one-time call tokens, incoming calls, and the call
conversation with approvals by voice or keypad."""

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime

import pytest

from gaia.phone.service import PhoneService, _twilio_signature_ok
from gaia.phone.store import PhoneStore
from gaia.ui.phone_talk import PhoneCall, mulaw_silence

ME = "+15551230001"
WSS = "wss://abc.ngrok-free.app/api/phone/stream"


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 3, 12, 0, 0).astimezone().timestamp()

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Secrets(dict):
    def get(self, name):
        return super().get(name)

    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        self.pop(name, None)


@pytest.fixture
def talk(tmp_path):
    clock = Clock()
    service = PhoneService(PhoneStore(tmp_path / "t.db"), Secrets(), clock=clock)
    service.use_simulated()
    yield service, clock
    service.store.close()


def _last_code(service):
    import re

    for call in reversed(service.simulated.calls):
        m = re.search(r"code is ([\d, ]+)\.", call["script"])
        if m:
            return m.group(1).replace(", ", "")
    raise AssertionError("no code")


async def _ready(service):
    started = await service.start_add(ME, "Me")
    await service.confirm_add(started["verification_id"], _last_code(service))
    service.update_settings({"enabled": True})
    service.set_pin("4321")
    service.set_talk_endpoint(WSS)


def _token(service):
    return service.simulated.calls[-1]["script"].split('value="')[1].split('"')[0]


# ---------------------------------------------------------------- service


async def test_alert_call_connects_to_the_pc_only_with_pin_and_address(talk):
    service, _ = talk
    started = await service.start_add(ME, "Me")
    await service.confirm_add(started["verification_id"], _last_code(service))
    service.update_settings({"enabled": True})
    service.raise_alert("test")
    await service.tick()
    assert "<Connect>" not in service.simulated.calls[-1]["script"]  # no PIN yet
    service.set_pin("4321")
    service.set_talk_endpoint(WSS)
    service.raise_alert("test")
    await service.tick()
    script = service.simulated.calls[-1]["script"]
    assert f'<Stream url="{WSS}">' in script and 'name="token"' in script


async def test_call_tokens_work_once_and_expire(talk):
    service, clock = talk
    await _ready(service)
    service.raise_alert("test")
    await service.tick()
    token = _token(service)
    claim = service.claim_talk(token)
    assert claim["voice"] == "af_nicole" and claim["incoming"] is False
    with pytest.raises(Exception):
        service.claim_talk(token)  # one-time
    service.raise_alert("test")
    await service.tick()
    token2 = _token(service)
    clock.advance(901)
    with pytest.raises(Exception):
        service.claim_talk(token2)


async def test_pin_three_tries_and_the_pin_is_never_returned(talk):
    service, _ = talk
    await _ready(service)
    public = service.public_settings()
    assert "pin" not in public and public["pin_set"] is True
    service.raise_alert("test")
    await service.tick()
    call = service.claim_talk(_token(service))["call_id"]
    assert service.check_pin(call, "0000") == {"ok": False, "tries_left": 2}
    assert service.check_pin(call, "1111")["tries_left"] == 1
    assert service.check_pin(call, "2222")["tries_left"] == 0
    assert service.check_pin(call, "4321")["ok"] is False  # locked
    with pytest.raises(Exception):
        service.set_pin("12")


async def test_right_pin_stops_further_calls_for_the_alert(talk):
    service, _ = talk
    await _ready(service)
    service.raise_alert("test")
    await service.tick()
    call = service.claim_talk(_token(service))["call_id"]
    assert service.check_pin(call, "4321")["ok"] is True
    assert service.alerts(open_only=True)[0]["reached"] is True


def _signed(token, url, params):
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_twilio_signature_matches_their_published_example():
    # Twilio's documented example request and its expected signature.
    url = "https://example.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675310",
        "Digits": "1234",
        "From": "+14158675310",
        "To": "+18005551212",
    }
    assert _twilio_signature_ok("12345", url, params, "L/OH5YylLD5NRKLltdqwSvS0BnU=")
    assert not _twilio_signature_ok("12345", url, params, "forged")
    assert not _twilio_signature_ok("12345", url, params, "")


async def test_incoming_calls_need_a_signature_and_a_known_caller(talk, monkeypatch):
    from gaia.phone.provider import TwilioProvider

    service, _ = talk
    await _ready(service)
    monkeypatch.setattr(TwilioProvider, "check", lambda self, n: None)
    monkeypatch.setattr(TwilioProvider, "set_voice_url", lambda self, n, u: None)
    service.set_credentials("AC" + "0" * 32, "tok", "+15550001111")
    service.store.add_contact(ME, "Me", verified_by="twilio")
    service.update_settings({"enabled": True})
    service.set_talk_endpoint(WSS)
    url = "https://abc.ngrok-free.app/api/phone/incoming"
    good = {"From": ME, "To": "+15550001111"}
    assert "<Connect>" in service.incoming_call(url, good, _signed("tok", url, good))
    assert "<Reject/>" in service.incoming_call(url, good, "forged")
    stranger = {"From": "+15559990000", "To": "+15550001111"}
    assert "<Reject/>" in service.incoming_call(
        url, stranger, _signed("tok", url, stranger)
    )


async def test_publishing_the_address_points_the_twilio_number_at_the_pc(
    talk, monkeypatch
):
    from gaia.phone.provider import TwilioProvider

    service, _ = talk
    pointed = []
    monkeypatch.setattr(TwilioProvider, "check", lambda self, n: None)
    monkeypatch.setattr(
        TwilioProvider, "set_voice_url", lambda self, n, u: pointed.append((n, u))
    )
    service.set_credentials("AC" + "0" * 32, "tok", "+15550001111")
    service.set_talk_endpoint(WSS)
    service.set_talk_endpoint(WSS)  # unchanged: no second update
    assert pointed == [
        ("+15550001111", "https://abc.ngrok-free.app/api/phone/incoming")
    ]


# ---------------------------------------------------------------- the call


class FakeVoice:
    def __init__(self):
        self.said = []
        self.speaking = False
        self.emit = None

    async def start(self):
        pass

    async def close(self):
        pass

    async def feed_mic(self, frame):
        pass

    async def speak(self, text, kind="spoken"):
        self.said.append(text)

    async def speak_end(self):
        pass

    async def interrupt(self):
        pass


class Line:
    """Twilio's side of the socket."""

    def __init__(self):
        self.inbox = asyncio.Queue()
        self.sent = []

    async def receive(self):
        return await self.inbox.get()

    async def send(self, message):
        self.sent.append(message)

    def push(self, event):
        self.inbox.put_nowait(json.dumps(event))

    def digits(self, text):
        for d in text:
            self.push({"event": "dtmf", "dtmf": {"digit": d}})

    def start(self):
        self.push(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {"customParameters": {"token": "t" * 20}},
            }
        )


def _call(turn_events=None, approvals=None):
    line = Line()
    voice = FakeVoice()
    pins = []

    async def daemon(method, path, body):
        if path == "/talk/claim":
            return {"call_id": "c" * 32, "voice": "af_nicole", "incoming": False}
        if path == "/talk/pin":
            pins.append(body["pin"])
            ok = body["pin"] == "4321"
            return {"ok": ok, "tries_left": 0 if not ok and len(pins) >= 3 else 2}
        raise AssertionError(path)

    def make_session(*, sink, voice: str, emit):
        voice_obj.emit = emit
        return voice_obj

    voice_obj = voice

    async def run_turn(text, history, events, answerer):
        async def approve(allowed, confirm_id):
            approvals.append((allowed, confirm_id))

        async def answer(rid, value):
            pass

        answerer.update(approve=approve, answer=answer)
        for e in turn_events or []:
            await events.put(e)
            await asyncio.sleep(0)
        await events.put(None)

    saved = []
    call = PhoneCall(
        receive=line.receive,
        send=line.send,
        daemon=daemon,
        make_session=make_session,
        run_turn=run_turn,
        save=lambda said, ans: saved.append((said, ans)),
    )
    return call, line, voice, pins, saved


async def test_nothing_is_said_or_run_before_the_pin():
    call, line, voice, pins, saved = _call(approvals=[])
    line.start()
    line.push({"event": "media", "media": {"payload": mulaw_silence(20)}})
    task = asyncio.create_task(call.run())
    await asyncio.sleep(0.05)
    await voice.emit({"type": "final", "text": "read me my email"})  # before the PIN
    for pin in ("1111#", "2222#", "3333#"):
        line.digits(pin)
        await asyncio.sleep(0.05)
    await asyncio.wait_for(task, 10)
    assert pins == ["1111", "2222", "3333"] and saved == []
    assert voice.said[0].startswith("Please enter your PIN")
    assert voice.said[-1].startswith("That PIN isn't right. Goodbye")


async def test_a_turn_speaks_the_summary_and_approves_by_keypad():
    approvals = []
    events = [
        {"type": "chunk", "content": "Let me check the disk."},
        {"type": "tool_start", "tool": "run_shell_command"},
        {"type": "commentary", "content": "Let me check the disk."},
        {
            "type": "permission_request",
            "tool": "run_shell_command",
            "confirm_id": "k1",
            "summary": "Run df -h to check free space",
        },
        {"type": "chunk", "content": "<spoken>Your drive has 12 GB free.</spoken>\n"},
        {"type": "answer", "content": "<spoken>Your drive has 12 GB free.</spoken>\n"},
    ]
    call, line, voice, pins, saved = _call(turn_events=events, approvals=approvals)
    line.start()
    task = asyncio.create_task(call.run())
    await asyncio.sleep(0.05)
    line.digits("4321#")
    await asyncio.sleep(0.05)
    await voice.emit({"type": "final", "text": "how much space is left on my drive"})
    await asyncio.sleep(0.05)
    line.digits("1")  # allow
    await asyncio.sleep(0.1)
    await voice.emit({"type": "final", "text": "bye"})
    await asyncio.wait_for(task, 10)
    assert approvals == [(True, "k1")]
    assert any("I need your OK to Run df -h" in s for s in voice.said)
    assert "Your drive has 12 GB free." in voice.said
    assert saved and saved[0][0] == "how much space is left on my drive"
    assert voice.said[-1].startswith("Okay, talk soon")


async def test_saying_no_denies_the_command():
    approvals = []
    events = [
        {
            "type": "permission_request",
            "confirm_id": "k2",
            "summary": "Delete old logs",
        },
        {"type": "answer", "content": "<spoken>Okay, I left them.</spoken>"},
    ]
    call, line, voice, *_ = _call(turn_events=events, approvals=approvals)
    line.start()
    task = asyncio.create_task(call.run())
    await asyncio.sleep(0.05)
    line.digits("4321#")
    await asyncio.sleep(0.05)
    await voice.emit({"type": "final", "text": "clean up my logs"})
    await asyncio.sleep(0.05)
    await voice.emit({"type": "final", "text": "no, leave them"})
    await asyncio.sleep(0.1)
    line.push({"event": "stop"})
    await asyncio.wait_for(task, 10)
    assert approvals == [(False, "k2")]


async def test_unknown_call_token_says_nothing():
    line = Line()

    async def daemon(method, path, body):
        raise RuntimeError("That call isn't known or has expired.")

    def no_voice(**_k):
        raise AssertionError("no voice for an unknown call")

    call = PhoneCall(
        receive=line.receive,
        send=line.send,
        daemon=daemon,
        make_session=no_voice,
        run_turn=None,
    )
    line.start()
    await asyncio.wait_for(call.run(), 5)
    assert call.ended_reason == "unknown call" and line.sent == []
