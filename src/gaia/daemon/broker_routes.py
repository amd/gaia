# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/host/v1/models/*`` — the model-slot broker's lease routes (V2-11 · §0.12).

This is the daemon's **callback plane** (``/host/v1/*``), distinct from the
client plane (``/daemon/v1/*``). A caller here is either:

- a **sidecar**, authenticating with the per-session launch token the manager
  minted for it (#1706) — resolved to its agent_id via
  ``SidecarRegistry.authenticate_callback``; or
- a **host-side** component (the UI server's embedder, host-custody RAG),
  authenticating with the daemon client token.

Either credential is accepted; anything else is a loud 401.

``POST /host/v1/models/lease`` does not return until the caller owns the slot.
A request with ``"stream": true`` gets NDJSON instead: a ``queued`` line when it
has to wait and every couple of seconds after (its place in line, who holds the
slot, for how long), then one ``granted`` or ``error`` line. The steady lines are
what let a client tell a long queue from a dead daemon. A request without it
gets the original single JSON response, bounded by
:data:`DEFAULT_LEASE_WAIT_TIMEOUT_S` (→ 504) unless it names its own timeout.

Either way the wait is tied to the requester: when its connection closes, it
leaves the queue, and a streamed grant that never reached it is released. (A
non-streaming client that leaves while its grant is being sent leaves that
lease to the TTL.) Renew and release succeed only for the lease's own holder.
Waits run on threads of their
own, never the worker pool the daemon's sync routes share, so a long queue
cannot starve ``/daemon/v1/status``.

A grant with ``"renewable": true`` is kept for as long as the holder calls
``POST /host/v1/models/lease/{id}/renew`` (every ``renew_interval_s``); one that
stops is reclaimed after the broker's heartbeat TTL.
"""

from __future__ import annotations

import asyncio
import json
import queue
import secrets
import threading
from concurrent.futures import Future
from typing import Optional

# Module-level (like sidecars/routes.py): this module is imported lazily from
# create_app, and the endpoint annotations must resolve from module globals
# under PEP 563.
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from gaia.daemon.broker import (
    BrokerError,
    Lease,
    LeaseCancelledError,
    LeaseNotHeldError,
    LeasePriority,
    LeaseTimeoutError,
    ModelSlotBroker,
)
from gaia.daemon.constants import AUTH_SCHEME, HOST_API_PREFIX
from gaia.daemon.paths import instance_path
from gaia.logger import get_logger

logger = get_logger(__name__)

# Default ceiling on how long a non-streaming lease request blocks before a 504.
# Older clients read the response with a 310 s timeout, so it must stay below
# that. A streaming request has no default ceiling: it waits while the holder
# ahead keeps its lease.
DEFAULT_LEASE_WAIT_TIMEOUT_S = 300.0

#: How often a streaming waiter hears where it stands. Clients treat a silence
#: several times this long as a dead daemon.
QUEUE_UPDATE_EVERY_S = 2.0

#: How often a waiter checks whether its requester is still connected.
_POLL_S = 0.25

#: HTTP status for a request whose client went away (nginx's convention).
_CLIENT_CLOSED = 499


def _caller_from_credential(
    credential: str, daemon_token: str, registry
) -> Optional[str]:
    """Resolve a bearer credential to a caller label, or ``None`` if invalid.

    Host-side callers present the daemon client token → ``"host"``. Sidecars
    present their launch token → their agent_id. Constant-time compare on the
    host token so it cannot be timed.
    """
    if credential and secrets.compare_digest(credential, daemon_token):
        return "host"
    if registry is not None:
        return registry.authenticate_callback(credential)
    return None


def _lease_json(lease: Lease, waited: dict, broker: ModelSlotBroker) -> dict:
    body = {
        "lease_id": lease.lease_id,
        "model": lease.model,
        "priority": lease.priority.name.lower(),
        "holder": lease.holder,
        "granted_at": lease.granted_at,
        "waited": waited["flag"],
        "switching": waited["flag"] and "switching model" in waited["reason"],
        "renewable": lease.renewable,
    }
    if lease.renewable:
        body["heartbeat_ttl_s"] = broker.heartbeat_ttl_s
        body["renew_interval_s"] = broker.heartbeat_ttl_s / 4
    return body


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event) + "\n").encode("utf-8")


class _Wait:
    """One lease request's wait, on a daemon thread of its own.

    Not the event loop's worker pool: waits are unbounded in length, and the
    pool also serves every sync route. A daemon thread also never holds up
    shutdown. :meth:`abandon` is the requester going away: the wait leaves the
    queue, and a lease granted in that moment is released.
    """

    def __init__(self, broker: ModelSlotBroker, model: str, **acquire_kwargs):
        self._broker = broker
        self.cancel = threading.Event()
        self.updates: "queue.Queue[dict]" = queue.Queue()
        self.future: "Future[Lease]" = Future()
        self._delivered = False
        self._guard = threading.Lock()
        self._outcome: Optional[asyncio.Future] = None
        threading.Thread(
            target=self._run,
            args=(model, acquire_kwargs),
            name=f"broker-lease-wait:{model}",
            daemon=True,
        ).start()

    def _run(self, model: str, acquire_kwargs: dict) -> None:
        try:
            lease = self._broker.acquire(
                model,
                cancel=self.cancel,
                on_update=self.updates.put_nowait,
                update_every_s=QUEUE_UPDATE_EVERY_S,
                **acquire_kwargs,
            )
        except BaseException as e:  # handed to the awaiting request
            self.future.set_exception(e)
            return
        # Under the guard so exactly one of this and abandon() sees the other.
        with self._guard:
            abandoned = self.cancel.is_set()
            self.future.set_result(lease)
        if abandoned:
            self._release_abandoned(lease)

    async def done(self) -> bool:
        """Wait briefly for the outcome; True once it is known."""
        if self._outcome is None:  # one wrapper, so its exception is retrieved
            self._outcome = asyncio.wrap_future(self.future)
        await asyncio.wait([self._outcome], timeout=_POLL_S)
        if self._outcome.done():
            self._outcome.exception()
            return True
        return False

    def delivered(self) -> None:
        with self._guard:
            self._delivered = True

    def abandon(self) -> None:
        """The requester is gone: leave the queue, or give back a fresh grant."""
        with self._guard:
            if self._delivered:
                return
            self.cancel.set()
            granted = self.future.done() and self.future.exception() is None
        self._broker.wake()
        if granted:
            self._release_abandoned(self.future.result())

    def _release_abandoned(self, lease: Lease) -> None:
        try:
            self._broker.release(lease.lease_id)
        except LeaseNotHeldError:
            return  # released by the other path of the race
        logger.info(
            "broker: released lease %s for '%s' — its requester left before "
            "the grant reached it",
            lease.lease_id,
            lease.model,
        )


def build_broker_router(
    daemon_token: str, registry, broker: ModelSlotBroker
) -> APIRouter:
    """Token-guarded APIRouter exposing the broker's lease routes.

    *daemon_token* authenticates host-side callers; *registry* resolves sidecar
    launch tokens to agent ids; *broker* is the shared :class:`ModelSlotBroker`.
    """

    # Async: a sync dependency would take a worker-pool thread on every call.
    async def require_caller(
        authorization: Optional[str] = Header(default=None),
    ) -> str:
        where = (
            f"the client token in {instance_path()} (host-side), or the "
            "sidecar's launch token"
        )
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Missing broker credential. Send "
                    f"'Authorization: {AUTH_SCHEME} <token>' — {where}."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != AUTH_SCHEME.lower() or not credential:
            raise HTTPException(
                status_code=401,
                detail=(
                    f"Malformed Authorization header. Expected "
                    f"'{AUTH_SCHEME} <token>'."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        caller = _caller_from_credential(credential, daemon_token, registry)
        if caller is None:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid broker credential: it matches neither the daemon "
                    "client token nor any running sidecar's launch token. If the "
                    "daemon restarted the tokens rotated — re-attach with "
                    "`gaia daemon status`, or re-ensure the sidecar."
                ),
                headers={"WWW-Authenticate": AUTH_SCHEME},
            )
        return caller

    router = APIRouter()

    @router.post(f"{HOST_API_PREFIX}/models/lease")
    async def lease(request: Request, caller: str = Depends(require_caller)):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        model = body.get("model")
        if not model or not isinstance(model, str):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Broker lease requires a non-empty string 'model' in the "
                    'request body, e.g. {"model": "Gemma-4-E4B-it-GGUF", '
                    '"priority": "interactive"}.'
                ),
            )
        try:
            priority = LeasePriority.parse(
                body.get("priority", LeasePriority.BACKGROUND)
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        stream = body.get("stream") is True
        timeout = body.get("timeout", None if stream else DEFAULT_LEASE_WAIT_TIMEOUT_S)
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout < 0
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Broker lease 'timeout' must be a number of seconds >= 0 "
                    f"or null (wait while the holder is alive); got {timeout!r}."
                ),
            )

        waited = {"flag": False, "reason": ""}

        def _on_wait(reason: str) -> None:
            waited["flag"] = True
            waited["reason"] = reason
            logger.info("broker: %s — %s (switching model…)", caller, reason)

        def start_wait() -> _Wait:
            return _Wait(
                broker,
                model,
                priority=priority,
                holder=caller,
                timeout=timeout,
                on_wait=_on_wait,
                renewable=body.get("renewable") is True,
            )

        if stream:
            # Started by the stream itself: a client that leaves before the
            # stream begins never runs its cleanup, so it must never queue.
            return StreamingResponse(
                _stream(start_wait, waited), media_type="application/x-ndjson"
            )
        wait = start_wait()
        try:
            while not await wait.done():
                if await request.is_disconnected():
                    wait.abandon()
                    raise HTTPException(
                        status_code=_CLIENT_CLOSED,
                        detail="the client closed the lease request while queued",
                    )
            granted = wait.future.result()
        except LeaseTimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e)) from e
        except LeaseCancelledError as e:
            raise HTTPException(status_code=_CLIENT_CLOSED, detail=str(e)) from e
        except asyncio.CancelledError:
            wait.abandon()
            raise
        wait.delivered()
        return _lease_json(granted, waited, broker)

    async def _stream(start_wait, waited: dict):
        wait = start_wait()
        try:
            while True:
                finished = await wait.done()
                while not wait.updates.empty():
                    yield _ndjson({"event": "queued", **wait.updates.get_nowait()})
                if finished:
                    break
            try:
                granted = wait.future.result()
            except BrokerError as e:
                status = 504 if isinstance(e, LeaseTimeoutError) else _CLIENT_CLOSED
                yield _ndjson({"event": "error", "status": status, "detail": str(e)})
                return
            yield _ndjson({"event": "granted", **_lease_json(granted, waited, broker)})
            # Only once the send returned: a client gone mid-send leaves the
            # generator at the yield, and the finally gives the lease back.
            wait.delivered()
        finally:
            wait.abandon()

    @router.post(f"{HOST_API_PREFIX}/models/lease/{{lease_id}}/renew")
    async def renew(lease_id: str, caller: str = Depends(require_caller)) -> dict:
        try:
            expires_in = broker.renew(lease_id, holder=caller)
        except LeaseNotHeldError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"lease_id": lease_id, "expires_in_s": expires_in}

    @router.post(f"{HOST_API_PREFIX}/models/lease/{{lease_id}}/release")
    async def release(lease_id: str, caller: str = Depends(require_caller)) -> dict:
        try:
            broker.release(lease_id, holder=caller)
        except LeaseNotHeldError as e:
            # 409: the lease is not the one holding the slot (double-release or a
            # stale id after a TTL reclaim). Loud, not swallowed.
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"lease_id": lease_id, "state": "released"}

    @router.get(f"{HOST_API_PREFIX}/models/queue")
    async def queue_state(_caller: str = Depends(require_caller)) -> dict:
        """Who holds the slot and who is waiting. Lease ids stay private."""
        snapshot = broker.snapshot()
        if snapshot["active"] is not None:
            snapshot["active"].pop("lease_id")
        return snapshot

    return router
