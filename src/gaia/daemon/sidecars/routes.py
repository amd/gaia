# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/agents`` — the daemon's token-guarded sidecar control plane
(#2142 D-3).

Built as a factory (``build_agents_router``) and included from inside
``create_app`` so the app's documented no-shared-global-state invariant holds —
nothing is smuggled through ``app.state``.

Only ``ensure`` responses carry the sidecar bearer token; the list route never
does (least exposure). Manager-level spawn failures surface as 502 with the
manager's actionable message verbatim (which embeds the sidecar log tail —
pre-first-health-success only, so it cannot contain mailbox data).

The daemon serves no OpenAPI schema, so this table IS the contract:

===============================================  ======================================
``GET    /daemon/v1/agents``                     registered sidecars (never tokens)
``POST   /daemon/v1/agents/{id}/ensure``         spawn-or-attach (body carries the token)
``POST   /daemon/v1/agents/{id}/stop``           tree-kill + verify the pid is gone
``GET    /daemon/v1/catalog``                    hub catalog + installed state
``POST   /daemon/v1/agents/{id}/install``        202, queue an install
``GET    /daemon/v1/agents/{id}/install-status`` poll install progress
``DELETE /daemon/v1/agents/{id}``                stop, verify, remove the install dir
===============================================  ======================================
"""

from __future__ import annotations

from typing import Optional

# Module-level on purpose (unlike app.py's deferred imports): this module is
# itself imported lazily from create_app, and the endpoint annotations below
# must be resolvable from module globals under PEP 563.
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from gaia.daemon.constants import API_PREFIX
from gaia.daemon.sidecars import install as install_svc
from gaia.daemon.sidecars.errors import (
    AgentNotInstalledError,
    AgentTrustRequiredError,
    CapacityError,
    HealthTimeoutError,
    HubUnavailableError,
    InstallBusyError,
    InstallFailedError,
    ModeConflictError,
    SidecarSpawnError,
    StopFailedError,
    UnknownAgentError,
    UnsupervisedAgentError,
    VersionMismatchError,
)


def build_agents_router(token: str, registry):
    """Token-guarded APIRouter over *registry* (``/daemon/v1/agents*``)."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(dependencies=[Depends(require_token)])

    async def _body_mode(request: Request) -> Optional[str]:
        """``mode`` from an optional JSON body ({"mode": "user"|"dev"|null})."""
        try:
            body = await request.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return body.get("mode")

    async def _install_body(request: Request) -> "tuple[Optional[str], bool]":
        """``(version, trusted)`` from an optional JSON install body.

        ``trusted`` MUST default to False: it is the user's explicit opt-in to
        run a non-verified agent's third-party code, so a body that omits it
        (or is absent entirely) is a refusal, never an approval.
        """
        try:
            body = await request.json()
        except ValueError:
            return None, False
        if not isinstance(body, dict):
            return None, False
        version = body.get("version")
        return (
            str(version) if version is not None else None,
            body.get("trusted") is True,
        )

    @router.get(f"{API_PREFIX}/agents")
    def list_agents() -> dict:
        return {"agents": registry.list_agents()}

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/ensure")
    async def ensure(agent_id: str, request: Request) -> dict:
        mode = await _body_mode(request)
        try:
            # manager.start() is sync-blocking (health poll, lazy fetch) —
            # keep it off the event loop.
            return await run_in_threadpool(registry.ensure, agent_id, mode=mode)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (ModeConflictError, CapacityError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (SidecarSpawnError, HealthTimeoutError, VersionMismatchError) as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/stop")
    async def stop(agent_id: str) -> dict:
        try:
            return await run_in_threadpool(registry.stop, agent_id)
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except StopFailedError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    # -- Agent Hub: catalog / install / uninstall ---------------------------
    # One implementation for three clients (TUI, `gaia hub`, Agent UI): one
    # integrity check, one install lock, one stop-the-sidecar-first rule.

    @router.get(f"{API_PREFIX}/catalog")
    async def catalog(
        refresh: bool = False,
        include_unsupervised: bool = False,
        installed_only: bool = False,
    ) -> dict:
        """Hub catalog merged with local install state (one call, not two).

        Each entry carries ``installed`` / ``installed_version`` /
        ``update_available`` read from the ``.installed`` sentinels, plus
        ``supervised``. Agents the daemon has no sidecar spec for are filtered
        out (their ids are listed in ``unsupervised_filtered``) so a client is
        never offered an agent that could not be started; pass
        ``include_unsupervised=true`` to see them anyway. ``offline: true``
        means the live hub was unreachable and the on-disk cache was used.

        ``refresh=true`` bypasses the 5-minute index cache.
        ``installed_only=true`` answers from the local sentinels and never
        touches the network (``source: "local"``).
        """
        try:
            return await run_in_threadpool(
                install_svc.build_catalog,
                registry=registry,
                refresh=refresh,
                include_unsupervised=include_unsupervised,
                installed_only=installed_only,
            )
        except HubUnavailableError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.post(f"{API_PREFIX}/agents/{{agent_id}}/install", status_code=202)
    async def install(agent_id: str, request: Request) -> dict:
        """Queue an install of *agent_id*; poll ``install-status`` for progress.

        Body (all optional): ``{"version": "0.5.0", "trusted": true}`` —
        ``version`` defaults to the hub's latest, ``trusted`` defaults to
        **false**. Returns 202 ``{"agent_id", "status": "queued", "version"}``.

        A non-verified agent (anything outside the ``verified`` security tier,
        which includes ``email``) is refused with **403** until the caller
        passes ``trusted: true`` — that is the user's explicit acknowledgement
        that installing runs third-party code on their machine. Render it as a
        "Trust & Install" prompt and retry; there is no bypass.

        A running sidecar is stopped first and a pid that survives aborts with
        500 — the install dir is that sidecar's own binary cache.
        """
        version, trusted = await _install_body(request)
        try:
            return await run_in_threadpool(
                install_svc.start_install,
                agent_id,
                registry=registry,
                version=version,
                trusted=trusted,
            )
        except UnknownAgentError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except AgentTrustRequiredError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        except UnsupervisedAgentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except InstallBusyError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except HubUnavailableError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        except StopFailedError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get(f"{API_PREFIX}/agents/{{agent_id}}/install-status")
    async def install_status(agent_id: str) -> dict:
        """Progress of an in-flight or finished install.

        ``{"agent_id", "status", "phase", "percent", "version", "error"}`` with
        ``status`` in ``queued|running|completed|failed``. ``failed`` carries
        the actionable reason in ``error`` (checksum mismatch, disk, hub).
        """
        state = await run_in_threadpool(install_svc.install_status, agent_id)
        if state is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no install has been requested for '{agent_id}' on this "
                    f"daemon. Start one with POST "
                    f"{API_PREFIX}/agents/{agent_id}/install."
                ),
            )
        return state

    @router.delete(f"{API_PREFIX}/agents/{{agent_id}}")
    async def uninstall(agent_id: str) -> dict:
        """Stop the sidecar, verify the pid is gone, then remove its install dir."""
        try:
            return await run_in_threadpool(
                install_svc.uninstall, agent_id, registry=registry
            )
        except (AgentNotInstalledError, UnknownAgentError) as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except UnsupervisedAgentError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except InstallBusyError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except (StopFailedError, InstallFailedError) as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    return router
