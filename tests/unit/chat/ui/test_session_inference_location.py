# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The session response says where its chat is answered, so the UI never
claims "100% local" while turns go to a cloud provider."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gaia.ui._chat_helpers import session_inference_location
from gaia.ui.server import create_app


class _Registry:
    """Minimal stand-in: one registration with a preferred-model list."""

    def __init__(self, models):
        self._reg = SimpleNamespace(models=models, device_configs=None)

    def get(self, _agent_type):
        return self._reg


@pytest.fixture(autouse=True)
def _no_eval_provider(monkeypatch):
    monkeypatch.delenv("GAIA_EVAL_AGENT_PROVIDER", raising=False)


@pytest.fixture
def client():
    return TestClient(create_app(db_path=":memory:"))


def _session(model="Gemma-4-E4B-it-GGUF", device=None):
    return {"model": model, "agent_type": "gaia", "device": device}


class TestSessionInferenceLocation:
    def test_local_model_is_local(self):
        loc = session_inference_location(_session(), None, registry=_Registry([]))
        assert loc.remote is False
        assert loc.provider == "lemonade"
        assert loc.model == "Gemma-4-E4B-it-GGUF"

    @pytest.mark.parametrize(
        "model,provider,name",
        [
            ("fireworks.deepseek-v4p1-flash", "fireworks", "Fireworks AI"),
            ("amd.Gemma-4-31B", "amd", "AMD LLM Gateway"),
        ],
    )
    def test_custom_cloud_override_is_remote(self, model, provider, name):
        loc = session_inference_location(_session(), model, registry=_Registry([]))
        assert loc.remote is True
        assert loc.provider == provider
        assert loc.display == name
        assert "sent there" in loc.describe()

    def test_override_beats_local_agent_preference(self):
        loc = session_inference_location(
            _session(),
            "fireworks.deepseek-v4p1-flash",
            registry=_Registry(["Gemma-4-E4B-it-GGUF"]),
        )
        assert loc.remote is True

    def test_dotted_local_id_is_not_cloud(self):
        loc = session_inference_location(
            _session("Qwen3.5-35B-A3B-GGUF"), None, registry=_Registry([])
        )
        assert loc.remote is False

    def test_cloud_session_model_with_local_preference_is_unknown(self):
        # Which one wins depends on what Lemonade has downloaded.
        loc = session_inference_location(
            _session("fireworks.deepseek-v4p1-flash"),
            None,
            registry=_Registry(["Gemma-4-E4B-it-GGUF"]),
        )
        assert loc is None

    def test_several_local_candidates_stay_local_without_a_model_name(self):
        loc = session_inference_location(
            _session(), None, registry=_Registry(["Qwen3-4B-GGUF"])
        )
        assert loc.remote is False
        assert loc.model == ""

    def test_eval_claude_provider_is_remote(self, monkeypatch):
        monkeypatch.setenv("GAIA_EVAL_AGENT_PROVIDER", "claude")
        monkeypatch.setenv("GAIA_EVAL_CLAUDE_MODEL", "claude-haiku-4-5")
        loc = session_inference_location(_session(), None, registry=_Registry([]))
        assert loc.remote is True
        assert loc.provider == "claude"

    def test_misconfigured_eval_provider_is_unknown(self, monkeypatch):
        monkeypatch.setenv("GAIA_EVAL_AGENT_PROVIDER", "bogus")
        assert session_inference_location(_session(), None, _Registry([])) is None


class TestSessionResponseFields:
    def test_cloud_override_reaches_session_responses(self, client):
        created = client.post("/api/sessions", json={"title": "t"}).json()
        client.put(
            "/api/settings", json={"custom_model": "fireworks.deepseek-v4p1-flash"}
        )

        for data in (
            client.get(f"/api/sessions/{created['id']}").json(),
            client.get("/api/sessions").json()["sessions"][0],
        ):
            assert data["inference_remote"] is True
            assert data["inference_provider"] == "fireworks"
            assert data["inference_provider_name"] == "Fireworks AI"
            assert "Fireworks AI via Lemonade" in data["inference_description"]
            # The header badge names the same model the location classified.
            assert data["effective_model"] == "fireworks.deepseek-v4p1-flash"

    def test_badge_names_the_eval_claude_model(self, client, monkeypatch):
        monkeypatch.setenv("GAIA_EVAL_AGENT_PROVIDER", "claude")
        monkeypatch.setenv("GAIA_EVAL_CLAUDE_MODEL", "claude-haiku-4-5")
        created = client.post("/api/sessions", json={"title": "t"}).json()
        data = client.get(f"/api/sessions/{created['id']}").json()
        assert data["inference_remote"] is True
        assert data["effective_model"] == "claude-haiku-4-5"

    def test_clearing_override_goes_back_to_local(self, client):
        created = client.post("/api/sessions", json={"title": "t"}).json()
        client.put(
            "/api/settings", json={"custom_model": "fireworks.deepseek-v4p1-flash"}
        )
        client.put("/api/settings", json={"custom_model": ""})
        data = client.get(f"/api/sessions/{created['id']}").json()
        assert data["inference_remote"] is False
        assert data["inference_provider"] == "lemonade"
