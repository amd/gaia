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
* **Everywhere else** — a sidecar agent, the Agent UI server, a library caller —
  the RUNNING daemon is attached to and asked. That is what makes the single
  instance real: no second process ever spawns a server, so two front-ends
  launching at once cannot race into two servers fighting over the port.

It attaches; it never *starts* a daemon. Bringing up a machine-wide background
service is not something a readiness check may do as a side effect — that is a
front-end's decision, made once, where a user can see it. Getting this wrong is
not theoretical: an earlier revision start-or-attached here, and constructing an
agent in a unit test then spawned a daemon and a model server and took 30
seconds. :func:`ensure_daemon_owns_lemonade` is the opt-in a front-end calls.

The fast path never reaches either branch. A server that is already answering
costs one probe and returns, because this sits in front of every agent
construction and every CLI call.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import List, Optional

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
    "ensure_daemon_owns_lemonade",
    "ensure_lemonade_running",
    "hold_lemonade_open",
    "release_lemonade_hold",
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


#: Set by the Electron app and the npm launcher on the Agent UI backend they
#: spawn: the app's own pid, so its registration ends when the app does even if
#: the backend lingers.
APP_PID_ENV = "GAIA_APP_PID"

# This process's registration with the daemon, released at exit.
_HELD_CLIENT: Optional[str] = None
_HELD_LOCK = threading.Lock()

#: How often a held registration is repeated, so a daemon that restarted while
#: this process ran learns of it before its idle timeout can fire.
HOLD_RENEW_SECONDS = 30.0
_RENEW_STOP = threading.Event()


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


def ensure_daemon_owns_lemonade(kind: str = "cli", name: str = "") -> None:
    """Make sure a daemon exists, so the model server has an owner, and tell it
    this process is using the server.

    A front-end calls this ONCE at startup. It is the opt-in that lets a
    user-facing command bring up the machine's background service —
    :func:`ensure_lemonade_running` deliberately will not, because a readiness
    check that boots a daemon behind the caller's back surprises every library
    and test caller, and costs them up to a minute to do it.

    Cheap when a daemon is already running (one status probe, one
    registration). A failure is logged, not raised: the
    ``ensure_lemonade_running`` call that follows produces the actionable
    error, and raising here would report the same problem twice in different
    words.

    Args:
        kind: ``"app"`` for the Agent UI backend, ``"cli"`` for a command; see
            :mod:`gaia.daemon.lemonade_clients` for what each means.
        name: Shown in the daemon log.
    """
    from gaia.daemon import client as daemon_client
    from gaia.daemon.errors import DaemonError

    try:
        daemon_client.start_or_attach()
    except DaemonError as e:
        log.warning("Could not start the GAIA background service: %s", e)
        return
    try:
        hold_lemonade_open(kind, name)
    except DaemonError as e:
        log.warning(
            "The GAIA background service will not know when this %s closes, so "
            "it cannot free Lemonade's memory for it: %s",
            kind,
            e,
        )


def _client_pids(kind: str) -> List[int]:
    """This process, plus the app that spawned it for an Agent UI backend.

    The app pid is taken out of the environment so the processes this one
    spawns do not register as part of an app that is not theirs.
    """
    pids = [os.getpid()]
    raw = os.environ.pop(APP_PID_ENV, "").strip() if kind == "app" else ""
    if raw:
        try:
            pids.append(int(raw))
        except ValueError:
            log.warning("Ignoring %s=%r: it is not a process id", APP_PID_ENV, raw)
    return pids


def hold_lemonade_open(kind: str, name: str = "") -> None:
    """Register this process with the running daemon until it exits.

    One registration per process: a second call is a no-op. Released by
    :func:`release_lemonade_hold` at interpreter exit; a process killed outright
    is noticed by the daemon within seconds.

    Raises:
        DaemonError: The daemon is not running or refused the registration.
    """
    global _HELD_CLIENT
    from gaia.daemon import client as daemon_client

    with _HELD_LOCK:
        if _HELD_CLIENT is not None:
            return
        pids = _client_pids(kind)
        _HELD_CLIENT = daemon_client.register_lemonade_client(kind, pids, name or kind)
        client_id = _HELD_CLIENT
    atexit.register(release_lemonade_hold)
    _RENEW_STOP.clear()
    threading.Thread(
        target=_renew_hold,
        args=(client_id, kind, pids, name or kind),
        name="gaia-lemonade-hold",
        daemon=True,
    ).start()


def _renew_hold(client_id: str, kind: str, pids: List[int], name: str) -> None:
    """Repeat the registration until it is released.

    Never starts a daemon: with none running there is nothing to tell, and the
    next one hears from this loop once it is up. A failure is logged once per
    distinct reason, not every 30 seconds.
    """
    from gaia.daemon import client as daemon_client
    from gaia.daemon.errors import DaemonError

    last_error = None
    while not _RENEW_STOP.wait(HOLD_RENEW_SECONDS):
        with _HELD_LOCK:
            if _HELD_CLIENT != client_id:
                return
        try:
            if daemon_client.attach() is None:
                continue
            daemon_client.register_lemonade_client(kind, pids, name, client_id)
            last_error = None
        except DaemonError as e:
            if str(e) != last_error:
                log.warning("Could not renew this %s's hold on Lemonade: %s", kind, e)
            last_error = str(e)


def release_lemonade_hold() -> None:
    """Release this process's registration, if it holds one."""
    global _HELD_CLIENT
    from gaia.daemon import client as daemon_client
    from gaia.daemon.errors import DaemonError

    with _HELD_LOCK:
        client_id, _HELD_CLIENT = _HELD_CLIENT, None
    _RENEW_STOP.set()
    if client_id is None:
        return
    try:
        daemon_client.release_lemonade_client(client_id)
    except DaemonError as e:
        log.warning("%s; the daemon notices this process exit on its own.", e)


def _post_start(payload: dict, timeout: float) -> dict:
    """POST ``/daemon/v1/lemonade/start``, translating every failure loudly."""
    import requests

    from gaia.daemon import client as daemon_client
    from gaia.daemon.constants import API_PREFIX, AUTH_SCHEME

    # ATTACH, never start-or-attach. See the module docstring: bringing up a
    # machine-wide daemon is a front-end's decision, not something a readiness
    # check does behind the caller's back.
    inst = daemon_client.attach()
    if inst is None:
        raise LemonadeStartError(
            "The local model server is not running, and neither is the GAIA "
            "background service that owns it — so nothing can start it.\n"
            "To fix: run `gaia daemon start`, then retry.\n"
            "Where to look: `gaia daemon logs`"
        )

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
