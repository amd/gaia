# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Discord DM adapter — drive the flagship agent from a Discord direct message.

Like the Slack adapter, this reaches the real flagship agent: it can read your
files, search your documents, and ask to run commands. The guard rails below
are what make that safe to expose to a chat app.

* **The confirmation gate is the agent's own.** Shell, file-write and code tools
  pause with ``needs_confirmation``; this adapter renders the pause as buttons
  and sends the click back over the bridge. A message can *ask* for a dangerous
  tool; it cannot *run* one unattended. Bypass is unreachable — the bridge has
  no verb for it.
* **One person, paired by a code.** Whoever DMs the one-time code shown on this
  PC becomes the only user the bot answers. Not the application owner: for a
  team-owned app that is a pseudo-user, and every team admin can reset the token.
* **DMs only, fresh only.** Server messages, other bots, edits, and anything
  older than :data:`STALE_SECONDS` are never executed — a Gateway resume
  replays buffered events, and a laptop waking from sleep should not run a
  two-hour-old "delete that folder".
* **One PC per bot.** A pinned lease message in the owner's DM names the PC that
  answers, so a second PC with the same token stands down instead of running
  every approved command twice.

This module is transport-agnostic and synchronous, like the Slack adapter: the
``discord.py`` client lives in :mod:`gaia.messaging.discord.transport`, and the
tests drive this class with a fake transport.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set

from gaia.logger import get_logger
from gaia.messaging.bridge import (
    DECISION_ALLOW,
    DECISION_ALWAYS,
    DECISION_DENY,
    AgentChannel,
    Turn,
    resolve_agent_argv,
)
from gaia.messaging.discord import github_watch, schedules, state

log = get_logger(__name__)

#: Every authorization decision, pinned at WARNING so a refusal survives the
#: default log level.
audit = get_logger("gaia.messaging.discord.audit")

#: What a stranger is told, once. Says nothing about who IS allowed.
UNAUTHORIZED_REPLY = "Sorry — you're not authorized to use this GAIA agent."

#: Added to every question from Discord. The reply is read on a phone, where a
#: bare "#6779" is a dead end; every PR, issue or page named must be a link.
DISCORD_REPLY_NOTE = (
    "(Sent from Discord. Link everything you mention that you found online: "
    "write each PR, issue, commit or page as a Markdown link to its full URL, "
    "like [#6779](https://github.com/amd/gaia/pull/6779), never a bare number "
    "or name.)"
)

PAIRING_PROMPT = (
    "That isn't the current pairing code. Send the code shown right now on the "
    "PC that runs GAIA (Settings → Messaging → Discord in the app, or /discord "
    "in the terminal app) — it changes after an hour or a few wrong tries."
)

#: How to bring the bridge back after its agent was given up on. Names the app
#: actions, never a command: the user is on their phone.
RESTART_HINT = (
    "Restart Discord from Settings → Messaging in the GAIA app, or type "
    "/discord restart in the terminal app."
)

#: Discord rejects a message over 2000 characters; chunks leave room for the
#: tool trail and a reopened code fence.
DISCORD_MESSAGE_LIMIT = 2000
CHUNK_LIMIT = 1900

#: Discord's upload ceiling for a bot in a DM.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

#: A message or click older than this is never executed.
STALE_SECONDS = 120.0

CONFIRM_TIMEOUT_SECONDS = 600.0
#: Long enough to walk through setup without racing a rotation; the code is
#: only ever shown on this PC, and wrong guesses rotate it anyway.
PAIRING_TTL_SECONDS = 3600.0
PAIRING_MAX_GUESSES = 5

#: A lease newer than this belongs to a PC that is still answering.
LEASE_FRESH_SECONDS = 180.0
LEASE_PREFIX = "🟢 GAIA is answering from"
_LEASE_RE = re.compile(r"lease ([0-9a-f]{8,32}) at (\d+)")

BUTTON_ALLOW = "gaia:allow"
BUTTON_ALWAYS = "gaia:always"
BUTTON_DENY = "gaia:deny"
_BUTTON_DECISIONS = {
    BUTTON_ALLOW: DECISION_ALLOW,
    BUTTON_ALWAYS: DECISION_ALWAYS,
    BUTTON_DENY: DECISION_DENY,
}
_DECISION_VERBS = {
    DECISION_ALLOW: "Allowed",
    DECISION_ALWAYS: "Always allowed",
    DECISION_DENY: "Denied",
}

#: Tools whose ``tool_call`` args name the file they write.
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "write_markdown_file",
        "write_python_file",
        "edit_python_file",
    }
)

_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_DISCORD_EPOCH_MS = 1420070400000
_PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_SHOWN_COMMAND_CHARS = 1400


# ----------------------------------------------------------------------
# Plain data crossing the transport boundary
# ----------------------------------------------------------------------


@dataclass
class Attachment:
    filename: str
    url: str
    size: int = 0


@dataclass
class InboundMessage:
    id: str
    channel_id: str
    author_id: str
    author_name: str = ""
    author_is_bot: bool = False
    #: True only for a direct-message channel (no guild).
    is_dm: bool = False
    content: str = ""
    attachments: List[Attachment] = field(default_factory=list)


@dataclass
class InboundClick:
    message_id: str
    channel_id: str
    user_id: str
    custom_id: str
    #: The interaction's own snowflake, for its age.
    interaction_id: str = ""


@dataclass
class Button:
    custom_id: str
    label: str
    style: str = "secondary"


@dataclass
class PinnedMessage:
    id: str
    author_id: str
    content: str


class Transport(Protocol):
    """Synchronous Discord primitives. Safe to call from any non-loop thread."""

    def send(
        self,
        channel_id: str,
        text: str,
        *,
        buttons: Optional[Sequence[Button]] = None,
        file_path: Optional[Path] = None,
        file_text: Optional[tuple] = None,
    ) -> str: ...

    def edit(
        self,
        channel_id: str,
        message_id: str,
        text: str,
        *,
        clear_buttons: bool = False,
    ) -> None: ...

    def delete(self, channel_id: str, message_id: str) -> None: ...

    def count_from_after(
        self, channel_id: str, author_id: str, after_id: str
    ) -> int: ...

    def pins(self, channel_id: str) -> List[PinnedMessage]: ...

    def pin(self, channel_id: str, message_id: str) -> None: ...


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def snowflake_time(snowflake: str) -> float:
    """Epoch seconds a Discord id was minted at."""
    return ((int(snowflake) >> 22) + _DISCORD_EPOCH_MS) / 1000.0


def is_discord_cdn(url: str) -> bool:
    """True for an https URL on Discord's attachment CDN.

    ``hostname`` strips userinfo and port, so ``cdn.discordapp.com@evil.com``
    is judged as evil.com.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host in _CDN_HOSTS


def safe_filename(name: str, prefix: str) -> str:
    """A basename that cannot escape the inbox or be special on Windows."""
    # Split by hand: Path would read "a:stream" as a drive on Windows only.
    base = _UNSAFE_NAME.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1])
    base = base.strip(". ") or "file"
    if _WINDOWS_RESERVED.match(base):
        base = f"_{base}"
    tag = _UNSAFE_NAME.sub("_", prefix) or uuid.uuid4().hex[:8]
    return f"{tag}-{base}"


def _visible(ch: str) -> str:
    """Keep printable text; spell out anything that can hide or reorder it."""
    if ch in "\n\t":
        return ch
    category = unicodedata.category(ch)
    # Cf covers bidi overrides and zero-width joiners; Cc the control codes.
    if category in ("Cf", "Cc", "Co", "Cn") or category.startswith("Z") and ch != " ":
        return f"⟨U+{ord(ch):04X}⟩"
    return ch


def neutralize(text: str) -> str:
    """Make *text* safe to show inside a code block.

    A backtick would close the fence and let the rest render as markdown, so a
    command could hide its real tail; invisible characters could reorder it.
    Both are shown, not removed — the user should see what will run.
    """
    return "".join(_visible(c) for c in text).replace("`", "ˋ")


def render_confirmation(action: str, summary: str) -> tuple:
    """Return ``(text, full_text_or_None)`` for an approval prompt.

    A long command shows its head, its tail and its length, and the whole of it
    goes along as a file, so padding cannot push the dangerous part out of view.
    """
    shown = neutralize(summary)
    full: Optional[str] = None
    if len(shown) > _SHOWN_COMMAND_CHARS:
        half = _SHOWN_COMMAND_CHARS // 2
        shown = (
            f"{shown[:half]}\n… {len(summary) - _SHOWN_COMMAND_CHARS} characters "
            f"not shown — the full text is attached …\n{shown[-half:]}"
        )
        full = summary
    head = f"**GAIA wants to {neutralize(action)}**"
    body = f"{head}\n```\n{shown}\n```" if shown else head
    if "ˋ" in body or "⟨U+" in body:
        body += "\n-# Backticks are shown as ˋ and hidden characters as ⟨U+…⟩."
    return body, full


def confirmation_detail(event: Dict[str, Any]) -> str:
    """The exact call being approved, from the event's ``args``.

    The agent's ``summary`` shortens long values (``…[310 chars]…``) and mixes
    in its risk note; approving what you cannot see is not approving. ``args``
    are what the model sent, so a shell command is shown whole.
    """
    args = event.get("args")
    if not isinstance(args, dict) or not args:
        return ""
    command = args.get("command")
    if isinstance(command, str) and set(args) <= {
        "command",
        "timeout",
        "working_directory",
    }:
        detail = command
        if args.get("working_directory"):
            detail += f"\n(in {args['working_directory']})"
    else:
        detail = "\n".join(f"{key}: {value}" for key, value in args.items())
    if event.get("args_truncated"):
        detail += "\n(the agent cut a value at 64,000 characters)"
    return detail


def split_message(text: str, limit: int = CHUNK_LIMIT) -> List[str]:
    """Split *text* into Discord-sized chunks at line boundaries.

    A chunk that ends inside a code fence closes it, and the next reopens it,
    so the second half of a code block does not render as prose.
    """
    if len(text) <= limit:
        return [text]
    # Room for a reopened fence line and a closing one.
    room = limit - 40
    pieces: List[str] = []
    for line in text.splitlines(keepends=True):
        while len(line) > room:
            pieces.append(line[:room])
            line = line[room:]
        pieces.append(line)
    chunks: List[str] = []
    current = ""
    fence: Optional[str] = None
    for piece in pieces:
        if current and len(current) + len(piece) > room:
            if fence is not None:
                current += "```" if current.endswith("\n") else "\n```"
            chunks.append(current)
            current = f"{fence}\n" if fence is not None else ""
        current += piece
        stripped = piece.strip()
        if stripped.startswith("```"):
            fence = None if fence is not None else stripped[:20]
    if current.strip():
        chunks.append(current)
    return chunks


def new_pairing_code() -> str:
    """Eight characters with no 0/O/1/I to misread, ~40 bits."""
    return "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))


def _normalize_code(text: str) -> str:
    return re.sub(r"[\s-]", "", text).upper()


# ----------------------------------------------------------------------
# The adapter
# ----------------------------------------------------------------------


@dataclass
class ReplyTarget:
    """Where one turn's output goes, and the live state of that reply."""

    channel_id: str
    message_id: str
    reply_id: str = ""
    #: Answer text collected since the last tool call.
    text: str = ""
    tools: List[str] = field(default_factory=list)
    uploaded: Set[str] = field(default_factory=set)
    pending_upload: str = ""


@dataclass
class _Pending:
    confirm_id: str
    channel_id: str
    message_id: str
    label: str
    timer: Any


class DiscordAdapter:
    """Bridge one paired Discord user's DMs to one local flagship agent."""

    def __init__(
        self,
        config: state.BridgeConfig,
        transport: Optional[Transport] = None,
        *,
        agent_argv: Optional[Sequence[str]] = None,
        upload_roots: Optional[Sequence[str]] = None,
        deny_gated_tools: bool = False,
        channel_factory: Optional[Callable[..., AgentChannel]] = None,
        confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
        code_factory: Callable[[], str] = new_pairing_code,
        hostname: Optional[str] = None,
    ) -> None:
        if not config.connected:
            raise ValueError(
                "The Discord adapter needs a connected bot (bridge.json has no "
                "bot id). Connect Discord in Settings → Messaging first."
            )
        if not config.instance_id:
            config.instance_id = uuid.uuid4().hex[:16]
            state.save_config(config)
        self.config = config
        self.transport = transport
        self.deny_gated_tools = deny_gated_tools
        self.confirm_timeout = confirm_timeout
        self.upload_roots = [
            Path(p).expanduser().resolve() for p in (upload_roots or [str(Path.home())])
        ]
        self._agent_argv = list(agent_argv) if agent_argv else None
        self._channel_factory = channel_factory or AgentChannel
        self._channel: Optional[AgentChannel] = None
        self._clock = clock
        self._code_factory = code_factory
        self._hostname = hostname or socket.gethostname()
        self._lock = threading.RLock()
        self._pending: Dict[str, _Pending] = {}
        self._seen: List[str] = []
        self._state = state.STATE_STARTING
        self._detail = ""
        self._lease_at = 0
        prior = state.read_status()
        self._last_message_id = prior.last_message_id
        self._refused: Set[str] = set(prior.refused_user_ids)
        self._pairing_code = ""
        self._pairing_expires = 0.0
        self._pairing_misses = 0
        self._poller: Optional[github_watch.Poller] = None
        self._tick_lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> str:
        return self._state

    def _set_state(self, new_state: str, detail: str = "", **extra: Any) -> None:
        self._state = new_state
        self._detail = detail
        state.write_status(
            new_state,
            detail,
            last_message_id=self._last_message_id,
            refused_user_ids=sorted(self._refused),
            pairing_code=self._pairing_code if new_state == state.STATE_PAIRING else "",
            pairing_expires_at=(
                self._pairing_expires if new_state == state.STATE_PAIRING else 0.0
            ),
            **extra,
        )

    def _refresh_status(self) -> None:
        self._set_state(self._state, self._detail)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_agent(self) -> None:
        """Spawn the agent child. Called before the Gateway connects."""
        argv = list(self._agent_argv or resolve_agent_argv())
        if self.config.model:
            argv += ["--model", self.config.model]
        self._channel = self._channel_factory(
            on_event=self._on_agent_event,
            argv=argv,
            env=_agent_env(),
            on_busy=self._on_busy,
            restart_hint=RESTART_HINT,
        )
        self._channel.start()

    def _agent(self) -> AgentChannel:
        if self._channel is None:
            raise RuntimeError(
                "The Discord adapter has no agent channel — start_agent() was "
                "never called, or it failed. " + RESTART_HINT
            )
        return self._channel

    def on_ready(self, *, resumed: bool = False, take_over: bool = False) -> None:
        """The Gateway is connected: pair, or claim the lease and catch up."""
        if not self.config.paired:
            self._begin_pairing()
            return
        if not self.claim_lease(force=take_over):
            return
        missed = 0
        if not resumed and self._last_message_id and self.config.dm_channel_id:
            missed = self.transport.count_from_after(
                self.config.dm_channel_id, self.config.owner_id, self._last_message_id
            )
        self._set_state(state.STATE_CONNECTED)
        if missed:
            self._send(
                self.config.dm_channel_id,
                f"I was offline and missed {missed} message"
                f"{'s' if missed != 1 else ''}. Send again anything you still "
                "need — I don't run old messages.",
            )

    def close(self) -> None:
        """Expire every open prompt, then stop the agent. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.timer.cancel()
            self._edit(
                item.channel_id,
                item.message_id,
                f"**Expired — the bridge stopped** — {neutralize(item.label)}",
                clear_buttons=True,
            )
            if self._channel is not None:
                self._channel.decide(DECISION_DENY, item.confirm_id)
        if self._channel is not None:
            self._channel.close()

    # ------------------------------------------------------------------
    # Pairing
    # ------------------------------------------------------------------

    def _begin_pairing(self) -> None:
        self._pairing_code = self._code_factory()
        self._pairing_expires = self._clock() + PAIRING_TTL_SECONDS
        self._pairing_misses = 0
        audit.warning("Discord bridge waiting for pairing")
        self._set_state(
            state.STATE_PAIRING,
            "DM the code to the bot to pair it with your Discord account.",
        )

    def tick(self) -> None:
        """Periodic upkeep: pairing code, lease, schedules, GitHub watches.

        Skipped while the previous tick still runs: overlapping ticks would
        report the same GitHub items twice.
        """
        if not self._tick_lock.acquire(blocking=False):
            log.info("Previous Discord upkeep still running; skipping this tick")
            return
        try:
            self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self) -> None:
        if self._state == state.STATE_PAIRING:
            if self._clock() >= self._pairing_expires:
                self._begin_pairing()
            return
        if self._state == state.STATE_CONNECTED:
            if self.claim_lease(force=False, heartbeat=True):
                for item in schedules.take_due(self._clock()):
                    self._run_schedule(item)
                self._watcher().poll()
        elif self._state == state.STATE_TAKEN_OVER and self.claim_lease(force=False):
            # The other PC stopped refreshing its lease; answer again.
            self._set_state(state.STATE_CONNECTED)

    def _try_pair(self, msg: InboundMessage) -> None:
        if self._clock() >= self._pairing_expires:
            self._begin_pairing()
        if _normalize_code(msg.content) != self._pairing_code:
            self._pairing_misses += 1
            audit.warning(
                "Discord pairing attempt with a wrong code from %s (%d/%d)",
                msg.author_id,
                self._pairing_misses,
                PAIRING_MAX_GUESSES,
            )
            if self._pairing_misses >= PAIRING_MAX_GUESSES:
                self._begin_pairing()
            self._send(msg.channel_id, PAIRING_PROMPT)
            return
        self.config.owner_id = msg.author_id
        self.config.owner_name = msg.author_name
        self.config.dm_channel_id = msg.channel_id
        state.save_config(self.config)
        self._pairing_code = ""
        self._last_message_id = msg.id
        audit.warning(
            "Discord bridge paired with %s (%s)", msg.author_name, msg.author_id
        )
        if not self.claim_lease(force=True):
            return
        self._set_state(state.STATE_CONNECTED)
        self._send(
            msg.channel_id,
            "Paired. I'll answer only you. Ask me anything — I run on your PC, and "
            "anything that changes it waits for you to press Allow.",
        )

    # ------------------------------------------------------------------
    # Lease
    # ------------------------------------------------------------------

    def _lease_text(self, now: int) -> str:
        return (
            f"{LEASE_PREFIX} **{neutralize(self._hostname)}**\n"
            f"-# lease {self.config.instance_id} at {now} · kept pinned so two "
            "PCs never both answer"
        )

    def claim_lease(self, *, force: bool, heartbeat: bool = False) -> bool:
        """Hold the lease for this PC, or stand down for a fresher one.

        ``force`` takes it regardless (pairing, or "Answer from this PC").
        Returns whether this PC may answer.
        """
        channel = self.config.dm_channel_id
        now = int(self._clock())
        ours: Optional[PinnedMessage] = None
        foreign_at = 0
        for pin in self.transport.pins(channel):
            if pin.author_id != self.config.bot_id:
                continue
            match = _LEASE_RE.search(pin.content)
            if not match:
                continue
            if match.group(1) == self.config.instance_id:
                ours = pin
            else:
                foreign_at = max(foreign_at, int(match.group(2)))
        newer_foreign = foreign_at > self._lease_at if heartbeat else False
        fresh_foreign = now - foreign_at < LEASE_FRESH_SECONDS
        if not force and (newer_foreign or (not heartbeat and fresh_foreign)):
            audit.warning("Another PC holds the Discord lease; standing down")
            self._set_state(
                state.STATE_TAKEN_OVER,
                "Another PC is answering this Discord bot.",
            )
            return False
        text = self._lease_text(now)
        if ours is not None:
            self.transport.edit(channel, ours.id, text)
        else:
            message_id = self.transport.send(channel, text)
            if not message_id:
                raise RuntimeError(
                    "Could not post the lease message to your Discord DM, so "
                    "this PC cannot prove it is the one answering. " + RESTART_HINT
                )
            self.transport.pin(channel, message_id)
        self._lease_at = now
        return True

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def _is_stale(self, snowflake: str) -> bool:
        try:
            return self._clock() - snowflake_time(snowflake) > STALE_SECONDS
        except ValueError:
            return True

    def _first_sight(self, message_id: str) -> bool:
        """False for a message this bridge already handled (resume replays)."""
        with self._lock:
            if message_id in self._seen:
                return False
            self._seen.append(message_id)
            del self._seen[:-256]
            return True

    def handle_message(self, msg: InboundMessage) -> None:
        """Dispatch one new message. Runs off the event loop."""
        if msg.author_is_bot or msg.author_id == self.config.bot_id:
            return
        if not msg.is_dm:
            audit.warning(
                "Ignored a Discord message outside a DM in channel %s — this "
                "bridge answers direct messages only",
                msg.channel_id,
            )
            return
        if not self._first_sight(msg.id):
            return
        if self._state == state.STATE_TAKEN_OVER:
            return
        if self._state == state.STATE_PAIRING:
            if self._is_stale(msg.id):
                return
            self._try_pair(msg)
            return
        if msg.author_id != self.config.owner_id:
            self._refuse(msg)
            return
        self._last_message_id = msg.id
        if self._is_stale(msg.id):
            audit.warning("Did not run a Discord message from %s: it is stale", msg.id)
            self._send(
                msg.channel_id,
                "I missed this one while I was offline, so I didn't run it. "
                "Send it again if you still want it.",
            )
            self._refresh_status()
            return
        self._refresh_status()
        audit.info("Authorized Discord message %s from the paired user", msg.id)

        note = self._ingest(msg.attachments)
        question = f"{msg.content.strip()} {note}".strip()
        if not question:
            self._send(msg.channel_id, "I got an empty message — send me a question.")
            return
        self._start_turn(msg.channel_id, msg.id, question, "-# Thinking…")

    def _start_turn(
        self, channel_id: str, message_id: str, question: str, placeholder: str
    ) -> None:
        target = ReplyTarget(channel_id=channel_id, message_id=message_id)
        target.reply_id = self._send(channel_id, placeholder)
        self._agent().submit(
            Turn(
                text=f"{question}\n\n{DISCORD_REPLY_NOTE}",
                context=target,
                sender=self.config.owner_id,
            )
        )

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    def _watcher(self) -> "github_watch.Poller":
        """The GitHub poller, built on first use."""
        if self._poller is None:
            self._poller = github_watch.Poller(
                lambda text: self._send(self.config.dm_channel_id, text)
            )
        return self._poller

    def _run_schedule(self, item: "schedules.Schedule") -> None:
        audit.info("Running Discord schedule %s", item.id)
        self._send(self.config.dm_channel_id, f"⏰ **Scheduled** — {item.prompt}")
        # An exact window: "the last 3 hours" left to the model drifts at the edges.
        start = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock() - item.every_seconds)
        )
        self._start_turn(
            self.config.dm_channel_id,
            "",
            f"{item.prompt}\n\n(This runs on a schedule the user set, not a "
            f"message they just typed. It covers what happened since {start} "
            "UTC. Keep the answer short.)",
            "-# Running…",
        )

    def _refuse(self, msg: InboundMessage) -> None:
        audit.warning(
            "Refused a Discord message from unauthorized user %s — pair again "
            "from the GAIA app if this refusal is wrong",
            msg.author_id,
        )
        if msg.author_id in self._refused:
            return
        self._refused.add(msg.author_id)
        self._refresh_status()
        self._send(msg.channel_id, UNAUTHORIZED_REPLY)

    def handle_click(self, click: InboundClick) -> Optional[str]:
        """Answer an approval button. Returns a private note for the clicker."""
        if click.user_id != self.config.owner_id or (
            click.channel_id != self.config.dm_channel_id
        ):
            audit.warning(
                "Refused a Discord button click from %s in %s",
                click.user_id,
                click.channel_id,
            )
            return "This button isn't for you."
        decision = _BUTTON_DECISIONS.get(click.custom_id)
        if decision is None:
            return None
        if click.interaction_id and self._is_stale(click.interaction_id):
            return "That click arrived too late to count. Nothing was run."
        with self._lock:
            pending = self._pending.pop(click.message_id, None)
        if pending is None:
            return "That prompt has expired or was already answered."
        pending.timer.cancel()
        audit.warning(
            "Paired Discord user answered %r for confirmation %s",
            decision,
            pending.confirm_id,
        )
        self._agent().decide(decision, pending.confirm_id)
        self._edit(
            pending.channel_id,
            pending.message_id,
            f"**{_DECISION_VERBS[decision]}** — {neutralize(pending.label)}",
            clear_buttons=True,
        )
        return None

    def _expire(self, message_id: str) -> None:
        with self._lock:
            pending = self._pending.pop(message_id, None)
        if pending is None:
            return
        audit.warning(
            "Denied %r: no answer within %ss", pending.label, int(self.confirm_timeout)
        )
        self._agent().decide(DECISION_DENY, pending.confirm_id)
        self._edit(
            pending.channel_id,
            pending.message_id,
            f"**Denied — no answer within {int(self.confirm_timeout // 60)} "
            f"minutes** — {neutralize(pending.label)}",
            clear_buttons=True,
        )

    def _ingest(self, attachments: Sequence[Attachment]) -> str:
        """Save shared files where the agent can read them, and say where."""
        notes: List[str] = []
        for item in attachments:
            name = item.filename or "file"
            if item.size and item.size > MAX_DOWNLOAD_BYTES:
                notes.append(f"[{name} skipped — larger than 100 MB]")
                continue
            try:
                path = self._download(item)
            except (OSError, ValueError) as e:
                log.warning("Could not download Discord attachment %s: %s", name, e)
                notes.append(f"[{name} could not be downloaded]")
                continue
            notes.append(
                f"[the user shared {name}, saved at {path} — open or index it "
                "before answering questions about it]"
            )
        return " ".join(notes)

    def _download(self, item: Attachment) -> Path:
        import requests

        if not is_discord_cdn(item.url):
            raise ValueError(
                f"Discord gave an attachment URL outside its CDN ({item.url!r}); "
                "refusing to fetch it."
            )
        folder = state.inbox_dir()
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / safe_filename(item.filename, uuid.uuid4().hex[:8])
        partial = folder / f".partial-{uuid.uuid4().hex}"
        try:
            response = requests.get(
                item.url, timeout=60, stream=True, allow_redirects=False
            )
        except requests.RequestException as e:
            raise OSError(f"download failed: {e}") from e
        with response:
            if 300 <= response.status_code < 400:
                raise ValueError(
                    "Discord redirected the attachment; refusing to follow."
                )
            if response.status_code != 200:
                raise OSError(f"Discord answered HTTP {response.status_code}")
            written = 0
            try:
                with open(partial, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=65536):
                        written += len(chunk)
                        if written > MAX_DOWNLOAD_BYTES:
                            raise ValueError(
                                f"{item.filename} exceeded the "
                                f"{MAX_DOWNLOAD_BYTES} byte cap"
                            )
                        fh.write(chunk)
                partial.replace(final)
            finally:
                partial.unlink(missing_ok=True)
        _mark_from_internet(final)
        return final

    def _on_busy(self, turn: Turn, ahead: int) -> None:
        target = turn.context
        if isinstance(target, ReplyTarget):
            self._update(
                target,
                f"-# Queued — finishing {ahead} earlier message"
                f"{'s' if ahead != 1 else ''} first…",
            )

    # ------------------------------------------------------------------
    # Outbound — rendering agent events
    # ------------------------------------------------------------------

    def _on_agent_event(self, event: Dict[str, Any], turn: Turn) -> None:
        target = turn.context
        if not isinstance(target, ReplyTarget):
            log.warning("Dropped an agent event with no Discord reply target")
            return
        etype = event.get("type")
        if etype == "token":
            # Collected, not streamed: models think aloud in the same stream,
            # and an edit never notifies the phone. The answer is posted whole.
            target.text += str(event.get("delta") or "")
        elif etype == "status":
            # Status text can be the model's own planning notes; the placeholder
            # names tools only, so nothing half-thought reaches the DM.
            if not target.reply_id:
                self._update(target, "-# Working…")
        elif etype == "tool_call":
            tool = str(event.get("tool") or "a tool")
            if tool not in target.tools:
                target.tools.append(tool)
            target.text = ""
            self._update(target, f"-# Using `{neutralize(tool)}`…")
            self._remember_written_file(target, tool, event.get("args"))
        elif etype == "tool_result":
            self._maybe_upload(target, event)
        elif etype == "needs_confirmation":
            self._ask_confirmation(target, event)
        elif etype == "needs_input":
            self._refuse_question(target, event)
        elif etype == "final":
            self._finish(target, str(event.get("answer") or ""))
        elif etype == "error":
            self._finish(target, f"⚠️ {event.get('detail') or 'The agent failed.'}")

    def _ask_confirmation(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        action = str(event.get("action") or "run a tool")
        summary = str(event.get("summary") or "")
        confirm_id = str(event.get("confirm_id") or "")
        if self.deny_gated_tools or not confirm_id:
            reason = (
                "this bridge is running read-only"
                if self.deny_gated_tools
                else "the agent sent no prompt id to answer it with"
            )
            audit.warning("Auto-denied %r: %s", action, reason)
            self._agent().decide(DECISION_DENY, confirm_id or None)
            self._send(
                target.channel_id, f"-# Refused **{neutralize(action)}** — {reason}."
            )
            return
        text, full = render_confirmation(action, confirmation_detail(event) or summary)
        risk = (event.get("prompt_context") or {}).get("risk_note")
        if risk:
            text += f"\n-# {neutralize(str(risk))}"
        buttons = [Button(BUTTON_ALLOW, "Allow", "primary")]
        scope = str(event.get("always_scope") or "")
        if scope:
            buttons.append(Button(BUTTON_ALWAYS, f"Always allow {scope}"[:80]))
        buttons.append(Button(BUTTON_DENY, "Deny", "danger"))
        message_id = self._send(
            target.channel_id,
            text,
            buttons=buttons,
            file_text=("command.txt", full) if full else None,
        )
        if not message_id:
            audit.warning("Denied %r: the approval prompt could not be shown", action)
            self._agent().decide(DECISION_DENY, confirm_id)
            return
        if target.reply_id:
            # The answer must land below the prompt, not in a placeholder above it.
            self._delete(target.channel_id, target.reply_id)
            target.reply_id = ""
        timer = threading.Timer(self.confirm_timeout, self._expire, args=(message_id,))
        timer.daemon = True
        with self._lock:
            self._pending[message_id] = _Pending(
                confirm_id, target.channel_id, message_id, action, timer
            )
        timer.start()

    def _refuse_question(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Show a mid-run question and end the turn; a reply continues it."""
        question = str(event.get("question") or "GAIA needs more information.")
        lines = [f"**GAIA asked:** {question}"]
        for option in event.get("options") or []:
            if isinstance(option, dict):
                lines.append(f"• {option.get('label') or option.get('value')}")
        if event.get("sensitive"):
            lines.append(
                "-# It asked for something sensitive — answer on the PC running "
                "GAIA, not in Discord."
            )
        else:
            lines.append("-# Reply with your answer and GAIA will continue from there.")
        self._send(target.channel_id, "\n".join(lines))
        self._agent().cancel()

    def _remember_written_file(self, target: ReplyTarget, tool: str, args: Any) -> None:
        if tool not in WRITE_TOOLS or not isinstance(args, dict):
            return
        raw = args.get("file_path") or args.get("path")
        target.pending_upload = raw if isinstance(raw, str) else ""

    def _maybe_upload(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Send back the file the preceding write tool produced, within limits."""
        raw = target.pending_upload
        target.pending_upload = ""
        if not raw:
            return
        data = event.get("data")
        if isinstance(data, dict) and data.get("success") is False:
            return
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            return
        key = str(path)
        if key in target.uploaded or not path.is_file():
            return
        if not any(path == root or root in path.parents for root in self.upload_roots):
            audit.warning(
                "Refused to upload %s — outside the allowed upload roots %s",
                path,
                ", ".join(str(r) for r in self.upload_roots),
            )
            return
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            self._send(
                target.channel_id,
                f"-# `{neutralize(path.name)}` is larger than Discord's 10 MB limit, "
                "so it stays on your PC.",
            )
            return
        target.uploaded.add(key)
        self._send(
            target.channel_id, f"Here's `{neutralize(path.name)}`.", file_path=path
        )

    def _finish(self, target: ReplyTarget, answer: str) -> None:
        """Post the answer as new messages below everything else, then drop the
        progress placeholder. A new message notifies the phone; an edit does not."""
        answer = answer or target.text
        trail = ", ".join(f"`{neutralize(t)}`" for t in target.tools)
        if trail:
            answer = f"{answer}\n-# Used {trail}"
        for chunk in split_message(answer or "-# (no answer)"):
            self._send(target.channel_id, chunk)
        if target.reply_id:
            self._delete(target.channel_id, target.reply_id)
            target.reply_id = ""

    # ------------------------------------------------------------------
    # Discord primitives (failures are logged, never raised into a turn)
    # ------------------------------------------------------------------

    def _send(self, channel_id: str, text: str, **kwargs: Any) -> str:
        try:
            return self.transport.send(channel_id, text, **kwargs)
        except Exception as e:  # noqa: BLE001 - a failed send must not kill the turn
            log.warning("Could not send to Discord channel %s: %s", channel_id, e)
            return ""

    def _edit(self, channel_id: str, message_id: str, text: str, **kwargs: Any) -> None:
        try:
            self.transport.edit(channel_id, message_id, text, **kwargs)
        except Exception as e:  # noqa: BLE001 - an edit failure must not kill the turn
            log.warning("Could not edit Discord message %s: %s", message_id, e)

    def _delete(self, channel_id: str, message_id: str) -> None:
        try:
            self.transport.delete(channel_id, message_id)
        except (
            Exception
        ) as e:  # noqa: BLE001 - a stale placeholder must not kill the turn
            log.warning("Could not delete Discord message %s: %s", message_id, e)

    def _update(self, target: ReplyTarget, text: str) -> None:
        if not target.reply_id:
            target.reply_id = self._send(target.channel_id, text)
            return
        self._edit(target.channel_id, target.reply_id, text)


def _agent_env() -> Dict[str, str]:
    """The agent's environment: this install's ``gaia`` on PATH, no bot token.

    The discord-automation skill runs ``gaia discord ...``, and an app install
    keeps that CLI beside this interpreter, not on PATH. Only a ``gaia`` shim is
    added: putting the interpreter's folder on PATH would hand the agent GAIA's
    own ``python`` and ``pip``. The token stays out of every shell it runs.
    """
    from gaia.messaging.discord.credentials import BOT_TOKEN_ENV_VAR

    env = {k: v for k, v in os.environ.items() if k != BOT_TOKEN_ENV_VAR}
    env["PATH"] = str(_gaia_shim_dir()) + os.pathsep + env.get("PATH", "")
    return env


def _gaia_shim_dir() -> Path:
    """A folder holding one ``gaia`` launcher for this interpreter.

    Raises:
        RuntimeError: on Windows, no ``gaia.exe`` sits beside this interpreter,
            so the agent could not set up schedules or watches.
    """
    import shutil
    import sys

    folder = state.discord_dir() / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        shim = folder / "gaia"
        body = f'#!/bin/sh\nexec "{sys.executable}" -m gaia.cli "$@"\n'
        if not shim.is_file() or shim.read_text(encoding="utf-8") != body:
            shim.write_text(body, encoding="utf-8")
            shim.chmod(0o755)
        return folder
    # The shell tool starts programs directly, and Windows starts only .exe
    # that way, so a .cmd shim is never found. The console-script launcher
    # names its interpreter by absolute path, so a copy runs from anywhere.
    here = Path(sys.executable).parent
    launcher = next(
        (p for p in (here / "gaia.exe", here / "Scripts" / "gaia.exe") if p.is_file()),
        None,
    )
    if launcher is None:
        raise RuntimeError(
            f"No gaia.exe next to {sys.executable}, so GAIA in Discord could not "
            "set up summaries or alerts. Reinstall GAIA from "
            "https://amd-gaia.ai/docs/guides/install to restore it."
        )
    shim = folder / "gaia.exe"
    current = shim.stat() if shim.is_file() else None
    source = launcher.stat()
    if current is None or (current.st_size, current.st_mtime) != (
        source.st_size,
        source.st_mtime,
    ):
        shutil.copy2(launcher, shim)
    return folder


def _mark_from_internet(path: Path) -> None:
    """Tag a download so Windows warns before running it (Mark of the Web)."""
    if os.name != "nt":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as fh:
            fh.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError as e:
        log.warning("Could not mark %s as downloaded from the internet: %s", path, e)
