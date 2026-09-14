# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Asking Fireworks directly what it serves.

The point of this module is to make "the gateway does not offer the model I
want" a checkable claim rather than a suspicion, so the ways it can mislead are
the ways that matter: an unreachable provider must not look like an empty
catalogue, and a model id must come back in the form a user can actually pass
to a Lemonade-routed request.
"""

from __future__ import annotations

import json

import pytest
import requests

from gaia.connectors.errors import ConnectorsError
from gaia.llm.providers.fireworks import (
    API_KEY_ENV_VARS,
    FIREWORKS_CONTROL_URL,
    FireworksError,
    FireworksModel,
    fetch_library,
    fetch_models,
    missing_from_gateway,
    needs_deployment,
    resolve_fireworks_api_key,
    search,
)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


CATALOGUE = {
    "data": [
        {
            "id": "accounts/fireworks/models/gemma-4-31b-it",
            "context_length": 262144,
            "owned_by": "fireworks",
            "supports_serverless": True,
        },
        {"id": "accounts/fireworks/models/qwen3p8-27b", "context_length": 262144},
        {"id": "accounts/fireworks/models/glm-5p3"},
        {"id": "", "context_length": 1},
        "not-a-model",
    ]
}


@pytest.fixture
def no_env(monkeypatch):
    """No key anywhere: not in the environment, not in the OS keyring.

    The keyring half matters — without it these tests pass or fail depending
    on whether the developer running them happens to have a real key stored,
    which is exactly the hidden-state trap the repo's testing rules warn about.
    """
    for name in API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda _name: None)


class TestKeyResolution:
    def test_argument_wins(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "from-env")
        assert resolve_fireworks_api_key("explicit") == "explicit"

    def test_falls_back_to_lemonades_own_variable(self, monkeypatch, no_env):
        monkeypatch.setenv(API_KEY_ENV_VARS[1], "lemonade-key")
        assert resolve_fireworks_api_key() == "lemonade-key"

    def test_blank_counts_as_unset(self, monkeypatch, no_env):
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "   ")
        assert resolve_fireworks_api_key() is None

    def test_a_stored_key_is_used_when_nothing_is_set(self, monkeypatch):
        """The keyring is the whole point: no shell profile, no file."""
        for name in API_KEY_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            "gaia.connectors.store.peek_secret", lambda _name: "from-keyring"
        )
        assert resolve_fireworks_api_key() == "from-keyring"

    def test_environment_beats_the_stored_key(self, monkeypatch):
        """A key set for this run must not be overridden by an old stored one."""
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "from-env")
        monkeypatch.setattr(
            "gaia.connectors.store.peek_secret", lambda _name: "from-keyring"
        )
        assert resolve_fireworks_api_key() == "from-env"

    def test_an_unreadable_keyring_still_names_the_variable_to_set(
        self, monkeypatch, no_env
    ):
        """No usable credential store must not hide the simple fix."""

        def no_backend(_name):
            raise ConnectorsError("Keyring get_password failed: no backend.")

        monkeypatch.setattr("gaia.connectors.store.peek_secret", no_backend)
        with pytest.raises(FireworksError) as e:
            resolve_fireworks_api_key()
        message = str(e.value)
        assert API_KEY_ENV_VARS[0] in message
        assert "no backend" in message, "the keyring problem must stay visible"
        assert isinstance(e.value.__cause__, ConnectorsError)

    def test_missing_key_names_what_to_set(self, no_env):
        with pytest.raises(FireworksError) as e:
            fetch_models()
        message = str(e.value)
        assert API_KEY_ENV_VARS[0] in message
        assert "fireworks.ai" in message


class TestFetching:
    def test_parses_the_catalogue(self, monkeypatch, no_env):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Response(200, CATALOGUE))
        models = fetch_models(api_key="k")
        assert [m.short_name for m in models] == [
            "gemma-4-31b-it",
            "glm-5p3",
            "qwen3p8-27b",
        ], "ids should be sorted and the malformed rows dropped"
        assert models[0].context_length == 262144
        assert models[0].serverless is True

    def test_ids_come_back_in_the_form_a_request_can_use(self, monkeypatch, no_env):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Response(200, CATALOGUE))
        ids = {m.lemonade_id for m in fetch_models(api_key="k")}
        assert "fireworks.gemma-4-31b-it" in ids

    def test_a_rejected_key_says_so_rather_than_returning_nothing(
        self, monkeypatch, no_env
    ):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: _Response(401, text="nope")
        )
        with pytest.raises(FireworksError, match="401"):
            fetch_models(api_key="bad")

    def test_an_unreachable_provider_is_not_an_empty_catalogue(
        self, monkeypatch, no_env
    ):
        def boom(*a, **k):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(requests, "get", boom)
        with pytest.raises(FireworksError, match="Could not reach Fireworks"):
            fetch_models(api_key="k")

    def test_an_http_error_carries_the_status(self, monkeypatch, no_env):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: _Response(503, text="busy")
        )
        with pytest.raises(FireworksError, match="503"):
            fetch_models(api_key="k")

    def test_an_unexpected_shape_is_rejected(self, monkeypatch, no_env):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: _Response(200, {"data": {}})
        )
        with pytest.raises(FireworksError, match="Unexpected catalogue shape"):
            fetch_models(api_key="k")


def _library_entry(short, serverless):
    return {
        "name": f"accounts/fireworks/models/{short}",
        "supportsServerless": serverless,
        "state": "READY",
    }


class TestLibrary:
    """The library is what exists; ``supportsServerless`` is what is callable."""

    def test_parses_one_page(self, monkeypatch, no_env):
        payload = {
            "models": [
                _library_entry("glm-5p3", True),
                _library_entry("gemma-4-31b-it", False),
                {"name": ""},
                "not-a-model",
            ]
        }
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Response(200, payload))
        library = fetch_library(api_key="k")
        assert [m.short_name for m in library] == ["gemma-4-31b-it", "glm-5p3"]
        assert library[0].serverless is False
        assert library[1].serverless is True
        assert library[1].state == "READY"

    def test_follows_next_page_token(self, monkeypatch, no_env):
        pages = {
            None: {
                "models": [_library_entry("glm-5p3", True)],
                "nextPageToken": "p2",
            },
            "p2": {"models": [_library_entry("gemma-4-31b-it", False)]},
        }
        calls = []

        def get(url, **kwargs):
            token = kwargs["params"].get("pageToken")
            calls.append((url, token))
            return _Response(200, pages[token])

        monkeypatch.setattr(requests, "get", get)
        library = fetch_library(api_key="k")
        assert [m.short_name for m in library] == ["gemma-4-31b-it", "glm-5p3"]
        assert calls == [(FIREWORKS_CONTROL_URL, None), (FIREWORKS_CONTROL_URL, "p2")]

    def test_a_model_without_serverless_needs_a_deployment(self, monkeypatch, no_env):
        payload = {
            "models": [
                _library_entry("glm-5p3", True),
                _library_entry("gemma-4-31b-it", False),
                _library_entry("qwen3p8-27b", False),
            ]
        }
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Response(200, payload))
        library = fetch_library(api_key="k")
        assert [m.short_name for m in needs_deployment(library)] == [
            "gemma-4-31b-it",
            "qwen3p8-27b",
        ]
        assert [m.short_name for m in needs_deployment(library, "GEMMA")] == [
            "gemma-4-31b-it"
        ]

    def test_a_repeated_page_token_raises_instead_of_looping(self, monkeypatch, no_env):
        calls = []

        def get(*a, **k):
            calls.append(k["params"].get("pageToken"))
            if len(calls) > 5:
                pytest.fail("fetch_library kept paging on a repeated token")
            return _Response(
                200, {"models": [_library_entry("glm-5p3", True)], "nextPageToken": "x"}
            )

        monkeypatch.setattr(requests, "get", get)
        with pytest.raises(FireworksError, match="repeated page token"):
            fetch_library(api_key="k")
        assert calls == [None, "x"]

    def test_a_non_json_body_raises(self, monkeypatch, no_env):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: _Response(200, text="<html>")
        )
        with pytest.raises(FireworksError, match="non-JSON"):
            fetch_library(api_key="k")

    def test_a_non_object_payload_raises(self, monkeypatch, no_env):
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Response(200, ["x"]))
        with pytest.raises(FireworksError, match="Unexpected library shape"):
            fetch_library(api_key="k")

    def test_an_http_error_carries_the_status_and_url(self, monkeypatch, no_env):
        monkeypatch.setattr(
            requests, "get", lambda *a, **k: _Response(403, text="forbidden")
        )
        with pytest.raises(FireworksError, match="403") as e:
            fetch_library(api_key="k")
        assert FIREWORKS_CONTROL_URL in str(e.value)


class TestGapAgainstTheGateway:
    """The reason this exists: name what the gateway is not offering."""

    def setup_method(self):
        self.catalogue = [
            FireworksModel(id="accounts/fireworks/models/gemma-4-31b-it"),
            FireworksModel(id="accounts/fireworks/models/qwen3p8-27b"),
            FireworksModel(id="accounts/fireworks/models/glm-5p3"),
        ]

    def test_reports_only_what_the_gateway_lacks(self):
        missing = missing_from_gateway(self.catalogue, ["fireworks.glm-5p3"])
        assert [m.short_name for m in missing] == ["gemma-4-31b-it", "qwen3p8-27b"]

    def test_matches_a_gateway_id_written_in_full_path_form(self):
        missing = missing_from_gateway(
            self.catalogue,
            ["fireworks.accounts/fireworks/models/glm-5p3"],
        )
        assert "glm-5p3" not in [m.short_name for m in missing]

    def test_an_empty_gateway_list_means_everything_is_missing(self):
        assert len(missing_from_gateway(self.catalogue, [])) == 3


class TestSearch:
    def test_matches_case_insensitively(self):
        models = [FireworksModel(id="accounts/fireworks/models/Gemma-4-31B-it")]
        assert search(models, "gemma") == models

    def test_an_empty_term_returns_everything(self):
        models = [FireworksModel(id="a"), FireworksModel(id="b")]
        assert search(models, "  ") == models
