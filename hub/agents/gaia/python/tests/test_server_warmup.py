# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar's start-up warm-up must never download a model.

Starting the sidecar warmed the agent's default local chat model with one chat
request, and Lemonade answers a request for a model it does not have by
downloading it. A user who chose a cloud provider got a silent ~5 GB download
every time the daemon started the agent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

import gaia_agent.server as server_mod  # noqa: E402
import gaia_agent.session_registry as session_registry_mod  # noqa: E402

import gaia.llm.lemonade_client as lemonade_client_mod  # noqa: E402


class _Agent:
    model_id = "Gemma-4-E4B-it-GGUF"

    def _get_system_prompt(self) -> str:
        return "You are GAIA."


class _Client:
    def __init__(self, downloaded: bool) -> None:
        self.downloaded = downloaded
        self.checked: list = []
        self.completions: list = []

    def check_model_available(self, model_id: str) -> bool:
        self.checked.append(model_id)
        return self.downloaded

    def chat_completions(self, **kwargs):
        self.completions.append(kwargs)
        return {"usage": {}}


def _run(monkeypatch, client: _Client) -> None:
    monkeypatch.setattr(session_registry_mod, "build_session_agent", _Agent)
    monkeypatch.setattr(server_mod, "close_agent", lambda agent: None)
    monkeypatch.setattr(
        lemonade_client_mod, "create_lemonade_client", lambda **kwargs: client
    )
    server_mod._warmup_blocking()  # noqa: SLF001


def test_warmup_skips_a_model_that_is_not_downloaded(monkeypatch, caplog):
    client = _Client(downloaded=False)
    with caplog.at_level("INFO"):
        _run(monkeypatch, client)

    assert client.checked == ["Gemma-4-E4B-it-GGUF"]
    assert client.completions == [], "the warm-up asked Lemonade to load (download) it"
    assert "warm-up skipped" in caplog.text


def test_warmup_still_warms_a_downloaded_model(monkeypatch):
    client = _Client(downloaded=True)
    _run(monkeypatch, client)

    assert len(client.completions) == 1
    assert client.completions[0]["model"] == "Gemma-4-E4B-it-GGUF"
