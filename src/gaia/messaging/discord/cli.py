# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia discord`` — every rule the TUI and the Agent UI rely on.

The apps are thin readers of ``status --json`` and callers of these functions:
what "connected", "running" and "paired" mean is decided here once.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from gaia.logger import get_logger
from gaia.messaging import onboarding as offer
from gaia.messaging.discord import credentials, state

log = get_logger(__name__)

PORTAL_URL = "https://discord.com/developers/applications"

SETUP_STEPS = (
    "1. Open the Discord Developer Portal and click New Application.",
    "2. Open Bot, click Reset Token, and copy the token.",
    "3. Paste it here. GAIA then shows an invite link and a pairing code.",
)

#: How long ``start --detach`` waits for the bridge to report in.
DETACH_WAIT_SECONDS = 90.0
#: How long ``stop`` waits for a graceful exit before ending the process.
STOP_WAIT_SECONDS = 15.0

_DISCORD_ENTITY = "app:discord"
_DISCORD_CONTENT = offer.installed_content_pattern("discord")


# ----------------------------------------------------------------------
# Paths and small helpers
# ----------------------------------------------------------------------


def _onboarding_path() -> Path:
    return state.discord_dir() / "onboarding.json"


def stop_request_path() -> Path:
    return state.discord_dir() / "stop.request"


def log_path() -> Path:
    return state.discord_dir() / "bridge.log"


def detect_discord() -> bool:
    """Whether the Discord desktop app is installed, via the shared inventory."""
    from gaia.agents.base.discovery import SystemDiscovery

    return offer.app_is_installed(
        SystemDiscovery().scan_installed_apps(), _DISCORD_ENTITY, _DISCORD_CONTENT
    )


def split_command(command: str) -> List[str]:
    """Split ``--agent-command``; on Windows keep backslashes, drop quotes."""
    import shlex

    if os.name != "nt":
        return shlex.split(command)
    return [part.strip('"') for part in shlex.split(command, posix=False)]


def _connector_bot_id() -> Optional[str]:
    """Bot id behind the ``mcp-discord`` connector's token, if one is saved."""
    from gaia.messaging.discord.transport import DiscordTokenError, verify_token

    token = credentials.connector_token()
    if not token:
        return None
    try:
        return verify_token(token)["bot_id"]
    except DiscordTokenError:
        return None


def conflict_message() -> str:
    return (
        "This bot is also used by the Discord connector. Two connections on one "
        "bot both receive every DM, so the bridge needs its own: create a second "
        "application in the Developer Portal and paste its token."
    )


# ----------------------------------------------------------------------
# connect / setup
# ----------------------------------------------------------------------


def connect(token: str) -> Dict[str, Any]:
    """Validate and store a bot token. Returns what the apps show next.

    Raises:
        ValueError: the token is empty or belongs to the connector's bot.
        DiscordTokenError: Discord refused it.
        RuntimeError: Discord could not be reached.
    """
    from gaia.messaging.discord.transport import (
        bot_page_url,
        invite_url,
        verify_token,
    )

    token = token.strip()
    if not token:
        raise ValueError(
            "No token was given. Paste the bot token from the Developer Portal."
        )
    identity = verify_token(token)
    if identity["bot_id"] == _connector_bot_id():
        raise ValueError(conflict_message())

    config = state.load_config()
    if config.bot_id != identity["bot_id"]:
        # A different bot means a different DM channel; pair again.
        config.owner_id = config.owner_name = config.dm_channel_id = ""
    config.bot_id = identity["bot_id"]
    config.bot_name = identity["bot_name"]
    config.application_id = identity["application_id"]
    config.autostart = True
    credentials.save(token)
    state.save_config(config)
    offer.record_decision(
        offer.STATE_CONNECTED,
        detected=True,
        path=_onboarding_path(),
        team_name=identity["bot_name"],
    )
    return {
        "connected": True,
        "bot_name": identity["bot_name"],
        "application_id": identity["application_id"],
        "invite_url": invite_url(identity["application_id"]),
        "bot_page_url": bot_page_url(identity["application_id"]),
        "bot_public": identity["bot_public"],
    }


def run_connect(*, stdin=None, emit: Callable[[str], None] = print) -> int:
    """Read the token from stdin (never argv: ``ps`` shows argv to everyone)."""
    from gaia.messaging.discord.transport import DiscordTokenError

    stream = stdin if stdin is not None else sys.stdin
    try:
        result = connect(stream.readline() or "")
    except (ValueError, DiscordTokenError, RuntimeError) as e:
        emit(json.dumps({"connected": False, "error": str(e)}))
        return 1
    emit(json.dumps(result))
    return 0


def run_setup(
    *,
    prompt: Callable[[str], str] = getpass.getpass,
    emit: Callable[[str], None] = print,
    open_browser: bool = True,
    model: Optional[str] = None,
) -> int:
    """Interactive setup for a terminal: token, start, then the pairing code."""
    from gaia.messaging.discord.transport import DiscordTokenError

    emit("")
    emit("Connecting GAIA to Discord. Discord bots can only be created in its")
    emit("Developer Portal, so this part is not automatic — about a minute.")
    emit("")
    for step in SETUP_STEPS:
        emit(f"  {step}")
    emit(f"\n  {PORTAL_URL}\n")
    if open_browser:
        _open_browser(PORTAL_URL, emit)
    try:
        token = prompt("Bot token (hidden): ")
    except EOFError:
        emit(
            "\n❌ No token was entered: setup needs a terminal to type it in. "
            "Run `gaia discord setup` in a terminal, or pipe the token to "
            "`gaia discord connect`."
        )
        return 1
    try:
        result = connect(token)
    except (ValueError, DiscordTokenError, RuntimeError) as e:
        emit(f"\n❌ {e}")
        return 1
    emit(f"\n✅ Token accepted for {result['bot_name']}. It is in your OS keyring.")
    if result["bot_public"]:
        emit(
            "   Public Bot is on, so anyone can add this bot to a server and DM it. "
            "They will only be refused, but you can turn it off under Bot."
        )
    code = start_detached(model=model, emit=lambda _: None)
    info = status(detect=False)
    emit("")
    emit("Next, add the bot to a server you're in (Discord only lets you DM a bot")
    emit("you share a server with):")
    emit(f"  {result['invite_url']}")
    if info["state"] == state.STATE_PAIRING and info["pairing_code"]:
        emit("")
        emit(
            f"Then DM the bot this code to pair it with your account:  {info['pairing_code']}"
        )
        emit("Whoever sends it is the only person GAIA will answer.")
    elif code != 0:
        emit(f"\n❌ The bridge did not start: {info['detail']}")
        return 1
    return 0


def _open_browser(url: str, emit: Callable[[str], None]) -> None:
    try:
        webbrowser.open(url)
    except webbrowser.Error as e:
        log.debug("Could not open a browser: %s", e)
        emit("(Could not open a browser — use the link above.)")


# ----------------------------------------------------------------------
# start / stop
# ----------------------------------------------------------------------


def _acquire_lock(retry_seconds: float = 2.0) -> state.BridgeLock:
    """Take the lock, riding out a status probe that holds it for an instant."""
    deadline = time.monotonic() + retry_seconds
    while True:
        try:
            return state.BridgeLock().acquire()
        except state.BridgeAlreadyRunning:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def run_foreground(
    *,
    agent_command: Optional[str] = None,
    model: Optional[str] = None,
    take_over: bool = False,
    deny_gated_tools: bool = False,
    upload_roots: Optional[Sequence[str]] = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Run the bridge in this process until stopped."""
    from gaia.messaging.discord.adapter import DiscordAdapter
    from gaia.messaging.discord.transport import DiscordTokenError, GatewayTransport

    try:
        lock = _acquire_lock()
    except state.BridgeAlreadyRunning as e:
        emit(f"{e} Stop it first, or leave it running.")
        return 0
    try:
        config = state.load_config()
        if not config.connected:
            state.write_status(state.STATE_ERROR, "Discord is not connected yet.")
            emit("❌ Discord is not connected yet. Paste a bot token first.")
            return 2
        try:
            token = credentials.load()
        except credentials.DiscordCredentialsError as e:
            state.write_status(state.STATE_CREDENTIALS_UNAVAILABLE, str(e))
            emit(f"❌ {e}")
            return 2
        try:
            shared = config.bot_id == _connector_bot_id()
        except RuntimeError as e:
            state.write_status(state.STATE_ERROR, str(e))
            emit(f"❌ {e}")
            return 1
        if shared:
            state.write_status(state.STATE_CONFLICT, conflict_message())
            emit(f"❌ {conflict_message()}")
            return 2
        if model or agent_command:
            config.model = model or config.model
            config.agent_command = agent_command or config.agent_command
            state.save_config(config)

        agent_argv: Optional[List[str]] = None
        if config.agent_command:
            agent_argv = split_command(config.agent_command)
        stop_request_path().unlink(missing_ok=True)
        state.write_status(state.STATE_STARTING, "Connecting to Discord…")
        adapter = DiscordAdapter(
            config,
            agent_argv=agent_argv,
            upload_roots=upload_roots,
            deny_gated_tools=deny_gated_tools,
        )
        transport = GatewayTransport()
        adapter.transport = transport
        try:
            adapter.start_agent()
        except Exception as e:  # noqa: BLE001 - surfaced as the bridge's status
            state.write_status(state.STATE_ERROR, f"The agent could not start: {e}")
            emit(f"❌ The agent could not start: {e}")
            return 1

        watcher = threading.Thread(
            target=_watch_stop_request, args=(transport,), daemon=True
        )
        watcher.start()
        emit("Discord bridge starting. Stop it from the GAIA app or with Ctrl-C.")
        try:
            transport.run(token, adapter, take_over=take_over)
        except DiscordTokenError as e:
            state.write_status(state.STATE_TOKEN_INVALID, str(e))
            emit(f"❌ {e}")
            return 1
        except KeyboardInterrupt:
            emit("\nStopping…")
        except Exception as e:  # noqa: BLE001 - surfaced as the bridge's status
            log.exception("Discord bridge failed")
            state.write_status(state.STATE_ERROR, f"The Discord bridge stopped: {e}")
            emit(f"❌ The Discord bridge stopped: {e}")
            return 1
        finally:
            adapter.close()
        if state.read_status().state in state.LIVE_STATES:
            state.write_status(state.STATE_STOPPED, "Stopped.")
        return 0
    finally:
        lock.release()
        stop_request_path().unlink(missing_ok=True)


def _watch_stop_request(transport: Any) -> None:
    """Close the session when the apps ask; a file works on every platform."""
    path = stop_request_path()
    while True:
        if path.exists():
            log.info("Discord bridge stop requested")
            transport.stop()
            return
        time.sleep(0.5)


def _spawn_argv(
    model: Optional[str], take_over: bool, agent_command: Optional[str]
) -> List[str]:
    argv = [sys.executable, "-m", "gaia.cli", "discord", "start"]
    if model:
        argv += ["--model", model]
    if take_over:
        argv.append("--take-over")
    if agent_command:
        argv += ["--agent-command", agent_command]
    return argv


def start_detached(
    *,
    model: Optional[str] = None,
    take_over: bool = False,
    agent_command: Optional[str] = None,
    emit: Callable[[str], None] = print,
) -> int:
    """Start the bridge as its own process and wait until it reports in.

    A start is also the user's word that it should come back with the apps.

    It outlives the TUI or Agent UI that started it: a hidden console of its
    own (never ``DETACHED_PROCESS``, which pops a window for every shell command
    the agent runs) and outside the caller's Windows job object.
    """
    from gaia.utils.file_lock import file_lock

    # One start at a time across processes: the TUI and the Agent UI both
    # autostart at launch, and two spawns would each report the other as failed.
    with file_lock(state.start_lock_target(), timeout=DETACH_WAIT_SECONDS + 30):
        _set_autostart(True)
        return _start_detached_locked(
            model=model, take_over=take_over, agent_command=agent_command, emit=emit
        )


def _start_detached_locked(
    *,
    model: Optional[str],
    take_over: bool,
    agent_command: Optional[str],
    emit: Callable[[str], None],
) -> int:
    from gaia.utils.terminal import background_popen_kwargs, popen_outliving_job

    if state.is_running():
        emit(json.dumps(status(detect=False)))
        return 0
    config = state.load_config()
    if not config.connected:
        emit("❌ Discord is not connected yet. Paste a bot token first.")
        return 2
    state.write_status(state.STATE_STARTING, "Starting…")
    log_file = log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w", encoding="utf-8") as out:
        proc = popen_outliving_job(
            _spawn_argv(model, take_over, agent_command),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            **background_popen_kwargs(),
        )
    # Recorded with the child's pid so status reads "starting", not "stopped",
    # while it imports and before it takes the lock.
    state.write_status(state.STATE_STARTING, "Starting…", pid=proc.pid)
    deadline = time.monotonic() + DETACH_WAIT_SECONDS
    while time.monotonic() < deadline:
        info = state.read_status()
        if proc.poll() is not None:
            if state.is_running():
                # It found another bridge already up and left; that one serves.
                break
            if info.state in (state.STATE_STARTING, state.STATE_STOPPED):
                state.write_status(
                    state.STATE_ERROR,
                    f"The Discord bridge exited right away (code {proc.returncode}). "
                    f"Its log is at {log_file}.",
                )
            break
        if info.state not in (state.STATE_STARTING, state.STATE_STOPPED):
            break
        time.sleep(0.25)
    info = status(detect=False)
    emit(json.dumps(info))
    return 0 if info["state"] in state.LIVE_STATES else 1


def _set_autostart(value: bool) -> None:
    config = state.load_config()
    if config.connected and config.autostart != value:
        config.autostart = value
        state.save_config(config)


def stop(
    *, wait_seconds: float = STOP_WAIT_SECONDS, stay_stopped: bool = False
) -> bool:
    """Ask a running bridge to stop; end it if it does not. True if it stopped.

    *stay_stopped* is the user's own Stop: the apps then leave it stopped at
    their next launch instead of bringing it back.
    """
    if stay_stopped:
        _set_autostart(False)
    if not state.is_running():
        return True
    path = stop_request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not state.is_running():
            return True
        time.sleep(0.25)
    pid = state.read_status().pid
    if pid:
        _kill_tree(pid)
    time.sleep(0.5)
    stopped = not state.is_running()
    if stopped:
        state.write_status(state.STATE_STOPPED, "Stopped.")
        path.unlink(missing_ok=True)
    return stopped


def _kill_tree(pid: int) -> None:
    """End the bridge and its agent child."""
    import psutil

    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return
    if "discord" not in cmdline or "start" not in cmdline:
        log.warning("Pid %s is not a Discord bridge (%s); not killing it", pid, cmdline)
        return
    for child in proc.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.kill()
    except psutil.NoSuchProcess:
        pass


def run_stop(*, emit: Callable[[str], None] = print) -> int:
    if stop(stay_stopped=True):
        emit("Stopped the Discord bridge.")
        return 0
    emit(f"❌ The Discord bridge did not stop. {stop_failed_hint()}")
    return 1


def ensure_started(*, emit: Callable[[str], None] = lambda _: None) -> Optional[int]:
    """Start the bridge if it is set to autostart and is not running.

    Called by the TUI at launch and the Agent UI backend at startup. Returns
    ``None`` when there was nothing to do.
    """
    config = state.load_config()
    if not (config.connected and config.autostart) or state.is_running():
        return None
    if credentials.load_optional() is None:
        state.write_status(
            state.STATE_CREDENTIALS_UNAVAILABLE,
            "The Discord token could not be read from the OS keyring. Unlock it, "
            "then start Discord again.",
        )
        return 2
    return start_detached(emit=emit)


def reset_pairing() -> None:
    """Forget the paired user; the next start shows a new code."""
    config = state.load_config()
    config.owner_id = config.owner_name = config.dm_channel_id = ""
    state.save_config(config)
    # Consent was given by the old pairing's account.
    state.forget_always()


def stop_failed_hint() -> str:
    """How to end a bridge that would not stop, in this OS's own words."""
    tool = {
        "win32": "Task Manager",
        "darwin": "Activity Monitor",
    }.get(sys.platform, "your system monitor")
    return (
        f"End the process running `gaia discord start` in {tool} (or restart "
        "the PC), then try again."
    )


def disconnect() -> None:
    """Stop the bridge and forget the token, the pairing and the status.

    Raises:
        RuntimeError: the bridge did not stop, so nothing was forgotten. A
            running bridge already holds the token and keeps answering; wiping
            its files would show "not connected" while it still runs.
    """
    if not stop():
        raise RuntimeError(
            "The Discord bridge did not stop, so it was not disconnected. "
            + stop_failed_hint()
        )
    credentials.clear()
    state.clear_config()
    state.status_path().unlink(missing_ok=True)
    state.forget_always()
    # Recorded as seen with Discord present, so the setup offer does not come
    # straight back at the next launch on a PC that has Discord installed.
    offer.record_decision(offer.STATE_SKIPPED, detected=True, path=_onboarding_path())


# ----------------------------------------------------------------------
# status / decline
# ----------------------------------------------------------------------


def status(detect: bool = True) -> Dict[str, Any]:
    """Everything a status row or settings card needs, as plain data."""
    from gaia.messaging.discord.transport import (
        bot_page_url,
        bot_profile_url,
        invite_url,
    )

    config = state.load_config()
    current = state.read_status()
    decision = offer.load_state(_onboarding_path(), "Discord")
    detected = detect_discord() if detect else False
    pairing = current.state == state.STATE_PAIRING
    return {
        "connected": config.connected,
        "paired": config.paired,
        "running": state.is_running(),
        "state": current.state,
        "detail": current.detail,
        "bot_name": config.bot_name,
        "owner_name": config.owner_name,
        "autostart": config.autostart,
        "always_allowed": sorted(set(state.always_labels())) if config.paired else [],
        "model": config.model,
        "portal_url": PORTAL_URL,
        "bot_page_url": (
            bot_page_url(config.application_id) if config.application_id else ""
        ),
        "invite_url": (
            invite_url(config.application_id) if config.application_id else ""
        ),
        "bot_profile_url": bot_profile_url(config.bot_id) if config.bot_id else "",
        "pairing_code": current.pairing_code if pairing else "",
        "pairing_expires_at": current.pairing_expires_at if pairing else 0.0,
        "discord_installed": detected,
        "should_offer_setup": not config.connected
        and offer.should_offer(decision, detected),
    }


def run_status(*, as_json: bool = False, emit: Callable[[str], None] = print) -> int:
    info = status()
    if as_json:
        emit(json.dumps(info, indent=2))
        return 0
    emit(f"Connected:  {'yes — ' + info['bot_name'] if info['connected'] else 'no'}")
    emit(f"Paired:     {info['owner_name'] or 'no'}")
    emit(
        f"Bridge:     {info['state']}{' — ' + info['detail'] if info['detail'] else ''}"
    )
    if info["pairing_code"]:
        emit(f"Pair code:  {info['pairing_code']}  (DM it to the bot)")
    if info["invite_url"] and not info["paired"]:
        emit(f"Invite:     {info['invite_url']}")
    return 0


def run_decline(*, never: bool = False, emit: Callable[[str], None] = print) -> int:
    detected = detect_discord()
    offer.record_decision(
        offer.STATE_NEVER if never else offer.STATE_SKIPPED,
        detected=detected,
        path=_onboarding_path(),
    )
    emit("Won't ask about Discord again." if never else "Skipped Discord setup.")
    return 0


# ----------------------------------------------------------------------
# watches — GitHub repos the bridge alerts on
# ----------------------------------------------------------------------


def _emit_json(emit: Callable[[str], None], data: Any) -> int:
    emit(json.dumps(data, indent=2))
    return 0


def run_watches(*, emit: Callable[[str], None] = print) -> int:
    from gaia.messaging.discord import github_watch

    return _emit_json(
        emit,
        [{"repo": w.repo, "own_accounts": w.own_accounts} for w in github_watch.load()],
    )


def run_watch(
    *, repo: str, mine: Optional[str], emit: Callable[[str], None] = print
) -> int:
    from gaia.messaging.discord import github_watch

    own = [a for a in (mine or "").split(",") if a.strip()]
    try:
        github_watch.check_repo(
            github_watch.normalize_repo(repo), github_watch.github_token()
        )
        watch = github_watch.add(repo, own)
    except github_watch.WatchError as e:
        emit(f"❌ {e}")
        return 2
    except RuntimeError as e:
        emit(f"❌ {e}")
        return 1
    mode = (
        "every 2 minutes"
        if github_watch.github_token()
        else "every 10 minutes per watched repo (sign in to the GitHub CLI to check every 2)"
    )
    emit(
        f"Watching {watch.repo} {mode}. New PRs, issues and comments from anyone "
        f"except {', '.join(watch.own_accounts) or 'nobody'} and bots go to the "
        "paired Discord DM as they happen."
    )
    return 0


def run_unwatch(*, repo: str, emit: Callable[[str], None] = print) -> int:
    from gaia.messaging.discord import github_watch

    try:
        removed = github_watch.remove(repo)
    except github_watch.WatchError as e:
        emit(f"❌ {e}")
        return 2
    emit(f"Stopped watching {repo}." if removed else f"❌ {repo} was not watched.")
    return 0 if removed else 1


def run_always_allowed(*, forget: bool, emit: Callable[[str], None] = print) -> int:
    if forget:
        count = state.forget_always()
        emit(
            f"Forgot {count} Always allow answer(s). The bridge asks again from "
            "its next prompt; restart it to also clear what the running agent "
            "remembers."
        )
        return 0
    return _emit_json(emit, sorted(set(state.always_labels())))


def main(args) -> int:
    """Dispatch one ``gaia discord <action>`` invocation."""
    from gaia.messaging.discord.state import BridgeStateError

    try:
        return _dispatch(args)
    except BridgeStateError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    action = getattr(args, "discord_action", None)
    model = getattr(args, "model", None)
    agent_command = getattr(args, "agent_command", None)
    take_over = getattr(args, "take_over", False)
    if action == "setup":
        return run_setup(
            open_browser=not getattr(args, "no_browser", False), model=model
        )
    if action == "connect":
        return run_connect()
    if action == "start":
        if getattr(args, "detach", False):
            if getattr(args, "deny_gated_tools", False) or getattr(
                args, "upload_root", None
            ):
                print(
                    "❌ --deny-gated-tools and --upload-root apply to a bridge run in "
                    "the foreground only; a background bridge would restart without "
                    "them. Run `gaia discord start` without --detach to use them.",
                    file=sys.stderr,
                )
                return 2
            return start_detached(
                model=model, take_over=take_over, agent_command=agent_command
            )
        return run_foreground(
            agent_command=agent_command,
            model=model,
            take_over=take_over,
            deny_gated_tools=getattr(args, "deny_gated_tools", False),
            upload_roots=getattr(args, "upload_root", None),
        )
    if action == "stop":
        return run_stop()
    if action == "restart":
        if not stop():
            print("❌ The Discord bridge did not stop, so it was not restarted.")
            return 1
        return start_detached(
            model=model, take_over=take_over, agent_command=agent_command
        )
    if action == "pair":
        if not stop():
            print("❌ The Discord bridge did not stop, so the pairing was kept.")
            return 1
        reset_pairing()
        return start_detached(model=model, agent_command=agent_command)
    if action == "autostart":
        result = ensure_started(emit=print)
        return 0 if result is None else result
    if action == "status":
        return run_status(as_json=getattr(args, "json", False))
    if action == "decline":
        return run_decline(never=getattr(args, "never", False))
    if action == "watches":
        return run_watches()
    if action == "watch":
        return run_watch(repo=args.repo, mine=getattr(args, "mine", None))
    if action == "unwatch":
        return run_unwatch(repo=args.repo)
    if action == "always-allowed":
        return run_always_allowed(forget=getattr(args, "forget", False))
    if action == "disconnect":
        try:
            disconnect()
        except RuntimeError as e:
            print(f"❌ {e}")
            return 1
        print("Disconnected Discord: the token, the pairing and the bridge are gone.")
        return 0
    print(
        "No discord action given. Use: gaia discord "
        "setup|connect|start|stop|restart|pair|status|decline|disconnect",
        file=sys.stderr,
    )
    return 2
