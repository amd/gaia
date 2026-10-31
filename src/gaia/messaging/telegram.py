# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Telegram messaging adapter for GAIA.

Wires Telegram updates into the `AgentSDK` and streams responses back via
message edits. Talks to the Bot API directly through
`gaia.messaging.telegram_api` (httpx long polling).

Security posture:
- A Telegram session is a plain `AgentSDK` — an LLM completion wrapper with no
  tool loop. The tool loop lives in `gaia.agents.base.agent.Agent`, which this
  adapter never constructs, so shell and file-write tools are unreachable from
  a Telegram message. `tests/unit/test_telegram_tool_surface.py` pins that.
- Every update handler is gated on an explicit allowlist of numeric Telegram
  user IDs. There is no permissive default: an adapter cannot be constructed
  without one.
"""

from __future__ import annotations

import asyncio
import functools
import os
import signal
import tempfile
import threading
from typing import Callable, Dict, Optional, Set

from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.logger import get_logger
from gaia.mcp.ports import TELEGRAM_HEALTH_PORT
from gaia.messaging.bridge import turn_idle_message
from gaia.messaging.ingest import ingest_document_to_rag, ingest_image_to_vlm
from gaia.messaging.telegram_api import (
    TelegramAPIError,
    TelegramBotAPI,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUpdate,
    require_token,
)
from gaia.stream_timeout import stream_timeout_from_env

log = get_logger(__name__)

#: Seconds Telegram holds each getUpdates request open.
POLL_TIMEOUT = 10
#: Only plain messages are requested; edits, channel posts and the rest are not.
ALLOWED_UPDATES = ("message",)
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0
#: How long a background start waits for getMe/deleteWebhook before giving up.
_STARTUP_WAIT = 60.0

# Simple per-user session store: user_id -> AgentSDK
_USER_SESSIONS: Dict[int, AgentSDK] = {}
_SESSIONS_LOCK = threading.RLock()


def get_or_create_session(user_id: int) -> AgentSDK:
    """Return a per-user AgentSDK instance, creating one if needed.

    This reuses AgentSDK instances to preserve conversation history and
    model warm-up. Simple in-memory store without persistence for now.

    ``AgentSDK`` is deliberate, not incidental: it has no tool loop, so a
    remote message cannot reach a shell or file-write tool. Swapping in an
    ``Agent`` subclass here hands that surface to anyone on the allowlist.
    """
    with _SESSIONS_LOCK:
        if user_id in _USER_SESSIONS:
            return _USER_SESSIONS[user_id]
        sdk = AgentSDK(AgentConfig())
        _USER_SESSIONS[user_id] = sdk
        return sdk


UNAUTHORIZED_REPLY = "Sorry — you're not authorized to use this bot."

#: Text of the refusal. One string so the CLI and the exception agree.
NO_ALLOWLIST_ERROR = (
    "Telegram adapter refused to start: no allowed-users configured.\n"
    "\n"
    "A Telegram bot is reachable by anyone who finds it, so an empty "
    "allowlist would expose your local agent to the whole platform.\n"
    "Pass the numeric user IDs permitted to use it:\n"
    "\n"
    "  gaia telegram start --token <token> --allowed-users 11111111,22222222\n"
    "\n"
    "Find your own ID by messaging @userinfobot on Telegram.\n"
    "Docs: https://amd-gaia.ai/docs/guides/telegram-adapter"
)


class TelegramAllowlistError(ValueError):
    """The adapter was asked to run without an allowlist.

    Subclasses ``ValueError`` so the CLI can catch this specific failure
    rather than any ``ValueError`` raised while polling.
    """


def require_allowed(handler):
    """Enforce the allowlist on a Telegram update handler.

    Every handler needs its own check: ``/start`` is routed to
    ``_handle_start`` and never reaches ``_handle_message``.
    """

    @functools.wraps(handler)
    async def guarded(self, update, context):
        user = update.effective_user
        if not self._allowed(user.id):
            log.warning(
                "Refused Telegram message from unauthorized user %s "
                "(handler=%s, allowlist holds %d id(s)) — add the id to "
                "--allowed-users if this refusal is wrong",
                user.id,
                handler.__name__,
                len(self.allowed_users),
            )
            await update.message.reply_text(UNAUTHORIZED_REPLY)
            return None
        log.debug("Authorized Telegram user %s for %s", user.id, handler.__name__)
        return await handler(self, update, context)

    guarded.__gaia_allowlist_guarded__ = True
    return guarded


def is_adapter_process(pid: int) -> bool:
    """True only for a live process that is a ``gaia telegram start`` adapter.

    A stale pid file can name an unrelated process that inherited the id, so
    liveness alone is not enough before signalling it.

    Raises:
        PermissionError: the process exists but its command line is unreadable.
    """
    try:
        import psutil

        from gaia.daemon.instance import pid_alive
    except ImportError as e:
        raise RuntimeError(
            "psutil is required to check the Telegram adapter. Install it with: "
            'pip install "amd-gaia[telegram]"'
        ) from e
    if not pid_alive(pid):
        return False
    try:
        argv = psutil.Process(pid).cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied as e:
        raise PermissionError(
            f"cannot read the command line of pid {pid} to confirm it is the "
            "Telegram adapter"
        ) from e
    # Adjacent argv entries, not substrings of the joined line: Telegram
    # Desktop's `telegram-desktop -startintray` contains both words.
    return any(a == "telegram" and b == "start" for a, b in zip(argv, argv[1:]))


class TelegramAdapter:
    def __init__(
        self,
        token: str,
        allowed_users: Optional[Set[int]] = None,
        idle_limit: Optional[float] = None,
    ):
        if idle_limit is None:
            idle_limit = float(stream_timeout_from_env())
        if idle_limit <= 0:
            raise ValueError(f"idle_limit must be > 0 seconds, got {idle_limit}")
        #: Seconds a reply may go without a new chunk before it is stopped.
        self._idle_limit = idle_limit
        # Refused here rather than at start(): an adapter that cannot serve
        # anyone should never exist, so no caller can reach a permissive one.
        if not allowed_users:
            raise TelegramAllowlistError(NO_ALLOWLIST_ERROR)
        # A bare string would iterate into single characters and silently deny
        # everyone, which reads as "the bot is broken" rather than "wrong type".
        if isinstance(allowed_users, (str, bytes)):
            raise TypeError(
                "allowed_users must be a collection of int user IDs, got "
                f"{type(allowed_users).__name__}. Pass {{11111111}}, not "
                "'11111111'."
            )
        self.token = token
        self.allowed_users = set(allowed_users)
        self.api: Optional[TelegramBotAPI] = None
        self._bot_username: Optional[str] = None
        #: Next update_id to fetch; Telegram drops everything below it once sent.
        self._offset: Optional[int] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._poll_task: Optional[asyncio.Task] = None
        log.info(
            "Telegram adapter configured with %d allowed user id(s)",
            len(self.allowed_users),
        )

    def _allowed(self, user_id: int) -> bool:
        """Membership only — an empty allowlist admits nobody.

        Defence in depth: ``__init__`` already refuses an empty allowlist, so
        this only fires if one is emptied after construction.
        """
        return user_id in self.allowed_users

    @require_allowed
    async def _handle_start(self, update, _context):
        await update.message.reply_text(
            "Hello! I'm Gaia. Send a message and I'll respond (streaming)."
        )

    @require_allowed
    async def _handle_message(self, update, _context):
        user = update.effective_user
        text = update.message.text or ""

        # If the user sent media, note it and download to tmp for later ingestion
        media_note = ""
        if update.message.photo:
            media_note = "[photo uploaded]"
            file = await update.message.photo[-1].get_file()
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"gaia_telegram_{file.file_id}.jpg")
            await file.download_to_drive(tmp_path)
            # Hand tmp_path to VLM ingestion pipeline (async-safe wrapper)
            vlm_result = ingest_image_to_vlm(tmp_path)
            if vlm_result.get("status") == "success":
                media_note = "[photo uploaded and processed]"
                # Optionally include a short excerpt in the conversation
                excerpt = vlm_result.get("text", "").strip()
                if excerpt:
                    media_note += " - " + (
                        excerpt[:400] + "..." if len(excerpt) > 400 else excerpt
                    )
            else:
                media_note = "[photo uploaded - VLM failed]"
        elif update.message.document:
            media_note = f"[file uploaded: {update.message.document.file_name}]"
            file = await update.message.document.get_file()
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"gaia_telegram_{file.file_id}")
            await file.download_to_drive(tmp_path)
            # Hand tmp_path to RAG ingestion pipeline
            rag_result = ingest_document_to_rag(tmp_path)
            if rag_result.get("success"):
                media_note = f"[file indexed: {update.message.document.file_name}]"
            else:
                media_note = f"[file uploaded: {update.message.document.file_name} - index failed]"
        elif any(
            getattr(update.message, media_type, None)
            for media_type in (
                "video",
                "voice",
                "audio",
                "sticker",
                "animation",
                "video_note",
            )
        ):
            await update.message.reply_text(
                "Unsupported media type — I can handle photos and documents."
            )
            return

        user_input = f"{text} {media_note}".strip()

        # Reply immediately with a placeholder we will edit
        reply = await update.message.reply_text("Thinking...")

        # Use an asyncio.Queue to bridge generator (blocking) -> async edits
        queue: asyncio.Queue = asyncio.Queue()
        stopped = threading.Event()
        # The session this turn sends on, once it has one; guarded by
        # _SESSIONS_LOCK together with `stopped`.
        used: Dict[str, AgentSDK] = {}

        loop = asyncio.get_running_loop()

        def run_generation():
            stream = None
            try:
                if stopped.is_set():
                    return  # stopped while waiting for an executor thread
                # Reuse per-user AgentSDK synchronously in thread
                chat = get_or_create_session(user.id)
                with _SESSIONS_LOCK:
                    if stopped.is_set():
                        return  # stopped while the session was being made
                    used["chat"] = chat
                full = ""
                stream = chat.send_stream(user_input)
                for chunk in stream:
                    if stopped.is_set():
                        return
                    full += chunk.text
                    # Put the current accumulated text into the async queue
                    loop.call_soon_threadsafe(queue.put_nowait, (full, False))
                # Signal completion
                loop.call_soon_threadsafe(queue.put_nowait, (full, True))
            except Exception as e:  # pragma: no cover - runtime errors bubble up
                log.exception(
                    "Error during generation for user %s: %s",
                    getattr(user, "id", None),
                    e,
                )
                loop.call_soon_threadsafe(queue.put_nowait, (f"Error: {e}", True))
            finally:
                # Closing the generator ends the model stream behind it.
                close = getattr(stream, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        log.exception("Could not close the stopped reply stream")

        # Start generator in thread to avoid blocking the asyncio loop
        asyncio.get_running_loop().run_in_executor(None, run_generation)

        # Consume queue and edit message. Updates are handled one at a time,
        # so a turn that never finishes would hold every other chat.
        accumulated = ""
        last_edited = None
        while True:
            try:
                text_chunk, done = await asyncio.wait_for(
                    queue.get(), timeout=self._idle_limit
                )
            except asyncio.CancelledError:
                # The adapter is stopping; the generator quits at its next chunk.
                stopped.set()
                raise
            except asyncio.TimeoutError:
                with _SESSIONS_LOCK:
                    stopped.set()
                    chat = used.get("chat")
                    # A generation stuck mid-send still holds its session; the
                    # next message gets a fresh one rather than racing it on the
                    # same history. One that never started touched nothing.
                    dropped = chat is not None
                    if dropped and _USER_SESSIONS.get(user.id) is chat:
                        del _USER_SESSIONS[user.id]
                log.warning(
                    "Telegram turn for user %s made no progress for %.0fs; "
                    "stopped it%s",
                    user.id,
                    self._idle_limit,
                    " and started a new session" if dropped else "",
                )
                note = (
                    turn_idle_message(self._idle_limit)
                    + (
                        " The next message starts a new conversation."
                        if dropped
                        else ""
                    )
                    + " Send yours again to retry."
                )
                text_chunk = f"{accumulated}\n\n{note}" if accumulated else note
                done = True
            accumulated = text_chunk
            # Edit the reply with the latest accumulated text (Telegram rate limits apply)
            try:
                if accumulated != last_edited:
                    await reply.edit_text(accumulated)
                    last_edited = accumulated
            except (TelegramRetryAfter, TelegramNetworkError) as e:
                log.debug("Transient telegram edit failure: %s", e)
            except Exception as e:
                log.exception("Unexpected error editing telegram message: %s", e)
            if done:
                break

    async def _dispatch(self, raw: dict) -> None:
        """Route one update: ``/start`` to its handler, other commands nowhere,
        everything else to ``_handle_message``."""
        try:
            update = TelegramUpdate(self.api, raw)
        except (KeyError, TypeError) as e:
            # Log the id only: the payload carries the user's message text.
            log.error(
                "Skipping malformed Telegram update %s: missing or invalid field %s",
                raw.get("update_id"),
                e,
            )
            return
        if update.message is None or update.effective_user is None:
            log.debug(
                "Ignoring Telegram update %s: no message from a user", update.update_id
            )
            return
        command = update.message.command()
        try:
            if command is None:
                await self._handle_message(update, None)
                return
            name, target = command
            if target and target.lower() != (self._bot_username or "").lower():
                log.debug("Ignoring /%s addressed to @%s", name, target)
            elif name == "start":
                await self._handle_start(update, None)
            else:
                log.debug("Ignoring unhandled Telegram command /%s", name)
        except Exception:
            # One bad update must not stop the bot for every other user.
            log.exception(
                "Telegram update %s from user %s failed",
                update.update_id,
                update.effective_user.id,
            )

    async def _poll(self, on_started: Optional[Callable] = None) -> None:
        """Validate the token, then long-poll getUpdates until cancelled.

        ``on_started`` is called once with ``None`` after startup succeeds, or
        with the exception that stopped it.

        Raises:
            TelegramAPIError: a non-transient Bot API failure (bad token,
                another poller on the same bot, malformed response).
        """
        try:
            me = await self.api.get_me()
            self._bot_username = me.get("username")
            # getUpdates is refused while a webhook is set.
            await self.api.delete_webhook()
        except BaseException as e:
            if on_started is not None:
                on_started(e)
            raise
        if on_started is not None:
            on_started(None)
        log.info("Telegram adapter polling as @%s", self._bot_username)

        backoff = _INITIAL_BACKOFF
        while True:
            try:
                updates = await self.api.get_updates(
                    offset=self._offset,
                    timeout=POLL_TIMEOUT,
                    allowed_updates=ALLOWED_UPDATES,
                )
            except TelegramRetryAfter as e:
                log.warning(
                    "Telegram rate-limited getUpdates; waiting %gs", e.retry_after
                )
                await asyncio.sleep(e.retry_after)
                continue
            except TelegramNetworkError as e:
                log.warning("%s Retrying in %.0fs.", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            backoff = _INITIAL_BACKOFF
            for raw in updates:
                update_id = raw.get("update_id") if isinstance(raw, dict) else None
                if not isinstance(update_id, int):
                    log.error("Skipping Telegram update with no update_id")
                    continue
                # Advance first, so an update whose handler fails is not refetched.
                self._offset = update_id + 1
                await self._dispatch(raw)

    async def _confirm_offset(self) -> None:
        """Acknowledge handled updates so a restart does not redeliver them.

        Telegram only drops updates once a getUpdates call carries the next
        offset; the poll loop is cancelled before it sends that call.
        """
        if self._offset is None:
            return
        try:
            await self.api.get_updates(
                offset=self._offset, timeout=0, allowed_updates=ALLOWED_UPDATES
            )
        except TelegramAPIError as e:
            log.warning(
                "Could not confirm Telegram updates before offset %s on stop; "
                "the last messages may be answered again after a restart: %s",
                self._offset,
                e,
            )

    def _run_polling_loop(self, on_started: Optional[Callable] = None) -> None:
        """Run ``_poll`` on a fresh event loop in this thread until stopped."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(self._poll(on_started))
        with self._loop_lock:
            self._loop, self._poll_task = loop, task
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            log.info("Telegram adapter stopped polling")
        finally:
            with self._loop_lock:
                self._loop = self._poll_task = None
            try:
                loop.run_until_complete(self._confirm_offset())
            finally:
                loop.run_until_complete(self.api.aclose())
            loop.close()
            asyncio.set_event_loop(None)

    def stop(self) -> bool:
        """Stop polling from any thread. Returns False if polling never started."""
        with self._loop_lock:
            loop, task = self._loop, self._poll_task
        if loop is None or task is None:
            log.error("Telegram polling loop never started; cannot stop it cleanly")
            return False
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError as e:
            log.error(
                "Telegram polling loop could not be stopped; "
                "use gaia telegram stop --force: %s",
                e,
            )
            return False
        return True

    def start(
        self,
        token: str,
        background: bool = False,
        health_port: int = TELEGRAM_HEALTH_PORT,
    ) -> None:
        """Connect to the Bot API and long-poll for updates.

        If `background` is True, polling runs in a non-daemon thread so a CLI
        return cannot take the service process down; call `stop()` to end it.
        Otherwise, this call blocks until SIGINT/SIGTERM or `stop()`.

        Raises:
            TelegramAPIError: the token was rejected or Telegram could not be
                reached at startup.
        """
        # Checked before the PID file so a missing token leaves nothing behind.
        require_token(token)

        pid_path = None
        if background:
            pid_dir = os.path.expanduser("~/.gaia")
            os.makedirs(pid_dir, exist_ok=True)
            pid_path = os.path.join(pid_dir, "telegram.pid")
            try:
                with open(pid_path, "w", encoding="utf-8") as f:
                    f.write(str(os.getpid()))
            except OSError as e:
                # The PID file is load-bearing in background mode: without it a
                # supervisor cannot find or kill the process. Fail loudly.
                raise RuntimeError(
                    f"Failed to write telegram PID file at {pid_path}: {e}. "
                    f"Ensure ~/.gaia is writable, or start without --background."
                ) from e

            log_path = os.path.join(pid_dir, "telegram.log")
            try:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write("Starting telegram adapter in background\n")
                    fh.flush()
            except OSError as e:
                log.exception("Failed to open telegram log file: %s", e)

            if os.getenv("GAIA_TEST_MODE"):
                log.info("GAIA_TEST_MODE set: skipping background services")
                return

            self.api = TelegramBotAPI(token)

            # Simple health server
            def _health_server(stop_event: threading.Event):
                from http.server import BaseHTTPRequestHandler, HTTPServer

                class HealthHandler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        if self.path == "/healthz":
                            self.send_response(200)
                            self.send_header("Content-Type", "text/plain")
                            self.end_headers()
                            self.wfile.write(b"ok")
                        else:
                            self.send_response(404)
                            self.end_headers()

                    def log_message(self, format, *args):
                        # Silence default logging
                        return

                try:
                    server = HTTPServer(("127.0.0.1", health_port), HealthHandler)
                except OSError as e:
                    log.error(
                        "Failed to bind Telegram health server on 127.0.0.1:%s "
                        "(%s). Pass --health-port <port> to use a different port.",
                        health_port,
                        e,
                    )
                    raise
                # Run until stop_event is set
                while not stop_event.is_set():
                    server.handle_request()

            stop_event = threading.Event()
            hs_thread = threading.Thread(
                target=_health_server, args=(stop_event,), daemon=True
            )
            hs_thread.start()

            started = threading.Event()
            startup: Dict[str, Optional[BaseException]] = {}

            def _on_started(error: Optional[BaseException]) -> None:
                startup["error"] = error
                started.set()

            def _stop_application(*_):
                stop_event.set()
                self.stop()

            def _run_polling():
                try:
                    self._run_polling_loop(_on_started)
                except Exception as e:
                    # start() re-raises a startup failure; log the later ones.
                    if "error" not in startup:
                        startup["error"] = e
                    elif startup["error"] is None:
                        log.error("Telegram adapter stopped: %s", e)
                finally:
                    if "error" not in startup:
                        startup["error"] = RuntimeError(
                            "Telegram adapter stopped before it connected."
                        )
                    started.set()
                    stop_event.set()
                    try:
                        os.remove(pid_path)
                    except FileNotFoundError:
                        pass
                    except OSError as e:
                        log.warning(
                            "Failed to remove Telegram PID file %s: %s", pid_path, e
                        )

            # This thread owns the background service lifetime. A daemon thread
            # dies as soon as the CLI handler returns, leaving only a stale PID
            # file and a bot that never polls.
            poll_thread = threading.Thread(target=_run_polling, daemon=False)
            self._poll_thread = poll_thread
            poll_thread.start()

            if not started.wait(timeout=_STARTUP_WAIT):
                _stop_application()
                raise RuntimeError(
                    f"Telegram adapter did not connect to api.telegram.org within "
                    f"{_STARTUP_WAIT:.0f}s. Check the network connection and retry."
                )
            if startup.get("error") is not None:
                poll_thread.join(timeout=10)
                raise startup["error"]

            # Register signal handlers for graceful shutdown (works in main thread only)
            try:
                signal.signal(signal.SIGTERM, _stop_application)
                signal.signal(signal.SIGINT, _stop_application)
            except (ValueError, OSError) as e:
                # Not all environments allow signal registration
                log.debug("Signal registration skipped: %s", e)

            return

        self.api = TelegramBotAPI(token)
        # Blocking run: SIGINT/SIGTERM cancel the poll rather than kill it mid-update.
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.signal(sig, lambda *_: self.stop())
            except (ValueError, OSError) as e:
                log.debug("Signal registration skipped: %s", e)
        try:
            self._run_polling_loop()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def run_telegram(
    token: str,
    allowed_users: Optional[Set[int]] = None,
    background: bool = False,
    health_port: int = TELEGRAM_HEALTH_PORT,
):
    """Entrypoint used by the CLI to start the Telegram adapter.

    Connects to the Bot API and long-polls for updates. Pass
    `background=True` to return once polling has started (call
    `adapter.stop()` to end it); otherwise this blocks until interrupted.

    Args:
        token: Telegram bot token.
        allowed_users: Set of numeric user IDs permitted to interact.
        background: If True, run as a daemon (writes PID + health endpoint).
        health_port: Health server port used in background mode.

    Raises:
        TelegramAllowlistError: if `allowed_users` is empty or None. A bot with
            no allowlist is reachable by every Telegram user, so it is refused
            rather than started permissively.
        TelegramAPIError: the bot token was rejected or Telegram was unreachable
            at startup.
    """
    adapter = TelegramAdapter(token=token, allowed_users=allowed_users)
    adapter.start(token=token, background=background, health_port=health_port)
    return adapter
