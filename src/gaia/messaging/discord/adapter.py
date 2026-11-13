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
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Set, Tuple

from gaia.automations import outbox
from gaia.automations import runner as automations
from gaia.automations import tick as automation_tick
from gaia.logger import get_logger
from gaia.messaging.bridge import (
    CONTROL_CLEAR_HISTORY,
    DECISION_ALLOW,
    DECISION_ALWAYS,
    DECISION_DENY,
    AgentChannel,
    Turn,
    resolve_agent_argv,
)
from gaia.messaging.discord import github_watch, state
from gaia.security import (
    SENSITIVE_EXTENSIONS,
    SENSITIVE_FILE_NAMES,
    is_scratch_path,
)

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
    "or name. Discord shows no tables: use a short bullet list instead. A "
    "file you write, a screenshot you take or an image you make is posted "
    "here on its own. To send any other file on this PC, put [[send: its full "
    "path]] on a line of its own in your reply; that line is removed before "
    "they see it, so never mention it or copy one from a file. Leave out the "
    "paths of your own working files. The "
    "user is likely on their phone: never tell them to run a command or paste "
    "one for them to run; name the GAIA app screen that does it, like Settings "
    "→ Connectors, or say what you could not do and leave it there. On the "
    "PC, Settings → Automations lists their reminders, alerts and triggers "
    "and pauses, resumes or removes one; changing what one does, and GitHub "
    "watches, happen by asking you here.)"
)

PAIRING_PROMPT = (
    "That isn't the current pairing code. Send the code shown right now on the "
    "PC that runs GAIA (Settings → Messaging → Discord in the app, or /discord "
    "in the terminal app) — it changes after an hour or a few wrong tries."
)

#: How to bring the bridge back after its agent was given up on. Names the app
#: actions, never a command: the user is on their phone.
#: What a placeholder left by a stopped bridge turns into.
INTERRUPTED_TEXT = (
    "-# Interrupted — GAIA stopped before it finished. Send it again if you "
    "still need it."
)
#: What an approval prompt left by a stopped bridge turns into.
EXPIRED_PROMPT_TEXT = "**Expired — GAIA stopped before you answered.** Nothing ran."
OPEN_PLACEHOLDER = "placeholder"
OPEN_PROMPT = "prompt"

RESTART_HINT = (
    "In the GAIA app, open Settings → Messaging and press Stop, then Start; or "
    "type /discord restart in the terminal app."
)

#: Discord rejects a message over 2000 characters; chunks leave room for the
#: tool trail and a reopened code fence.
DISCORD_MESSAGE_LIMIT = 2000
CHUNK_LIMIT = 1900

#: Discord's upload ceiling for a bot in a DM.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
#: How much of a replied-to message is quoted to the agent.
REPLY_QUOTE_CHARS = 400
#: Shared files older than this are removed when the bridge starts.
INBOX_KEEP_SECONDS = 14 * 24 * 3600

#: A message or click older than this is never executed.
STALE_SECONDS = 120.0
#: An edit to a message older than this is a tidy-up and gets no notice.
EDIT_WINDOW_SECONDS = 600.0
EDIT_NOTICE = (
    "-# I don't act on edited messages, so nothing ran again. Send the new "
    "wording as a new message."
)

CONFIRM_TIMEOUT_SECONDS = 600.0
#: Long enough to walk through setup without racing a rotation; the code is
#: only ever shown on this PC, and wrong guesses rotate it anyway.
PAIRING_TTL_SECONDS = 3600.0
PAIRING_MAX_GUESSES = 5

#: A lease newer than this belongs to a PC that is still answering.
LEASE_FRESH_SECONDS = 180.0
#: A foreign lease untouched this long belongs to a PC that is gone.
ABANDONED_LEASE_SECONDS = 86400.0
LEASE_PREFIX = "🟢 GAIA is answering from"
_LEASE_RE = re.compile(r"lease ([0-9a-f]{8,32}) at (\d+)")

BUTTON_ALLOW = "gaia:allow"
BUTTON_ALWAYS = "gaia:always"
BUTTON_DENY = "gaia:deny"
#: On a progress placeholder: stop what is running and what waits behind it.
BUTTON_STOP = "gaia:stop"
#: A reply that is only one of these answers the one approval waiting.
TYPED_DECISIONS = {
    **dict.fromkeys(
        (
            "yes",
            "y",
            "yep",
            "yeah",
            "ok",
            "okay",
            "sure",
            "allow",
            "allow it",
            "approve",
            "approved",
            "go ahead",
            "do it",
            "go",
            "yes please",
        ),
        DECISION_ALLOW,
    ),
    **dict.fromkeys(
        ("no", "n", "nope", "deny", "don't", "dont", "no thanks"), DECISION_DENY
    ),
    **dict.fromkeys(("always", "always allow", "always allow it"), DECISION_ALWAYS),
}
#: A message that is only one of these stops the work instead of queuing.
STOP_WORDS = frozenset(
    {"stop", "cancel", "abort", "nevermind", "never mind", "stop it", "halt"}
)
#: Ends the first answer after a start, when the DM holds an older conversation.
FORGOT_NOTICE = (
    "-# GAIA restarted since your last message, so it doesn't remember what was "
    "said here before. Anything you asked it to remember is still there."
)
#: A message that is only one of these starts a fresh conversation.
NEW_CHAT_WORDS = frozenset(
    {"new chat", "new conversation", "start over", "start fresh", "reset chat"}
)
NEW_CHAT_REPLY = (
    "-# Started a new conversation. I still remember what you've asked me to "
    "keep, and your reminders and alerts are unchanged."
)
#: A message that is only one of these gets :data:`HELP_TEXT`, not the model.
HELP_WORDS = frozenset({"help", "/help", "commands", "what can you do here"})
HELP_TEXT = "\n".join(
    (
        "**GAIA on Discord**",
        "- Ask anything, or attach a file for me to read.",
        "- I ask before I change anything: press a button, or reply *yes*, "
        "*no* or *always*.",
        "- *stop* ends what I'm doing. *new chat* starts a fresh conversation.",
        "- *retry* runs your last request again after an approval expired.",
        '- Reminders and alerts: "remind me at 5pm to…", "tell me when…", '
        '"what are you watching?".',
        '- Files I make are sent here. Ask "send me <file>" for any other file '
        "on the PC (up to 10 MB).",
    )
)
#: A message that is only one of these re-runs the request an expired approval ended.
RETRY_WORDS = frozenset({"retry", "try again", "run it again", "again"})
#: Tools whose result names a file made for the user, and the keys naming it.
PRODUCER_TOOLS = frozenset(
    {"take_screenshot", "generate_image", "generate_music", "generate_sound_effect"}
)
PRODUCED_FILE_KEYS = ("file_path", "image_path", "audio_path")
#: A reply line asking for a file on this PC to be attached.
SEND_MARK = re.compile(r"^[ \t]*\[\[send:\s*(.+?)\s*\]\][ \t]*$", re.MULTILINE)
#: Where the OS keeps app settings and sign-ins, directly under the home folder.
APP_DATA_FOLDERS = frozenset({"appdata", "library"})
#: Files sent from one reply, so a confused model cannot flood the DM.
MAX_SENDS_PER_REPLY = 5
_BUTTON_DECISIONS = {
    BUTTON_ALLOW: DECISION_ALLOW,
    BUTTON_ALWAYS: DECISION_ALWAYS,
    BUTTON_DENY: DECISION_DENY,
}
_DECISION_VERBS = {
    DECISION_ALLOW: "✅ Allowed",
    DECISION_ALWAYS: "✅ Always allowed",
    DECISION_DENY: "⛔ Denied",
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
    #: When the user last edited it (epoch seconds); 0 for a new message.
    edited_at: float = 0.0
    #: The text of the message this one replies to with Discord's Reply.
    reply_to: str = ""


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


#: The one button a progress placeholder carries.
STOP_BUTTONS = [Button(BUTTON_STOP, "Stop", "secondary")]


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
        self, channel_id: str, author_id: str, after_id: str, bot_id: str = ""
    ) -> int: ...

    def pins(self, channel_id: str) -> List[PinnedMessage]: ...

    def open_controls(
        self, channel_id: str, author_id: str, limit: int = 100
    ) -> List[Tuple[str, List[str]]]: ...

    def pin(self, channel_id: str, message_id: str) -> None: ...

    def unpin(self, channel_id: str, message_id: str) -> None: ...


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


def tool_words(tool: str) -> str:
    """``run_shell_command`` -> "Running a shell command", as the apps say it."""
    from gaia.ui.event_narration import derive_narration

    return derive_narration(tool)


def short_command(text: str, limit: int = 80) -> str:
    """The first line of *text*, clipped, for a one-line record of a prompt."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


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
    head = f"🔐 **Approve:** {neutralize(tool_words(action))}"
    body = f"{head}\n```\n{shown}\n```" if shown else head
    if "ˋ" in body or "⟨U+" in body:
        body += "\n-# Backticks are shown as ˋ and hidden characters as ⟨U+…⟩."
    return body, full


_TEXT_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".cs", ".sh", ".ps1", ".bat", ".md", ".txt", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".csv", ".html", ".css", ".sql", ".xml",
}  # fmt: skip


def render_file_write(action: str, event: Dict[str, Any]) -> Optional[tuple]:
    """``(text, (filename, content))`` for a long file write, else ``None``.

    A write is a path and a body, not a command: the prompt names the path and
    size, and the body goes along under the file's own name, which Discord
    previews as code. Hidden characters in the body are called out, since the
    preview shows them raw.
    """
    args = event.get("args")
    if not isinstance(args, dict):
        return None
    path, content = args.get("file_path"), args.get("content")
    if not isinstance(path, str) or not isinstance(content, str):
        return None
    if len(neutralize(content)) + len(path) <= _SHOWN_COMMAND_CHARS:
        return None
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE_NAME.sub("_", base).strip(". ") or "file"
    if Path(name).suffix.lower() not in _TEXT_SUFFIXES:
        name += ".txt"
    size = f"{len(content.splitlines()):,} lines · {len(content):,} characters"
    text = (
        f"🔐 **Approve:** {neutralize(tool_words(action))}\n"
        f"```\n{neutralize(path)}\n```\n-# {size} — the whole file is attached"
    )
    hidden = sorted({c for c in content if _visible(c) != c})
    if hidden:
        codes = ", ".join(f"U+{ord(c):04X}" for c in hidden[:5])
        text += f"\n-# ⚠️ The file contains hidden characters ({codes})."
    return text, (name, content)


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


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def untable(text: str) -> str:
    """Rewrite Markdown tables as bullet lists; Discord prints them as raw pipes.

    A table is a row of cells over a ``|---|`` rule. Each body row becomes
    ``• a · b · c``; the header is dropped, its words are in the rows' context.
    Code blocks are left alone.
    """
    lines = text.split("\n")
    out: List[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if (
            not in_fence
            and "|" in line
            and i + 1 < len(lines)
            and _TABLE_RULE.match(lines[i + 1])
        ):
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c for c in _cells(lines[i]) if c]
                if cells:
                    out.append("• " + " · ".join(cells))
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


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
            # Cut at a space when there is one, so a link is not split in two.
            cut = line.rfind(" ", room // 2, room) + 1 or room
            pieces.append(line[:cut])
            line = line[cut:]
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
    #: The tool whose result is awaited, and the question that started the turn.
    last_tool: str = ""
    question: str = ""
    #: A line put above the answer (a scheduled run's name).
    heading: str = ""
    #: The user stopped it; whatever the cancelled turn ends with is not shown.
    stopped: bool = False
    #: Shows nothing while it runs: an alert or trigger that may end in silence.
    quiet: bool = False
    #: Decides what of the final answer is posted; None from it posts nothing.
    answer_filter: Optional[Callable[[str], Optional[str]]] = None


@dataclass
class _Pending:
    confirm_id: str
    channel_id: str
    message_id: str
    label: str
    timer: Any
    #: What "Always allow" grants for this call; empty when it is not offered.
    scope: str = ""
    #: The agent's own keys for that grant, which are what get remembered.
    grant_keys: Tuple[str, ...] = ()
    #: The request that led here, offered again if the prompt expires.
    question: str = ""


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
        #: This DM has earlier messages the freshly started agent never saw.
        self._forgot_notice = bool(prior.last_message_id)
        self._refused: Set[str] = set(prior.refused_user_ids)
        #: message id -> (channel id, kind) of what is open right now.
        self._open: Dict[str, Any] = {}
        #: What a bridge that stopped without closing them left open.
        self._orphans = [
            item
            for item in prior.open_messages
            if isinstance(item, list) and len(item) == 3
        ]
        self._pairing_code = ""
        self._pairing_expires = 0.0
        self._pairing_misses = 0
        self._poller: Optional[github_watch.Poller] = None
        self._tick_lock = threading.Lock()
        self._closed = False
        #: The newest typed message still waiting behind a running turn, its
        #: reply target and every question folded into it so far.
        self._waiting: Optional[Any] = None
        #: Reply targets submitted and not finished yet.
        self._in_flight: List[ReplyTarget] = []
        #: The last automations failure the user was told about.
        self._automation_error = ""
        #: Queued deliveries whose turn failed to start.
        self._stalled: Set[str] = set()
        #: The DM was searched for buttons a dead bridge left; done once.
        self._scanned = False
        #: The request an expired approval ended; "retry" runs it again.
        self._retry_question = ""

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> str:
        return self._state

    def _set_state(self, new_state: str, detail: str = "", **extra: Any) -> None:
        with self._lock:
            self._state = new_state
            self._detail = detail
            state.write_status(
                new_state,
                detail,
                last_message_id=self._last_message_id,
                refused_user_ids=sorted(self._refused),
                open_messages=[
                    [ch, mid, kind] for mid, (ch, kind) in self._open.items()
                ]
                + self._orphans,
                pairing_code=(
                    self._pairing_code if new_state == state.STATE_PAIRING else ""
                ),
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
            env=agent_env(),
            on_busy=self._on_busy,
            restart_hint=RESTART_HINT,
        )
        self._channel.start()
        self._prune_inbox()

    def _prune_inbox(self) -> None:
        """Remove shared files nobody has needed for two weeks."""
        cutoff = time.time() - INBOX_KEEP_SECONDS
        folder = state.inbox_dir()
        if not folder.is_dir():
            return
        for item in folder.iterdir():
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
            except OSError as e:
                log.warning("Could not remove old shared file %s: %s", item, e)

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
        self._close_orphans()
        missed = 0
        if not resumed and self._last_message_id and self.config.dm_channel_id:
            missed = self.transport.count_from_after(
                self.config.dm_channel_id,
                self.config.owner_id,
                self._last_message_id,
                self.config.bot_id,
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
            waiting, self._waiting = self._waiting, None
        if waiting is not None and self._channel is not None:
            # Denying the prompts below lets the agent move on; the message
            # queued behind them must not start a turn nobody will see.
            self._channel.withdraw(waiting[0])
        for item in pending:
            item.timer.cancel()
            self._edit(
                item.channel_id,
                item.message_id,
                f"-# ⌛ Expired — the bridge stopped · {_record(item.label)}",
                clear_buttons=True,
            )
            self._untrack(item.message_id)
            if self._channel is not None:
                self._channel.decide(DECISION_DENY, item.confirm_id)
        with self._lock:
            placeholders = list(self._open.items())
        for message_id, (channel_id, _kind) in placeholders:
            self._edit(channel_id, message_id, INTERRUPTED_TEXT, clear_buttons=True)
            self._untrack(message_id)
        if self._channel is not None:
            self._channel.close()
        try:
            automation_tick.release(automation_tick.OWNER_DISCORD)
        except (state.StateFileError, OSError) as e:
            log.error("Could not release the automations tick: %s", e)

    # ------------------------------------------------------------------
    # Open messages: placeholders and prompts that must not outlive the bridge
    # ------------------------------------------------------------------

    def _buttoned_messages(self) -> List[Tuple[str, List[str]]]:
        try:
            return self.transport.open_controls(
                self.config.dm_channel_id, self.config.bot_id
            )
        except Exception as e:  # noqa: BLE001 - a failed scan leaves them as they are
            log.warning("Could not scan the DM for leftover buttons: %s", e)
            return []

    def _track(self, channel_id: str, message_id: str, kind: str) -> None:
        if not message_id:
            return
        with self._lock:
            self._open[message_id] = (channel_id, kind)
        self._refresh_status()

    def _untrack(self, message_id: str) -> None:
        with self._lock:
            known = self._open.pop(message_id, None)
        if known is not None:
            self._refresh_status()

    def _close_orphans(self) -> None:
        """Close what a bridge that crashed left open, once.

        The status file names what the last bridge knew it left; the DM's own
        recent history catches the rest (a crash before the file was written,
        a file that was lost): any message of this bot that still has buttons.
        """
        orphans, self._orphans = self._orphans, []
        if orphans:
            self._refresh_status()
        if not self._scanned and self.config.dm_channel_id:
            self._scanned = True
            known = {mid for _, mid, _ in orphans}
            with self._lock:
                known |= set(self._open)
            for message_id, ids in self._buttoned_messages():
                if message_id in known:
                    continue
                kind = OPEN_PLACEHOLDER if BUTTON_STOP in ids else OPEN_PROMPT
                orphans.append([self.config.dm_channel_id, message_id, kind])
        for channel_id, message_id, kind in orphans:
            if kind == OPEN_PROMPT:
                self._edit(
                    channel_id, message_id, EXPIRED_PROMPT_TEXT, clear_buttons=True
                )
            else:
                self._edit(channel_id, message_id, INTERRUPTED_TEXT, clear_buttons=True)

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
                try:
                    self._run_automations()
                    self._automation_error = ""
                except state.DamagedStateFile as e:
                    self._report_damage(e, "Ask me to set up your automations again.")
                except (state.StateFileError, ValueError, OSError) as e:
                    # Said once, not every minute; the GitHub watches still run.
                    log.error("Automations could not run: %s", e)
                    if str(e) != self._automation_error:
                        self._automation_error = str(e)
                        self._send(
                            self.config.dm_channel_id,
                            f"⚠️ Your automations can't run right now: "
                            f"{neutralize(str(e))}",
                        )
                try:
                    self._watcher().poll()
                except state.DamagedStateFile as e:
                    self._report_damage(e, "Ask me to watch your repos again.")
        elif self._state == state.STATE_TAKEN_OVER and self.claim_lease(force=False):
            # The other PC stopped refreshing its lease; answer again.
            self._set_state(state.STATE_CONNECTED)

    def _run_automations(self) -> None:
        """Deliver what the daemon queued; run what is due when no daemon does."""
        now = self._clock()
        if automation_tick.claim(automation_tick.OWNER_DISCORD, now):
            automations.run_due(_DiscordSink(self), now)
        sink = _DiscordSink(self)
        for delivery in outbox.pending():
            note = outbox.late_note(delivery, now)
            late = f"\n{note}" if note else ""
            if delivery.kind != outbox.KIND_TURN:
                if not sink.post(delivery.text + late):
                    # Kept for the next tick; the failed send is in the log.
                    continue
            else:
                try:
                    sink.start_turn(
                        delivery.text,
                        delivery.heading + late,
                        # A retry shows no placeholder: one dead message is enough.
                        quiet=delivery.quiet or delivery.id in self._stalled,
                        answer_filter=outbox.answer_filter(delivery),
                    )
                except Exception as e:  # noqa: BLE001 - kept queued, said once
                    self._stalled.add(delivery.id)
                    log.exception("Queued automation %s did not start", delivery.name)
                    # The rest waits too: the agent that failed would fail them.
                    raise ValueError(
                        f"{delivery.name} could not start: {e}. It runs when the "
                        f"agent answers again. {RESTART_HINT}"
                    ) from e
            self._stalled.discard(delivery.id)
            outbox.remove(delivery.id)

    def _always_keys(self) -> Set[str]:
        try:
            return state.always_keys(self.config.owner_id)
        except state.DamagedStateFile as e:
            self._report_damage(e, "Choose Always allow again where you want it.")
            return set()

    def _report_damage(self, error: state.DamagedStateFile, remedy: str) -> None:
        self._send(self.config.dm_channel_id, f"⚠️ {neutralize(str(error))} {remedy}")

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
        abandoned: List[str] = []
        for pin in self.transport.pins(channel):
            if pin.author_id != self.config.bot_id:
                continue
            match = _LEASE_RE.search(pin.content)
            if not match:
                continue
            if match.group(1) == self.config.instance_id:
                ours = pin
            else:
                at = int(match.group(2))
                foreign_at = max(foreign_at, at)
                if now - at > ABANDONED_LEASE_SECONDS:
                    abandoned.append(pin.id)
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
        for message_id in abandoned:
            # A PC that went quiet a day ago is gone; its pin would otherwise
            # stay until the DM's pin limit makes pinning ours fail.
            try:
                self.transport.unpin(channel, message_id)
            except Exception as e:  # noqa: BLE001 - the lease itself is held
                log.warning("Could not unpin an abandoned Discord lease: %s", e)
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
        if not msg.attachments and self._answer_by_text(msg):
            return
        words = "" if msg.attachments else _only_words(msg.content)
        if words in STOP_WORDS:
            self._stop_work(msg.channel_id)
            return
        if words in HELP_WORDS:
            self._send(msg.channel_id, HELP_TEXT)
            return
        if words in NEW_CHAT_WORDS:
            self._new_chat(msg.channel_id, msg.id)
            return
        retry, self._retry_question = self._retry_question, ""
        if words in RETRY_WORDS and retry:
            question = retry
        elif msg.reply_to:
            quoted = " ".join(msg.reply_to.split())[:REPLY_QUOTE_CHARS]
            question = f'(Replying to this earlier message: "{quoted}")\n{question}'
        if self._fold_into_waiting(question):
            return
        self._start_turn(msg.channel_id, msg.id, question, "-# Thinking…")

    def handle_edit(self, msg: InboundMessage) -> None:
        """Say, once, that an edit was not acted on.

        An edit never runs: a message changed after its approval must not run
        again. Ignoring it silently left a fixed typo unanswered with no hint
        why, so the user is told to send the new wording as a new message.
        """
        if (
            msg.author_is_bot
            or not msg.is_dm
            or msg.author_id != self.config.owner_id
            or self._state != state.STATE_CONNECTED
        ):
            return
        now = self._clock()
        try:
            age = now - snowflake_time(msg.id)
        except ValueError:
            return
        if age > EDIT_WINDOW_SECONDS or now - msg.edited_at > STALE_SECONDS:
            return
        if not self._first_sight(f"edit:{msg.id}"):
            return
        audit.info("Did not run edited Discord message %s: edits never run", msg.id)
        self._send(msg.channel_id, EDIT_NOTICE)

    def _new_chat(self, channel_id: str, message_id: str) -> None:
        """Clear the agent's conversation, in its turn behind anything running."""
        target = ReplyTarget(
            channel_id=channel_id,
            message_id=message_id,
            answer_filter=lambda _answer: NEW_CHAT_REPLY,
        )
        turn = Turn(
            text="",
            context=target,
            sender=self.config.owner_id,
            control=CONTROL_CLEAR_HISTORY,
        )
        with self._lock:
            self._in_flight.append(target)
            self._waiting = None
        audit.info("The paired user started a new conversation")
        self._agent().submit(turn)

    def _stop_work(self, channel_id: str) -> None:
        """Withdraw the waiting message and cancel the running one."""
        stopped = False
        with self._lock:
            waiting, self._waiting = self._waiting, None
        if waiting is not None:
            turn, target, _questions = waiting
            if self._agent().withdraw(turn):
                stopped = True
                with self._lock:
                    if target in self._in_flight:
                        self._in_flight.remove(target)
                if target.reply_id:
                    self._edit(
                        target.channel_id,
                        target.reply_id,
                        "-# Cancelled — not run.",
                        clear_buttons=True,
                    )
                    self._untrack(target.reply_id)
        if self._agent().busy:
            stopped = True
            audit.info("The paired user stopped the running turn")
            with self._lock:
                for target in self._in_flight:
                    target.stopped = True
            self._agent().cancel()
        self._send(channel_id, "-# Stopped." if stopped else "-# Nothing is running.")

    def _fold_into_waiting(self, question: str) -> bool:
        """Add *question* to the message still waiting, so both are answered
        together: a quick follow-up often corrects the one before it."""
        with self._lock:
            waiting = self._waiting
            if waiting is None:
                return False
            turn, target, questions = waiting
            merged = questions + [question]
            if not self._agent().amend(turn, _turn_text(merged)):
                self._waiting = None
                return False
            questions.append(question)
        self._update(
            target,
            f"-# Queued — I'll answer your {len(questions)} messages together "
            "once the current one is done…",
        )
        return True

    def _start_turn(
        self,
        channel_id: str,
        message_id: str,
        question: str,
        placeholder: Optional[str],
        heading: str = "",
        *,
        quiet: bool = False,
        answer_filter: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        target = ReplyTarget(
            channel_id=channel_id,
            message_id=message_id,
            heading=heading,
            quiet=quiet,
            answer_filter=answer_filter,
            question=question if message_id else "",
        )
        if placeholder is not None and not quiet:
            target.reply_id = self._send(channel_id, placeholder, buttons=STOP_BUTTONS)
            self._track(channel_id, target.reply_id, OPEN_PLACEHOLDER)
        turn = Turn(
            text=_turn_text([question]),
            context=target,
            sender=self.config.owner_id,
        )
        with self._lock:
            self._in_flight.append(target)
        try:
            ahead = self._agent().submit(turn)
        except Exception:
            # Nothing will ever answer under it: close it, then say why upstream.
            with self._lock:
                self._in_flight.remove(target)
            if target.reply_id:
                self._edit(
                    channel_id, target.reply_id, INTERRUPTED_TEXT, clear_buttons=True
                )
                self._untrack(target.reply_id)
            raise
        # Only typed messages are folded together; a schedule stands alone.
        with self._lock:
            self._waiting = (turn, target, [question]) if ahead and message_id else None

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
        if click.custom_id == BUTTON_STOP:
            self._stop_work(click.channel_id)
            return None
        decision = _BUTTON_DECISIONS.get(click.custom_id)
        if decision is None:
            return None
        if click.interaction_id and self._is_stale(click.interaction_id):
            return "That click arrived too late to count. Nothing was run."
        with self._lock:
            pending = self._pending.pop(click.message_id, None)
        if pending is None:
            return "That prompt has expired or was already answered."
        self._apply_decision(pending, decision)
        return None

    def _answer_by_text(self, msg: InboundMessage) -> bool:
        """A bare "yes" / "no" / "always" answers the one approval waiting.

        People reply in words; queuing the word behind the turn that waits for
        it leaves both stuck until the prompt expires as a deny.
        """
        decision = TYPED_DECISIONS.get(
            " ".join(msg.content.lower().strip(" .!").split())
        )
        if decision is None:
            return False
        with self._lock:
            waiting = list(self._pending.values())
            if len(waiting) == 1 and not (
                decision == DECISION_ALWAYS and not waiting[0].scope
            ):
                pending = self._pending.pop(waiting[0].message_id)
            else:
                pending = None
        if pending is not None:
            self._apply_decision(pending, decision)
            return True
        if not waiting:
            return False  # Nothing to answer: it is just a message.
        if len(waiting) > 1:
            note = f"{len(waiting)} approvals are waiting — tap the button on the one you mean."
        else:
            note = "That one can't be allowed always — tap Allow or Deny."
        self._send(msg.channel_id, f"-# {note}")
        return True

    def _apply_decision(self, pending: "_Pending", decision: str) -> None:
        pending.timer.cancel()
        audit.warning(
            "Paired Discord user answered %r for confirmation %s",
            decision,
            pending.confirm_id,
        )
        self._agent().decide(decision, pending.confirm_id)
        self._untrack(pending.message_id)
        if decision == DECISION_ALWAYS and pending.grant_keys:
            try:
                state.remember_always(
                    self.config.owner_id, pending.grant_keys, pending.scope
                )
            except (state.BridgeStateError, OSError) as e:
                log.error("Could not remember Always allow %s: %s", pending.scope, e)
                self._send(
                    pending.channel_id,
                    f"-# ⚠️ Allowed this time, but GAIA could not save Always "
                    f"allow, so it will ask again after a restart: "
                    f"{neutralize(str(e))}",
                )
        self._edit(
            pending.channel_id,
            pending.message_id,
            f"-# {_DECISION_VERBS[decision]} · {_record(pending.label)}",
            clear_buttons=True,
        )

    def _expire(self, message_id: str) -> None:
        with self._lock:
            pending = self._pending.pop(message_id, None)
        if pending is None:
            return
        audit.warning(
            "Denied %r: no answer within %ss", pending.label, int(self.confirm_timeout)
        )
        self._agent().decide(DECISION_DENY, pending.confirm_id)
        self._untrack(pending.message_id)
        self._retry_question = pending.question
        again = " Send **retry** to run it again." if pending.question else ""
        self._edit(
            pending.channel_id,
            pending.message_id,
            f"-# ⛔ Denied — no answer within {int(self.confirm_timeout // 60)} "
            f"minutes · {_record(pending.label)}{again}",
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
            with self._lock:
                asking = bool(self._pending)
            self._update(
                target,
                (
                    "-# Queued — GAIA is waiting for your answer above (tap a button, "
                    "or reply yes or no)…"
                    if asking
                    else f"-# Queued — finishing {ahead} earlier task"
                    f"{'s' if ahead != 1 else ''} first…"
                ),
            )

    # ------------------------------------------------------------------
    # Outbound — rendering agent events
    # ------------------------------------------------------------------

    def _on_agent_event(self, event: Dict[str, Any], turn: Turn) -> None:
        target = turn.context
        if not isinstance(target, ReplyTarget):
            log.warning("Dropped an agent event with no Discord reply target")
            return
        if self._closed:
            # Closing already marked everything interrupted; nothing new may
            # appear after it, nor be left behind for the next bridge.
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
            target.last_tool = tool
            self._update(target, f"-# {neutralize(tool_words(tool))}…")
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
            self._finish(
                target,
                f"⚠️ {event.get('detail') or 'The agent failed.'}",
                failed=True,
            )

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
        scope = str(event.get("always_scope") or "")
        grant_keys = _grant_keys(action, event) if scope else ()
        record = short_command(confirmation_detail(event) or summary) or tool_words(
            action
        )
        if grant_keys and set(grant_keys) <= self._always_keys():
            # Answered "always" before; the agent restarted and forgot it.
            audit.warning(
                "Allowed %r without asking: the paired user chose Always allow %s",
                action,
                scope,
            )
            self._agent().decide(DECISION_ALWAYS, confirm_id)
            self._send(
                target.channel_id,
                f"-# ✅ Allowed — you chose Always allow · {_record(record)}",
            )
            return
        written = render_file_write(action, event)
        if written:
            text, attachment = written
        else:
            text, full = render_confirmation(
                action, confirmation_detail(event) or summary
            )
            attachment = ("command.txt", full) if full else None
        if target.heading:
            # No message of the user's led here: say which automation asks.
            text = f"-# Asked by the automation {target.heading}\n{text}"
        risk = (event.get("prompt_context") or {}).get("risk_note")
        if risk:
            text += f"\n-# {neutralize(str(risk))}"
        buttons = [Button(BUTTON_ALLOW, "Allow", "primary")]
        if scope:
            buttons.append(Button(BUTTON_ALWAYS, f"Always allow {scope}"[:80]))
        buttons.append(Button(BUTTON_DENY, "Deny", "danger"))
        message_id = self._send(
            target.channel_id,
            text,
            buttons=buttons,
            file_text=attachment,
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
                confirm_id,
                target.channel_id,
                message_id,
                record,
                timer,
                scope,
                grant_keys,
                target.question,
            )
        timer.start()
        self._track(target.channel_id, message_id, OPEN_PROMPT)

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
        """Send back the file the preceding tool wrote or made, within limits."""
        raw = target.pending_upload
        target.pending_upload = ""
        tool, target.last_tool = target.last_tool, ""
        data = event.get("data")
        if isinstance(data, dict) and (
            data.get("success") is False or data.get("status") == "error"
        ):
            return
        if not raw and tool in PRODUCER_TOOLS and isinstance(data, dict):
            raw = next(
                (
                    data[key]
                    for key in PRODUCED_FILE_KEYS
                    if isinstance(data.get(key), str) and data[key]
                ),
                "",
            )
        if raw:
            self._upload(target, raw)

    def _upload(self, target: ReplyTarget, raw: str, *, asked: bool = False) -> None:
        """Post one file from this PC in the DM.

        *asked* is a file the reply named with ``[[send: …]]``: a refusal is
        then said in the DM, since the user is waiting for that file.
        """

        def refuse(why: str) -> None:
            audit.warning("Did not send %s to Discord: %s", raw, why)
            if asked:
                self._send(target.channel_id, f"-# Not sent: {why}")

        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, ValueError) as e:
            refuse(f"`{neutralize(raw)}` is not a path I can read ({e}).")
            return
        key = str(path)
        if key in target.uploaded:
            return
        name = f"`{neutralize(path.name)}`"
        if not path.is_file():
            refuse(f"{name} is not a file on this PC.")
            return
        if is_scratch_path(path) and not asked:
            # The agent's own working files (a smoke-test log), not a deliverable.
            return
        roots = [r for r in self.upload_roots if path == r or r in path.parents]
        if not roots:
            refuse(f"{name} is outside the folders GAIA may send files from.")
            return
        if (
            path.name.lower() in {n.lower() for n in SENSITIVE_FILE_NAMES}
            or path.suffix.lower() in SENSITIVE_EXTENSIONS
        ):
            refuse(f"{name} looks like a key or password file, so it stays on your PC.")
            return
        if asked and _holds_settings(path, max(roots, key=lambda r: len(r.parts))):
            refuse(
                f"{name} is in a hidden or app-settings folder, where sign-ins are "
                "kept, so it stays on your PC."
            )
            return
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            self._send(
                target.channel_id,
                f"-# {name} is larger than Discord's 10 MB limit, "
                "so it stays on your PC.",
            )
            return
        if asked and len(target.uploaded) >= MAX_SENDS_PER_REPLY:
            refuse(f"{name}: only {MAX_SENDS_PER_REPLY} files are sent per reply.")
            return
        target.uploaded.add(key)
        self._send(target.channel_id, f"Here's {name}.", file_path=path)

    def _finish(self, target: ReplyTarget, answer: str, failed: bool = False) -> None:
        """Post the answer as new messages below everything else, then drop the
        progress placeholder. A new message notifies the phone; an edit does not."""
        with self._lock:
            if target in self._in_flight:
                self._in_flight.remove(target)
        if target.stopped:
            # "Stopped." already answered; the cancelled turn's ending is noise.
            if target.reply_id:
                self._delete(target.channel_id, target.reply_id)
                target.reply_id = ""
            return
        answer = answer or target.text
        asked: List[str] = []
        only_files = False
        if not failed and SEND_MARK.search(answer):
            asked = [raw.strip("`\"' ") for raw in SEND_MARK.findall(answer)]
            answer = re.sub(r"\n{3,}", "\n\n", SEND_MARK.sub("", answer)).strip()
            only_files = not answer
            answer = answer or "-# Sent."
        # A failure is shown as it is: it is not an alert's news, nor silence.
        if target.answer_filter is not None and not failed:
            kept = target.answer_filter(answer)
            if kept is None:
                if target.reply_id:
                    self._delete(target.channel_id, target.reply_id)
                    target.reply_id = ""
                return
            answer = kept
        if target.heading:
            answer = f"{target.heading}\n{answer}"
        if self._forgot_notice and target.question and target.answer_filter is None:
            self._forgot_notice = False
            answer = f"{answer}\n\n{FORGOT_NOTICE}"
        if only_files:
            # Nothing else to say: the files are the answer, then "Sent."
            for raw in asked:
                self._upload(target, raw, asked=True)
            asked = []
        for chunk in split_message(untable(answer) or "-# (no answer)"):
            self._send(target.channel_id, chunk)
        # After the words, so a refusal is the last thing said about the file.
        for raw in asked:
            self._upload(target, raw, asked=True)
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
        self._untrack(message_id)
        try:
            self.transport.delete(channel_id, message_id)
        except (
            Exception
        ) as e:  # noqa: BLE001 - a stale placeholder must not kill the turn
            log.warning("Could not delete Discord message %s: %s", message_id, e)

    def _update(self, target: ReplyTarget, text: str) -> None:
        if target.quiet:
            return
        if target.heading:
            text = f"{target.heading}\n{text}"
        if not target.reply_id:
            target.reply_id = self._send(target.channel_id, text, buttons=STOP_BUTTONS)
            self._track(target.channel_id, target.reply_id, OPEN_PLACEHOLDER)
            return
        self._edit(target.channel_id, target.reply_id, text)


def _holds_settings(path: Path, root: Path) -> bool:
    """Whether *path*, somewhere below *root*, is where apps keep sign-ins.

    A hidden file or folder anywhere on the way down, or the OS's app-data
    folder directly under the home folder. A folder the user picked as a root
    is theirs to send from, whatever it is called.
    """
    parts = path.relative_to(root).parts
    if any(part.startswith(".") for part in parts):
        return True
    return (
        root == Path.home().resolve()
        and len(parts) > 1
        and parts[0].lower() in APP_DATA_FOLDERS
    )


def _only_words(text: str) -> str:
    """*text* lowered and trimmed, for matching a message that is one phrase."""
    return " ".join(text.lower().strip(" .!?").split())


def _grant_keys(action: str, event: Dict[str, Any]) -> Tuple[str, ...]:
    """The agent's grant keys for this call, or none when they can't be known."""
    from gaia.agents.base.tool_grants import grant_scope

    args = event.get("args")
    if event.get("args_truncated") or not isinstance(args, dict):
        return ()
    scope = grant_scope(action, args)
    return tuple(scope.keys) if scope is not None else ()


def _record(label: str) -> str:
    return f"`{neutralize(label)}`" if label else ""


class _DiscordSink:
    """Where automations land when the Discord bridge runs them: the paired DM."""

    def __init__(self, adapter: "DiscordAdapter") -> None:
        self._adapter = adapter

    def post(self, text: str) -> bool:
        return bool(self._adapter._send(self._adapter.config.dm_channel_id, text))

    def start_turn(
        self,
        prompt: str,
        heading: str,
        *,
        quiet: bool,
        answer_filter: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._adapter._start_turn(
            self._adapter.config.dm_channel_id,
            "",
            prompt,
            f"{heading}\n-# Running…",
            heading=heading,
            quiet=quiet,
            answer_filter=answer_filter,
        )

    def check_env(self) -> Optional[Dict[str, str]]:
        return agent_env()


def _turn_text(questions: List[str]) -> str:
    if len(questions) == 1:
        return f"{questions[0]}\n\n{DISCORD_REPLY_NOTE}"
    joined = "\n\n".join(questions)
    return (
        f"{joined}\n\n(The user sent these {len(questions)} messages in a row; "
        "answer them together in one reply. A later one can change or cancel an "
        f"earlier one.)\n\n{DISCORD_REPLY_NOTE}"
    )


def agent_env() -> Dict[str, str]:
    """The agent's environment: this install's ``gaia`` on PATH, no bot token.

    The automations skill runs ``gaia automation ...``, and an app install
    keeps that CLI beside this interpreter, not on PATH. Only a ``gaia`` shim is
    added: putting the interpreter's folder on PATH would hand the agent GAIA's
    own ``python`` and ``pip``. The token stays out of every shell it runs.
    """
    from gaia.messaging.discord.credentials import BOT_TOKEN_ENV_VAR

    env = {k: v for k, v in os.environ.items() if k != BOT_TOKEN_ENV_VAR}
    env["PATH"] = str(_gaia_shim_dir()) + os.pathsep + env.get("PATH", "")
    return env


def _gaia_launcher() -> Optional[Path]:
    """The ``gaia.exe`` console-script launcher beside this interpreter."""
    import sys

    here = Path(sys.executable).parent
    return next(
        (p for p in (here / "gaia.exe", here / "Scripts" / "gaia.exe") if p.is_file()),
        None,
    )


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
    launcher = _gaia_launcher()
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
