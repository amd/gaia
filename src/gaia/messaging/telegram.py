"""Telegram messaging adapter for GAIA.

This is a minimal scaffolding for v0.18.2. It wires Telegram updates into
the `AgentSDK` and streams responses back via message edits.

Notes:
- Security defaults (restricted tools) and per-user sessions must be enforced
  by the caller creating AgentSDK instances with the appropriate config.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import threading
from typing import Optional, Set, Dict

from gaia.chat.sdk import AgentSDK, AgentConfig
from gaia.messaging.ingest import ingest_image_to_vlm, ingest_document_to_rag

log = logging.getLogger(__name__)

# Simple per-user session store: user_id -> AgentSDK
_USER_SESSIONS: Dict[int, AgentSDK] = {}
_SESSIONS_LOCK = threading.RLock()


def get_or_create_session(user_id: int) -> AgentSDK:
    """Return a per-user AgentSDK instance, creating one if needed.

    This reuses AgentSDK instances to preserve conversation history and
    model warm-up. Simple in-memory store without persistence for now.
    """
    with _SESSIONS_LOCK:
        if user_id in _USER_SESSIONS:
            return _USER_SESSIONS[user_id]
        config = AgentConfig()  # Default config; callers may customize later
        sdk = AgentSDK(config)
        _USER_SESSIONS[user_id] = sdk
        return sdk


class TelegramAdapter:
    def __init__(self, token: str, allowed_users: Optional[Set[int]] = None):
        self.token = token
        self.allowed_users = allowed_users or set()
        self.application = None

    def _allowed(self, user_id: int) -> bool:
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _handle_start(self, update, context):
        await update.message.reply_text(
            "Hello! I'm Gaia. Send a message and I'll respond (streaming)."
        )

    async def _handle_message(self, update, context):
        user = update.effective_user
        if not self._allowed(user.id):
            await update.message.reply_text(
                "Sorry — you're not authorized to use this bot."
            )
            return

        text = update.message.text or ""

        # If the user sent media, note it and download to tmp for later ingestion
        media_note = ""
        if update.message.photo:
            media_note = "[photo uploaded]"
            file = await update.message.photo[-1].get_file()
            tmp_path = os.path.join("/tmp", f"gaia_telegram_{file.file_id}.jpg")
            await file.download_to_drive(tmp_path)
            # Hand tmp_path to VLM ingestion pipeline (async-safe wrapper)
            vlm_result = ingest_image_to_vlm(tmp_path)
            if vlm_result.get("status") == "success":
                media_note = "[photo uploaded and processed]"
                # Optionally include a short excerpt in the conversation
                excerpt = vlm_result.get("text", "").strip()
                if excerpt:
                    media_note += " - " + (excerpt[:400] + "..." if len(excerpt) > 400 else excerpt)
            else:
                media_note = "[photo uploaded - VLM failed]"
        elif update.message.document:
            media_note = f"[file uploaded: {update.message.document.file_name}]"
            file = await update.message.document.get_file()
            tmp_path = os.path.join("/tmp", f"gaia_telegram_{file.file_id}")
            await file.download_to_drive(tmp_path)
            # Hand tmp_path to RAG ingestion pipeline
            rag_result = ingest_document_to_rag(tmp_path)
            if rag_result.get("success"):
                media_note = f"[file indexed: {update.message.document.file_name}]"
            else:
                media_note = f"[file uploaded: {update.message.document.file_name} - index failed]"

        user_input = f"{text} {media_note}".strip()

        # Reply immediately with a placeholder we will edit
        reply = await update.message.reply_text("Thinking...")

        # Use an asyncio.Queue to bridge generator (blocking) -> async edits
        queue: asyncio.Queue = asyncio.Queue()

        loop = asyncio.get_running_loop()

        def run_generation():
            try:
                # Create AgentSDK synchronously in thread
                config = AgentConfig()
                chat = AgentSDK(config)
                full = ""
                for chunk in chat.send_stream(user_input):
                    full += chunk.text
                    # Put the current accumulated text into the async queue
                    loop.call_soon_threadsafe(queue.put_nowait, (full, False))
                # Signal completion
                loop.call_soon_threadsafe(queue.put_nowait, (full, True))
            except Exception as e:  # pragma: no cover - runtime errors bubble up
                loop.call_soon_threadsafe(queue.put_nowait, (f"Error: {e}", True))

        # Start generator in thread to avoid blocking the asyncio loop
        asyncio.get_running_loop().run_in_executor(None, run_generation)

        # Consume queue and edit message
        accumulated = ""
        try:
            while True:
                text_chunk, done = await queue.get()
                accumulated = text_chunk
                # Edit the reply with the latest accumulated text (Telegram rate limits apply)
                try:
                    await reply.edit_text(accumulated)
                except Exception:
                    # Ignore transient edit failures (rate limits)
                    pass
                if done:
                    break
        finally:
            # Optionally finalize or log
            pass

    def start(self, token: str, background: bool = False) -> None:
        """Start the telegram Application and run polling.

        If `background` is True, the `Application` instance is returned and not
        run (caller can manage its lifecycle). Otherwise, this call blocks and
        runs `run_polling()` until interrupted.
        """
        try:
            from telegram.ext import (
                ApplicationBuilder,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except Exception as e:  # pragma: no cover - dependency missing
            # If running in background mode (tests or dry-run), allow import to be missing
            if background:
                log.warning("python-telegram-bot not installed; running in dry/background mode")
                self.application = None
                return
            raise RuntimeError("python-telegram-bot is required for Telegram support") from e

        app = ApplicationBuilder().token(token).build()

        app.add_handler(CommandHandler("start", self._handle_start))
        app.add_handler(
            MessageHandler(filters.ALL & ~filters.COMMAND, self._handle_message)
        )

        self.application = app

        if background:
            # Background/daemon mode: write PID file, start health server,
            # and run polling in a thread unless GAIA_TEST_MODE is set.
            pid_dir = os.path.expanduser("~/.gaia")
            os.makedirs(pid_dir, exist_ok=True)
            pid_path = os.path.join(pid_dir, "telegram.pid")
            with open(pid_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))

            log_path = os.path.join(pid_dir, "telegram.log")
            fh = open(log_path, "a", encoding="utf-8")
            fh.write("Starting telegram adapter in background\n")
            fh.flush()

            # Simple health server
            def _health_server(stop_event: threading.Event):
                from http.server import HTTPServer, BaseHTTPRequestHandler

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

                server = HTTPServer(("127.0.0.1", 8765), HealthHandler)
                # Run until stop_event is set
                while not stop_event.is_set():
                    server.handle_request()

            stop_event = threading.Event()
            hs_thread = threading.Thread(target=_health_server, args=(stop_event,), daemon=True)
            hs_thread.start()

            # If GAIA_TEST_MODE is set, avoid running the real polling loop
            if os.getenv("GAIA_TEST_MODE"):
                log.info("GAIA_TEST_MODE set: skipping actual polling start")
                return

            def _run_polling():
                try:
                    app.run_polling()
                finally:
                    # cleanup
                    stop_event.set()

            poll_thread = threading.Thread(target=_run_polling, daemon=True)
            poll_thread.start()

            # Register signal handlers for graceful shutdown (works in main thread only)
            try:
                signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
                signal.signal(signal.SIGINT, lambda *_: stop_event.set())
            except Exception:
                # Not all environments allow signal registration
                pass

            return

        # Blocking run
        app.run_polling()


def run_telegram(token: str, allowed_users: Optional[Set[int]] = None, background: bool = False):
    """Entrypoint used by the CLI to start the Telegram adapter.

    This builds the `Application`, registers handlers, and runs polling.
    Pass `background=True` to return control without blocking (caller must
    call `adapter.application.run_polling()` or `await adapter.application.initialize()`).
    """
    adapter = TelegramAdapter(token=token, allowed_users=allowed_users)
    adapter.start(token=token, background=background)
    return adapter
