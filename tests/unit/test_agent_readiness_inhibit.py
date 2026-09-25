# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the model-load test hook (issue #2539).

Lemonade lazily reloads an unloaded model on the next inference request,
which makes "model unavailable" un-testable through the readiness probe
alone: unload it, and the very next request anywhere may silently reload it
before a second check can observe the unloaded state. ``GAIA_TEST_INHIBIT_MODEL``
makes ``probe_backend_health`` report chosen model ids as absent from
``all_models_loaded`` regardless of what Lemonade actually answers.
"""

from __future__ import annotations

import requests

from gaia.agents.base.readiness import (
    INHIBIT_MODEL_ENV_VAR,
    _filter_inhibited_loaded_models,
    probe_backend_health,
)

_LOADED = [
    {"model_name": "user.Test-Model", "checkpoint": "org/test-model"},
    {"model_name": "user.Other-Model", "checkpoint": "org/other-model"},
]


def test_no_env_var_passes_loaded_models_through(monkeypatch):
    monkeypatch.delenv(INHIBIT_MODEL_ENV_VAR, raising=False)

    assert _filter_inhibited_loaded_models(_LOADED) == _LOADED


def test_inhibited_model_id_is_stripped_from_loaded(monkeypatch):
    monkeypatch.setenv(INHIBIT_MODEL_ENV_VAR, "Test-Model")

    filtered = _filter_inhibited_loaded_models(_LOADED)

    assert [m["model_name"] for m in filtered] == ["user.Other-Model"]


def test_wildcard_inhibits_every_model(monkeypatch):
    monkeypatch.setenv(INHIBIT_MODEL_ENV_VAR, "*")

    assert _filter_inhibited_loaded_models(_LOADED) == []


def test_probe_backend_health_applies_the_hold_end_to_end(monkeypatch):
    """The real HTTP boundary, not just the pure filter, so a refactor that
    forgets to route loaded_models through the filter is caught here."""

    class _FakeResponse:
        def json(self):
            return {"version": "10.10.0", "all_models_loaded": _LOADED}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse())
    monkeypatch.setenv(INHIBIT_MODEL_ENV_VAR, "Test-Model")

    reachable, _base, version, loaded = probe_backend_health("http://127.0.0.1:8000")

    assert reachable is True
    assert version == "10.10.0"
    assert [m["model_name"] for m in loaded] == ["user.Other-Model"]
