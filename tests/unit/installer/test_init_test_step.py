# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Test step: every chosen model runs for real, not just downloads."""

import base64
import json
import struct
import threading
import wave
import zlib
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer import init_command, model_choice
from gaia.installer.init_command import (
    ChatModelChoice,
    CheckTimedOut,
    InitCommand,
    InitEvents,
    MemoryShortfall,
    _solid_red_png,
    _test_limit_s,
    _tone_wav,
)
from gaia.ui import setup_plan, setup_runner


class _Events(InitEvents):
    def __init__(self):
        super().__init__(True)
        self.seen = []

    def emit(self, event, **fields):
        self.seen.append({"event": event, **fields})


def _command():
    cmd = InitCommand(profile="gaia", yes=True)
    cmd.events = _Events()
    return cmd


def _pixels(png: bytes):
    """The decoded rows of a PNG, read by hand so the test checks the real format."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    at, idat, header = 8, b"", None
    while at < len(png):
        length = struct.unpack(">I", png[at : at + 4])[0]
        tag, data = png[at + 4 : at + 8], png[at + 8 : at + 8 + length]
        crc = struct.unpack(">I", png[at + 8 + length : at + 12 + length])[0]
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif tag == b"IDAT":
            idat += data
        at += 12 + length
    return header, zlib.decompress(idat)


def test_the_test_image_is_a_valid_red_png():
    (width, height, depth, colour, *_), raw = _pixels(_solid_red_png(8))
    assert (width, height, depth, colour) == (8, 8, 8, 2)
    assert len(raw) == 8 * (1 + 8 * 3)
    assert raw[0] == 0 and raw[1:4] == b"\xdc\x14\x14"


def test_the_test_tone_is_16khz_mono(tmp_path):
    path = tmp_path / "tone.wav"
    _tone_wav(str(path), seconds=0.1)
    with wave.open(str(path), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getframerate() == 16000
        assert audio.getnframes() == 1600
        assert any(audio.readframes(1600))


def test_the_vision_test_sends_a_real_image_and_needs_the_right_answer():
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": {"content": "Red"}}]
    }
    assert _command()._test_vision(client, "Gemma-4-E4B-it-GGUF") == (True, None)

    kwargs = client.chat_completions.call_args.kwargs
    assert kwargs["auto_download"] is False
    text, image = kwargs["messages"][0]["content"]
    assert text["type"] == "text"
    prefix, encoded = image["image_url"]["url"].split(",", 1)
    assert image["type"] == "image_url" and prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == _solid_red_png()


@pytest.mark.parametrize(
    "message",
    [
        {"content": "blue"},
        {"content": ""},
    ],
)
def test_a_model_that_answers_wrongly_fails_the_vision_test(message):
    client = MagicMock()
    client.chat_completions.return_value = {"choices": [{"message": message}]}
    ok, error = _command()._test_vision(client, "m")
    assert ok is False and "test image" in error


def test_a_thinking_model_passes_on_its_reasoning():
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": {"reasoning_content": "It looks red."}}]
    }
    assert _command()._test_vision(client, "m")[0] is True


def test_a_stuck_test_stops_at_its_limit():
    release = threading.Event()

    with patch.object(init_command, "_test_limit_s", return_value=0.05):
        ok, error = _command()._within_limit("m", lambda: release.wait(5))
    release.set()
    assert ok is False and isinstance(error, CheckTimedOut)


def test_a_bigger_model_gets_longer():
    assert _test_limit_s("no-such-model") == 300
    assert _test_limit_s("Qwen3.6-35B-A3B-GGUF") >= 300
    assert _test_limit_s("user.Qwen3.8-Flash-Next-GGUF") > 1000


@pytest.mark.parametrize(
    "error, kind",
    [
        ("boom", "broken"),
        (MemoryShortfall("close apps"), "memory"),
        (CheckTimedOut("too slow"), "stuck"),
    ],
)
def test_a_failed_test_says_what_kind_of_failure(error, kind):
    cmd = _command()
    cmd._emit_test("chat", "m", "failed", error, 1.26)
    event = cmd.events.seen[-1]
    assert event["kind"] == kind and event["error"] == str(error)
    assert event["seconds"] == 1.3


def _verify(cmd, client, inference, vision=(True, None)):
    config = MagicMock()
    config.load.return_value.vision_model = None
    with (
        patch.object(cmd, "_test_model_inference", side_effect=inference),
        patch.object(cmd, "_test_vision", return_value=vision),
        patch.object(cmd, "_chat_model", return_value="Gemma-4-E4B-it-GGUF"),
        patch.object(
            cmd,
            "_chat_model_choice",
            return_value=ChatModelChoice(
                model_id="Gemma-4-E4B-it-GGUF", user_set=False, skipped=[]
            ),
        ),
        patch("gaia.config.GaiaConfig", config),
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch(
            "gaia.llm.lemonade_client.fit_ctx_to_free_memory",
            return_value=65536,
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch.object(cmd, "_skip_list", return_value=[]),
    ):
        return cmd._verify_setup()


def _client():
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    client.check_model_available.return_value = True
    return client


def _tests(cmd):
    return [e for e in cmd.events.seen if e["event"] == "test"]


def test_what_the_user_ticked_is_tested_too():
    cmd = _command()
    cmd.extra_models = ["Whisper-Large-v3-Turbo"]
    tested = []
    assert _verify(cmd, _client(), lambda c, m: (tested.append(m) or (True, None)))
    assert "Whisper-Large-v3-Turbo" in tested
    jobs = {(e["job"], e["state"]) for e in _tests(cmd)}
    assert ("speech", "passed") in jobs
    assert ("server", "passed") in jobs
    assert ("vision", "passed") in jobs


def test_an_optional_model_failing_is_reported_but_setup_still_passes():
    cmd = _command()
    cmd.extra_models = ["SDXL-Turbo"]

    def inference(_client, model):
        return (False, "boom") if model == "SDXL-Turbo" else (True, None)

    assert _verify(cmd, _client(), inference) is True
    failed = [e for e in _tests(cmd) if e["state"] == "failed"]
    assert [(e["job"], e["subject"]) for e in failed] == [
        ("image_creation", "SDXL-Turbo")
    ]


def test_a_vision_model_that_can_not_read_an_image_fails_setup():
    cmd = _command()
    assert _verify(cmd, _client(), lambda c, m: (True, None), (False, "wrong")) is False


def test_a_cloud_chat_setup_has_no_vision_test():
    cmd = InitCommand(profile="gaia", yes=True, skip_chat_model=True)
    assert cmd._vision_test_model(MagicMock()) is None


def test_the_runner_keeps_the_test_rows_and_components():
    run = setup_runner.SetupRun(skip_chat_model=False)
    runner = setup_runner.SetupRunner()
    runner._run = run
    runner._on_event({"event": "component", "component": "agent", "state": "active"})
    runner._on_event(
        {"event": "test", "job": "chat", "subject": "m", "state": "running"}
    )
    runner._on_event(
        {
            "event": "test",
            "job": "chat",
            "subject": "m",
            "state": "failed",
            "error": "boom",
            "kind": "broken",
            "seconds": 2.0,
        }
    )
    view = run.view()
    assert view["components"] == {"agent": "active"}
    assert view["tests"] == [
        {
            "job": "chat",
            "subject": "m",
            "state": "failed",
            "error": "boom",
            "kind": "broken",
            "seconds": 2.0,
        }
    ]


def _ran(code, out="", err=""):
    return MagicMock(returncode=code, stdout=out, stderr=err)


def test_a_real_agent_turn_passes_when_gaia_answers():
    with patch.object(
        setup_runner.subprocess, "run", return_value=_ran(0, json.dumps({"ok": 1}))
    ) as run:
        assert setup_runner._agent_turn_problem() is None
    argv = run.call_args.args[0]
    assert "-p" in argv and "--output-format" in argv


@pytest.mark.parametrize("result", [_ran(1, "", "agent crashed"), _ran(0, "   ", "")])
def test_an_agent_turn_that_fails_says_so(result):
    with patch.object(setup_runner.subprocess, "run", return_value=result):
        assert "didn't answer" in setup_runner._agent_turn_problem()


def test_an_agent_turn_that_hangs_says_so():
    with patch.object(
        setup_runner.subprocess,
        "run",
        side_effect=setup_runner.subprocess.TimeoutExpired("gaia", 1),
    ):
        assert "minutes" in setup_runner._agent_turn_problem()


@pytest.mark.parametrize(
    "model, label",
    [
        ("Gemma-4-E4B-it-GGUF", "verified"),
        ("Qwen3.6-35B-A3B-GGUF", "verified"),
        ("gemma4-it-e4b-FLM", "verified"),
        ("user.embeddinggemma-300m-GGUF", "verified"),
        ("user.Qwen3.8-Flash-Next-GGUF", "untested"),
        ("Qwen3-VL-4B-Instruct-GGUF", "untested"),
        ("gemma4-it-e2b-FLM", "untested"),
        ("Whisper-Large-v3-Turbo", None),
    ],
)
def test_support_labels(model, label):
    assert model_choice.support_label(model) == label


def test_optional_models_match_the_defaults_they_stand_for():
    from gaia.audio.lemonade_asr import DEFAULT_ASR_MODEL
    from gaia.audio.lemonade_tts import DEFAULT_TTS_MODEL

    assert model_choice.OPTIONAL_MODELS[DEFAULT_ASR_MODEL] == model_choice.ROLE_SPEECH
    assert model_choice.OPTIONAL_MODELS[DEFAULT_TTS_MODEL] == model_choice.ROLE_VOICE
    assert model_choice.OPTIONAL_MODELS["SDXL-Turbo"] == (
        model_choice.ROLE_IMAGE_CREATION
    )


def test_gemma_has_no_image_creation_and_qwen_does():
    assert model_choice.feature_tier("Gemma-4-E4B-it-GGUF") == "compact"
    assert model_choice.feature_tier("gemma4-it-e4b-FLM") == "compact"
    assert model_choice.feature_tier("Qwen3.6-35B-A3B-GGUF") == "full"


def test_the_saved_vision_model_is_the_one_scans_use(tmp_path, monkeypatch):
    from gaia.config import GaiaConfig
    from gaia.llm import lemonade_client

    config = MagicMock()
    config.vision_model = "Qwen3-VL-4B-Instruct-GGUF"
    monkeypatch.setattr(GaiaConfig, "load", classmethod(lambda cls, *a, **k: config))
    assert lemonade_client.resolve_default_vision_model() == "Qwen3-VL-4B-Instruct-GGUF"


def test_a_model_that_can_not_read_images_is_refused_as_the_vision_model():
    plan = {
        "catalog": [
            {
                "id": "embed",
                "reads_images": False,
                "roles": [model_choice.ROLE_EMBEDDING],
            }
        ]
    }
    with patch.object(setup_plan, "build_plan", return_value=plan):
        with pytest.raises(setup_plan.UnknownModel):
            setup_plan.choose_vision_model("embed")


def _plan_with(entry, reachable=True):
    return {
        "lemonade": {"reachable": reachable},
        "catalog": [entry],
        "disk": {"free_gb": 400},
    }


GEMMA = {
    "id": "Gemma-4-E4B-it-GGUF",
    "name": "Gemma 4 E4B",
    "size_gb": 5.97,
    "source": "huggingface.co/unsloth/gemma",
    "downloaded": False,
}


def test_a_missing_model_is_named_with_its_size_and_source():
    with (
        patch.object(setup_plan, "_job_model", return_value=GEMMA["id"]),
        patch.object(setup_plan, "build_plan", return_value=_plan_with(GEMMA)),
    ):
        need = setup_plan.model_need("vision")
    assert need["needed"] is True
    assert need["model"]["size_gb"] == 5.97
    assert need["model"]["source"].startswith("huggingface.co/")
    assert need["disk_free_gb"] == 400


def test_a_model_on_disk_needs_nothing():
    entry = {**GEMMA, "downloaded": True}
    with (
        patch.object(setup_plan, "_job_model", return_value=GEMMA["id"]),
        patch.object(setup_plan, "build_plan", return_value=_plan_with(entry)),
    ):
        assert setup_plan.model_need("chat")["needed"] is False


def test_nothing_is_asked_when_the_server_is_down_or_chat_is_in_the_cloud():
    with (
        patch.object(setup_plan, "_job_model", return_value=GEMMA["id"]),
        patch.object(
            setup_plan, "build_plan", return_value=_plan_with(GEMMA, reachable=False)
        ),
    ):
        assert setup_plan.model_need("chat")["needed"] is False
    with (
        patch.object(setup_plan, "_job_model", return_value=None),
        patch.object(setup_plan, "build_plan", return_value=_plan_with(GEMMA)),
    ):
        assert setup_plan.model_need("chat")["needed"] is False


def test_backends_come_from_what_lemonade_reports():
    info = {
        "recipes": {
            "llamacpp": {
                "default_backend": "vulkan",
                "backends": {
                    "vulkan": {"state": "installed"},
                    "cpu": {"state": "installable"},
                },
            }
        }
    }
    assert setup_plan._backends(info) == [
        {
            "recipe": "llamacpp",
            "backend": "vulkan",
            "state": "installed",
            "default": True,
        },
        {
            "recipe": "llamacpp",
            "backend": "cpu",
            "state": "installable",
            "default": False,
        },
    ]
    assert setup_plan._backends(None) == []


def test_the_checks_start_the_server_and_fetch_nothing_else():
    cmd = InitCommand(profile="gaia", yes=True, server_only=True)
    cmd.events = _Events()
    with (
        patch.object(cmd, "_choose_lemonade", return_value=True),
        patch.object(cmd, "_ensure_lemonade_ready", return_value=True),
        patch.object(cmd, "_download_models") as models,
        patch.object(cmd, "_install_pip_extras") as packages,
        patch.object(cmd, "_ensure_hub_agent_installed") as agent,
        patch.object(cmd, "_ensure_llamacpp_backend") as backend,
    ):
        assert cmd.run() == 0
    for fetch in (models, packages, agent, backend):
        fetch.assert_not_called()


def test_a_server_that_will_not_start_fetches_no_agent_either():
    cmd = InitCommand(profile="gaia", yes=True, server_only=True)
    with (
        patch.object(cmd, "_choose_lemonade", return_value=True),
        patch.object(cmd, "_ensure_lemonade_ready", return_value=False),
        patch.object(cmd, "_ensure_hub_agent_installed") as agent,
    ):
        assert cmd.run() == 1
    agent.assert_not_called()
