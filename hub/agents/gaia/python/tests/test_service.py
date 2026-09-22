# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Container configuration and real ASGI route integration."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from gaia_agent import caller_auth, server, service


@pytest.fixture
def configured(monkeypatch, tmp_path):
    values = {
        "HOME": str(tmp_path),
        "GAIA_SERVICE_WORKSPACE": str(tmp_path),
        "GAIA_SERVICE_MODEL": "fireworks.test-model",
        "GAIA_SERVICE_ALLOWED_HOSTS": "worker.internal,localhost",
        "LEMONADE_BASE_URL": "http://inference.internal:8000/api/v1",
        caller_auth.TOKEN_ENV_VAR: "test-service-secret",
    }
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv("PORT", raising=False)
    for key in (
        "LEMONADE_API_KEY",
        "GAIA_SERVICE_CLOUD_PROVIDER",
        "GAIA_SERVICE_CLOUD_URL",
        "GAIA_SERVICE_LEMONADE_BUNDLE",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield service.ServiceConfig.from_environment()
    caller_auth.reset()


@pytest.mark.parametrize(
    "variable",
    [
        "GAIA_SERVICE_MODEL",
        "GAIA_SERVICE_ALLOWED_HOSTS",
        "GAIA_SERVICE_WORKSPACE",
        "HOME",
        caller_auth.TOKEN_ENV_VAR,
    ],
)
def test_missing_required_configuration_fails(configured, monkeypatch, variable):
    monkeypatch.delenv(variable)
    with pytest.raises(ValueError, match="required|requires"):
        service.ServiceConfig.from_environment()


@pytest.mark.parametrize(
    "hosts",
    ["*", "", "worker:8080", "https://worker", "worker,", "user@worker", "bad host"],
)
def test_invalid_hosts_fail(configured, monkeypatch, hosts):
    monkeypatch.setenv("GAIA_SERVICE_ALLOWED_HOSTS", hosts)
    with pytest.raises(ValueError):
        service.ServiceConfig.from_environment()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/x",
        "http://user:secret@host",
        "https://host/?secret=x",
        "https://host/#x",
    ],
)
def test_invalid_inference_urls_fail(configured, monkeypatch, url):
    monkeypatch.setenv("LEMONADE_BASE_URL", url)
    with pytest.raises(ValueError, match="LEMONADE_BASE_URL"):
        service.ServiceConfig.from_environment()


@pytest.mark.parametrize("port", ["0", "65536", "abc"])
def test_invalid_port_fails(configured, monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(ValueError):
        service.ServiceConfig.from_environment()


def test_secret_file_is_supported(configured, monkeypatch, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("from-file\n")
    monkeypatch.setenv(caller_auth.TOKEN_FILE_ENV_VAR, str(secret))
    assert service.ServiceConfig.from_environment().auth.token == "from-file"


def test_embedded_rejects_inherited_external_key(configured, monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL")
    monkeypatch.setenv("LEMONADE_API_KEY", "wrong-for-embedded")
    with pytest.raises(ValueError, match="Unset LEMONADE_API_KEY"):
        service.ServiceConfig.from_environment()


def test_cloud_bootstrap_requires_paired_config_and_key(configured, monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL")
    monkeypatch.setenv("GAIA_SERVICE_CLOUD_PROVIDER", "example")
    with pytest.raises(ValueError, match="Set both"):
        service.ServiceConfig.from_environment()
    monkeypatch.setenv("GAIA_SERVICE_CLOUD_URL", "https://gateway.example/v1")
    with pytest.raises(ValueError, match="LEMONADE_EXAMPLE_API_KEY"):
        service.ServiceConfig.from_environment()
    monkeypatch.setenv("LEMONADE_EXAMPLE_API_KEY", "scoped-key")
    assert service.ServiceConfig.from_environment().cloud_provider == "example"


def test_cloud_registration_wire_contract(configured, monkeypatch):
    import requests

    calls = []
    monkeypatch.setenv("LEMONADE_API_KEY", "private-lemonade-secret")

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(ok=True, json=lambda: {"status": "success"})

    monkeypatch.setattr(requests, "post", post)
    service._configure_cloud(
        replace(
            configured, cloud_provider="example", cloud_url="https://gateway.example/v1"
        ),
        "http://localhost:1234/api/v1",
    )
    assert calls == [
        (
            "http://localhost:1234/api/v1/install",
            {
                "headers": {"Authorization": "Bearer private-lemonade-secret"},
                "json": {
                    "backend": "cloud",
                    "provider": "example",
                    "base_url": "https://gateway.example/v1",
                },
                "timeout": 60,
            },
        )
    ]


def test_cloud_registration_failure_does_not_echo_provider_body(
    configured, monkeypatch
):
    import requests

    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: SimpleNamespace(
            ok=False, status_code=401, text="secret-provider-body"
        ),
    )
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        service._configure_cloud(configured, "http://localhost:1234/api/v1")
    assert "secret-provider-body" not in str(error.value)


def test_service_auth_host_and_origin(configured):
    with TestClient(
        service.create_app(configured), base_url="http://worker.internal"
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/gaia/memory").status_code == 401
        assert (
            client.get("/health", headers={"Host": "evil.example"}).status_code == 400
        )
        assert (
            client.get("/health", headers={"Origin": "http://localhost"}).status_code
            == 403
        )
        assert client.app.state.warmup_task is None


def test_desktop_remains_loopback_only(configured):
    with TestClient(
        server.build_app(warmup=False), base_url="http://worker.internal"
    ) as client:
        assert client.get("/health").status_code == 400


@pytest.mark.parametrize("present,status", [(True, 200), (False, 503)])
def test_readiness_uses_selected_model_without_exposing_config(
    configured, monkeypatch, present, status
):
    observed = []

    def probe(**kwargs):
        observed.append(kwargs)
        return {"reachable": True, "present": present, "version": "11.8.1"}

    monkeypatch.setattr(server, "_probe_lemonade", probe)
    with TestClient(
        service.create_app(configured), base_url="http://worker.internal"
    ) as client:
        response = client.get("/ready")
    assert response.status_code == status
    assert response.json() == {"ready": present}
    assert observed == [{"model_id": configured.model, "base_url": configured.base_url}]


def test_probe_authenticates_models_and_health(configured, monkeypatch):
    import requests

    monkeypatch.setenv("LEMONADE_API_KEY", "inference-secret")
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        body = (
            {"data": [{"id": configured.model}]}
            if "/models" in url
            else {"version": "11.8.1"}
        )
        return SimpleNamespace(
            ok=True, json=lambda: body, raise_for_status=lambda: None
        )

    monkeypatch.setattr(requests, "get", get)
    result = server._probe_lemonade(configured.model, configured.base_url)
    assert result["present"] and result["reachable"]
    assert len(calls) == 2
    assert all(
        call[1]["headers"] == {"Authorization": "Bearer inference-secret"}
        for call in calls
    )
    assert all(call[1]["timeout"] == 5 for call in calls)


def test_query_passes_workspace_and_model_to_agent(configured, monkeypatch):
    observed = []

    class Agent:
        conversation_history = []
        console = None

        def process_query(self, query, **kwargs):
            return {"answer": "service result"}

        def close(self):
            pass

    def build(**kwargs):
        observed.append(kwargs)
        return Agent()

    monkeypatch.setattr(server, "build_query_agent", build)
    with TestClient(
        service.create_app(configured), base_url="http://worker.internal"
    ) as client:
        response = client.post(
            "/v1/gaia/query",
            headers={"Authorization": "Bearer test-service-secret"},
            json={"query": "hello", "run_id": str(uuid.uuid4()), "context": []},
        )
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert response.status_code == 200
    assert events[-1]["type"] == "final"
    assert observed[0]["model_id"] == configured.model
    assert observed[0]["allowed_paths"] == [str(configured.workspace)]
    assert observed[0]["project_root"] == str(configured.workspace)


def test_embedded_lifecycle_owns_start_and_stop(configured, monkeypatch):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    calls = []
    monkeypatch.delenv("GAIA_SERVICE_LEMONADE_BUNDLE", raising=False)
    monkeypatch.setattr(
        EmbeddedLemonade,
        "status",
        lambda self: SimpleNamespace(running=False, unresponsive_pid=None),
    )

    def start(self, **kwargs):
        calls.append(("start", kwargs))
        return SimpleNamespace(base_url="http://localhost:43210/api/v1")

    monkeypatch.setattr(EmbeddedLemonade, "start", start)
    monkeypatch.setattr(
        EmbeddedLemonade, "stop", lambda self: calls.append(("stop", {}))
    )
    with TestClient(
        service.create_app(replace(configured, base_url=None)),
        base_url="http://worker.internal",
    ) as client:
        assert (
            client.app.state.agent_config["base_url"] == "http://localhost:43210/api/v1"
        )
        assert len(calls) == 1
    assert calls == [("start", {"install_if_missing": False}), ("stop", {})]


def test_failed_embedded_start_cleans_up(configured, monkeypatch):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    stopped = []
    monkeypatch.delenv("GAIA_SERVICE_LEMONADE_BUNDLE", raising=False)
    monkeypatch.setattr(
        EmbeddedLemonade,
        "status",
        lambda self: SimpleNamespace(running=False, unresponsive_pid=None),
    )

    def fail(self, **kwargs):
        raise RuntimeError("backend did not start")

    monkeypatch.setattr(EmbeddedLemonade, "start", fail)
    monkeypatch.setattr(EmbeddedLemonade, "stop", lambda self: stopped.append(True))
    with pytest.raises(RuntimeError, match="backend did not start"):
        with TestClient(service.create_app(replace(configured, base_url=None))):
            pass
    assert stopped == [True]


def test_service_cli_real_http_and_shutdown(configured, tmp_path):
    import httpx

    class Inference(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer inference-test-key"
            body = (
                {"data": [{"id": configured.model}]}
                if "/models" in self.path
                else {"version": "11.8.1"}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Inference)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "PORT": str(port),
        "GAIA_SERVICE_HOST": "127.0.0.1",
        "GAIA_SERVICE_ALLOWED_HOSTS": "127.0.0.1",
        "LEMONADE_BASE_URL": f"http://127.0.0.1:{upstream.server_port}/api/v1",
        "LEMONADE_API_KEY": "inference-test-key",
    }
    log = tmp_path / "service.log"
    try:
        with log.open("w") as output:
            process = subprocess.Popen(
                [str(Path(sys.executable).with_name("gaia-agent-service"))],
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 15
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}", trust_env=False
                ) as client:
                    while True:
                        if process.poll() is not None:
                            pytest.fail(log.read_text())
                        try:
                            response = client.get("/health")
                            break
                        except httpx.ConnectError:
                            if time.monotonic() > deadline:
                                pytest.fail("Service did not bind within 15 seconds")
                            time.sleep(0.05)
                    assert response.status_code == 200
                    assert client.get("/ready").json() == {"ready": True}
                    assert client.get("/v1/gaia/init").status_code == 401
                    ready = client.get(
                        "/v1/gaia/init",
                        headers={"Authorization": "Bearer test-service-secret"},
                    )
                    assert ready.status_code == 200
                    assert ready.json()["model"]["id"] == configured.model
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    pytest.fail("Service did not terminate gracefully")
            assert process.returncode in (0, -15)
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
