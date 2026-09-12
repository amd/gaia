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
import tempfile
import threading
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
from gaia.messaging.ingest import ingest_document_to_rag, ingest_image_to_vlm

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
        self.bot_token = bot_token
        self.app_token = app_token
        self.allowed_users = set(allowed_users)
        self.team_id = team_id
        self.deny_gated_tools = deny_gated_tools
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
        """Download shared files and hand them to RAG or the VLM.

        Reuses the ingestion the Telegram adapter already uses, so a document
        arriving over Slack lands in the same index as one added locally.
        """
        notes: List[str] = []
        for meta in files:
            name = meta.get("name") or meta.get("id") or "file"
            size = meta.get("size") or 0
            if size and size > MAX_DOWNLOAD_BYTES:
                notes.append(f"[{name} skipped — larger than 100 MB]")
                continue
            url = meta.get("url_private_download") or meta.get("url_private")
            if not url:
                notes.append(f"[{name} skipped — Slack gave no download URL]")
                continue
            try:
                path = self._download(url, name)
            except Exception as e:  # noqa: BLE001 - one bad file must not kill the turn
                log.warning("Could not download Slack file %s: %s", name, e)
                notes.append(f"[{name} could not be downloaded]")
                continue
            mimetype = str(meta.get("mimetype") or "")
            if mimetype.startswith("image/"):
                result = ingest_image_to_vlm(path)
                if result.get("status") == "success":
                    excerpt = (result.get("text") or "").strip()
                    notes.append(
                        f"[image {name}: {excerpt[:400]}]"
                        if excerpt
                        else f"[image {name} processed]"
                    )
                else:
                    notes.append(f"[image {name} — the vision model could not read it]")
            else:
                result = ingest_document_to_rag(path)
                notes.append(
                    f"[file indexed: {name}]"
                    if result.get("success")
                    else f"[file {name} — indexing failed]"
                )
        return " ".join(notes)

    def _download(self, url: str, name: str) -> str:
        """Fetch a private Slack file to a temp path using the bot token."""
        import requests

        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.bot_token}"},
            timeout=60,
            stream=True,
        )
        response.raise_for_status()
        suffix = Path(name).suffix
        handle, path = tempfile.mkstemp(prefix="gaia_slack_", suffix=suffix)
        written = 0
        with os.fdopen(handle, "wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise ValueError(
                        f"{name} exceeded the {MAX_DOWNLOAD_BYTES} byte download cap"
                    )
                fh.write(chunk)
        return path

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
            audit.warning(
                "Slack member %s answered %r for confirmation %s",
                user,
                decision,
                confirm_id or "(none pending)",
            )
            self._agent().decide(decision, confirm_id or None)
            self._settle_confirmation(confirm_id, decision, user)

    def _settle_confirmation(
        self, confirm_id: str, decision: str, user: Optional[str]
    ) -> None:
        """Replace a confirmation's buttons with the decision that was made.

        Without this the buttons stay live after the turn moved on, and a second
        click looks like it did something when it did not.
        """
        with self._lock:
            posted = self._confirmations.pop(confirm_id, None)
        if not posted:
            return
        channel, ts, action = posted
        verb = {
            DECISION_ALLOW: "Allowed",
            DECISION_ALWAYS: "Always allowed",
            DECISION_DENY: "Denied",
        }[decision]
        self._edit(
            channel,
            ts,
            f"*{verb}* — {action}" + (f"  (by <@{user}>)" if user else ""),
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
                target.throttle.add(str(event.get("text") or event.get("token") or ""))
        elif etype == "status":
            # Only shown while nothing has streamed yet; once tokens arrive,
            # replacing the answer with a status line is a regression.
            if target.throttle is not None and not target.throttle.text:
                self._update(target, f"_{event.get('message') or 'Working…'}_")
        elif etype == "tool_call":
            self._note_tool(target, str(event.get("tool") or "a tool"))
        elif etype == "tool_result":
            self._maybe_upload(target, event)
        elif etype == "needs_confirmation":
            self._ask_confirmation(target, event)
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
        if confirm_id and ts:
            with self._lock:
                self._confirmations[confirm_id] = (target.channel, ts, action)

    def _maybe_upload(self, target: ReplyTarget, event: Dict[str, Any]) -> None:
        """Send back a file the agent just wrote.

        Only paths the agent itself reported, only under an upload root, only
        once per turn, and only within the size cap. Writing the file was
        already approved through the confirmation gate, so handing back what the
        user asked for needs no second prompt — but a path outside the roots is
        refused, because that approval covered a write, not a transfer.
        """
        data = event.get("data")
        if not isinstance(data, dict):
            return
        raw = data.get("file_path")
        if not raw or not isinstance(raw, str):
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
