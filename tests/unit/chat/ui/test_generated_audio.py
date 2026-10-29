# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A generated WAV reaches the chat as a playable card, live and after reload.

The flagship runs as a sidecar, so the card has to cross the canonical /query
translation and the host relay, then survive persistence. Anything that is not
a successful music/SFX result with an absolute .wav path must not become one.
"""

import asyncio
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
import gaia.ui.email_sidecar.daemon_client as daemon_client_module
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import GAIA_PROFILE
from gaia.ui.event_narration import derive_narration
from gaia.ui.models import AgentStepResponse, ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator
from gaia.ui.turn_summary import is_openable
from gaia.ui.utils import message_to_response

WAV = "C:/Users/me/.gaia/cache/audio/generations/music_1.wav"
SUCCESS = {
    "status": "success",
    "audio_path": WAV,
    "model": "ACE-Step-Music",
    "type": "music",
    "format": "wav",
    "prompt": "lo-fi piano, rainy mood",
    "duration_requested": 12.0,
    "duration_seconds": 12.04,
}


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _result_event(tool, data, args=None):
    handler = SSEOutputHandler()
    handler.print_tool_usage(tool)
    if args is not None:
        handler.pretty_print_json(args, title="Arguments")
    _drain(handler)
    handler.pretty_print_json(data, title="Result")
    return next(e for e in _drain(handler) if e["type"] == "tool_result")


def test_music_result_carries_the_card_and_a_plain_summary():
    event = _result_event("generate_music", SUCCESS)
    assert event["generated_audio"] == {
        "path": WAV,
        "model": "ACE-Step-Music",
        "kind": "music",
        "format": "wav",
        "duration_seconds": 12.04,
        "duration_requested": 12.0,
        "prompt": "lo-fi piano, rainy mood",
    }
    assert event["audio_path"] == WAV
    assert event["summary"] == "Saved music_1.wav (12.0 s)"


def test_sound_effect_is_labelled_sfx():
    data = {**SUCCESS, "type": "sfx", "model": "ThinkSound-SFX"}
    assert _result_event("generate_sound_effect", data)["generated_audio"]["kind"] == (
        "sfx"
    )


@pytest.mark.parametrize(
    "tool, data",
    [
        ("generate_music", {"status": "error", "error": "Lemonade 503"}),
        ("generate_music", {**SUCCESS, "status": "error"}),
        ("generate_music", {**SUCCESS, "audio_path": "C:/x/song.mp3"}),
        ("generate_music", {**SUCCESS, "audio_path": "C:/x/run.exe"}),
        ("generate_music", {**SUCCESS, "audio_path": "relative/song.wav"}),
        ("generate_music", {**SUCCESS, "audio_path": None}),
        ("generate_music", "Saved somewhere.wav"),
        # Another tool reporting an audio_path is not a generated track.
        ("text_to_speech", SUCCESS),
        ("write_file", SUCCESS),
    ],
)
def test_anything_but_a_successful_wav_gets_no_card(tool, data):
    assert "generated_audio" not in _result_event(tool, data)


@pytest.mark.parametrize("bogus", [float("nan"), float("inf"), -3.0, 0, True, "12"])
def test_bogus_durations_are_dropped_not_forwarded(bogus):
    data = {**SUCCESS, "duration_seconds": bogus, "duration_requested": bogus}
    card = _result_event("generate_music", data)["generated_audio"]
    assert card["duration_seconds"] is None
    assert card["duration_requested"] is None


def test_card_crosses_the_canonical_translation_and_the_relay():
    sidecar = SSEOutputHandler()
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia")
    sidecar.print_tool_usage("generate_music")
    sidecar.pretty_print_json({"prompt": "lo-fi piano", "duration": 12}, "Arguments")
    sidecar.pretty_print_json(SUCCESS, title="Result")
    canonical = [c for e in _drain(sidecar) for c in translator.translate(e)]
    canonical += translator.flush()
    result = next(c for c in canonical if c["type"] == "tool_result")
    assert result["data"]["generated_audio"]["path"] == WAV

    host = SSEOutputHandler()
    for event in canonical:
        relay._dispatch_one(host, event, profile=GAIA_PROFILE)
    relayed = next(e for e in _drain(host) if e["type"] == "tool_result")
    assert relayed["generated_audio"]["model"] == "ACE-Step-Music"
    assert relayed["summary"] == "Saved music_1.wav (12.0 s)"
    outputs = host.turn_ledger.summary(location=None)["outputs"]
    assert [(o["kind"], o["name"]) for o in outputs] == [("audio", "music_1.wav")]


def test_saved_step_keeps_the_card():
    step = AgentStepResponse(
        id=1,
        type="tool",
        label="Composing music",
        tool="generate_music",
        generatedAudio={
            "path": WAV,
            "model": "ACE-Step-Music",
            "kind": "music",
            "durationSeconds": 12.04,
            "prompt": "lo-fi piano",
        },
    )
    saved = step.model_dump()
    assert saved["generatedAudio"]["path"] == WAV
    assert saved["generatedAudio"]["format"] == "wav"


class _Proxy:
    def __init__(self, events):
        self._events = list(events)

    def init(self):
        return 200, {}

    def query_stream(self, body, *, read_timeout=300.0, on_response=None):
        if on_response is not None:
            on_response(SimpleNamespace(close=lambda: None))
        return iter(self._events)

    def cancel_query(self, run_id):  # pragma: no cover - not hit
        return None


class _Handle:
    api_version = "%d.%d" % GAIA_PROFILE.min_api_version

    def __init__(self, events):
        self._proxy = _Proxy(events)

    def proxy(self):
        return self._proxy


async def _no_retitle(**kwargs):
    return None


def _relayed_steps(monkeypatch, card):
    """Run one relayed flagship turn whose result carries *card*; return its saved steps."""
    canonical = [
        {
            "type": "tool_call",
            "tool": "generate_music",
            "args": {"prompt": "lo-fi piano", "duration": 12},
        },
        {
            "type": "tool_result",
            "tool": "generate_music",
            "data": {
                "summary": "Saved music_1.wav (12.0 s)",
                "success": True,
                "status": "success",
                "audio_path": WAV,
                "generated_audio": card,
            },
        },
        {"type": "final", "answer": "Here is your track."},
    ]
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        lambda agent_id="gaia": _Handle(canonical),
    )
    monkeypatch.setattr(chat_helpers_module, "_maybe_update_session_title", _no_retitle)

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")
    request = ChatRequest(
        session_id=session["id"], message="make music", stream=True, agent_type="gaia"
    )

    async def _drive():
        async for _ in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    asyncio.run(_drive())

    (assistant,) = [
        m for m in db.get_messages(session["id"]) if m["role"] == "assistant"
    ]
    return message_to_response(assistant).agent_steps


def test_a_relayed_turn_persists_the_card_for_reload(monkeypatch):
    steps = _relayed_steps(
        monkeypatch,
        {
            "path": WAV,
            "model": "ACE-Step-Music",
            "kind": "music",
            "format": "wav",
            "duration_seconds": 12.04,
            "duration_requested": 12.0,
            "prompt": "lo-fi piano",
        },
    )
    (tool,) = [s for s in steps if s.tool == "generate_music"]
    assert tool.generatedAudio is not None
    assert tool.generatedAudio.path == WAV
    assert tool.generatedAudio.durationSeconds == 12.04
    assert tool.generatedAudio.prompt == "lo-fi piano"


@pytest.mark.parametrize("card", [{"model": "x"}, {"path": None}, {"path": 7}])
def test_a_pathless_relayed_card_is_not_saved_and_the_turn_still_loads(
    monkeypatch, card
):
    steps = _relayed_steps(monkeypatch, card)
    (tool,) = [s for s in steps if s.tool == "generate_music"]
    assert tool.generatedAudio is None
    assert tool.result == "Saved music_1.wav (12.0 s)"


@pytest.mark.parametrize(
    "tool, args, expected",
    [
        (
            "generate_music",
            {"duration": 12, "prompt": "lo-fi"},
            "Composing 12 s of music: lo-fi",
        ),
        ("generate_music", {"duration": 2.5}, "Composing 2.5 s of music"),
        ("generate_music", {}, "Composing music"),
        ("generate_sound_effect", {"duration": 3}, "Generating a 3 s sound effect"),
        (
            "generate_sound_effect",
            {"prompt": "door"},
            "Generating a sound effect: door",
        ),
        (
            "list_audio_generation_models",
            {},
            "Checking which audio models are downloaded",
        ),
    ],
)
def test_narration_says_what_is_being_made(tool, args, expected):
    assert derive_narration(tool, args) == expected


@pytest.mark.parametrize("bogus", [0, -4, float("nan"), float("inf"), 1e9, True, "12"])
def test_narration_drops_a_bogus_duration(bogus):
    assert derive_narration("generate_music", {"duration": bogus}) == "Composing music"


def test_narration_clips_a_long_prompt_to_one_line():
    line = derive_narration("generate_music", {"prompt": "piano\n" * 200})
    assert "\n" not in line and len(line) < 120


def test_generated_wav_can_be_opened_from_the_outputs_rail():
    assert is_openable("C:/Users/me/music_1.WAV")
    assert not is_openable("C:/Users/me/music_1.wav.exe")


def test_a_failed_generation_is_not_an_output():
    from gaia.ui.turn_summary import TurnLedger

    ledger = TurnLedger()
    ledger.tool_started("generate_music")
    ledger.tool_args({"prompt": "x"})
    ledger.tool_result({"status": "error", "error": "Lemonade 503"}, False)
    ledger.tool_started("generate_sound_effect")
    ledger.tool_args({"prompt": "door"})
    ledger.tool_result({**SUCCESS, "type": "sfx"}, True)
    outputs = ledger.summary(location=None)["outputs"]
    assert [(o["kind"], o["name"], o["detail"]) for o in outputs] == [
        ("audio", "music_1.wav", "door")
    ]
