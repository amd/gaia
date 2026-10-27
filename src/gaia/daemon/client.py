# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Client-side daemon helpers: start-or-attach, attach, shutdown request.

``start_or_attach()`` is the entry point every client (the web UI and the
``gaia <agent>`` CLIs) calls. It returns the running daemon, starting one only if
needed — and does so under an exclusive start lock so two concurrent callers yield
exactly ONE daemon (the second attaches to the first's instance.json).

Recovery follows §0.25: a recorded instance is trusted only when its pid is alive
AND a token-authed probe succeeds. A dead pid → reclaim by spawning fresh; a
live pid with nothing answering on its port → terminate it, then spawn fresh. A
live pid that accepts the probe but answers slowly is busy, not dead: it is
re-probed with a longer wait and never killed (:class:`DaemonBusyError`).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from gaia.daemon import paths
from gaia.daemon.constants import API_PREFIX, AUTH_SCHEME, DAEMON_API_VERSION
from gaia.daemon.custody.constants import CUSTODY_URL_ENV_VAR
from gaia.daemon.errors import (
    DaemonBusyError,
    DaemonError,
    DaemonHungError,
    DaemonStartError,
    DaemonVersionError,
    LemonadeNotAnsweringError,
)
from gaia.daemon.instance import (
    DaemonInstance,
    is_live,
    pid_alive,
    probe_state,
    read_instance,
    runs_replaced_install,
    terminate_instance,
)
from gaia.daemon.lock import StartLock
from gaia.env import is_internal_secret
from gaia.log_rotation import rotate_if_oversized
from gaia.logger import get_logger
from gaia.utils.terminal import background_popen_kwargs, popen_outliving_job

logger = get_logger(__name__)

# A first start without compiled bytecode takes tens of seconds under endpoint
# scanning. tests/unit/test_daemon_lemonade.py keeps the TUI's CheckTimeout above.
_START_TIMEOUT = 60.0
#: Silence for a normal start; after this the caller hears the daemon is coming up.
_PROGRESS_AFTER = 5.0
_PROGRESS_EVERY = 10.0
_LOG_TAIL_LINES = 5

#: The second, longer probe a daemon gets after one that connected but timed
#: out. Status never waits on other work, so a daemon still silent after this is
#: overloaded or stuck, and is reported busy rather than killed.
BUSY_PROBE_TIMEOUT_S = 10.0

#: A daemon whose status has gone unanswered this long, across probes, is hung,
#: not busy: the next start replaces it.
HUNG_AFTER_S = 120.0

#: Unanswered probes further apart than this belong to separate busy spells, so
#: a marker left from a spell that ended unobserved never counts toward hung.
BUSY_SPELL_GAP_S = HUNG_AFTER_S / 2

#: Where the start of a daemon's current unanswered spell is recorded.
BUSY_MARKER_NAME = "busy_since.json"

#: Called with the seconds waited so far while a daemon start is in progress.
ProgressCallback = Callable[[float], None]

# (module, distribution) the daemon process imports that a base install lacks.
# The [api] extra declares every one (tests/unit/test_api_extras.py).
DAEMON_DEPS = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("psutil", "psutil"),
)

# The daemon MINOR that introduced the /daemon/v1/agents control plane + the
# /v1/<agent>/* relay (#2142 / #2150). A pre-#2142 daemon passes the MAJOR gate
# but would 404 every agents/relay route — fail loudly instead. A stale daemon
# surviving an app auto-update is the EXPECTED path, not an edge case.
_REQUIRED_AGENTS_MINOR = 1
# The MINOR that added /daemon/v1/lemonade/ensure.
_REQUIRED_LEMONADE_MINOR = 2
# The MINOR that added /daemon/v1/lemonade/clients.
_REQUIRED_CLIENTS_MINOR = 4
# The MINOR that added /daemon/v1/lemonade/reconfigure.
_REQUIRED_RECONFIGURE_MINOR = 5
# Registration and release are loopback bookkeeping; an exiting app must not
# hang on them.
_CLIENTS_TIMEOUT = (2.0, 10.0)

# Ensure: connect fast; read generously — a first-run ensure may lazily fetch the
# sidecar binary before answering (mirrors gaia.ui.email_sidecar.daemon_client).
_ENSURE_TIMEOUT = (5.0, 900.0)
# A cold start's health wait (60s) plus a start already in flight. Keep
# gaiainit.CheckTimeout in the TUI above this plus daemon start (60s).
_LEMONADE_ENSURE_TIMEOUT = (5.0, 80.0)

# How long `gaia daemon stop` waits before tree-killing. Shutdown now stops
# GAIA's Lemonade Server, so this must outlast that teardown or a
# routine stop kills the daemon before its custody store closes.
# tests/unit/test_daemon_lemonade.py pins the margin.
STOP_WAIT_TIMEOUT = 45.0

# A daemon that served a Lemonade request this recently may be mid-turn.
OUTDATED_BUSY_RECENT_S = 120.0
# Outdated daemons already reported as kept running, so the notice shows once.
_DEFERRED_NOTED: set = set()

UPGRADE_CORE_GUIDANCE = (
    "Upgrade the installed GAIA core so it matches this app: "
    "`pip install --upgrade amd-gaia`, or re-run the installer from "
    "https://amd-gaia.ai. If you already upgraded, restart the daemon to pick "
    "it up; otherwise the version comes from the installed core, so "
    "`gaia daemon restart` brings the same one back."
)


def _major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except (ValueError, IndexError) as e:
        raise DaemonVersionError(
            f"cannot parse a MAJOR daemon API version from '{version}'"
        ) from e


def _check_version(inst: DaemonInstance) -> None:
    """Fail loudly on a daemon↔client MAJOR skew (§0.25) — no silent stale attach."""
    if _major(inst.api_version) != _major(DAEMON_API_VERSION):
        raise DaemonVersionError(
            f"the running daemon speaks host API v{inst.api_version} but this client "
            f"speaks v{DAEMON_API_VERSION}. An app update replaced the client while the "
            f"old daemon kept running. {UPGRADE_CORE_GUIDANCE}"
        )


def _patient_probe(
    inst: DaemonInstance,
    timeout: float,
    on_slow: Optional[Callable[[], None]] = None,
) -> Tuple[str, Optional[dict]]:
    """:func:`probe_state`, giving a busy daemon one longer second chance.

    *on_slow* is called before that second, longer wait.
    """
    state, body = probe_state(inst, timeout=timeout)
    if state == "busy":
        if on_slow is not None:
            on_slow()
        state, body = probe_state(inst, timeout=max(timeout, BUSY_PROBE_TIMEOUT_S))
    return state, body


def _is_gaia_daemon(pid: int) -> bool:
    """True unless *pid* is visibly some other program (a reused pid)."""
    import psutil

    try:
        return "gaia.daemon" in " ".join(psutil.Process(pid).cmdline())
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True  # cannot tell; the hung limit still bounds it


def _busy_marker(host_dir: Optional[Path]) -> Path:
    return (host_dir or paths.host_dir()) / BUSY_MARKER_NAME


def _busy_since(inst: DaemonInstance, host_dir: Optional[Path]) -> float:
    """When this daemon's current unanswered spell began; starts one if new.

    Records this unanswered probe too, so a later one more than
    :data:`BUSY_SPELL_GAP_S` after it starts a new spell.
    """
    import json

    marker = _busy_marker(host_dir)
    now = time.time()
    since = now
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if int(data["pid"]) == inst.pid and now - float(data["last"]) <= (
            BUSY_SPELL_GAP_S
        ):
            since = float(data["since"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("daemon: ignoring unreadable %s (%s); starting over", marker, e)
    paths.atomic_write_json(marker, {"pid": inst.pid, "since": since, "last": now})
    return since


def _clear_busy(host_dir: Optional[Path]) -> None:
    try:
        _busy_marker(host_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("daemon: could not remove %s: %s", _busy_marker(host_dir), e)


def _answers(
    inst: DaemonInstance,
    timeout: float,
    *,
    patient: bool = True,
    host_dir: Optional[Path] = None,
) -> bool:
    """True if *inst* is live, False if it is gone.

    A daemon that accepts the probe but does not answer gets a longer second
    probe when *patient* (an exiting process skips it). A pid that is visibly
    not a GAIA daemon is gone, whatever its port does.

    Raises:
        DaemonHungError: it has not answered for :data:`HUNG_AFTER_S`.
        DaemonBusyError: it accepted the probe but has not answered, for less
            than that.
    """
    if not pid_alive(inst.pid):
        return False
    state, _ = probe_state(inst, timeout=timeout)
    if state == "busy" and not _is_gaia_daemon(inst.pid):
        state = "gone"
    if state == "busy":
        since = _busy_since(inst, host_dir)
        if time.time() - since < HUNG_AFTER_S and patient:
            state, _ = probe_state(inst, timeout=max(timeout, BUSY_PROBE_TIMEOUT_S))
            if state == "busy":
                since = _busy_since(inst, host_dir)
    if state != "busy":
        _clear_busy(host_dir)
        return state == "live"
    silent_for = time.time() - since
    where = f"Where to look: `gaia daemon logs` ({paths.log_path()})."
    if silent_for >= HUNG_AFTER_S:
        raise DaemonHungError(
            f"the GAIA daemon (pid {inst.pid}, port {inst.port}) has not answered "
            f"its status check for {silent_for:.0f}s, so it is hung. Run `gaia "
            f"daemon restart`; the next `gaia daemon start` also replaces it. " + where
        )
    raise DaemonBusyError(
        f"the GAIA daemon (pid {inst.pid}, port {inst.port}) is running but has "
        f"not answered its status check for {silent_for:.0f}s, so it is "
        f"overloaded or stuck. It was left running; after {HUNG_AFTER_S:.0f}s "
        "it is treated as hung and replaced. Retry in a moment, or run `gaia "
        "daemon restart` to restart it and its sidecar agents now. " + where
    )


def attach(
    timeout: float = 1.5, host_dir: Optional[Path] = None, *, patient: bool = True
) -> Optional[DaemonInstance]:
    """Return the running daemon if one is live and version-compatible, else None.

    Args:
        host_dir: Attach to the daemon of another GAIA home, whose
            ``instance.json`` is in this directory, instead of this process's.
        patient: give a slow daemon a longer second probe. Exit paths pass
            False rather than hold up the process.

    Raises:
        DaemonBusyError: a daemon is running but not answering. ``None`` would
            tell the caller there is no daemon, and it would act as if alone.
    """
    inst = read_instance(host_dir / "instance.json") if host_dir else read_instance()
    if inst is None or not _answers(inst, timeout, patient=patient, host_dir=host_dir):
        return None
    _check_version(inst)
    return inst


def start_or_attach(
    timeout: float = _START_TIMEOUT,
    on_progress: Optional[ProgressCallback] = None,
    drop_env: tuple = (),
) -> DaemonInstance:
    """Return the running daemon, starting one if needed. Single-instance guaranteed.

    *on_progress* is called every few seconds while a start is still under way,
    so a front-end can show that something is happening. *drop_env* names
    variables of this process a newly started daemon must not inherit.
    """
    try:
        inst = attach()
    except DaemonHungError:
        inst = None  # replaced below, under the lock
    if inst is not None and runs_replaced_install(inst) is None:
        return inst

    with StartLock(timeout=timeout):
        # Re-check under the lock: a concurrent caller may have just started it.
        inst = read_instance()
        try:
            if inst is not None and _answers(inst, 1.5):
                _check_version(inst)
                stale = runs_replaced_install(inst)
                if stale is None:
                    return inst
                busy = outdated_daemon_busy(inst)
                if busy is not None:
                    if inst.pid in _DEFERRED_NOTED:
                        return inst
                    _DEFERRED_NOTED.add(inst.pid)
                    logger.warning(
                        "daemon: pid %s is out of date (%s), but %s, so it keeps "
                        "running for now. It restarts on a later GAIA command "
                        "once it is free, or run `gaia daemon restart`.",
                        inst.pid,
                        stale,
                        busy,
                    )
                    return inst
                _replace_outdated(inst, stale)
                inst = None
        except DaemonHungError as e:
            logger.warning("daemon: %s Replacing it.", e)
        # Stale registry. A live pid whose port refuses us, answers as
        # something else, or stayed silent past HUNG_AFTER_S is our own daemon
        # gone bad — terminate it before reclaiming (§0.25). A merely busy one
        # raised above and is never killed.
        if inst is not None and pid_alive(inst.pid):
            logger.warning(
                "daemon: instance pid=%s is alive but not answering as this "
                "daemon; reclaiming",
                inst.pid,
            )
            terminate_instance(inst)
            _clear_busy(None)
        missing = missing_daemon_deps()
        if missing:
            raise DaemonStartError(
                f"the GAIA daemon needs {', '.join(missing)}, which this Python "
                f"({sys.executable}) does not have. Install the daemon extras "
                'into that environment with pip install "amd-gaia[api]" '
                "(or uv pip install), then retry."
            )
        return _spawn_and_wait(timeout, on_progress, drop_env)


def _scheduled_work() -> Optional[str]:
    """Scheduled jobs the daemon is running or is due to start, or None.

    Read straight from its job store: older daemons report only a pending
    count, and a recurring schedule is always pending.
    """
    import sqlite3
    from contextlib import closing

    from gaia.daemon.scheduler.clock import DEFAULT_STALE_FIRING_SECONDS

    path = paths.host_dir() / "scheduler.db"
    if not path.exists():
        return None
    now = time.time()
    try:
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2.0)
        ) as db:
            running, due = db.execute(
                "SELECT SUM(status = 'firing' AND claimed_at > ?), "
                "SUM(status = 'pending' AND fire_at <= ?) FROM daemon_jobs",
                (now - DEFAULT_STALE_FIRING_SECONDS, now),
            ).fetchone()
    except sqlite3.Error as e:
        return f"GAIA could not read its scheduled jobs at {path} ({e})"
    if running:
        return f"{running} scheduled job(s) are running on it"
    if due:
        return f"{due} scheduled job(s) are due to run on it"
    return None


def outdated_daemon_busy(inst: DaemonInstance) -> Optional[str]:
    """Why stopping *inst* now could cut off work, or None when it is free.

    Uses what older daemons expose too: their job store, the front ends
    registered with them, and when Lemonade last served a request.
    """
    import requests

    jobs = _scheduled_work()
    if jobs is not None:
        return jobs
    try:
        clients = requests.get(
            f"{inst.base_url}{API_PREFIX}/lemonade/clients",
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=5.0,
        )
    except requests.exceptions.RequestException as e:
        return f"GAIA could not check whether it is busy ({e})"
    if clients.status_code == 404:
        return None
    if clients.status_code != 200:
        return f"GAIA could not check whether it is busy ({_error_detail(clients)})"
    snapshot = clients.json()
    ours = {os.getpid(), os.getppid()}
    users = [
        c.get("name") or c.get("kind") or "a GAIA app"
        for c in snapshot.get("clients") or []
        if any(pid_alive(p) for p in c.get("pids") or [] if p not in ours)
    ]
    if users:
        return f"{', '.join(users)} is still using it"
    ago = snapshot.get("seconds_since_request")
    if ago is not None and ago < OUTDATED_BUSY_RECENT_S:
        return f"its model server answered a request {ago:.0f}s ago"
    return None


def _replace_outdated(inst: DaemonInstance, why: str) -> None:
    """Stop a daemon an update left running on the previous release's code."""
    logger.warning(
        "daemon: pid %s is out of date (%s); restarting it and its agents",
        inst.pid,
        why,
    )
    try:
        accepted = request_shutdown(inst)
    except DaemonError as e:
        raise DaemonError(
            f"the GAIA daemon (pid {inst.pid}) is out of date ({why}) and could "
            f"not be asked to stop: {e} Run `gaia daemon restart`."
        ) from e
    if not accepted or not wait_until_gone(inst, timeout=STOP_WAIT_TIMEOUT):
        raise DaemonError(
            f"the GAIA daemon (pid {inst.pid}) is out of date ({why}) and did not "
            "stop when asked. Run `gaia daemon restart`. Where to look: `gaia "
            f"daemon logs` ({paths.log_path()})."
        )


def missing_daemon_deps() -> list:
    """Distributions the daemon needs that this interpreter cannot import."""
    import importlib.util

    return [pkg for mod, pkg in DAEMON_DEPS if importlib.util.find_spec(mod) is None]


def _daemon_env(drop: tuple = ()) -> dict:
    """The parent's environment minus anything pointing at another daemon.

    The daemon mints its own credentials and serves its own custody endpoint, so
    a stale ``GAIA_HOST_CUSTODY_URL`` would reach its sidecars without a secret.
    ``GAIA_CHILD_ENV_DENY`` is not applied: the daemon is GAIA itself.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if not is_internal_secret(name)
        and name != CUSTODY_URL_ENV_VAR
        and name not in drop
    }


def _is_spawned_daemon(registered_pid: int, spawned_pid: int) -> bool:
    """True when the registered pid is the process we spawned, or its child.

    A Windows venv's ``python.exe`` is a launcher that runs the base interpreter
    as a child, so the daemon registers that child's pid, never the launcher's.
    """
    if registered_pid == spawned_pid:
        return True
    import psutil

    try:
        return psutil.Process(registered_pid).ppid() == spawned_pid
    except psutil.Error:
        return False


def _log_tail(lines: int = _LOG_TAIL_LINES) -> str:
    """The last *lines* of the daemon log, or why they could not be read."""
    try:
        text = paths.log_path().read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"(could not read {paths.log_path()}: {e})"
    tail = [line for line in text.splitlines() if line.strip()][-lines:]
    return "\n".join(tail) if tail else "(the daemon log is empty)"


def _spawn_and_wait(
    timeout: float,
    on_progress: Optional[ProgressCallback] = None,
    drop_env: tuple = (),
) -> DaemonInstance:
    """Spawn the daemon detached, wait until it registers a live instance.json."""
    paths.ensure_host_dir()
    # The daemon's stdout can't be rotated while it runs, so cap it per start.
    try:
        rotate_if_oversized(paths.log_path())
    except OSError as e:
        logger.warning(
            "Could not rotate %s (%s); appending to it.", paths.log_path(), e
        )
    log_file = open(paths.log_path(), "ab")
    # 0600: the daemon log lives beside token-minting code (D-5, #2142) and
    # would otherwise land world-readable under umask 022.
    try:
        os.chmod(paths.log_path(), 0o600)
    except OSError:
        pass
    # Own group/session to outlive the launcher. A hidden console, not none, so
    # the console programs the daemon starts don't each open a blank window.
    spawn_kwargs = background_popen_kwargs()
    try:
        proc = popen_outliving_job(
            [sys.executable, "-m", "gaia.daemon"],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_daemon_env(drop_env),
            **spawn_kwargs,
        )
    except OSError as e:
        log_file.close()
        raise DaemonStartError(
            f"failed to launch the daemon ({sys.executable} -m gaia.daemon): {e}. "
            f"Check the Python environment and `gaia daemon logs` at {paths.log_path()}."
        ) from e
    finally:
        # The child inherits the fd; the parent no longer needs it.
        log_file.close()

    started = time.monotonic()
    deadline = started + timeout
    next_progress = started + _PROGRESS_AFTER
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_progress:
            next_progress = now + _PROGRESS_EVERY
            elapsed = now - started
            logger.info("daemon: still starting after %.0fs", elapsed)
            if on_progress is not None:
                on_progress(elapsed)
        rc = proc.poll()
        if rc is not None:
            raise DaemonStartError(
                f"the daemon process exited early (code {rc}) before registering. "
                f"See the daemon log at {paths.log_path()}."
            )
        inst = read_instance()
        # Trust only OUR child's registration (pid match), and only once live.
        if (
            inst is not None
            and _is_spawned_daemon(inst.pid, proc.pid)
            and is_live(inst)
        ):
            _check_version(inst)
            logger.info("daemon: started pid=%s port=%s", inst.pid, inst.port)
            return inst
        time.sleep(0.1)

    state = (
        f"It is still running (pid {proc.pid}) and may finish on its own; run "
        "the command again in a minute"
        if proc.poll() is None
        else f"It exited with code {proc.returncode}"
    )
    raise DaemonStartError(
        f"the daemon did not become healthy within {timeout:.0f}s. {state}. If it "
        f"keeps happening, `gaia daemon logs` shows why; the full log is "
        f"{paths.log_path()}. Last lines:\n{_log_tail()}"
    )


def _minor(inst: DaemonInstance) -> int:
    parts = str(inst.api_version).split(".")
    try:
        return int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0


def _check_agents_floor(inst: DaemonInstance) -> None:
    """Fail loudly if the running daemon predates the agents/relay control plane."""
    if _minor(inst) < _REQUIRED_AGENTS_MINOR:
        raise DaemonVersionError(
            f"the running daemon (host API v{inst.api_version}) predates the "
            "sidecar control plane + relay (needs v1.1+) — it would 404 every "
            f"agents route. {UPGRADE_CORE_GUIDANCE}"
        )


def _error_detail(response) -> str:
    """The actionable ``detail`` from a daemon error response (or raw status)."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return f"HTTP {response.status_code}"


def ensure_agent(
    agent_id: str,
    *,
    mode: Optional[str] = None,
    timeout=_ENSURE_TIMEOUT,
) -> DaemonInstance:
    """Start-or-attach the daemon and ensure *agent_id*'s sidecar is running.

    Returns the :class:`DaemonInstance` — ``base_url`` + the DAEMON client token —
    which a thin client drives the sidecar through, exclusively via the daemon
    relay (``ANY /v1/<agent>/*``). The sidecar's own port/bearer in the ensure
    response are deliberately DISCARDED here so a thin client never holds sidecar
    credentials (design §0.0 "one contract, many front-doors"): it presents only
    the daemon client token, and the daemon swaps it for the sidecar bearer
    server-side.

    Blocking (daemon start + sidecar spawn + possible first-run binary fetch) —
    call it off any event loop. Every failure raises :class:`DaemonError` with an
    actionable remedy; there is no silent in-process fallback.
    """
    import requests

    inst = start_or_attach()
    _check_agents_floor(inst)
    url = f"{inst.base_url}{API_PREFIX}/agents/{agent_id}/ensure"
    payload = {} if mode is None else {"mode": mode}
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to ensure the "
            f"'{agent_id}' sidecar: {e}. Check `gaia daemon status` and the "
            f"daemon log at {paths.log_path()}."
        ) from e
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon refused to ensure the '{agent_id}' sidecar: "
            f"{_error_detail(r)}"
        )
    # Deliberately DO NOT read/return the response body — it carries the sidecar
    # bearer token, which a thin client must never learn or hold.
    return inst


def stop_running_agent(agent_id: str, timeout: float = 30.0) -> bool:
    """Stop *agent_id*'s sidecar if a running daemon has it up; True when it did.

    For an install that replaced the agent's binary outside the daemon: the
    running sidecar keeps executing the old one, and the next ensure starts
    the new one. Never starts a daemon just to stop nothing.
    """
    import requests

    inst = attach()
    if inst is None:
        return False
    headers = {"Authorization": f"{AUTH_SCHEME} {inst.token}"}
    try:
        r = requests.get(
            f"{inst.base_url}{API_PREFIX}/agents", headers=headers, timeout=timeout
        )
        if r.status_code != 200:
            raise DaemonError(
                f"the daemon at {inst.base_url} could not list its agents: "
                f"{_error_detail(r)}"
            )
        running = any(
            a.get("agent_id") == agent_id and a.get("state") == "running"
            for a in r.json().get("agents", [])
        )
        if not running:
            return False
        r = requests.post(
            f"{inst.base_url}{API_PREFIX}/agents/{agent_id}/stop",
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to restart the "
            f"'{agent_id}' agent on its new version: {e}. Run `gaia daemon "
            f"stop-agent {agent_id}`; the next chat starts the new version."
        ) from e
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon could not stop the '{agent_id}' agent, so it is still "
            f"running its previous version: {_error_detail(r)}. Run `gaia daemon "
            f"stop-agent {agent_id}`; the next chat starts the new version."
        )
    return True


def ensure_lemonade(
    timeout=_LEMONADE_ENSURE_TIMEOUT, host_dir: Optional[Path] = None
) -> dict:
    """Start-or-attach the daemon and have it start GAIA's embedded Lemonade.

    Returns the daemon's answer: ``base_url``, ``port``, ``version`` and
    whether this call ``started`` the server. Blocking -- a cold start waits for
    the server's health check -- so call it off any event loop.

    Args:
        host_dir: Ask another GAIA home's daemon (see :func:`attach`), which
            must already be running: this never starts another home's daemon.

    Raises:
        LemonadeNotAnsweringError: GAIA's server is up but not answering yet;
            the daemon left it alone, so asking again later may find it back.
        DaemonError: The daemon is unreachable, has nothing to start (another
            server is configured, or GAIA's own is not installed), or the
            server would not start. The message names the remedy.
    """
    import requests

    if host_dir is None:
        inst = start_or_attach()
    else:
        inst = attach(host_dir=host_dir)
        if inst is None:
            raise DaemonError(
                f"the GAIA daemon in {host_dir}, which owns the Lemonade Server "
                "this process uses, is not running. Start it with `gaia daemon "
                "start` (without GAIA_HOME set)."
            )
    if _minor(inst) < _REQUIRED_LEMONADE_MINOR:
        raise DaemonVersionError(
            f"the running daemon (host API v{inst.api_version}) predates GAIA "
            "starting its own Lemonade Server. Restart it with `gaia daemon "
            "restart` so the current version takes over."
        )
    url = f"{inst.base_url}{API_PREFIX}/lemonade/ensure"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to start Lemonade "
            f"Server: {e}. Check `gaia daemon status` and the daemon log at "
            f"{paths.log_path()}."
        ) from e
    if r.status_code == 503 and r.headers.get("Retry-After"):
        raise LemonadeNotAnsweringError(_error_detail(r))
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon could not start Lemonade Server: {_error_detail(r)}"
        )
    return r.json()


def _post_reconfigure(inst: DaemonInstance, timeout) -> dict:
    import requests

    url = f"{inst.base_url}{API_PREFIX}/lemonade/reconfigure"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to switch Lemonade "
            f"Server: {e}. Run `gaia daemon restart` so it picks up the change."
        ) from e
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon could not switch Lemonade Server: {_error_detail(r)}"
        )
    return r.json()


def reconfigure_lemonade(timeout=_LEMONADE_ENSURE_TIMEOUT) -> Optional[dict]:
    """Make a running daemon follow the saved Lemonade choice.

    The daemon reads the choice live, so normally it only has to stop (or
    start) GAIA's own server. It is restarted instead when it predates the
    reconfigure route, or when it still resolves another server -- it was
    spawned with a ``LEMONADE_BASE_URL`` -- so it never quietly stays on the old
    one. The daemon is machine-wide, so it follows the saved setting; the
    restarted one does not inherit this shell's ``LEMONADE_BASE_URL``.

    Returns:
        The daemon's effective choice plus ``action`` and ``restarted``, or
        None when no daemon is running (the next one reads the setting).

    Raises:
        DaemonError: The daemon could not apply the setting or restart.
    """
    from gaia.llm.lemonade_choice import saved_target

    inst = attach()
    if inst is None:
        return None
    wanted = saved_target()

    def _matches(applied: dict) -> bool:
        return (applied.get("mode"), applied.get("base_url")) == (
            wanted["mode"],
            wanted["base_url"],
        )

    if _minor(inst) >= _REQUIRED_RECONFIGURE_MINOR:
        applied = _post_reconfigure(inst, timeout)
        if _matches(applied):
            return {**applied, "restarted": False}
        logger.info(
            "daemon: it uses Lemonade at %s (%s), the setting says %s; restarting it",
            applied.get("base_url"),
            applied.get("source"),
            wanted["base_url"],
        )
    if not request_shutdown(inst):
        raise DaemonError(
            "the daemon refused to restart for the new Lemonade setting. Run "
            "`gaia daemon restart`."
        )
    if not wait_until_gone(inst, timeout=STOP_WAIT_TIMEOUT):
        raise DaemonError(
            f"the daemon (pid {inst.pid}) did not exit to pick up the new Lemonade "
            "setting. Run `gaia daemon restart`."
        )
    fresh = start_or_attach(drop_env=("LEMONADE_BASE_URL", "LEMONADE_API_KEY"))
    applied = _post_reconfigure(fresh, timeout)
    if not _matches(applied):
        raise DaemonError(
            f"the restarted daemon still uses the Lemonade at {applied.get('base_url')}, "
            f"not the one GAIA is set to use. Check `gaia daemon logs`."
        )
    return {**applied, "restarted": True}


def register_lemonade_client(
    kind: str,
    pids: list,
    name: str = "",
    client_id: Optional[str] = None,
    host_dir: Optional[Path] = None,
) -> str:
    """Tell the running daemon this process is using GAIA's Lemonade.

    The registration lasts until :func:`release_lemonade_client`, or until any
    of *pids* exits. While one is held the daemon keeps the server's models
    loaded and its idle timeout paused.

    Args:
        kind: ``"app"`` (Agent UI, TUI) or ``"cli"`` (a ``gaia`` command).
        pids: Processes the registration lives as long as.
        name: Shown in the daemon log.
        client_id: Repeat an earlier registration, so a daemon that restarted
            since learns of this client too. A no-op on the daemon that has it.
        host_dir: Register with another GAIA home's daemon (see :func:`attach`).

    Returns:
        The client id to release with.

    Raises:
        DaemonError: No daemon is running, it predates client registration, or
            it refused the registration.
    """
    import requests

    inst = attach(host_dir=host_dir, patient=False)
    if inst is None:
        raise DaemonError(
            "the GAIA daemon is not running, so there is nothing to register "
            "with. Start it with `gaia daemon start`."
        )
    if _minor(inst) < _REQUIRED_CLIENTS_MINOR:
        raise DaemonVersionError(
            f"the running daemon (host API v{inst.api_version}) cannot tell when "
            "GAIA apps close, so it will not free Lemonade's memory for them. "
            "Restart it with `gaia daemon restart` so the current version takes "
            "over."
        )
    url = f"{inst.base_url}{API_PREFIX}/lemonade/clients"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            json={
                "kind": kind,
                "pids": list(pids),
                "name": name,
                "client_id": client_id,
            },
            timeout=_CLIENTS_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to register this "
            f"{kind}: {e}. Check `gaia daemon status`."
        ) from e
    if r.status_code != 200:
        raise DaemonError(
            f"the daemon refused to register this {kind}: {_error_detail(r)}"
        )
    return r.json()["client_id"]


def release_lemonade_client(
    client_id: str, host_dir: Optional[Path] = None
) -> Optional[dict]:
    """Release a registration from :func:`register_lemonade_client`.

    Returns:
        The daemon's answer (``clients`` left, whether it ``unloaded``), or None
        when there was nothing to release: the daemon has exited, or already
        dropped the registration.

    Raises:
        DaemonError: The daemon could not be reached or refused the release.
    """
    import requests

    inst = attach(host_dir=host_dir, patient=False)
    if inst is None:
        return None
    url = f"{inst.base_url}{API_PREFIX}/lemonade/clients/{client_id}"
    try:
        r = requests.delete(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=_CLIENTS_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to release client "
            f"{client_id}: {e}. It drops the registration once this process "
            "exits."
        ) from e
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise DaemonError(f"the daemon refused the release: {_error_detail(r)}")
    return r.json()


def request_shutdown(inst: DaemonInstance, timeout: float = 5.0) -> bool:
    """Ask the daemon to shut down gracefully via its authed shutdown route.

    Returns True if the daemon accepted the request. Raises DaemonError on a
    transport failure so the caller can decide whether to escalate to a kill.
    """
    import requests

    url = f"{inst.base_url}{API_PREFIX}/shutdown"
    try:
        r = requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the daemon at {inst.base_url} to request shutdown: {e}. "
            "It may already be dead; `gaia daemon status` will confirm."
        ) from e
    return r.status_code == 200


def wait_until_gone(inst: DaemonInstance, timeout: float = 10.0) -> bool:
    """Poll until the daemon pid is no longer alive. Returns True if it exited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(inst.pid):
            return True
        time.sleep(0.1)
    return not pid_alive(inst.pid)


def status_report(on_slow: Optional[Callable[[], None]] = None) -> dict:
    """Structured status for the CLI: running/busy/stale/absent + identity + uptime.

    *on_slow* is called if the daemon is slow to answer, before a longer wait.
    """
    inst = read_instance()
    if inst is None:
        return {"state": "not_running"}
    if not pid_alive(inst.pid):
        return {"state": "stale", "reason": "pid_dead", "instance": inst}
    state, body = _patient_probe(inst, 1.5, on_slow)
    if state == "busy":
        return {"state": "busy", "instance": inst}
    if body is None:
        return {"state": "stale", "reason": "unresponsive", "instance": inst}
    return {"state": "running", "instance": inst, "status": body}
