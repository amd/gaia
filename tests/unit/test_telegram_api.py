# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Bot API client and polling loop, against a recording fake of the Bot API.

Every test asserts the requests that would reach Telegram — method, path and
body — not just that a call was made.
"""

import asyncio
import functools
import json
import logging
import os
import signal
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from gaia.messaging import telegram
from gaia.messaging.telegram import UNAUTHORIZED_REPLY, TelegramAdapter
from gaia.messaging.telegram_api import (
    REDACTED,
    TelegramAPIError,
    TelegramBotAPI,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramTimedOut,
    TelegramTokenError,
    redact_token,
)

TOKEN = "123456789:AAH-secret_token_value_XYZ0123456789"
ALLOWED = 111
STRANGER = 999
_REAL_SLEEP = asyncio.sleep


def _ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def _error(status, description, **extra):
    return httpx.Response(
        status,
        json={"ok": False, "error_code": status, "description": description, **extra},
    )


class FakeBotAPI:
    """Serves the Bot API methods the adapter uses and records every call."""

    def __init__(self, updates=(), username="gaia_test_bot"):
        self.username = username
        self.updates = list(updates)
        self.calls = []  # (method, json body)
        self.downloads = []  # file paths requested from /file/bot<token>/
        self.overrides = {}  # method -> [httpx.Response | Exception, ...]
        self.files = {}  # file_id -> (file_path, bytes)
        self.on_idle = None
        self._next_message_id = 5000

    def transport(self):
        return httpx.MockTransport(self.ahandle)

    async def ahandle(self, request):
        # A real request yields to the loop; without this, cancel never lands.
        await _REAL_SLEEP(0)
        return self.handle(request)

    def of(self, method):
        return [body for name, body in self.calls if name == method]

    def handle(self, request):
        path = request.url.path
        file_prefix = f"/file/bot{TOKEN}/"
        if path.startswith(file_prefix):
            file_path = path[len(file_prefix) :]
            self.downloads.append(file_path)
            for stored_path, content in self.files.values():
                if stored_path == file_path:
                    return httpx.Response(200, content=content)
            return httpx.Response(404, text="Not Found")

        assert path.startswith(f"/bot{TOKEN}/"), f"token missing from {path}"
        assert request.method == "POST"
        method = path.rsplit("/", 1)[1]
        body = json.loads(request.content) if request.content else {}
        self.calls.append((method, body))

        queued = self.overrides.get(method)
        if queued:
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        if method == "getMe":
            return _ok({"id": 1, "is_bot": True, "username": self.username})
        if method == "deleteWebhook":
            return _ok(True)
        if method == "getUpdates":
            offset = body.get("offset")
            pending = [
                u for u in self.updates if offset is None or u["update_id"] >= offset
            ]
            if not pending and self.on_idle is not None:
                self.on_idle()
            return _ok(pending)
        if method == "sendMessage":
            self._next_message_id += 1
            return _ok(
                {
                    "message_id": self._next_message_id,
                    "chat": {"id": body["chat_id"], "type": "private"},
                    "text": body["text"],
                }
            )
        if method == "editMessageText":
            return _ok({"message_id": body["message_id"], "text": body["text"]})
        if method == "getFile":
            file_path, _ = self.files[body["file_id"]]
            return _ok({"file_id": body["file_id"], "file_path": file_path})
        raise AssertionError(f"unexpected Bot API method {method}")


def _message(update_id, user_id, text=None, chat_type="private", **fields):
    message = {
        "message_id": 100 + update_id,
        "chat": {"id": user_id, "type": chat_type},
        "from": {"id": user_id},
        **fields,
    }
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            length = len(text.split()[0])
            message["entities"] = [
                {"type": "bot_command", "offset": 0, "length": length}
            ]
    return {"update_id": update_id, "message": message}


@pytest.fixture
def fake():
    return FakeBotAPI()


@pytest.fixture
def adapter(fake):
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED}, idle_limit=5)
    adapter.api = TelegramBotAPI(TOKEN, transport=fake.transport())
    return adapter


@pytest.fixture
def stub_session(monkeypatch):
    sent = []

    class Session:
        def send_stream(self, text):
            sent.append(text)
            return iter([SimpleNamespace(text="Hi"), SimpleNamespace(text=" there")])

    monkeypatch.setattr(telegram, "get_or_create_session", lambda uid: Session())
    return sent


async def _poll_until_idle(adapter, fake):
    """Run the poll loop until the fake has no updates left, then stop it."""
    task = asyncio.create_task(adapter._poll())
    fake.on_idle = task.cancel
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=10)


# --- request shape ---------------------------------------------------------


async def test_send_message_posts_chat_id_and_text_to_the_token_path(fake):
    seen = []

    def record(request):
        seen.append(request)
        return fake.handle(request)

    api = TelegramBotAPI(TOKEN, transport=httpx.MockTransport(record))
    await api.send_message(42, "hello")
    await api.aclose()

    request = seen[0]
    assert request.method == "POST"
    assert request.url.host == "api.telegram.org"
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {"chat_id": 42, "text": "hello"}


async def test_reply_in_a_group_quotes_the_original_and_private_does_not(adapter, fake):
    group = telegram.TelegramUpdate(
        adapter.api, _message(1, ALLOWED, "hi", chat_type="group")
    )
    private = telegram.TelegramUpdate(adapter.api, _message(2, ALLOWED, "hi"))

    await group.message.reply_text("a")
    await private.message.reply_text("b")

    assert fake.of("sendMessage") == [
        {"chat_id": ALLOWED, "text": "a", "reply_parameters": {"message_id": 101}},
        {"chat_id": ALLOWED, "text": "b"},
    ]


# --- dispatch --------------------------------------------------------------


async def test_start_command_gets_the_greeting_and_no_model_call(
    adapter, fake, stub_session
):
    fake.updates = [_message(1, ALLOWED, "/start")]
    await _poll_until_idle(adapter, fake)

    [greeting] = fake.of("sendMessage")
    assert greeting["chat_id"] == ALLOWED
    assert greeting["text"].startswith("Hello! I'm Gaia.")
    assert stub_session == []


async def test_plain_text_streams_into_the_thinking_placeholder(
    adapter, fake, stub_session
):
    fake.updates = [_message(1, ALLOWED, "what time is it")]
    await _poll_until_idle(adapter, fake)

    assert stub_session == ["what time is it"]
    assert fake.of("sendMessage") == [{"chat_id": ALLOWED, "text": "Thinking..."}]
    edits = fake.of("editMessageText")
    assert [e["text"] for e in edits] == ["Hi", "Hi there"]
    # Edits target the placeholder the bot sent, in the user's chat.
    assert {(e["chat_id"], e["message_id"]) for e in edits} == {(ALLOWED, 5001)}


async def test_photo_downloads_the_largest_size_to_the_same_tmp_path(
    adapter, fake, stub_session, monkeypatch, tmp_path
):
    fake.files["large"] = ("photos/file_7.jpg", b"\xff\xd8jpeg-bytes")
    fake.updates = [
        _message(
            1,
            ALLOWED,
            "what is this",
            photo=[{"file_id": "small"}, {"file_id": "large"}],
        )
    ]
    ingested = []

    def fake_vlm(path):
        with open(path, "rb") as handle:
            ingested.append((path, handle.read()))
        return {"status": "success", "text": "A cat"}

    monkeypatch.setattr(telegram.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(telegram, "ingest_image_to_vlm", fake_vlm)

    await _poll_until_idle(adapter, fake)

    assert fake.of("getFile") == [{"file_id": "large"}]
    assert fake.downloads == ["photos/file_7.jpg"]
    expected = os.path.join(str(tmp_path), "gaia_telegram_large.jpg")
    assert ingested == [(expected, b"\xff\xd8jpeg-bytes")]
    assert stub_session == ["what is this [photo uploaded and processed] - A cat"]


async def test_document_downloads_and_is_indexed(
    adapter, fake, stub_session, monkeypatch, tmp_path
):
    fake.files["doc-1"] = ("documents/file_3.pdf", b"%PDF-1.7")
    fake.updates = [
        _message(1, ALLOWED, document={"file_id": "doc-1", "file_name": "notes.pdf"})
    ]
    ingested = []

    def fake_rag(path):
        with open(path, "rb") as handle:
            ingested.append((path, handle.read()))
        return {"success": True}

    monkeypatch.setattr(telegram.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(telegram, "ingest_document_to_rag", fake_rag)

    await _poll_until_idle(adapter, fake)

    assert fake.of("getFile") == [{"file_id": "doc-1"}]
    assert fake.downloads == ["documents/file_3.pdf"]
    expected = os.path.join(str(tmp_path), "gaia_telegram_doc-1")
    assert ingested == [(expected, b"%PDF-1.7")]
    assert stub_session == ["[file indexed: notes.pdf]"]


@pytest.mark.parametrize("text", ["hello", "/start"])
async def test_unauthorized_user_is_refused_without_a_session(
    adapter, fake, monkeypatch, text
):
    def fail(uid):
        raise AssertionError(f"session created for {uid}")

    monkeypatch.setattr(telegram, "get_or_create_session", fail)
    fake.updates = [_message(1, STRANGER, text)]

    await _poll_until_idle(adapter, fake)

    assert fake.of("sendMessage") == [{"chat_id": STRANGER, "text": UNAUTHORIZED_REPLY}]
    assert fake.of("getFile") == []


async def test_other_commands_and_commands_for_other_bots_are_ignored(
    adapter, fake, stub_session
):
    fake.updates = [
        _message(1, ALLOWED, "/help"),
        _message(2, ALLOWED, "/start@some_other_bot"),
    ]
    await _poll_until_idle(adapter, fake)

    assert fake.of("sendMessage") == []
    assert stub_session == []


async def test_start_addressed_to_this_bot_by_name_is_handled(
    adapter, fake, stub_session
):
    fake.updates = [_message(1, ALLOWED, "/start@Gaia_Test_Bot")]
    await _poll_until_idle(adapter, fake)

    assert fake.of("sendMessage")[0]["text"].startswith("Hello! I'm Gaia.")


# --- polling ---------------------------------------------------------------


async def test_startup_validates_token_and_clears_webhook_before_polling(
    adapter, fake, stub_session
):
    await _poll_until_idle(adapter, fake)

    assert [name for name, _ in fake.calls[:3]] == [
        "getMe",
        "deleteWebhook",
        "getUpdates",
    ]
    assert fake.of("deleteWebhook") == [{"drop_pending_updates": False}]
    assert fake.of("getUpdates")[0] == {
        "timeout": telegram.POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }


async def test_offset_advances_so_updates_are_handled_once(adapter, fake, stub_session):
    fake.updates = [_message(7, ALLOWED, "one"), _message(8, ALLOWED, "two")]
    await _poll_until_idle(adapter, fake)

    offsets = [body.get("offset") for body in fake.of("getUpdates")]
    assert offsets == [None, 9]
    assert stub_session == ["one", "two"]


async def test_a_malformed_update_is_skipped_and_polling_continues(
    adapter, fake, stub_session, caplog
):
    fake.updates = [
        {"update_id": 1, "message": {"message_id": 9, "text": "secret words"}},
        _message(2, ALLOWED, "still served"),
    ]
    with caplog.at_level(logging.ERROR, logger="gaia.messaging.telegram"):
        await _poll_until_idle(adapter, fake)

    assert stub_session == ["still served"]
    assert [b.get("offset") for b in fake.of("getUpdates")] == [None, 3]
    assert "Skipping malformed Telegram update 1" in caplog.text
    assert "secret words" not in caplog.text


async def test_an_update_without_an_id_is_skipped(adapter, fake, stub_session, caplog):
    fake.updates = [_message(5, ALLOWED, "ok")]
    fake.overrides["getUpdates"] = [_ok([{"message": {}}, _message(5, ALLOWED, "ok")])]
    with caplog.at_level(logging.ERROR, logger="gaia.messaging.telegram"):
        await _poll_until_idle(adapter, fake)

    assert stub_session == ["ok"]
    assert "Skipping Telegram update with no update_id" in caplog.text


async def test_a_failing_handler_does_not_refetch_its_update(
    adapter, fake, monkeypatch, caplog
):
    seen = []

    async def failing_handler(self, update, context):
        seen.append(update.update_id)
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(TelegramAdapter, "_handle_message", failing_handler)
    fake.updates = [_message(3, ALLOWED, "x")]
    with caplog.at_level(logging.ERROR, logger="gaia.messaging.telegram"):
        await _poll_until_idle(adapter, fake)

    assert seen == [3]
    assert [b.get("offset") for b in fake.of("getUpdates")] == [None, 4]
    assert "Telegram update 3 from user 111 failed" in caplog.text


async def test_429_on_get_updates_waits_retry_after_then_resumes(
    adapter, fake, stub_session, monkeypatch
):
    sleeps = []
    real_sleep = asyncio.sleep

    async def record_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(telegram.asyncio, "sleep", record_sleep)
    fake.overrides["getUpdates"] = [
        _error(429, "Too Many Requests: retry after 17", parameters={"retry_after": 17})
    ]
    fake.updates = [_message(1, ALLOWED, "after the wait")]

    await _poll_until_idle(adapter, fake)

    assert sleeps == [17.0]
    assert stub_session == ["after the wait"]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        _error(429, "Too Many Requests"),
    ],
    ids=["connect-error", "timeout", "429-without-retry-after"],
)
async def test_network_errors_back_off_and_log_then_resume(
    adapter, fake, stub_session, monkeypatch, caplog, failure
):
    sleeps = []
    real_sleep = asyncio.sleep

    async def record_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(telegram.asyncio, "sleep", record_sleep)
    fake.overrides["getUpdates"] = [failure, failure]
    fake.updates = [_message(1, ALLOWED, "back online")]

    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        await _poll_until_idle(adapter, fake)

    assert sleeps == [1.0, 2.0]
    assert "Retrying in 1s" in caplog.text
    assert stub_session == ["back online"]


async def test_rejected_token_fails_startup_with_a_remedy(adapter, fake):
    fake.overrides["getMe"] = [_error(401, "Unauthorized")]

    with pytest.raises(TelegramAPIError) as excinfo:
        await adapter._poll()

    assert "@BotFather" in str(excinfo.value)
    assert fake.of("getUpdates") == []


async def test_conflict_while_polling_stops_the_loop_loudly(adapter, fake):
    fake.overrides["getUpdates"] = [
        _error(409, "Conflict: terminated by other getUpdates request")
    ]

    with pytest.raises(TelegramAPIError) as excinfo:
        await asyncio.wait_for(adapter._poll(), timeout=10)

    assert excinfo.value.error_code == 409
    assert "gaia telegram stop" in str(excinfo.value)


async def test_transient_edit_failure_is_logged_not_raised(
    adapter, fake, stub_session, caplog
):
    fake.overrides["editMessageText"] = [
        _error(429, "Too Many Requests", parameters={"retry_after": 3})
    ]
    fake.updates = [_message(1, ALLOWED, "hi")]

    with caplog.at_level(logging.DEBUG, logger="gaia.messaging.telegram"):
        await _poll_until_idle(adapter, fake)

    assert "Transient telegram edit failure" in caplog.text
    assert "Unexpected error editing" not in caplog.text
    assert [e["text"] for e in fake.of("editMessageText")] == ["Hi", "Hi there"]


# --- error mapping ---------------------------------------------------------


async def test_429_without_retry_after_is_a_transient_network_error():
    api = TelegramBotAPI(
        TOKEN, transport=httpx.MockTransport(lambda r: _error(429, "Slow down"))
    )
    with pytest.raises(TelegramNetworkError) as excinfo:
        await api.send_message(1, "x")
    assert not isinstance(excinfo.value, TelegramRetryAfter)
    assert excinfo.value.error_code == 429


async def test_429_maps_to_retry_after():
    api = TelegramBotAPI(
        TOKEN,
        transport=httpx.MockTransport(
            lambda r: _error(429, "Too Many Requests", parameters={"retry_after": 5})
        ),
    )
    with pytest.raises(TelegramRetryAfter) as excinfo:
        await api.send_message(1, "x")
    assert excinfo.value.retry_after == 5.0
    assert excinfo.value.method == "sendMessage"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (httpx.ConnectError("refused"), TelegramNetworkError),
        (httpx.ConnectTimeout("slow"), TelegramTimedOut),
        (httpx.ReadTimeout("slow"), TelegramTimedOut),
    ],
)
async def test_transport_failures_map_to_network_errors(exc, expected):
    def raise_it(request):
        raise exc

    api = TelegramBotAPI(TOKEN, transport=httpx.MockTransport(raise_it))
    with pytest.raises(expected) as excinfo:
        await api.get_me()
    assert isinstance(excinfo.value, TelegramNetworkError)
    assert excinfo.value.method == "getMe"
    assert "Check the network connection to api.telegram.org." in str(excinfo.value)


@pytest.mark.parametrize(
    "response, expected",
    [
        (_error(400, "Bad Request: chat not found"), TelegramAPIError),
        (httpx.Response(200, text="<html>not json</html>"), TelegramAPIError),
        (httpx.Response(200, json={"result": []}), TelegramAPIError),
        (httpx.Response(200, json={"ok": True}), TelegramAPIError),
        (httpx.Response(502, text="Bad Gateway"), TelegramNetworkError),
    ],
    ids=["ok-false", "not-json", "no-ok-field", "no-result", "bad-gateway"],
)
async def test_failed_or_malformed_responses_raise_actionable_errors(
    response, expected
):
    api = TelegramBotAPI(TOKEN, transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(expected) as excinfo:
        await api.send_message(1, "x")
    message = str(excinfo.value)
    assert "sendMessage" in message
    assert "amd-gaia.ai/docs/guides/telegram-adapter" in message


async def test_get_updates_rejects_a_non_list_result():
    api = TelegramBotAPI(TOKEN, transport=httpx.MockTransport(lambda r: _ok({})))
    with pytest.raises(TelegramAPIError, match="expected a list"):
        await api.get_updates(offset=None, timeout=1, allowed_updates=["message"])


async def test_failed_download_raises_and_leaves_no_file(tmp_path):
    api = TelegramBotAPI(
        TOKEN, transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    dest = tmp_path / "out.bin"
    with pytest.raises(TelegramAPIError, match="resend the file"):
        await api.download_file("documents/gone.pdf", str(dest))
    assert not dest.exists()


@pytest.mark.parametrize("token", ["", "   ", "\n", None])
def test_empty_token_is_refused(token):
    with pytest.raises(TelegramTokenError, match="@BotFather"):
        TelegramBotAPI(token)


# --- token redaction -------------------------------------------------------


def test_redact_token_removes_exact_and_token_shaped_text():
    other = "987654321:BBBotherSecretValue_1234567890"
    text = f"https://api.telegram.org/bot{TOKEN}/getMe and {other}"
    redacted = redact_token(text, TOKEN)
    assert TOKEN not in redacted and other not in redacted
    assert f"bot{REDACTED}/getMe" in redacted


async def test_token_never_reaches_logs_or_errors(caplog):
    """The wire request carries the token; nothing a user can read does."""
    wire_paths = []

    def leaky(request):
        wire_paths.append(request.url.path)
        if request.url.path.endswith("/getMe"):
            # A transport error that embeds the full URL, as some do.
            raise httpx.ConnectError(f"cannot connect to {request.url}")
        return _error(409, f"Conflict for bot{TOKEN}")

    api = TelegramBotAPI(TOKEN, transport=httpx.MockTransport(leaky))
    errors = []
    with caplog.at_level(logging.DEBUG):
        for call in (api.get_me(), api.send_message(1, "x")):
            try:
                await call
            except TelegramAPIError as e:
                errors.append(e)

    assert wire_paths == [f"/bot{TOKEN}/getMe", f"/bot{TOKEN}/sendMessage"]
    assert len(errors) == 2
    for error in errors:
        assert TOKEN not in str(error)
        assert TOKEN not in str(error.description or "")
    # httpx logs every request URL; it must only ever see the placeholder.
    assert f"bot{REDACTED}/sendMessage" in caplog.text
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(api)


# --- lifecycle -------------------------------------------------------------


def _idle(polled):
    polled.set()
    time.sleep(0.01)  # stand in for the server holding the long poll


def _patch_api(monkeypatch, fake):
    monkeypatch.setattr(
        telegram,
        "TelegramBotAPI",
        functools.partial(TelegramBotAPI, transport=fake.transport()),
    )


@pytest.mark.allow_network
def test_background_polling_thread_stops_cleanly(mock_home, monkeypatch, stub_session):
    fake = FakeBotAPI(updates=[_message(41, ALLOWED, "hi")])
    polled = threading.Event()
    fake.on_idle = functools.partial(_idle, polled)
    _patch_api(monkeypatch, fake)
    handlers = {}
    monkeypatch.setattr(
        telegram.signal, "signal", lambda sig, h: handlers.__setitem__(sig, h)
    )

    adapter = telegram.run_telegram(
        token=TOKEN, allowed_users={ALLOWED}, background=True, health_port=0
    )
    try:
        assert polled.wait(timeout=10)
        assert adapter._poll_thread.is_alive()
        assert not adapter._poll_thread.daemon
        assert (mock_home / ".gaia" / "telegram.pid").exists()

        handlers[signal.SIGTERM](signal.SIGTERM, None)
        adapter._poll_thread.join(timeout=10)
    finally:
        if adapter._poll_thread.is_alive():
            adapter.stop()
            adapter._poll_thread.join(timeout=10)

    assert not adapter._poll_thread.is_alive()
    assert not (mock_home / ".gaia" / "telegram.pid").exists()
    assert adapter.api._client.is_closed
    _assert_confirmed_on_stop(fake, 42)


@pytest.mark.allow_network
def test_background_start_with_a_rejected_token_raises_and_removes_pid(
    mock_home, monkeypatch
):
    fake = FakeBotAPI()
    fake.overrides["getMe"] = [_error(401, "Unauthorized")]
    _patch_api(monkeypatch, fake)
    monkeypatch.setattr(telegram.signal, "signal", lambda *_a: None)

    with pytest.raises(TelegramAPIError, match="@BotFather"):
        telegram.run_telegram(
            token=TOKEN, allowed_users={ALLOWED}, background=True, health_port=0
        )

    assert not (mock_home / ".gaia" / "telegram.pid").exists()
    assert fake.of("getUpdates") == []


def test_foreground_start_blocks_until_stopped(monkeypatch, stub_session):
    fake = FakeBotAPI(updates=[_message(7, ALLOWED, "hi")])
    polled = threading.Event()
    fake.on_idle = functools.partial(_idle, polled)
    _patch_api(monkeypatch, fake)
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED})
    # Off the main thread signal registration is skipped, as in an embedding app.
    runner = threading.Thread(target=adapter.start, kwargs={"token": TOKEN})
    runner.start()
    try:
        assert polled.wait(timeout=10)
        assert runner.is_alive()
    finally:
        assert adapter.stop()
        runner.join(timeout=10)
    assert not runner.is_alive()
    assert adapter.api._client.is_closed
    _assert_confirmed_on_stop(fake, 8)


def _assert_confirmed_on_stop(fake, next_offset):
    """Without this last call Telegram redelivers the batch after a restart."""
    method, body = fake.calls[-1]
    assert method == "getUpdates"
    assert body == {
        "offset": next_offset,
        "timeout": 0,
        "allowed_updates": ["message"],
    }


def test_a_cancelled_poll_task_still_confirms_its_offset(stub_session):
    fake = FakeBotAPI(updates=[_message(3, ALLOWED, "a"), _message(4, ALLOWED, "b")])
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED})
    adapter.api = TelegramBotAPI(TOKEN, transport=fake.transport())
    cancelled = []

    def cancel_once():
        if not cancelled:
            cancelled.append(True)
            adapter._poll_task.cancel()

    fake.on_idle = cancel_once
    runner = threading.Thread(target=adapter._run_polling_loop)
    runner.start()
    runner.join(timeout=10)

    assert not runner.is_alive()
    assert stub_session == ["a", "b"]
    _assert_confirmed_on_stop(fake, 5)
    assert adapter.api._client.is_closed


def test_a_failed_confirm_on_stop_is_logged(stub_session, caplog):
    fake = FakeBotAPI(updates=[_message(3, ALLOWED, "a")])
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED})
    adapter.api = TelegramBotAPI(TOKEN, transport=fake.transport())

    def cancel_then_fail():
        adapter._poll_task.cancel()
        fake.overrides["getUpdates"] = [httpx.ConnectError("network down")]

    fake.on_idle = cancel_then_fail
    with caplog.at_level(logging.WARNING, logger="gaia.messaging.telegram"):
        runner = threading.Thread(target=adapter._run_polling_loop)
        runner.start()
        runner.join(timeout=10)

    assert not runner.is_alive()
    assert "Could not confirm Telegram updates before offset 4" in caplog.text
    assert adapter.api._client.is_closed


def test_stop_before_any_update_sends_no_confirm():
    fake = FakeBotAPI()
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED})
    adapter.api = TelegramBotAPI(TOKEN, transport=fake.transport())
    fake.on_idle = lambda: adapter._poll_task.cancel()
    runner = threading.Thread(target=adapter._run_polling_loop)
    runner.start()
    runner.join(timeout=10)

    assert [body.get("timeout") for body in fake.of("getUpdates")] == [
        telegram.POLL_TIMEOUT
    ]


def test_stop_before_start_reports_it_never_started(caplog):
    adapter = TelegramAdapter(token=TOKEN, allowed_users={ALLOWED})
    with caplog.at_level(logging.ERROR, logger="gaia.messaging.telegram"):
        assert adapter.stop() is False
    assert "never started" in caplog.text
