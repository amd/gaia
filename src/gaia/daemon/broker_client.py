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
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from gaia.daemon.constants import (
    AUTH_SCHEME,
    BROKER_PRIORITY_ENV_VAR,
    BROKER_TOKEN_ENV_VAR,
    BROKER_TOKEN_FILE_ENV_VAR,
    BROKER_URL_ENV_VAR,
    HOST_API_PREFIX,
)
from gaia.logger import get_logger

logger = get_logger(__name__)


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


def _default_priority() -> str:
    return os.environ.get(BROKER_PRIORITY_ENV_VAR, "background").strip() or "background"


def acquire_lease(
    model: str,
    *,
    priority: Optional[str] = None,
    timeout: Optional[float] = None,
    request_timeout: float = 310.0,
) -> dict:
    """Acquire a model-slot lease from the broker. Blocks until granted.

    Returns the lease dict (``lease_id``, ``model``, ``waited``, ``switching``…).
    Raises :class:`BrokerUnavailableError` on any transport/HTTP failure — never
    returns without a lease when the broker is configured.

    The lease *holder* label is not a client input: the broker derives it from
    the authenticated caller (agent_id for a sidecar, ``"host"`` otherwise), so
    a caller cannot spoof another's identity in logs/status.
    """
    import requests

    url = f"{_broker_base()}{HOST_API_PREFIX}/models/lease"
    payload = {"model": model, "priority": priority or _default_priority()}
    if timeout is not None:
        payload["timeout"] = timeout
    headers = {"Authorization": f"{AUTH_SCHEME} {_credential()}"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=request_timeout)
    except requests.exceptions.RequestException as e:
        raise BrokerUnavailableError(
            f"could not reach the model-slot broker at {url} to lease '{model}': "
            f"{e}. The daemon may be down — check `gaia daemon status` and "
            "`gaia daemon logs`. Refusing a direct load that would race-evict "
            "another process's model."
        ) from e
    if r.status_code != 200:
        detail = _safe_detail(r)
        raise BrokerUnavailableError(
            f"model-slot broker refused the lease for '{model}' "
            f"(HTTP {r.status_code}): {detail}. Check `gaia daemon logs`.",
            status_code=r.status_code,
        )
    return r.json()


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


def _acquire_with_reattach(model: str, priority: Optional[str]) -> Optional[dict]:
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
        return acquire_lease(model, priority=priority)
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
        return acquire_lease(model, priority=priority)


_held = threading.local()


def holding_lease() -> bool:
    """True when this thread already owns the model slot (see :func:`model_lease`)."""
    return getattr(_held, "depth", 0) > 0


@contextmanager
def model_lease(
    model: str,
    *,
    priority: Optional[str] = None,
    on_wait=None,
) -> Iterator[Optional[dict]]:
    """Hold a model-slot lease for the duration of the ``with`` block.

    When the broker is not configured this is a no-op (standalone mode) and
    yields ``None``. Otherwise it acquires a lease (blocking until the slot is
    free), yields the lease dict, and releases on exit — even on exception.

    **Re-entrant per thread.** The broker grants exactly one lease at a time, so
    a nested acquire on a thread that already holds the slot would block forever
    waiting for itself. Nesting is normal now that the lease lives at the
    ``load_model`` chokepoint (#2248): a caller wrapping a multi-step
    unload→load sequence in an outer lease reaches an inner one on the load. The
    outermost ``with`` owns the lease; inner ones yield ``None`` and are no-ops.

    ``on_wait`` is called with a human-readable reason when the grant response
    indicates the request had to queue, so the caller can surface a
    ``switching model…`` status.
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
    if holding_lease():
        held_model = getattr(_held, "model", None)
        if held_model is not None and held_model != model:
            # The outer holder leased a DIFFERENT model. Folding silently would
            # let this load happen under a lease the broker booked against
            # another model, desyncing its slot bookkeeping — loud, because the
            # quiet version is a race the broker thinks it prevented.
            raise BrokerUnavailableError(
                f"nested model-slot lease for '{model}' on a thread already "
                f"holding the slot for '{held_model}'. A nested load must "
                "target the same model as the enclosing lease; leasing a "
                "second model would corrupt the broker's slot accounting. "
                "Move the inner load outside the enclosing lease."
            )
        logger.debug(
            "broker client: already holding the model slot on this thread; "
            "nested lease for '%s' is a no-op",
            model,
        )
        yield None
        return
    lease = _acquire_with_reattach(model, priority)
    if lease is None:  # daemon has no broker — proceed unbrokered (warned above)
        yield None
        return
    if lease.get("waited") and on_wait is not None:
        reason = (
            "switching model…"
            if lease.get("switching")
            else "waiting for the model slot…"
        )
        on_wait(reason)
    _held.depth = 1
    _held.model = model
    try:
        yield lease
    finally:
        _held.depth = 0
        _held.model = None
        release_lease(lease["lease_id"])
