# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image and audio through the provider interface (#381).

``LemonadeProvider`` is what ``create_client()`` returns, so anything it does not
expose is unreachable to a caller written against ``LLMClient``.
"""

import json
from unittest.mock import MagicMock

import pytest
import responses

from gaia.audio.lemonade_asr import DEFAULT_ASR_MODEL
from gaia.llm.base_client import LLMClient
from gaia.llm.exceptions import NotSupportedError
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.llm.providers.lemonade import LemonadeProvider

BASE = "http://localhost:13305/api/v1"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("LEMONADE_BASE_URL", BASE)
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    return LemonadeProvider(model="Gemma-4-E4B-it-GGUF", base_url=BASE)


@pytest.fixture
def fake_asr(monkeypatch):
    """Patch the ASR client at its source; the provider imports it lazily."""
    instance = MagicMock()
    instance.transcribe.return_value = "the transcript"
    cls = MagicMock(return_value=instance)
    monkeypatch.setattr("gaia.audio.lemonade_asr.LemonadeASRClient", cls)
    return cls, instance


# --- generate_image ---------------------------------------------------------


@responses.activate
def test_generate_image_posts_the_generation_request_and_returns_the_body(provider):
    body = {"data": [{"b64_json": "aGk="}]}
    responses.add(responses.POST, f"{BASE}/images/generations", json=body)

    result = provider.generate_image(
        "a lemon on a desk", model="SD-Turbo", size="512x512", seed=7
    )

    assert result == body
    assert len(responses.calls) == 1
    sent = json.loads(responses.calls[0].request.body)
    assert sent["prompt"] == "a lemon on a desk"
    assert sent["model"] == "SD-Turbo"
    assert sent["size"] == "512x512"
    assert sent["seed"] == 7
    assert sent["response_format"] == "b64_json"


@responses.activate
def test_generate_image_surfaces_a_server_error(provider):
    responses.add(
        responses.POST,
        f"{BASE}/images/generations",
        json={"error": {"message": "sd-cpp backend not installed"}},
        status=500,
    )

    with pytest.raises(LemonadeClientError):
        provider.generate_image("a lemon", model="SD-Turbo")


@responses.activate
def test_generate_image_rejects_an_unknown_model_without_generating(provider):
    responses.add(responses.GET, f"{BASE}/models", json={"data": []})

    with pytest.raises(LemonadeClientError, match="Invalid"):
        provider.generate_image("a lemon", model="not-an-sd-model")
    assert not any(
        call.request.url.endswith("/images/generations") for call in responses.calls
    )


# --- transcribe_audio -------------------------------------------------------


def test_transcribe_audio_targets_the_providers_own_server(fake_asr, provider):
    cls, instance = fake_asr

    result = provider.transcribe_audio(
        "/tmp/meeting.wav", language="en", require_timestamps=False
    )

    assert result == "the transcript"
    cls.assert_called_once_with(
        base_url=provider._backend.base_url,
        model=DEFAULT_ASR_MODEL,
        api_key=provider._backend.api_key,
    )
    instance.transcribe.assert_called_once_with(
        "/tmp/meeting.wav", language="en", require_timestamps=False
    )


def test_transcribe_audio_forwards_the_providers_api_key(fake_asr, monkeypatch):
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    cls, _ = fake_asr
    provider = LemonadeProvider(base_url=BASE, api_key="secret-key")

    provider.transcribe_audio("/tmp/a.wav", model="Whisper-Base")

    assert cls.call_args.kwargs["api_key"] == "secret-key"
    assert cls.call_args.kwargs["model"] == "Whisper-Base"


def test_transcribe_audio_does_not_swap_an_empty_model_for_the_default(provider):
    # An empty id is a caller bug; quietly using the default would hide it.
    with pytest.raises(ValueError, match="transcription model id"):
        provider.transcribe_audio("/tmp/a.wav", model="")


def test_transcribe_audio_reports_a_missing_file(provider, tmp_path):
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        provider.transcribe_audio(str(tmp_path / "absent.wav"))


def test_transcribe_audio_rejects_a_non_wav_before_any_request(provider, tmp_path):
    clip = tmp_path / "clip.mp3"
    clip.write_bytes(b"ID3")
    with responses.RequestsMock() as rsps:
        with pytest.raises(ValueError, match="WAV only"):
            provider.transcribe_audio(str(clip))
        assert len(rsps.calls) == 0


# --- the interface defaults -------------------------------------------------


class _TextOnly(LLMClient):
    @property
    def provider_name(self) -> str:
        return "TextOnly"

    def generate(self, prompt, model=None, stream=False, **kwargs):
        return ""

    def chat(self, messages, model=None, stream=False, **kwargs):
        return ""


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.generate_image("a lemon"),
        lambda c: c.transcribe_audio("/tmp/a.wav"),
    ],
    ids=["generate_image", "transcribe_audio"],
)
def test_a_provider_without_the_capability_says_so(call):
    with pytest.raises(NotSupportedError) as exc:
        call(_TextOnly())
    assert "TextOnly" in str(exc.value)
