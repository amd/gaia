# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Client side of the model-slot broker (V2-11 · §0.12).

Any process that loads a model — a sidecar, or the host-side embedder/RAG in the
UI server — routes that load through the daemon's broker so loads serialize
instead of racing the single slot. This module is the thin HTTP client for the
``POST /host/v1/models/lease`` route, plus the :func:`model_lease` context
manager that acquires a lease, yields, and releases it.

**Env-gated, and no silent fallback (CLAUDE.md).** Broker routing is active only
when ``GAIA_MODEL_BROKER_URL`` is set — the daemon sets it for the processes it
spawns and host-side components discover it via ``start_or_attach``. When it is
unset the caller is running standalone (e.g. ``gaia llm`` with no daemon): there
is no slot contention to arbitrate, so :func:`model_lease` is a no-op — that is
the *absence* of a broker, not a fallback around a failed one. But when the URL
IS set and the broker cannot be reached, :func:`model_lease` raises loudly rather
than doing a direct load that would race-evict another process's model.
"""

from __future__ import annotations

import os
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
    """


def broker_configured() -> bool:
    """True when this process should route model loads through the broker."""
    return bool(os.environ.get(BROKER_URL_ENV_VAR, "").strip())


def _broker_base() -> str:
    return os.environ[BROKER_URL_ENV_VAR].rstrip("/")


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
            f"(HTTP {r.status_code}): {detail}. Check `gaia daemon logs`."
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

    ``on_wait`` is called with a human-readable reason when the grant response
    indicates the request had to queue, so the caller can surface a
    ``switching model…`` status.
    """
    if not broker_configured():
        yield None
        return
    lease = acquire_lease(model, priority=priority)
    if lease.get("waited") and on_wait is not None:
        reason = (
            "switching model…"
            if lease.get("switching")
            else "waiting for the model slot…"
        )
        on_wait(reason)
    try:
        yield lease
    finally:
        release_lease(lease["lease_id"])
