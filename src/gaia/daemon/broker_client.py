# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Client side of the model-slot broker (V2-11 · §0.12).

Any process that loads a model — a sidecar, or the host-side embedder/RAG in the
UI server — routes that load through the daemon's broker so loads serialize
instead of racing the single slot. This module is the thin HTTP client for the
``POST /host/v1/models/lease`` route, plus the :func:`model_lease` context
manager that acquires a lease, yields, and releases it.

**Env-gated, and no silent fallback (CLAUDE.md).** Broker routing is active only
when ``GAIA_MODEL_BROKER_URL`` is set. The daemon sets it for the processes it
spawns; host-side processes it did NOT spawn (the UI backend, a CLI run) opt in
via :func:`enable_broker_discovery` and then attach to a live daemon lazily at
their first load (:func:`attach_broker_env`). When it is unset the caller is
running standalone (e.g. ``gaia llm`` with no daemon): there
is no slot contention to arbitrate, so :func:`model_lease` is a no-op — that is
the *absence* of a broker, not a fallback around a failed one. But when the URL
IS set and the broker cannot be reached, :func:`model_lease` raises loudly rather
than doing a direct load that would race-evict another process's model.

**Queued is not dead.** A lease request streams the broker's queue updates, so
a wait lasts as long as the holder ahead stays alive, the waiter hears where it
stands every couple of seconds, and a silent daemon is caught within
:data:`STREAM_IDLE_TIMEOUT_S`. Only :data:`LEASE_WAIT_LIMIT_ENV_VAR` caps the
wait. A granted lease is renewed in the background while it is held, so a long
generation is not reclaimed under its holder before its declared call budget.

**A turn's scope.** :func:`interactive_turn` marks the code running a user's
turn: its leases go ahead of queued background work, its waits are reported to
the turn's own event stream (:func:`lease_wait_listener`), and the turn's stop
signal cancels them (:func:`lease_cancel_scope`). Context variables carry all
three, so a thread the turn starts plainly (memory extraction) does not inherit
them and stays background.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Optional, Tuple

from gaia.daemon.constants import (
    AUTH_SCHEME,
    BROKER_PRIORITY_ENV_VAR,
    BROKER_TOKEN_ENV_VAR,
    BROKER_TOKEN_FILE_ENV_VAR,
    BROKER_URL_ENV_VAR,
    HOST_API_PREFIX,
    LEASE_WAIT_LIMIT_ENV_VAR,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

#: A queued lease request hears from the broker every ~2 s; this much silence
#: means the daemon died or hung, not that the queue is long.
STREAM_IDLE_TIMEOUT_S = 30.0

#: How often a wait on this process's own per-model lock re-checks for a cancel
#: and reports how long it has waited.
LOCAL_WAIT_POLL_S = 0.25
LOCAL_WAIT_UPDATE_EVERY_S = 2.0


class BrokerUnavailableError(Exception):
    """Broker routing is configured but the broker could not be reached/used.

    Fail-loud, per CLAUDE.md: raising this beats a silent direct load that would
    race the model slot. The message names what failed, what to do, and where to
    look.

    ``status_code`` is the broker's HTTP status when the failure came from a
    response, and ``None`` when it came from the transport (connection refused,
    timeout). Callers use it to tell "wrong/stale credential" apart from "this
    daemon has no broker at all" — those need opposite responses.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class LeaseWaitCancelled(Exception):
    """The caller's cancel signal fired while it waited for the model slot."""


# The turn-scoped state described in the module docstring.
_priority_scope: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "gaia_model_lease_priority", default=None
)
_wait_listener: contextvars.ContextVar[Optional[Callable[[dict], None]]] = (
    contextvars.ContextVar("gaia_model_lease_wait_listener", default=None)
)
_cancel_scope: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar(
    "gaia_model_lease_cancel", default=None
)


@contextmanager
def _scoped(var: contextvars.ContextVar, value) -> Iterator[None]:
    token = var.set(value)
    try:
        yield
    finally:
        var.reset(token)


def lease_priority(priority: str) -> ContextManager[None]:
    """Lease at *priority* (``interactive``/``background``) within the block."""
    return _scoped(_priority_scope, priority)


def lease_wait_listener(listener: Callable[[dict], None]) -> ContextManager[None]:
    """Send every model-slot wait inside the block to *listener*.

    It is called with ``{"event": "queued", "scope": "daemon"|"process", ...}``
    when a request starts waiting and every couple of seconds after (place in
    line, who holds the slot, seconds waited), then ``{"event": "granted", ...}``
    once it gets the slot. It runs on the waiting thread and must not block.
    """
    return _scoped(_wait_listener, listener)


def lease_cancel_scope(cancel) -> ContextManager[None]:
    """End a model-slot wait inside the block once ``cancel.is_set()``.

    The wait raises :class:`LeaseWaitCancelled`, and the broker drops it from
    its queue instead of granting the slot to a caller that has gone.
    """
    return _scoped(_cancel_scope, cancel)


@contextmanager
def interactive_turn(
    *,
    on_wait_event: Optional[Callable[[dict], None]] = None,
    cancel=None,
) -> Iterator[None]:
    """Mark the block as a user's turn (see the module docstring)."""
    with lease_priority("interactive"):
        with _scoped(_wait_listener, on_wait_event or _wait_listener.get()):
            with _scoped(_cancel_scope, cancel or _cancel_scope.get()):
                yield


@contextmanager
def detached_from_turn() -> Iterator[None]:
    """Run the block as background work, outside any turn's scope.

    For work a turn starts on a thread that copies its context but outlives it
    (background indexing): the turn's Stop and end must not cancel it, its
    waits do not belong on the turn's stream, and it must not jump the queue.
    """
    with _scoped(_priority_scope, None):
        with _scoped(_wait_listener, None):
            with _scoped(_cancel_scope, None):
                yield


def current_priority() -> str:
    """The lease priority for a request made here: turn scope, env, background."""
    return (
        _priority_scope.get()
        or os.environ.get(BROKER_PRIORITY_ENV_VAR, "").strip()
        or "background"
    )


def waits_are_listened() -> bool:
    """True when a wait here reaches a listener rather than only the log."""
    return _wait_listener.get() is not None


def notify_wait(event: dict) -> None:
    """Hand a wait event to this context's listener, if it has one."""
    listener = _wait_listener.get()
    if listener is not None:
        listener(event)


def check_cancelled(what: str) -> None:
    """Raise :class:`LeaseWaitCancelled` if this context's cancel has fired."""
    cancel = _cancel_scope.get()
    if cancel is not None and cancel.is_set():
        raise LeaseWaitCancelled(f"stopped while waiting for {what}")


def broker_configured() -> bool:
    """True when this process should route model loads through the broker."""
    return bool(os.environ.get(BROKER_URL_ENV_VAR, "").strip()) or _attached is not None


_discovery_enabled = False

# Address + credential discovered by :func:`attach_broker_env`, as (url, token).
#
# Deliberately NOT written into ``os.environ``. The daemon client token is not
# broker-scoped — it also guards ``/daemon/v1/shutdown`` and the sidecar control
# plane (``app.py``) — so exporting it would hand full daemon authority to every
# child process this one spawns and leave it inspectable in the process
# environment. That is exactly the posture the ``BROKER_TOKEN_FILE`` leg below
# exists to avoid. Keeping it in-process also means a child re-discovers the
# daemon on its own terms rather than inheriting a URL it has no credential for.
_attached: Optional[tuple] = None


def enable_broker_discovery() -> None:
    """Declare this process a host-side broker participant (#2248).

    Call once from a process *entry point* (the UI backend's lifespan, the CLI's
    ``main``). It only sets a flag — no probing here — so it is free to call
    unconditionally.

    Discovery is opt-in rather than implicit because importing
    :class:`~gaia.llm.lemonade_client.LemonadeClient` must not, by itself, make
    an arbitrary script or test silently join whatever daemon happens to be
    running on the machine. Entry points know they are a real host process;
    library code does not.
    """
    global _discovery_enabled  # pylint: disable=global-statement
    _discovery_enabled = True


def discovery_enabled() -> bool:
    """True when this process opted into host-side broker discovery."""
    return _discovery_enabled


def attach_broker_env() -> bool:
    """Point this process at a running daemon's broker. Returns True if attached.

    The daemon exports :data:`BROKER_URL_ENV_VAR` into its *own* environment, so
    only the processes it spawns (sidecars) inherit it. A host-side process that
    the daemon did NOT spawn — the UI backend, a CLI run — starts with no broker
    configured even when a daemon is live, and would load models directly,
    race-evicting whatever a sidecar just loaded (#2248).

    This closes that gap by *discovering* the live daemon and adopting its
    address plus client token (the credential ``/host/v1`` resolves to the
    ``"host"`` caller label).

    Returning False when no daemon is running is **not** a fallback: with no
    daemon there are no sidecars, nothing else holds the single Lemonade slot,
    and there is no arbiter to route through. A broker that IS configured but
    unreachable still raises loudly from :func:`model_lease`.
    """
    global _attached  # pylint: disable=global-statement

    if broker_configured():
        return True
    # Deferred: keeps the daemon client (and its httpx/probe path) off the
    # import path of standalone callers that never attach.
    from gaia.daemon.client import attach
    from gaia.daemon.errors import DaemonVersionError

    try:
        inst = attach()
    except DaemonVersionError as e:
        # A MAJOR-skewed daemon left running is an expected state after an app
        # update (see gaia.daemon.client), and it must not take down commands
        # that merely load a model — `gaia llm` has no business hard-failing on
        # it. Loud warning + unbrokered rather than a hard stop, since the
        # alternative bricks the CLI until the user finds `gaia daemon restart`.
        logger.warning(
            "broker client: a version-skewed daemon is running (%s). Model "
            "loads in this process run UNBROKERED and may race a sidecar's "
            "model. Run `gaia daemon restart` to restore serialization.",
            e,
        )
        return False
    if inst is None:
        logger.debug(
            "broker client: no live daemon to attach to; model loads run "
            "unbrokered (standalone — nothing else holds the model slot)"
        )
        return False
    _attached = (inst.base_url, inst.token)
    logger.info(
        "broker client: attached to daemon broker at %s — model loads in this "
        "process now serialize against sidecar loads",
        inst.base_url,
    )
    return True


def _detach() -> None:
    """Forget a discovered daemon so the next load re-discovers it.

    Called when a lease attempt fails against an auto-attached daemon: a
    ``gaia daemon restart`` rotates both the port and the token, and a
    long-lived host process (the UI backend) holding the stale pair would
    otherwise fail EVERY subsequent load until it was itself restarted.
    """
    global _attached  # pylint: disable=global-statement
    _attached = None


def _broker_base() -> str:
    # Env first: a daemon-spawned process is told where its broker is, and that
    # assignment outranks anything this process discovered on its own.
    url = os.environ.get(BROKER_URL_ENV_VAR, "").strip()
    if not url and _attached is not None:
        url = _attached[0]
    return url.rstrip("/")


def _credential() -> str:
    # Prefer the 0600-file leg (#2149 posture) so a sidecar's launch secret is
    # never copied into inspectable process env; fall back to the bare-env leg
    # (older-binary delivery, or host-side callers passing the daemon token).
    token_file = os.environ.get(BROKER_TOKEN_FILE_ENV_VAR, "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise BrokerUnavailableError(
                f"{BROKER_TOKEN_FILE_ENV_VAR} points at {token_file} but its "
                f"contents could not be read as the broker credential: {e}. "
                "Re-ensure the sidecar (its launch secret file may have been "
                "removed), or `gaia daemon restart`."
            ) from e
        if token:
            return token
    token = os.environ.get(BROKER_TOKEN_ENV_VAR, "").strip()
    if not token and _attached is not None:
        # Discovered in-process by attach_broker_env (never exported to env —
        # see the _attached docstring for why).
        token = _attached[1]
    if not token:
        raise BrokerUnavailableError(
            f"{BROKER_URL_ENV_VAR} is set (broker routing is active) but neither "
            f"{BROKER_TOKEN_FILE_ENV_VAR} nor {BROKER_TOKEN_ENV_VAR} yields a "
            "credential to lease the model slot. The daemon sets one when it "
            "spawns a sidecar; host-side callers must set the token to the daemon "
            f"client token. Restart via `gaia daemon restart`, or unset "
            f"{BROKER_URL_ENV_VAR} to run standalone without the broker."
        )
    return token


def user_wait_limit() -> Optional[float]:
    """The user's cap on a model-slot wait (:data:`LEASE_WAIT_LIMIT_ENV_VAR`)."""
    raw = os.environ.get(LEASE_WAIT_LIMIT_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        limit = float(raw)
    except ValueError as e:
        raise BrokerUnavailableError(
            f"{LEASE_WAIT_LIMIT_ENV_VAR} must be a number of seconds; got "
            f"{raw!r}. Fix it, or unset it to wait while the model is in use."
        ) from e
    if limit <= 0:
        raise BrokerUnavailableError(
            f"{LEASE_WAIT_LIMIT_ENV_VAR} must be greater than 0; got {raw!r}."
        )
    return limit


def acquire_lease(
    model: str,
    *,
    priority: Optional[str] = None,
    timeout: Optional[float] = None,
    request_timeout: float = 310.0,
    on_update: Optional[Callable[[dict], None]] = None,
    max_hold_s: Optional[float] = None,
) -> dict:
    """Acquire a model-slot lease from the broker. Blocks until granted.

    Returns the lease dict (``lease_id``, ``model``, ``waited``, ``switching``,
    and ``renew_interval_s`` when the lease must be renewed). Raises
    :class:`BrokerUnavailableError` on any transport/HTTP failure — never
    returns without a lease when the broker is configured.

    The wait has no limit of its own: it lasts while the holder ahead keeps its
    lease. *timeout*, else :data:`LEASE_WAIT_LIMIT_ENV_VAR`, caps it.
    *on_update* gets the broker's queue status each time it reports one, and
    this context's :func:`lease_cancel_scope` ends the wait. *request_timeout*
    bounds the wait for a daemon too old to stream its queue. *max_hold_s* is
    the longest the caller's work under the lease can take; the broker takes
    the lease back from a holder still renewing past it.

    The lease *holder* label is not a client input: the broker derives it from
    the authenticated caller (agent_id for a sidecar, ``"host"`` otherwise), so
    a caller cannot spoof another's identity in logs/status.
    """
    import requests

    url = f"{_broker_base()}{HOST_API_PREFIX}/models/lease"
    limit_from_env = timeout is None
    wait_limit = user_wait_limit() if limit_from_env else timeout
    payload = {
        "model": model,
        "priority": priority or current_priority(),
        "stream": True,
        "renewable": True,
    }
    if wait_limit is not None:
        payload["timeout"] = wait_limit
    if max_hold_s is not None:
        payload["max_hold_s"] = max_hold_s
    headers = {"Authorization": f"{AUTH_SCHEME} {_credential()}"}
    unreachable = f"could not reach the model-slot broker at {url} to lease '{model}'"
    try:
        r = requests.post(
            url, json=payload, headers=headers, timeout=request_timeout, stream=True
        )
    except requests.exceptions.RequestException as e:
        raise BrokerUnavailableError(
            f"{unreachable}: {e}. The daemon may be down — check `gaia daemon "
            "status` and `gaia daemon logs`. Refusing a direct load that would "
            "race-evict another process's model."
        ) from e
    with r:
        if r.status_code != 200:
            detail = _safe_detail(r)
            raise BrokerUnavailableError(
                f"model-slot broker refused the lease for '{model}' "
                f"(HTTP {r.status_code}): {detail}. Check `gaia daemon logs`.",
                status_code=r.status_code,
            )
        if not r.headers.get("content-type", "").startswith("application/x-ndjson"):
            return r.json()  # a daemon from before queue streaming
        _fail_reads_after_silence(r, url)
        try:
            for raw in r.iter_lines():
                if not raw:
                    check_cancelled(f"the model slot for '{model}'")
                    continue
                event = json.loads(raw)
                kind = event.pop("event", None)
                if kind == "granted":
                    try:
                        check_cancelled(f"the model slot for '{model}'")
                    except LeaseWaitCancelled:
                        release_lease(event["lease_id"])  # granted too late
                        raise
                    return event
                check_cancelled(f"the model slot for '{model}'")
                if kind == "queued":
                    if on_update is not None:
                        on_update(event)
                elif kind == "error":
                    raise _queue_error(model, event, limit_from_env)
        except requests.exceptions.RequestException as e:
            raise BrokerUnavailableError(
                f"the model-slot broker at {url} stopped answering while "
                f"'{model}' was queued (no update for {STREAM_IDLE_TIMEOUT_S:.0f}s; "
                f"it sends one every few seconds): {e}. The daemon may have "
                "died or hung — check `gaia daemon status` and `gaia daemon logs`."
            ) from e
    raise BrokerUnavailableError(
        f"the model-slot broker at {url} ended the lease stream for '{model}' "
        "without granting or refusing it. Check `gaia daemon logs`."
    )


def _fail_reads_after_silence(response, url: str) -> None:
    """Make a body read with no data for :data:`STREAM_IDLE_TIMEOUT_S` raise.

    Not the request's own read timeout: a daemon from before queue streaming
    sends nothing at all until the grant, for up to its 300 s wait.
    """
    conn = getattr(response.raw, "connection", None)
    sock = getattr(conn, "sock", None)
    if sock is None:
        raise BrokerUnavailableError(
            f"cannot watch the lease stream from {url} for silence: the HTTP "
            "response exposes no socket. This needs requests with urllib3 1.26 "
            "or newer; reinstall GAIA's dependencies."
        )
    sock.settimeout(STREAM_IDLE_TIMEOUT_S)


def _queue_error(model: str, event: dict, limit_from_env: bool) -> Exception:
    status = event.get("status")
    detail = event.get("detail", "")
    if status == 504 and limit_from_env:
        detail += (
            f" To wait longer, raise {LEASE_WAIT_LIMIT_ENV_VAR}, or unset it to "
            "wait for as long as the model is in use."
        )
    return BrokerUnavailableError(
        f"no model-slot lease for '{model}': {detail}", status_code=status
    )


def renew_lease(
    lease_id: str, *, request_timeout: float = 10.0
) -> Tuple[Optional[int], str]:
    """Renew a lease's heartbeat.

    Returns ``(HTTP status, detail)``, with a ``None`` status if unreachable.
    The caller reports a refusal: only it knows whether the lease is still held.
    """
    import requests

    url = f"{_broker_base()}{HOST_API_PREFIX}/models/lease/{lease_id}/renew"
    headers = {"Authorization": f"{AUTH_SCHEME} {_credential()}"}
    try:
        r = requests.post(url, headers=headers, timeout=request_timeout)
    except requests.exceptions.RequestException as e:
        logger.warning(
            "broker client: could not renew lease %s at %s: %s. It is reclaimed "
            "if renewals keep failing for the broker's heartbeat TTL.",
            lease_id,
            url,
            e,
        )
        return None, str(e)
    return r.status_code, "" if r.status_code == 200 else _safe_detail(r)


class _Heartbeat:
    """Renews a held lease every ``renew_interval_s`` until stopped."""

    def __init__(self, lease: dict):
        self._stop = threading.Event()
        interval = lease.get("renew_interval_s")
        if not interval:  # a daemon whose leases do not renew
            return
        threading.Thread(
            target=self._run,
            args=(lease["lease_id"], lease.get("model"), float(interval)),
            name="broker-lease-heartbeat",
            daemon=True,
        ).start()

    def _run(self, lease_id: str, model: Optional[str], interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                status, detail = renew_lease(lease_id)
            except BrokerUnavailableError as e:  # e.g. the credential file is gone
                logger.error(
                    "broker client: cannot renew the model-slot lease for '%s' "
                    "(%s); it lapses after the broker's heartbeat TTL, and "
                    "another process may then load a model over this request.",
                    model,
                    e,
                )
                return
            if self._stop.is_set():
                return  # released while this renewal was in flight
            if status is not None and 400 <= status < 500:
                # 409: reclaimed; 401: the daemon restarted. Either way it's gone.
                logger.error(
                    "broker client: lost the model-slot lease for '%s' while "
                    "still using it (renewal: HTTP %s: %s). Another process may "
                    "now load a model over this one's request.",
                    model,
                    status,
                    detail,
                )
                return
            if status is not None and status != 200:
                logger.warning(
                    "broker client: renewal of the model-slot lease for '%s' "
                    "failed (HTTP %s: %s); retrying at the next interval.",
                    model,
                    status,
                    detail,
                )

    def stop(self) -> None:
        self._stop.set()


def release_lease(lease_id: str, *, request_timeout: float = 10.0) -> None:
    """Release a previously-acquired lease. Best-effort but loud on failure.

    A failed release is logged (never silently swallowed): the broker's TTL will
    eventually reclaim a leaked lease, but a leak is a bug worth surfacing.
    """
    import requests

    url = f"{_broker_base()}{HOST_API_PREFIX}/models/lease/{lease_id}/release"
    headers = {"Authorization": f"{AUTH_SCHEME} {_credential()}"}
    try:
        r = requests.post(url, headers=headers, timeout=request_timeout)
    except requests.exceptions.RequestException as e:
        logger.warning(
            "broker client: failed to release lease %s at %s: %s. The broker's "
            "TTL will reclaim it, but this indicates a leaked lease.",
            lease_id,
            url,
            e,
        )
        return
    if r.status_code != 200:
        logger.warning(
            "broker client: release of lease %s returned HTTP %s: %s",
            lease_id,
            r.status_code,
            _safe_detail(r),
        )


def _safe_detail(response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
    except ValueError:
        pass
    return (response.text or "")[:500]


# Set when the attached daemon answers the lease route with 404 — it is an
# older build with no broker mounted. Sticky for the process so we neither
# re-probe nor re-warn on every load.
_broker_unsupported = False

# Statuses that mean "the credential/address I hold is stale", i.e. worth one
# re-discovery. A 404 (no broker on this daemon) or a 5xx (broker is there and
# broken) are NOT stale-attach signals and must not trigger a pointless retry.
_STALE_ATTACH_STATUSES = (401, 403)


def _acquire_with_reattach(
    model: str, priority: Optional[str], on_update=None, max_hold_s=None
) -> Optional[dict]:
    """Acquire a lease, re-discovering the daemon once if a stale one is cached.

    Returns ``None`` when the attached daemon has no broker to lease from — see
    the 404 branch below.

    ``gaia daemon restart`` rotates the daemon's port AND token. A long-lived
    host process (the UI backend) that auto-attached before the restart holds a
    dead address, and every later load would fail permanently — the daemon's own
    401 text tells the caller to "re-attach", which is precisely what this does.

    Only retried for daemons this process DISCOVERED, and only for failures that
    actually indicate a stale attach. A URL handed down by the daemon that
    spawned us is authoritative: if that one is unreachable the failure is real
    and propagates untouched.
    """
    global _broker_unsupported  # pylint: disable=global-statement

    try:
        return acquire_lease(
            model, priority=priority, on_update=on_update, max_hold_s=max_hold_s
        )
    except BrokerUnavailableError as e:
        if e.status_code == 404:
            # The daemon is running but exposes no lease route: a build older
            # than the broker (#2151). There is no arbiter to route through —
            # and such a daemon is not managing sidecars that lease either — so
            # loads proceed unbrokered. That is the *absence* of a broker, the
            # same as no daemon at all, not a fallback around a failed one. Warn
            # once (not per load) because serialization is genuinely off.
            _broker_unsupported = True
            logger.warning(
                "broker client: the running daemon at %s has no model-slot "
                "broker (HTTP 404 on the lease route) — it predates #2151. "
                "Model loads in this process run UNBROKERED. Run "
                "`gaia daemon restart` on a current build to restore "
                "serialization.",
                _broker_base(),
            )
            return None
        stale = e.status_code is None or e.status_code in _STALE_ATTACH_STATUSES
        if _attached is None or not _discovery_enabled or not stale:
            raise
        logger.info(
            "broker client: lease attempt failed against the discovered daemon "
            "at %s (%s); it likely restarted and rotated its port/token. "
            "Re-discovering once.",
            _attached[0],
            f"HTTP {e.status_code}" if e.status_code else "unreachable",
        )
        _detach()
        if not attach_broker_env():
            raise
        return acquire_lease(
            model, priority=priority, on_update=on_update, max_hold_s=max_hold_s
        )


@dataclass
class _HeldLease:
    model: str
    live: bool = True


# A context variable, not a thread-local, so a thread started with a copy of the
# holder's context (the stream watchdog's reader) shares the lease. ``live`` is
# shared by every copy, so ending the lease ends it in all of them at once.
_held: contextvars.ContextVar[Optional[_HeldLease]] = contextvars.ContextVar(
    "gaia_model_slot_lease", default=None
)


def _current_hold() -> Optional[_HeldLease]:
    hold = _held.get()
    return hold if hold is not None and hold.live else None


def holding_lease() -> bool:
    """True when this context already owns the model slot (see :func:`model_lease`)."""
    return _current_hold() is not None


@contextmanager
def model_lease(
    model: str,
    *,
    priority: Optional[str] = None,
    on_wait=None,
    max_hold_s: Optional[float] = None,
) -> Iterator[Optional[dict]]:
    """Hold a model-slot lease for the duration of the ``with`` block.

    When the broker is not configured this is a no-op (standalone mode) and
    yields ``None``. Otherwise it acquires a lease (blocking until the slot is
    free), yields the lease dict, and releases on exit — even on exception.

    **Re-entrant per context.** The broker grants exactly one lease at a time, so
    a nested acquire in a context that already holds the slot would block forever
    waiting for itself. Nesting is normal now that the lease lives at the
    ``load_model`` chokepoint (#2248): a caller wrapping a multi-step
    unload→load sequence in an outer lease reaches an inner one on the load. The
    outermost ``with`` owns the lease; inner ones yield ``None`` and are no-ops.

    ``on_wait`` is called once, with a human-readable reason, as soon as the
    request has to queue, so the caller can surface a ``switching model…``
    status while it waits. Every queue update also reaches this context's
    :func:`lease_wait_listener`. While the lease is held it is renewed in the
    background, so a long request keeps the slot up to *max_hold_s* (its call
    budget; the broker's fixed TTL without one).
    """
    # Discover lazily, at the moment of the load, rather than once at process
    # start: a daemon can come up *after* a long-lived host process (the UI
    # backend) did, and a start-time-only check would leave every later load in
    # that process permanently unbrokered. Gated on the process having opted in
    # (see :func:`enable_broker_discovery`), and once attached the address is
    # cached so this short-circuits and never probes again.
    if _broker_unsupported:
        yield None
        return
    if not broker_configured():
        if not (_discovery_enabled and attach_broker_env()):
            yield None
            return
    hold = _current_hold()
    if hold is not None:
        held_model = hold.model
        if held_model != model:
            # The outer holder leased a DIFFERENT model. Folding silently would
            # let this load happen under a lease the broker booked against
            # another model, desyncing its slot bookkeeping — loud, because the
            # quiet version is a race the broker thinks it prevented.
            raise BrokerUnavailableError(
                f"nested model-slot lease for '{model}' in a context already "
                f"holding the slot for '{held_model}'. A nested load must "
                "target the same model as the enclosing lease; leasing a "
                "second model would corrupt the broker's slot accounting. "
                "Move the inner load outside the enclosing lease."
            )
        logger.debug(
            "broker client: already holding the model slot in this context; "
            "nested lease for '%s' is a no-op",
            model,
        )
        yield None
        return
    started = time.monotonic()
    told = {"on_wait": False}

    def _queued(status: dict) -> None:
        if not told["on_wait"] and on_wait is not None:
            told["on_wait"] = True
            on_wait(describe_wait(status))
        notify_wait({"event": "queued", "scope": "daemon", **status})

    lease = _acquire_with_reattach(
        model, priority, on_update=_queued, max_hold_s=max_hold_s
    )
    if lease is None:  # daemon has no broker — proceed unbrokered (warned above)
        yield None
        return
    heartbeat = None
    hold = _HeldLease(model)
    _held.set(hold)
    try:
        heartbeat = _Heartbeat(lease)
        if lease.get("waited"):
            if not told["on_wait"] and on_wait is not None:
                # A daemon from before queue streaming reports the wait only now.
                on_wait(
                    "switching model…"
                    if lease.get("switching")
                    else "waiting for the model slot…"
                )
            notify_wait(
                {
                    "event": "granted",
                    "scope": "daemon",
                    "model": model,
                    "waited_s": time.monotonic() - started,
                }
            )
        yield lease
    finally:
        hold.live = False
        _held.set(None)
        if heartbeat is not None:
            heartbeat.stop()
        release_lease(lease["lease_id"])


def queue_status_event(event: dict) -> dict:
    """The turn-stream ``status`` event for a :func:`lease_wait_listener` event.

    One shape for every stream a turn has (the Agent UI's and the sidecar's):
    ``phase: "model_queue"``, a human ``message``, and the wait itself under
    ``queue`` (``event`` ``queued``/``granted``, ``position``, ``queue_length``,
    ``holder``, ``holder_model``, ``waited_s``).
    """
    waited = int(event.get("waited_s") or 0)
    if event.get("event") == "granted":
        message = f"Got the model after waiting {waited}s"
    else:
        message = f"{describe_wait(event)} ({waited}s)"
    return {
        "type": "status",
        "status": "working",
        "phase": "model_queue",
        "message": message,
        "queue": event,
    }


def describe_wait(status: dict) -> str:
    """A one-line, user-facing description of a queued wait."""
    holder = status.get("holder")
    if status.get("scope") == "process":
        ahead = "another request from this app is using it"
    elif holder:
        ahead = f"'{holder}' is using it for '{status.get('holder_model')}'"
    else:
        ahead = "a higher-priority request goes first"
    place = ""
    if status.get("queue_length", 1) > 1:
        place = f", #{status['position']} of {status['queue_length']} in line"
    return f"Waiting for the model — {ahead}{place}"
