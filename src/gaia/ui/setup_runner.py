# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup for the Agent UI: the same ``gaia init`` the TUI runs.

``src/gaia/installer/init_command.py`` is the single source of truth for what
"set up" means, so this never re-derives it. It asks ``gaia init --check`` and
runs ``gaia init --profile <gaia|npu> --yes``, exactly as ``tui/internal/gaiainit``
does, and turns the output into numbered steps with progress. Readiness is read
from the real state every time, never from a marker file.

``--skip-chat-model`` is passed when the user chose a cloud provider: the chat
then runs remotely and only the embedding model is needed locally.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger
from gaia.utils.terminal import windows_hidden_popen_kwargs

logger = get_logger(__name__)

#: The profile a fresh install sets up; afterwards setup follows
#: ``configured_setup_profile()``.
PROFILE = "gaia"
#: Covers a daemon start plus an embedded Lemonade start (see gaiainit.CheckTimeout).
CHECK_TIMEOUT_S = 180
#: The check plus a cold model load on a slow disk.
VERIFY_TIMEOUT_S = CHECK_TIMEOUT_S + 150
_NOT_READY_EXIT = 1
_LOAD_FAILED_EXIT = 3
_UNWRAPPED_COLUMNS = 1000

PHASE_SERVER = "server"
PHASE_MODELS = "models"
PHASE_FINISH = "finish"
PHASES = (PHASE_SERVER, PHASE_MODELS, PHASE_FINISH)

_STEP_RE = re.compile(r"^Step \d+/\d+:\s*(.+?)\.*$")
_DOWNLOAD_RE = re.compile(r"^Downloading:\s*(\S+)")
_FETCH_RE = re.compile(r"^Downloading Lemonade Server v(\S+?)\.*$")
_PERCENT_RE = re.compile(r"\]\s*(\d{1,3})%")
# "1.20 GB / 2.50 GB" (a model pull) or "(1.0 MB/5.0 MB)" (the server download).
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*[KMG]B)\s*/\s*(\d+(?:\.\d+)?\s*[KMG]B)")
_CHAT_MODEL_RE = re.compile(r"^Chat model:\s*(\S+)")
_LOG_PREFIX_RE = re.compile(r"^\[\d{4}-\d\d-\d\d [\d:]+\]\s*\|")


class SetupError(Exception):
    """``gaia init`` could not be asked or run, with what to do next."""


def _marker():
    from gaia.config import gaia_home

    return gaia_home() / "chat" / "initialized"


def is_initialized() -> bool:
    """True once setup has been seen ready; gates the background agent loop."""
    return _marker().exists()


def mark_initialized() -> None:
    marker = _marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ready", encoding="utf-8")


#: Written by the desktop app's installer (``services/backend-installer.cjs``).
DESKTOP_INSTALL_STATE = "electron-install-state.json"


def desktop_setup_error() -> Optional[str]:
    """Why the desktop app's first-run ``gaia init`` failed, or None.

    The desktop app opens even when that run failed, so the UI says why
    before the first message does.
    """
    from gaia.config import gaia_home

    path = gaia_home() / DESKTOP_INSTALL_STATE
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return f"GAIA could not read the desktop app's setup record at {path}: {e}"
    if not isinstance(state, dict) or state.get("init") != "failed":
        return None
    reason = str(state.get("initError") or "").strip()
    return reason or (
        f"`{run_command(False)}` did not finish. Its output is in "
        f"{path.parent / 'electron-install.log'}."
    )


def configured_setup_profile() -> str:
    # Imported on use: init_command is slow to import and most UI paths never need it.
    from gaia.installer.init_command import (
        configured_setup_profile as profile,  # pylint: disable=import-outside-toplevel
    )

    return profile()


def _gaia_argv(*args: str) -> List[str]:
    # This interpreter, so setup acts on the same GAIA install that serves the UI.
    return [sys.executable, "-m", "gaia.cli", *args]


def check_args(skip_chat_model: bool, load: bool) -> List[str]:
    args = ["init", "--check", "--profile", configured_setup_profile(), "--json"]
    if skip_chat_model:
        args.append("--skip-chat-model")
    if load:
        args.append("--load")
    return args


def run_args(skip_chat_model: bool, gaia_lemonade: bool = False) -> List[str]:
    # --yes: nobody can answer a prompt on a pipe. --skip-webui-build: a source
    # checkout would otherwise rebuild the very UI that is running this.
    args = [
        "init",
        "--profile",
        configured_setup_profile(),
        "--yes",
        "--skip-webui-build",
    ]
    if skip_chat_model:
        args.append("--skip-chat-model")
    if gaia_lemonade:
        args.append("--gaia-lemonade")
    return args


def run_command(skip_chat_model: bool, gaia_lemonade: bool = False) -> str:
    """What a person would type to do this themselves."""
    return (
        f"gaia init --profile {configured_setup_profile()}"
        + (" --skip-chat-model" if skip_chat_model else "")
        + (" --gaia-lemonade" if gaia_lemonade else "")
    )


def _last_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "(no output)"


def parse_status(stdout: str) -> Dict[str, Any]:
    """The JSON object on the last line that holds one (logs may precede it)."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            status = json.loads(line)
        except ValueError as e:
            raise SetupError(
                f"`gaia init --check --json` printed unreadable JSON: {e}"
            ) from e
        if not status.get("ready") and not status.get("stage"):
            status["stage"] = "setup"
        return status
    raise SetupError(
        f"`gaia init --check --json` printed no result: {_last_line(stdout)}"
    )


def check(skip_chat_model: bool = False, load: bool = False) -> Dict[str, Any]:
    """Is the flagship profile set up (and, with *load*, do its models load)?

    Raises :class:`SetupError` when the question could not be answered — that
    is not the same as "not set up" and must never be shown as it.
    """
    try:
        proc = subprocess.run(
            _gaia_argv(*check_args(skip_chat_model, load)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERIFY_TIMEOUT_S if load else CHECK_TIMEOUT_S,
            check=False,
            **windows_hidden_popen_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise SetupError(
            "Checking setup took too long. Run `gaia init --check` in a terminal "
            "to see where it stops."
        ) from e
    if proc.returncode not in (0, _NOT_READY_EXIT, _LOAD_FAILED_EXIT):
        raise SetupError(
            f"Setup could not be checked (exit {proc.returncode}). GAIA said: "
            f"{_last_line(proc.stderr + chr(10) + proc.stdout)}"
        )
    status = parse_status(proc.stdout)
    if status.get("ready"):
        mark_initialized()
    return status


@dataclass
class Progress:
    phase: Optional[str] = None
    text: str = ""
    percent: Optional[int] = None
    size: Optional[str] = None
    failed: bool = False
    chat_model: Optional[str] = None


def describe(line: str) -> Optional[Progress]:
    """What one line of ``gaia init`` output means to the person waiting.

    None for lines that mean nothing to them (log records, banners); those stay
    in the details. Mirrors ``gaiainit.Describe`` in the TUI.
    """
    line = line.strip()
    if not line or _LOG_PREFIX_RE.match(line):
        return None
    m = _PERCENT_RE.search(line)
    if m:
        size = _SIZE_RE.search(line)
        return Progress(
            percent=min(100, int(m.group(1))),
            size=f"{size.group(1)} of {size.group(2)}" if size else None,
        )
    m = _CHAT_MODEL_RE.match(line)
    if m:
        return Progress(chat_model=m.group(1))
    if line.startswith("❌"):
        return Progress(text=line.lstrip("❌").strip(), failed=True)
    m = _STEP_RE.match(line)
    if m:
        step = m.group(1).lower()
        if "lemonade" in step:
            return Progress(PHASE_SERVER, "Starting the local model server")
        if "downloading models" in step:
            return Progress(PHASE_MODELS, "Downloading the models")
        if "python dependencies" in step:
            return Progress(PHASE_FINISH, "Installing Python packages")
        if "agent installation" in step:
            return Progress(PHASE_FINISH, "Checking the GAIA agent")
        if "verifying" in step:
            return Progress(PHASE_FINISH, "Checking that the models load")
        return Progress(PHASE_FINISH, m.group(1))
    if _FETCH_RE.match(line):
        return Progress(PHASE_SERVER, "Downloading the local model server")
    m = _DOWNLOAD_RE.match(line)
    if m:
        return Progress(PHASE_MODELS, f"Downloading {m.group(1)}")
    return None


def users_lemonade_url() -> Optional[str]:
    """The user's own Lemonade when they chose "Use mine", else None."""
    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice as choice

    try:
        info = choice.describe()
    except (choice.LemonadeChoiceError, GaiaConfigError) as e:
        raise SetupError(f"Could not read which Lemonade Server GAIA uses: {e}") from e
    return info["base_url"] if info["mode"] == choice.MODE_EXTERNAL else None


#: Hosts that mean this PC, including the wildcard bind addresses.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::")


def _start_hint(host: Optional[str]) -> Optional[str]:
    """How to start the user's server, or None when this PC has none to start."""
    from gaia.llm.lemonade_launcher import describe_start_hint, resolve_lemonade

    if host not in _LOCAL_HOSTS:
        return f"Start it on the computer at {host}, then try again."
    # With nothing installed the hint says to run `gaia init`, which fails the same way.
    if not resolve_lemonade().found:
        return None
    return describe_start_hint().instruction


def server_problem() -> Optional[Dict[str, Any]]:
    """Plain words and next steps when the user's own Lemonade isn't answering.

    None when GAIA uses its own server or the user's answers: the failure was
    something else, and ``gaia init``'s own error line says what.
    """
    from urllib.parse import urlparse

    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice as choice
    from gaia.llm.lemonade_client import (
        configured_lemonade_url,
        resolve_lemonade_base_url,
    )

    try:
        configured = configured_lemonade_url()
        from_env = choice.configured_source() == "env"
    except (choice.LemonadeChoiceError, GaiaConfigError) as e:
        logger.warning("Setup failed; could not read which Lemonade it used: %s", e)
        return None
    if not configured:
        return None
    url = resolve_lemonade_base_url(configured)
    if choice.probe(url).status != choice.UNREACHABLE:
        return None
    parsed = urlparse(url)
    try:
        start = _start_hint(parsed.hostname)
    except OSError as e:
        # The hint is extra; losing it must not hide that the server isn't answering.
        logger.warning("Could not work out how to start Lemonade: %s", e)
        start = None
    return {
        "kind": "server_unreachable",
        "message": f"Your Lemonade Server at {parsed.netloc or url} isn't answering.",
        "start": start,
        # LEMONADE_BASE_URL outranks a saved choice, so only unsetting it switches.
        "use_gaia": not from_env,
        "switch_hint": (
            "LEMONADE_BASE_URL points GAIA at this server. To use GAIA's own "
            "instead, unset it and restart GAIA."
            if from_env
            else None
        ),
    }


def _forget_previous_server() -> None:
    """Cached agents and the model manager still point at the old server."""
    from gaia.llm.lemonade_manager import LemonadeManager

    from ._chat_helpers import evict_all_session_agents

    evict_all_session_agents()
    LemonadeManager.reset()


def step_labels(
    skip_chat_model: bool,
    users_server: Optional[str] = None,
    chat_model: Optional[str] = None,
) -> List[Dict[str, str]]:
    """The steps as the user reads them: their server, and the model setup chose."""
    if skip_chat_model:
        models = "the embedding model, under 1 GB"
    elif chat_model:
        models = f"chat model {chat_model}, plus the embedding model"
    else:
        models = "the chat model that fits this PC, plus the embedding model"
    return [
        (
            {
                "key": PHASE_SERVER,
                "label": "Use your Lemonade Server",
                "detail": users_server,
            }
            if users_server
            else {
                "key": PHASE_SERVER,
                "label": "Install the local model server",
                "detail": "about 1 minute",
            }
        ),
        {"key": PHASE_MODELS, "label": "Download the models", "detail": models},
        {"key": PHASE_FINISH, "label": "Check that the models load", "detail": ""},
    ]


@dataclass
class SetupRun:
    """One ``gaia init`` run and what it has reported so far."""

    skip_chat_model: bool
    users_server: Optional[str] = None
    chat_model: Optional[str] = None
    gaia_lemonade: bool = False
    state: str = "running"  # running | verifying | ready | failed | cancelled
    phase: Optional[str] = None
    text: str = "Starting setup"
    percent: Optional[int] = None
    size: Optional[str] = None
    error: Optional[str] = None
    #: The user's server didn't answer: see :func:`server_problem`.
    problem: Optional[Dict[str, Any]] = None
    log: List[str] = field(default_factory=list)
    status: Optional[Dict[str, Any]] = None

    def view(self) -> Dict[str, Any]:
        reached = PHASES.index(self.phase) if self.phase in PHASES else -1
        steps = []
        labels = step_labels(self.skip_chat_model, self.users_server, self.chat_model)
        for i, step in enumerate(labels):
            if self.state == "ready" or i < reached:
                status = "done"
            elif i == reached or (reached < 0 and i == 0):
                status = (
                    "failed"
                    if self.state == "failed"
                    else "cancelled" if self.state == "cancelled" else "active"
                )
            else:
                status = "pending"
            steps.append({**step, "status": status})
        return {
            "state": self.state,
            "steps": steps,
            "text": self.text,
            "percent": self.percent,
            "size": self.size,
            "error": self.error,
            "problem": self.problem,
            "command": run_command(self.skip_chat_model, self.gaia_lemonade),
            "log_tail": self.log[-40:],
            "skip_chat_model": self.skip_chat_model,
            "gaia_lemonade": self.gaia_lemonade,
            "chat_model": None if self.skip_chat_model else self.chat_model,
        }


class SetupRunner:
    """Runs at most one ``gaia init`` at a time for this backend."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run: Optional[SetupRun] = None
        self._proc: Optional[subprocess.Popen] = None

    def status(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._run.view() if self._run else None

    def start(
        self, skip_chat_model: bool, gaia_lemonade: bool = False
    ) -> Dict[str, Any]:
        """Start setup; *gaia_lemonade* sets up and saves GAIA's own server."""
        if gaia_lemonade:
            from gaia.llm.lemonade_choice import env_override_note

            if env_override_note():
                raise SetupError(
                    "LEMONADE_BASE_URL points GAIA at another server, so setup "
                    "can't switch to GAIA's own. Unset it and restart GAIA."
                )
        with self._lock:
            if self._run is not None and self._run.state in ("running", "verifying"):
                raise SetupError("Setup is already running.")
            run = SetupRun(
                skip_chat_model=skip_chat_model,
                users_server=None if gaia_lemonade else users_lemonade_url(),
                gaia_lemonade=gaia_lemonade,
            )
            command = run_command(skip_chat_model, gaia_lemonade)
            try:
                self._proc = subprocess.Popen(
                    _gaia_argv(*run_args(skip_chat_model, gaia_lemonade)),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    # Rich wraps a pipe at 80 columns, which would cut the
                    # error line shown as the failure reason mid-sentence.
                    env={**os.environ, "COLUMNS": str(_UNWRAPPED_COLUMNS)},
                    **windows_hidden_popen_kwargs(),
                )
            except OSError as e:
                raise SetupError(
                    f"Could not start `{command}`: {e}. Run it in a terminal instead."
                ) from e
            self._run = run
            proc = self._proc
            view = run.view()
        threading.Thread(target=self._watch, args=(run, proc), daemon=True).start()
        return view

    def cancel(self) -> bool:
        with self._lock:
            if self._proc is None or self._run is None or self._run.state != "running":
                return False
            self._run.state = "cancelled"
            self._run.text = "Setup stopped"
            self._proc.kill()
            return True

    def _watch(self, run: SetupRun, proc: subprocess.Popen) -> None:
        """Run the setup thread; an unexpected error fails the run, never strands it."""
        try:
            self._pump(run, proc)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Setup thread failed")
            with self._lock:
                if run.state in ("running", "verifying"):
                    run.state = "failed"
                    run.error = (
                        f"Setup stopped on an unexpected error: {e}. "
                        "See the Agent UI backend log for details."
                    )

    def _pump(self, run: SetupRun, proc: subprocess.Popen) -> None:
        buf = b""
        assert proc.stdout is not None
        while True:
            chunk = (
                proc.stdout.read1(4096)
                if hasattr(proc.stdout, "read1")
                else proc.stdout.read(4096)
            )
            if not chunk:
                break
            buf += chunk
            # Progress bars redraw with \r and no newline.
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                self._consume(run, raw.decode("utf-8", errors="replace"))
        if buf:
            self._consume(run, buf.decode("utf-8", errors="replace"))
        code = proc.wait()
        # A --gaia-lemonade run fails on GAIA's own server; its error says why.
        failed_early = (
            code != 0
            and run.state == "running"
            and run.phase in (None, PHASE_SERVER)
            and not run.gaia_lemonade
        )
        # Probed outside the lock: the user's server may take seconds to refuse.
        problem = server_problem() if failed_early else None
        if code == 0 and run.gaia_lemonade:
            # init saved the switch, whether or not the check below passes.
            _forget_previous_server()
        with self._lock:
            if run.state == "cancelled":
                return
            if code != 0:
                run.state = "failed"
                if problem is not None:
                    # The raw exception stays in the log, behind "Details".
                    run.problem, run.error = problem, problem["message"]
                    return
                run.error = (
                    f"`{run_command(run.skip_chat_model, run.gaia_lemonade)}` "
                    f"failed (exit {code}): "
                    f"{run.error or _last_line(chr(10).join(run.log))}"
                )
                return
            run.state = "verifying"
            run.phase = PHASE_FINISH
            run.text = "Checking that the models load"
            run.percent = run.size = None
        self._verify(run)

    def _verify(self, run: SetupRun) -> None:
        try:
            status = check(run.skip_chat_model, load=True)
        except SetupError as e:
            with self._lock:
                run.state, run.error = "failed", str(e)
            return
        with self._lock:
            run.status = status
            if status.get("ready"):
                run.state, run.text = "ready", "GAIA is ready"
            else:
                run.state = "failed"
                run.error = "; ".join(status.get("reasons") or []) or (
                    "Setup finished but GAIA is still not ready."
                )

    def _consume(self, run: SetupRun, line: str) -> None:
        line = line.strip()
        if not line:
            return
        progress = describe(line)
        with self._lock:
            run.log.append(line)
            if len(run.log) > 400:
                del run.log[:-400]
            if progress is None:
                return
            if progress.failed:
                run.error = progress.text
                return
            if progress.chat_model is not None:
                run.chat_model = progress.chat_model
                return
            if progress.percent is not None:
                run.percent, run.size = progress.percent, progress.size
            if progress.phase is not None:
                if run.phase not in PHASES or PHASES.index(
                    progress.phase
                ) >= PHASES.index(run.phase):
                    run.phase = progress.phase
                run.percent = run.size = None
            if progress.text:
                run.text = progress.text


runner = SetupRunner()
