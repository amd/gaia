# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OAuth re-forward timer — keep every running sidecar's forwarded connector
access tokens fresh (issues #2388 / #2159).

The daemon forwards SHORT-LIVED access tokens to a sidecar only at spawn time
(``SidecarRegistry._fire_started`` -> ``forward.ConnectionForwarder.forward_all``).
Nothing re-forwarded them afterwards, so once the forwarded token expired (~1h;
observed dying at ~2.6h continuous uptime) every Gmail/Graph call the sidecar
made returned 401 and the sidecar never self-recovered — only a restart fixed
it. Both ``forward.py`` and the sidecar's ``forwarded_credentials`` module
already documented a re-forward that was never actually implemented; this module
is it.

Design (§0.6 role inversion): the daemon stays the single owner of the long-lived
refresh token. This timer periodically re-mints (via the connectors token cache,
which only performs a real OAuth refresh when the cached access token is near
expiry) and re-forwards each granted+connected provider to the sidecar's intake,
overwriting the about-to-expire token minutes before the sidecar's own 30s expiry
buffer would trip. Because the interval is kept well below the access-token TTL,
a freshly-refreshed token is always re-forwarded in time.

Fail loudly, no silent fallbacks (CLAUDE.md): the per-tick loop is resilient
(one wedged sidecar or a transient blip must not kill the timer and end recovery
for all sidecars), but nothing is swallowed silently — every failure is logged
with context and ``exc_info``. A revoked or disconnected provider stops being
re-forwarded — its per-tick ``forward_all`` reports the error (logged loudly),
this timer never fabricates a token to keep it alive, and the sidecar's own
resolver still raises an actionable error at mailbox-use time once the last
forwarded token expires.
"""

from __future__ import annotations

import os
import threading

from gaia.logger import get_logger

logger = get_logger(__name__)

# Re-forward cadence. MUST stay well below the shortest expected access-token TTL
# (~1h) so the connectors token cache's freshly-refreshed token reaches the
# sidecar minutes before its 30s expiry buffer trips. 5 minutes gives ~12
# re-forwards per hour of lead. Re-minting a still-valid token is cheap and
# idempotent (the cache returns it unchanged until near expiry), so ticking
# often costs almost nothing.
DEFAULT_REFRESH_INTERVAL = 300.0

_INTERVAL_ENV_VAR = "GAIA_DAEMON_FORWARD_REFRESH_INTERVAL"

# Bounded join so daemon shutdown never hangs behind the timer thread.
_STOP_JOIN_TIMEOUT = 5.0


def resolve_interval() -> float:
    """Refresh interval in seconds: ``GAIA_DAEMON_FORWARD_REFRESH_INTERVAL`` or
    :data:`DEFAULT_REFRESH_INTERVAL`.

    A non-positive or unparseable override is a loud error, not a silent
    fallback — an interval of 0 would mean "never re-forward", reintroducing the
    exact bug this module fixes.
    """
    raw = os.environ.get(_INTERVAL_ENV_VAR)
    if raw is None or raw.strip() == "":
        return DEFAULT_REFRESH_INTERVAL
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(
            f"{_INTERVAL_ENV_VAR}={raw!r} is not a number. Set it to a positive "
            f"number of seconds (well below the access-token TTL), or unset it to "
            f"use the {DEFAULT_REFRESH_INTERVAL:.0f}s default."
        ) from e
    if value <= 0:
        raise ValueError(
            f"{_INTERVAL_ENV_VAR}={raw!r} must be positive — a non-positive "
            "interval would disable re-forwarding and let forwarded tokens expire "
            "unrecovered (the bug this timer fixes)."
        )
    return value


class ForwardRefresher:
    """Background thread that periodically re-forwards granted connector tokens
    to every running sidecar.

    The registry (``running_connections``) and forwarder (``forward_all``) seams
    are injected so the tick logic is unit-tested without a real thread wall
    clock or a live sidecar.
    """

    def __init__(
        self,
        registry,
        forwarder,
        *,
        interval: "float | None" = None,
    ):
        self._registry = registry
        self._forwarder = forwarder
        self._interval = interval if interval is not None else resolve_interval()
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None

    # -- tick ----------------------------------------------------------------

    def tick(self) -> None:
        """Re-forward once to every running sidecar. Best-effort per sidecar: a
        failure for one is logged loudly and does not stop the others."""
        connections = self._registry.running_connections()
        if not connections:
            return
        for agent_id, base_url, bearer in connections:
            try:
                summary = self._forwarder.forward_all(
                    agent_id, base_url=base_url, bearer=bearer
                )
            except Exception:  # noqa: BLE001 - logged loudly, next sidecar still tried
                logger.warning(
                    "forward-refresh: re-forward to sidecar '%s' raised; its "
                    "forwarded credentials may be stale until the next tick — it "
                    "surfaces loudly at mailbox-use time",
                    agent_id,
                    exc_info=True,
                )
                continue
            errors = summary.get("errors") if isinstance(summary, dict) else None
            if errors:
                logger.warning(
                    "forward-refresh: re-forward to sidecar '%s' reported "
                    "provider errors: %s",
                    agent_id,
                    errors,
                )
            else:
                logger.debug("forward-refresh: re-forwarded to sidecar '%s'", agent_id)

    def _safe_tick(self) -> None:
        """Run one tick, absorbing any unexpected error so the loop survives to
        retry next interval. Loud (``exc_info``), never silent."""
        try:
            self.tick()
        except Exception:  # noqa: BLE001 - the timer must outlive a transient failure
            logger.error(
                "forward-refresh: tick failed unexpectedly; the re-forward timer "
                "will retry at the next interval",
                exc_info=True,
            )

    # -- lifecycle -----------------------------------------------------------

    def _run(self) -> None:
        # wait() returns True when stop is set (exit), False on timeout (tick).
        while not self._stop.wait(self._interval):
            self._safe_tick()

    def start(self) -> None:
        """Spawn the background timer thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="forward-refresher", daemon=True
        )
        self._thread.start()
        logger.info("forward-refresh: started (interval=%.0fs)", self._interval)

    def stop(self) -> None:
        """Signal the timer to exit and join it (idempotent, safe unstarted)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_STOP_JOIN_TIMEOUT)
        self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


__all__ = [
    "ForwardRefresher",
    "DEFAULT_REFRESH_INTERVAL",
    "resolve_interval",
]
