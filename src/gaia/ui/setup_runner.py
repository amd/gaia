# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup for the Agent UI: the same ``gaia init`` the TUI runs.

``src/gaia/installer/init_command.py`` is the single source of truth for what
"set up" means, so this never re-derives it. It asks ``gaia init --check`` and
runs ``gaia init --profile gaia --yes``, exactly as ``tui/internal/gaiainit``
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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.llm.surface_hints import surface_wording
from gaia.logger import get_logger
from gaia.utils.terminal import windows_hidden_popen_kwargs

logger = get_logger(__name__)

PROFILE = "gaia"
#: Covers a daemon start plus an embedded Lemonade start (see gaiainit.CheckTimeout).
CHECK_TIMEOUT_S = 180
#: The check plus a cold model load on a slow disk.
VERIFY_TIMEOUT_S = CHECK_TIMEOUT_S + 150
_AGENT_ID = "gaia"
_AGENT_TEST_PROMPT = "Reply with the single word: ok"
#: The Test step's reason when the assistant's program isn't on this PC.
AGENT_MISSING = "GAIA's assistant isn't installed on this PC."
_NOT_READY_EXIT = 1
_LOAD_FAILED_EXIT = 3
_UNWRAPPED_COLUMNS = 1000
#: How long a new start waits for a stopped run's last checks to finish.
_STOPPING_WAIT_S = 10.0

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
# Terminal escapes: OSC (e.g. hyperlinks), CSI (colour, cursor), other ESC
# sequences, and one cut off at the end of a line. A literal "[" is content.
_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*(?:[@-~]|$)"
    r"|\x1b[ -/]*[0-~]"
    r"|\x1b"
)


class SetupError(Exception):
    """``gaia init`` could not be asked or run, with what to do next."""


class SetupBusy(SetupError):
    """Setup, or that model's download, is already running."""


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
        "The desktop app's setup did not finish. Its output is in "
        f"{path.parent / 'electron-install.log'}."
    )


def _child_env(**extra: str) -> Dict[str, str]:
    """This process's environment for a ``gaia init`` child.

    Without the broker URL the daemon exports for its sidecars: run from the
    daemon, the child inherited the URL with no credential, so every model
    load in its check failed. Without it the CLI finds the daemon itself.
    """
    from gaia.daemon.constants import BROKER_URL_ENV_VAR
    from gaia.llm.lemonade_choice import child_lemonade_env

    env = {k: v for k, v in os.environ.items() if k != BROKER_URL_ENV_VAR}
    env.update(extra)
    return child_lemonade_env(env)


def _gaia_argv(*args: str) -> List[str]:
    # This interpreter, so setup acts on the same GAIA install that serves the UI.
    return [sys.executable, "-m", "gaia.cli", *args]


def check_args(skip_chat_model: bool, load: bool) -> List[str]:
    args = ["init", "--check", "--profile", PROFILE, "--json"]
    if skip_chat_model:
        args.append("--skip-chat-model")
    if load:
        args.append("--load")
    return args


def run_args(
    skip_chat_model: bool,
    gaia_lemonade: bool = False,
    allow_low_disk: bool = False,
    keep_trying: bool = False,
    extra_models: Optional[List[str]] = None,
    server_only: bool = False,
    upgrade_embedded: bool = False,
) -> List[str]:
    # --yes: nobody can answer a prompt on a pipe. --skip-webui-build: a source
    # checkout would otherwise rebuild the very UI that is running this.
    # --skip-pip-extras: the app ships its packages; setup never pip-installs.
    # --progress-json: per-model bytes and states for the progress rows.
    # server_only: start the model server so the user can choose models next.
    args = [
        "init",
        "--profile",
        PROFILE,
        "--yes",
        "--skip-webui-build",
        "--skip-pip-extras",
        "--progress-json",
    ]
    if allow_low_disk:
        args.append("--allow-low-disk")
    if keep_trying:
        args.append("--keep-trying")
    if skip_chat_model:
        args.append("--skip-chat-model")
    if gaia_lemonade:
        args.append("--gaia-lemonade")
    if server_only or upgrade_embedded:
        args.append("--server-only")
    for model_id in extra_models or []:
        args += ["--download-model", model_id]
    return args


def run_command(skip_chat_model: bool, gaia_lemonade: bool = False) -> str:
    """What a person would type to do this themselves."""
    return (
        "gaia init --profile gaia"
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


def check(
    skip_chat_model: bool = False,
    load: bool = False,
    on_start: Optional[Callable[[subprocess.Popen], None]] = None,
    mark: bool = True,
) -> Dict[str, Any]:
    """Is the flagship profile set up (and, with *load*, do its models load)?

    *on_start* receives the running check, so a caller can stop it (Cancel).
    *mark* False leaves setup unmarked, for a caller with tests still to run.
    Raises :class:`SetupError` when the question could not be answered — that
    is not the same as "not set up" and must never be shown as it.
    """
    proc = subprocess.Popen(
        _gaia_argv(*check_args(skip_chat_model, load)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
        **windows_hidden_popen_kwargs(),
    )
    if on_start is not None:
        on_start(proc)
    try:
        stdout, stderr = proc.communicate(
            timeout=VERIFY_TIMEOUT_S if load else CHECK_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        proc.kill()
        proc.communicate()
        raise SetupError(
            "Checking setup took too long. Try again; its log shows where it stopped."
        ) from e
    if proc.returncode not in (0, _NOT_READY_EXIT, _LOAD_FAILED_EXIT):
        raise SetupError(
            f"Setup could not be checked (exit {proc.returncode}). GAIA said: "
            f"{_last_line(stderr + chr(10) + stdout)}"
        )
    status = parse_status(stdout)
    if mark and status.get("ready"):
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


def _agent_turn_problem(model_id: str) -> Optional[str]:
    """Why one turn through the installed GAIA assistant on *model_id* failed,
    in the agent's own words (or :data:`AGENT_MISSING`), or None when it answered.

    The turn goes through the assistant's own program, the way chat reaches it.
    """
    from gaia.hub import installer as hub_installer
    from gaia.ui._chat_helpers import relay_one_prompt
    from gaia.ui.sse_handler import SSEOutputHandler

    if hub_installer.read_sentinel(_AGENT_ID) is None:
        return AGENT_MISSING
    try:
        answer = relay_one_prompt(
            SSEOutputHandler(background_mode=True),
            _AGENT_TEST_PROMPT,
            agent_type=_AGENT_ID,
            model_id=model_id,
            memory="off",
            max_steps=1,
        )
        why = None if (answer or "").strip() else "it ended without an answer"
    except RuntimeError as e:
        why = str(e)
    return why


def _agent_turn_failure(model_id: str) -> str:
    """The Test step's sentence for a failed chat on *model_id*."""
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        is_cloud_model_with_catalog,
    )
    from gaia.ui.routers.providers import display_name
    from gaia.ui.warmup import _device_for

    name = display_name(model_id)
    try:
        cloud = model_id.startswith("claude-") or is_cloud_model_with_catalog(model_id)
    except LemonadeClientError as e:
        return (
            f"GAIA's test chat on {name} got no answer, and the model catalog "
            f"could not be checked ({e}). Check Lemonade and retry setup."
        )
    if cloud:
        what = f"GAIA's test chat on {name}, which runs in the cloud, got no answer."
    else:
        device = _device_for(LemonadeClient(verbose=False), model_id)
        where = f"this PC's {device.upper()}" if device else "this PC"
        what = f"{name} loads on {where}, but GAIA's test chat on it got no answer."
    return (
        f"{what} Test again, or choose another chat model. The setup report "
        "has the details."
    )


def agent_ui_chat_model(db, run: "SetupRun") -> str:
    """The model the Agent UI's first chat after *run* opens on.

    A local run: the chat model it set up, which the Ready screen switches
    chat to. A cloud run: the model a new chat opens on, resolved as chat does.
    """
    if not run.skip_chat_model:
        if not run.chat_model:
            raise SetupError(
                "Setup didn't say which chat model it set up, so GAIA can't test "
                "a chat. Test again; the setup report has the details."
            )
        return run.chat_model
    from gaia.ui._chat_helpers import get_agent_registry
    from gaia.ui.routers.providers import restore_last_model
    from gaia.ui.warmup import new_chat_model

    # The UI applies last_model once the server is up; do it now, so the test
    # and the first chat agree.
    restore_last_model(db)
    model = new_chat_model(db, get_agent_registry())[0]
    if not model:
        raise SetupError(
            "GAIA couldn't tell which model chat will open on, so it can't test "
            "a chat. Pick a chat model in Settings, then test again."
        )
    return model


def tui_chat_model(run: "SetupRun") -> str:  # pylint: disable=unused-argument
    """The model the TUI opens chat on: the saved ``last_model``, else the agent's default."""
    from gaia.llm.lemonade_client import resolve_chat_model

    return resolve_chat_model()


#: The Test step's reason when a cloud setup has no cloud model saved to chat with.
NO_CLOUD_MODEL = (
    "No cloud model is saved, so GAIA can't test a chat. Go back to Your setup "
    "and connect the cloud provider again."
)


#: Setup's own record, in GAIA's home, which ``gaia diagnostics`` bundles.
SETUP_REPORT = "setup-report.json"
REPORT_NAME_RE = re.compile(r"diagnostics-\d{8}-\d{6}\.tgz")
_REPORT_TIMEOUT_S = 120
#: How long a report waits for a failed run's problem report to finish saving.
_RECORD_WAIT_S = 30


def write_report(record: Dict[str, Any]) -> Path:
    """Save *record* beside GAIA's logs and bundle both for a bug report.

    Returns the bundle: setup's record, every GAIA log and a system snapshot,
    secret environment variables redacted, as ``gaia diagnostics`` writes it.
    """
    from gaia.home import gaia_home

    home = gaia_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / SETUP_REPORT).write_text(
            json.dumps(record, indent=2, default=str), encoding="utf-8"
        )
    except OSError as e:
        raise SetupError(f"Couldn't save setup's record in {home}: {e}") from e
    out = home / f"diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.tgz"
    try:
        proc = subprocess.run(
            _gaia_argv("diagnostics", "--output", str(out)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_REPORT_TIMEOUT_S,
            check=False,
            env=_child_env(),
            **windows_hidden_popen_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        raise SetupError(
            f"The troubleshooting report took over {_REPORT_TIMEOUT_S} s and was stopped."
        ) from e
    except OSError as e:
        raise SetupError(f"Couldn't start the troubleshooting report: {e}") from e
    if proc.returncode != 0 or not out.is_file():
        raise SetupError(
            "Couldn't write the troubleshooting report: "
            f"{_last_line(proc.stdout + chr(10) + proc.stderr)}"
        )
    return out


def _record_setup_failure(
    run: "SetupRun", cause: Optional[BaseException]
) -> Optional[str]:
    """Save a local fault record for a failed run; its id."""
    from gaia.llm.failure_record import record_local_failure

    error = SetupError(run.error or "Setup failed")
    if cause is not None:
        error.__cause__ = cause
    return record_local_failure(
        error,
        where="setup",
        kind="setup",
        model=run.chat_model,
        extra={"problem": run.problem} if run.problem else None,
    )


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
    """Plain words and next steps when the user's own Lemonade isn't answering or is too old.

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
    probed = choice.probe(url)
    if probed.status not in (choice.UNREACHABLE, choice.TOO_OLD):
        return None
    parsed = urlparse(url)
    if probed.status == choice.TOO_OLD:
        # Upgrading happens outside GAIA, so the way on from here is GAIA's own.
        kind, start = "server_too_old", None
        message = (
            f"Your Lemonade Server at {parsed.netloc or url} is v{probed.version}; "
            f"GAIA needs v{probed.minimum} or newer."
        )
    else:
        kind = "server_unreachable"
        message = f"Your Lemonade Server at {parsed.netloc or url} isn't answering."
        try:
            start = _start_hint(parsed.hostname)
        except OSError as e:
            # The hint is extra; losing it must not hide that the server isn't answering.
            logger.warning("Could not work out how to start Lemonade: %s", e)
            start = None
    # Choosing GAIA's own here also overrides LEMONADE_BASE_URL (start()).
    return {
        "kind": kind,
        "message": message,
        "start": start,
        "use_gaia": True,
        # Switching ignores this value from then on; the screen says so first.
        "env_url": configured if from_env else None,
    }


def _override_env_server() -> None:
    """GAIA's own, picked here, wins over the ``LEMONADE_BASE_URL`` it replaces.

    Saved before ``gaia init`` runs: init, its check and the daemon inherit
    that variable and would otherwise all go back to the server that failed.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_choice import ignore_env_url

    try:
        ignore_env_url()
    except GaiaConfigError as e:
        raise SetupError(f"Could not save the switch to GAIA's own: {e}") from e


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
    server_only: bool = False,
    upgrade_embedded: bool = False,
) -> List[Dict[str, str]]:
    """The steps as the user reads them: their server, and the model setup chose."""
    if upgrade_embedded:
        return [
            {
                "key": PHASE_SERVER,
                "label": "Update the local model server",
                "detail": "your downloaded models stay",
            }
        ]
    server = (
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
    )
    if server_only:
        return [server]
    if skip_chat_model:
        models = "the embedding model, under 1 GB"
    elif chat_model:
        models = f"chat model {chat_model}, plus the embedding model"
    else:
        models = "the chat model that fits this PC, plus the embedding model"
    return [
        server,
        {"key": PHASE_MODELS, "label": "Download the models", "detail": models},
        {"key": PHASE_FINISH, "label": "Check that the models load", "detail": ""},
    ]


#: The screen's stages that keep a log: the checks, the downloads, the Test step.
STAGES = ("check", "download", "test")
#: Lines each stage shows in Details; a troubleshooting report keeps them all.
STAGE_LOG_SHOWN = 200
_STAGE_LOG_KEPT = 4000


#: Per-model states, in the order a model moves through them.
MODEL_STATES = (
    "queued",
    "downloading",
    "verifying",
    "ready",
    "failed",
    "skipped",
    "cancelled",
    # Downloaded, but the run stopped before its load check: kept, never retried.
    "unchecked",
)


#: A line that starts one model's download in ``gaia init``'s output.
_DOWNLOADING_RE = re.compile(
    r"^\s*(?:\S+\s+)?(?:Downloading|Pulling)\b[:\s]+(\S+)", re.I
)
#: A progress bar's redraw: only its latest state is worth keeping.
_BAR_RE = re.compile(r"^\s*\[[=#>\-. ━─]*\]")
#: What gaia init prints after a failed download: where to look next.
_HINT_RE = re.compile(
    r"^\s*(?:Read .* for the server's own error|Check the Lemonade Server at )"
)
_MODEL_LOG_KEPT = 300
#: Lines each row shows; the troubleshooting report keeps them all.
MODEL_LOG_SHOWN = 80


@dataclass
class ModelProgress:
    """One model's download, as the progress rows show it."""

    id: str
    state: str = "queued"
    bytes_done: int = 0
    bytes_total: int = 0
    speed_bps: int = 0
    error: Optional[str] = None
    error_kind: Optional[str] = None
    retrying_until: Optional[int] = None
    #: The Test step's row that failed this model, so Retry repeats that test.
    test_job: Optional[str] = None
    #: Bytes already on disk when this pull started (a resumed download).
    bytes_before: Optional[int] = None
    #: What the download printed and reported, this model's lines only.
    log: List[str] = field(default_factory=list)

    def add_log(self, line: str) -> None:
        """Keep *line*; a progress bar's redraw replaces the one before it."""
        if self.log and _BAR_RE.search(line) and _BAR_RE.search(self.log[-1]):
            self.log[-1] = line
            return
        self.log.append(line)
        if len(self.log) > _MODEL_LOG_KEPT:
            del self.log[:-_MODEL_LOG_KEPT]

    def view(self, chat_model: Optional[str] = None) -> Dict[str, Any]:
        from gaia.installer.model_choice import roles_for

        left = max(0, self.bytes_total - self.bytes_done)
        return {
            "id": self.id,
            "roles": roles_for(self.id, chat_model),
            "state": self.state,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "speed_bps": self.speed_bps,
            "eta_s": (
                round(left / self.speed_bps)
                if self.state == "downloading" and self.speed_bps > 0
                else None
            ),
            "error": self.error,
            "error_kind": self.error_kind,
            "can_retry": self.state in ("failed", "cancelled"),
            "retrying_until": self.retrying_until,
            "downloaded_before_gb": (
                round(self.bytes_before / 1e9, 2) if self.bytes_before else None
            ),
            "log": self.log[-MODEL_LOG_SHOWN:],
        }


def _bytes_on_disk(client, model_id: str) -> Optional[int]:
    """Bytes an interrupted pull of *model_id* left in Lemonade's store, the
    same count the setup plan's ``partial_gb`` comes from. None when unknown."""
    from gaia.installer.init_command import bytes_already_on_disk
    from gaia.llm.lemonade_client import LemonadeClientError
    from gaia.llm.model_fit import model_store_free_gb

    try:
        _free, store = model_store_free_gb(client.get_system_info(timeout=15))
        return bytes_already_on_disk(client, [model_id], store).get(model_id)
    except LemonadeClientError as e:
        # Only the "resumed" note depends on it; the download itself goes on.
        logger.warning("Could not read what %s left on disk: %s", model_id, e)
        return None


def overall(models: List[ModelProgress]) -> Dict[str, Any]:
    """Bytes-weighted progress across the models being set up."""
    counted = [m for m in models if m.state != "skipped"]
    done = sum(min(m.bytes_done, m.bytes_total) for m in counted)
    total = sum(m.bytes_total for m in counted)
    speed = sum(m.speed_bps for m in counted if m.state == "downloading")
    return {
        "bytes_done": done,
        "bytes_total": total,
        "speed_bps": speed,
        "eta_s": round((total - done) / speed) if speed > 0 else None,
        "percent": int(done * 100 / total) if total else None,
        "failed": sum(1 for m in counted if m.state == "failed"),
    }


def _failure_text(model_id: str, kind: Optional[str]) -> str:
    """What the row's Details says, in plain words; Lemonade's own go to its log."""
    if kind == "disk_full":
        return (
            f"Ran out of disk space while downloading {model_id}. Free some space "
            "on the model drive and press Retry; the download continues from "
            "where it stopped."
        )
    if kind == "network":
        return (
            f"The connection dropped while downloading {model_id}. Press Retry "
            "to continue from where it stopped."
        )
    if kind == "path_too_long":
        return (
            f"Windows couldn't save {model_id}: the path to the models folder is "
            "too long. Turn on long file paths in Windows, or use a models folder "
            "with a shorter path, then press Retry."
        )
    # Lemonade's own words can name paths on this PC: they go to the row's log.
    return (
        f"Lemonade couldn't download {model_id}. Its download log has "
        "Lemonade's message; press Retry to try again."
    )


#: Test failures that say nothing is wrong with the file: Retry tests again,
#: and never deletes or re-downloads the model. A "broken" one is ``verify``.
TEST_KINDS = ("memory", "stuck", "answer", "window")


def _test_failure(
    model_id: str, kind: Optional[str], error: Optional[str]
) -> Tuple[str, str]:
    """A failed test's row: its ``error_kind`` and what Details says."""
    why = f" ({error})" if error else ""
    if kind == "memory":
        return kind, (
            f"{model_id} is downloaded, but this PC didn't have enough free memory "
            "to test it. Close some apps, then press Retry to test it again; "
            "nothing is downloaded again."
        )
    if kind == "stuck":
        return kind, (
            f"{model_id} is downloaded, but the model server didn't answer before "
            "its test finished. Press Retry to test it again; nothing is "
            "downloaded again."
        )
    if kind == "answer":
        return kind, (
            f"{model_id} loaded, but its test got nothing usable back{why}. "
            "Press Retry to test it again; nothing is downloaded again."
        )
    if kind == "window":
        return kind, (
            f"{model_id} loaded with a smaller context window than GAIA needs{why}. "
            "Press Retry to test it again; nothing is downloaded again."
        )
    return "verify", f"{model_id} is downloaded but would not load: {error}"


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
    allow_low_disk: bool = False
    keep_trying: bool = False
    #: Only start the model server; the models are chosen and pulled after.
    server_only: bool = False
    #: Upgrade an existing embedded Lemonade artifact without model downloads.
    upgrade_embedded: bool = False
    #: Models pulled beside the profile's; setup has no load check for them.
    extra_models: List[str] = field(default_factory=list)
    #: A model's download failed in this run: its row already says why.
    model_failed: bool = False
    #: The only unresolved failure is a model test, so a later successful retest
    #: may finish setup. Any other failed stage keeps the run failed.
    failed_by_tests: bool = False
    #: The model the app's first chat after this run opens on; see :func:`agent_ui_chat_model`.
    chat_model_for: Optional[Callable[["SetupRun"], str]] = None
    #: Each piece besides the models, by id: ``active``, ``done``, ``failed``.
    components: Dict[str, str] = field(default_factory=dict)
    #: Why a failed piece failed, when init said: ``transient`` or ``other``.
    component_kinds: Dict[str, str] = field(default_factory=dict)
    #: The Test step's rows, in the order they ran, by (job, subject).
    tests: Dict[Any, Dict[str, Any]] = field(default_factory=dict)
    #: Everything this run printed or reported, by screen stage.
    stage_logs: Dict[str, List[str]] = field(default_factory=dict)

    def stage(self) -> str:
        """The screen stage this run is at, for its log."""
        if self.server_only or self.upgrade_embedded:
            return "check"
        if self.phase == PHASE_FINISH or self.state == "verifying" or self.tests:
            return "test"
        return "download"

    def note(self, line: str, printed: bool = False) -> None:
        """Log one line under the current stage; *printed* lines are ``gaia
        init``'s own output, which also feeds the failure reason. Caller holds
        the runner's lock."""
        if printed:
            self.log.append(line)
            if len(self.log) > 400:
                del self.log[:-400]
        kept = self.stage_logs.setdefault(self.stage(), [])
        kept.append(line)
        if len(kept) > _STAGE_LOG_KEPT:
            del kept[:-_STAGE_LOG_KEPT]

    def view(self) -> Dict[str, Any]:
        reached = PHASES.index(self.phase) if self.phase in PHASES else -1
        steps = []
        labels = step_labels(
            self.skip_chat_model,
            self.users_server,
            self.chat_model,
            self.server_only,
            self.upgrade_embedded,
        )
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
            "server_only": self.server_only,
            "upgrade_embedded": self.upgrade_embedded,
            "model_failed": self.model_failed,
            "components": dict(self.components),
            "component_kinds": dict(self.component_kinds),
            "tests": list(self.tests.values()),
            "chat_model": None if self.skip_chat_model else self.chat_model,
        }


class _QuietConsole:
    """What ``_JsonDownloadProgress`` prints to, for a pull with no terminal."""

    def print_download_error(self, message: str, model_id: str) -> None:
        logger.warning("Download of %s failed: %s", model_id, message)


class _Events:
    """Feeds an in-process pull's events to the runner, as JSON lines would."""

    enabled = True

    def __init__(self, runner: "SetupRunner") -> None:
        self._runner = runner

    def emit(self, event: str, **fields) -> None:
        self._runner._on_event({"event": event, **fields})


class SetupRunner:
    """Runs at most one ``gaia init`` at a time for this backend, plus the
    single-model downloads Settings and Retry start between runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run: Optional[SetupRun] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        #: Saving a failed run's problem report, which the next report waits for.
        self._recording: Optional[threading.Thread] = None
        self._check_proc: Optional[subprocess.Popen] = None
        # Clear while a load check runs: only it can write to the model rows.
        self._check_idle = threading.Event()
        self._check_idle.set()
        self._models: Dict[str, ModelProgress] = {}
        #: model id -> Stop flag of an in-process pull.
        self._pulls: Dict[str, threading.Event] = {}
        #: The checks' log, kept when the run that started the server is replaced.
        self._check_log: List[str] = []
        #: The model gaia init is downloading now, so its printed lines go to its row.
        self._log_model: Optional[str] = None
        #: The last download that failed, which the "where to look" hint belongs to.
        self._last_failed: Optional[str] = None
        #: Consecutive timeout failures per model. Two unanswered tests offer
        #: the user a fresh download instead of looping forever on Retry.
        self._test_failures: Dict[str, int] = {}

    def _logs(self, shown: Optional[int]) -> Dict[str, List[str]]:
        """Each stage's log, its last *shown* lines. Caller holds the lock."""
        logs = dict(self._run.stage_logs) if self._run else {}
        if "check" not in logs and self._check_log:
            logs["check"] = self._check_log
        return {k: list(v[-shown:] if shown else v) for k, v in logs.items()}

    def report(self) -> Dict[str, Any]:
        """Everything setup knows about its last run, with every log line kept."""
        with self._lock:
            recording = self._recording
        if recording is not None:
            recording.join(timeout=_RECORD_WAIT_S)
        with self._lock:
            view = self._run.view() if self._run else {"state": "idle"}
            chat = self._run.chat_model if self._run else None
            view["models"] = [
                {**m.view(chat), "log": list(m.log)} for m in self._models.values()
            ]
            view["log_tail"] = list(self._run.log) if self._run else []
            view["logs"] = self._logs(None)
            view["check_result"] = self._run.status if self._run else None
            if recording is not None and recording.is_alive():
                view["problem_report"] = (
                    f"still being saved after {_RECORD_WAIT_S} s; it is in "
                    "the failures folder once done"
                )
            return view

    def status(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._run is None and not self._models:
                return None
            view = self._run.view() if self._run else {"state": "idle"}
            models = list(self._models.values())
            chat = self._run.chat_model if self._run else None
            view["models"] = [m.view(chat) for m in models]
            view["overall"] = overall(models)
            view["logs"] = self._logs(STAGE_LOG_SHOWN)
            return view

    def busy_with(self, model_id: str) -> bool:
        """Whether setup is running, or *model_id* is downloading right now.

        Either way a remove could delete a model setup is about to load.
        """
        from gaia.installer.model_choice import ids_match

        with self._lock:
            if self._run is not None and self._run.state in ("running", "verifying"):
                return True
            # "verifying" outside a run is Retry testing it again.
            return any(
                ids_match(entry.id, model_id)
                and entry.state in ("downloading", "verifying")
                for entry in self._models.values()
            )

    def start(
        self,
        skip_chat_model: bool,
        gaia_lemonade: bool = False,
        allow_low_disk: bool = False,
        keep_trying: bool = False,
        extra_models: Optional[List[str]] = None,
        server_only: bool = False,
        upgrade_embedded: bool = False,
        *,
        chat_model_for: Callable[["SetupRun"], str],
    ) -> Dict[str, Any]:
        """Start setup; *gaia_lemonade* sets up and saves GAIA's own server.

        *extra_models* are pulled beside the plan's. *server_only* starts the
        model server and stops there, so the models can be chosen against it.
        *upgrade_embedded* replaces a mismatched embedded server without
        re-running first-time model setup. *chat_model_for* names the model
        the app's chat will open on, the one setup's test chat runs on.
        """
        # A cancelled run's checks may still be finishing; they would land on the new run.
        # A stopped run's load check may still be ending; its results would land
        # on the new run. Cancel kills it, so this wait is short.
        over = self._run is None or self._run.state not in ("running", "verifying")
        if over:
            self._check_idle.wait(timeout=_STOPPING_WAIT_S)
        with self._lock:
            if self._run is not None and self._run.state in ("running", "verifying"):
                raise SetupBusy("Setup is already running.")
            if not self._check_idle.is_set():
                raise SetupBusy("The last setup is still stopping.")
            if self._pulls:
                raise SetupBusy(
                    f"{', '.join(self._pulls)} is still downloading. Wait for it "
                    "or cancel it, then start setup."
                )
            run = SetupRun(
                skip_chat_model=skip_chat_model,
                users_server=None if gaia_lemonade else users_lemonade_url(),
                gaia_lemonade=gaia_lemonade,
                allow_low_disk=allow_low_disk,
                keep_trying=keep_trying,
                server_only=server_only,
                upgrade_embedded=upgrade_embedded,
                extra_models=list(extra_models or []),
                chat_model_for=chat_model_for,
            )
            command = run_command(skip_chat_model, gaia_lemonade)
            if gaia_lemonade:
                _override_env_server()
            if self._run is not None and self._run.server_only:
                self._check_log = list(self._run.stage_logs.get("check", []))
            try:
                self._proc = subprocess.Popen(
                    _gaia_argv(
                        *run_args(
                            skip_chat_model,
                            gaia_lemonade,
                            allow_low_disk,
                            keep_trying,
                            extra_models,
                            server_only,
                            upgrade_embedded,
                        )
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    # Rich wraps a pipe at 80 columns, which would cut the
                    # error line shown as the failure reason mid-sentence.
                    env=_child_env(
                        COLUMNS=str(_UNWRAPPED_COLUMNS),
                        # Setup output is shown in the browser, not a terminal.
                        NO_COLOR="1",
                        CLICOLOR="0",
                    ),
                    **windows_hidden_popen_kwargs(),
                )
            except OSError as e:
                raise SetupError(
                    f"Could not start setup ({e}). Try again; GAIA's log has the details."
                ) from e
            self._run = run
            self._models = {}
            self._test_failures = {}
            self._log_model = self._last_failed = None
            run.note(f"$ {command}")
            proc = self._proc
            view = run.view()
            self._thread = threading.Thread(
                target=self._watch, args=(run, proc), daemon=True
            )
            self._thread.start()
        return view

    def download_model(
        self, client, model_id: str, keep_trying: bool = False
    ) -> Dict[str, Any]:
        """Pull one model now, in this process: Retry, Resume, or a model the
        user skipped at setup and now wants. Lemonade resumes a partial file.

        A model that downloaded but failed its test for a reason that leaves
        the file fine (see :data:`TEST_KINDS`) is tested again instead."""
        retested = self._retest(client, model_id)
        if retested is not None:
            return retested
        on_disk = _bytes_on_disk(client, model_id)
        with self._lock:
            if self._run is not None and self._run.state in ("running", "verifying"):
                raise SetupBusy(
                    "Setup is running and downloads its models itself. Retry this "
                    "model once it finishes."
                )
            if model_id in self._pulls:
                raise SetupBusy(f"{model_id} is already downloading.")
            stop = threading.Event()
            self._pulls[model_id] = stop
            # Start from the bytes already down, so the first poll doesn't read 0
            # and the next one jump to the partial size.
            before = self._models.get(model_id)
            entry = ModelProgress(
                id=model_id,
                state="downloading",
                bytes_done=before.bytes_done if before else 0,
                bytes_total=before.bytes_total if before else 0,
                bytes_before=on_disk or None,
                log=list(before.log) if before else [],
            )
            resumed = f", resuming from {on_disk / 1e9:.2f} GB" if on_disk else ""
            entry.add_log(f"Downloading {model_id} through Lemonade{resumed}")
            self._models[model_id] = entry
        threading.Thread(
            target=self._pull_one,
            args=(client, model_id, keep_trying, stop),
            daemon=True,
        ).start()
        return self.status() or {"state": "idle"}

    def cancel(self, model_id: Optional[str] = None) -> bool:
        """Stop the run, or one model's download. Finished models and partial
        files are kept, so Resume continues from where it stopped."""
        with self._lock:
            stopped = False
            for pulling, stop in self._pulls.items():
                if model_id is None or pulling == model_id:
                    stop.set()
                    stopped = True
            if model_id is not None:
                return stopped
            if self._run is not None and self._run.state == "verifying":
                # Stop the load check; whatever it already found is dropped.
                if self._check_proc is not None and self._check_proc.poll() is None:
                    self._check_proc.kill()
                self._run.state = "cancelled"
                self._run.text = "Setup stopped"
                self._run.note("Stopped by the user while checking the models")
                # No check will finish them now: kept, as "unchecked".
                self._settle_models(None, {}, run_failed=True)
                self._end_rows(self._run, cancelled=True)
                return True
            if self._proc is None or self._run is None or self._run.state != "running":
                return stopped
            self._run.state = "cancelled"
            self._run.text = "Setup stopped"
            self._run.note("Stopped by the user")
            for entry in self._models.values():
                if entry.state in ("queued", "downloading"):
                    entry.state, entry.speed_bps = "cancelled", 0
            self._settle_models(None, {}, run_failed=True)
            self._end_rows(self._run, cancelled=True)
            self._proc.kill()
            return True

    def _retest(self, client, model_id: str) -> Optional[Dict[str, Any]]:
        """Test one downloaded model again, on its own thread; nothing is
        deleted. None when its last failure was not a test of a fine file."""
        from gaia.installer.init_command import LOAD_CHECK_JOB

        with self._lock:
            entry = self._models.get(model_id)
            if (
                entry is None
                or entry.state not in ("failed", "cancelled")
                or entry.error_kind not in TEST_KINDS
            ):
                return None
            if self._run is not None and self._run.state in ("running", "verifying"):
                raise SetupBusy(
                    "Setup is running and tests its models itself. Retry this "
                    "model once it finishes."
                )
            if model_id in self._pulls:
                raise SetupBusy(f"{model_id} is already being tested.")
            stop = threading.Event()
            self._pulls[model_id] = stop
            entry.state, entry.error, entry.speed_bps = "verifying", None, 0
            entry.add_log(f"Testing {model_id} again")
            if self._run is not None and entry.test_job != LOAD_CHECK_JOB:
                self._record_test(
                    self._run,
                    {"job": entry.test_job, "subject": model_id, "state": "running"},
                )
        threading.Thread(
            target=self._retest_one, args=(client, model_id, stop), daemon=True
        ).start()
        return self.status() or {"state": "idle"}

    def _test_failure_kind(self, model_id: str, kind: Optional[str]) -> Optional[str]:
        """Count stuck tests once, so setup and Retry use the same escalation."""
        if kind != "stuck":
            return kind
        attempts = self._test_failures.get(model_id, 0) + 1
        self._test_failures[model_id] = attempts
        return "broken" if attempts >= 2 else kind

    def _retest_one(self, client, model_id: str, stop: threading.Event) -> None:
        from gaia.installer.init_command import LOAD_CHECK_JOB, retest_model

        with self._lock:
            job = self._models[model_id].test_job
        started = time.monotonic()
        crashed = None
        try:
            passed, error, kind = retest_model(client, model_id, job)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Never leave the row "verifying": it fails, and Retry tests again.
            logger.exception(
                "Testing %s again stopped on an unexpected error", model_id
            )
            passed, error, kind = False, f"{type(e).__name__}: {e}", "stuck"
            crashed = (
                f"GAIA couldn't test {model_id} again ({error}). Its server log "
                "has the details; press Retry to test it again."
            )
        verify_run = None
        with self._lock:
            self._pulls.pop(model_id, None)
            entry = self._models.setdefault(model_id, ModelProgress(id=model_id))
            row = {"job": entry.test_job, "subject": model_id}
            if stop.is_set():
                # The test can't be interrupted; its result no longer counts.
                entry.state = "cancelled"
                entry.add_log("Cancelled")
                if self._run is not None and entry.test_job != LOAD_CHECK_JOB:
                    self._record_test(self._run, {**row, "state": "skipped"})
                return
            reported_kind = kind
            if passed:
                entry.state, entry.error, entry.error_kind = "ready", None, None
                self._test_failures.pop(model_id, None)
                entry.add_log("Test passed")
            else:
                reported_kind = self._test_failure_kind(model_id, kind)
                entry.state = "failed"
                entry.error_kind, entry.error = _test_failure(
                    model_id, reported_kind, error
                )
                entry.error = crashed or entry.error
                said = (
                    "Failed to load"
                    if entry.error_kind == "verify"
                    else "Failed its test"
                )
                entry.add_log(f"{said}: {error}")
            if self._run is not None and entry.test_job != LOAD_CHECK_JOB:
                self._record_test(
                    self._run,
                    {
                        **row,
                        "state": "passed" if passed else "failed",
                        "error": None if passed else error,
                        "kind": None if passed else reported_kind,
                        "seconds": round(time.monotonic() - started, 1),
                    },
                )
            # A passing retest only makes the prior failure eligible for the
            # normal load and real-agent verification; it does not prove setup.
            if passed and self._run is not None and self._run.state == "failed":
                tests_failed = any(
                    test.get("state") == "failed" for test in self._run.tests.values()
                )
                models_failed = any(
                    model.state == "failed" for model in self._models.values()
                )
                if self._run.failed_by_tests and not tests_failed and not models_failed:
                    verify_run = self._run
                    verify_run.state = "verifying"
                    verify_run.phase = PHASE_FINISH
                    verify_run.text = "Checking that the models load"
                    verify_run.error = None
                    verify_run.note("Rechecking setup after a successful retest")
        if verify_run is not None:
            self._verify(verify_run)

    def _pull_one(
        self, client, model_id: str, keep_trying: bool, stop: threading.Event
    ) -> None:
        from gaia.installer import model_choice
        from gaia.installer.init_command import _JsonDownloadProgress
        from gaia.llm.lemonade_client import (
            LemonadeClientError,
            ModelDownloadCancelledError,
        )
        from gaia.llm.retry import model_retry_notices

        progress = _JsonDownloadProgress(
            _QuietConsole(), _Events(self), model_id, 1, 1, None
        )

        def retrying(until: float) -> None:
            self._on_event({"event": "retrying", "model": model_id, "until": until})

        try:
            with model_retry_notices(lambda _notice: None, is_cancelled=stop.is_set):
                ok = model_choice.pull_model(
                    client,
                    model_id,
                    progress,
                    keep_trying_s=model_choice.KEEP_TRYING_S if keep_trying else 0,
                    on_retry=retrying,
                    **model_choice.pull_kwargs_for(model_id),
                )
            outcome, kind = ("ready" if ok else "failed"), "lemonade"
        except ModelDownloadCancelledError:
            outcome, kind, cause = "cancelled", None, None
        except LemonadeClientError as e:
            logger.exception("Download of %s failed", model_id)
            outcome, kind, cause = "failed", model_choice.error_kind(str(e)), str(e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Download of %s stopped on an unexpected error", model_id)
            outcome, kind, cause = "failed", "lemonade", f"{type(e).__name__}: {e}"
        else:
            cause = None
        with self._lock:
            self._pulls.pop(model_id, None)
            entry = self._models.setdefault(model_id, ModelProgress(id=model_id))
            entry.speed_bps, entry.retrying_until = 0, None
            if stop.is_set():
                outcome = "cancelled"
            entry.add_log(
                "Downloaded"
                if outcome == "ready"
                else (
                    "Cancelled"
                    if outcome == "cancelled"
                    else f"Failed: {cause or 'Lemonade reported the download failed'}"
                )
            )
            if outcome == "ready":
                entry.state, entry.bytes_done = "ready", max(
                    entry.bytes_done, entry.bytes_total
                )
            elif outcome == "cancelled":
                entry.state = "cancelled"
            elif entry.state != "failed":
                entry.state, entry.error_kind = "failed", kind
                entry.error = _failure_text(model_id, kind)

    def _on_event(self, event: Dict[str, Any]) -> None:
        """One ``gaia init --progress-json`` event (or an in-process pull's)."""
        from gaia.installer.model_choice import error_kind

        kind = event.get("event")
        with self._lock:
            if kind == "plan":
                for planned in event.get("models") or []:
                    if not planned.get("id"):
                        continue
                    entry = ModelProgress(
                        id=planned["id"],
                        state="verifying" if planned.get("downloaded") else "queued",
                        bytes_total=planned.get("bytes_total") or 0,
                        bytes_before=planned.get("bytes_on_disk") or None,
                    )
                    if planned.get("downloaded"):
                        entry.bytes_done = entry.bytes_total
                        entry.add_log("Already on this PC")
                    self._models[planned["id"]] = entry
                for skipped in event.get("skipped") or []:
                    self._models[skipped] = ModelProgress(id=skipped, state="skipped")
                if self._run is not None:
                    planned = ", ".join(
                        str(m.get("id")) for m in event.get("models") or []
                    )
                    self._run.note(f"Plan: {planned or 'no models'}")
                return
            if kind == "component":
                component, state = event.get("component"), event.get("state")
                if self._run is not None and component and state:
                    self._run.components[component] = state
                    # The last download figure belongs to the part before this one.
                    if state == "active":
                        self._run.percent = self._run.size = None
                    failure = event.get("kind") if state == "failed" else None
                    if failure:
                        self._run.component_kinds[component] = failure
                    else:
                        self._run.component_kinds.pop(component, None)
                    self._run.note(f"{component}: {state}")
                return
            if kind == "test":
                if self._run is not None:
                    test_event = dict(event)
                    subject = test_event.get("subject")
                    if test_event.get("state") == "failed" and subject:
                        test_event["kind"] = self._test_failure_kind(
                            subject, test_event.get("kind")
                        )
                    elif test_event.get("state") == "passed" and subject:
                        self._test_failures.pop(subject, None)
                    self._record_test(self._run, test_event)
                return
            model_id = event.get("model")
            if not model_id:
                if kind == "error" and self._run is not None:
                    self._run.error = event.get("message") or self._run.error
                    self._run.note(f"Error: {event.get('message')}")
                return
            entry = self._models.setdefault(model_id, ModelProgress(id=model_id))
            if kind == "progress":
                self._log_model = model_id
                if entry.state in ("queued", "cancelled", "failed", "downloading"):
                    entry.state = "downloading"
                entry.bytes_done = int(event.get("bytes_done") or 0)
                entry.bytes_total = int(event.get("bytes_total") or entry.bytes_total)
                entry.speed_bps = int(event.get("speed_bps") or 0)
                entry.retrying_until = None
                if self._run is not None:
                    self._mirror_totals(self._run)
            elif kind == "model_done":
                entry.state, entry.speed_bps = "verifying", 0
                entry.bytes_done = max(entry.bytes_done, entry.bytes_total)
                entry.add_log("Downloaded")
                if self._log_model == model_id:
                    self._log_model = None
                if self._run is not None:
                    self._run.note(f"{model_id}: downloaded")
            elif kind == "retrying":
                # The opted-in wait for a dropped connection: still downloading.
                entry.state, entry.speed_bps = "downloading", 0
                entry.error = entry.error_kind = None
                entry.retrying_until = int(event.get("until") or 0)
                entry.add_log("Connection dropped; still trying")
                if self._run is not None:
                    self._run.note(f"{model_id}: connection dropped, still trying")
            elif kind == "error":
                message = str(event.get("message") or "")
                logger.warning("Download of %s failed: %s", model_id, message)
                entry.add_log(f"Failed: {message}")
                if self._log_model == model_id:
                    self._log_model = None
                self._last_failed = model_id
                if self._run is not None:
                    self._run.note(f"{model_id}: download failed: {message}")
                entry.error_kind = error_kind(message)
                entry.state, entry.speed_bps = "failed", 0
                entry.error = _failure_text(model_id, entry.error_kind)
                if self._run is not None:
                    self._run.model_failed = True

    def _attribute(self, line: str) -> None:
        """Give a printed line to the model it is about. Caller holds the lock."""
        from gaia.installer.model_choice import ids_match

        if self._log_model is None and self._last_failed and _HINT_RE.match(line):
            if self._last_failed in self._models:
                self._models[self._last_failed].add_log(line.strip())
            return
        m = _DOWNLOADING_RE.match(line)
        if m:
            self._last_failed = None
            named = m.group(1).rstrip(".:,")
            self._log_model = next(
                (k for k in self._models if ids_match(k, named)), self._log_model
            )
        if self._log_model and self._log_model in self._models:
            self._models[self._log_model].add_log(line)

    @staticmethod
    def _record_test(run: SetupRun, event: Dict[str, Any]) -> None:
        """One Test row. Caller holds the lock."""
        # A check still finishing after Cancel must not repaint the stopped run.
        if run.state == "cancelled":
            return
        row = {
            k: event.get(k)
            for k in ("job", "subject", "state", "error", "kind", "seconds")
        }
        run.tests[(row["job"], row["subject"])] = row
        if row["state"] != "running":
            took = f" in {row['seconds']} s" if row.get("seconds") is not None else ""
            why = f": {row['error']}" if row.get("error") else ""
            subject = f" {row['subject']}" if row["subject"] else ""
            run.note(f"Test {row['job']}{subject}: {row['state']}{took}{why}")

    def _mirror_totals(self, run: SetupRun) -> None:
        """Fill the run's single bar from the per-model totals. Caller holds the lock."""
        totals = overall(list(self._models.values()))
        if totals["percent"] is None:
            return
        run.percent = totals["percent"]
        run.size = f"{totals['bytes_done'] / 1e9:.2f} GB of {totals['bytes_total'] / 1e9:.2f} GB"

    def _settle_models(
        self,
        ready: Optional[set],
        failed: Dict[str, str],
        run_failed: bool = False,
        kinds: Optional[Dict[str, str]] = None,
    ) -> None:
        """After a run: downloaded models become ready, or failed when their
        load check failed. When the run stopped before checking, they stay
        downloaded as "unchecked": nothing is wrong with them, so nothing may
        delete or re-download them. *kinds* gives a failed model's
        ``error_kind``; only ``verify`` (the default) offers a fresh download.
        Caller holds the lock."""
        for entry in self._models.values():
            if entry.id in failed:
                kind = (kinds or {}).get(entry.id, "verify")
                entry.state, entry.error_kind = "failed", kind
                entry.error = failed[entry.id]
                # Details shows the row's log, so the reason must be in it.
                said = "Failed to load" if kind == "verify" else "Failed its test"
                entry.add_log(f"{said}: {failed[entry.id]}")
            elif entry.state != "verifying":
                continue
            elif run_failed:
                entry.state, entry.error_kind, entry.error = "unchecked", None, None
            elif ready is None or entry.id in ready:
                entry.state = "ready"

    @staticmethod
    def _end_rows(run: SetupRun, cancelled: bool = False) -> None:
        """A run that ended leaves nothing working: a piece still fetching or
        a test still running is failed, or on Cancel set back. Caller holds the lock."""
        for component, state in list(run.components.items()):
            if state == "active":
                if cancelled:
                    del run.components[component]
                else:
                    run.components[component] = "failed"
        for row in run.tests.values():
            if row["state"] == "running":
                row["state"] = "skipped" if cancelled else "failed"
                if not cancelled:
                    row["error"] = "Setup stopped before this test finished."
                    row["kind"] = "stuck"

    def _watch(self, run: SetupRun, proc: subprocess.Popen) -> None:
        """Run the setup thread; an unexpected error fails the run, never strands it."""
        cause: Optional[BaseException] = None
        try:
            self._pump(run, proc)
        except Exception as e:  # pylint: disable=broad-exception-caught
            cause = e
            logger.exception("Setup thread failed")
            with self._lock:
                if run.state in ("running", "verifying"):
                    run.state = "failed"
                    run.error = (
                        "Setup stopped on an unexpected error. Details are in "
                        "the GAIA server log."
                    )
                    self._settle_models(None, {}, run_failed=True)
                    self._end_rows(run)
                    # The cause stays out of the screen, but a report needs it.
                    run.note(f"Setup failed: {type(e).__name__}: {e}")
            # Nothing reads its output any more: a child left running could
            # block on a full pipe and hold the server's models.
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    logger.error(
                        "gaia init (pid %s) did not stop when killed", proc.pid
                    )
        with self._lock:
            failed = run.state == "failed"
        if failed:
            # Its own thread: the record probes Lemonade for a second or two,
            # and the runner is free for a retry the moment the run ends.
            recording = threading.Thread(
                target=self._record_failure, args=(run, cause), daemon=True
            )
            # Started under the lock, so report() never joins an unstarted thread.
            with self._lock:
                self._recording = recording
                recording.start()

    def _record_failure(self, run: SetupRun, cause: Optional[BaseException]) -> None:
        record_id = _record_setup_failure(run, cause)
        if record_id:
            with self._lock:
                run.note(f"Problem report saved as {record_id}")

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
                failed_tests = {
                    test["subject"]: test
                    for test in run.tests.values()
                    if test.get("state") == "failed" and test.get("subject")
                }
                # A model-test-only failure can become ready after its last
                # failed row passes. Download/component failures and every
                # other exit still require a full setup retry.
                run.failed_by_tests = (
                    bool(failed_tests)
                    and not run.model_failed
                    and not any(state == "failed" for state in run.components.values())
                )
                failed_models: Dict[str, str] = {}
                failed_kinds: Dict[str, str] = {}
                for model_id, test in failed_tests.items():
                    if model_id not in self._models:
                        continue
                    kind = test.get("kind")
                    if kind == "stuck" and self._test_failures.get(model_id, 0) >= 2:
                        kind = "broken"
                    failed_kinds[model_id], failed_models[model_id] = _test_failure(
                        model_id, kind, test.get("error")
                    )
                    self._models[model_id].test_job = test.get("job")
                # Downloaded but untested models stay unchecked; failed tests
                # remain retryable so a successful retest can re-verify setup.
                self._settle_models(
                    None, failed_models, run_failed=True, kinds=failed_kinds
                )
                self._end_rows(run)
                if problem is not None:
                    # The raw exception stays in the log, behind "Details".
                    run.problem, run.error = problem, problem["message"]
                    run.note(f"Setup failed: {run.error}")
                    return
                run.error = (
                    f"Setup stopped (exit {code}): "
                    f"{run.error or _last_line(chr(10).join(run.log))}"
                )
                run.note(f"Setup failed: {run.error}")
                return
            if run.server_only or run.upgrade_embedded:
                # Nothing downloaded, so there is nothing to load; an update keeps the models.
                run.state, run.text = "ready", (
                    "The local model server is running"
                    if run.server_only
                    else "Lemonade Server upgraded"
                )
                run.note(run.text)
                return
            run.state = "verifying"
            run.phase = PHASE_FINISH
            run.text = "Checking that the models load"
            run.percent = run.size = None
        self._verify(run)

    def _load_check(self, run: SetupRun) -> bool:
        """Load every model and record the results; True when setup is ready."""

        def started(proc: subprocess.Popen) -> None:
            with self._lock:
                self._check_proc = proc
                if run.state == "cancelled":
                    proc.kill()

        try:
            status = check(run.skip_chat_model, load=True, on_start=started, mark=False)
        except SetupError as e:
            with self._lock:
                if run.state == "cancelled":
                    return False
                run.state, run.error = "failed", str(e)
                run.note(f"Load check failed: {e}")
                self._settle_models(None, {}, run_failed=True)
            return False
        from gaia.installer.init_command import LOAD_CHECK_JOB

        loads = status.get("models") or []
        failed: Dict[str, str] = {}
        kinds: Dict[str, str] = {}
        for m in loads:
            if not m.get("loaded"):
                # Only a broken file is offered a fresh download.
                kinds[m["id"]], failed[m["id"]] = _test_failure(
                    m["id"], m.get("kind"), m.get("error")
                )
        with self._lock:
            # Cancelled during the check: its results belong to no run any more.
            if run.state == "cancelled":
                return False
            run.status = status
            for m in loads:
                outcome = (
                    "loads"
                    if m.get("loaded")
                    else f"failed ({m.get('kind') or 'broken'}): {m.get('error')}"
                )
                run.note(f"Load check {m['id']}: {outcome}")
                entry = self._models.get(m["id"])
                if entry is not None and not m.get("loaded"):
                    # Retry repeats this load; the friendly line may drop its words.
                    entry.test_job = LOAD_CHECK_JOB
                    entry.add_log(f"Load check: {m.get('error')}")
            loaded = {m["id"] for m in loads if m.get("loaded")}
            # gaia init's Test step ran what the user ticked; this check only
            # covers the plan. Any other model it skipped stays unverified.
            checked = {m["id"] for m in loads}
            # An extra's own test result stands; only an untested one counts as loaded.
            tested_failed = {
                t["subject"]: t
                for t in run.tests.values()
                if t.get("state") == "failed" and t.get("subject") in run.extra_models
            }
            for i, test in tested_failed.items():
                if i in checked:
                    continue
                kinds[i], failed[i] = _test_failure(
                    i, test.get("kind"), test.get("error")
                )
                if i in self._models:
                    self._models[i].test_job = test.get("job")
                    # The friendly line may leave out the test's own words.
                    self._models[i].add_log(
                        f"Test {test.get('job')}: {test.get('error')}"
                    )
            loaded |= {
                i
                for i in run.extra_models
                if i not in checked and i not in tested_failed
            }
            self._settle_models(
                loaded if loads else None,
                failed,
                run_failed=not status.get("ready") and not loads,
                kinds=kinds,
            )
            ready = bool(status.get("ready"))
            if not ready:
                run.state = "failed"
                run.failed_by_tests = bool(tested_failed) and not (
                    status.get("reasons") or failed
                )
                run.error = "; ".join(status.get("reasons") or []) or (
                    "Setup finished but GAIA is still not ready."
                )
                run.note(f"Not ready: {run.error}")
        return ready

    def _verify(self, run: SetupRun) -> None:
        self._check_idle.clear()
        try:
            ready = self._load_check(run)
        finally:
            self._check_idle.set()
        if not ready:
            return
        problem = self._test_agent(run)
        with self._lock:
            if run.state == "cancelled":
                return
            if problem:
                run.state, run.error, run.failed_by_tests = "failed", problem, False
            else:
                run.state, run.text = "ready", "GAIA is ready"
                mark_initialized()
            run.note(run.error if problem else run.text)

    def _test_agent(self, run: SetupRun) -> Optional[str]:
        """One real turn through the agent, on the model chat will open on:
        every model loading doesn't prove GAIA answers on that one."""
        from gaia.llm.lemonade_client import (
            LemonadeClientError,
            is_cloud_model_with_catalog,
        )

        started = time.monotonic()
        from gaia.config import GaiaConfigError

        if run.chat_model_for is None:
            raise SetupError("This setup run doesn't know which model chat opens on.")
        try:
            model = run.chat_model_for(run)
        except (SetupError, GaiaConfigError) as e:
            logger.warning("Setup can't tell which model chat opens on: %s", e)
            model, unknown = "", str(e)
        else:
            unknown = None
        try:
            cloud = model.startswith("claude-") or is_cloud_model_with_catalog(model)
        except LemonadeClientError as e:
            cloud = False
            if unknown is None:
                unknown = (
                    f"GAIA could not classify the chat model {model!r} because "
                    f"the Lemonade catalog was unavailable: {e}"
                )
        problem = unknown
        if problem is None and run.skip_chat_model and not cloud:
            # A cloud setup chats on the cloud model connecting saved.
            problem = NO_CLOUD_MODEL
        # The row names the model the chat test runs on.
        subject = None if problem else model
        with self._lock:
            self._record_test(
                run, {"job": "agent", "subject": subject, "state": "running"}
            )
            if problem is None:
                run.note(f"Testing a chat on {model}")
        if problem is None:
            why = _agent_turn_problem(model)
            if why == AGENT_MISSING:
                problem = why
            elif why is not None:
                logger.warning("Setup's test chat on %s failed: %s", model, why)
                problem = _agent_turn_failure(model)
                with self._lock:
                    run.note(f"Test chat on {model} failed: {why}")
        with self._lock:
            self._record_test(
                run,
                {
                    "job": "agent",
                    "subject": subject,
                    "state": "failed" if problem else "passed",
                    "error": problem,
                    "kind": (
                        None
                        if not problem
                        else "missing" if problem == AGENT_MISSING else "agent"
                    ),
                    "seconds": round(time.monotonic() - started, 1),
                },
            )
        return problem

    def _consume(self, run: SetupRun, line: str) -> None:
        # Defend the browser-facing log against a program that ignores NO_COLOR.
        line = _ANSI_RE.sub("", line).strip()
        if not line:
            return
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if isinstance(event, dict) and "event" in event:
                # A killed run's last lines must not bring cancelled rows back.
                with self._lock:
                    if run.state == "cancelled":
                        return
                self._on_event(event)
                return
        # init words its advice for a terminal; this log is read in the app.
        line = surface_wording(line, "agent_ui")
        progress = describe(line)
        with self._lock:
            run.note(line, printed=True)
            self._attribute(line)
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
