import logging
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

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
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())
    adapter = run_telegram(
        token="fake-token-123", allowed_users={12345}, background=True
    )
    assert adapter.token == "fake-token-123"
    assert 12345 in adapter.allowed_users
    assert adapter.application is not None


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


def test_every_registered_update_handler_enforces_the_allowlist(mock_home, monkeypatch):
    """Every callback actually registered with Telegram must be guarded."""

    class FakeHandler:
        def __init__(self, *args):
            self.callback = args[-1]

    class FakeApplication:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

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
    fake_ext.CommandHandler = FakeHandler
    fake_ext.MessageHandler = FakeHandler
    fake_ext.filters = SimpleNamespace(ALL=FakeFilter(), COMMAND=FakeFilter())
    fake_telegram.ext = fake_ext
    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake_ext)
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    adapter = TelegramAdapter(token="fake-token")
    adapter.start(token="fake-token", background=True)

    callbacks = [handler.callback for handler in app.handlers]
    assert callbacks, "no Telegram handlers were registered"
    unguarded = [
        callback.__name__
        for callback in callbacks
        if not getattr(callback, "__gaia_allowlist_guarded__", False)
    ]
    assert not unguarded, f"registered handlers missing @require_allowed: {unguarded}"


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
