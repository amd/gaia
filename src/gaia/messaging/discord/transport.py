# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The ``discord.py`` side of the bridge: one Gateway session on its own loop.

``discord.py`` is asyncio; the adapter and the agent bridge are threads. The
boundary is kept to two rules:

* **Outbound** calls (from the agent's reader thread) run a coroutine on the
  loop and wait for it, so edits to one message land in order.
* **Inbound** events are handed to a worker thread, so a blocked pipe write to
  the agent can never stall the heartbeat and get the session dropped.

Calling an outbound method from the loop thread itself raises instead of
deadlocking.

Only the ``DIRECT_MESSAGES`` intent is requested. Discord delivers the content
of a DM to the bot without the privileged Message Content intent, and button
clicks arrive over the Gateway because no Interactions Endpoint URL is set.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from gaia.logger import get_logger
from gaia.messaging.discord.adapter import (
    Attachment,
    Button,
    DiscordAdapter,
    InboundClick,
    InboundMessage,
    PinnedMessage,
)

log = get_logger(__name__)

#: How long an outbound call may wait on Discord, including rate-limit sleeps.
CALL_TIMEOUT_SECONDS = 60.0

#: How often the adapter's upkeep (pairing-code rotation, lease) runs.
TICK_SECONDS = 60.0

_STYLES = {"primary": "primary", "danger": "danger", "secondary": "secondary"}


class DiscordTokenError(RuntimeError):
    """Discord refused the bot token."""


TOKEN_REFUSED = (
    "Discord refused the bot token — it was reset or the bot was deleted. "
    "Paste a new token in Settings → Messaging → Discord."
)


def _require_discord():
    try:
        import discord
    except ImportError as e:
        raise RuntimeError(
            "discord.py is missing from this GAIA install, so the Discord bridge "
            "cannot run. Reinstall GAIA from https://amd-gaia.ai/docs/guides/install "
            "— it ships with every install."
        ) from e
    return discord


def intents():
    """DMs, plus the non-privileged Guilds intent discord.py's cache expects."""
    discord = _require_discord()
    value = discord.Intents.none()
    value.dm_messages = True
    value.guilds = True
    return value


class GatewayTransport:
    """A ``discord.py`` client on a dedicated loop thread, behind sync calls."""

    def __init__(self) -> None:
        self._discord = _require_discord()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._workers = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="gaia-discord-in"
        )
        # DMs one at a time, in the order they arrived: a quick follow-up must
        # land after the message it follows, or it cannot join it. Clicks stay
        # on the pool above, so an attachment download never delays one.
        self._messages = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gaia-discord-dm"
        )
        # Upkeep makes slow network calls (GitHub, the lease); on its own
        # thread it can never hold up a DM or a button click.
        self._tick_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gaia-discord-tick"
        )
        self._adapter: Optional[DiscordAdapter] = None

    # ------------------------------------------------------------------
    # Outbound (sync, from any thread but the loop's)
    # ------------------------------------------------------------------

    def _call(self, coro_factory: Callable[[], Any]) -> Any:
        if self._loop is None:
            raise RuntimeError("The Discord transport is not running.")
        if threading.current_thread() is self._loop_thread:
            raise RuntimeError(
                "A Discord call was made from the event loop thread; it would "
                "deadlock. Hand inbound work to a worker first."
            )
        future = asyncio.run_coroutine_threadsafe(coro_factory(), self._loop)
        return future.result(timeout=CALL_TIMEOUT_SECONDS)

    def _channel(self, channel_id: str) -> Any:
        return self._client.get_partial_messageable(
            int(channel_id), type=self._discord.ChannelType.private
        )

    def _view(self, buttons: Sequence[Button]) -> Any:
        discord = self._discord
        view = discord.ui.View(timeout=None)
        for button in buttons:
            view.add_item(
                discord.ui.Button(
                    custom_id=button.custom_id,
                    label=button.label,
                    style=getattr(discord.ButtonStyle, _STYLES[button.style]),
                )
            )
        return view

    def send(
        self,
        channel_id: str,
        text: str,
        *,
        buttons: Optional[Sequence[Button]] = None,
        file_path: Optional[Path] = None,
        file_text: Optional[tuple] = None,
    ) -> str:
        discord = self._discord

        async def go() -> str:
            # No link previews: a summary with eight PR links became eight cards.
            kwargs: dict = {
                "allowed_mentions": discord.AllowedMentions.none(),
                "suppress_embeds": True,
            }
            if buttons:
                kwargs["view"] = self._view(buttons)
            if file_path is not None:
                kwargs["file"] = discord.File(str(file_path), filename=file_path.name)
            elif file_text is not None:
                name, body = file_text
                kwargs["file"] = discord.File(
                    io.BytesIO(body.encode("utf-8")), filename=name
                )
            message = await self._channel(channel_id).send(text, **kwargs)
            view = kwargs.get("view")
            if view is not None:
                # Clicks are routed by on_interaction against the adapter's own
                # records; a live View would dispatch them a second time.
                view.stop()
            return str(message.id)

        return self._call(go)

    def edit(
        self,
        channel_id: str,
        message_id: str,
        text: str,
        *,
        clear_buttons: bool = False,
    ) -> None:
        discord = self._discord

        async def go() -> None:
            message = self._channel(channel_id).get_partial_message(int(message_id))
            kwargs: dict = {
                "content": text,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if clear_buttons:
                # A closed prompt is a one-line record; its attached command
                # or file would otherwise show twice once the turn replies.
                kwargs["view"] = None
                kwargs["attachments"] = []
            await message.edit(**kwargs)

        self._call(go)

    def delete(self, channel_id: str, message_id: str) -> None:
        async def go() -> None:
            await self._channel(channel_id).get_partial_message(
                int(message_id)
            ).delete()

        self._call(go)

    def count_from_after(
        self, channel_id: str, author_id: str, after_id: str, bot_id: str = ""
    ) -> int:
        """*author_id*'s messages after *after_id* that no reply of *bot_id* follows.

        Another PC answering the same bot replies in this DM too, so a message
        it answered was not missed.
        """
        discord = self._discord

        async def go() -> int:
            count = 0
            async for message in self._channel(channel_id).history(
                limit=100, after=discord.Object(id=int(after_id)), oldest_first=True
            ):
                author = str(message.author.id)
                if author == author_id:
                    count += 1
                elif bot_id and author == bot_id:
                    count = 0
            return count

        return self._call(go)

    def open_controls(
        self, channel_id: str, author_id: str, limit: int = 100
    ) -> List[Tuple[str, List[str]]]:
        """*author_id*'s recent messages that still carry buttons, with their ids."""

        async def go() -> List[Tuple[str, List[str]]]:
            found: List[Tuple[str, List[str]]] = []
            async for message in self._channel(channel_id).history(limit=limit):
                if str(message.author.id) != author_id or not message.components:
                    continue
                ids = [
                    str(getattr(child, "custom_id", "") or "")
                    for row in message.components
                    for child in getattr(row, "children", [])
                ]
                if any(ids):
                    found.append((str(message.id), ids))
            return found

        return self._call(go)

    def pins(self, channel_id: str) -> List[PinnedMessage]:
        async def go() -> List[PinnedMessage]:
            return [
                PinnedMessage(str(m.id), str(m.author.id), m.content or "")
                async for m in self._channel(channel_id).pins(limit=50)
            ]

        return self._call(go)

    def unpin(self, channel_id: str, message_id: str) -> None:
        async def go() -> None:
            await self._channel(channel_id).get_partial_message(int(message_id)).unpin()

        self._call(go)

    def pin(self, channel_id: str, message_id: str) -> None:
        async def go() -> None:
            await self._channel(channel_id).get_partial_message(int(message_id)).pin()

        self._call(go)

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    def _message(self, message: Any) -> InboundMessage:
        discord = self._discord
        replied = getattr(getattr(message, "reference", None), "resolved", None)
        return InboundMessage(
            reply_to=str(getattr(replied, "content", "") or ""),
            id=str(message.id),
            channel_id=str(message.channel.id),
            author_id=str(message.author.id),
            author_name=str(message.author),
            author_is_bot=bool(message.author.bot),
            is_dm=message.guild is None
            and getattr(message.channel, "type", None) == discord.ChannelType.private,
            content=message.content or "",
            attachments=[
                Attachment(a.filename, a.url, int(a.size or 0))
                for a in message.attachments
            ],
        )

    def run(
        self,
        token: str,
        adapter: DiscordAdapter,
        *,
        take_over: bool = False,
        on_identity: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Connect and serve until :meth:`stop`. Blocks the calling thread.

        Raises:
            DiscordTokenError: Discord refused the token at login or later.
        """
        discord = self._discord
        # The bridge has no voice; don't warn on every start that it lacks it.
        logging.getLogger("discord.client").setLevel(logging.ERROR)
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._loop_thread = threading.current_thread()
        client = discord.Client(intents=intents(), max_messages=None)
        self._client = client
        self._adapter = adapter
        ready_once = threading.Event()

        def guard(fn: Callable[..., Any], *args: Any) -> Callable[[], None]:
            def guarded() -> None:
                try:
                    fn(*args)
                except (
                    Exception
                ):  # noqa: BLE001 - one bad event must not kill the bridge
                    log.exception("Discord event handler %s failed", fn.__name__)

            return guarded

        def in_worker(fn: Callable[..., Any], *args: Any) -> None:
            self._workers.submit(guard(fn, *args))

        @client.event
        async def on_ready() -> None:
            # A second on_ready is a fresh session after an invalidated one
            # (sleep, network loss): DMs sent meanwhile are not replayed, so
            # they are counted like a fresh start. "Answer from this PC" applies
            # to the first only, or a later reconnect would take the lease back
            # from a PC the user moved to since.
            first = not ready_once.is_set()
            ready_once.set()
            if on_identity is not None:
                on_identity(str(client.user.id), str(client.user))
            log.info("Discord Gateway ready as %s (first=%s)", client.user, first)
            in_worker(
                lambda: adapter.on_ready(resumed=False, take_over=take_over and first)
            )

        @client.event
        async def on_message(message: Any) -> None:
            self._messages.submit(guard(adapter.handle_message, self._message(message)))

        @client.event
        async def on_message_edit(before: Any, after: Any) -> None:
            # A link preview arriving is an edit too; only changed words count.
            if before.content == after.content:
                return
            edited = self._message(after)
            edited.edited_at = after.edited_at.timestamp() if after.edited_at else 0.0
            self._messages.submit(guard(adapter.handle_edit, edited))

        @client.event
        async def on_interaction(interaction: Any) -> None:
            if interaction.type != discord.InteractionType.component:
                return
            # Acknowledge first: Discord fails the click after 3 seconds.
            await interaction.response.defer()
            data = interaction.data or {}
            click = InboundClick(
                message_id=str(interaction.message.id) if interaction.message else "",
                channel_id=str(interaction.channel_id or ""),
                user_id=str(interaction.user.id),
                custom_id=str(data.get("custom_id") or ""),
                interaction_id=str(interaction.id),
            )

            def answer() -> None:
                note = adapter.handle_click(click)
                if note:
                    self._call(lambda: interaction.followup.send(note, ephemeral=True))

            in_worker(answer)

        async def ticker() -> None:
            while True:
                await asyncio.sleep(TICK_SECONDS)
                if ready_once.is_set():
                    self._tick_pool.submit(guard(adapter.tick))

        async def main() -> None:
            tick_task = asyncio.ensure_future(ticker())
            try:
                async with client:
                    await client.start(token)
            except discord.LoginFailure as e:
                raise DiscordTokenError(TOKEN_REFUSED) from e
            except discord.ConnectionClosed as e:
                # 4004: the token stopped working mid-session (reset or bot deleted).
                if e.code == 4004:
                    raise DiscordTokenError(TOKEN_REFUSED) from e
                raise
            finally:
                tick_task.cancel()

        try:
            loop.run_until_complete(main())
        finally:
            self._workers.shutdown(wait=False, cancel_futures=True)
            self._messages.shutdown(wait=False, cancel_futures=True)
            self._tick_pool.shutdown(wait=False, cancel_futures=True)
            loop.close()
            self._loop = None

    def stop(self) -> None:
        """Expire open prompts, then close the session. Any thread but the loop's.

        The prompts are edited while the session can still send; once the loop
        has closed, "Expired — the bridge stopped" could not reach Discord.
        """
        loop = self._loop
        if loop is None or self._client is None:
            return
        adapter = self._adapter

        async def shutdown() -> None:
            if adapter is not None:
                await loop.run_in_executor(None, adapter.close)
            await self._client.close()

        asyncio.run_coroutine_threadsafe(shutdown(), loop)


def verify_token(token: str) -> dict:
    """Prove a token works and describe its bot and application.

    REST only, no Gateway session: it spends none of the 1000-a-day identify
    budget. Returns ``{bot_id, bot_name, application_id, bot_public}``.

    Raises:
        DiscordTokenError: Discord refused the token.
        RuntimeError: Discord could not be reached.
    """
    import httpx

    headers = {"Authorization": f"Bot {token}", "User-Agent": "GAIA (amd-gaia.ai, 1)"}
    try:
        with httpx.Client(base_url="https://discord.com/api/v10", timeout=20) as http:
            me = http.get("/users/@me", headers=headers)
            if me.status_code == 401:
                raise DiscordTokenError(
                    "Discord refused that token. Copy it again from the Developer "
                    "Portal → your app → Bot → Reset Token."
                )
            me.raise_for_status()
            app = http.get("/oauth2/applications/@me", headers=headers)
            app.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(
            f"Could not reach Discord to check the token: {e}. Check the "
            "connection and try again."
        ) from e
    user = me.json()
    application = app.json()
    return {
        "bot_id": str(user["id"]),
        "bot_name": str(user.get("username") or "GAIA"),
        "application_id": str(application["id"]),
        "bot_public": bool(application.get("bot_public", False)),
    }


def bot_page_url(application_id: str) -> str:
    """The app's Bot page, where the token is reset and Public Bot turned off."""
    return f"https://discord.com/developers/applications/{application_id}/bot"


def bot_profile_url(bot_id: str) -> str:
    """Opens the bot's profile in Discord, with its Message button."""
    return f"https://discord.com/users/{bot_id}"


def invite_url(application_id: str) -> str:
    """Add-to-server link. ``permissions=0``: a DM bot needs none."""
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&scope=bot&permissions=0"
    )
