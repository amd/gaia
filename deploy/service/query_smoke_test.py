# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Test the frozen CLI and real containerized GAIA against fixture inference."""

import json
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryFile


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], text=True, capture_output=True, **kwargs)


def checked(*args):
    result = docker(*args, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def main():
    suffix = uuid.uuid4().hex[:8]
    network, inference, worker = [
        f"gaia-{name}-{suffix}" for name in ("net", "inference", "worker")
    ]
    created = []
    volumes = []
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
        for label in ("state", "workspace"):
            volume = f"gaia-{label}-{suffix}"
            checked("volume", "create", volume)
            volumes.append(volume)
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
            "--mount",
            f"type=volume,src={volumes[0]},dst=/data",
            "--mount",
            f"type=volume,src={volumes[1]},dst=/workspace",
            "gaia-service:test",
        )
        created.append(worker)
        client = ("exec", worker, "gaia-agent", "--client")
        deadline = time.monotonic() + 60
        while True:
            result = docker(*client, "status", timeout=20)
            if result.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("CLI status did not become ready: " + result.stderr)
            time.sleep(0.5)
        result = checked(*client, "query", "Reply with the fixture answer", "--json")
        events = [json.loads(line) for line in result.splitlines()]
        assert events[-1]["type"] == "final", events
        assert events[-1]["answer"].startswith(
            "Container inference fixture passed."
        ), events
        run_id = str(uuid.uuid4())
        with TemporaryFile(mode="w+") as output, TemporaryFile(mode="w+") as errors:
            process = subprocess.Popen(
                [
                    "docker",
                    *client,
                    "query",
                    "fixture-wait",
                    "--run-id",
                    run_id,
                    "--json",
                ],
                stdout=output,
                stderr=errors,
                text=True,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    output.seek(0)
                    if output.read().strip():
                        break
                    if process.poll() is not None or time.monotonic() > deadline:
                        errors.seek(0)
                        raise RuntimeError(
                            "No active stream to cancel: " + errors.read()
                        )
                    time.sleep(0.1)
                stopped = json.loads(checked(*client, "cancel", run_id))
                assert stopped["cancelled"] is True, stopped
                process.wait(timeout=30)
                output.seek(0)
                cancelled_events = [json.loads(line) for line in output]
                assert cancelled_events[-1]["type"] == "error", cancelled_events
                assert process.returncode == 1
                assert cancelled_events[-1].get("status") == 499, cancelled_events
                assert "Container inference fixture passed." not in cancelled_events[
                    -1
                ].get("answer", "")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        # Unknown cancellation is explicit and repeatable, not a fake success.
        cancelled = json.loads(checked(*client, "cancel", str(uuid.uuid4())))
        assert cancelled["cancelled"] is False
        checked(
            "exec",
            worker,
            "sh",
            "-c",
            "printf persistent > /workspace/restart-check; printf state > /data/restart-check",
        )
        checked("restart", "--time", "50", worker)
        deadline = time.monotonic() + 30
        while docker(*client, "status", timeout=20).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError("Worker did not become ready after restart")
            time.sleep(0.5)
        assert (
            checked("exec", worker, "cat", "/workspace/restart-check") == "persistent"
        )
        assert checked("exec", worker, "cat", "/data/restart-check") == "state"
        print(
            "Frozen CLI status, real GAIA query/SSE, live cancellation and restart passed."
        )
    finally:
        for name in reversed(created):
            logs = docker("logs", name, timeout=10)
            print(logs.stdout + logs.stderr)
            checked("rm", "-f", name)
        checked("network", "rm", network)
        for volume in volumes:
            checked("volume", "rm", volume)


if __name__ == "__main__":
    main()
