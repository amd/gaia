# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise service limits over real HTTP in the frozen Linux container."""

import concurrent.futures
import http.client
import json
import sys
import threading
import time
import uuid
from pathlib import Path

from query_smoke_test import checked, docker


def exercise():
    def request(body, path="/v1/gaia/query", chunks=False):
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=20)
        headers = {
            "Authorization": "Bearer fixture-worker-token",
            "Content-Type": "application/json",
        }
        connection.request(
            "POST", path, body=body, headers=headers, encode_chunked=chunks
        )
        response = connection.getresponse()
        result = (
            response.status,
            response.read().decode(),
            response.getheader("Retry-After"),
        )
        connection.close()
        return result

    def query(text="Reply with fixture answer", steps=1):
        return json.dumps(
            {
                "query": text,
                "context": [],
                "run_id": str(uuid.uuid4()),
                "max_steps": steps,
            }
        )

    assert request("x" * 2049)[0] == 413
    assert request(iter([b"x" * 1024, b"x" * 1025]), chunks=True)[0] == 413
    assert request(query(steps=4))[0] == 422
    print(
        "PASS HTTP: known-length and chunked overflow => 413; excessive steps => 422",
        flush=True,
    )

    barrier = threading.Barrier(8)

    def slow_query():
        barrier.wait(timeout=10)
        return request(query("fixture-wait"))

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: slow_query(), range(8)))
    assert sum(status == 503 for status, _, _ in results) == 7, results
    accepted = [body for status, body, _ in results if status == 200]
    assert len(accepted) == 1, results
    events = [
        json.loads(line[6:])
        for line in accepted[0].splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["status"] == 504, events
    assert all(retry == "1" for status, _, retry in results if status == 503)
    assert (
        request(query())[0] == 503
    ), "Deadline freed capacity before provider work stopped"
    print(
        f"PASS HTTP: burst=8, accepted=1, busy=7, terminal deadline=504, elapsed={time.monotonic()-started:.2f}s",
        flush=True,
    )

    def recover():
        deadline = time.monotonic() + 20
        while True:
            status, body, _ = request(query())
            if status != 503:
                assert status == 200 and '"type": "final"' in body, (status, body)
                return
            if time.monotonic() > deadline:
                raise AssertionError(
                    "Capacity did not recover after provider completed"
                )
            time.sleep(0.2)

    recover()
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=20)
    connection.request(
        "POST",
        "/v1/gaia/query",
        body=query("fixture-wait"),
        headers={
            "Authorization": "Bearer fixture-worker-token",
            "Content-Type": "application/json",
        },
    )
    response = connection.getresponse()
    assert response.status == 200
    assert response.readline(), "No initial progress event"
    # Give the upstream call time to begin, then drop the actual TCP connection.
    time.sleep(0.5)
    response.close()
    connection.close()
    assert request(query())[0] == 503
    recover()
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=10)
    connection.request("GET", "/health")
    response = connection.getresponse()
    assert response.status == 200 and json.loads(response.read())["status"] == "ok"
    connection.close()
    print(
        "PASS HTTP: disconnect retains occupied slot; provider completion restores successful queries and health",
        flush=True,
    )


def main():
    suffix = uuid.uuid4().hex[:8]
    network, inference, worker = [
        f"gaia-limit-{name}-{suffix}" for name in ("net", "inference", "worker")
    ]
    created = []
    checked("network", "create", "--internal", network)
    try:
        checked(
            "run",
            "-d",
            "--name",
            inference,
            "--network",
            network,
            "--mount",
            f"type=bind,src={Path(__file__).with_name('inference_fixture.py').resolve()},dst=/fixture.py,readonly",
            "--entrypoint",
            "python3",
            "gaia-service:test",
            "/fixture.py",
        )
        created.append(inference)
        checked(
            "run",
            "-d",
            "--name",
            worker,
            "--network",
            network,
            "-e",
            "GAIA_GAIA_SIDECAR_TOKEN=fixture-worker-token",
            "-e",
            "GAIA_SERVICE_ALLOWED_HOSTS=127.0.0.1,localhost",
            "-e",
            "GAIA_SERVICE_MODEL=fireworks.container-fixture",
            "-e",
            f"LEMONADE_BASE_URL=http://{inference}:8099/api/v1",
            "-e",
            "LEMONADE_API_KEY=fixture-inference-key",
            "-e",
            "GAIA_MEMORY_DISABLED=1",
            "-e",
            "GAIA_SERVICE_MAX_REQUEST_BYTES=2048",
            "-e",
            "GAIA_SERVICE_MAX_STEPS=3",
            "-e",
            "GAIA_SERVICE_RUN_TIMEOUT_SECONDS=3",
            "gaia-service:test",
        )
        created.append(worker)
        deadline = time.monotonic() + 60
        while docker(
            "exec", worker, "gaia-agent", "--client", "status", timeout=20
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("Worker did not become ready")
            time.sleep(0.5)
        # Standalone stdlib script runs inside the service network. No host dependencies.
        source = (
            Path(__file__)
            .read_text()
            .replace("from query_smoke_test import checked, docker", "")
        )
        result = docker(
            "exec", "-i", worker, "python3", "-", "--inside", input=source, timeout=120
        )
        print(result.stdout, end="")
        if result.returncode:
            raise RuntimeError(result.stderr)
        # Losing inference must change readiness without killing the HTTP worker.
        checked("stop", "--time", "5", inference)
        probe = """import urllib.request, urllib.error
for path, expected in [('/health', 200), ('/ready', 503)]:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8080'+path, timeout=15) as response:
   status=response.status
 except urllib.error.HTTPError as error:
  status=error.code
 assert status == expected, (path, status)
 print('PASS HTTP: upstream offline', path, '=>', status)
"""
        result = docker("exec", "-i", worker, "python3", "-", input=probe, timeout=40)
        print(result.stdout, end="")
        if result.returncode:
            raise RuntimeError(result.stderr)
        checked("start", inference)
        deadline = time.monotonic() + 30
        while docker(
            "exec", worker, "gaia-agent", "--client", "status", timeout=20
        ).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("Readiness did not recover after inference restart")
            time.sleep(0.5)
        result = checked(
            "exec",
            worker,
            "gaia-agent",
            "--client",
            "query",
            "Reply with the fixture answer",
            "--json",
        )
        events = [json.loads(line) for line in result.splitlines()]
        assert events[-1]["type"] == "final", events
        print(
            "PASS HTTP: inference restart restores readiness and successful frozen CLI query"
        )
    finally:
        for name in reversed(created):
            checked("rm", "-f", name)
        checked("network", "rm", network)


if __name__ == "__main__":
    if "--inside" in sys.argv:
        exercise()
    else:
        main()
