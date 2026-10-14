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

At shutdown the daemon only stops a server it started itself. One that ``gaia
init``, the user, or another daemon started is left running, and one that is
slow to answer is reported rather than killed -- it may be loading a large
model. The exception is a switch to the user's own server, which stops GAIA's
whoever started it.
"""

import threading
from dataclasses import asdict, dataclass
from typing import Callable, Optional

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


def _embedded_factory():
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    return EmbeddedLemonade()


class EmbeddedLemonadeOwner:
    """Starts GAIA's embedded Lemonade on demand and stops it with the daemon.

    Calls are serialised: a client asking while the daemon's own startup is
    still bringing the server up waits for that start rather than racing it.

    Args:
        factory: Builds the :class:`~gaia.llm.lemonade_embedded.EmbeddedLemonade`
            to manage. Called per operation so ``GAIA_HOME`` is read live.
    """

    def __init__(self, factory: Callable = _embedded_factory):
        self._factory = factory
        self._lock = threading.Lock()
        self._started_pid: Optional[int] = None

    @property
    def started_pid(self) -> Optional[int]:
        """Pid of the server this daemon started and will stop, or None."""
        return self._started_pid

    @staticmethod
    def configured_url() -> Optional[str]:
        """The server the user chose (env var or saved setting), if any."""
        from gaia.llm.lemonade_client import configured_lemonade_url

        return configured_lemonade_url()

    def ensure(self) -> EnsuredLemonade:
        """Return the running embedded server, starting it if it is stopped.

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
            if current.unresponsive_pid:
                raise EmbeddedLemonadeError(
                    f"GAIA's Lemonade Server (pid {current.unresponsive_pid}) is "
                    "running but not answering; it may still be loading a model. "
                    f"Wait and retry, or run `gaia init` to restart it. Its log is "
                    f"{embedded.log_path}."
                )
            if current.running:
                if current.version != embedded.version:
                    raise EmbeddedLemonadeError(
                        f"GAIA's Lemonade Server v{current.version} is running, "
                        f"but this GAIA needs v{embedded.version}. Run `gaia init` "
                        "to replace it."
                    )
                return EnsuredLemonade(
                    base_url=current.base_url,
                    port=current.port,
                    version=current.version,
                    started=False,
                )
            status = embedded.start(install_if_missing=False)
            self._started_pid = status.pid
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

    def ensure_in_background(self) -> threading.Thread:
        """Start the server without holding up the daemon's own startup.

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
                self._started_pid = None
        elif self._factory().is_installed():
            self.ensure_in_background()
            action = "starting_gaia_lemonade"
        return {**target, "action": action}

    def stop(self) -> bool:
        """Stop the embedded server if this daemon started it.

        Bounded so the daemon's shutdown fits `gaia daemon stop`'s wait: a
        start still in flight is left to finish, and the server it brings up
        keeps running like any other this daemon did not stop.

        Returns:
            True if a server was stopped.

        Raises:
            EmbeddedLemonadeError: The server would not stop.
        """
        if not self._lock.acquire(timeout=STOP_LOCK_TIMEOUT):
            logger.warning(
                "daemon: embedded Lemonade is still starting; leaving it running"
            )
            return False
        try:
            if self._started_pid is None:
                return False
            embedded = self._factory()
            current = embedded.status()
            recorded = current.pid or current.unresponsive_pid
            if recorded is None:
                logger.info(
                    "daemon: embedded Lemonade (pid %s) had already exited",
                    self._started_pid,
                )
                self._started_pid = None
                return False
            if recorded != self._started_pid:
                logger.info(
                    "daemon: leaving embedded Lemonade (pid %s) running; this "
                    "daemon started pid %s",
                    recorded,
                    self._started_pid,
                )
                return False
            stopped = embedded.stop()
            self._started_pid = None
            return stopped
        finally:
            self._lock.release()
