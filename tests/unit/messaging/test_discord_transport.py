# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The discord.py side: which intents it asks for, and how it reads a message."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

discord = pytest.importorskip("discord")

from gaia.messaging.discord import transport  # noqa: E402


def test_only_unprivileged_intents_are_requested():
    intents = transport.intents()
    assert intents.dm_messages and intents.guilds
    # Privileged intents need a Developer Portal toggle; the bridge needs none.
    assert not intents.message_content
    assert not intents.members
    assert not intents.presences
    assert not intents.guild_messages, "server messages are never read"


def _message(channel_type, guild=None, bot=False):
    return SimpleNamespace(
        id=1567999999999999999,
        channel=SimpleNamespace(id=555, type=channel_type),
        author=SimpleNamespace(id=111, bot=bot),
        guild=guild,
        content="hi",
        attachments=[
            SimpleNamespace(
                filename="a.pdf",
                url="https://cdn.discordapp.com/attachments/1/2/a.pdf",
                size=10,
            )
        ],
    )


def _converter():
    gateway = transport.GatewayTransport.__new__(transport.GatewayTransport)
    gateway._discord = discord
    return gateway


def test_a_dm_is_read_as_a_dm():
    message = _message(discord.ChannelType.private)
    inbound = _converter()._message(message)
    assert inbound.is_dm
    assert (inbound.id, inbound.channel_id, inbound.author_id) == (
        "1567999999999999999",
        "555",
        "111",
    )
    assert inbound.attachments[0].filename == "a.pdf"


def test_a_server_message_is_never_read_as_a_dm():
    message = _message(discord.ChannelType.text, guild=SimpleNamespace(id=9))
    assert not _converter()._message(message).is_dm


def test_a_group_dm_is_not_a_dm_with_the_bot():
    message = _message(discord.ChannelType.group)
    assert not _converter()._message(message).is_dm


def test_invite_url_asks_for_no_permissions():
    url = transport.invite_url("123")
    assert "permissions=0" in url and "scope=bot" in url and "client_id=123" in url


def test_dms_are_handled_one_at_a_time_in_arrival_order():
    """Parallel handling let a quick follow-up overtake the message it follows."""
    gateway = transport.GatewayTransport()
    assert gateway._messages._max_workers == 1


def test_closing_a_prompt_drops_its_buttons_and_attachment():
    edited = {}

    class _Partial:
        async def edit(self, **kwargs):
            edited.update(kwargs)

    gateway = transport.GatewayTransport.__new__(transport.GatewayTransport)
    gateway._discord = discord
    gateway._channel = lambda _cid: SimpleNamespace(
        get_partial_message=lambda _mid: _Partial()
    )

    def run(make):
        import asyncio

        return asyncio.run(make())

    gateway._call = run
    gateway.edit("1", "2", "✅ Allowed", clear_buttons=True)
    assert edited["view"] is None and edited["attachments"] == []
    edited.clear()
    gateway.edit("1", "2", "Thinking…")
    assert "attachments" not in edited, "a plain edit keeps what is attached"
