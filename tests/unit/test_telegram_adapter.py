import logging
import os
import sys
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
