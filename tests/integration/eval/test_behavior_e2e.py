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

    # Redirect the child's stdout/stderr to a FILE, never a PIPE. The server
    # logs heavily during startup (registry discovery, agent loop, scheduler,
    # monitor, connector tripwire, DispatchQueue progress) — several KB before
    # ``/api/health`` comes up. An unread ``subprocess.PIPE`` fills the OS pipe
    # buffer (~4-8 KB on Windows) and the server BLOCKS on write, so the ASGI
    # lifespan never finishes and health never responds — a deadlock that
    # timed out at 30 s only on the (Windows) strix-halo runner, not locally
    # where the pipe buffer is larger. File writes never block, and the log is
    # uploaded as a CI artifact for diagnosis.
    # Write under an ``artifacts`` subdir so the workflow's
    # ``pytest-behavior/**/artifacts/**`` upload step captures the server log
    # for post-mortem diagnosis (e.g. a slow model load or a wedged tool loop).
    log_dir = home_dir / "artifacts"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"
    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")

    def _server_log_tail(limit: int = 4000) -> str:
        log_fh.flush()
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError as e:  # pragma: no cover — diagnostic best-effort
            return f"<could not read {log_path}: {e}>"

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
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f"http://127.0.0.1:{port}"
    # Cold first-run boot on a busy self-hosted runner (heavy imports + DB init
    # + registry/agent-loop/scheduler/monitor startup) legitimately needs more
    # than 30 s. The pipe-deadlock fix above is the real cause of the historical
    # hang; the larger budget is headroom for a genuinely slow cold boot.
    deadline = time.time() + 90
    try:
        while time.time() < deadline:
            try:
                resp = requests.get(f"{base_url}/api/health", timeout=2)
                if resp.status_code == 200:
                    break
            except requests.RequestException:
                pass
            if process.poll() is not None:
                pytest.fail(
                    f"gaia.ui.server exited unexpectedly (code {process.returncode}).\n"
                    f"server log tail:\n{_server_log_tail()}"
                )
            time.sleep(0.5)
        else:
            process.terminate()
            process.wait(timeout=10)
            pytest.fail(
                f"gaia.ui.server did not become healthy on port {port} within 90 s.\n"
                f"server log tail:\n{_server_log_tail()}"
            )
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log_fh.close()
        raise

    yield base_url, home_dir

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    log_fh.close()


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

    # Match the server's own 600 s inference ceiling (``_get_chat_response``
    # wraps ``process_query`` in ``asyncio.wait_for(timeout=600)``) plus a
    # small margin. A slower budget avoids the client cutting a still-running
    # builder request at 180 s — which left the request in flight server-side
    # and wedged the single worker so subsequent runs also timed out. Letting
    # the server-side bound fire first yields a clean response instead of a
    # client-side ReadTimeout.
    harness = BehaviorHarness(base_url=base_url, timeout=620)
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
