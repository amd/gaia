# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live behavior-E2E test — assert tool side-effects against a real model.

Requires:
- A running GAIA Agent UI server (gaia.ui.server) reachable at
  ``GAIA_UI_BASE_URL`` (defaults to ``http://127.0.0.1:4200``).
- A running Lemonade server at ``LEMONADE_BASE_URL``
  (defaults to ``http://localhost:13305/api/v1``).
- The server must serve an isolated home directory so that agent files
  written during the test do not pollute the developer's real agent registry.

These tests are gated by ``@pytest.mark.real_model`` and the ``require_lemonade``
fixture (auto-skip when Lemonade is absent).  They are NOT run in normal CI;
they execute on ``[self-hosted, strix-halo]`` runners once #1297 lands, and can
be run locally via::

    LEMONADE_BASE_URL=http://localhost:13305/api/v1 \\
    python -m pytest tests/integration/eval/test_behavior_e2e.py -m real_model -v

The test spins up an isolated ``gaia.ui.server`` instance (ephemeral port,
tmp HOME) so that the real developer agent registry is never touched.
"""

import logging
import os
import subprocess
import sys
import time

import pytest
import requests

from gaia.eval.behavior_harness import BUILDER_SCENARIOS, BehaviorHarness

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.real_model

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _lemonade_reachable() -> bool:
    """Return True if the Lemonade server responds to a health check."""
    base_url = os.environ.get("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
    # Strip /api/v1 suffix to get the root health URL pattern
    health_url = base_url.removesuffix("/api/v1").rstrip("/") + "/api/v1/health"
    try:
        resp = requests.get(health_url, timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def require_real_model():
    """Skip the entire module if Lemonade is not reachable."""
    if not _lemonade_reachable():
        pytest.skip(
            "Lemonade server not reachable — skipping real_model behavior E2E. "
            "Set LEMONADE_BASE_URL and ensure the server is running."
        )


@pytest.fixture(scope="module")
def behavior_server(require_real_model, tmp_path_factory):
    """Start an isolated gaia.ui.server on an ephemeral port.

    Uses a fresh temporary directory as HOME so that any agents created
    during the test are sandboxed away from the developer's real registry.

    Yields:
        Tuple[str, Path]: (base_url, home_dir)
    """
    home_dir = tmp_path_factory.mktemp("gaia_home")

    # Pick a free port by binding to :0, then releasing it.
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)  # Windows compat

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gaia.ui.server",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    # gaia.ui.server imports fastapi/uvicorn + the RAG stack at boot; on a busy
    # self-hosted runner that cold-import can take well over 30 s. Gate on a
    # generous, override-able budget so a slow-but-healthy boot isn't a flake.
    startup_timeout = int(os.environ.get("GAIA_UI_STARTUP_TIMEOUT", "120"))
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/health", timeout=2)
            if resp.status_code == 200:
                break
        except requests.RequestException:
            pass
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"gaia.ui.server exited unexpectedly.\n"
                f"stdout: {stdout[-2000:]}\nstderr: {stderr[-2000:]}"
            )
        time.sleep(0.5)
    else:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        pytest.fail(
            f"gaia.ui.server did not become healthy on port {port} within "
            f"{startup_timeout} s.\n"
            f"stdout: {(stdout or '')[-2000:]}\nstderr: {(stderr or '')[-2000:]}"
        )

    yield base_url, home_dir

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.real_model
def test_builder_creates_agent_file(behavior_server, tmp_path):
    """Builder scenario: agent must write agent.py for every prompt run.

    Any run where the agent claims success but no file was written is a
    false_success (hard fail — see #1428).  All runs must show the side-effect.
    """
    base_url, home_dir = behavior_server
    artifact_dir = tmp_path / "artifacts"

    harness = BehaviorHarness(base_url=base_url, timeout=180)
    scenario = BUILDER_SCENARIOS[0]

    result = harness.run_scenario(
        scenario, home_dir=home_dir, artifact_dir=artifact_dir
    )

    # Log a readable summary regardless of outcome.
    for t in result["transcripts"]:
        logger.info(
            "run %d | verdict=%s | reply=%s",
            t["idx"],
            t["verdict"],
            t["reply"][:120],
        )

    assert not result["hard_fail"], (
        f"Builder produced false_success (claimed success with no side-effect) "
        f"— this is the #1428 class of regression. "
        f"Verdict counts: {result['counts']}. "
        f"Artifacts in {artifact_dir}."
    )
    assert result["passed"], (
        f"Builder scenario failed: verdict counts = {result['counts']}. "
        f"Artifacts in {artifact_dir}."
    )
