# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A failed turn names the server it couldn't reach (#5295).

A local model behind an OpenAI-compatible proxy that answered 502 was reported
as "Lemonade on this machine", which sent the user looking at a Lemonade
install that wasn't involved. When the user chose the base URL, GAIA can't tell
what serves it, so the message names the endpoint instead.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat import agent as chat_agent  # noqa: E402
from gaia_agent_chat.agent import ChatAgent  # noqa: E402

from gaia.llm.inference_location import InferenceLocation  # noqa: E402


def _agent(provider, base_url):
    location = InferenceLocation(provider, "gpt-oss-20b", remote=provider != "lemonade")
    return SimpleNamespace(
        _inference_location=lambda: location,
        chat=SimpleNamespace(
            llm_client=SimpleNamespace(current_base_url=lambda: base_url)
        ),
    )


def test_a_user_chosen_base_url_is_named_without_its_credentials(monkeypatch):
    url = "http://user:secret@192.168.1.20:8080/v1?key=k"
    monkeypatch.setattr(chat_agent, "configured_lemonade_url", lambda: url)
    label = ChatAgent._model_service_label(_agent("lemonade", url))
    assert label == "The model server at 192.168.1.20:8080"


def test_an_unparseable_user_url_is_never_called_lemonade(monkeypatch):
    url = "http://host:notaport/v1"
    monkeypatch.setattr(chat_agent, "configured_lemonade_url", lambda: url)
    label = ChatAgent._model_service_label(_agent("lemonade", url))
    assert label == "The configured model server"


def test_gaias_own_lemonade_keeps_its_name(monkeypatch):
    monkeypatch.setattr(chat_agent, "configured_lemonade_url", lambda: None)
    label = ChatAgent._model_service_label(
        _agent("lemonade", "http://127.0.0.1:13305/api/v1")
    )
    assert label == "Lemonade on this machine"


def test_a_cloud_provider_keeps_its_name(monkeypatch):
    monkeypatch.setattr(chat_agent, "configured_lemonade_url", lambda: "http://x:1")
    label = ChatAgent._model_service_label(_agent("fireworks", "http://x:1/api/v1"))
    assert label == "Fireworks AI"
