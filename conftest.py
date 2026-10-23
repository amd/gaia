# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Root conftest — make the test run exercise THIS checkout.

``gaia`` and the hub agent packages are normally editable-installed. On a
machine with more than one clone or git worktree, that install points at
whichever one ran ``pip install -e`` last — so ``import gaia`` inside another
checkout's tests silently imports someone else's source. The tests pass or fail
against code that is not the code under review, which is worse than either
outcome on its own.

This file runs before collection and puts this repository's own source
directories at the front of ``sys.path``, so the checkout you are sitting in is
the one that gets tested. It also fails the session loudly if a core module
still resolves elsewhere, rather than letting a green run mean nothing.

It also points GAIA's config file, daemon home, TUI home, memory database, file
index and scratchpad at a per-session tmp dir, so no result depends on the
developer's real ~/.gaia, and fails any test that opens a database inside it.

Set ``GAIA_ALLOW_EXTERNAL_IMPORTS=1`` to opt out — testing an actually-installed
wheel is a legitimate thing to do, it just must not happen by accident.
"""

from __future__ import annotations

import atexit
import functools
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent


def _source_roots():
    """Directories to shadow with, newest-wins order handled by the caller.

    For a hub agent the importable root is the directory CONTAINING the package
    (``hub/agents/gaia/python``), not the package itself — putting the package
    dir on sys.path makes ``import gaia_agent`` miss it entirely.

    A hub agent is included ONLY when its package already resolves somewhere.
    This redirects an existing install to this checkout; it must not *create*
    one. Tests for an agent nobody installed guard themselves with
    ``importorskip``, and forcing those onto the path turns a deliberate skip
    into a wall of failures for dependencies the developer never asked for.
    """
    roots = [REPO_ROOT / "src"]
    for init in sorted(REPO_ROOT.glob("hub/agents/*/python/*/__init__.py")):
        package = init.parent.name
        if importlib.util.find_spec(package) is not None:
            roots.append(init.parent.parent)
    return roots


_SOURCE_ROOTS = _source_roots()

#: Modules whose origin proves which checkout is under test. The hub agents are
#: listed too: they are separately editable-installed, so ``gaia`` can resolve
#: here while ``gaia_agent`` quietly comes from another worktree.
_ANCHOR_MODULES = ("gaia", "gaia_agent", "gaia_agent_chat")


def _prepend_source_roots() -> None:
    present = [str(root) for root in _SOURCE_ROOTS if root.is_dir()]
    for entry in reversed(present):
        if entry in sys.path:
            sys.path.remove(entry)
        sys.path.insert(0, entry)

    # Tests that shell out to `python -c "import ..."` get a fresh interpreter
    # that inherits none of the above, so the child would resolve to whatever
    # the editable install points at — or find nothing at all.
    inherited = os.environ.get("PYTHONPATH", "")
    tail = [p for p in inherited.split(os.pathsep) if p and p not in present]
    os.environ["PYTHONPATH"] = os.pathsep.join(present + tail)


def _assert_local(session_warn) -> None:
    """Refuse to report results for a different checkout's source."""
    if os.environ.get("GAIA_ALLOW_EXTERNAL_IMPORTS") == "1":
        return

    strays = []
    for name in _ANCHOR_MODULES:
        try:
            module = __import__(name)
        except ImportError:
            continue
        origin = Path(getattr(module, "__file__", "") or "").resolve()
        if origin and REPO_ROOT not in origin.parents:
            strays.append(f"{name} -> {origin}")

    if strays:
        session_warn(
            "These modules resolved OUTSIDE this checkout, so the tests would "
            "not be testing this branch:\n  "
            + "\n  ".join(strays)
            + f"\n\nThis checkout: {REPO_ROOT}\n"
            "Usually an editable install pointing at another clone or git "
            "worktree. Re-run `uv pip install -e .` from here, or set "
            "GAIA_ALLOW_EXTERNAL_IMPORTS=1 if you really mean to test the "
            "installed package."
        )


#: State a test must never read from the developer's real ~/.gaia, each pointed
#: at a per-session tmp dir unless the caller already set it. config.json's
#: ``default_model`` made results depend on the machine.
#:
#: GAIA_HOME (and the GAIA_CONFIG_DIR env var) are deliberately not set: they
#: outrank both ways tests already move GAIA's tree (setting GAIA_CONFIG_DIR,
#: patching ``Path.home``), so a session value would silently override them.
_STATE_ENV = {
    "GAIA_CONFIG_FILE": ("config", "config.json"),
    "GAIA_DAEMON_HOME": ("daemon-home",),
    "GAIA_MEMORY_DB": ("memory", "memory.db"),
    # Cloud calls record model availability here.
    "GAIA_GATEWAY_FILE": ("gateway", "gateway.json"),
    # The stdio agent writes each session's chat history under it.
    "GAIA_TUI_HOME": ("tui",),
}
#: The _STATE_ENV entries that name a directory rather than a file.
_STATE_DIRS = {"GAIA_DAEMON_HOME", "GAIA_TUI_HOME"}
_STATE_ROOT = Path(tempfile.mkdtemp(prefix="gaia-test-state-"))
atexit.register(shutil.rmtree, _STATE_ROOT, ignore_errors=True)
#: Names this conftest set, so a pytest-xdist worker can tell them from the
#: caller's own: it inherits the controller's environment.
_STATE_SET_BY_CONFTEST = "_GAIA_TEST_STATE_SET_BY_CONFTEST"


def _isolate_gaia_state() -> None:
    # Module level, not a fixture: gaia.config reads GAIA_CONFIG_FILE at import,
    # which can happen before any fixture runs.
    inherited = set()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        # Otherwise every worker shares the controller's config and memory DB.
        inherited = set(os.environ.get(_STATE_SET_BY_CONFTEST, "").split(","))
    ours = []
    for name, parts in _STATE_ENV.items():
        if os.environ.get(name) and name not in inherited:
            continue
        path = _STATE_ROOT.joinpath(*parts)
        (path if name in _STATE_DIRS else path.parent).mkdir(
            parents=True, exist_ok=True
        )
        os.environ[name] = str(path)
        ours.append(name)
    os.environ[_STATE_SET_BY_CONFTEST] = ",".join(ours)


def _isolate_config_dir() -> None:
    """Point ``gaia.config.GAIA_CONFIG_DIR`` (sessions, SD images) at the tmp dir."""
    if os.environ.get("GAIA_CONFIG_DIR"):
        return
    from gaia import config

    config.GAIA_CONFIG_DIR = Path(os.environ["GAIA_CONFIG_FILE"]).parent


def _keep_ssl_unpatched() -> None:
    """Tests that call an entry point must not swap ``ssl.SSLContext`` for the
    rest of the session; tests/unit/test_tls_trust_store.py covers injection."""
    if importlib.util.find_spec("gaia.tls") is None:  # an older installed wheel
        return
    from gaia import tls

    tls._injected = True


#: Ties every daemon a test starts, directly or through a ``gaia`` subprocess,
#: to this pytest process: it exits once this process is gone, even when the
#: run is killed before any teardown runs. Mirrors gaia.daemon.lifetime.
_EXIT_WITH_PID_ENV = "GAIA_DAEMON_EXIT_WITH_PID"

#: The developer's real GAIA home, read before any test patches ``Path.home``.
_REAL_GAIA_HOME = Path.home() / ".gaia"

_REAL_HOME_MESSAGE = (
    "This test would {action} the GAIA in your real home, {home}: {harm} "
    "Point it at a tmp folder instead: set GAIA_HOME (a daemon also needs "
    "GAIA_DAEMON_HOME) with monkeypatch.setenv, or use the mock_home fixture. "
    "{hint}"
)
_LEMONADE_HARM = (
    "it would start, stop or unload the Lemonade Server your own GAIA is using."
)
_LEMONADE_HINT = (
    "A test that only needs a model server tries to start one when yours is "
    "stopped: start it first with `gaia daemon start`."
)
_DB_HARM = "it would read or write your own chats, file index or scratchpad."
_DB_HINT = "A database a test opens itself belongs under tmp_path."


def _is_real_gaia_home(path: Path) -> bool:
    real = os.path.normcase(str(_REAL_GAIA_HOME.resolve()))
    candidate = os.path.normcase(str(Path(path).resolve()))
    return candidate == real or candidate.startswith(real + os.sep)


def _child_gaia_home(env) -> Path:
    """The GAIA home a process started with *env* resolves (see gaia_home())."""
    override = env.get("GAIA_HOME", "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    user_home = env.get("USERPROFILE" if os.name == "nt" else "HOME")
    return (Path(user_home) if user_home else _REAL_GAIA_HOME.parent) / ".gaia"


_prepend_source_roots()
_isolate_gaia_state()
os.environ[_EXIT_WITH_PID_ENV] = str(os.getpid())


def pytest_sessionstart(session):  # noqa: D103 — pytest hook
    def _fail(message: str) -> None:
        raise RuntimeError(message)

    _assert_local(_fail)
    _isolate_config_dir()
    _keep_ssl_unpatched()


def _restore_tool_registry():
    """Undo every ``@tool`` registration made while the caller was active.

    ``_TOOL_REGISTRY`` is process-global and agents snapshot all of it, so a
    tool one test registers shows up in every agent built after it.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


# Module scope also catches class- and module-scoped fixtures that build agents,
# which set up before (and outside) the per-test restore.
@pytest.fixture(scope="module", autouse=True)
def _isolate_tool_registry_per_module():
    yield from _restore_tool_registry()


@pytest.fixture(autouse=True)
def _isolate_tool_registry_per_test():
    yield from _restore_tool_registry()


def pytest_configure(config):  # noqa: D103 — pytest hook
    config.addinivalue_line(
        "markers",
        "embedded_start: let LemonadeManager.start_embedded_if_stopped run "
        "(its own dependencies must be mocked)",
    )


_REAL_START_EMBEDDED = {}


@pytest.fixture(scope="session", autouse=True)
def _no_embedded_lemonade_start():
    """Keep ensure_ready() from starting a daemon for GAIA's own Lemonade.

    Building an agent runs ensure_ready(), and on a machine where ``gaia init``
    installed GAIA's server it would spawn a daemon that owns the real
    ~/.gaia server: the source of the daemons test runs left behind (#5293).
    Session scope also covers class- and module-scoped fixtures that build
    agents. Opt a test out with @pytest.mark.embedded_start.
    """
    from gaia.llm.lemonade_manager import LemonadeManager

    _REAL_START_EMBEDDED["method"] = LemonadeManager.__dict__[
        "start_embedded_if_stopped"
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            LemonadeManager,
            "start_embedded_if_stopped",
            classmethod(lambda cls: False),
        )
        yield


@pytest.fixture(autouse=True)
def _allow_embedded_start(request, monkeypatch):
    if not request.node.get_closest_marker("embedded_start"):
        return
    from gaia.llm.lemonade_manager import LemonadeManager

    monkeypatch.setattr(
        LemonadeManager, "start_embedded_if_stopped", _REAL_START_EMBEDDED["method"]
    )


#: EmbeddedLemonade methods that act on a server; none may reach the real home.
_EMBEDDED_ACTIONS = (
    "install",
    "install_backend",
    "claim",
    "start",
    "stop",
    "unload_models",
    "uninstall",
)


@pytest.fixture(scope="session", autouse=True)
def _guard_real_gaia_home():
    """Fail a test that would act on the developer's real ``~/.gaia`` Lemonade.

    Covers both ways a test reaches it: an ``EmbeddedLemonade`` built on the
    real home, and a daemon spawned without ``GAIA_HOME`` -- that daemon owns,
    and on shutdown stops, the real home's server.
    """
    from gaia.daemon import client
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    def _guard_method(name, real):
        def guarded(self, *args, **kwargs):
            if _is_real_gaia_home(self.root):
                pytest.fail(
                    _REAL_HOME_MESSAGE.format(
                        action=f"EmbeddedLemonade.{name}()",
                        home=_REAL_GAIA_HOME,
                        harm=_LEMONADE_HARM,
                        hint=_LEMONADE_HINT,
                    ),
                    pytrace=False,
                )
            return real(self, *args, **kwargs)

        return guarded

    real_spawn = client._spawn_and_wait

    def guarded_spawn(*args, **kwargs):
        home = _child_gaia_home(os.environ)
        if _is_real_gaia_home(home):
            pytest.fail(
                _REAL_HOME_MESSAGE.format(
                    action="start a daemon for",
                    home=home,
                    harm=_LEMONADE_HARM,
                    hint=_LEMONADE_HINT,
                ),
                pytrace=False,
            )
        return real_spawn(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        for name in _EMBEDDED_ACTIONS:
            mp.setattr(
                EmbeddedLemonade,
                name,
                _guard_method(name, getattr(EmbeddedLemonade, name)),
            )
        mp.setattr(client, "_spawn_and_wait", guarded_spawn)
        # sqlite3.dbapi2 is the module SQLAlchemy and other drivers bind.
        for module in (sqlite3, sqlite3.dbapi2):
            mp.setattr(module, "connect", _guard_connect(module.connect))
        yield


def _sqlite_target(database, uri: bool) -> Path | None:
    """The file a ``sqlite3.connect`` call opens, or None for an in-memory DB."""
    if isinstance(database, int):
        return None
    target = os.fsdecode(database)
    if uri and target.startswith("file:"):
        parsed = urllib.parse.urlsplit(target)
        target = urllib.parse.unquote(parsed.path)
        # file:///C:/x is a Windows URI for C:/x.
        if os.name == "nt" and target.startswith("/") and target[2:3] == ":":
            target = target[1:]
    if target in ("", ":memory:"):
        return None
    return Path(target).expanduser()


def _guard_connect(real_connect):
    """Fail a test that opens a SQLite database in the real ``~/.gaia``."""

    @functools.wraps(real_connect)
    def guarded(database, *args, **kwargs):
        target = _sqlite_target(database, bool(kwargs.get("uri")))
        if target is not None and _is_real_gaia_home(target):
            pytest.fail(
                _REAL_HOME_MESSAGE.format(
                    action=f"open {target.name} in",
                    home=_REAL_GAIA_HOME,
                    harm=_DB_HARM,
                    hint=_DB_HINT,
                ),
                pytrace=False,
            )
        return real_connect(database, *args, **kwargs)

    return guarded


#: Where a database whose default lives in ``~/.gaia`` goes for this session.
#: Per process, so pytest-xdist workers never share one.
_TEST_GAIA_HOME = _STATE_ROOT / "gaia-home"


def _off_real_home(path: Path) -> Path:
    """*path* moved from the real ``~/.gaia`` into ``_TEST_GAIA_HOME``."""
    if not _is_real_gaia_home(path):
        return path
    relative = Path(path).resolve().relative_to(_REAL_GAIA_HOME.resolve())
    return _TEST_GAIA_HOME / relative


@pytest.fixture(scope="session", autouse=True)
def _redirect_default_databases():
    """Open the file index and scratchpad under ``_TEST_GAIA_HOME``, not ``~/.gaia``.

    Both default to ``gaia_home()``, and GAIA_HOME stays unset (see
    _STATE_ENV). ``ChatAgentConfig`` passes that same default in explicitly, so
    the constructor's ``db_path`` is redirected as well as its default.
    """
    from gaia.config import gaia_home
    from gaia.filesystem import index
    from gaia.scratchpad import service

    def redirected_home():
        return _off_real_home(gaia_home())

    def redirect_init(real_init):
        @functools.wraps(real_init)
        def init(self, db_path=None):
            if db_path:
                db_path = str(_off_real_home(Path(db_path).expanduser()))
            return real_init(self, db_path)

        return init

    with pytest.MonkeyPatch.context() as mp:
        for module, cls in (
            (index, index.FileSystemIndexService),
            (service, service.ScratchpadService),
        ):
            mp.setattr(module, "gaia_home", redirected_home)
            mp.setattr(cls, "__init__", redirect_init(cls.__init__))
        yield


#: How long a daemon may take to finish a shutdown a test already asked for.
_DAEMON_EXIT_GRACE_SECONDS = 15.0


def _session_daemons():
    """``(process, environ)`` for each live daemon this pytest process started."""
    import psutil

    from gaia.daemon.strays import is_daemon_cmdline

    mine = str(os.getpid())
    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if not is_daemon_cmdline(proc.info["cmdline"] or []):
            continue
        try:
            env = proc.environ()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue  # another user's, or gone: not one this session started
        if env.get(_EXIT_WITH_PID_ENV) == mine:
            found.append((proc, env))
    return found


@pytest.fixture(scope="session", autouse=True)
def _no_daemon_outlives_the_session():
    """Fail the run if a daemon a test started is still alive at its end.

    Each one is named with its home and the test that started it, then
    stopped, so a leak is reported once instead of piling up on the machine.
    """
    yield
    if importlib.util.find_spec("psutil") is None:
        return  # without psutil no daemon could have started (DAEMON_DEPS)
    import time

    import psutil

    deadline = time.monotonic() + _DAEMON_EXIT_GRACE_SECONDS
    leaked = _session_daemons()
    while leaked and time.monotonic() < deadline:
        time.sleep(0.5)
        leaked = _session_daemons()
    if not leaked:
        return
    from gaia.daemon.strays import DaemonProcess, stop_daemon

    lines = []
    for proc, env in leaked:
        home = env.get("GAIA_DAEMON_HOME") or env.get("GAIA_HOME") or "~/.gaia"
        test = env.get("PYTEST_CURRENT_TEST", "(started outside a test)")
        try:
            stopped = stop_daemon(DaemonProcess(proc.pid, proc.create_time(), None))
        except psutil.NoSuchProcess:
            stopped = True
        note = "" if stopped else " -- would not stop, kill it by pid"
        lines.append(f"  pid {proc.pid}, home {home}, started by {test}{note}")
    pytest.fail(
        f"{len(leaked)} GAIA daemon(s) started during this test session were "
        "still running at its end, and have been killed:\n"
        + "\n".join(lines)
        + "\nStop the daemon in the test's teardown, in a finally "
        "(gaia.daemon.client.request_shutdown, then wait_until_gone), or mock "
        "the daemon boundary if the test does not need a real one.",
        pytrace=False,
    )
