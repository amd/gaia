# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's ownership of GAIA's embedded Lemonade Server.

The daemon starts the server when it comes up and whenever a client asks, and
stops it when the daemon shuts down, so nobody has to run ``gaia init`` again
after a reboot. Installing it stays ``gaia init``'s job: the daemon only starts
what is already installed. A ``LEMONADE_BASE_URL``, or the saved
``lemonade_mode: external`` choice, means the user runs their own server, so
the daemon leaves Lemonade alone -- and :meth:`EmbeddedLemonadeOwner.reconfigure`
stops GAIA's own when the user switches to theirs.

The daemon owns every instance of GAIA's own server, whoever started it. One
that ``gaia init`` or ``gaia lemonade embedded start`` brought up is claimed the
first time the daemon finds it healthy, and stops with the daemon. One a
crashed daemon left behind is adopted while it still answers on the right
version, and reaped otherwise. Nothing is signalled until
:meth:`~gaia.llm.lemonade_embedded.EmbeddedLemonade.stop` has confirmed the pid
is GAIA's lemond, so a user's Lemonade or a recycled pid is never touched.

A slow server whose owner is still alive is reported, never killed: it may be
loading a large model. Switching to the user's own server stops GAIA's,
whoever started it.
"""

import os
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

#: How long shutdown waits for a start already in flight before leaving it be.
STOP_LOCK_TIMEOUT = 5.0


class LemonadeNotManaged(Exception):
    """The daemon has nothing to start: another server is configured, or GAIA's
    own is not installed. The message names the remedy."""


@dataclass(frozen=True)
class EnsuredLemonade:
    """The embedded server a client should talk to."""

    base_url: str
    port: int
    version: str
    started: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LemonadeActivity:
    """What GAIA's server is doing right now, from its ``/health``.

    Lemonade 2026.40+ marks each loaded model ``is_busy`` while a request holds
    it, a stream included, and moves its ``last_use`` whenever a request starts
    or a model loads. ``last_use`` counts from an arbitrary epoch, so it is
    only ever compared with itself: a changed :attr:`marker` means traffic.
    """

    busy: bool
    marker: Tuple
    detail: str = ""


def activity_from_health(health: dict) -> LemonadeActivity:
    """Read :class:`LemonadeActivity` out of a ``/health`` payload."""
    models = [m for m in health.get("all_models_loaded") or [] if isinstance(m, dict)]
    busy = [
        str(m.get("model_name"))
        for m in models
        if m.get("is_busy") or m.get("is_streaming")
    ]
    marker = (
        health.get("model_loaded"),
        tuple(
            sorted((str(m.get("model_name")), str(m.get("last_use"))) for m in models)
        ),
    )
    return LemonadeActivity(
        busy=bool(busy),
        marker=marker,
        detail=f"serving {', '.join(busy)}" if busy else "",
    )


def _embedded_factory():
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    return EmbeddedLemonade()


def _process_started(pid: int) -> Optional[float]:
    """When *pid* started, or None when that cannot be read."""
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def _owner_alive(pid: int, started: Optional[float]) -> bool:
    """Whether the daemon recorded as *pid* (started at *started*) still runs.

    A process GAIA may not inspect counts as alive: an orphan is only reaped on
    proof its owner is gone. A record without a start time can only be checked
    by pid.
    """
    import psutil

    try:
        actual = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    return started is None or abs(actual - started) < 1.0


class EmbeddedLemonadeOwner:
    """Starts GAIA's embedded Lemonade on demand and stops it with the daemon.

    Calls are serialised: a client asking while the daemon's own startup is
    still bringing the server up waits for that start rather than racing it.

    Args:
        factory: Builds the :class:`~gaia.llm.lemonade_embedded.EmbeddedLemonade`
            to manage. Called per operation so ``GAIA_HOME`` is read live.
        owner_pid: This daemon's pid, recorded in the server's state as its
            owner. Defaults to the current process.
        owner_alive: Whether a recorded owner ``(pid, start time)`` still runs.
            Injected so tests can stage a crashed daemon.
    """

    def __init__(
        self,
        factory: Callable = _embedded_factory,
        owner_pid: Optional[int] = None,
        owner_alive: Optional[Callable[[int, Optional[float]], bool]] = None,
    ):
        self._factory = factory
        self._lock = threading.Lock()
        self._owner_pid = owner_pid if owner_pid is not None else os.getpid()
        self._owner_started = _process_started(self._owner_pid)
        self._owner_alive = owner_alive or _owner_alive

    @property
    def owner_pid(self) -> int:
        """The pid this daemon records as the owner of GAIA's server."""
        return self._owner_pid

    @staticmethod
    def configured_url() -> Optional[str]:
        """The server the user chose (env var or saved setting), if any."""
        from gaia.llm.lemonade_client import configured_lemonade_url

        return configured_lemonade_url()

    def _orphaned(self, current) -> bool:
        """Whether *current* belonged to a daemon that is no longer running."""
        owner = current.owner_pid
        return (
            owner is not None
            and owner != self._owner_pid
            and not self._owner_alive(owner, current.owner_started)
        )

    def ensure(self) -> EnsuredLemonade:
        """Return the running embedded server, starting it if it is stopped.

        A healthy server someone else started is claimed, so this daemon stops
        it later. One a crashed daemon left that is not answering, or runs
        another version, is reaped and replaced.

        Raises:
            LemonadeNotManaged: ``LEMONADE_BASE_URL`` or the saved setting names
                another server, or GAIA's own is not installed.
            LemonadeChoiceError: The saved setting is unusable.
            EmbeddedLemonadeError: The server would not start, is running but
                not answering, or runs a version this GAIA does not target.
        """
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        # Under the lock, so a switch to the user's server that reconfigure()
        # is applying can't be undone by a start already on its way.
        with self._lock:
            configured = self.configured_url()
            if configured:
                from gaia.llm.lemonade_choice import (
                    configured_source,
                    switch_back_hint,
                )

                chosen_by = (
                    "LEMONADE_BASE_URL is set to"
                    if configured_source() == "env"
                    else "GAIA is set to use your Lemonade Server at"
                )
                raise LemonadeNotManaged(
                    f"{chosen_by} {configured}, so GAIA does not start its own. "
                    f"Start that server, or {switch_back_hint()}."
                )
            embedded = self._factory()
            if not embedded.is_installed():
                raise LemonadeNotManaged(
                    "GAIA's Lemonade Server is not installed. Run `gaia init` to "
                    "install it."
                )
            current = embedded.status()
            orphaned = self._orphaned(current)
            if current.unresponsive_pid and not orphaned:
                raise EmbeddedLemonadeError(
                    f"GAIA's Lemonade Server (pid {current.unresponsive_pid}) is "
                    "running but not answering; it may still be loading a model. "
                    f"Wait and retry, or run `gaia init` to restart it. Its log is "
                    f"{embedded.log_path}."
                )
            if current.running and current.version == embedded.version:
                return self._claim(embedded, current)
            if current.running and not orphaned:
                raise EmbeddedLemonadeError(
                    f"GAIA's Lemonade Server v{current.version} is running, "
                    f"but this GAIA needs v{embedded.version}. Run `gaia init` "
                    "to replace it."
                )
            if orphaned:
                logger.warning(
                    "daemon: reaping embedded Lemonade (pid %s) left by daemon "
                    "pid %s, which exited; it was %s",
                    current.pid or current.unresponsive_pid,
                    current.owner_pid,
                    (
                        "not answering"
                        if current.unresponsive_pid
                        else f"running v{current.version}"
                    ),
                )
                embedded.stop()
            status = embedded.start(
                install_if_missing=False,
                owner_pid=self._owner_pid,
                owner_started=self._owner_started,
            )
            logger.info(
                "daemon: started embedded Lemonade %s on port %s",
                status.version,
                status.port,
            )
            return EnsuredLemonade(
                base_url=status.base_url,
                port=status.port,
                version=status.version,
                started=True,
            )

    def _claim(self, embedded, current) -> EnsuredLemonade:
        if current.owner_pid != self._owner_pid:
            embedded.claim(self._owner_pid, self._owner_started)
            logger.info(
                "daemon: took ownership of embedded Lemonade (pid %s, port %s) "
                "started by %s",
                current.pid,
                current.port,
                (
                    f"daemon pid {current.owner_pid}, which exited"
                    if current.owner_pid
                    else "gaia init or the CLI"
                ),
            )
        return EnsuredLemonade(
            base_url=current.base_url,
            port=current.port,
            version=current.version,
            started=False,
        )

    def ensure_in_background(self) -> threading.Thread:
        """Start the server without holding up the caller.

        A failure is logged; the next client to call :meth:`ensure` gets the
        same error raised to it, so it is never lost.
        """
        from gaia.config import GaiaConfigError
        from gaia.llm.lemonade_choice import LemonadeChoiceError
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        def _run() -> None:
            try:
                self.ensure()
            except LemonadeNotManaged as e:
                logger.info("daemon: not starting embedded Lemonade: %s", e)
            except (EmbeddedLemonadeError, LemonadeChoiceError, GaiaConfigError) as e:
                logger.error("daemon: embedded Lemonade is not usable: %s", e)

        thread = threading.Thread(target=_run, name="embedded-lemonade", daemon=True)
        thread.start()
        return thread

    def reconfigure(self) -> dict:
        """Apply the current Lemonade choice, read afresh from config.

        Switching to the user's server stops GAIA's own, whoever started it: it
        is GAIA's private instance, nothing uses it any more, and leaving it up
        is the doubled memory the switch exists to free. Switching back starts
        GAIA's own in the background, as the daemon does at boot.

        Returns:
            The effective choice (:func:`gaia.llm.lemonade_choice.describe`)
            plus ``action``: ``stopped_gaia_lemonade``, ``starting_gaia_lemonade``
            or ``none``.

        Raises:
            EmbeddedLemonadeError: GAIA's own server would not stop.
            LemonadeChoiceError: The saved choice is unusable.
        """
        from gaia.llm.lemonade_choice import describe

        target = describe()
        action = "none"
        if target["mode"] == "external":
            with self._lock:
                if self._factory().stop():
                    logger.info(
                        "daemon: switched to the Lemonade at %s; stopped GAIA's own",
                        target["base_url"],
                    )
                    action = "stopped_gaia_lemonade"
        elif self._factory().is_installed():
            self.ensure_in_background()
            action = "starting_gaia_lemonade"
        return {**target, "action": action}

    def activity(self) -> Optional[LemonadeActivity]:
        """What GAIA's own server is doing, or None when none is running.

        A recorded server that does not answer counts as busy: it may be
        loading a large model, and it is never stopped for being idle.

        Never touches a server ``LEMONADE_BASE_URL`` names.
        """
        if self.configured_url():
            return None
        recorded, health = self._factory().recorded_health()
        if not recorded:
            return None
        if health is None:
            return LemonadeActivity(
                busy=True,
                marker=("unanswered",),
                detail="not answering its health check",
            )
        return activity_from_health(health)

    def unload_models(self, still_wanted: Callable[[], bool] = lambda: True) -> bool:
        """Unload every model from GAIA's own server; the server keeps running.

        Never touches a server ``LEMONADE_BASE_URL`` names: that one is the
        user's, and so are its models. Not under the start lock: an unload is
        one quick request, and holding the lock would stall shutdown behind it.

        Args:
            still_wanted: Checked last, so a client that registered since the
                caller decided keeps its models.

        Returns:
            True if models were unloaded, False if GAIA's server is not running
            or *still_wanted* said no.

        Raises:
            EmbeddedLemonadeError: The server refused the unload.
        """
        if self.configured_url() or not still_wanted():
            return False
        return self._factory().unload_models()

    def stop(
        self,
        reason: str = "the daemon is shutting down",
        still_wanted: Callable[[], bool] = lambda: True,
    ) -> bool:
        """Stop GAIA's embedded server, whoever started it.

        Bounded so the daemon's shutdown fits `gaia daemon stop`'s wait: a
        start still in flight is left to finish, and `gaia daemon stop` stops
        what it brings up once the daemon has exited.

        Args:
            reason: Why, for the log.
            still_wanted: Checked under the start lock as the last step before
                the stop, so a client that registered, or a request that
                arrived, since the caller decided keeps its server.

        Returns:
            True if a server was stopped.

        Raises:
            EmbeddedLemonadeError: The server would not stop, or its recorded
                pid could not be confirmed as GAIA's server.
        """
        if self.configured_url():
            return False
        if not self._lock.acquire(timeout=STOP_LOCK_TIMEOUT):
            logger.warning(
                "daemon: embedded Lemonade is still starting; leaving it running"
            )
            return False
        try:
            embedded = self._factory()
            current = embedded.status()
            if not (current.running or current.unresponsive_pid):
                return False
            # Last, right before the signal, so a request that just landed wins.
            if not still_wanted():
                return False
            logger.info(
                "daemon: stopping embedded Lemonade (pid %s) because %s",
                current.pid or current.unresponsive_pid,
                reason,
            )
            return embedded.stop()
        finally:
            self._lock.release()
