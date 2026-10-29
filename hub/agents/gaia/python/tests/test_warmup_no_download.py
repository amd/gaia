# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar's warm-up never downloads a model the user did not choose.

Building the warm-up agent preloads its default model, and Lemonade downloads a
missing one on load. A user who skipped the local model and picked a cloud one
got a multi-GB download, and an Agent UI stuck "starting" behind it, the moment
the sidecar spawned.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

import gaia_agent.session_registry as registry_mod  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

import gaia.llm.lemonade_client as lc  # noqa: E402


@pytest.fixture
def default_model(monkeypatch):
    def _set(model_id, downloaded):
        monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: model_id)
        monkeypatch.setattr(
            lc.LemonadeClient,
            "get_model_info",
            lambda self, name: {"id": name, "downloaded": downloaded, "size_gb": 5},
        )

    return _set


def _forbid_agent_build(monkeypatch):
    built = []
    monkeypatch.setattr(
        registry_mod, "build_session_agent", lambda **k: built.append(k) or 1 / 0
    )
    return built


def test_an_undownloaded_default_skips_the_warmup(monkeypatch, default_model):
    default_model("Gemma-4-E4B-it-GGUF", downloaded=False)
    built = _forbid_agent_build(monkeypatch)

    server_mod._warmup_blocking()

    assert built == [], "building the agent would preload, and so download, it"


def test_a_downloaded_default_is_warmed(default_model):
    default_model("Gemma-4-E4B-it-GGUF", downloaded=True)

    assert server_mod._default_model_is_servable() is True


def test_a_cloud_default_is_warmed_without_a_catalog_lookup(monkeypatch):
    monkeypatch.setattr(lc, "resolve_default_chat_model", lambda: "amd.DeepSeek")

    def no_lookup(self, name):
        raise AssertionError("a cloud model has nothing to download")

    monkeypatch.setattr(lc.LemonadeClient, "get_model_info", no_lookup)

    assert server_mod._default_model_is_servable() is True


class _Agent:
    model_id = "Gemma-4-E4B-it-GGUF"

    def __init__(self, fail=False):
        self.fail = fail
        self.warmed = False

    def warm_up(self):
        if self.fail:
            raise ConnectionError("Lemonade not reachable")
        self.warmed = True
        return {"seconds": 1.0, "prompt_tokens": 20000, "cached_tokens": 0}


@pytest.mark.parametrize("fail", [False, True])
def test_the_warm_up_primes_a_session_agents_prefix(monkeypatch, default_model, fail):
    """The warm-up runs the agent's own warm_up — the composed system prompt and
    tool schemas a turn sends — and a failure is a visible warning."""
    default_model("Gemma-4-E4B-it-GGUF", downloaded=True)
    agent = _Agent(fail=fail)
    closed = []
    monkeypatch.setattr(registry_mod, "build_session_agent", lambda **k: agent)
    monkeypatch.setattr(server_mod, "close_agent", closed.append)
    warnings = []
    monkeypatch.setattr(
        server_mod.logger, "warning", lambda msg, *a, **k: warnings.append(msg % a)
    )

    server_mod._warmup_blocking()

    assert closed == [agent]
    assert agent.warmed is not fail
    assert bool(warnings) is fail
    if fail:
        assert "Lemonade not reachable" in warnings[0]
