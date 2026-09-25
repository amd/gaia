# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's ownership of GAIA's embedded Lemonade Server.

The daemon starts the server when it comes up and whenever a client asks, and
stops it when the daemon shuts down, so nobody has to run ``gaia init`` again
after a reboot. Installing it stays ``gaia init``'s job: the daemon only starts
what is already installed. A ``LEMONADE_BASE_URL`` means the user runs their
own server, so the daemon leaves Lemonade alone.

The daemon only ever stops a server it started itself. One that ``gaia init``,
the user, or another daemon started is left running, and one that is slow to
answer is reported rather than killed -- it may be loading a large model.
"""

import threading
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)


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

    @staticmethod
    def configured_url() -> Optional[str]:
        """The server the user chose with ``LEMONADE_BASE_URL``, if any."""
        from gaia.llm.lemonade_client import configured_lemonade_url

        return configured_lemonade_url()

    def ensure(self) -> EnsuredLemonade:
        """Return the running embedded server, starting it if it is stopped.

        Raises:
            LemonadeNotManaged: ``LEMONADE_BASE_URL`` names another server, or
                GAIA's own is not installed.
            EmbeddedLemonadeError: The server would not start, is running but
                not answering, or runs a version this GAIA does not target.
        """
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        configured = self.configured_url()
        if configured:
            raise LemonadeNotManaged(
                f"LEMONADE_BASE_URL is set to {configured}, so GAIA uses that "
                "server and does not start its own. Start that server, or unset "
                "LEMONADE_BASE_URL and restart the daemon (`gaia daemon restart`)."
            )
        with self._lock:
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
        from gaia.llm.lemonade_embedded import EmbeddedLemonadeError

        def _run() -> None:
            try:
                self.ensure()
            except LemonadeNotManaged as e:
                logger.info("daemon: not starting embedded Lemonade: %s", e)
            except EmbeddedLemonadeError as e:
                logger.error("daemon: embedded Lemonade is not usable: %s", e)

        thread = threading.Thread(target=_run, name="embedded-lemonade", daemon=True)
        thread.start()
        return thread

    def stop(self) -> bool:
        """Stop the embedded server if this daemon started it.

        Returns:
            True if a server was stopped.

        Raises:
            EmbeddedLemonadeError: The server would not stop.
        """
        with self._lock:
            if self._started_pid is None:
                return False
            embedded = self._factory()
            current = embedded.status()
            recorded = current.pid or current.unresponsive_pid
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
