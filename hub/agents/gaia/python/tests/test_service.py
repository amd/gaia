# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Container configuration and real ASGI route integration."""

import asyncio
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
        "GAIA_HOME": str(tmp_path / ".gaia"),
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
        return SimpleNamespace(base_url="http://localhost:43210/api/v1", pid=1234)

    monkeypatch.setattr(EmbeddedLemonade, "start", start)
    monkeypatch.setattr(
        EmbeddedLemonade, "stop", lambda self, **kwargs: calls.append(("stop", kwargs))
    )
    with TestClient(
        service.create_app(replace(configured, base_url=None)),
        base_url="http://worker.internal",
    ) as client:
        assert (
            client.app.state.agent_config["base_url"] == "http://localhost:43210/api/v1"
        )
        assert len(calls) == 1
    assert calls == [
        ("start", {"install_if_missing": False, "reuse_existing": False}),
        ("stop", {"expected_pid": 1234}),
    ]


def test_failed_embedded_start_does_not_stop_an_unowned_daemon(configured, monkeypatch):
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
    assert stopped == []


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


@pytest.mark.parametrize(
    "catalog_id,expected", [("Qwen3-4B-GGUF", 503), ("Qwen3", 200)]
)
def test_service_readiness_requires_exact_model_id(
    configured, monkeypatch, catalog_id, expected
):
    import requests

    def get(url, **kwargs):
        body = (
            {"data": [{"id": catalog_id, "checkpoint": "unsloth/Qwen3-4B-GGUF:Q4_K_M"}]}
            if "/models" in url
            else {"version": "11.8.1"}
        )
        return SimpleNamespace(
            ok=True, json=lambda: body, raise_for_status=lambda: None
        )

    monkeypatch.setattr(requests, "get", get)
    with TestClient(
        service.create_app(replace(configured, model="Qwen3")),
        base_url="http://worker.internal",
    ) as client:
        assert client.get("/ready").status_code == expected
        response = client.get(
            "/v1/gaia/init", headers={"Authorization": "Bearer test-service-secret"}
        )
        assert response.status_code == expected
        assert response.json()["model"]["present"] is (expected == 200)


def test_racing_embedded_start_does_not_adopt_or_stop_existing_daemon(
    configured, monkeypatch
):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError

    states = iter(
        [
            SimpleNamespace(running=False, unresponsive_pid=None),
            SimpleNamespace(running=True, unresponsive_pid=None),
        ]
    )
    monkeypatch.setattr(EmbeddedLemonade, "_status", lambda self: next(states))
    monkeypatch.setattr(
        EmbeddedLemonade,
        "stop",
        lambda *args, **kwargs: pytest.fail("Must not stop an unowned process"),
    )
    with pytest.raises(EmbeddedLemonadeError, match="exclusive ownership"):
        with TestClient(service.create_app(replace(configured, base_url=None))):
            pass


@pytest.mark.asyncio
async def test_cancelled_startup_waits_for_owned_daemon_cleanup(
    configured, monkeypatch
):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    entered = threading.Event()
    release = threading.Event()
    stopped = []
    monkeypatch.delenv("GAIA_SERVICE_LEMONADE_BUNDLE", raising=False)
    monkeypatch.setattr(
        EmbeddedLemonade,
        "status",
        lambda self: SimpleNamespace(running=False, unresponsive_pid=None),
    )

    def start(self, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(base_url="http://localhost:43210/api/v1", pid=1234)

    monkeypatch.setattr(EmbeddedLemonade, "start", start)
    monkeypatch.setattr(
        EmbeddedLemonade, "stop", lambda self, **kwargs: stopped.append(kwargs)
    )
    app = service.create_app(replace(configured, base_url=None))
    lifespan = app.router.lifespan_context(app)
    task = asyncio.create_task(lifespan.__aenter__())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert stopped == [{"expected_pid": 1234}]


@pytest.mark.parametrize(
    "name",
    [
        "GAIA_SERVICE_MAX_REQUEST_BYTES",
        "GAIA_SERVICE_MAX_CONCURRENT_RUNS",
        "GAIA_SERVICE_MAX_STEPS",
        "GAIA_SERVICE_RUN_TIMEOUT_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1", "nan", "1.5", ""])
def test_service_limits_require_positive_integers(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        service.ServiceConfig.from_environment()


def test_service_limits_defaults_and_environment(configured, monkeypatch):
    assert configured.max_request_bytes == 1048576
    assert configured.max_concurrent_runs == 1
    assert configured.max_steps == 20
    assert configured.run_timeout_seconds == 300
    monkeypatch.setenv("GAIA_SERVICE_MAX_CONCURRENT_RUNS", "2")
    assert service.ServiceConfig.from_environment().max_concurrent_runs == 2


def test_service_rejects_large_body_before_agent_construction(configured, monkeypatch):
    def unexpected(**_kwargs):
        pytest.fail("Rejected request constructed an agent")

    monkeypatch.setattr(server, "build_query_agent", unexpected)
    with TestClient(
        service.create_app(replace(configured, max_request_bytes=128)),
        base_url="http://worker.internal",
    ) as client:
        response = client.post(
            "/v1/gaia/query",
            headers={"Authorization": "Bearer test-service-secret"},
            json={"query": "x" * 129, "context": [], "run_id": str(uuid.uuid4())},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "Request body exceeds 128 bytes."}
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("action", ["deadline", "cancel"])
def test_capacity_held_until_worker_exits(configured, monkeypatch, action):
    entered, release, closed = (threading.Event() for _ in range(3))
    calls = []

    class Agent:
        def process_query(self, _query, max_steps=None):
            calls.append(max_steps)
            entered.set()
            assert release.wait(5)
            return {"answer": "done"}

        def close(self):
            closed.set()

    monkeypatch.setattr(server, "build_query_agent", lambda **_kw: Agent())
    app = service.create_app(configured)
    if action == "deadline":
        app.state.query_timeout_seconds = 0.1
    with TestClient(app, base_url="http://worker.internal") as client:
        headers = {"Authorization": "Bearer test-service-secret"}
        body = {"query": "test", "context": [], "run_id": str(uuid.uuid4())}
        results = []
        thread = threading.Thread(
            target=lambda: results.append(
                client.post("/v1/gaia/query", json=body, headers=headers)
            )
        )
        thread.start()
        try:
            assert entered.wait(2)
            assert client.get("/health").status_code == 200
            response = client.post(
                "/v1/gaia/query",
                json={**body, "run_id": str(uuid.uuid4())},
                headers=headers,
            )
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "1"
            if action == "cancel":
                response = client.post(
                    f"/v1/gaia/query/{body['run_id']}/cancel", headers=headers
                )
                assert response.json()["cancelled"] is True
            thread.join(2)
            assert not thread.is_alive()
            events = [
                json.loads(line[6:])
                for line in results[0].text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1]["status"] == (504 if action == "deadline" else 499)
            assert (
                client.post(
                    "/v1/gaia/query",
                    json={**body, "run_id": str(uuid.uuid4())},
                    headers=headers,
                ).status_code
                == 503
            )
            assert calls == [20]
        finally:
            release.set()
            thread.join(2)
        assert closed.wait(2)
        # Closing the agent precedes releasing admission; wait on the actual semaphore.
        assert app.state.query_slots.acquire(timeout=2)
        app.state.query_slots.release()
        response = client.post(
            "/v1/gaia/query",
            json={**body, "run_id": str(uuid.uuid4()), "max_steps": 2},
            headers=headers,
        )
        assert response.status_code == 200
        assert calls == [20, 2]


def test_step_ceiling_and_failed_setup_leave_capacity_available(
    configured, monkeypatch
):
    def fail(**_kwargs):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(server, "build_query_agent", fail)
    app = service.create_app(configured)
    with TestClient(app, base_url="http://worker.internal") as client:
        headers = {"Authorization": "Bearer test-service-secret"}
        body = {"query": "test", "context": [], "run_id": str(uuid.uuid4())}
        assert (
            client.post(
                "/v1/gaia/query", json={**body, "max_steps": 21}, headers=headers
            ).status_code
            == 422
        )
        for _ in range(2):
            assert (
                client.post("/v1/gaia/query", json=body, headers=headers).status_code
                == 500
            )
        assert app.state.query_slots.acquire(blocking=False)
        app.state.query_slots.release()


@pytest.mark.asyncio
async def test_deadline_cancels_worker_even_when_sse_client_stops_reading(
    configured, monkeypatch
):
    stopped = threading.Event()
    send_blocked, resume_send = asyncio.Event(), asyncio.Event()

    class Agent:
        def process_query(self, _query, max_steps=None):
            self.console.print_processing_start("working", 20)
            assert self._cancel_event.wait(3), "Deadline depended on blocked SSE send"
            assert self.console.cancelled.wait(1)
            stopped.set()
            return {"answer": "cancelled"}

        def close(self):
            pass

    monkeypatch.setattr(server, "build_query_agent", lambda **_kw: Agent())
    app = service.create_app(configured)
    app.state.query_timeout_seconds = 0.15
    response = await server.query(
        server.QueryRequest(query="test", context=[], run_id=str(uuid.uuid4())),
        SimpleNamespace(app=app),
    )

    async def receive():
        await asyncio.sleep(5)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and not send_blocked.is_set():
            send_blocked.set()
            await resume_send.wait()

    stream = asyncio.create_task(
        response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    )
    try:
        await asyncio.wait_for(send_blocked.wait(), 2)
        assert await asyncio.to_thread(stopped.wait, 2)
        assert not stream.done(), "Send was not stalled during cancellation"
    finally:
        resume_send.set()
        await asyncio.wait_for(stream, 2)
    assert app.state.query_slots.acquire(timeout=2)
    app.state.query_slots.release()


def test_configured_parallel_capacity_admits_two_and_rejects_third(
    configured, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    release, both_started = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = []

    class Agent:
        def process_query(self, query, max_steps=None):
            with lock:
                active.append(query)
                if len(active) == 2:
                    both_started.set()
            assert release.wait(5)
            return {"answer": query}

        def close(self):
            pass

    monkeypatch.setattr(server, "build_query_agent", lambda **_kw: Agent())
    app = service.create_app(replace(configured, max_concurrent_runs=2))
    with TestClient(app, base_url="http://worker.internal") as client:

        def submit(query):
            return client.post(
                "/v1/gaia/query",
                json={"query": query, "context": [], "run_id": str(uuid.uuid4())},
                headers={"Authorization": "Bearer test-service-secret"},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, name) for name in ("first", "second")]
            try:
                assert both_started.wait(2)
                assert submit("third").status_code == 503
            finally:
                release.set()
            assert all(
                future.result(timeout=3).status_code == 200 for future in futures
            )
        assert sorted(active) == ["first", "second"]
        assert app.state.query_slots.acquire(timeout=2)
        assert app.state.query_slots.acquire(timeout=2)
        app.state.query_slots.release()
        app.state.query_slots.release()
