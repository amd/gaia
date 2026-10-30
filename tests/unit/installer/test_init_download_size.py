# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` names what it is about to download, and how big it is.

On a 32 GB-GPU PC the default profile pulls ~28 GB (Qwen3.6 + Gemma + the
embedder) while the installer and the prompt promised ~6 GB.
"""

from unittest.mock import MagicMock, patch

from gaia.installer.init_command import InitCommand, pending_download_sizes

CATALOG = {
    "data": [
        {"id": "Qwen3.6-35B-A3B-GGUF", "size": 21.7, "downloaded": False},
        {"id": "Gemma-4-E4B-it-GGUF", "size": 5.56, "downloaded": True},
        # Lemonade lists a user.-registered model under its stripped id.
        {"id": "embeddinggemma-300m-GGUF", "size": 0.311, "downloaded": False},
    ]
}


def _client():
    client = MagicMock()
    client.list_models.return_value = CATALOG
    return client


def test_sizes_come_from_lemonade_and_skip_what_is_on_disk():
    sizes = pending_download_sizes(
        _client(),
        [
            "Qwen3.6-35B-A3B-GGUF",
            "Gemma-4-E4B-it-GGUF",
            "user.embeddinggemma-300m-GGUF",
        ],
    )
    assert sizes == {
        "Qwen3.6-35B-A3B-GGUF": 21.7,
        "Gemma-4-E4B-it-GGUF": 0.0,
        "user.embeddinggemma-300m-GGUF": 0.311,
    }


def test_unlisted_model_falls_back_to_the_registry_then_unknown():
    client = MagicMock()
    client.list_models.return_value = {"data": []}
    sizes = pending_download_sizes(client, ["Qwen3.6-35B-A3B-GGUF", "No-Such-GGUF"])
    assert sizes["Qwen3.6-35B-A3B-GGUF"] == 23.3
    assert sizes["No-Such-GGUF"] is None


def test_download_step_prints_each_size_and_the_total(capsys):
    cmd = InitCommand(profile="minimal", yes=True)
    client = _client()
    client.ensure_model_downloaded.return_value = True
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch(
            "gaia.installer.init_command.INIT_PROFILES",
            {
                "minimal": {
                    "models": ["Qwen3.6-35B-A3B-GGUF", "Gemma-4-E4B-it-GGUF"],
                    "agent": "minimal",
                }
            },
        ),
    ):
        assert cmd._download_models()
    out = capsys.readouterr().out
    assert "Qwen3.6-35B-A3B-GGUF (21.7 GB)" in out
    assert "Gemma-4-E4B-it-GGUF (already downloaded)" in out
    assert "Total download: about 21.7 GB" in out
