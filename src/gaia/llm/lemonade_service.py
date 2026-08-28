# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One verb — "get me a running model server" — for every GAIA process.

The daemon owns the Lemonade process (see
:mod:`gaia.llm.lemonade_supervisor`). Everything else is a client of it: the Go
TUI over ``POST /daemon/v1/lemonade/start``, and every Python caller through
:func:`ensure_lemonade_running` here.

Two callers, one function, because "who owns the process" is a real distinction
and not a configuration knob:

* **Inside the daemon**, the supervisor object is right there —
  :func:`install_supervisor` puts it in reach and the call goes straight to it.
  Posting to ourselves would be a loopback round-trip to reach an object in the
  same address space.
* **Everywhere else** — a CLI invocation, a sidecar agent, the Agent UI server —
  the daemon is started-or-attached and asked. That is what makes the single
  instance real: no second process ever spawns a server, so two front-ends
  launching at once cannot race into two servers fighting over the port.

The fast path never reaches either branch. A server that is already answering
costs one probe and returns, because this sits in front of every agent
construction and every CLI call.
"""

from __future__ import annotations

from typing import Optional

from gaia.llm.lemonade_supervisor import (
    DEFAULT_START_TIMEOUT_S,
    LemonadeStartError,
    LemonadeState,
    LemonadeSupervisor,
    _probe,
)
from gaia.logger import get_logger

log = get_logger(__name__)

__all__ = [
    "LemonadeStartError",
    "LemonadeState",
    "ensure_lemonade_running",
    "install_supervisor",
    "installed_supervisor",
]

# Set by the daemon at startup. Module-level because "am I the process that owns
# the model server" is a property of the process, not of any one call site.
_SUPERVISOR: Optional[LemonadeSupervisor] = None


def install_supervisor(supervisor: Optional[LemonadeSupervisor]) -> None:
    """Declare this process the owner of the model server (``None`` clears it)."""
    global _SUPERVISOR
    _SUPERVISOR = supervisor


def installed_supervisor() -> Optional[LemonadeSupervisor]:
    """The supervisor this process owns, or ``None`` if it owns none."""
    return _SUPERVISOR


def ensure_lemonade_running(
    ctx_size: Optional[int] = None,
    timeout: float = DEFAULT_START_TIMEOUT_S,
    base_url: Optional[str] = None,
) -> LemonadeState:
    """Return once a Lemonade Server is answering, starting one if it is not.

    Args:
        ctx_size: the context window a started server must come up with. A
            server started without one answers ``/health`` and then fails every
            long request, so pass the machine's profile window
            (``lemonade_client.profile_ctx_size``).
        timeout: how long a freshly started server gets to answer.
        base_url: which server to check; omit for the ``LEMONADE_BASE_URL``
            environment default.

    Raises:
        LemonadeStartError: it is down and could not be started — not
            installed, the port is held by something else, the daemon is
            unreachable, the server died on launch. The message names what
            failed, what to do, and where to look.
    """
    supervisor = installed_supervisor()
    if supervisor is not None:
        return supervisor.ensure_running(ctx_size=ctx_size, timeout=timeout)

    return _ask_the_daemon(ctx_size=ctx_size, timeout=timeout, base_url=base_url)


def _ask_the_daemon(
    *, ctx_size: Optional[int], timeout: float, base_url: Optional[str]
) -> LemonadeState:
    """Start-or-attach the daemon and ask it for a server.

    The probe happens FIRST and short-circuits: a machine whose server is
    already up must not pay a daemon round-trip (nor start a daemon it did not
    otherwise need) just to be told so.
    """
    from gaia.llm.lemonade_client import LemonadeClient

    client = (
        LemonadeClient(base_url=base_url, keep_alive=True, verbose=False)
        if base_url
        else LemonadeClient(keep_alive=True, verbose=False)
    )
    if _probe(client):
        return LemonadeState(
            base_url=client.base_url,
            started=False,
            owned=False,
            pid=None,
            waited_seconds=0.0,
        )

    payload = {"ctx_size": ctx_size} if ctx_size else {}
    body = _post_start(payload, timeout)
    return LemonadeState(
        base_url=body.get("base_url", client.base_url),
        started=body.get("status") == "started",
        # Reported by the daemon, never assumed: it is False for a server the
        # daemon merely FOUND (one the user launched from the tray), which it
        # will not reap at shutdown. Hardcoding True here would make this
        # dataclass claim an ownership that does not exist.
        owned=bool(body.get("supervised", False)),
        pid=body.get("pid"),
        waited_seconds=float(body.get("waited_seconds") or 0.0),
    )


def _post_start(payload: dict, timeout: float) -> dict:
    """POST ``/daemon/v1/lemonade/start``, translating every failure loudly."""
    import requests

    from gaia.daemon import client as daemon_client
    from gaia.daemon.constants import API_PREFIX, AUTH_SCHEME
    from gaia.daemon.errors import DaemonError

    try:
        inst = daemon_client.start_or_attach()
    except DaemonError as e:
        raise LemonadeStartError(
            "The local model server is not running, and GAIA could not reach "
            f"the background service that owns it: {e}\n"
            "To fix: start it with `gaia daemon start`, then retry.\n"
            "Where to look: `gaia daemon logs`"
        ) from e

    url = f"http://{inst.host}:{inst.port}{API_PREFIX}/lemonade/start"
    headers = {"Authorization": f"{AUTH_SCHEME} {inst.token}"}
    # The client waits strictly longer than the daemon's own start budget, so a
    # start that was about to succeed is never aborted here and reported as a
    # different failure.
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout + 30)
    except requests.RequestException as e:
        raise LemonadeStartError(
            "The local model server is not running, and the request asking the "
            f"background service to start it failed: {e}\n"
            "To fix: check it with `gaia daemon status`, then retry.\n"
            "Where to look: `gaia daemon logs`"
        ) from e

    if resp.status_code == 200:
        return resp.json()

    # The daemon's own detail is the most specific thing in the system about
    # WHICH failure this was, so it is surfaced verbatim rather than restated.
    detail = ""
    try:
        detail = resp.json().get("detail", "")
    except ValueError:
        detail = resp.text.strip()
    raise LemonadeStartError(
        detail
        or (
            f"The background service refused to start the local model server "
            f"(HTTP {resp.status_code}).\n"
            "To fix: check it with `gaia daemon status`.\n"
            "Where to look: `gaia daemon logs`"
        )
    )
