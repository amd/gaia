# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Slack Socket Mode adapter — drive the flagship agent from a Slack DM.

Unlike the Telegram adapter, which reaches a bare ``AgentSDK`` with no tool loop,
this one drives the real flagship agent: it can read your files, search your
documents, and run commands. That is the feature, and it is why the guard rails
below are not optional extras.

**The gate that makes this safe is the one that already exists.** The base agent
puts every shell, file-write and code-execution tool behind
``TOOLS_REQUIRING_CONFIRMATION``, which pauses the turn and emits a
``needs_confirmation`` event. This adapter renders that pause as Block Kit
buttons and sends the click back over the bridge's control channel. So a remote
message can *ask* for a dangerous tool; it cannot *run* one unattended.

What remains reachable without a prompt is reading — files under the agent's
``allowed_paths`` (the user's home by default), indexed documents, and the web.
An allowlisted Slack user can therefore read your files. That is stated plainly
in the setup confirmation rather than buried, because it is the real boundary.

Three further rules, each load-bearing:

* **Socket Mode only.** An outbound WebSocket: no inbound port, no public URL,
  no request-signing secret to leak.
* **DM-only, allowlisted, one workspace.** The adapter refuses to start without
  an allowlist, ignores anything that is not a direct message, and drops traffic
  from a workspace other than the one it was set up for.
* **Bypass is unreachable.** The bridge exposes no way to send the agent's
  ``bypass`` control verb, so no Slack message or button can disarm the gate.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from gaia.logger import get_logger
from gaia.messaging.bridge import (
    DECISION_ALLOW,
    DECISION_ALWAYS,
    DECISION_DENY,
    AgentChannel,
    StreamThrottle,
    Turn,
)

log = get_logger(__name__)

#: Audit trail for every authorization decision this adapter makes. Pinned at
#: WARNING so a refusal survives the default log level — a silent refusal is
#: indistinguishable from a broken bot.
audit = get_logger("gaia.messaging.slack.audit")

#: What an unauthorized sender is told. Deliberately says nothing about who IS
#: allowed: a stranger who found the bot learns only that they are not.
UNAUTHORIZED_REPLY = "Sorry — you're not authorized to use this GAIA agent."

#: Refusal text when no allowlist is configured. One string so the CLI and the
#: exception cannot drift.
NO_ALLOWLIST_ERROR = (
    "Slack adapter refused to start: no allowed-users configured.\n"
    "\n"
    "This bridge drives the full GAIA agent, which can read your files and "
    "ask to run commands. Everyone in the workspace can DM a bot, so an empty "
    "allowlist would offer that to all of them.\n"
    "Pass the Slack member IDs permitted to use it:\n"
    "\n"
    "  gaia slack start --allowed-users U024BE7LH,U0G9QF9C6\n"
    "\n"
    "Find your own ID in Slack: click your avatar -> Profile -> the ... menu "
    "-> Copy member ID.\n"
    "Docs: https://amd-gaia.ai/docs/guides/slack"
)

#: Largest file uploaded back to Slack. Slack's own ceiling is 1 GB, but a
#: multi-hundred-MB upload from a chat reply is far more likely to be a mistake
#: than an intent, and it would block the turn while it transferred.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Largest inbound file accepted for ingestion, for the same reason.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024

#: Slack hard-caps a message at 4000 characters; past that ``chat.update``
#: rejects the edit outright and the reply would vanish at the finish line.
SLACK_MESSAGE_LIMIT = 3900

#: Seconds between streamed edits. ``chat.update`` is rate-limited at roughly
#: one call per second per channel.
EDIT_INTERVAL = 1.0

#: Block action ids for the three answers to a confirmation.
ACTION_ALLOW = "gaia_allow"
ACTION_ALWAYS = "gaia_always"
ACTION_DENY = "gaia_deny"

_ACTION_DECISIONS = {
    ACTION_ALLOW: DECISION_ALLOW,
    ACTION_ALWAYS: DECISION_ALWAYS,
    ACTION_DENY: DECISION_DENY,
}


#: Refusal text for more than one allowed member. The bridge drives ONE agent
#: process with one conversation history and one set of "always" grants, so a
#: second member would read the first's history and inherit their approvals.
MULTI_USER_ERROR = (
    "Slack adapter refused to start: more than one allowed user.\n"
    "\n"
    "This bridge drives a single agent with a single conversation, so every "
    "allowed member would share one history — anyone could ask what the "
    "others asked or read — and one set of 'Always allow' approvals.\n"
    "Pass exactly one Slack member ID:\n"
    "\n"
    "  gaia slack start --allowed-users U024BE7LH\n"
    "\n"
    "Docs: https://amd-gaia.ai/docs/guides/slack"
)

#: Tools whose ``tool_call`` args name the file they write. The canonical
#: ``tool_result`` carries only a summary, so the path is taken from the call.
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "write_markdown_file",
        "write_python_file",
        "edit_python_file",
    }
)

#: Seconds an approval prompt waits before it is denied. The agent itself waits
#: forever, and it is the bridge's only agent, so an unanswered prompt would
#: otherwise block every later message.
CONFIRM_TIMEOUT_SECONDS = 600

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

#: Domains a shared file may be fetched from. The bot token rides along as a
#: bearer header, so a URL anywhere else would hand it to that host.
_SLACK_FILE_DOMAINS = ("slack.com", "slack-edge.com")


def _is_slack_file_url(url: str) -> bool:
    """True for an https URL on a Slack-owned host.

    Compares ``hostname``, not ``netloc``, so userinfo and a port are stripped
    before matching: ``files.slack.com@evil.com`` is judged as evil.com, and a
    Slack host with an explicit port is still recognised.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == d or host.endswith("." + d) for d in _SLACK_FILE_DOMAINS
    )


def inbox_dir() -> Path:
    """Where files shared over Slack are saved for the agent to read."""
    base = os.environ.get("GAIA_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".gaia"
    return root / "slack" / "inbox"


class SlackAllowlistError(ValueError):
    """The adapter was asked to run without an allowlist.

    Subclasses ``ValueError`` so the CLI can catch this specific failure rather
    than any ``ValueError`` raised while connecting.
    """


class SlackDependencyError(RuntimeError):
    """``slack_sdk`` is not installed."""


@dataclass
class ReplyTarget:
    """Where one turn's output goes, and the live state of that reply."""

    channel: str
    #: Timestamp of the user's message — replies thread under it, so a busy
    #: channel does not interleave two conversations.
    thread_ts: str
    #: Timestamp of the placeholder being edited as tokens arrive.
    reply_ts: str = ""
    throttle: Optional[StreamThrottle] = None
    #: Tool names seen this turn, rendered as a trail under the reply.
    tools: List[str] = field(default_factory=list)
    #: Files already uploaded this turn, so a re-reported path is not sent twice.
    uploaded: Set[str] = field(default_factory=set)
    #: Path the most recent write tool was asked to write, uploaded when its
    #: result arrives.
    pending_upload: str = ""


def _truncate(text: str) -> str:
    """Clip a reply to Slack's message ceiling, saying so."""
    if len(text) <= SLACK_MESSAGE_LIMIT:
        return text
    return text[:SLACK_MESSAGE_LIMIT] + "\n\n_…truncated — ask for the rest._"


def confirmation_blocks(
    action: str, summary: str, always_scope: str = ""
) -> List[dict]:
    """Render a pending tool approval as Block Kit.

    ``always_scope`` decides whether "Always allow" appears at all. The agent
    supplies it only when the call has a scope narrow enough to grant standing
    permission to; offering the button without one would grant far more than the
    user believes they are granting.
    """
    elements = [
        {
            "type": "button",
            "action_id": ACTION_ALLOW,
            "text": {"type": "plain_text", "text": "Allow"},
            "style": "primary",
        }
    ]
    if always_scope:
        elements.append(
            {
                "type": "button",
                "action_id": ACTION_ALWAYS,
                "text": {"type": "plain_text", "text": f"Always allow {always_scope}"},
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": ACTION_DENY,
            "text": {"type": "plain_text", "text": "Deny"},
            "style": "danger",
        }
    )
    body = f"*GAIA wants to {action}*"
    if summary:
        body += f"\n```{summary}```"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "actions", "elements": elements},
    ]


class SlackAdapter:
    """Bridge a Slack workspace to one local flagship agent."""

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        allowed_users: Optional[Set[str]] = None,
        *,
        team_id: Optional[str] = None,
        agent_argv: Optional[Sequence[str]] = None,
        upload_roots: Optional[Sequence[str]] = None,
        deny_gated_tools: bool = False,
        channel_factory: Optional[Callable[..., AgentChannel]] = None,
        web_client: Any = None,
        confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS,
    ) -> None:
        # Refused here rather than in start(): an adapter that should serve
        # nobody must never exist, so no later caller can reach a permissive one.
        if not allowed_users:
            raise SlackAllowlistError(NO_ALLOWLIST_ERROR)
        # A bare string would iterate into single characters and deny everyone,
        # which reads as "the bot is broken" rather than "wrong type".
        if isinstance(allowed_users, (str, bytes)):
            raise TypeError(
                "allowed_users must be a collection of Slack member IDs, got "
                f"{type(allowed_users).__name__}. Pass {{'U024BE7LH'}}, not "
                "'U024BE7LH'."
            )
        if len(allowed_users) > 1:
            raise SlackAllowlistError(MULTI_USER_ERROR)
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_users = set(allowed_users)
        self.team_id = team_id
        self.deny_gated_tools = deny_gated_tools
        self.confirm_timeout = confirm_timeout
        # Default to the same scope the agent itself reads from, so a file the
        # user asked the agent to write can be handed back without extra setup.
        self.upload_roots = [
            Path(p).expanduser().resolve() for p in (upload_roots or [str(Path.home())])
        ]
        self._web = web_client
        self._socket: Any = None
        self._agent_argv = list(agent_argv) if agent_argv else None
        self._channel_factory = channel_factory or AgentChannel
        self._channel: Optional[AgentChannel] = None
        self._targets: Dict[str, ReplyTarget] = {}
        self._lock = threading.RLock()
        #: Message ts of each posted confirmation, keyed by confirm_id, so the
        #: buttons can be replaced with the decision once one is made.
        self._confirmations: Dict[str, tuple] = {}
        audit.info(
            "Slack adapter configured for %d allowed member id(s)%s",
            len(self.allowed_users),
            f", workspace {team_id}" if team_id else "",
        )

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def _allowed(self, user_id: Optional[str]) -> bool:
        """Membership only — an empty allowlist admits nobody.

        Defence in depth: ``__init__`` already refuses an empty allowlist, so
        this only matters if one is emptied after construction.
        """
        return bool(user_id) and user_id in self.allowed_users

    def _same_workspace(self, team_id: Optional[str]) -> bool:
        """True when the traffic came from the workspace this was set up for.

        Unpinned (``team_id=None``) accepts any, which is only the case before a
        first successful ``auth.test``. Once pinned, a token that has been
        installed elsewhere is refused rather than served.
        """
        if not self.team_id:
            return True
        return team_id == self.team_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_web_client(self) -> Any:
        if self._web is not None:
            return self._web
        try:
            from slack_sdk import WebClient
        except ImportError as e:
            raise SlackDependencyError(
                "slack-sdk is required for Slack support. Install it with: "
                'pip install "amd-gaia[slack]" '
                "(see https://amd-gaia.ai/docs/guides/slack)"
            ) from e
        self._web = WebClient(token=self.bot_token)
        return self._web

    def verify(self) -> Dict[str, Any]:
        """Prove the bot token works and learn which workspace it belongs to.

        Called before the socket opens so a bad token fails with Slack's own
        reason rather than as an opaque WebSocket handshake failure.
        """
        web = self._build_web_client()
        response = web.auth_test()
        data = response.data if hasattr(response, "data") else response
        if not data.get("ok", False):
            raise RuntimeError(
                f"Slack rejected the bot token: {data.get('error', 'unknown error')}. "
                f"Re-copy the 'xoxb-' token from OAuth & Permissions, or re-run "
                f"`gaia slack setup`."
            )
        if not self.team_id:
            self.team_id = data.get("team_id")
        return dict(data)

    def start(self, *, connect: bool = True) -> None:
        """Verify the token, start the agent child, and open the socket."""
        self.verify()
        self._channel = self._channel_factory(
            on_event=self._on_agent_event,
            argv=self._agent_argv,
            on_busy=self._on_busy,
        )
        self._channel.start()
        if connect:
            self._connect()

    def _connect(self) -> None:
        try:
            from slack_sdk.socket_mode import SocketModeClient
        except ImportError as e:
            raise SlackDependencyError(
                "slack-sdk is required for Slack support. Install it with: "
                'pip install "amd-gaia[slack]" '
                "(see https://amd-gaia.ai/docs/guides/slack)"
            ) from e
        self._socket = SocketModeClient(
            app_token=self.app_token, web_client=self._build_web_client()
        )
        self._socket.socket_mode_request_listeners.append(self.handle_request)
        self._socket.connect()
        log.info("Slack Socket Mode connected for workspace %s", self.team_id)

    def _agent(self) -> AgentChannel:
        """The agent channel, or an actionable error if start() never ran.

        Not an ``assert``: ``python -O`` strips those, and this would then
        surface as ``NoneType has no attribute submit`` from inside a Slack
        event handler, naming neither the cause nor the fix.
        """
        if self._channel is None:
            raise RuntimeError(
                "The Slack adapter has no agent channel — start() was never "
                "called, or it failed. Restart with `gaia slack start "
                "--allowed-users <ids>`."
            )
        return self._channel

    def close(self) -> None:
        """Disconnect and stop the agent child."""
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception as e:  # noqa: BLE001 - shutdown must not raise
                log.debug("Slack socket already closed: %s", e)
        with self._lock:
            pending = list(self._confirmations.values())
            self._confirmations.clear()
        for *_, timer in pending:
            timer.cancel()
        if self._channel is not None:
            self._channel.close()

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def handle_request(self, client: Any, req: Any) -> None:
        """Dispatch one Socket Mode request.

        Every request is acknowledged first: Slack retries anything unacked
        within three seconds, and a turn takes far longer than that, so a late
        ack turns one question into three.
        """
        self._ack(client, req)
        try:
            if req.type == "events_api":
                self._handle_event(req.payload or {})
            elif req.type == "interactive":
                self._handle_interactive(req.payload or {})
        except Exception:
            # A raised handler kills the socket's listener thread and the bot
            # goes quiet with no indication why.
            log.exception("Slack request handler failed for type %r", req.type)

    @staticmethod
    def _ack(client: Any, req: Any) -> None:
        try:
            from slack_sdk.socket_mode.response import SocketModeResponse

            client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id)
            )
        except ImportError:
            log.debug("slack_sdk missing while acking — test context")
        except Exception as e:  # noqa: BLE001 - a failed ack must not drop the event
            log.warning("Could not acknowledge Slack request: %s", e)

    def _handle_event(self, payload: Dict[str, Any]) -> None:
        event = payload.get("event") or {}
        if event.get("type") != "message":
            return
        # A bot's own replies come back as events; answering them is an infinite
        # loop with a local LLM at the bottom of it.
        if event.get("bot_id") or event.get("subtype") in {
            "bot_message",
            "message_changed",
            "message_deleted",
        }:
            return
        # DM-only. Anyone in a channel can address a bot in it, so channels are
        # a separate security decision, not a configuration one.
        if event.get("channel_type") != "im":
            audit.warning(
                "Ignored a Slack message in channel_type=%r — this bridge "
                "answers direct messages only",
                event.get("channel_type"),
            )
            return

        team = payload.get("team_id") or event.get("team")
        if not self._same_workspace(team):
            audit.warning(
                "Refused a Slack message from workspace %r — this bridge is "
                "bound to %r",
                team,
                self.team_id,
            )
            return

        user = event.get("user")
        channel = event.get("channel")
        ts = event.get("ts", "")
        if not self._allowed(user):
            audit.warning(
                "Refused a Slack message from unauthorized member %s "
                "(allowlist holds %d id(s)) — add the id to --allowed-users if "
                "this refusal is wrong",
                user,
                len(self.allowed_users),
            )
            self._post(channel, UNAUTHORIZED_REPLY, thread_ts=ts)
            return

        audit.info("Authorized Slack member %s in %s", user, channel)
        text = (event.get("text") or "").strip()
        note = self._ingest_files(event.get("files") or [])
        question = f"{text} {note}".strip()
        if not question:
            self._post(
                channel,
                "I got an empty message — send me a question.",
                thread_ts=ts,
            )
            return

        target = ReplyTarget(channel=channel, thread_ts=ts)
        target.reply_ts = self._post(channel, "_Thinking…_", thread_ts=ts)
        target.throttle = StreamThrottle(
            flush=lambda body: self._update(target, body),
            interval=EDIT_INTERVAL,
        )
        self._agent().submit(Turn(text=question, context=target, sender=user or ""))

    def _ingest_files(self, files: Sequence[Dict[str, Any]]) -> str:
        """Save shared files where the agent can read them, and say where.

        Indexing here would put the document in THIS process's RAG index, which
        the agent child never sees — so the agent would be told a file was
        indexed that it cannot find. The agent indexes or opens it itself.
        """
        notes: List[str] = []
        for meta in files:
            name = str(meta.get("name") or meta.get("id") or "file")
            size = meta.get("size") or 0
            if size and size > MAX_DOWNLOAD_BYTES:
                notes.append(f"[{name} skipped — larger than 100 MB]")
                continue
            url = meta.get("url_private_download") or meta.get("url_private")
            if not url:
                notes.append(f"[{name} skipped — Slack gave no download URL]")
                continue
            try:
                path = self._download(url, name, str(meta.get("id") or ""))
            except Exception as e:  # noqa: BLE001 - one bad file must not kill the turn
                log.warning("Could not download Slack file %s: %s", name, e)
                notes.append(f"[{name} could not be downloaded]")
                continue
            notes.append(
                f"[the user shared {name}, saved at {path} — open or index it "
                "before answering questions about it]"
            )
        return " ".join(notes)

    def _download(self, url: str, name: str, file_id: str = "") -> Path:
        """Fetch a private Slack file into the inbox using the bot token."""
        import requests

        if not _is_slack_file_url(url):
            raise ValueError(
                f"Slack gave a file URL outside Slack's own domains ({url!r}); "
                "refusing to send the bot token to it."
            )

        # Slack's filename is user-controlled: keep only a safe basename so a
        # name like "../../.ssh/config" cannot write outside the inbox.
        safe = _UNSAFE_NAME.sub("_", Path(name).name).lstrip(".") or "file"
        prefix = _UNSAFE_NAME.sub("_", file_id) or uuid.uuid4().hex[:8]
        folder = inbox_dir()
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{prefix}-{safe}"
        partial = folder / f".partial-{uuid.uuid4().hex}"

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.bot_token}"},
            timeout=60,
            stream=True,
            # A redirect is refused, not followed: following one is how a token
            # checked against one host ends up sent to another.
            allow_redirects=False,
        )
        if response.is_redirect or 300 <= response.status_code < 400:
            raise ValueError(
                f"Slack redirected the download of {name} instead of serving it; "
                "refusing to follow, since the bot token would go with it."
            )
        response.raise_for_status()
        written = 0
        try:
            with open(partial, "wb") as fh:
                for chunk in response.iter_content(chunk_size=65536):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"{name} exceeded the {MAX_DOWNLOAD_BYTES} byte download cap"
                        )
                    fh.write(chunk)
            partial.replace(final)
        finally:
            partial.unlink(missing_ok=True)
        return final

    def _handle_interactive(self, payload: Dict[str, Any]) -> None:
        """Answer a tool confirmation from a Block Kit button click."""
        user = (payload.get("user") or {}).get("id")
        team = (payload.get("team") or {}).get("id")
        if not self._same_workspace(team):
            audit.warning("Refused a Slack button click from workspace %r", team)
            return
        if not self._allowed(user):
            # The buttons are visible to anyone who can see the DM, and a shared
            # channel or a workspace admin viewing it must not be able to
            # approve a command on the owner's machine.
            audit.warning("Refused a tool approval from unauthorized member %s", user)
            return
        for action in payload.get("actions") or []:
            action_id = action.get("action_id")
            decision = _ACTION_DECISIONS.get(action_id)
            if decision is None:
                continue
            confirm_id = action.get("value") or ""
            posted = self._take_confirmation(confirm_id)
            if posted is None:
                # Already answered, expired, or never ours. Sending it anyway
                # could resolve a different prompt than the one clicked.
                log.info("Ignored a click for confirmation %r: not pending", confirm_id)
                continue
            audit.warning(
                "Slack member %s answered %r for confirmation %s",
                user,
                decision,
                confirm_id,
            )
            self._agent().decide(decision, confirm_id)
            channel, ts, label, _ = posted
            verb = {
                DECISION_ALLOW: "Allowed",
                DECISION_ALWAYS: "Always allowed",
                DECISION_DENY: "Denied",
            }[decision]
            self._edit(channel, ts, f"*{verb}* — {label}  (by <@{user}>)", blocks=[])

    def _take_confirmation(self, confirm_id: str) -> Optional[tuple]:
        """Claim a pending prompt exactly once and stop its timeout."""
        with self._lock:
            posted = self._confirmations.pop(confirm_id, None)
        if posted is not None:
            posted[3].cancel()
        return posted

    def _expire_confirmation(self, confirm_id: str) -> None:
        """Deny a prompt nobody answered, so the agent can move on."""
        posted = self._take_confirmation(confirm_id)
        if posted is None:
            return
        channel, ts, label, _ = posted
        audit.warning(
            "Denied %r: no answer within %ss", label, int(self.confirm_timeout)
        )
        self._agent().decide(DECISION_DENY, confirm_id)
        self._edit(
            channel,
            ts,
            f"*Denied — no answer within {int(self.confirm_timeout // 60)} "
            f"minutes* — {label}",
            blocks=[],
        )

    def _on_busy(self, turn: Turn, ahead: int) -> None:
        """Tell the sender their message is queued behind a running turn."""
        target = turn.context
        if not isinstance(target, ReplyTarget):
            return
        self._update(
            target,
            f"_Queued — finishing {ahead} earlier message"
            f"{'s' if ahead != 1 else ''} first…_",
        )

    # ------------------------------------------------------------------
    # Outbound — rendering agent events
    # ------------------------------------------------------------------

    def _on_agent_event(self, event: Dict[str, Any], turn: Turn) -> None:
        target = turn.context
        if not isinstance(target, ReplyTarget):
            log.warning("Dropped an agent event with no Slack reply target")
            return
        etype = event.get("type")
        if etype == "token":
            if target.throttle is not None:
                target.throttle.add(str(event.get("delta") or ""))
        elif etype == "status":
            # Only shown while nothing has streamed yet; once tokens arrive,
            # replacing the answer with a status line is a regression.
            if target.throttle is not None and not target.throttle.text:
                self._update(target, f"_{event.get('message') or 'Working…'}_")
        elif etype == "tool_call":
            tool = str(event.get("tool") or "a tool")
            self._note_tool(target, tool)
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
                f":warning: {event.get('detail') or 'The agent failed.'}",
            )

    def _note_tool(self, target: ReplyTarget, tool: str) -> None:
        """Show what the agent is doing while it is doing it."""
        if tool not in target.tools:
            target.tools.append(tool)
        if target.throttle is not None and not target.throttle.text:
            self._update(target, f"_Using `{tool}`…_")

    def _ask_confirmation(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Post the approval buttons — or refuse outright when locked down."""
        action = str(event.get("action") or "run a tool")
        summary = str(event.get("summary") or "")
        confirm_id = str(event.get("confirm_id") or "")
        if self.deny_gated_tools:
            audit.warning(
                "Auto-denied %r: this bridge runs with --deny-gated-tools", action
            )
            self._agent().decide(DECISION_DENY, confirm_id or None)
            self._post(
                target.channel,
                f"_Refused *{action}* — this Slack bridge is running read-only "
                f"(`--deny-gated-tools`)._",
                thread_ts=target.thread_ts,
            )
            return
        blocks = confirmation_blocks(
            action, summary, str(event.get("always_scope") or "")
        )
        for block in blocks:
            if block.get("type") != "actions":
                continue
            for element in block.get("elements", []):
                # The decision has to name WHICH prompt it answers, or a late
                # click resolves whatever confirmation replaced the one it was
                # typed for.
                element["value"] = confirm_id
        ts = self._post(
            target.channel,
            f"GAIA wants to {action}",
            thread_ts=target.thread_ts,
            blocks=blocks,
        )
        if not ts or not confirm_id:
            # Nobody can press a button that never posted, and the agent waits
            # forever — deny rather than block every later message.
            audit.warning("Denied %r: the approval prompt could not be shown", action)
            self._agent().decide(DECISION_DENY, confirm_id or None)
            return
        timer = threading.Timer(
            self.confirm_timeout, self._expire_confirmation, args=(confirm_id,)
        )
        timer.daemon = True
        with self._lock:
            self._confirmations[confirm_id] = (target.channel, ts, action, timer)
        timer.start()

    def _refuse_question(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Show a mid-run question and end the turn.

        The agent's stdio wire has no way to deliver an answer, so waiting would
        leave the turn parked for the question's whole timeout with nothing in
        Slack. Cancelling keeps the conversation, so a reply continues it.
        """
        question = str(event.get("question") or "GAIA needs more information.")
        lines = [f"*GAIA asked:* {question}"]
        for option in event.get("options") or []:
            if isinstance(option, dict):
                lines.append(f"• {option.get('label') or option.get('value')}")
        if event.get("sensitive"):
            lines.append(
                "_It asked for something sensitive — answer on the computer "
                "running GAIA, not in Slack._"
            )
        else:
            lines.append("_Reply with your answer and GAIA will continue from there._")
        self._post(target.channel, "\n".join(lines), thread_ts=target.thread_ts)
        self._agent().cancel()

    def _remember_written_file(self, target: ReplyTarget, tool: str, args: Any) -> None:
        """Note the path a file-writing tool was asked to write."""
        if tool not in WRITE_TOOLS or not isinstance(args, dict):
            return
        raw = args.get("file_path") or args.get("path")
        target.pending_upload = raw if isinstance(raw, str) else ""

    def _maybe_upload(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Send back the file the preceding write tool produced.

        Only a path a write tool was called with, only after its result says it
        did not fail, only under an upload root, only once per turn, and only
        within the size cap. The write was approved through the confirmation
        gate; a path outside the roots is still refused, because that approval
        covered a write, not a transfer off the machine.
        """
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
            log.info("Skipped uploading %s — larger than the 25 MB cap", path)
            return
        target.uploaded.add(key)
        try:
            self._upload(target, path)
        except Exception as e:  # noqa: BLE001 - a failed upload must not kill the turn
            log.warning("Could not upload %s to Slack: %s", path, e)

    def _upload(self, target: ReplyTarget, path: Path) -> None:
        """Upload one file. ``files_upload_v2`` — ``files.upload`` is retired."""
        web = self._build_web_client()
        web.files_upload_v2(
            channel=target.channel,
            thread_ts=target.thread_ts,
            file=str(path),
            filename=path.name,
            initial_comment=f"Here's `{path.name}`.",
        )
        log.info("Uploaded %s to Slack channel %s", path.name, target.channel)

    def _finish(self, target: ReplyTarget, answer: str) -> None:
        """Write the final answer, replacing whatever was streaming."""
        if target.throttle is not None and answer:
            # The final answer is authoritative — the streamed text can be a
            # partial render of the same content.
            self._update(target, answer)
        elif target.throttle is not None:
            target.throttle.finish()
        trail = ", ".join(f"`{t}`" for t in target.tools)
        if trail:
            self._post(
                target.channel,
                f"_Used {trail}_",
                thread_ts=target.thread_ts,
            )

    # ------------------------------------------------------------------
    # Slack primitives
    # ------------------------------------------------------------------

    def _post(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str = "",
        blocks: Optional[List[dict]] = None,
    ) -> str:
        """Post a message, returning its timestamp (empty string on failure)."""
        web = self._build_web_client()
        kwargs: Dict[str, Any] = {"channel": channel, "text": _truncate(text)}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        if blocks is not None:
            kwargs["blocks"] = blocks
        try:
            response = web.chat_postMessage(**kwargs)
        except Exception as e:  # noqa: BLE001 - a failed post must not kill the turn
            log.warning("Could not post to Slack channel %s: %s", channel, e)
            return ""
        data = response.data if hasattr(response, "data") else response
        return str(data.get("ts") or "")

    def _edit(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: Optional[List[dict]] = None,
    ) -> None:
        web = self._build_web_client()
        kwargs: Dict[str, Any] = {"channel": channel, "ts": ts, "text": _truncate(text)}
        if blocks is not None:
            kwargs["blocks"] = blocks
        try:
            web.chat_update(**kwargs)
        except Exception as e:  # noqa: BLE001 - rate limits must not kill the turn
            log.debug("Could not edit Slack message %s: %s", ts, e)

    def _update(self, target: ReplyTarget, text: str) -> None:
        """Edit the turn's reply in place, posting one if there is none yet."""
        if not target.reply_ts:
            target.reply_ts = self._post(
                target.channel, text, thread_ts=target.thread_ts
            )
            return
        self._edit(target.channel, target.reply_ts, text)


def run_slack(
    bot_token: str,
    app_token: str,
    allowed_users: Optional[Set[str]] = None,
    *,
    team_id: Optional[str] = None,
    agent_argv: Optional[Sequence[str]] = None,
    deny_gated_tools: bool = False,
    upload_roots: Optional[Sequence[str]] = None,
) -> SlackAdapter:
    """Build and start a Slack adapter.

    Raises:
        SlackAllowlistError: if ``allowed_users`` is empty. Every member of a
            workspace can DM a bot, so an empty allowlist would offer the local
            agent to all of them.
    """
    adapter = SlackAdapter(
        bot_token=bot_token,
        app_token=app_token,
        allowed_users=allowed_users,
        team_id=team_id,
        agent_argv=agent_argv,
        deny_gated_tools=deny_gated_tools,
        upload_roots=upload_roots,
    )
    adapter.start()
    return adapter
