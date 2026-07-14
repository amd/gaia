# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the email agent readiness endpoint ``GET /v1/email/init`` (#1795).

The endpoint is a read-only preflight over the whole triage stack: it probes the
local Lemonade Server and confirms the triage model is downloaded, returning a
structured status (200 when ready, 503 when not, with an actionable hint). Unlike
``/health`` it does touch the LLM backend — but only with cheap probes, never a
model load or pull.

These tests follow the repo's "verify call validity at boundaries" rule
(CLAUDE.md): the probe helpers are exercised against a mocked ``requests.get`` and
the SHAPE of the outgoing call is asserted (URL suffix, short timeout, auth
header), not merely that a call happened.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as ar  # noqa: E402
import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email.api_routes import (  # noqa: E402
    _probe_lemonade_health,
    _probe_lemonade_reachable,
    _probe_model_present,
    _pull_model,
    _resolve_email_model_id,
    _resolve_probe_base,
)
from gaia_agent_email.export_openapi import build_app  # noqa: E402
from gaia_agent_email.version import MIN_LEMONADE_VERSION  # noqa: E402

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME  # noqa: E402

# The short pre-flight timeout pair (#1677) — every probe must use it so an
# unreachable server fails fast instead of blocking on the OS SYN timeout.
_EXPECTED_TIMEOUT = (
    ar._LEMONADE_PROBE_CONNECT_TIMEOUT,
    ar._LEMONADE_PROBE_READ_TIMEOUT,
)


@pytest.fixture
def client() -> TestClient:
    """In-process ASGI client mounting only the email router — no mailbox, no LLM."""
    return TestClient(build_app())


# ---------------------------------------------------------------------------
# 1. Base-URL resolution
# ---------------------------------------------------------------------------


def test_resolve_probe_base_appends_api_v1_when_missing():
    assert (
        _resolve_probe_base("http://localhost:9999") == "http://localhost:9999/api/v1"
    )


def test_resolve_probe_base_keeps_existing_api_v1_and_strips_trailing_slash():
    assert (
        _resolve_probe_base("http://localhost:9999/api/v1/")
        == "http://localhost:9999/api/v1"
    )


# ---------------------------------------------------------------------------
# 2. Reachability probe — boundary shape
# ---------------------------------------------------------------------------


def test_reachable_probe_targets_health_with_short_timeout():
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        reachable, base = _probe_lemonade_reachable("http://localhost:9999")

    assert reachable is True
    assert base == "http://localhost:9999/api/v1"
    args, kwargs = mock_get.call_args
    # The probe MUST hit /api/v1/health (not the bare host) with the short
    # connect/read timeout — assert the validity of the outgoing call.
    assert args[0] == "http://localhost:9999/api/v1/health"
    assert kwargs["timeout"] == _EXPECTED_TIMEOUT


def test_reachable_probe_treats_any_http_response_as_up():
    # A 500 from Lemonade still means the *server* is up — only a transport
    # failure counts as unreachable.
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=500)
        reachable, _ = _probe_lemonade_reachable("http://localhost:9999")
    assert reachable is True


def test_unreachable_probe_returns_false_not_raises():
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
        reachable, base = _probe_lemonade_reachable("http://localhost:9999")
    assert reachable is False
    assert base == "http://localhost:9999/api/v1"


def test_health_probe_extracts_server_version_from_body():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"version": "10.3.1", "all_models_loaded": []}
    with patch("requests.get", return_value=resp) as mock_get:
        reachable, base, version, loaded = _probe_lemonade_health(
            "http://localhost:9999"
        )
    assert reachable is True
    assert base == "http://localhost:9999/api/v1"
    assert version == "10.3.1"
    assert loaded == []
    # Shares the same /health probe target + short timeout as reachability.
    args, kwargs = mock_get.call_args
    assert args[0] == "http://localhost:9999/api/v1/health"
    assert kwargs["timeout"] == _EXPECTED_TIMEOUT


def test_health_probe_version_none_when_not_advertised():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"all_models_loaded": []}  # no 'version' key
    with patch("requests.get", return_value=resp):
        _, _, version, _ = _probe_lemonade_health("http://localhost:9999")
    assert version is None


def test_health_probe_version_none_when_body_not_json():
    resp = MagicMock(status_code=200)
    resp.json.side_effect = ValueError("no json")
    with patch("requests.get", return_value=resp):
        reachable, _, version, loaded = _probe_lemonade_health("http://localhost:9999")
    # Server is up (HTTP responded) even though the body wasn't JSON.
    assert reachable is True
    assert version is None
    assert loaded == []


# ---------------------------------------------------------------------------
# 3. Model-presence probe — boundary shape
# ---------------------------------------------------------------------------


def test_model_present_queries_models_endpoint_with_short_timeout():
    resp = MagicMock()
    resp.json.return_value = {"data": [{"id": DEFAULT_MODEL_NAME}, {"id": "other"}]}
    with patch("requests.get", return_value=resp) as mock_get:
        present = _probe_model_present(
            "http://localhost:9999/api/v1", DEFAULT_MODEL_NAME
        )

    assert present is True
    args, kwargs = mock_get.call_args
    assert args[0] == "http://localhost:9999/api/v1/models"
    assert kwargs["timeout"] == _EXPECTED_TIMEOUT


def test_model_absent_when_id_not_in_list():
    resp = MagicMock()
    resp.json.return_value = {"data": [{"id": "some-other-model"}]}
    with patch("requests.get", return_value=resp):
        present = _probe_model_present(
            "http://localhost:9999/api/v1", DEFAULT_MODEL_NAME
        )
    assert present is False


def test_model_probe_sends_auth_header_when_key_set(monkeypatch):
    monkeypatch.setenv("LEMONADE_API_KEY", "secret-key")
    resp = MagicMock()
    resp.json.return_value = {"data": []}
    with patch("requests.get", return_value=resp) as mock_get:
        _probe_model_present("http://localhost:9999/api/v1", DEFAULT_MODEL_NAME)
    _, kwargs = mock_get.call_args
    # An authenticated Lemonade server must receive the Bearer header or it 401s.
    assert kwargs["headers"].get("Authorization") == "Bearer secret-key"


# ---------------------------------------------------------------------------
# 4. Model-id resolution mirrors the agent's own resolution
# ---------------------------------------------------------------------------


def test_resolve_email_model_id_defaults_to_agent_default():
    # With no config override the readiness probe reports the same model the
    # triage path loads (config.model_id or DEFAULT_MODEL_NAME).
    assert _resolve_email_model_id() == DEFAULT_MODEL_NAME


# ---------------------------------------------------------------------------
# 5. Route — ready / not-ready status codes and structured body
# ---------------------------------------------------------------------------

_BASE = "http://localhost:8000/api/v1"


def _patch_health(version, loaded_models=()):
    """Patch the GET readiness path's /health probe to a reachable server at
    ``version`` (None = server doesn't advertise one). ``loaded_models``
    mirrors /health's raw ``all_models_loaded`` — empty by default because
    these route tests imply a downloaded (present) model, not a loaded one."""
    return patch.object(
        ar,
        "_probe_lemonade_health",
        return_value=(True, _BASE, version, list(loaded_models)),
    )


def test_init_ready_returns_200(client):
    with (
        _patch_health(MIN_LEMONADE_VERSION),
        patch.object(ar, "_probe_model_present", return_value=True),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["lemonade"] == {
        "reachable": True,
        "base_url": _BASE,
        "version": MIN_LEMONADE_VERSION,
        "min_version": MIN_LEMONADE_VERSION,
        "compatible": True,
    }
    assert body["model"]["present"] is True
    assert body["model"]["id"] == DEFAULT_MODEL_NAME
    # loadable is not probed in v1 — null, never a fabricated bool.
    assert body["model"]["loadable"] is None
    assert body["hint"] is None


def test_init_lemonade_down_returns_503_with_actionable_hint(client):
    with patch.object(
        ar, "_probe_lemonade_health", return_value=(False, _BASE, None, [])
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["lemonade"]["reachable"] is False
    # Hint names what failed AND what to do (CLAUDE.md fail-loudly rule).
    assert "not reachable" in body["hint"]
    assert "lemonade-server serve" in body["hint"]
    # Model is not probed once Lemonade is down — reported absent, not crashed.
    assert body["model"]["present"] is False


def test_init_lemonade_too_old_returns_503_with_upgrade_hint(client):
    # Reachable + model present, but the server is older than the required
    # minimum → not ready, with a found-vs-required upgrade hint.
    with (
        _patch_health("9.0.0"),
        patch.object(ar, "_probe_model_present", return_value=True),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["lemonade"]["version"] == "9.0.0"
    assert body["lemonade"]["min_version"] == MIN_LEMONADE_VERSION
    assert body["lemonade"]["compatible"] is False
    assert "9.0.0" in body["hint"]
    assert MIN_LEMONADE_VERSION in body["hint"]
    assert "upgrade" in body["hint"].lower()


def test_init_too_old_takes_priority_over_missing_model(client):
    # An older-than-min Lemonade is the more fundamental blocker — its hint wins
    # even when the model is also absent (upgrade first).
    with (
        _patch_health("9.0.0"),
        patch.object(ar, "_probe_model_present", return_value=False),
    ):
        body = client.get("/v1/email/init").json()
    assert body["ready"] is False
    assert body["lemonade"]["compatible"] is False
    assert "older than" in body["hint"]


def test_init_newer_lemonade_is_compatible(client):
    with (
        _patch_health("11.5.0"),
        patch.object(ar, "_probe_model_present", return_value=True),
    ):
        resp = client.get("/v1/email/init")
    body = resp.json()
    assert resp.status_code == 200
    assert body["ready"] is True
    assert body["lemonade"]["compatible"] is True


def test_init_unknown_version_is_indeterminate_not_blocking(client):
    # Server didn't advertise a version → compatible=null and readiness is NOT
    # blocked on it (mirrors gaia init's don't-block-on-unparseable policy).
    with (
        _patch_health(None),
        patch.object(ar, "_probe_model_present", return_value=True),
    ):
        resp = client.get("/v1/email/init")
    body = resp.json()
    assert resp.status_code == 200
    assert body["ready"] is True
    assert body["lemonade"]["version"] is None
    assert body["lemonade"]["compatible"] is None
    assert body["hint"] is None


def test_init_model_missing_returns_503_with_model_hint(client):
    with (
        _patch_health(MIN_LEMONADE_VERSION),
        patch.object(ar, "_probe_model_present", return_value=False),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["lemonade"]["reachable"] is True
    assert body["lemonade"]["compatible"] is True
    assert body["model"]["present"] is False
    assert body["model"]["id"] == DEFAULT_MODEL_NAME
    assert "not downloaded" in body["hint"]
    assert "gaia init" in body["hint"]


def test_init_model_list_unreadable_returns_503_loudly(client):
    # Lemonade answered /health but its /models list errored — surface loudly
    # (503 + hint), never silently report "present".
    with (
        _patch_health(MIN_LEMONADE_VERSION),
        patch.object(
            ar,
            "_probe_model_present",
            side_effect=requests.exceptions.ConnectionError("reset"),
        ),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert "model list" in body["hint"]


def test_init_response_forbids_unknown_fields(client):
    # _Strict response models — the serialized body carries exactly the
    # documented keys, no silent extras.
    with (
        _patch_health(MIN_LEMONADE_VERSION),
        patch.object(ar, "_probe_model_present", return_value=True),
    ):
        body = client.get("/v1/email/init").json()
    assert set(body) == {"ready", "lemonade", "model", "hint"}
    assert set(body["lemonade"]) == {
        "reachable",
        "base_url",
        "version",
        "min_version",
        "compatible",
    }
    assert set(body["model"]) == {"id", "present", "loadable", "ctx_size"}


# ---------------------------------------------------------------------------
# 5b. Version helpers + manifest lock-step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("found", "minimum", "expected"),
    [
        ("10.2.0", "10.2.0", True),
        ("10.3.0", "10.2.0", True),
        ("11.0.0", "10.2.0", True),
        ("9.9.9", "10.2.0", False),
        ("10.1.9", "10.2.0", False),
        ("v10.2.0", "10.2.0", True),  # leading 'v' tolerated
        (None, "10.2.0", None),  # unknown → indeterminate
        ("not-a-version", "10.2.0", None),
    ],
)
def test_version_meets_min(found, minimum, expected):
    from gaia_agent_email.api_routes import _version_meets_min

    assert _version_meets_min(found, minimum) is expected


def test_min_lemonade_version_locksteps_with_manifest():
    # ONE source of truth: the runtime constant and the gaia-agent.yaml manifest
    # value `gaia init` reads MUST match, or readiness and install disagree.
    import gaia_agent_email
    import yaml
    from gaia_agent_email.version import MIN_LEMONADE_VERSION as RUNTIME_MIN

    manifest_path = (
        Path(gaia_agent_email.__file__).resolve().parents[1] / "gaia-agent.yaml"
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    declared = manifest["requirements"]["min_lemonade_version"]
    assert declared == RUNTIME_MIN, (
        f"gaia-agent.yaml min_lemonade_version ({declared!r}) != "
        f"version.MIN_LEMONADE_VERSION ({RUNTIME_MIN!r}) — keep in lock-step."
    )


# ---------------------------------------------------------------------------
# 6. OpenAPI + sidecar mount
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6b. GET /v1/email/init tracks the LEMONADE_BASE_URL override (#1888 AC3)
# ---------------------------------------------------------------------------


def test_init_endpoint_tracks_lemonade_base_url_override(monkeypatch, client):
    """Setting LEMONADE_BASE_URL redirects the readiness endpoint's reported
    base_url + resolved model (AC3). Mocks ``requests.get`` directly (NOT the
    ``_probe_*`` helpers) so the assertion isn't tautological.
    """
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9556")
    resolved_model_id = _resolve_email_model_id()
    probe_base = "http://127.0.0.1:9556/api/v1"

    def _fake_get(url, *args, **kwargs):
        if url == f"{probe_base}/health":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"version": MIN_LEMONADE_VERSION}
            return resp
        if url == f"{probe_base}/system-info":
            # No NPU — this test is about base_url tracking (AC3), not
            # NPU auto-select (#1439); keep the resolved model unchanged.
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"devices": {"amd_npu": {"available": False}}}
            return resp
        if url == f"{probe_base}/models":
            resp = MagicMock(status_code=200)
            resp.json.return_value = {"data": [{"id": resolved_model_id}]}
            return resp
        raise AssertionError(f"unexpected probe URL: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    resp = client.get("/v1/email/init")
    body = resp.json()

    assert body["lemonade"]["base_url"] == probe_base
    assert body["model"]["id"] == resolved_model_id
    assert resp.status_code == 200
    assert body["ready"] is True


def test_init_route_in_openapi_with_init_response_model(client):
    spec = build_app().openapi()
    assert "/v1/email/init" in spec["paths"]
    init = spec["paths"]["/v1/email/init"]["get"]
    schema = init["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/InitResponse"}
    assert "503" in init["responses"]
    assert "InitResponse" in spec["components"]["schemas"]


def test_init_route_mounted_via_packaging_server():
    # The frozen-binary sidecar entrypoint (packaging/server.py) must also serve
    # the route. Loaded by file path to dodge the stdlib ``packaging`` name
    # collision.
    import importlib.util

    import gaia_agent_email

    server_path = (
        Path(gaia_agent_email.__file__).resolve().parents[1] / "packaging" / "server.py"
    )
    spec = importlib.util.spec_from_file_location("_email_sidecar_server", server_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = module.build_app()
    # Prove reachability with a real request rather than route introspection:
    # depending on the FastAPI version ``include_router`` either flattens routes
    # into ``app.routes`` or keeps them under a mounted sub-router, so a
    # ``.path`` scan is version-fragile. An HTTP request is not — the route is
    # mounted iff the sidecar app serves it (503 here, Lemonade is down in the
    # test env), and a 404 would prove it is NOT mounted. Probe patched so the
    # test never hits the network.
    with patch.object(
        ar,
        "_probe_lemonade_health",
        return_value=(False, "http://localhost:8000/api/v1", None, []),
    ):
        # Loopback base_url: the sidecar app's caller-auth Host allowlist (#1706)
        # rejects TestClient's default `testserver` Host with 400.
        resp = TestClient(app, base_url="http://127.0.0.1").get("/v1/email/init")
    assert resp.status_code != 404, "/v1/email/init is not mounted on the sidecar app"
    assert resp.status_code == 503
    assert resp.json()["ready"] is False


# ---------------------------------------------------------------------------
# 7. POST /v1/email/init — provisioning (#1795 follow-up). Streams progress.
# ---------------------------------------------------------------------------


def test_pull_model_posts_only_model_name_no_recipe():
    # Built-in models must NOT carry `recipe` or Lemonade 400s (#1655). Assert
    # the SHAPE of the outgoing pull, not merely that it was called.
    resp = MagicMock()
    with patch("requests.post", return_value=resp) as mock_post:
        _pull_model("http://localhost:9999/api/v1", DEFAULT_MODEL_NAME)
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:9999/api/v1/pull"
    assert kwargs["json"] == {"model_name": DEFAULT_MODEL_NAME}
    assert "recipe" not in kwargs["json"]
    resp.raise_for_status.assert_called_once()  # non-2xx pulls fail loudly


def test_pull_model_sends_auth_header_when_key_set(monkeypatch):
    monkeypatch.setenv("LEMONADE_API_KEY", "secret-key")
    with patch("requests.post", return_value=MagicMock()) as mock_post:
        _pull_model("http://localhost:9999/api/v1", DEFAULT_MODEL_NAME)
    _, kwargs = mock_post.call_args
    assert kwargs["headers"].get("Authorization") == "Bearer secret-key"


def test_provision_lemonade_down_returns_503_streamed_actionable(client):
    with (
        patch.object(
            ar,
            "_probe_lemonade_reachable",
            return_value=(False, "http://localhost:8000/api/v1"),
        ),
        patch.object(ar, "_pull_model") as mock_pull,
    ):
        resp = client.post("/v1/email/init")

    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "not reachable" in body
    assert "lemonade-server serve" in body
    # No pull is attempted when Lemonade is down — the sidecar can't install it.
    mock_pull.assert_not_called()


def test_provision_model_already_present_streams_done_without_pull(client):
    with (
        patch.object(
            ar,
            "_probe_lemonade_reachable",
            return_value=(True, "http://localhost:8000/api/v1"),
        ),
        patch.object(ar, "_probe_model_present", return_value=True),
        patch.object(ar, "_pull_model") as mock_pull,
    ):
        resp = client.post("/v1/email/init")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "already downloaded" in body
    # Final authoritative line is a success marker.
    assert body.rstrip().splitlines()[-1].startswith("✓")
    mock_pull.assert_not_called()


def test_provision_pulls_missing_model_and_streams_success(client):
    # First presence check → absent (triggers pull); post-pull verify → present.
    with (
        patch.object(
            ar,
            "_probe_lemonade_reachable",
            return_value=(True, "http://localhost:8000/api/v1"),
        ),
        patch.object(ar, "_probe_model_present", side_effect=[False, True]),
        patch.object(ar, "_pull_model") as mock_pull,
    ):
        resp = client.post("/v1/email/init")

    assert resp.status_code == 200
    body = resp.text
    assert "Pulling" in body
    assert "downloaded" in body
    assert "Verified" in body
    assert body.rstrip().splitlines()[-1].startswith("✓")
    # Pull was invoked once for the resolved model against the probed server.
    mock_pull.assert_called_once_with(
        "http://localhost:8000/api/v1", DEFAULT_MODEL_NAME
    )


def test_provision_pull_failure_streams_failure_line(client):
    with (
        patch.object(
            ar,
            "_probe_lemonade_reachable",
            return_value=(True, "http://localhost:8000/api/v1"),
        ),
        patch.object(ar, "_probe_model_present", side_effect=[False]),
        patch.object(
            ar,
            "_pull_model",
            side_effect=requests.exceptions.HTTPError("400 Bad Request"),
        ),
    ):
        resp = client.post("/v1/email/init")

    # Status was committed to 200 once streaming began; the final ✗ line is the
    # authoritative failure signal (documented HTTP-streaming constraint).
    assert resp.status_code == 200
    body = resp.text
    assert "Provisioning failed" in body
    assert body.rstrip().splitlines()[-1].startswith("✗")


def test_provision_model_list_unreadable_aborts(client):
    with (
        patch.object(
            ar,
            "_probe_lemonade_reachable",
            return_value=(True, "http://localhost:8000/api/v1"),
        ),
        patch.object(
            ar,
            "_probe_model_present",
            side_effect=requests.exceptions.ConnectionError("reset"),
        ),
        patch.object(ar, "_pull_model") as mock_pull,
    ):
        resp = client.post("/v1/email/init")

    body = resp.text
    assert "model list" in body
    assert body.rstrip().splitlines()[-1].startswith("✗")
    mock_pull.assert_not_called()


def test_provision_verb_not_in_openapi_contract():
    # POST is a streaming operational verb (like GET /spec), deliberately kept
    # out of the JSON contract so the cross-impl OpenAPI stays JSON-only.
    spec = build_app().openapi()
    assert "post" not in spec["paths"].get("/v1/email/init", {})


def test_get_init_still_readiness_only_unchanged(client):
    # Guard: adding POST must not change GET's readiness semantics.
    with (
        patch.object(
            ar,
            "_probe_lemonade_health",
            return_value=(
                True,
                "http://localhost:8000/api/v1",
                MIN_LEMONADE_VERSION,
                [],
            ),
        ),
        patch.object(ar, "_probe_model_present", return_value=True),
        patch.object(ar, "_pull_model") as mock_pull,
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 200
    assert resp.json()["ready"] is True
    # GET never provisions.
    mock_pull.assert_not_called()
