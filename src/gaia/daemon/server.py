# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon process entrypoint: bind loopback port, mint token, serve, register.

Run as ``python -m gaia.daemon`` (the client spawns it detached). This process IS
the daemon — its pid is what instance.json records — so ``start_or_attach`` can
assert the file it reads back belongs to the child it launched.

Order matters: uvicorn binds the port during its startup phase; instance.json is
written from a startup hook so the registry only appears once the port is actually
accepting. On shutdown the registry is removed (guarded by pid so a newer daemon's
file is never clobbered).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import time
from pathlib import Path

from gaia.daemon.app import create_app
from gaia.daemon.constants import HOST, RESERVED_PORT
from gaia.daemon.errors import DaemonStartError
from gaia.daemon.instance import DaemonInstance, remove_instance, write_instance
from gaia.logger import get_logger

logger = get_logger(__name__)

# Cap on uvicorn's graceful drain so `gaia daemon stop`/`restart` never waits
# behind a slow in-flight ensure (a first-run binary fetch can take minutes);
# shutdown_all() tree-kills the sidecars regardless.
_GRACEFUL_SHUTDOWN_TIMEOUT = 5.0


def _find_free_port(host: str = HOST) -> int:
    """OS-assigned free loopback port. Never returns the reserved 4001."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port != RESERVED_PORT:
            return port
    raise DaemonStartError(
        "could not find a free loopback port for the daemon after 50 attempts — "
        "the ephemeral port range may be exhausted; check `gaia daemon logs`."
    )


def _load_extra_specs() -> dict:
    """Test-only spec seam (#2142 T4): ``GAIA_DAEMON_EXTRA_SPECS`` names a JSON
    file mapping ``agent_id -> spec fields``, merged over ``builtin_specs()``.

    Read once at daemon startup. This is how the integration suite registers
    its toy agent inside a real daemon process; production agents are added in
    ``builtin_specs()`` — there is deliberately NO runtime registration route.
    """
    path = os.environ.get("GAIA_DAEMON_EXTRA_SPECS")
    if not path:
        return {}
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DaemonStartError(
            f"GAIA_DAEMON_EXTRA_SPECS points at {path} but it could not be "
            f"read as JSON: {e}. Fix or unset the variable, then restart the "
            "daemon."
        ) from e
    specs = {}
    for agent_id, fields in raw.items():
        data = dict(fields)
        data["agent_id"] = agent_id
        if data.get("dev_src_dir"):
            data["dev_src_dir"] = Path(data["dev_src_dir"])
        try:
            specs[agent_id] = AgentSidecarSpec(**data)
        except TypeError as e:
            raise DaemonStartError(
                f"GAIA_DAEMON_EXTRA_SPECS entry '{agent_id}' in {path} is not "
                f"a valid AgentSidecarSpec: {e}"
            ) from e
    return specs


def _build_registry(specs, forwarder, *, custody_auth=None, custody_base_url=None):
    """The daemon's sidecar registry, wired to the crash-reap ledger and the
    OAuth forward-out on-spawn push (#2154).

    When *custody_auth* + *custody_base_url* are given, the registry mints a
    per-agent custody secret at spawn and injects the /host/v1 wiring (#2153).
    """
    from gaia.daemon.sidecars import ledger
    from gaia.daemon.sidecars.registry import SidecarRegistry

    def _on_spawn(agent_id, manager) -> None:
        ledger.record_spawn(
            agent_id=agent_id,
            pid=manager.pid,
            port=manager.port,
            mode=manager.resolved_mode,
            argv=list(manager.spawn_argv or []),
            started_at=manager.started_at,
        )

    def _on_started(agent_id, manager) -> None:
        # Forward granted connector access tokens OUT once the sidecar is
        # healthy (its /v1/connections intake can now answer). Best-effort per
        # provider; the forwarder logs every failure — nothing is swallowed.
        if not manager.base_url:
            return
        forwarder.forward_all(
            agent_id, base_url=manager.base_url, bearer=manager.auth_token
        )

    return SidecarRegistry(
        specs,
        on_spawn=_on_spawn,
        on_stop=ledger.remove_entry,
        on_started=_on_started,
        custody_auth=custody_auth,
        custody_base_url=custody_base_url,
    )


def run(host: str = HOST) -> None:
    """Start the daemon and serve until shutdown. Blocks."""
    import uvicorn

    from gaia.daemon.custody.auth import CustodyAuth
    from gaia.daemon.custody.store import CustodyStore
    from gaia.daemon.migrate import run_migrations
    from gaia.daemon.paths import custody_db_path
    from gaia.daemon.sidecars import ledger
    from gaia.daemon.sidecars.spec import builtin_specs

    # One-time versioned state migration (§0.10 step 0). Runs before the port is
    # bound so a corrupt/unknown-newer custody schema refuses loudly (MigrationError
    # propagates and the daemon exits) rather than serving over ambiguous state.
    result = run_migrations()
    logger.info("daemon: custody schema %s", result)

    port = _find_free_port(host)
    token = secrets.token_urlsafe(32)
    pid = os.getpid()
    started_at = time.time()

    from gaia.daemon.forward import ConnectionForwarder

    # Custody backing (#2153): one SQLite store owned by the daemon, and the
    # in-memory secret→agent-id bindings. Constructed before the registry so the
    # registry can mint each sidecar's custody secret at spawn. The custody
    # callback URL is the daemon's own loopback address.
    custody_store = CustodyStore(custody_db_path())
    custody_auth = CustodyAuth()
    custody_base_url = f"http://{host}:{port}"

    specs = {**builtin_specs(), **_load_extra_specs()}
    forwarder = ConnectionForwarder(specs)
    registry = _build_registry(
        specs,
        forwarder,
        custody_auth=custody_auth,
        custody_base_url=custody_base_url,
    )

    from gaia.daemon.forward_refresh import ForwardRefresher

    # Keep forwarded connector tokens fresh for the life of each sidecar (#2388
    # / #2159). Without this the on-spawn forward is the ONLY forward, so the
    # token expires (~1h) and the sidecar 401s until restarted. Started after
    # the port is up; stopped before shutdown_all so it never re-forwards to a
    # sidecar that is about to die.
    refresher = ForwardRefresher(registry, forwarder)

    from gaia.daemon.broker import ModelSlotBroker
    from gaia.daemon.constants import BROKER_URL_ENV_VAR

    broker = ModelSlotBroker()
    # Advertise the broker to the processes this daemon spawns. Sidecars inherit
    # os.environ at spawn (AgentSidecarManager builds spawn_env from it) and add
    # their own launch token as GAIA_MODEL_BROKER_TOKEN, so both host-side and
    # sidecar model loads route through the broker rather than racing the slot.
    os.environ[BROKER_URL_ENV_VAR] = f"http://{host}:{port}"

    def _register() -> None:
        # Reap identity-confirmed sidecar survivors of a previous daemon that
        # died hard (SIGKILL/OOM) BEFORE serving — never adopt them silently.
        killed = ledger.reap_stale(specs)
        if killed:
            logger.info("daemon: reaped stale sidecar pids %s", killed)
        write_instance(
            DaemonInstance(
                pid=pid, port=port, token=token, host=host, started_at=started_at
            )
        )
        refresher.start()
        logger.info("daemon: registered instance pid=%s port=%s", pid, port)

    def _deregister() -> None:
        refresher.stop()
        registry.shutdown_all()
        custody_store.close()
        remove_instance(only_pid=pid)
        logger.info("daemon: deregistered instance pid=%s", pid)

    app = create_app(
        token=token,
        port=port,
        pid=pid,
        started_at=started_at,
        on_startup=_register,
        on_shutdown=_deregister,
        registry=registry,
        forwarder=forwarder,
        broker=broker,
        custody_auth=custody_auth,
        custody_store=custody_store,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=int(_GRACEFUL_SHUTDOWN_TIMEOUT),
    )
    server = uvicorn.Server(config)
    # Give the shutdown route a handle to trigger a graceful exit.
    app.state.server = server

    logger.info("daemon: starting on http://%s:%s (pid=%s)", host, port, pid)
    server.run()


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m gaia.daemon`
    run()
