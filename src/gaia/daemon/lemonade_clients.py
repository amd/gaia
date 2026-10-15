# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which GAIA front ends are using GAIA's Lemonade, and when to free its memory.

Every GAIA front end that runs on the local server registers here: the Agent UI
backend (Electron app and ``npx @amd-gaia/agent-ui``), the TUI, and each
``gaia`` CLI command that needs a model. A registration names the processes it
lives as long as, and ends when they all exit or the client releases it.

The policy, in two rules:

1. **An app closes, the models go.** When an app (``kind="app"``: Agent UI or
   TUI) leaves and no other GAIA client remains, every model is unloaded from
   GAIA's server. The server keeps running, so the next launch skips the
   server start but reloads the model. A CLI command (``kind="cli"``) keeps the
   models resident while it runs but never unloads on its own exit, so a run of
   ``gaia llm`` calls does not reload the model every time.
2. **Nobody here, the server goes.** Once no client has been registered for
   ``lemonade_idle_timeout_minutes`` (``gaia config``; 0 turns it off), GAIA's
   server is stopped outright. The next client to register starts it again.

A running sidecar agent counts as a client for both rules. Neither rule ever
touches a server ``LEMONADE_BASE_URL`` names -- that is the user's -- because
both act only through :class:`~gaia.daemon.lemonade.EmbeddedLemonadeOwner`.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

#: How often dead clients are noticed and the idle timeout is checked.
SWEEP_INTERVAL = 5.0

#: How long daemon shutdown waits for the sweep thread to finish.
STOP_JOIN_TIMEOUT = 1.0

CLIENT_KINDS = ("app", "cli")

#: What :func:`secrets.token_urlsafe` produces, and nothing a log could abuse.
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")

#: ``(pid, create_time)`` -- a pid alone is reused by the OS.
ProcessIdentity = Tuple[int, float]


def process_identity(pid: int) -> Optional[ProcessIdentity]:
    """*pid* with its start time, or None when no such process runs."""
    import psutil

    try:
        return pid, psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return None


def idle_timeout_seconds() -> Optional[float]:
    """The configured idle timeout, or None when it is turned off.

    Raises:
        GaiaConfigError: ``~/.gaia/config.json`` is unreadable or holds a
            value that is not a whole number of minutes.
    """
    from gaia.config import GaiaConfig

    minutes = GaiaConfig.load().lemonade_idle_timeout_minutes
    return minutes * 60.0 if minutes > 0 else None


@dataclass
class _Client:
    client_id: str
    kind: str
    name: str
    processes: List[ProcessIdentity]

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "kind": self.kind,
            "name": self.name,
            "pids": [pid for pid, _ in self.processes],
        }


class ClientRejected(ValueError):
    """A registration names no usable process. The message says which."""


class LemonadeClients:
    """The GAIA clients of the daemon's Lemonade, and the two rules above.

    Args:
        owner: The :class:`~gaia.daemon.lemonade.EmbeddedLemonadeOwner` whose
            server this frees.
        busy: True while something outside this registry needs the server,
            e.g. a running sidecar agent.
        timeout: Returns the idle timeout in seconds, or None when it is off.
        identity: Returns a process's identity, or None when it is gone.
        clock: Monotonic seconds.
    """

    def __init__(
        self,
        owner,
        *,
        busy: Callable[[], bool] = lambda: False,
        timeout: Callable[[], Optional[float]] = idle_timeout_seconds,
        identity: Callable[[int], Optional[ProcessIdentity]] = process_identity,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._owner = owner
        self._busy = busy
        self._timeout = timeout
        self._identity = identity
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: Dict[str, _Client] = {}
        # Bumped by every registration; a decision to unload or stop is only
        # carried out if no client arrived since it was made.
        self._generation = 0
        # An app left while another client was still here; unload once the
        # last one goes.
        self._unload_pending = False
        self._empty_since: Optional[float] = clock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._timeout_error: Optional[str] = None

    # -- registration ------------------------------------------------------

    def register(
        self,
        kind: str,
        pids: List[int],
        name: str = "",
        client_id: Optional[str] = None,
    ) -> dict:
        """Add a client that lives as long as every one of *pids*.

        Args:
            client_id: Re-register under this id. Clients repeat their
                registration periodically, so one registered with a daemon
                that has since restarted is known to the new one too.

        Returns:
            ``client_id``, how many ``clients`` are connected, and whether this
            call ``added`` the client (False when it was already registered).

        Raises:
            ClientRejected: *kind* or *client_id* is malformed, or a pid is not
                running.
        """
        if client_id is not None and not _CLIENT_ID.fullmatch(client_id):
            raise ClientRejected(f"Malformed client id {client_id!r}.")
        with self._lock:
            if client_id in self._clients:
                return {
                    "client_id": client_id,
                    "clients": len(self._clients),
                    "added": False,
                }
        if kind not in CLIENT_KINDS:
            raise ClientRejected(
                f"Unknown client kind {kind!r}; expected one of {', '.join(CLIENT_KINDS)}."
            )
        if not pids:
            raise ClientRejected("A client must name at least one process id.")
        processes = []
        for pid in pids:
            ident = self._identity(int(pid))
            if ident is None:
                raise ClientRejected(
                    f"Process {pid} is not running, so it cannot hold GAIA's "
                    "Lemonade Server open."
                )
            processes.append(ident)
        client = _Client(
            client_id=client_id or secrets.token_urlsafe(12),
            kind=kind,
            name=name or kind,
            processes=processes,
        )
        with self._lock:
            self._clients[client.client_id] = client
            self._generation += 1
            self._empty_since = None
            count = len(self._clients)
        logger.info(
            "lemonade clients: %s registered (%s, pids %s); %d connected",
            client.name,
            kind,
            [p for p, _ in processes],
            count,
        )
        return {"client_id": client.client_id, "clients": count, "added": True}

    def release(self, client_id: str) -> dict:
        """Remove a client; unload the models if it was the last app.

        Raises:
            KeyError: No such client (already released, or swept as dead).
        """
        with self._lock:
            client = self._clients.pop(client_id)
            if client.kind == "app":
                self._unload_pending = True
        logger.info("lemonade clients: %s released", client.name)
        return self._after_departure()

    def sweep(self) -> None:
        """Drop clients whose processes exited, then apply both rules."""
        with self._lock:
            gone = [
                c
                for c in self._clients.values()
                if any(self._identity(pid) != (pid, t) for pid, t in c.processes)
            ]
            for client in gone:
                del self._clients[client.client_id]
                if client.kind == "app":
                    self._unload_pending = True
        for client in gone:
            logger.info(
                "lemonade clients: %s exited without releasing; dropped",
                client.name,
            )
        self._after_departure()
        self._check_idle()

    def snapshot(self) -> dict:
        """The connected clients and the idle clock, for status routes."""
        with self._lock:
            clients = [c.to_dict() for c in self._clients.values()]
            empty_since = self._empty_since
        idle = None if empty_since is None else self._clock() - empty_since
        return {
            "clients": clients,
            "idle_seconds": idle,
            "idle_timeout_error": self._timeout_error,
        }

    # -- the two rules -----------------------------------------------------

    def _after_departure(self) -> dict:
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        with self._lock:
            remaining = len(self._clients)
            if remaining == 0 and self._empty_since is None:
                self._empty_since = self._clock()
            unload = remaining == 0 and self._unload_pending and not self._busy()
            if unload:
                self._unload_pending = False
            generation = self._generation
        result = {"clients": remaining, "unloaded": False}
        if not unload:
            return result
        try:
            result["unloaded"] = self._owner.unload_models(
                still_wanted=lambda: self._unchanged_since(generation)
            )
        except EmbeddedLemonadeError as e:
            logger.error("lemonade clients: could not unload models: %s", e)
            result["unload_error"] = str(e)
            return result
        if result["unloaded"]:
            logger.info(
                "lemonade clients: last GAIA app closed; unloaded every model "
                "from GAIA's Lemonade Server"
            )
        return result

    def _check_idle(self) -> None:
        from gaia.config import GaiaConfigError
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        now = self._clock()
        with self._lock:
            if self._clients:
                return
            if self._busy():
                self._empty_since = now
                return
            empty_since = self._empty_since
            generation = self._generation
        try:
            timeout = self._timeout()
        except GaiaConfigError as e:
            if self._timeout_error != str(e):
                logger.error(
                    "lemonade clients: idle timeout is off until the config is "
                    "fixed: %s",
                    e,
                )
            self._timeout_error = str(e)
            return
        self._timeout_error = None
        if timeout is None or empty_since is None or now - empty_since < timeout:
            return
        with self._lock:
            # Restart the clock either way, so a failed stop is not retried
            # every sweep.
            self._empty_since = now
        try:
            stopped = self._owner.stop(
                reason=f"no GAIA client connected for {timeout / 60:g} minutes",
                still_wanted=lambda: self._unchanged_since(generation),
            )
        except EmbeddedLemonadeError as e:
            logger.error("lemonade clients: idle stop failed: %s", e)
            return
        if stopped:
            logger.info("lemonade clients: idle timeout stopped GAIA's Lemonade")

    def _unchanged_since(self, generation: int) -> bool:
        """No client registered since *generation*, and none is connected."""
        with self._lock:
            return self._generation == generation and not self._clients

    # -- background sweep --------------------------------------------------

    def start(self, interval: float = SWEEP_INTERVAL) -> None:
        """Sweep every *interval* seconds on a daemon thread until :meth:`stop`."""
        if self._thread is not None:
            return

        def _run() -> None:
            while not self._stop_event.wait(interval):
                try:
                    self.sweep()
                except Exception:  # pylint: disable=broad-except
                    # Keep sweeping: a dead sweeper would hold models forever.
                    logger.exception("lemonade clients: sweep failed")

        self._thread = threading.Thread(
            target=_run, name="lemonade-clients", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the sweep thread. Safe when it never started.

        Does not wait out a sweep mid-unload: shutdown stops the server anyway,
        and `gaia daemon stop`'s wait has no room for it.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=STOP_JOIN_TIMEOUT)
            self._thread = None
