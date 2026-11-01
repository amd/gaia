# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Minimal async client for the Telegram Bot API (https://core.telegram.org/bots/api).

Covers exactly what the Telegram adapter uses: long polling, sending and
editing text, and downloading photos and documents. Built on httpx so GAIA
ships no copyleft Telegram library.

The bot token never appears in a URL httpx can see. Requests carry a
placeholder that a wrapping transport swaps for the real token just before
the wire, so httpx's request log, exception messages and ``response.request``
all show ``bot<REDACTED>`` instead.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

import httpx

from gaia.logger import get_logger

log = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DOCS_URL = "https://amd-gaia.ai/docs/guides/telegram-adapter"

REDACTED = "REDACTED"
_PLACEHOLDER_SEGMENT = f"/bot{REDACTED}/"
# A Bot API token is "<bot id>:<secret>"; used to scrub any token-shaped text.
_TOKEN_PATTERN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{20,}")

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=1.0)


def redact_token(text: str, token: Optional[str] = None) -> str:
    """Remove a bot token from ``text``: the exact token, then any token-shaped run."""
    if token:
        text = text.replace(token, REDACTED)
    return _TOKEN_PATTERN.sub(REDACTED, text)


class TelegramTokenError(ValueError):
    """No usable bot token was supplied."""


def require_token(token: str) -> None:
    """Raise ``TelegramTokenError`` unless ``token`` is a non-blank string."""
    if not isinstance(token, str) or not token.strip():
        raise TelegramTokenError(
            "A Telegram bot token is required. Get one from @BotFather and "
            f"pass it with --token. Docs: {DOCS_URL}"
        )


class TelegramAPIError(RuntimeError):
    """A Bot API call failed. The message names the method, cause and remedy."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        status_code: Optional[int] = None,
        error_code: Optional[int] = None,
        description: Optional[str] = None,
    ):
        super().__init__(message)
        self.method = method
        self.status_code = status_code
        self.error_code = error_code
        self.description = description


class TelegramNetworkError(TelegramAPIError):
    """Telegram could not be reached, or answered with a server-side error."""


class TelegramTimedOut(TelegramNetworkError):
    """A request to Telegram timed out."""


class TelegramRetryAfter(TelegramAPIError):
    """HTTP 429: Telegram asked the caller to wait ``retry_after`` seconds."""

    def __init__(self, message: str, *, method: str, retry_after: float, **kwargs):
        super().__init__(message, method=method, **kwargs)
        self.retry_after = retry_after


def _remedy(error_code: Optional[int]) -> str:
    if error_code in (401, 404):
        return (
            "The bot token was rejected — copy it again from @BotFather and pass "
            "it with --token."
        )
    if error_code == 409:
        return (
            "Another process is polling this bot, or a webhook is set. Stop the "
            "other instance (gaia telegram stop) before starting this one."
        )
    if error_code == 403:
        return "The user blocked the bot or never started a chat with it."
    return f"See {DOCS_URL}."


class _TokenTransport(httpx.AsyncBaseTransport):
    """Swap the URL placeholder for the real token on the way to the wire."""

    def __init__(self, inner: httpx.AsyncBaseTransport, token: str):
        self._inner = inner
        self._segment = f"/bot{token}/".encode("ascii")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raw_path = request.url.raw_path.replace(
            _PLACEHOLDER_SEGMENT.encode("ascii"), self._segment, 1
        )
        wire = httpx.Request(
            request.method,
            request.url.copy_with(raw_path=raw_path),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        return await self._inner.handle_async_request(wire)

    async def aclose(self) -> None:
        await self._inner.aclose()


class TelegramBotAPI:
    """Async Bot API client. Create and use it on one event loop."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = TELEGRAM_API_BASE,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ):
        require_token(token)
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            transport=_TokenTransport(transport or httpx.AsyncHTTPTransport(), token),
            timeout=timeout,
        )

    def __repr__(self) -> str:
        return f"TelegramBotAPI(base_url={self._base_url!r})"

    def redact(self, text: str) -> str:
        return redact_token(text, self._token)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        read_timeout: Optional[float] = None,
    ) -> Any:
        """POST ``payload`` as JSON to ``method`` and return the ``result`` field.

        Raises:
            TelegramRetryAfter: HTTP 429 with ``parameters.retry_after``.
            TelegramTimedOut: the request timed out.
            TelegramNetworkError: connection failure or a 5xx answer.
            TelegramAPIError: any other ``ok: false`` or malformed response.
        """
        url = f"{self._base_url}/bot{REDACTED}/{method}"
        timeout = self._timeout
        if read_timeout is not None:
            timeout = httpx.Timeout(
                connect=self._timeout.connect,
                read=read_timeout,
                write=self._timeout.write,
                pool=self._timeout.pool,
            )
        try:
            response = await self._client.post(url, json=payload or {}, timeout=timeout)
        except httpx.TimeoutException as e:
            raise TelegramTimedOut(
                f"Telegram {method} timed out: {self.redact(str(e)) or type(e).__name__}. "
                "Check the network connection to api.telegram.org.",
                method=method,
            ) from e
        except httpx.TransportError as e:
            raise TelegramNetworkError(
                f"Telegram {method} could not reach {self._base_url}: "
                f"{self.redact(str(e)) or type(e).__name__}. "
                "Check the network connection to api.telegram.org.",
                method=method,
            ) from e
        return self._parse(method, response)

    def _parse(self, method: str, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or "ok" not in body:
            snippet = self.redact(response.text[:200])
            cls = (
                TelegramNetworkError
                if response.status_code >= 500
                else TelegramAPIError
            )
            raise cls(
                f"Telegram {method} returned a malformed response "
                f"(HTTP {response.status_code}): {snippet!r}. {_remedy(None)}",
                method=method,
                status_code=response.status_code,
            )
        if body["ok"] is True:
            if "result" not in body:
                raise TelegramAPIError(
                    f"Telegram {method} returned ok without a result. {_remedy(None)}",
                    method=method,
                    status_code=response.status_code,
                )
            return body["result"]

        error_code = body.get("error_code")
        description = self.redact(str(body.get("description") or "no description"))
        parameters = body.get("parameters") or {}
        common = {
            "method": method,
            "status_code": response.status_code,
            "error_code": error_code,
            "description": description,
        }
        if error_code == 429 and "retry_after" in parameters:
            retry_after = float(parameters["retry_after"])
            raise TelegramRetryAfter(
                f"Telegram rate-limited {method}: retry after {retry_after:g}s "
                f"({description}).",
                retry_after=retry_after,
                **common,
            )
        message = (
            f"Telegram {method} failed (error {error_code}): {description}. "
            f"{_remedy(error_code)}"
        )
        # A 429 without retry_after is still transient; callers back off.
        if response.status_code >= 500 or error_code == 429:
            raise TelegramNetworkError(message, **common)
        raise TelegramAPIError(message, **common)

    async def get_me(self) -> Dict[str, Any]:
        return await self.call("getMe")

    async def delete_webhook(self) -> bool:
        return await self.call("deleteWebhook", {"drop_pending_updates": False})

    async def get_updates(
        self,
        offset: Optional[int],
        timeout: int,
        allowed_updates: Sequence[str],
    ) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": list(allowed_updates),
        }
        if offset is not None:
            payload["offset"] = offset
        # The server holds the request up to `timeout` seconds, so read longer.
        result = await self.call(
            "getUpdates", payload, read_timeout=timeout + self._timeout.read
        )
        if not isinstance(result, list):
            raise TelegramAPIError(
                f"Telegram getUpdates returned {type(result).__name__}, expected a "
                f"list. {_remedy(None)}",
                method="getUpdates",
            )
        return result

    async def send_message(
        self, chat_id: int, text: str, reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return await self.call("sendMessage", payload)

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str
    ) -> Dict[str, Any]:
        return await self.call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    async def get_file(self, file_id: str) -> Dict[str, Any]:
        return await self.call("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str, destination: str) -> None:
        """Download ``file_path`` (from :meth:`get_file`) to ``destination``."""
        url = f"{self._base_url}/file/bot{REDACTED}/{file_path.lstrip('/')}"
        method = "file download"
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise TelegramAPIError(
                        f"Telegram {method} of {file_path!r} failed (HTTP "
                        f"{response.status_code}): "
                        f"{self.redact(response.text[:200])!r}. Telegram keeps "
                        "files for at least an hour; resend the file.",
                        method=method,
                        status_code=response.status_code,
                    )
                with open(destination, "wb") as handle:
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
        except httpx.TimeoutException as e:
            _discard(destination)
            raise TelegramTimedOut(
                f"Telegram {method} of {file_path!r} timed out. Check the network "
                "connection to api.telegram.org.",
                method=method,
            ) from e
        except httpx.TransportError as e:
            _discard(destination)
            raise TelegramNetworkError(
                f"Telegram {method} of {file_path!r} failed: "
                f"{self.redact(str(e)) or type(e).__name__}. Check the network "
                "connection to api.telegram.org.",
                method=method,
            ) from e


def _discard(path: str) -> None:
    """Drop a partial download so a truncated file is never ingested."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


class TelegramFile:
    def __init__(self, api: TelegramBotAPI, data: Dict[str, Any]):
        self._api = api
        self.file_id: str = data["file_id"]
        self.file_path: Optional[str] = data.get("file_path")

    async def download_to_drive(self, destination: str) -> None:
        if not self.file_path:
            raise TelegramAPIError(
                f"Telegram returned no file_path for file {self.file_id!r}, so it "
                "cannot be downloaded. Bots can only fetch files up to 20 MB; "
                "send a smaller file.",
                method="getFile",
            )
        await self._api.download_file(self.file_path, destination)


class TelegramAttachment:
    """A photo size or document: anything with a ``file_id`` to fetch."""

    def __init__(self, api: TelegramBotAPI, data: Dict[str, Any]):
        self._api = api
        self.file_id: str = data["file_id"]
        self.file_name: Optional[str] = data.get("file_name")

    async def get_file(self) -> TelegramFile:
        return TelegramFile(self._api, await self._api.get_file(self.file_id))


class TelegramUser:
    def __init__(self, data: Dict[str, Any]):
        self.id: int = data["id"]
        self.username: Optional[str] = data.get("username")


_OTHER_MEDIA = ("video", "voice", "audio", "sticker", "animation", "video_note")


class TelegramMessage:
    def __init__(self, api: TelegramBotAPI, data: Dict[str, Any]):
        self._api = api
        self.raw = data
        self.message_id: int = data["message_id"]
        chat = data.get("chat") or {}
        self.chat_id: int = chat["id"]
        self.chat_type: Optional[str] = chat.get("type")
        self.text: Optional[str] = data.get("text")
        self.photo = [TelegramAttachment(api, p) for p in data.get("photo") or []]
        document = data.get("document")
        self.document = TelegramAttachment(api, document) if document else None
        for media_type in _OTHER_MEDIA:
            setattr(self, media_type, data.get(media_type))

    def command(self) -> Optional[tuple]:
        """``(name, bot_username_or_None)`` if the text starts with a bot command."""
        entities = self.raw.get("entities") or []
        if not self.text or not entities:
            return None
        first = entities[0]
        if first.get("type") != "bot_command" or first.get("offset") != 0:
            return None
        name, _, target = self.text[1 : first.get("length", 0)].partition("@")
        return name.lower(), (target or None)

    async def reply_text(self, text: str) -> "TelegramMessage":
        # Quote the original in groups so the reply is attributable.
        reply_to = None if self.chat_type == "private" else self.message_id
        sent = await self._api.send_message(self.chat_id, text, reply_to)
        return TelegramMessage(self._api, sent)

    async def edit_text(self, text: str) -> None:
        await self._api.edit_message_text(self.chat_id, self.message_id, text)


class TelegramUpdate:
    def __init__(self, api: TelegramBotAPI, data: Dict[str, Any]):
        self.update_id: int = data["update_id"]
        message = data.get("message")
        self.message = TelegramMessage(api, message) if message else None
        sender = (message or {}).get("from")
        self.effective_user = TelegramUser(sender) if sender else None
