# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia slack`` — setup, start, stop, status.

Kept out of ``gaia/cli.py`` so that file stays a parser rather than a program,
and so the TUI can drive the same flow: every screen it needs is a function
here, not a block of argparse handling.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set

from gaia.logger import get_logger
from gaia.messaging.slack import credentials, manifest, onboarding

log = get_logger(__name__)

#: Where a backgrounded bridge records its pid, matching the Telegram adapter's
#: layout so one supervisor convention covers both.
PID_PATH = Path.home() / ".gaia" / "slack.pid"


def parse_allowed_users(raw: Optional[str]) -> Set[str]:
    """Split a comma-separated allowlist into Slack member IDs.

    Slack member IDs are opaque strings (``U024BE7LH``), not numbers, so the
    only validation available is non-emptiness — and the shape check below,
    which catches the common paste of a @handle or an email instead of the ID.
    """
    if not raw:
        return set()
    ids = {part.strip() for part in raw.split(",") if part.strip()}
    wrong = sorted(i for i in ids if not i[:1].isalpha() or "@" in i)
    if wrong:
        raise ValueError(
            f"These do not look like Slack member IDs: {', '.join(wrong)}. "
            f"A member ID looks like 'U024BE7LH' — find yours in Slack under "
            f"your avatar -> Profile -> the ... menu -> Copy member ID. A "
            f"display name or an email address will not work."
        )
    return ids


def run_setup(
    *,
    prompt: Callable[[str], str] = input,
    emit: Callable[[str], None] = print,
    open_browser: bool = True,
    app_name: str = manifest.DEFAULT_APP_NAME,
    print_url_only: bool = False,
) -> int:
    """Walk the user through creating the Slack app and store its tokens.

    Injectable ``prompt``/``emit`` so the TUI and the tests drive the same flow
    the CLI does, rather than a second copy of it that can disagree.
    """
    url = manifest.create_app_url(app_name)
    if print_url_only:
        # For a caller that runs its own prompts — the TUI's setup panel — so
        # the manifest stays defined in exactly one place. The browser is opened
        # here too: that caller wants both, and a second flag for one intent is
        # a worse API than one that does the obvious thing.
        if open_browser:
            _open_browser(emit)
        emit(url)
        return 0
    emit("")
    emit("Connecting GAIA to Slack.")
    emit("")
    emit(
        "Slack apps can only be created from api.slack.com, so this cannot be "
        "fully automatic. The manifest is filled in for you; you paste two "
        "tokens back. About ninety seconds."
    )
    emit("")
    for step in manifest.SETUP_STEPS:
        emit(f"  {step}")
    emit("")
    emit(f"Create-app URL:\n  {url}")
    emit("")

    if open_browser:
        _open_browser(emit, url)

    app_token = prompt("App-level token (xapp-…): ").strip()
    bot_token = prompt("Bot user OAuth token (xoxb-…): ").strip()

    try:
        manifest.validate_tokens(bot_token, app_token)
    except manifest.TokenFormatError as e:
        emit(f"\n❌ {e}")
        return 2

    # Prove the token works BEFORE storing it, so a typo is caught here rather
    # than at the next start with an opaque WebSocket failure.
    from gaia.messaging.slack.adapter import SlackAdapter

    probe = SlackAdapter(
        bot_token=bot_token,
        app_token=app_token,
        # The probe never serves anyone; the allowlist is a placeholder that
        # satisfies the constructor's refusal to exist without one.
        allowed_users={"__setup_probe__"},
    )
    try:
        identity = probe.verify()
    except Exception as e:  # noqa: BLE001 - surfaced verbatim below
        emit(f"\n❌ Slack rejected the tokens: {e}")
        emit("Re-copy both tokens and run `gaia slack setup` again.")
        return 1

    credentials.save(bot_token=bot_token, app_token=app_token)
    team = identity.get("team") or identity.get("team_id") or "your workspace"
    user_id = identity.get("user_id") or ""
    onboarding.record_decision(
        onboarding.STATE_CONNECTED,
        detected=True,
        team_name=str(team),
    )

    emit("")
    emit(f"✅ Connected to {team} as {identity.get('user', 'GAIA')}.")
    emit("")
    emit("Before you start it, decide who may use it. Everyone in a workspace")
    emit("can DM a bot, and this bridge drives the full agent — it can read")
    emit("your files and ask to run commands.")
    emit("")
    emit("  gaia slack start --allowed-users <your member ID>")
    emit("")
    emit(
        "Find your member ID in Slack: your avatar -> Profile -> the ... menu "
        "-> Copy member ID."
    )
    if user_id:
        emit(f"(The bot itself is {user_id} — that is not the ID you want.)")
    return 0


def run_start(
    *,
    allowed_users: Optional[str],
    agent_command: Optional[str] = None,
    deny_gated_tools: bool = False,
    upload_roots: Optional[Sequence[str]] = None,
    background: bool = False,
    emit: Callable[[str], None] = print,
) -> int:
    """Start the bridge. Blocks until interrupted unless ``background``."""
    from gaia.messaging.slack.adapter import (
        SlackAdapter,
        SlackAllowlistError,
        SlackDependencyError,
    )

    try:
        allowed = parse_allowed_users(allowed_users)
    except ValueError as e:
        emit(f"❌ {e}")
        return 2

    try:
        creds = credentials.load()
    except credentials.SlackCredentialsError as e:
        emit(f"❌ {e}")
        return 2

    agent_argv: Optional[List[str]] = None
    if agent_command:
        import shlex

        agent_argv = shlex.split(agent_command)

    try:
        adapter = SlackAdapter(
            bot_token=creds.bot_token,
            app_token=creds.app_token,
            allowed_users=allowed,
            agent_argv=agent_argv,
            deny_gated_tools=deny_gated_tools,
            upload_roots=upload_roots,
        )
    except SlackAllowlistError as e:
        emit(f"❌ {e}")
        return 2

    if background:
        _write_pid()
    try:
        adapter.start()
    except SlackDependencyError as e:
        emit(f"❌ {e}")
        _clear_pid(background)
        return 1
    except Exception as e:  # noqa: BLE001 - surfaced with its own message
        emit(f"❌ Could not start the Slack bridge: {e}")
        _clear_pid(background)
        return 1

    emit(
        f"✅ Slack bridge running for {len(allowed)} allowed member(s) in "
        f"workspace {adapter.team_id}."
    )
    emit(
        "   Direct messages only. Shell and file writes will ask before " "running."
        if not deny_gated_tools
        else "   Direct messages only. Running read-only — every gated tool is "
        "auto-denied."
    )
    emit("   Press Ctrl-C to stop.")

    try:
        _sleep_forever()
    except KeyboardInterrupt:
        emit("\nStopping…")
    finally:
        adapter.close()
        _clear_pid(background)
    return 0


def _sleep_forever() -> None:
    """Block until interrupted.

    ``threading.Event().wait()`` rather than ``signal.pause()``: Windows has no
    ``pause``, and an unbounded ``wait()`` still raises KeyboardInterrupt in the
    main thread on both platforms.
    """
    import threading

    threading.Event().wait()


def _write_pid() -> None:
    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as e:
        # The pid file is load-bearing in background mode: without it nothing
        # can find or stop this process.
        raise RuntimeError(
            f"Could not write the Slack pid file at {PID_PATH}: {e}. Ensure "
            f"~/.gaia is writable, or start without --background."
        ) from e


def _clear_pid(background: bool) -> None:
    if not background:
        return
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not remove the Slack pid file: %s", e)


def run_stop(*, emit: Callable[[str], None] = print) -> int:
    """Stop a backgrounded bridge."""
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        emit("No backgrounded Slack bridge is recorded as running.")
        return 0
    except (OSError, ValueError) as e:
        emit(f"❌ Could not read the Slack pid file at {PID_PATH}: {e}")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        emit(f"No process {pid} — clearing the stale pid file.")
        PID_PATH.unlink(missing_ok=True)
        return 0
    except OSError as e:
        emit(f"❌ Could not stop process {pid}: {e}")
        return 1
    PID_PATH.unlink(missing_ok=True)
    emit(f"Stopped the Slack bridge (pid {pid}).")
    return 0


def _open_browser(emit: Callable[[str], None], url: Optional[str] = None) -> None:
    """Best-effort browser open. Never the only route to the URL.

    A headless box, a container, or an SSH session has no browser, and
    ``webbrowser.open`` reports success on some of them regardless — so every
    caller prints the URL too.
    """
    if url is None:
        url = manifest.create_app_url()
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001 - absence of a browser is not an error
        log.debug("Could not open a browser: %s", e)
        emit("(Could not open a browser — use the URL below.)")


def run_connect(
    *,
    stdin=None,
    emit: Callable[[str], None] = print,
) -> int:
    """Validate and store a pair of tokens read from stdin. No prompting.

    The non-interactive half of :func:`run_setup`, for a caller that collected
    the tokens itself — the TUI's own setup panel does, so it never has to hand
    the terminal over to this process.

    **stdin, not argv.** A token passed as an argument is visible to every
    other process on the machine through ``ps``, and lands in shell history.
    Two lines: the app-level token, then the bot token.
    """
    stream = stdin if stdin is not None else sys.stdin
    app_token = (stream.readline() or "").strip()
    bot_token = (stream.readline() or "").strip()

    if not app_token or not bot_token:
        emit(
            "❌ Expected two lines on stdin: the app-level token, then the bot "
            "token. Got "
            f"{sum(1 for t in (app_token, bot_token) if t)}."
        )
        return 2

    try:
        manifest.validate_tokens(bot_token, app_token)
    except manifest.TokenFormatError as e:
        emit(f"❌ {e}")
        return 2

    # Proven against Slack before anything is stored, so a typo fails here
    # rather than at the next start as an opaque WebSocket handshake failure.
    from gaia.messaging.slack.adapter import SlackAdapter

    probe = SlackAdapter(
        bot_token=bot_token,
        app_token=app_token,
        allowed_users={"__setup_probe__"},
    )
    try:
        identity = probe.verify()
    except Exception as e:  # noqa: BLE001 - surfaced verbatim
        emit(f"❌ Slack rejected the tokens: {e}")
        return 1

    credentials.save(bot_token=bot_token, app_token=app_token)
    team = str(identity.get("team") or identity.get("team_id") or "your workspace")
    onboarding.record_decision(
        onboarding.STATE_CONNECTED, detected=True, team_name=team
    )
    # One machine-readable line, so a caller that drove this does not have to
    # parse prose to learn which workspace it landed in.
    emit(json.dumps({"connected": True, "team": team}))
    return 0


def run_decline(*, never: bool = False, emit: Callable[[str], None] = print) -> int:
    """Record that the user does not want Slack set up.

    ``never`` is the difference between "not now" and "stop asking". A plain
    decline is reconsidered exactly once, if Slack appears on a machine that
    did not have it when the answer was given; ``--never`` is honoured forever.

    Exists as a command so the TUI records the decision through the same state
    machine the CLI uses, rather than writing the file itself and drifting from
    it.
    """
    detected = onboarding.detect_slack()
    state = onboarding.STATE_NEVER if never else onboarding.STATE_SKIPPED
    onboarding.record_decision(state, detected=detected)
    if never:
        emit("Won't ask about Slack again. `gaia slack setup` still works.")
    else:
        emit(
            "Skipped Slack setup."
            + (
                ""
                if detected
                else " You'll be offered it once if you install Slack later."
            )
        )
    return 0


def status(detect: bool = True) -> dict:
    """Everything a status line or a TUI row needs, as plain data."""
    state = onboarding.load_state()
    creds = credentials.load_optional()
    detected = onboarding.detect_slack() if detect else False
    return {
        "slack_installed": detected,
        "configured": creds is not None,
        "onboarding_state": state.state,
        "team_name": state.team_name,
        "should_offer_setup": onboarding.should_offer(state, detected),
        "running": _pid_alive(),
    }


def _pid_alive() -> bool:
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_status(*, as_json: bool = False, emit: Callable[[str], None] = print) -> int:
    """Print the bridge's configuration and liveness."""
    info = status()
    if as_json:
        emit(json.dumps(info, indent=2))
        return 0
    emit(
        f"Slack app installed on this machine: {'yes' if info['slack_installed'] else 'no'}"
    )
    emit(
        f"GAIA configured for Slack:           {'yes' if info['configured'] else 'no'}"
    )
    if info["team_name"]:
        emit(f"Workspace:                           {info['team_name']}")
    emit(f"Bridge running (backgrounded):       {'yes' if info['running'] else 'no'}")
    if info["should_offer_setup"]:
        emit("")
        emit("Slack is installed but not connected. Run `gaia slack setup`.")
    return 0


def main(args) -> int:
    """Dispatch one ``gaia slack <action>`` invocation."""
    action = getattr(args, "slack_action", None)
    if action == "setup":
        if getattr(args, "print_url", False):
            return run_setup(print_url_only=True)
        return run_setup(open_browser=not getattr(args, "no_browser", False))
    if action == "start":
        return run_start(
            allowed_users=getattr(args, "allowed_users", None),
            agent_command=getattr(args, "agent_command", None),
            deny_gated_tools=getattr(args, "deny_gated_tools", False),
            upload_roots=getattr(args, "upload_root", None),
            background=getattr(args, "background", False),
        )
    if action == "stop":
        return run_stop()
    if action == "decline":
        return run_decline(never=getattr(args, "never", False))
    if action == "connect":
        return run_connect()
    if action == "status":
        return run_status(as_json=getattr(args, "json", False))
    print(
        "No slack action specified. Use: gaia slack setup|start|stop|status|connect|decline",
        file=sys.stderr,
    )
    return 2
