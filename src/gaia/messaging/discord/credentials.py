# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where the Discord bot token lives.

The OS keyring, never a file: the token is a standing grant to act as the bot,
and the bot can reach the owner's agent. ``GAIA_DISCORD_BOT_TOKEN`` overrides
the keyring, which is how a container supplies it without one.
"""

from __future__ import annotations

import os
from typing import Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: Shared with the connectors framework so GAIA has one credential service.
SERVICE_NAME = "gaia.connections"

BOT_TOKEN_KEY = "discord:bot_token"
BOT_TOKEN_ENV_VAR = "GAIA_DISCORD_BOT_TOKEN"

#: The slot the ``mcp-discord`` connector saves its token under
#: (``gaia.connectors.mcp_server._saved_secret``).
CONNECTOR_TOKEN_KEY = "mcp-discord:DISCORD_TOKEN"


class DiscordCredentialsError(RuntimeError):
    """The bot token is missing or could not be reached."""


def _keyring():
    """Import keyring through the connectors guard for an actionable error."""
    from gaia.connectors._keyring import keyring, normalize_keyring_backend_env

    normalize_keyring_backend_env()
    return keyring


#: Said when this PC has nowhere secure to keep the token.
NO_STORE_HINT = (
    "This PC has no secure place to keep the Discord bot token ({detail}). On a "
    "Linux desktop, log in to your desktop session so its keyring (GNOME Keyring "
    "or KWallet) is running, then connect again. On a server or in WSL, set "
    f"{BOT_TOKEN_ENV_VAR} to the token in the environment GAIA starts in, then "
    "connect again. Docs: https://amd-gaia.ai/docs/guides/discord"
)


def save(bot_token: str) -> None:
    """Store the bot token in the OS keyring.

    Nothing to store when the environment already supplies this very token:
    that is the way to run where there is no keyring.

    Raises:
        DiscordCredentialsError: the keyring cannot keep it, with what to do.
    """
    if os.environ.get(BOT_TOKEN_ENV_VAR) == bot_token:
        log.info("The Discord bot token comes from %s; not stored", BOT_TOKEN_ENV_VAR)
        return
    try:
        _keyring().set_password(SERVICE_NAME, BOT_TOKEN_KEY, bot_token)
    except Exception as e:  # noqa: BLE001 - re-raised with the remedy below
        raise DiscordCredentialsError(NO_STORE_HINT.format(detail=_short(e))) from e
    log.info("Stored the Discord bot token in the OS keyring under %s", SERVICE_NAME)


def _short(error: Exception) -> str:
    text = str(error).split(". ")[0].strip()
    return text[:120] or type(error).__name__


def clear() -> None:
    """Remove the stored token. A missing entry is not an error."""
    keyring = _keyring()
    try:
        keyring.delete_password(SERVICE_NAME, BOT_TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        log.debug("No stored Discord bot token to remove")


def load() -> str:
    """Return the bot token, environment first, then the keyring.

    Raises:
        DiscordCredentialsError: when no token is stored or the keyring fails.
    """
    token = os.environ.get(BOT_TOKEN_ENV_VAR)
    if token:
        return token
    try:
        token = _keyring().get_password(SERVICE_NAME, BOT_TOKEN_KEY)
    except Exception as e:  # noqa: BLE001 - re-raised with the remedy below
        raise DiscordCredentialsError(
            f"Could not read the Discord bot token from the OS keyring: {e}. "
            "Unlock the keyring, or connect Discord again in Settings → "
            "Messaging."
        ) from e
    if not token:
        raise DiscordCredentialsError(
            "Discord is not connected on this machine: no bot token is stored. "
            "Connect it in Settings → Messaging → Discord, or with /discord setup "
            "in the terminal app. Docs: https://amd-gaia.ai/docs/guides/discord"
        )
    return token


def load_optional() -> Optional[str]:
    """Return the token, or ``None`` when Discord has not been connected."""
    try:
        return load()
    except DiscordCredentialsError:
        return None


def connector_token() -> Optional[str]:
    """The ``mcp-discord`` connector's token, if one is saved.

    Read only to refuse sharing it: two Gateway sessions on one bot both receive
    every DM, so the bridge needs a bot of its own. With no usable keyring the
    connector could not have saved one either, so the answer is "none".
    """
    try:
        return _keyring().get_password(SERVICE_NAME, CONNECTOR_TOKEN_KEY)
    except Exception as e:  # noqa: BLE001 - no keyring means no saved connector token
        log.info("No keyring to look up the Discord connector's token in: %s", e)
        return None
