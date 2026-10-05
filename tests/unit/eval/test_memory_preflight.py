# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The eval refuses to score memory scenarios against a backend with memory off.

The Agent UI's memory switch defaults off, and with it off every ``remember``
returns "skipped" — a run that ignores that scores memory being off, not the
agent. The probe reads the same JSON the real settings endpoint returns.
"""

import io
import json

import pytest

from gaia.eval import runner


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _serve(monkeypatch, payload):
    seen = []

    def fake_urlopen(url, timeout=None):
        seen.append(url if isinstance(url, str) else url.full_url)
        return _Resp(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


@pytest.fixture
def settings_endpoint():
    """The real GET /api/memory/settings: the default, or with the switch set."""
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app

    client = TestClient(create_app(db_path=":memory:"))

    def read(enable=None):
        if enable is not None:
            resp = client.put(
                "/api/memory/settings",
                json={"memory_enabled": enable},
                headers={"X-Gaia-UI": "1"},
            )
            assert resp.status_code == 200, resp.text
        return client.get("/api/memory/settings").json()

    return read


@pytest.mark.parametrize(
    "scenario,needs",
    [
        ({"category": "memory"}, True),
        ({"category": "gaia_memory"}, True),
        ({"category": "gaia_voice", "setup": {"memory_clear": "all"}}, True),
        ({"category": "gaia_core", "setup": {"memory_seed": []}}, True),
        ({"category": "gaia_core", "setup": {"index_documents": []}}, False),
        ({"category": "rag_quality"}, False),
        (None, False),
    ],
)
def test_which_scenarios_need_memory(scenario, needs):
    assert runner._scenario_needs_memory(scenario) is needs


def test_memory_turned_off_fails_the_probe_with_the_command_that_fixes_it(
    monkeypatch, settings_endpoint
):
    seen = _serve(monkeypatch, settings_endpoint(enable=False))

    error = runner._probe_memory_enabled("http://127.0.0.1:4200")

    assert seen == ["http://127.0.0.1:4200/api/memory/settings"]
    assert "memory_enabled=False" in error
    assert '{"memory_enabled": true}' in error
    assert "X-Gaia-UI: 1" in error


def test_backend_with_memory_on_passes_the_probe(monkeypatch, settings_endpoint):
    _serve(monkeypatch, settings_endpoint(enable=True))

    assert runner._probe_memory_enabled("http://127.0.0.1:4200") is None


def test_a_default_backend_passes_the_probe(monkeypatch, settings_endpoint):
    """Memory is on unless someone turned it off."""
    _serve(monkeypatch, settings_endpoint())

    assert runner._probe_memory_enabled("http://127.0.0.1:4200") is None


def test_unreachable_backend_is_named(monkeypatch):
    import urllib.error

    def refuse(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)

    error = runner._probe_memory_enabled("http://127.0.0.1:1")

    assert "could not reach http://127.0.0.1:1" in error


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"<html>proxy error</html>", "non-JSON response"),
        (b'["memory_enabled"]', "expected a JSON object"),
    ],
)
def test_malformed_settings_body_is_a_preflight_error(monkeypatch, body, expected):
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: _Resp(body))

    error = runner._probe_memory_enabled("http://127.0.0.1:4200")

    assert expected in error
    assert "http://127.0.0.1:4200/api/memory/settings" in error


def test_preflight_probes_memory_only_when_a_scenario_needs_it(monkeypatch):
    probed = []
    monkeypatch.setattr(
        runner, "_probe_memory_enabled", lambda url: probed.append(url) or "off"
    )
    monkeypatch.setattr(runner, "_probe_memory_admin", lambda url: None)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)

    errors = runner.preflight_check(
        "http://127.0.0.1:1", scenarios=[(None, {"category": "rag_quality"})]
    )
    assert "off" not in errors and probed == []

    errors = runner.preflight_check(
        "http://127.0.0.1:1",
        scenarios=[
            (None, {"category": "gaia_voice", "setup": {"memory_clear": "all"}})
        ],
    )
    assert "off" in errors and probed == ["http://127.0.0.1:1"]
