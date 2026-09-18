# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Spec for the model-load test hook (issues #2539, #3853).

Lemonade lazily reloads an unloaded model on the next inference request,
which makes "model unavailable" un-testable through the readiness probe
alone: unload it, and the very next request anywhere may silently reload it
before a second check can observe the unloaded state. ``GAIA_TEST_INHIBIT_MODEL``
makes chosen model ids report as absent regardless of what Lemonade answers.

The hold must cover both probes. ``all_models_loaded`` only feeds the ctx-size
annotation on an otherwise-green row; the row that actually fails the preflight
gate comes from ``probe_model_present``. Holding down only the first produced a
green row — the state the hook claimed to make un-green (#3853).
"""

from __future__ import annotations

import requests

from gaia.agents.base.readiness import (
    INHIBIT_MODEL_ENV_VAR,
    AgentRequirements,
    _filter_inhibited_loaded_models,
    compute_init_status,
    probe_backend_health,
    probe_model_present,
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


class _FakeModelsResponse:
    """A ``/models`` answer that lists every model as downloaded."""

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"id": "Test-Model"}, {"id": "Other-Model"}]}


def test_probe_model_present_honours_the_hold(monkeypatch):
    """The probe that actually fails the gate, not just the ctx annotation."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeModelsResponse())
    monkeypatch.setenv(INHIBIT_MODEL_ENV_VAR, "Test-Model")

    base = "http://127.0.0.1:8000/api/v1"
    assert probe_model_present(base, "Test-Model") is False
    assert probe_model_present(base, "Other-Model") is True


def test_unheld_model_is_still_reported_present(monkeypatch):
    monkeypatch.delenv(INHIBIT_MODEL_ENV_VAR, raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeModelsResponse())

    assert probe_model_present("http://127.0.0.1:8000/api/v1", "Test-Model") is True


def test_hold_reaches_the_model_unavailable_gate_row(monkeypatch):
    """#3853: the whole point of the hook — a not-ready status the gate renders.

    Lemonade is faked as fully healthy WITH the model both loaded and
    downloaded, so only the hook can make this come back not-ready.
    """

    class _Health:
        def json(self):
            return {"version": "10.10.0", "all_models_loaded": _LOADED}

    def _get(url, *args, **kwargs):
        return _FakeModelsResponse() if url.endswith("/models") else _Health()

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setenv(INHIBIT_MODEL_ENV_VAR, "Test-Model")

    status = compute_init_status(
        AgentRequirements(
            model_id="Test-Model", base_url="http://127.0.0.1:8000/api/v1"
        )
    )

    assert status.ready is False
    assert status.model.present is False
    assert "not downloaded" in status.hint
