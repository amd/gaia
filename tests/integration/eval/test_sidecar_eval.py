# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live golden-path eval — drive the email agent's ``/query`` loop THROUGH the
daemon → sidecar → Lemonade path and assert the canonical event sequence
(V2-19 AC2 / issue #2180).

This is the on-hardware acceptance path the design's §0.17 calls for: a thin
client (the :class:`SidecarEvalHarness`, standing in for the CLI / UI front-door)
posts to the DAEMON's streaming relay; the daemon spawns/attaches the real email
sidecar and relays its agent loop; the sidecar runs local Lemonade inference. The
committed baseline (``hub/agents/python/email/eval_baselines/query_sequences/``)
pins the §0.2 event *shape*, not a brittle final string.

It is deliberately gated and NOT part of normal CI (it needs a real model + a
configured mailbox — the same posture as the ``gmail_live`` tests):

- ``@pytest.mark.real_model`` — self-hosted strix-halo only (see #1297).
- ``require_real_model`` — loud skip if Lemonade is unreachable.
- ``GAIA_SIDECAR_EVAL_LIVE=1`` — explicit opt-in, because a triage run needs a
  connected mailbox; absent that, the test skips with a named reason rather than
  silently passing or failing as if it were a code bug.

Serial by construction: the harness takes the cross-process
:class:`SerialEvalLock`, so this run can never race a concurrent ``gaia eval``
for the single Lemonade slot (CLAUDE.md).

Run locally on hardware::

    GAIA_SIDECAR_EVAL_LIVE=1 GAIA_EMAIL_AGENT_MODE=dev \\
    LEMONADE_BASE_URL=http://localhost:13305/api/v1 \\
    python -m pytest tests/integration/eval/test_sidecar_eval.py -m real_model -v
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_model


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _lemonade_reachable() -> bool:
    import requests

    base_url = os.environ.get("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")
    health_url = base_url.removesuffix("/api/v1").rstrip("/") + "/api/v1/health"
    try:
        return requests.get(health_url, timeout=5).status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope="module")
def require_live_optin():
    """Skip unless the operator explicitly opted into the live golden path.

    The triage query drives the real mailbox tools; without a connected mailbox
    the run cannot produce a golden sequence. Rather than silently pass (or fail
    as if the code were broken), the test skips with an actionable reason unless
    ``GAIA_SIDECAR_EVAL_LIVE=1`` is set on a machine that is actually set up.
    """
    if os.environ.get("GAIA_SIDECAR_EVAL_LIVE") != "1":
        pytest.skip(
            "sidecar live golden path is opt-in: set GAIA_SIDECAR_EVAL_LIVE=1 on "
            "a machine with a running daemon-capable env + a connected mailbox "
            "(see the module docstring). Not run in normal CI."
        )
    if not _lemonade_reachable():
        pytest.skip(
            "Lemonade server not reachable — set LEMONADE_BASE_URL and start it "
            "before running the sidecar live golden path."
        )


def _serve(app):
    """Run *app* under uvicorn on an ephemeral port in a background thread."""
    import uvicorn

    from gaia.daemon.sidecars.manager import find_free_port

    port = find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("daemon uvicorn server never started")
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def live_daemon_with_email(require_live_optin, tmp_path_factory):
    """A real in-process daemon supervising the REAL email sidecar (dev mode).

    Yields ``(daemon_url, client_token)``. Ensuring the sidecar can fail if dev
    deps are missing — that is surfaced as a loud skip (named reason), never a
    silent pass.
    """
    from gaia.daemon.app import create_app
    from gaia.daemon.sidecars.registry import SidecarRegistry
    from gaia.daemon.sidecars.spec import builtin_specs

    client_token = "sidecar-eval-client-token"
    registry = SidecarRegistry(builtin_specs())

    # Isolate the serial lock to this run's tmp dir so it never contends with a
    # developer's real eval lock.
    os.environ["GAIA_EVAL_LOCK_PATH"] = str(
        tmp_path_factory.mktemp("eval_lock") / ".sidecar-eval.lock"
    )

    daemon_app = create_app(
        token=client_token,
        port=55555,
        pid=os.getpid(),
        started_at=time.time(),
        registry=registry,
    )
    server, thread, daemon_url = _serve(daemon_app)

    # Ensure the email sidecar (dev mode from source unless the operator set a
    # different mode). Blocking — run it directly; the fixture is module-scoped.
    mode = os.environ.get("GAIA_EMAIL_AGENT_MODE", "dev")
    try:
        registry.ensure("email", mode=mode)
    except Exception as exc:  # loud skip: name what failed and how to fix it
        registry.shutdown_all()
        server.should_exit = True
        thread.join(timeout=10)
        pytest.skip(
            f"could not ensure the email sidecar in {mode!r} mode ({exc}). "
            "Install the email package's dev deps (uvicorn + the agent wheel) or "
            "set GAIA_EMAIL_AGENT_MODE=user with an installed binary."
        )

    yield daemon_url, client_token

    registry.shutdown_all()
    server.should_exit = True
    thread.join(timeout=10)


# ---------------------------------------------------------------------------
# The golden path
# ---------------------------------------------------------------------------


def _email_baseline(scenario_id: str):
    from gaia.eval.sidecar_harness import baselines_dir_for, load_baseline

    pkg_root = (
        Path(__file__).resolve().parents[3] / "hub" / "agents" / "python" / "email"
    )
    return load_baseline(baselines_dir_for(pkg_root) / f"{scenario_id}.json")


def test_email_triage_golden_sequence_through_daemon_relay(live_daemon_with_email):
    """UI/CLI → daemon → sidecar → Lemonade: a triage run streams the committed
    canonical §0.2 sequence, verified against the agent-package baseline."""
    from gaia.eval.sidecar_harness import QuerySequenceScenario, SidecarEvalHarness

    daemon_url, client_token = live_daemon_with_email
    baseline = _email_baseline("triage_inbox")

    harness = SidecarEvalHarness(daemon_url, auth_token=client_token)
    scenario = QuerySequenceScenario(
        agent_id="email",
        query="What needs my attention in my inbox today?",
        baseline=baseline,
    )

    verdict, events = harness.run_scenario(scenario)

    assert verdict.passed, (
        f"golden triage sequence did not match baseline {baseline.scenario_id!r}: "
        f"{verdict.reasons}; observed types={verdict.observed_types}"
    )
    # Sanity: the run really streamed multiple canonical events, not a single
    # buffered blob.
    assert len(events) >= len(baseline.required_subsequence)
