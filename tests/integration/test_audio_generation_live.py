# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Opt-in real Lemonade test: fresh model pull followed by WAV generation.

Run with GAIA_RUN_AUDIO_GENERATION_LIVE=1 on a host with supported hardware,
network access, and an installed Lemonade server. The selected model should
not already be downloaded; this test never deletes user models.
"""

import os
from pathlib import Path

import pytest

from gaia.agents.tools.audio_generation_tools import AudioGenerationToolsMixin
from gaia.llm.lemonade_client import LemonadeClient


@pytest.mark.integration
@pytest.mark.real_model
def test_audio_generation_after_fresh_model_pull(require_lemonade, tmp_path):
    if os.environ.get("GAIA_RUN_AUDIO_GENERATION_LIVE") != "1":
        pytest.skip("Opt in with GAIA_RUN_AUDIO_GENERATION_LIVE=1")

    model = "ThinkSound-SFX"
    client = LemonadeClient(verbose=False)
    downloaded = {item["id"] for item in client.list_models().get("data", [])}
    if model in downloaded:
        pytest.skip("ThinkSound-SFX is already downloaded; use a fresh model cache")

    # Download only; the generation request must perform the cold model load.
    assert client.ensure_model_downloaded(model, show_progress=False, timeout=1800)
    audio = AudioGenerationToolsMixin()
    audio.audio_gen_client = client
    audio.audio_gen_output_dir = tmp_path

    result = audio._generate_lemonade_audio(
        "sfx",
        "A wooden door shuts with a soft click in a quiet room",
        model,
        duration=3.0,
    )
    assert result["status"] == "success", result
    wav = Path(result["audio_path"])
    assert wav.is_file() and wav.stat().st_size >= 44
    assert wav.read_bytes()[:4] in (b"RIFF", b"RF64")
