# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The bridge's files: what was set up, what is running, and the one-copy lock.

* ``bridge.json`` — written at setup: who the owner is, which bot, autostart.
* ``status.json`` — written by the running bridge: its state and pid, read by
  the CLI, the TUI and the Agent UI.
* ``bridge.lock`` — an OS advisory lock held for the bridge's lifetime. It is
  the authority on "running": the OS drops it when the process dies, so a
  status file a crash left behind cannot claim a live bridge.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from gaia.logger import get_logger

log = get_logger(__name__)

STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
#: Waiting for the owner to DM the one-time code shown on this PC.
STATE_PAIRING = "pairing"
STATE_CONNECTED = "connected"
#: Another PC holds the lease for this bot; this bridge answers nothing.
STATE_TAKEN_OVER = "taken_over"
#: Discord refused the token — it was reset or the bot was deleted.
STATE_TOKEN_INVALID = "token_invalid"
#: The OS keyring could not be read (a locked Secret Service at login).
STATE_CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
#: The token is the ``mcp-discord`` connector's bot; two sessions would race.
STATE_CONFLICT = "conflict"
STATE_ERROR = "error"

#: States only a live bridge can be in.
LIVE_STATES = frozenset(
    {STATE_STARTING, STATE_PAIRING, STATE_CONNECTED, STATE_TAKEN_OVER}
)


class BridgeStateError(RuntimeError):
    """A bridge file exists but cannot be read."""


class CorruptStateFile(BridgeStateError):
    """A bridge file exists but its bytes are not the JSON object it should hold."""


class DamagedStateFile(BridgeStateError):
    """A corrupt bridge file was moved aside; *moved_to* keeps its bytes."""

    def __init__(self, message: str, moved_to: Path) -> None:
        super().__init__(message)
        self.moved_to = moved_to


class BridgeAlreadyRunning(RuntimeError):
    """Another Discord bridge holds the lock on this machine."""


def discord_dir() -> Path:
    """Root of the bridge's files; honours ``GAIA_CONFIG_DIR`` and ``GAIA_HOME``."""
    from gaia.home import gaia_home

    return gaia_home() / "discord"


def inbox_dir() -> Path:
    """Where files shared over Discord are saved for the agent to read."""
    return discord_dir() / "inbox"


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write atomically so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            # Without it a crash can keep the rename but not the bytes: a file of zeros.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """Return the object at *path*, ``None`` when absent; raise when unusable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise BridgeStateError(
            f"Cannot read {path}: {e}. Check its permissions."
        ) from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CorruptStateFile(
            f"{path} is not valid JSON: {e}. Reconnect Discord to rewrite it."
        ) from e
    if not isinstance(data, dict):
        raise CorruptStateFile(
            f"{path} must hold a JSON object, got {type(data).__name__}. "
            f"Reconnect Discord to rewrite it."
        )
    return data


def read_json_or_set_aside(path: Path, what: str) -> Optional[Dict[str, Any]]:
    """Like :func:`read_json`, but move a corrupt file aside and say so.

    For files the bridge can start over without (schedules, watches, status):
    raises :class:`DamagedStateFile` once, naming where the bytes went; the next
    read finds no file and starts empty.
    """
    try:
        return read_json(path)
    except CorruptStateFile as e:
        moved = path.with_name(f"{path.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(path, moved)
        except FileNotFoundError:
            # Another reader moved it first; it reports the damage.
            return read_json(path)
        except OSError as move_error:
            raise BridgeStateError(
                f"{path} is damaged and could not be moved aside: {move_error}. "
                f"Delete it to start over."
            ) from e
        log.error("Moved damaged %s to %s: %s", path, moved, e)
        raise DamagedStateFile(
            f"Your saved {what} could not be read — the file was damaged, most "
            f"likely because the PC shut down while saving it. GAIA kept a copy "
            f"at {moved} and starts over with none.",
            moved,
        ) from e


def _known(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class BridgeConfig:
    """What setup learned. Never holds the token."""

    #: The one Discord user the bridge answers, set by pairing.
    owner_id: str = ""
    owner_name: str = ""
    #: The owner's DM channel with the bot, learned when they paired.
    dm_channel_id: str = ""
    bot_id: str = ""
    bot_name: str = ""
    application_id: str = ""
    #: Names this PC in the lease, so two PCs on one bot cannot both answer.
    instance_id: str = ""
    #: Start the bridge whenever the TUI or Agent UI opens.
    autostart: bool = False
    #: Chat model the agent runs; empty means the agent's own default.
    model: str = ""
    #: Command that starts the agent; empty means the installed gaia-agent.
    agent_command: str = ""

    @property
    def connected(self) -> bool:
        """A token was accepted; the bridge can start."""
        return bool(self.bot_id)

    @property
    def paired(self) -> bool:
        return bool(self.owner_id)


def config_path() -> Path:
    return discord_dir() / "bridge.json"


def load_config() -> BridgeConfig:
    data = read_json(config_path())
    if data is None:
        return BridgeConfig()
    try:
        return BridgeConfig(**_known(BridgeConfig, data))
    except TypeError as e:
        raise BridgeStateError(
            f"{config_path()} is not usable: {e}. Reconnect Discord to rewrite it."
        ) from e


def save_config(config: BridgeConfig) -> None:
    write_json(config_path(), asdict(config))


def clear_config() -> None:
    config_path().unlink(missing_ok=True)


@dataclass
class BridgeStatus:
    """What the running bridge last reported."""

    state: str = STATE_STOPPED
    pid: int = 0
    #: Human-readable reason for the state, shown as-is by the apps.
    detail: str = ""
    updated_at: float = 0.0
    #: Newest DM id the bridge handled; on the next fresh connect, DMs after it
    #: are ones that arrived while it was offline.
    last_message_id: str = ""
    #: The code to DM the bot while pairing, and when it stops working.
    pairing_code: str = ""
    pairing_expires_at: float = 0.0
    #: Strangers already told they are not authorized, so each hears it once.
    refused_user_ids: List[str] = field(default_factory=list)
    #: ``[channel_id, message_id, kind]`` of placeholders and approval prompts
    #: still open, so a bridge started after a crash can close them.
    open_messages: List[List[str]] = field(default_factory=list)


def status_path() -> Path:
    return discord_dir() / "status.json"


def _read_status_file() -> Optional[Dict[str, Any]]:
    try:
        return read_json_or_set_aside(status_path(), "bridge status")
    except DamagedStateFile:
        # Only runtime state; the bridge rewrites it, and the move is logged.
        return None


def read_status() -> BridgeStatus:
    """The last reported status, corrected by the lock.

    A live state with no lock held means the bridge died without saying so.
    """
    data = _read_status_file()
    status = BridgeStatus(**_known(BridgeStatus, data)) if data else BridgeStatus()
    if status.state == STATE_STARTING and _starting_process_alive(status):
        # Spawned, still importing: it takes the lock only once it is up.
        return status
    if status.state in LIVE_STATES and not is_running():
        status.state = STATE_STOPPED
        status.detail = "The Discord bridge stopped unexpectedly."
        status.pid = 0
    return status


#: A start that has not taken the lock within this long has failed.
STARTING_GRACE_SECONDS = 120.0


def _starting_process_alive(status: BridgeStatus) -> bool:
    if not status.pid or time.time() - status.updated_at > STARTING_GRACE_SECONDS:
        return False
    import psutil

    return psutil.pid_exists(status.pid)


def write_status(state: str, detail: str = "", **extra: Any) -> BridgeStatus:
    """Record the bridge's state, keeping fields this call does not name.

    Locked: the bridge and the CLI both read-modify-write this file.
    """
    from gaia.utils.file_lock import file_lock

    with file_lock(status_path()):
        data = _read_status_file() or {}
        status = BridgeStatus(**_known(BridgeStatus, data))
        status.state = state
        status.detail = detail
        status.pid = os.getpid() if state in LIVE_STATES else 0
        status.updated_at = time.time()
        for key, value in extra.items():
            setattr(status, key, value)
        write_json(status_path(), asdict(status))
    return status


def always_path() -> Path:
    """The grants the paired user answered "Always allow" to, kept across restarts.

    The agent keeps an "always" only for its own process; the bridge restarts
    it, so without this file every restart asks again. Holds the agent's own
    grant keys (tool plus exact target), never the shorter prompt label, and
    the account that gave them: another account's consent never carries over.
    """
    return discord_dir() / "always.json"


def _always_data() -> Dict[str, Any]:
    data = read_json_or_set_aside(always_path(), '"Always allow" answers') or {}
    grants = data.get("grants")
    return {
        "owner_id": str(data.get("owner_id") or ""),
        "grants": grants if isinstance(grants, dict) else {},
    }


def always_keys(owner_id: str) -> Set[str]:
    """Grant keys *owner_id* answered "Always allow" to; none for anyone else."""
    data = _always_data()
    if not owner_id or data["owner_id"] != owner_id:
        return set()
    return {k for k in data["grants"] if isinstance(k, str) and k}


def always_labels() -> List[str]:
    """What each remembered answer allows, as its prompt described it."""
    return [str(label) for label in _always_data()["grants"].values()]


def remember_always(owner_id: str, keys: Sequence[str], label: str) -> None:
    from gaia.utils.file_lock import file_lock

    with file_lock(always_path()):
        data = _always_data()
        grants = data["grants"] if data["owner_id"] == owner_id else {}
        for key in keys:
            grants[key] = label
        write_json(always_path(), {"owner_id": owner_id, "grants": grants})


def forget_always() -> int:
    """Forget every "Always allow" answer; returns how many there were."""
    from gaia.utils.file_lock import file_lock

    with file_lock(always_path()):
        try:
            count = len(set(always_labels()))
        except DamagedStateFile:
            count = 0
        always_path().unlink(missing_ok=True)
    return count


def lock_path() -> Path:
    return discord_dir() / "bridge.lock"


def start_lock_target() -> Path:
    """Serializes starting the bridge, so two apps never spawn two at once."""
    return discord_dir() / "start"


class BridgeLock:
    """Exclusive, process-lifetime lock: at most one bridge per machine.

    Two bridges on one token both receive every DM and would answer each twice.
    """

    def __init__(self) -> None:
        self._fd: Optional[int] = None

    def acquire(self) -> "BridgeLock":
        from gaia.daemon.lock import try_lock

        path = lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        if not try_lock(fd):
            os.close(fd)
            raise BridgeAlreadyRunning(
                "A Discord bridge is already running on this machine."
            )
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        from gaia.daemon.lock import unlock

        try:
            unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "BridgeLock":
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


def is_running() -> bool:
    """True while some process holds the bridge lock."""
    probe = BridgeLock()
    try:
        probe.acquire()
    except BridgeAlreadyRunning:
        return True
    probe.release()
    return False
