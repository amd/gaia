# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise the shipped Compose configuration without a model or credentials."""

import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


def main():
    project = "gaia-compose-test-" + uuid.uuid4().hex[:8]
    directory = Path(__file__).resolve().parent
    # Compose gives process variables precedence over the explicit env file.
    # Never let a developer's model or credentials enter a deterministic test.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GAIA_", "LEMONADE_", "COMPOSE_"))
    }
    with tempfile.TemporaryDirectory(prefix="gaia-compose-test-") as temporary:
        root = Path(temporary)
        config = root / "service.env"
        config.write_text(
            "GAIA_GAIA_SIDECAR_TOKEN=compose-test-only\n"
            "GAIA_SERVICE_MODEL=not-installed-smoke-model\n"
            "GAIA_SERVICE_PORT=0\n"
        )
        config.chmod(0o600)
        override = root / "override.yaml"
        override.write_text(
            "services:\n  gaia:\n    image: gaia-service:test\n"
            "    healthcheck:\n      interval: 1s\n      start_period: 90s\n"
        )
        base = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--env-file",
            str(config),
            "-f",
            str(directory / "compose.yaml"),
            "-f",
            str(override),
        ]

        def compose(*args, check=True):
            result = subprocess.run(
                [*base, *args], env=env, text=True, capture_output=True, timeout=180
            )
            if check and result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            return result

        def up():
            compose("up", "--no-build", "-d", "--wait", "--wait-timeout", "120")

        try:
            compose("config", "--quiet")
            up()
            before = compose("ps", "-q", "gaia").stdout.strip()
            settings = json.loads(
                subprocess.check_output(
                    ["docker", "inspect", before], env=env, text=True, timeout=10
                )
            )[0]
            assert settings["State"]["Health"]["Status"] == "healthy"
            assert settings["Config"]["User"] == "gaia"
            assert settings["HostConfig"]["Memory"] == 4 * 1024**3
            assert settings["HostConfig"]["NanoCpus"] == 2 * 10**9
            assert settings["HostConfig"]["PidsLimit"] == 256
            assert "ALL" in settings["HostConfig"]["CapDrop"]
            assert "no-new-privileges:true" in settings["HostConfig"]["SecurityOpt"]
            assert all(
                binding["HostIp"] == "127.0.0.1"
                for binding in settings["NetworkSettings"]["Ports"]["8080/tcp"]
            )
            # Liveness is healthy while absent inference correctly rejects readiness.
            status = compose(
                "exec", "-T", "gaia", "gaia-agent", "--client", "status", check=False
            )
            assert status.returncode == 1 and "HTTP 503" in status.stderr, status
            compose(
                "exec",
                "-T",
                "gaia",
                "sh",
                "-ec",
                "printf retained > /data/compose-check; printf workspace > /workspace/compose-check",
            )
            compose("down", "--timeout", "50")
            up()
            assert compose("ps", "-q", "gaia").stdout.strip() != before
            assert (
                compose("exec", "-T", "gaia", "cat", "/data/compose-check").stdout
                == "retained"
            )
            assert (
                compose("exec", "-T", "gaia", "cat", "/workspace/compose-check").stdout
                == "workspace"
            )
            print(
                "Compose health, deployment limits, absent-model readiness and volume recreation passed."
            )
        finally:
            logs = compose("logs", "--no-color", check=False)
            print(logs.stdout + logs.stderr)
            compose("down", "--volumes", "--remove-orphans", "--timeout", "50")


if __name__ == "__main__":
    main()
