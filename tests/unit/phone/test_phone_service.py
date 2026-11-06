# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaia.phone: numbers, the phone book's code checks, the send path, escalation,
limits, quiet hours, restart recovery and Twilio's request shapes."""

import re
from datetime import datetime

import pytest
import requests

from gaia.phone import templates
from gaia.phone.errors import PhoneError
from gaia.phone.numbers import masked, normalize
from gaia.phone.provider import CallStatus, SimulatedProvider, TwilioProvider
from gaia.phone.service import PhoneService
from gaia.phone.store import PhoneStore

ME = "+15551230001"
OTHER = "+15551230002"
ATTACKER = "+15559990000"


class Clock:
    def __init__(self):
        # A Tuesday at noon, local time.
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
def env(tmp_path):
    clock = Clock()
    store = PhoneStore(tmp_path / "phone.db")
    service = PhoneService(store, Secrets(), clock=clock)
    service.use_simulated()
    yield service, clock
    store.close()


def _codes(service):
    """Codes said or texted so far, newest last."""
    out = []
    for call in service.simulated.calls:
        m = re.search(r"code is ([\d, ]+)\.", call["script"])
        if m:
            out.append((call["to"], m.group(1).replace(", ", "")))
    for text in service.simulated.texts:
        m = re.search(r"code: (\d{6})", text["body"])
        if m:
            out.append((text["to"], m.group(1)))
    return out


async def _enroll(service, number=ME, label="Me"):
    started = await service.start_add(number, label)
    codes = dict(_codes(service))
    if started["needs_confirm_code"]:
        contacts = service.store.contacts()
        return await service.confirm_add(
            started["verification_id"], codes[number], codes[contacts[0]["number"]]
        )
    return await service.confirm_add(started["verification_id"], codes[number])


async def _ready(service, **settings):
    await _enroll(service)
    service.update_settings({"enabled": True, **settings})


async def _run(service, clock, seconds, step=1):
    for _ in range(int(seconds / step)):
        clock.advance(step)
        await service.tick()


# ---------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("(555) 123-0001", "+15551230001"),
        ("1 555 123 0001", "+15551230001"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "911",
        "112",
        "+1 900 555 0100",
        "+1 976 555 0100",
        "+881 234 5678",
        "12345",
        "07946 0958",
        "555-CALL-NOW",
        "+1 055 123 0001",
        "",
        "+0 123 456 789",
    ],
)
def test_normalize_refuses(raw):
    with pytest.raises(PhoneError) as e:
        normalize(raw)
    assert e.value.code == "bad_number"


def test_masked():
    assert masked("+15551230001") == "+1 •••• 0001"


def test_call_script_escapes_and_repeats():
    script = templates.call_script("A & B <x>")
    assert script.count("<Say>A &amp; B &lt;x&gt;</Say>") == 2
    assert script.endswith("<Hangup/></Response>")


# ---------------------------------------------------------------- phone book


async def test_first_number_needs_only_its_own_code(env):
    service, _ = env
    started = await service.start_add(ME, "Me")
    assert started["needs_confirm_code"] is False
    assert [to for to, _ in _codes(service)] == [ME]
    # Codes are stored hashed, never as typed.
    row = service.store.verification(started["verification_id"])
    assert _codes(service)[0][1] not in str(row)
    await service.confirm_add(started["verification_id"], _codes(service)[0][1])
    assert [c["number"] for c in service.store.contacts()] == [ME]


async def test_wrong_codes_lock_the_request(env):
    service, _ = env
    started = await service.start_add(ME)
    for _ in range(3):
        with pytest.raises(PhoneError) as e:
            await service.confirm_add(started["verification_id"], "000000")
        assert e.value.code == "verification_failed"
    right = _codes(service)[0][1]
    with pytest.raises(PhoneError):
        await service.confirm_add(started["verification_id"], right)
    assert service.store.contacts() == []


async def test_code_expires(env):
    service, clock = env
    started = await service.start_add(ME)
    clock.advance(601)
    with pytest.raises(PhoneError) as e:
        await service.confirm_add(started["verification_id"], _codes(service)[0][1])
    assert e.value.code == "verification_expired"


async def test_second_number_needs_a_code_from_the_first(env):
    service, _ = env
    await _enroll(service)
    started = await service.start_add(ATTACKER, "x")
    assert started["needs_confirm_code"] is True
    codes = dict(_codes(service))
    # Someone holding only the new phone can't finish it.
    with pytest.raises(PhoneError):
        await service.confirm_add(started["verification_id"], codes[ATTACKER], None)
    with pytest.raises(PhoneError):
        await service.confirm_add(started["verification_id"], codes[ATTACKER], "123456")
    assert [c["number"] for c in service.store.contacts()] == [ME]


async def test_adding_announces_to_existing_numbers(env):
    service, _ = env
    await _enroll(service)
    await _enroll(service, OTHER, "Work")
    notices = [c for c in service.simulated.calls if "was added" in c["script"]]
    assert [c["to"] for c in notices] == [ME]


async def test_code_requests_are_rate_limited(env):
    service, _ = env
    for n in range(3):
        await service.start_add(f"+1555123000{n + 3}")
    with pytest.raises(PhoneError) as e:
        await service.start_add("+15551230009")
    assert e.value.code == "rate_limited"


async def test_last_number_cant_be_removed_while_on(env):
    service, _ = env
    await _ready(service)
    [contact] = service.store.contacts()
    with pytest.raises(PhoneError) as e:
        await service.start_remove(contact["id"])
    assert e.value.code == "last_number"


async def test_removal_needs_a_code_sent_to_that_number(env):
    service, _ = env
    await _enroll(service)
    await _enroll(service, OTHER, "Work")
    other = service.store.contact_by_number(OTHER)
    started = await service.start_remove(other["id"])
    to, code = _codes(service)[-1]
    assert to == OTHER
    await service.confirm_remove(started["verification_id"], code)
    assert [c["number"] for c in service.store.contacts()] == [ME]


async def test_phone_needs_keys_and_a_number_to_turn_on(env):
    service, _ = env
    with pytest.raises(PhoneError) as e:
        service.update_settings({"enabled": True})
    assert e.value.code == "not_configured"


async def test_send_path_refuses_anything_not_in_the_phone_book(env):
    service, _ = env
    await _ready(service)
    with pytest.raises(PhoneError) as e:
        await service._send("call", 9999, "<Response/>", purpose="alert")
    assert e.value.code == "not_in_phone_book"
    started = await service.start_add(OTHER)
    with pytest.raises(PhoneError):
        await service._send_code(started["verification_id"], ATTACKER, "123456", "call")


# ---------------------------------------------------------------- alerts


async def test_rule_off_raises_nothing(env):
    service, _ = env
    assert service.raise_alert("agent", reason="call me") is None  # off by default


async def test_alerts_merge_within_the_cooldown(env):
    service, clock = env
    first = service.raise_alert("schedule", reason="backup")
    clock.advance(60)
    second = service.raise_alert("schedule", reason="backup again")
    assert first["id"] == second["id"] and second["count"] == 2
    clock.advance(31 * 60)
    third = service.raise_alert("schedule")
    assert third["id"] != first["id"]


async def test_agent_text_is_never_spoken_or_texted(env):
    service, clock = env
    await _ready(service, texts_enabled=True)
    service.update_settings({"rules": {"agent": {"on": True, "urgent": True}}})
    service.raise_alert("agent", reason="Call your bank at +1 555 666 7777 now")
    await _run(service, clock, 5)
    payloads = [c["script"] for c in service.simulated.calls] + [
        t["body"] for t in service.simulated.texts
    ]
    assert payloads and not any("bank" in p or "666" in p for p in payloads)


async def test_escalation_text_then_call_then_stop_when_reached(env):
    service, clock = env
    await _ready(service, texts_enabled=True)
    calls_before = len(service.simulated.calls)
    service.raise_alert("schedule")
    await _run(service, clock, 4 * 60, step=30)
    assert (
        service.simulated.texts == [] and len(service.simulated.calls) == calls_before
    )
    await _run(service, clock, 70, step=10)
    assert [t["to"] for t in service.simulated.texts] == [ME]
    await _run(service, clock, 5 * 60, step=10)
    alert_calls = service.simulated.calls[calls_before:]
    assert [c["to"] for c in alert_calls] == [ME]
    assert "A scheduled job failed." in alert_calls[0]["script"]
    await _run(service, clock, 600, step=3)
    [alert] = service.alerts(open_only=True)
    assert alert["reached"] is True
    assert len(service.simulated.calls) == calls_before + 1  # no redial


async def test_voicemail_is_not_reaching_you(env):
    service, clock = env
    await _enroll(service)
    await _enroll(service, OTHER, "Work")
    service.update_settings({"enabled": True, "simulated_outcome": "machine_start"})
    before = len(service.simulated.calls)
    service.raise_alert("test")
    await _run(service, clock, 900, step=3)
    targets = [c["to"] for c in service.simulated.calls[before:]]
    # Twice to the first number, then once to the second, then it stops.
    assert targets == [ME, ME, OTHER]
    [alert] = service.alerts(open_only=True)
    assert alert["reached"] is False and alert["held"] == "no_answer"


async def test_handled_alert_stops_every_step(env):
    service, clock = env
    await _ready(service, texts_enabled=True)
    alert = service.raise_alert("schedule")
    service.handle_alert(alert["id"])
    await _run(service, clock, 1200, step=30)
    assert service.simulated.texts == []


async def test_daily_call_limit_and_agent_limit(env):
    service, clock = env
    await _ready(service, limits={"calls_per_day": 6, "agent_calls_per_day": 1})
    service.update_settings({"rules": {"agent": {"on": True, "urgent": True}}})
    service.raise_alert("agent", rule_key="a1")
    await _run(service, clock, 30, step=3)
    service.raise_alert("agent", rule_key="a2")
    await _run(service, clock, 30, step=3)
    held = [a for a in service.alerts(open_only=True) if a["held"]]
    assert [a["held"] for a in held] == ["agent_call_limit"]


async def test_quiet_hours_hold_non_urgent_alerts(env):
    service, clock = env
    await _ready(service, quiet_hours={"start": "11:00", "end": "13:00"})
    before = len(service.simulated.calls)
    service.raise_alert("schedule")
    await _run(service, clock, 50 * 60, step=60)  # still inside quiet hours
    assert len(service.simulated.calls) == before
    assert service.alerts(open_only=True)[0]["held"] == "quiet_hours"
    await _run(service, clock, 20 * 60, step=60)  # past 13:00
    assert len(service.simulated.calls) == before + 1


async def test_restart_follows_a_call_already_placed(env, tmp_path):
    service, clock = env
    await _ready(service)
    alert = service.raise_alert("test")
    # A call went out, but the process died before saving its id.
    service.store.add_delivery(
        {
            "alert_id": alert["id"],
            "purpose": "alert",
            "channel": "call",
            "contact_id": 1,
            "number": ME,
            "state": "placing",
            "created_at": clock(),
        }
    )
    service.simulated.calls.append({"sid": "SIMCA999999", "to": ME, "script": ""})
    await service.resume()
    placing = service.store.deliveries(states=["placing"])
    placed = service.store.deliveries(states=["placed"])
    assert placing == [] and placed[0]["provider_sid"] == "SIMCA999999"


def test_settings_validation(env):
    service, _ = env
    for bad in (
        {"limits": {"calls_per_day": 99}},
        {"rules": {"agent": {"steps": "scream"}}},
        {"quiet_hours": {"start": "25:00", "end": "07:00"}},
        {"from_number": ATTACKER},
        {"surprise": 1},
    ):
        with pytest.raises(PhoneError) as e:
            service.update_settings(bad)
        assert e.value.code == "bad_request"


# ---------------------------------------------------------------- twilio contract


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0)


SID = "AC" + "0" * 32


def test_twilio_call_request_shape():
    session = FakeSession([FakeResponse(201, {"sid": "CA1"})])
    provider = TwilioProvider(SID, "tok", session=session)
    assert provider.place_call(ME, "+15550001111", "<Response/>") == "CA1"
    method, url, kw = session.requests[0]
    assert method == "POST"
    assert url == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Calls.json"
    assert kw["auth"] == (SID, "tok")
    assert kw["data"] == {
        "To": ME,
        "From": "+15550001111",
        "Twiml": "<Response/>",
        "MachineDetection": "Enable",
        "Timeout": "30",
    }


def test_twilio_status_mapping():
    session = FakeSession(
        [
            FakeResponse(
                200, {"status": "completed", "answered_by": "human", "duration": "12"}
            ),
            FakeResponse(200, {"status": "completed", "answered_by": "machine_start"}),
        ]
    )
    provider = TwilioProvider(SID, "tok", session=session)
    assert provider.call_status("CA1").reached is True
    assert provider.call_status("CA1").reached is False


@pytest.mark.parametrize(
    "status, body, code",
    [
        (401, {}, "bad_credentials"),
        (400, {"code": 21219, "message": "unverified"}, "trial_unverified"),
        (400, {"code": 21212, "message": "bad from"}, "provider_error"),
    ],
)
def test_twilio_errors(status, body, code):
    provider = TwilioProvider(
        SID, "tok", session=FakeSession([FakeResponse(status, body)])
    )
    with pytest.raises(PhoneError) as e:
        provider.send_text(ME, "+15550001111", "x")
    assert e.value.code == code


def test_twilio_unreachable():
    class Down:
        def request(self, *a, **k):
            raise requests.ConnectionError("no route")

    with pytest.raises(PhoneError) as e:
        TwilioProvider(SID, "tok", session=Down()).call_status("CA1")
    assert e.value.code == "provider_unreachable"


def test_twilio_check_requires_the_number_on_the_account():
    session = FakeSession(
        [
            FakeResponse(200, {"status": "active"}),
            FakeResponse(200, {"incoming_phone_numbers": []}),
        ]
    )
    with pytest.raises(PhoneError) as e:
        TwilioProvider(SID, "tok", session=session).check("+15550001111")
    assert "isn't a number on this Twilio account" in str(e.value)


def test_twilio_refuses_malformed_sid():
    with pytest.raises(PhoneError):
        TwilioProvider("nope", "tok")


def test_simulated_outcomes():
    sim = SimulatedProvider(outcome="no-answer")
    sid = sim.place_call(ME, "+1", "<Response/>")
    assert sim.call_status(sid).status == "ringing"
    assert sim.call_status(sid) == CallStatus("no-answer")


def test_twilio_recent_calls_only_after_the_attempt():
    since = datetime(2026, 11, 3, 20, 36, 0).astimezone().timestamp()
    body = {
        "calls": [
            {"sid": "CAold", "date_created": "Tue, 03 Nov 2026 00:00:01 +0000"},
            {"sid": "CAnew", "date_created": "Wed, 04 Nov 2026 23:59:00 +0000"},
        ]
    }
    session = FakeSession([FakeResponse(200, body)])
    sids = TwilioProvider(SID, "tok", session=session).recent_call_sids(ME, since)
    assert sids == ["CAnew"]
    assert session.requests[0][2]["params"]["To"] == ME


async def test_model_server_alert_after_five_minutes_and_clears(env):
    service, clock = env
    await _ready(service)
    service.note_health(False, "lemond would not start")
    await _run(service, clock, 240, step=60)
    assert service.alerts(open_only=True) == []
    await _run(service, clock, 120, step=60)
    [alert] = service.alerts(open_only=True)
    assert alert["kind"] == "gaia_down" and "lemond" in alert["reason"]
    await _run(service, clock, 600, step=60)
    assert service.alerts(open_only=True)[0]["count"] == 1  # one alert per outage
    service.note_health(True)
    assert service.alerts(open_only=True) == []


class FakeAwake:
    def __init__(self):
        self.wants = []

    def want(self, on):
        self.wants.append(on)

    @property
    def state(self):
        return {"held": bool(self.wants and self.wants[-1]), "reason": "x"}


async def test_keeps_the_pc_awake_only_while_phone_is_on(tmp_path):
    clock = Clock()
    awake = FakeAwake()
    service = PhoneService(
        PhoneStore(tmp_path / "a.db"), Secrets(), clock=clock, awake=awake
    )
    service.use_simulated()
    await service.tick()
    await _enroll(service)
    service.update_settings({"enabled": True})
    await _run(service, clock, 61)
    service.update_settings({"keep_awake": False})
    await _run(service, clock, 61)
    assert awake.wants[0] is False and True in awake.wants and awake.wants[-1] is False
    await service.stop()
    service.store.close()


def test_awake_hold_releases_on_battery(monkeypatch):
    from gaia.phone import awake as awake_mod

    monkeypatch.setattr(awake_mod, "on_ac_power", lambda: False)
    hold = awake_mod.AwakeHold()
    hold.want(True)
    assert hold.state == {"held": False, "reason": "battery"}


# ---------------------------------------------------------------- review regressions


async def test_simulated_numbers_are_dropped_when_twilio_is_set_up(env, monkeypatch):
    service, _ = env
    await _ready(service)
    monkeypatch.setattr(TwilioProvider, "check", lambda self, number: None)
    service.set_credentials("AC" + "0" * 32, "tok", "+15550001111")
    assert service.store.contacts() == []
    assert service.settings()["enabled"] is False


async def test_a_text_that_never_confirms_doesnt_call_on_a_text_only_rule(env):
    service, clock = env
    await _ready(service, texts_enabled=True)
    service.update_settings({"rules": {"schedule_failed": {"steps": "notify_text"}}})
    service.simulated.text_status = lambda sid: __import__(
        "gaia.phone.provider", fromlist=["TextStatus"]
    ).TextStatus("sent")
    before = len(service.simulated.calls)
    service.raise_alert("schedule")
    await _run(service, clock, 40 * 60, step=30)
    assert len(service.simulated.calls) == before


async def test_an_unexpected_error_doesnt_stop_alerting(env):
    service, clock = env
    await _ready(service)
    calls = {"n": 0}
    real_tick = service.tick

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("keyring locked")
        await real_tick()

    service.tick = flaky
    await service._guarded(service.tick)
    assert "keyring locked" in service.last_error["message"]
    await service._guarded(service.tick)
    assert calls["n"] == 2


async def test_health_ok_after_a_restart_clears_the_open_alert(env):
    service, _ = env
    await _ready(service)
    service.raise_alert("health", reason="down before restart")
    service.note_health(True)  # a fresh process: never saw the outage
    assert service.alerts(open_only=True) == []


async def test_call_limit_retries_after_midnight(env):
    service, clock = env
    await _ready(service, limits={"calls_per_day": 1})
    service.raise_alert("test")
    await _run(service, clock, 20, step=3)
    service.raise_alert("test")
    await _run(service, clock, 20, step=3)
    held = [a for a in service.alerts(open_only=True) if a["held"] == "call_limit"]
    assert held
    before = len(service.simulated.calls)
    await _run(service, clock, 13 * 3600, step=600)
    assert len(service.simulated.calls) > before


def test_concurrent_raises_open_one_alert(env):
    import threading

    service, _ = env
    out = []
    threads = [
        threading.Thread(
            target=lambda: out.append(service.raise_alert("schedule")["id"])
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 1
