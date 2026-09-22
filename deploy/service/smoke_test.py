# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise a built image with embedded Lemonade, without model downloads."""

import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def run(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def main():
    invalid = subprocess.run(
        ["docker", "run", "--rm", "gaia-service:test"],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert invalid.returncode != 0, "Missing configuration must reject startup"
    assert "requires a bearer token" in invalid.stdout + invalid.stderr
    name = "gaia-service-smoke-" + uuid.uuid4().hex[:8]
    run(
        "run",
        "-d",
        "--name",
        name,
        "-p",
        "127.0.0.1::8080",
        "-e",
        "GAIA_SERVICE_ALLOWED_HOSTS=localhost,127.0.0.1",
        "-e",
        "GAIA_GAIA_SIDECAR_TOKEN=smoke-test-only",
        "-e",
        "GAIA_SERVICE_MODEL=not-installed-smoke-model",
        "gaia-service:test",
    )
    try:
        binding = run("port", name, "8080/tcp").splitlines()[0]
        url = f"http://{binding}"
        deadline = time.monotonic() + 90
        while True:
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as response:
                    assert json.load(response)["status"] == "ok"
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if run("inspect", "--format", "{{.State.Status}}", name) == "exited":
                    raise RuntimeError("Container exited before becoming live")
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Container did not become live within 90 seconds"
                    )
                time.sleep(1)
        for path, expected in [("/ready", 503), ("/v1/gaia/memory", 401)]:
            try:
                urllib.request.urlopen(url + path, timeout=15)
            except urllib.error.HTTPError as exc:
                assert exc.code == expected, (path, exc.code)
            else:
                raise AssertionError(f"{path} should return {expected}")
        assert run("exec", name, "id", "-u") == "10001"
        for headers in [
            {"Host": "untrusted.example"},
            {"Origin": "https://untrusted.example"},
        ]:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url + "/health", headers=headers), timeout=5
                )
            except urllib.error.HTTPError as exc:
                assert exc.code in {400, 403}, (headers, exc.code)
            else:
                raise AssertionError(f"Untrusted caller accepted: {headers}")
        run(
            "exec",
            name,
            "sh",
            "-c",
            'test ! -e /opt/venv && ! command -v pip && test -x "$GAIA_PYTHON_EXECUTABLE"',
        )
        run("stop", "--time", "45", name)
        # Uvicorn re-raises SIGTERM after graceful lifespan cleanup; tini reports 143.
        exit_code = run("inspect", "--format", "{{.State.ExitCode}}", name)
        assert exit_code in {"0", "143"}, exit_code
        assert "Embedded Lemonade stopped" in run("logs", name)
        print(
            "Frozen container configuration rejection, startup, readiness failure, "
            "authentication, Host/Origin rejection, non-root user, "
            "absence of a GAIA virtual environment and shutdown passed."
        )
    finally:
        print(run("logs", name))
        run("rm", "-f", name)


if __name__ == "__main__":
    main()
