import asyncio
import inspect
import logging
import os
import re
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging import telegram as telegram_module
from gaia.messaging.telegram import (
    FINAL_DELIVERY_WARNING,
    UNAUTHORIZED_REPLY,
    TelegramAdapter,
)
from gaia.messaging.telegram_api import TelegramNetworkError, TelegramRetryAfter


def test_run_telegram_scaffold_returns_adapter(mock_home, monkeypatch):
    from gaia.messaging.telegram import run_telegram

    # GAIA_TEST_MODE stops real polling; mock_home keeps the background pid/log
    # files out of the developer's real ~/.gaia (see test_telegram_background).
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    adapter = run_telegram(
        token="fake-token-123", allowed_users={12345}, background=True
    )
    assert adapter.token == "fake-token-123"
    assert 12345 in adapter.allowed_users
    # Test mode returns before any HTTP client exists, so none can leak.
    assert adapter.api is None


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


def test_every_dispatched_handler_enforces_the_allowlist():
    """Every handler ``_dispatch`` can route an update to must be guarded."""
    source = inspect.getsource(TelegramAdapter._dispatch)
    handlers = set(re.findall(r"self\.(_handle_\w+)\(", source))
    assert handlers, "no Telegram handlers are dispatched"
    unguarded = [
        name
        for name in sorted(handlers)
        if not getattr(
            getattr(TelegramAdapter, name), "__gaia_allowlist_guarded__", False
        )
    ]
    assert not unguarded, f"dispatched handlers missing @require_allowed: {unguarded}"


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


def _streaming_adapter(monkeypatch):
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

    class StubSession:
        def send_stream(self, text):
            return iter(
                [
                    SimpleNamespace(text="Hello"),
                    SimpleNamespace(text=" from Gaia"),
                ]
            )

    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session", lambda user_id: StubSession()
    )
    return adapter, streamed_reply, reply_text, update


@pytest.mark.asyncio
async def test_a_failed_final_edit_posts_a_warning(monkeypatch, caplog):
    """The user sees a failed answer without the answer or the API error repeated."""
    adapter, reply, reply_text, update = _streaming_adapter(monkeypatch)

    async def fail_final(text):
        if text == "Hello from Gaia":
            raise TelegramNetworkError("offline", method="editMessageText")

    reply.edit_text = AsyncMock(side_effect=fail_final)
    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await adapter._handle_message(update, None)

    assert reply.edit_text.await_args_list == [
        call("Hello"),
        call("Hello from Gaia"),
        call("Hello from Gaia"),
        call("Hello from Gaia"),
    ]
    assert reply_text.await_args_list == [
        call("Thinking..."),
        call(FINAL_DELIVERY_WARNING),
    ]
    assert any(
        record.levelno == logging.WARNING
        and "Final Telegram edit" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_a_rate_limited_final_edit_goes_straight_to_the_warning(
    monkeypatch, caplog
):
    """A 429 is not retried immediately; the user hears about it instead."""
    adapter, reply, reply_text, update = _streaming_adapter(monkeypatch)

    async def rate_limited(text):
        if text == "Hello from Gaia":
            raise TelegramRetryAfter(
                "slow down", method="editMessageText", retry_after=2
            )

    reply.edit_text = AsyncMock(side_effect=rate_limited)
    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await adapter._handle_message(update, None)

    assert reply.edit_text.await_count == 3
    assert reply_text.await_args_list == [
        call("Thinking..."),
        call(FINAL_DELIVERY_WARNING),
    ]
    assert any(
        record.levelno == logging.WARNING and "rate limited" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_a_failed_fallback_post_is_logged_and_raises(monkeypatch, caplog):
    """A warning that cannot be posted is not hidden from the bridge either."""
    adapter, reply, reply_text, update = _streaming_adapter(monkeypatch)

    async def fail_final(text):
        raise TelegramNetworkError("offline", method="editMessageText")

    async def fail_warning(text):
        if text == FINAL_DELIVERY_WARNING:
            raise TelegramNetworkError("offline", method="sendMessage")
        return reply

    reply.edit_text = AsyncMock(side_effect=fail_final)
    reply_text.side_effect = fail_warning
    with (
        caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"),
        pytest.raises(TelegramNetworkError),
    ):
        await adapter._handle_message(update, None)

    assert any(
        record.levelno == logging.WARNING
        and "Could not post the final Telegram warning" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_a_final_edit_retries_before_telling_the_user(monkeypatch):
    """A transient failure that clears on the final retry needs no fallback."""
    adapter, reply, reply_text, update = _streaming_adapter(monkeypatch)
    failed = False

    async def flaky_final(text):
        nonlocal failed
        if text == "Hello from Gaia" and not failed:
            failed = True
            raise TelegramNetworkError("offline", method="editMessageText")

    reply.edit_text = AsyncMock(side_effect=flaky_final)
    await adapter._handle_message(update, None)

    assert reply.edit_text.await_args_list == [
        call("Hello"),
        call("Hello from Gaia"),
        call("Hello from Gaia"),
    ]
    assert reply_text.await_args_list == [call("Thinking...")]


@pytest.mark.asyncio
async def test_a_mid_stream_edit_failure_stays_tolerant(monkeypatch, caplog):
    """A failed progress edit is not retried or reported as a final failure."""
    adapter, reply, reply_text, update = _streaming_adapter(monkeypatch)

    async def fail_first(text):
        if text == "Hello":
            raise TelegramNetworkError("offline", method="editMessageText")

    reply.edit_text = AsyncMock(side_effect=fail_first)
    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await adapter._handle_message(update, None)

    assert reply.edit_text.await_args_list == [
        call("Hello"),
        call("Hello from Gaia"),
    ]
    assert reply_text.await_args_list == [call("Thinking...")]
    assert not any(
        record.levelno == logging.WARNING
        and "Final Telegram edit" in record.getMessage()
        for record in caplog.records
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type", ["video", "voice", "audio", "sticker", "animation", "video_note"]
)
async def test_unsupported_media_type_replies_directly(monkeypatch, media_type):
    adapter = TelegramAdapter(token="fake-token", allowed_users={12345})
    reply_text = AsyncMock()
    media = {
        "video": None,
        "voice": None,
        "audio": None,
        "sticker": None,
        "animation": None,
        "video_note": None,
    }
    media[media_type] = SimpleNamespace(file_id=f"{media_type}-1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(
            text="",
            photo=[],
            document=None,
            reply_text=reply_text,
            **media,
        ),
    )

    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session",
        lambda user_id: (_ for _ in ()).throw(
            AssertionError("unsupported media should not create a session")
        ),
    )

    await adapter._handle_message(update, None)

    reply_text.assert_awaited_once_with(
        "Unsupported media type — I can handle photos and documents."
    )


def _text_update(user_id, text, reply):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            text=text,
            photo=[],
            document=None,
            reply_text=AsyncMock(return_value=reply),
        ),
    )


@pytest.mark.asyncio
async def test_a_reply_that_stops_streaming_is_stopped_and_frees_the_bot(monkeypatch):
    """Updates are handled one at a time, so a wedged reply held every chat."""
    release = threading.Event()
    closed = threading.Event()

    class WedgedSession:
        def send_stream(self, text):
            try:
                yield SimpleNamespace(text="Partial")
                release.wait(10)
                yield SimpleNamespace(text=" more")
            finally:
                closed.set()

    stuck_session = WedgedSession()
    monkeypatch.setitem(telegram_module._USER_SESSIONS, 1, stuck_session)
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session",
        lambda uid: telegram_module._USER_SESSIONS[uid],
    )
    adapter = TelegramAdapter(token="t", allowed_users={1}, idle_limit=1.0)
    reply = AsyncMock()
    update = _text_update(1, "hello", reply)

    started = time.monotonic()
    await asyncio.wait_for(adapter._handle_message(update, None), timeout=8)
    assert time.monotonic() - started < 8

    final = reply.edit_text.await_args_list[-1].args[0]
    assert final.startswith("Partial\n\n")
    assert "without progress" in final
    assert "new conversation" in final
    # The stuck generation keeps its session; the next message gets a new one.
    assert 1 not in telegram_module._USER_SESSIONS

    # Once the stream yields again it is closed, not read further.
    release.set()
    assert await asyncio.to_thread(closed.wait, 5)
    assert all(" more" not in c.args[0] for c in reply.edit_text.await_args_list)


def test_idle_limit_must_be_positive():
    with pytest.raises(ValueError, match="idle_limit"):
        TelegramAdapter(token="t", allowed_users={1}, idle_limit=0)


@pytest.mark.asyncio
async def test_a_turn_stopped_before_its_thread_ran_never_sends_later(monkeypatch):
    """The executor was busy, so the stopped turn's thread starts late. It must
    not then send the old question, nor touch the user's session."""
    from concurrent.futures import ThreadPoolExecutor

    sent = []

    class Session:
        def send_stream(self, text):
            sent.append(text)
            yield SimpleNamespace(text="ok")

    session = Session()
    monkeypatch.setitem(telegram_module._USER_SESSIONS, 1, session)
    monkeypatch.setattr(
        "gaia.messaging.telegram.get_or_create_session",
        lambda uid: telegram_module._USER_SESSIONS[uid],
    )
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(1)
    loop.set_default_executor(pool)
    gate = threading.Event()
    loop.run_in_executor(None, gate.wait)  # the one worker is held elsewhere

    adapter = TelegramAdapter(token="t", allowed_users={1}, idle_limit=0.3)
    old = _text_update(1, "old question", AsyncMock())
    await adapter._handle_message(old, None)
    gate.set()
    await asyncio.sleep(0.3)  # the stale generation gets its thread now
    await adapter._handle_message(_text_update(1, "new question", AsyncMock()), None)
    pool.shutdown(wait=True)

    assert sent == ["new question"]
    assert telegram_module._USER_SESSIONS[1] is session
