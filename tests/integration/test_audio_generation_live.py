# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opt-in real Lemonade test: fresh model pull followed by WAV generation.

Run with GAIA_RUN_AUDIO_GENERATION_LIVE=1 on a host with supported hardware,
network access, and an installed Lemonade server. The selected model should
not already be downloaded; this test never deletes user models.

No CI lane runs it (see util/test_lane_allowlist.yml): it needs a live
Lemonade, a cold model cache and a multi-GB download.
"""

import os
from pathlib import Path

import pytest
import requests

from gaia.agents.tools.audio_generation_tools import AudioGenerationToolsMixin
from gaia.llm.lemonade_client import LemonadeClient


def _loaded_models(client):
    return {
        entry.get("model_name") or entry.get("id")
        for entry in client.health_check().get("all_models_loaded") or []
    }


@pytest.mark.integration
@pytest.mark.real_model
def test_audio_generation_after_fresh_model_pull(
    require_lemonade, tmp_path, monkeypatch
):
    if os.environ.get("GAIA_RUN_AUDIO_GENERATION_LIVE") != "1":
        pytest.skip("Opt in with GAIA_RUN_AUDIO_GENERATION_LIVE=1")

    model = "ThinkSound-SFX"
    client = LemonadeClient(verbose=False)
    downloaded = {item["id"] for item in client.list_models().get("data", [])}
    if model in downloaded:
        pytest.skip("ThinkSound-SFX is already downloaded; use a fresh model cache")

    # Record every request GAIA sends, then let the real server judge it.
    sent = []
    real_post = requests.post

    def recording_post(url, *args, **kwargs):
        sent.append((url, kwargs.get("json")))
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(requests, "post", recording_post)

    # Pull only: the generation request itself must cold-load the model.
    client.pull_model(model, timeout=1800)
    pulls = [body for url, body in sent if url.endswith("/pull")]
    # A built-in model is pulled by name alone; a recipe makes Lemonade 400 (#1655).
    assert pulls == [{"model_name": model}], pulls
    assert model not in _loaded_models(client)

    audio = AudioGenerationToolsMixin()
    audio.audio_gen_client = client
    audio.audio_gen_output_dir = tmp_path

    prompt = "A wooden door shuts with a soft click in a quiet room"
    result = audio._generate_lemonade_audio("sfx", prompt, model, duration=3.0)
    assert result["status"] == "success", result

    generations = [body for url, body in sent if url.endswith("/audio/generations")]
    assert generations == [
        {
            "model": model,
            "prompt": prompt,
            "response_format": "wav",
            "duration": 3.0,
        }
    ], generations

    wav = Path(result["audio_path"])
    assert wav.is_file() and wav.stat().st_size >= 44
    assert wav.read_bytes()[:4] in (b"RIFF", b"RF64")
    # Lemonade honours the requested length to within a second.
    if result["duration_seconds"] is not None:
        assert abs(result["duration_seconds"] - 3.0) < 1.0, result
