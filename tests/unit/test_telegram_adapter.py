import asyncio
import logging
import os
import signal
import sys
import threading
import time
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import UNAUTHORIZED_REPLY, TelegramAdapter


def test_run_telegram_scaffold_returns_adapter(mock_home, monkeypatch):
    # Ensure local "src" directory is on sys.path for imports during tests
    sys.path.insert(0, os.path.abspath("src"))
    from gaia.messaging.telegram import run_telegram

    # GAIA_TEST_MODE stops real polling; mock_home keeps the background pid/log
    # files out of the developer's real ~/.gaia (see test_telegram_background).
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    # Run in background mode so the function does not block; in CI the
    # python-telegram-bot runtime may not be available, so guard accordingly.
    adapter = run_telegram(
        token="fake-token-123", allowed_users={12345}, background=True
    )
    assert adapter.token == "fake-token-123"
    assert 12345 in adapter.allowed_users
    # Application may be None if the telegram dependency is missing.
    assert hasattr(adapter, "application")


@pytest.mark.allow_network
def test_background_start_keeps_polling_process_alive_until_stopped(
    mock_home, monkeypatch
):
    poll_started = threading.Event()
    poll_stopped = threading.Event()
    signal_handlers = {}
    polling_kwargs = {}

    class FakeApplication:
        def add_handler(self, handler):
            pass

        def run_polling(self, **kwargs):
            polling_kwargs.update(kwargs)
            poll_started.set()
            asyncio.get_event_loop().run_forever()
            poll_stopped.set()

    app = FakeApplication()

    class FakeBuilder:
        def token(self, token):
            assert token == "fake-token"
            return self

        def build(self):
            return app

    class FakeFilter:
        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    fake_telegram = types.ModuleType("telegram")
    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.ApplicationBuilder = FakeBuilder
    fake_ext.CommandHandler = lambda *args: object()
    fake_ext.MessageHandler = lambda *args: object()
    fake_ext.filters = SimpleNamespace(ALL=FakeFilter(), COMMAND=FakeFilter())
    fake_telegram.ext = fake_ext
    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)

    class FakeHTTPServer:
        def __init__(self, address, handler):
            self.timeout = 0.01

        def handle_request(self):
            time.sleep(self.timeout)

    monkeypatch.setattr("http.server.HTTPServer", FakeHTTPServer)
    monkeypatch.setattr(
        "gaia.messaging.telegram.signal.signal",
        lambda sig, handler: signal_handlers.__setitem__(sig, handler),
    )

    adapter = TelegramAdapter(token="fake-token")
    adapter.start(token="fake-token", background=True)

    assert poll_started.wait(timeout=2)
    assert adapter._poll_thread is not None
    assert not adapter._poll_thread.daemon
    assert signal.SIGTERM in signal_handlers
    assert polling_kwargs == {"stop_signals": None}

    signal_handlers[signal.SIGTERM](signal.SIGTERM, None)
    adapter._poll_thread.join(timeout=2)
    assert not adapter._poll_thread.is_alive()
    assert poll_stopped.is_set()


@pytest.mark.asyncio
async def test_unknown_user_is_refused_before_session_creation(monkeypatch, caplog):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99999),
        message=SimpleNamespace(reply_text=reply_text),
    )

    def fail_if_session_created(user_id):
        raise AssertionError(f"session created for unauthorized user {user_id}")

    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", fail_if_session_created
    )

    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await adapter._handle_message(update, None)

    reply_text.assert_awaited_once_with(UNAUTHORIZED_REPLY)
    assert "Refused Telegram message from unauthorized user 99999" in caplog.text


@pytest.mark.asyncio
async def test_unknown_user_is_refused_by_start_command(caplog):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99999),
        message=SimpleNamespace(reply_text=reply_text),
    )

    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await adapter._handle_start(update, None)

    reply_text.assert_awaited_once_with(UNAUTHORIZED_REPLY)
    assert "Refused Telegram message from unauthorized user 99999" in caplog.text


@pytest.mark.asyncio
async def test_allowed_user_gets_start_greeting():
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(reply_text=reply_text),
    )

    await adapter._handle_start(update, None)

    assert reply_text.await_args.args[0].startswith("Hello! I'm Gaia.")


def test_every_update_handler_enforces_the_allowlist():
    """A handler added without ``@require_allowed`` is reachable unauthenticated."""
    handlers = [name for name in dir(TelegramAdapter) if name.startswith("_handle_")]
    assert handlers, "no update handlers found — did they get renamed?"
    unguarded = [
        name
        for name in handlers
        if not getattr(
            getattr(TelegramAdapter, name), "__gaia_allowlist_guarded__", False
        )
    ]
    assert not unguarded, f"handlers missing @require_allowed: {unguarded}"


@pytest.mark.asyncio
async def test_allowed_user_streams_accumulated_response(monkeypatch):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    streamed_reply = AsyncMock()
    reply_text = AsyncMock(return_value=streamed_reply)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="hello",
            photo=[],
            document=None,
            reply_text=reply_text,
        ),
    )
    requested_users = []
    sent_inputs = []

    class StubSession:
        def send_stream(self, text):
            sent_inputs.append(text)
            return iter(
                [
                    SimpleNamespace(text="Hello"),
                    SimpleNamespace(text=" from Gaia"),
                ]
            )

    def get_session(user_id):
        requested_users.append(user_id)
        return StubSession()

    monkeypatch.setattr("gaia.messaging.telegram.get_or_create_session", get_session)

    await adapter._handle_message(update, None)

    assert requested_users == [12345]
    assert sent_inputs == ["hello"]
    reply_text.assert_awaited_once_with("Thinking...")
    assert streamed_reply.edit_text.await_args_list == [
        call("Hello"),
        call("Hello from Gaia"),
    ]


@pytest.mark.asyncio
async def test_photo_ingest_is_included_in_session_input(monkeypatch, tmp_path):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    streamed_reply = AsyncMock()
    reply_text = AsyncMock(return_value=streamed_reply)
    downloaded_path = tmp_path / "gaia_telegram_photo-1.jpg"
    downloaded_file = SimpleNamespace(
        file_id="photo-1",
        download_to_drive=AsyncMock(),
    )
    photo = SimpleNamespace(get_file=AsyncMock(return_value=downloaded_file))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="What is shown here?",
            photo=[photo],
            document=None,
            reply_text=reply_text,
        ),
    )
    sent_inputs = []

    class StubSession:
        def send_stream(self, text):
            sent_inputs.append(text)
            return iter([SimpleNamespace(text="Description")])

    monkeypatch.setattr(
        "gaia.messaging.telegram.tempfile.gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.ingest_image_to_vlm",
        lambda path: {"status": "success", "text": "A red bicycle"},
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", lambda user_id: StubSession()
    )

    await adapter._handle_message(update, None)

    downloaded_file.download_to_drive.assert_awaited_once_with(str(downloaded_path))
    assert sent_inputs == [
        "What is shown here? [photo uploaded and processed] - A red bicycle"
    ]


@pytest.mark.asyncio
async def test_document_ingest_failure_is_visible_in_session_input(
    monkeypatch, tmp_path
):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    streamed_reply = AsyncMock()
    reply_text = AsyncMock(return_value=streamed_reply)
    downloaded_file = SimpleNamespace(
        file_id="document-1",
        download_to_drive=AsyncMock(),
    )
    document = SimpleNamespace(
        file_name="notes.pdf", get_file=AsyncMock(return_value=downloaded_file)
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="Summarize this",
            photo=[],
            document=document,
            reply_text=reply_text,
        ),
    )
    sent_inputs = []

    class StubSession:
        def send_stream(self, text):
            sent_inputs.append(text)
            return iter([SimpleNamespace(text="Unable to index")])

    monkeypatch.setattr(
        "gaia.messaging.telegram.tempfile.gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.ingest_document_to_rag",
        lambda path: {"success": False},
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", lambda user_id: StubSession()
    )

    await adapter._handle_message(update, None)

    downloaded_file.download_to_drive.assert_awaited_once_with(
        str(tmp_path / "gaia_telegram_document-1")
    )
    assert sent_inputs == ["Summarize this [file uploaded: notes.pdf - index failed]"]


@pytest.mark.asyncio
async def test_photo_ingest_failure_is_visible_in_session_input(monkeypatch, tmp_path):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    streamed_reply = AsyncMock()
    reply_text = AsyncMock(return_value=streamed_reply)
    downloaded_file = SimpleNamespace(
        file_id="photo-2",
        download_to_drive=AsyncMock(),
    )
    photo = SimpleNamespace(get_file=AsyncMock(return_value=downloaded_file))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="Describe this",
            photo=[photo],
            document=None,
            reply_text=reply_text,
        ),
    )
    sent_inputs = []

    class StubSession:
        def send_stream(self, text):
            sent_inputs.append(text)
            return iter([SimpleNamespace(text="Unable to describe")])

    monkeypatch.setattr(
        "gaia.messaging.telegram.tempfile.gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.ingest_image_to_vlm",
        lambda path: {"status": "error", "error": "vlm unavailable"},
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", lambda user_id: StubSession()
    )

    await adapter._handle_message(update, None)

    downloaded_file.download_to_drive.assert_awaited_once_with(
        str(tmp_path / "gaia_telegram_photo-2.jpg")
    )
    assert sent_inputs == ["Describe this [photo uploaded - VLM failed]"]


@pytest.mark.asyncio
async def test_document_ingest_success_is_included_in_session_input(
    monkeypatch, tmp_path
):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    streamed_reply = AsyncMock()
    reply_text = AsyncMock(return_value=streamed_reply)
    downloaded_file = SimpleNamespace(
        file_id="document-2",
        download_to_drive=AsyncMock(),
    )
    document = SimpleNamespace(
        file_name="report.pdf", get_file=AsyncMock(return_value=downloaded_file)
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="Review this",
            photo=[],
            document=document,
            reply_text=reply_text,
        ),
    )
    sent_inputs = []

    class StubSession:
        def send_stream(self, text):
            sent_inputs.append(text)
            return iter([SimpleNamespace(text="Indexed")])

    monkeypatch.setattr(
        "gaia.messaging.telegram.tempfile.gettempdir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.ingest_document_to_rag",
        lambda path: {"success": True},
    )
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", lambda user_id: StubSession()
    )

    await adapter._handle_message(update, None)

    downloaded_file.download_to_drive.assert_awaited_once_with(
        str(tmp_path / "gaia_telegram_document-2")
    )
    assert sent_inputs == ["Review this [file indexed: report.pdf]"]
